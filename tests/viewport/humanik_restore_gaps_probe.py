"""Investigation-only Maya GUI commandPort probe for HUMANIK-RESTORE-GAPS-1.

Reproduces (or rules out) the reported "Restore MMD Rig does not work after
Control Rig" symptom against a real Maya scene, one operation-sequence case
at a time. ``humanik_builder._initialize_humanik_control_rig_ui`` raises in
batch mode (``about -batch``), so any case that calls
``create_control_rig``/``hikCreateControlRig`` cannot run under plain
mayapy -- this probe follows the same Maya GUI + commandPort pattern as
``tests/viewport/e2e_humanik_control_rig_cycle.py`` instead.

This script is NOT wired into ``noxfile.py`` and is not meant to gate CI --
it is a disposable diagnostic tool for HUMANIK-RESTORE-GAPS-1 (see
``TODO.md``).

Each case starts from a fresh scene (``cmds.file(new, force=True)``) inside
the same long-running Maya GUI process, so one case's failure cannot corrupt
another's. Cases without a Control Rig step (``retarget``) could run under
mayapy too, but are kept here for a single consistent report shape.

Host-side usage (Maya GUI required)::

    python tests/viewport/humanik_restore_gaps_probe.py --maya 2024 \\
        --pmx tests/data/mmt_test_model.pmx

Report JSON: ``build/reports/humanik_restore_gaps_probe.json``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.common import maya_commandport

COMMAND_PORT = 7726
COMPLETION_MARKER = "//-- HUMANIK_RESTORE_GAPS_PROBE_DONE --//"
TEST_TIMEOUT = 900
LOG_POLL_INTERVAL = 1.0

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CHANNELS = ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ")
HIK_NODE_TYPES = (
    "HIKCharacterNode",
    "HIKControlSetNode",
    "HIKSolverNode",
    "HIKState2SK",
    "HIKProperty2State",
)
# ``restore_mmd_rig``/``stop_humanik_control_rig`` only ever tear down the
# Control Rig it created (``hikDeleteControlRig()``) -- characterizing a
# model intentionally leaves the HIK character system (HIKCharacterNode,
# its HIKSolverNode/HIKState2SK/HIKProperty2State) in the scene; there is no
# "restore_mmd_rig deletes the character" contract anywhere in
# ``humanik_frontend``/``humanik_control_rig``. Nodes in this set are
# therefore expected/acceptable residue after a restore and must NOT be
# treated as a topology mismatch. Only Control Rig nodes are required to be
# gone.
CONTROL_RIG_ONLY_NODE_TYPES = ("HIKControlSetNode",)
CHARACTERIZE_RESIDUAL_NODE_TYPES = tuple(
    node_type for node_type in HIK_NODE_TYPES if node_type not in CONTROL_RIG_ONLY_NODE_TYPES
)


# ===================================================================
# Maya-side: runs inside the live Maya GUI via commandPort
# ===================================================================
def run_probe(log_path: str, pmx_path: str, report_path: str) -> None:
    import traceback

    import maya.cmds as cmds

    def _log(msg: str) -> None:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(str(msg) + "\n")
        try:
            print(msg)
        except Exception:
            pass

    def _load_plugin() -> None:
        plugin_path = _PROJECT_ROOT / "mmd_tools" / "plugin_main.py"
        if not cmds.pluginInfo(plugin_path.stem, query=True, loaded=True):
            cmds.loadPlugin(str(plugin_path), quiet=True)

    def _import_model(path: Path) -> str:
        from mmd_tools.io.mmd_importer import import_mmd_file

        root = import_mmd_file(
            str(path),
            options={
                "use_namespace": True,
                "setup_rig": True,
                "import_physics": False,
                "create_mmd_shaders": False,
                "use_native_pmx_parse": False,
                "require_native_pmx_parse": False,
            },
        )
        if not root:
            raise RuntimeError(f"PMX import failed: {path}")
        return str(root)

    def _joint_topology(joints: List[str]) -> Dict[str, List[str]]:
        connections: Dict[str, List[str]] = {}
        for joint in sorted(joints):
            for channel in CHANNELS:
                plug = f"{joint}.{channel}"
                connections[plug] = sorted(
                    cmds.listConnections(plug, source=True, destination=False, plugs=True) or []
                )
        return connections

    def _scene_snapshot(joints: List[str]) -> Dict[str, Any]:
        snapshot: Dict[str, Any] = {
            "connections": _joint_topology(joints),
            "animCurves": sorted(cmds.ls(type="animCurve") or []),
        }
        for node_type in HIK_NODE_TYPES:
            snapshot[node_type] = sorted(cmds.ls(type=node_type) or [])
        return snapshot

    def _snapshot_diff(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
        diff: Dict[str, Any] = {}
        for key in sorted(set(before) | set(after)):
            if before.get(key) != after.get(key):
                diff[key] = {"before": before.get(key), "after": after.get(key)}
        return diff

    def _writer_topology_diff(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
        """Diff only the MMD-writer-owned facts a restore must undo.

        Unlike ``_snapshot_diff``, this deliberately excludes
        ``CHARACTERIZE_RESIDUAL_NODE_TYPES`` (see that constant) -- HIK
        character-system nodes surviving a restore is expected, not a
        regression. ``connections``/``animCurves`` and
        ``CONTROL_RIG_ONLY_NODE_TYPES`` are still compared in full.
        """
        ignored_keys = set(CHARACTERIZE_RESIDUAL_NODE_TYPES)
        return {
            key: value
            for key, value in _snapshot_diff(before, after).items()
            if key not in ignored_keys
        }

    def _describe_state(session, model_root=None) -> Dict[str, Any]:
        try:
            return session.describe_frontend_state(model_root)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    def _new_scene() -> None:
        cmds.file(new=True, force=True)
        _load_plugin()

    # ---------------- individual cases ----------------

    def case_basic(pmx: Path) -> Dict[str, Any]:
        from mmd_tools.core.humanik_builder import resolve_scene_humanik_assignments
        from mmd_tools.core.humanik_frontend import HumanIkFrontendSession

        report: Dict[str, Any] = {"case": "basic"}
        _new_scene()
        root = _import_model(pmx)
        report["modelRoot"] = root
        joints = [a.joint for a in resolve_scene_humanik_assignments(root).assignments]

        before_characterize = _scene_snapshot(joints)
        session = HumanIkFrontendSession()
        session.setup_and_characterize(root)
        session.create_control_rig(root)
        during = _scene_snapshot(joints)
        report["stateWhileControlRigActive"] = _describe_state(session, root)
        restored = session.restore_mmd_rig()
        after_restore = _scene_snapshot(joints)

        report["restoreMmdRigReturned"] = bool(restored)
        report["hikNodesWhileActive"] = {t: during[t] for t in HIK_NODE_TYPES}
        report["hikNodesAfterRestore"] = {t: after_restore[t] for t in HIK_NODE_TYPES}
        report["topologyDiffVsPreCharacterize"] = _snapshot_diff(before_characterize, after_restore)
        # NOTE: characterize-derived HIK nodes (HIKCharacterNode and friends,
        # see CHARACTERIZE_RESIDUAL_NODE_TYPES) are expected to remain --
        # only the writer-topology diff (joint connections/animCurves) and
        # the Control Rig node type are pass/fail criteria.
        report["writerTopologyDiffVsPreCharacterize"] = _writer_topology_diff(
            before_characterize, after_restore
        )
        report["writerTopologyRestored"] = report["writerTopologyDiffVsPreCharacterize"] == {}
        report["controlRigClearedAfterRestore"] = not after_restore["HIKControlSetNode"]
        report["hikNodesAllClearedAfterRestore"] = all(not after_restore[t] for t in HIK_NODE_TYPES)
        report["stateAfterRestore"] = _describe_state(session, root)
        report["noResidualControlRigTransaction"] = (
            "error" not in report["stateAfterRestore"]
            and report["stateAfterRestore"].get("restoreHint", {}).get("controlRigCount") == 0
            and not report["stateAfterRestore"].get("restoreHint", {}).get("orphanedControlRigs")
        )
        report["status"] = "pass" if (
            report["restoreMmdRigReturned"]
            and report["writerTopologyRestored"]
            and report["controlRigClearedAfterRestore"]
            and report["noResidualControlRigTransaction"]
        ) else "fail-reproduces-gap"
        return report

    def case_repeat(pmx: Path) -> Dict[str, Any]:
        from mmd_tools.core.humanik_builder import resolve_scene_humanik_assignments
        from mmd_tools.core.humanik_frontend import HumanIkFrontendSession

        report: Dict[str, Any] = {"case": "repeat"}
        _new_scene()
        root = _import_model(pmx)
        report["modelRoot"] = root
        joints = [a.joint for a in resolve_scene_humanik_assignments(root).assignments]
        baseline = _scene_snapshot(joints)

        session = HumanIkFrontendSession()
        rounds: List[Dict[str, Any]] = []
        for round_index in range(2):
            round_report: Dict[str, Any] = {"round": round_index}
            try:
                session.setup_and_characterize(root)
                session.create_control_rig(root)
                round_report["stateWhileActive"] = _describe_state(session, root)
                round_report["restoreMmdRigReturned"] = bool(session.restore_mmd_rig())
            except Exception as exc:  # noqa: BLE001
                round_report["error"] = str(exc)
            after = _scene_snapshot(joints)
            round_report["hikNodesAfter"] = {t: after[t] for t in HIK_NODE_TYPES}
            # baseline is captured before the first round's setup_and_characterize,
            # so characterize-derived HIK nodes are legitimately absent from it on
            # round 0 but present (and expected to stay present) from round 1
            # onward -- only writer topology (joint connections/animCurves) and
            # the Control Rig node type are pass/fail criteria across rounds.
            round_report["writerTopologyDiffVsBaseline"] = _writer_topology_diff(baseline, after)
            round_report["controlRigCleared"] = not after["HIKControlSetNode"]
            round_report["stateAfter"] = _describe_state(session, root)
            round_report["noResidualControlRigTransaction"] = (
                "error" not in round_report["stateAfter"]
                and round_report["stateAfter"].get("restoreHint", {}).get("controlRigCount") == 0
                and not round_report["stateAfter"].get("restoreHint", {}).get("orphanedControlRigs")
            )
            rounds.append(round_report)
        report["rounds"] = rounds
        report["secondRoundBroke"] = (
            "error" in rounds[1]
            or bool(rounds[1]["writerTopologyDiffVsBaseline"])
            or not rounds[1]["controlRigCleared"]
            or not rounds[1]["noResidualControlRigTransaction"]
        )
        report["status"] = "fail-reproduces-gap" if report["secondRoundBroke"] else "pass"
        return report

    def case_retarget(pmx: Path) -> Dict[str, Any]:
        from mmd_tools.core.humanik_builder import resolve_scene_humanik_assignments
        from mmd_tools.core.humanik_frontend import HumanIkFrontendSession

        report: Dict[str, Any] = {"case": "retarget"}
        _new_scene()
        source_root = _import_model(pmx)
        target_root = _import_model(pmx)
        report["sourceRoot"] = source_root
        report["targetRoot"] = target_root
        target_joints = [a.joint for a in resolve_scene_humanik_assignments(target_root).assignments]

        session = HumanIkFrontendSession()
        session.setup_and_characterize(source_root)
        session.enter_source_mode(source_root)
        session.setup_and_characterize(target_root)
        session.enter_target_mode(target_root)
        report["stateDuringPreview"] = _describe_state(session, target_root)
        bake_result = session.bake_to_mmd_rig(0, 10)
        report["bakeResultType"] = type(bake_result).__name__
        report["statePostBakePrePreviewClear"] = _describe_state(session, target_root)
        restored = session.restore_mmd_rig()
        after = _scene_snapshot(target_joints)

        report["restoreMmdRigReturned"] = bool(restored)
        report["hikNodesAfterRestore"] = {t: after[t] for t in HIK_NODE_TYPES}
        # The bake intentionally leaves animCurves on the target MMD rig --
        # that is the whole point of retarget -- so a populated animCurves
        # list here is the expected successful outcome, not residue to clear.
        report["animCurvesAfterRestore"] = after["animCurves"]
        report["previewClearedAfterRestore"] = not session.active_preview
        report["stateAfterRestore"] = _describe_state(session, target_root)
        # Both source and target were characterized (setup_and_characterize),
        # so their HIK character-system nodes are expected to remain after
        # restore for the same reason as basic/repeat (see
        # CHARACTERIZE_RESIDUAL_NODE_TYPES) -- retarget never creates a
        # Control Rig, so only that node type is required to stay clear.
        report["controlRigClearedAfterRestore"] = not after["HIKControlSetNode"]
        report["hikNodesAllClearedAfterRestore"] = all(not after[t] for t in HIK_NODE_TYPES)
        report["noResidualControlRigTransaction"] = (
            "error" not in report["stateAfterRestore"]
            and report["stateAfterRestore"].get("restoreHint", {}).get("controlRigCount") == 0
            and not report["stateAfterRestore"].get("restoreHint", {}).get("orphanedControlRigs")
        )
        report["status"] = "pass" if (
            report["previewClearedAfterRestore"]
            and report["controlRigClearedAfterRestore"]
            and report["noResidualControlRigTransaction"]
        ) else "fail-reproduces-gap"
        return report

    def case_session_loss(pmx: Path) -> Dict[str, Any]:
        """Hypothesis A: the transaction survives a scene reopen via restore_state data.

        HUMANIK-RESTORE-GAPS-1d persists the writer-isolation restore_state on an
        owned scene network node.  A fresh frontend session must reconstruct
        the tracked transaction, restore the exact pre-Control-Rig writer
        topology, and leave no orphan-recovery warning (the 1c scene-facts
        fallback remains reserved for Control Rigs created outside this path).
        """
        import tempfile

        from mmd_tools.core.humanik_builder import resolve_scene_humanik_assignments
        from mmd_tools.core.humanik_frontend import HumanIkFrontendSession

        report: Dict[str, Any] = {"case": "session_loss"}
        _new_scene()
        root = _import_model(pmx)
        report["modelRoot"] = root
        joints = [a.joint for a in resolve_scene_humanik_assignments(root).assignments]

        session = HumanIkFrontendSession()
        session.setup_and_characterize(root)
        before_control_rig = _scene_snapshot(joints)
        session.create_control_rig(root)
        before_reopen = _scene_snapshot(joints)
        report["stateBeforeReopen"] = _describe_state(session, root)

        tmp_dir = Path(tempfile.mkdtemp(prefix="humanik_restore_gaps_"))
        scene_path = tmp_dir / "session_loss.ma"
        cmds.file(rename=str(scene_path))
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(new=True, force=True)
        cmds.file(str(scene_path), open=True, force=True)
        _load_plugin()

        new_session = HumanIkFrontendSession()
        report["stateInNewSessionAfterReopen"] = _describe_state(new_session, root)
        report["persistedTransactionReconstructed"] = bool(new_session._control_rig_transactions)
        restore_error = None
        try:
            restored = new_session.restore_mmd_rig()
        except Exception as exc:  # noqa: BLE001
            restored = None
            restore_error = str(exc)
        after_restore = _scene_snapshot(joints)

        last_orphan_recovery = new_session.describe_last_orphan_recovery()
        recovered_rows = last_orphan_recovery.get("recovered", [])

        report["restoreMmdRigReturned"] = restored
        report["restoreMmdRigError"] = restore_error
        report["hikNodesBeforeReopen"] = {t: before_reopen[t] for t in HIK_NODE_TYPES}
        report["hikNodesAfterRestoreAttempt"] = {t: after_restore[t] for t in HIK_NODE_TYPES}
        report["controlRigSurvivedReopenAndRestoreAttempt"] = bool(after_restore["HIKControlSetNode"])
        report["characterSurvivedReopenAndRestoreAttempt"] = bool(after_restore["HIKCharacterNode"])
        report["lastOrphanRecovery"] = last_orphan_recovery
        report["orphanRecoveredCount"] = len(recovered_rows)
        report["orphanRecoveryReportedUnrecoverableWarning"] = bool(recovered_rows) and all(
            row.get("unrecoverableWarnings") for row in recovered_rows
        )
        report["writerTopologyRestored"] = (
            before_control_rig.get("connections") == after_restore.get("connections")
        )
        report["status"] = "pass" if (
            restored is True
            and not report["controlRigSurvivedReopenAndRestoreAttempt"]
            and report["persistedTransactionReconstructed"]
            and report["orphanRecoveredCount"] == 0
            and not report["orphanRecoveryReportedUnrecoverableWarning"]
            and report["writerTopologyRestored"]
        ) else "fail-reproduces-gap"
        return report

    def case_raw_control_rig(pmx: Path) -> Dict[str, Any]:
        """Hypothesis B: a Control Rig created outside begin_humanik_control_rig
        (Maya's own UI, or a raw hikCreateControlRig() call) is never
        registered in the session's transaction table, so restore_mmd_rig has
        nothing to tear down for it.

        HUMANIK-RESTORE-GAPS-1 slice 1c: the same scene-facts fallback used
        for ``case_session_loss`` also covers this -- ``session`` here is
        the *same* session that characterized ``root`` (unlike
        ``case_session_loss``'s fresh ``new_session``), so this additionally
        exercises the "characterize-live binding" path
        (``find_binding_by_character`` still resolves) alongside the
        scene-facts-only path. The pass bar changed the same way: fallback
        recovers it and reports the unrecoverable-restore_state limitation instead
        of leaving the Control Rig node behind.
        """
        from mmd_tools.core.humanik_builder import (
            create_humanik_control_rig,
            resolve_scene_humanik_assignments,
        )
        from mmd_tools.core.humanik_frontend import HumanIkFrontendSession

        report: Dict[str, Any] = {"case": "raw_control_rig"}
        _new_scene()
        root = _import_model(pmx)
        report["modelRoot"] = root
        joints = [a.joint for a in resolve_scene_humanik_assignments(root).assignments]

        session = HumanIkFrontendSession()
        binding = session.setup_and_characterize(root)
        character = binding.character
        report["character"] = character
        report["stateAfterCharacterizeOnly"] = _describe_state(session, root)

        # Raw MEL path: the same MEL sequence humanik_builder.create_humanik_control_rig
        # issues, but bypassing begin_humanik_control_rig's restore_state/isolate/
        # cycle-gate transaction -- equivalent to Character Controls UI.
        create_humanik_control_rig(character)
        cmds.refresh()
        try:
            import maya.utils as maya_utils

            maya_utils.processIdleEvents()
        except Exception:
            pass
        after_raw_create = _scene_snapshot(joints)
        report["stateAfterRawControlRigCreate"] = _describe_state(session, root)
        report["controlRigTransactionsTrackedByRawSession"] = list(session._control_rig_transactions.keys())

        restore_error = None
        try:
            restored = session.restore_mmd_rig()
        except Exception as exc:  # noqa: BLE001
            restored = None
            restore_error = str(exc)
        after_restore = _scene_snapshot(joints)

        last_orphan_recovery = session.describe_last_orphan_recovery()
        recovered_rows = last_orphan_recovery.get("recovered", [])

        report["restoreMmdRigReturned"] = restored
        report["restoreMmdRigError"] = restore_error
        report["hikNodesAfterRawCreate"] = {t: after_raw_create[t] for t in HIK_NODE_TYPES}
        report["hikNodesAfterRestoreAttempt"] = {t: after_restore[t] for t in HIK_NODE_TYPES}
        report["controlRigSurvivedRestoreAttempt"] = bool(after_restore["HIKControlSetNode"])
        report["lastOrphanRecovery"] = last_orphan_recovery
        report["orphanRecoveredCount"] = len(recovered_rows)
        report["orphanRecoveryReportedUnrecoverableWarning"] = bool(recovered_rows) and all(
            row.get("unrecoverableWarnings") for row in recovered_rows
        )
        report["status"] = "pass" if (
            restored is True
            and not report["controlRigSurvivedRestoreAttempt"]
            and report["orphanRecoveredCount"] >= 1
            and report["orphanRecoveryReportedUnrecoverableWarning"]
        ) else "fail-reproduces-gap"
        return report

    def case_partial_delete(pmx: Path) -> Dict[str, Any]:
        """Manual deletion of the HIK character node after create_control_rig,
        then restore_mmd_rig -- how far does best-effort teardown get?"""
        from mmd_tools.core.humanik_builder import resolve_scene_humanik_assignments
        from mmd_tools.core.humanik_frontend import HumanIkFrontendSession

        report: Dict[str, Any] = {"case": "partial_delete"}
        _new_scene()
        root = _import_model(pmx)
        report["modelRoot"] = root
        joints = [a.joint for a in resolve_scene_humanik_assignments(root).assignments]

        session = HumanIkFrontendSession()
        binding = session.setup_and_characterize(root)
        session.create_control_rig(root)
        character = binding.character
        report["character"] = character

        character_nodes = cmds.ls(character, long=True) or []
        if character_nodes and cmds.objExists(character_nodes[0]):
            cmds.lockNode(character_nodes[0], lock=False)
            cmds.delete(character_nodes[0])
        report["characterNodeDeletedManually"] = bool(character_nodes)
        after_manual_delete = _scene_snapshot(joints)
        report["hikNodesAfterManualDelete"] = {t: after_manual_delete[t] for t in HIK_NODE_TYPES}
        report["stateAfterManualDelete"] = _describe_state(session, root)

        restore_error = None
        try:
            restored = session.restore_mmd_rig()
        except Exception as exc:  # noqa: BLE001
            restored = None
            restore_error = str(exc)
        after_restore = _scene_snapshot(joints)

        report["restoreMmdRigReturned"] = restored
        report["restoreMmdRigError"] = restore_error
        report["hikNodesAfterRestoreAttempt"] = {t: after_restore[t] for t in HIK_NODE_TYPES}
        report["controlRigTransactionsAfterRestoreAttempt"] = list(session._control_rig_transactions.keys())
        report["pendingCharactersAfterRestoreAttempt"] = list(session._pending_characters)
        report["pendingStancesAfterRestoreAttempt"] = list(session._pending_stances)
        # HUMANIK-RESTORE-GAPS-1 fix 1a: manually deleting the character node
        # must no longer wedge restore_mmd_rig into a permanent exception
        # loop. The Control Rig itself (HIKControlSetNode) and the in-memory
        # transaction must both be released even though the character node
        # is gone and cannot be un-deleted; a second restore_mmd_rig call is
        # exercised to confirm the (now-released) transaction does not make
        # the same call fail again.
        report["controlRigTransactionReleased"] = (
            not report["controlRigTransactionsAfterRestoreAttempt"]
        )
        report["controlRigNodeClearedAfterRestoreAttempt"] = not after_restore["HIKControlSetNode"]
        second_restore_error = None
        try:
            second_restored = session.restore_mmd_rig()
        except Exception as exc:  # noqa: BLE001
            second_restored = None
            second_restore_error = str(exc)
        report["secondRestoreMmdRigReturned"] = second_restored
        report["secondRestoreMmdRigError"] = second_restore_error
        report["status"] = "pass" if (
            report["controlRigTransactionReleased"]
            and report["controlRigNodeClearedAfterRestoreAttempt"]
            and second_restore_error is None
        ) else "fail-reproduces-gap"
        return report

    # ---------------- driver ----------------

    report: Dict[str, Any] = {
        "status": "error",
        "mayaVersion": None,
        "pmxPath": pmx_path,
        "cases": {},
        "errors": [],
    }

    def _write_report() -> None:
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        Path(report_path).write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    try:
        report["mayaVersion"] = cmds.about(version=True)
        pmx = Path(pmx_path)
        case_funcs = {
            "basic": case_basic,
            "repeat": case_repeat,
            "retarget": case_retarget,
            "session_loss": case_session_loss,
            "raw_control_rig": case_raw_control_rig,
            "partial_delete": case_partial_delete,
        }
        for name, func in case_funcs.items():
            _log(f"--- running case: {name} ---")
            try:
                case_report = func(pmx)
            except Exception:
                case_report = {"case": name, "status": "error", "error": traceback.format_exc()}
                report["errors"].append(f"{name}: {traceback.format_exc()}")
            report["cases"][name] = case_report
            _log(f"--- case {name} done: status={case_report.get('status')} ---")
            _write_report()

        report["status"] = "done"
        _write_report()
        _log(
            "RESULT_JSON: "
            + json.dumps({name: report["cases"][name].get("status") for name in case_funcs})
        )
        _log(COMPLETION_MARKER)
    except Exception:
        report["errors"].append(traceback.format_exc())
        report["status"] = "error"
        _write_report()
        _log(f"EXCEPTION:\n{traceback.format_exc()}")
        _log(f"RESULT_JSON: {json.dumps({'status': 'error'})}")
        _log(COMPLETION_MARKER)


# ===================================================================
# Host-side
# ===================================================================
def main() -> int:
    import io

    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    ap = argparse.ArgumentParser(description="HUMANIK-RESTORE-GAPS-1 investigation probe")
    ap.add_argument("--maya", default="2024")
    ap.add_argument("--pmx", default="tests/data/mmt_test_model.pmx")
    ap.add_argument("--port", type=int, default=COMMAND_PORT)
    args = ap.parse_args()

    project_root = _PROJECT_ROOT
    log_dir = project_root / "build" / "e2e"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "humanik_restore_gaps_probe.log"
    if log_path.exists():
        log_path.unlink()
    report_path = project_root / "build" / "reports" / "humanik_restore_gaps_probe.json"

    pmx_posix = (project_root / args.pmx).resolve().as_posix()
    maya_exe = maya_commandport.maya_exe(args.maya)
    logger.info("Maya: %s", maya_exe)

    proc = maya_commandport.launch_maya(
        version=args.maya,
        project_root=project_root,
        output_dir=log_dir,
        port=args.port,
        launch_mode="explorer" if sys.platform == "win32" else "direct",
    )

    try:
        maya_commandport.wait_for_port(args.port, timeout=120, process=proc)
        logger.info("commandPort :%d ready", args.port)

        command = (
            "import sys\n"
            "from pathlib import Path\n"
            f"project_root = Path(r'{project_root.as_posix()}')\n"
            "if str(project_root) not in sys.path:\n"
            "    sys.path.insert(0, str(project_root))\n"
            "\n"
            "from tests.viewport.humanik_restore_gaps_probe import run_probe\n"
            f"run_probe(r'{log_path.as_posix()}', r'{pmx_posix}', r'{report_path.as_posix()}')\n"
        )
        maya_commandport.send_python(args.port, command, label="<humanik-restore-gaps-probe-command>")
        logger.info("command sent (%d bytes)", len(command))

        if not log_path.exists():
            log_path.touch()
        start = time.time()
        done = False
        result_json = None
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(0, 2)
            while time.time() - start < TEST_TIMEOUT:
                line = f.readline()
                if line:
                    print(line, end="")
                    if line.strip().startswith("RESULT_JSON:"):
                        result_json = json.loads(line.strip().split("RESULT_JSON:", 1)[1])
                    if COMPLETION_MARKER in line:
                        done = True
                        break
                else:
                    time.sleep(LOG_POLL_INTERVAL)

        if not done:
            raise TimeoutError(f"Probe did not finish within {TEST_TIMEOUT}s")

        if result_json:
            logger.info("=== RESULT ===")
            logger.info("per-case status: %s", result_json)
            logger.info("report: %s", report_path)
        return 0
    finally:
        maya_commandport.quit_maya(args.port)
        maya_commandport.close_process_logs(proc)


if __name__ == "__main__":
    raise SystemExit(main())
