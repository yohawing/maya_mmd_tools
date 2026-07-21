"""E2E evidence capture: DG cycle around HumanIK Control Rig creation.

This script reproduces (or rules out) the reported DG cycle that spans
``HIKState2SK.*``, ``pairBlend.outTranslate*``, ``mmdCcdIk.outputRotate``, and
primary joint ``translate``/``parentMatrix`` after ``hikCreateControlRig()``
is run on an MMD-imported model, then moving Control Rig effectors
(hands/feet).

It does NOT disable ``cycleCheck`` -- the goal is to capture the exact
cyclic plugs/edges as JSON evidence, not to hide them.

Two code paths are exercised in the same Maya session on fresh imports.
The legacy pre-fix evidence stages (``frontend``, ``raw_mel``) and the
``isolation_validation`` diagnostic stage that predated the session-level
fix have been retired -- that evidence is recorded in commit history and
``docs-dev/`` and does not need to keep re-running. Passing Maya's own
Control Rig UI through the cycle gate was also dropped as a requirement
(see ``TODO.md``): ``humanik_control_rig_watch`` is now a warn-only
detector for that path, not an auto-adopter, so this script only verifies
the two paths that still matter:

* ``transaction_frontend`` (gates ``status``) -- exercises
  ``HumanIkFrontendSession.create_control_rig()``, which wraps
  ``hikCreateControlRig()`` in ``humanik_control_rig.begin_humanik_control_rig()``
  (journal -> isolate MMD writers -> pre-cycle gate -> create -> re-scan/
  re-isolate -> post-cycle gate). Asserts the control-rig-related cycle
  bucket is empty right after creation and after each effector move, that
  ``keep_post`` writers stay connected, and that
  ``session.restore_mmd_rig()`` returns writer topology and cycle state to
  the pre-characterize baseline. Any assertion failure here is recorded as a
  script error (not just a "found a cycle" finding): this is the real
  regression gate.
* ``standard_ui_warning`` (gates ``status``) -- characterizes through the
  mmd_tools frontend, then creates the Control Rig via RAW MEL
  (``hikSetCurrentCharacter`` + ``hikCreateControlRig()``), simulating Maya's
  standard HumanIK UI (Character Controls -> Create Control Rig) rather than
  ``session.create_control_rig()``. After pumping idle events
  (``cmds.refresh()`` + ``maya.utils.processIdleEvents()``) so the
  ``humanik_control_rig_watch`` node-added callback's deferred handler runs,
  asserts only that (a) the watch's warning was emitted (script-editor
  history capture), and (b) the watch itself performed no scene mutation --
  the writer census right after ``hikCreateControlRig()`` and after the idle
  pump are identical. This stage deliberately does NOT gate on cycle count:
  the raw-MEL rig here has its known historical cycle (see the retired
  legacy stages' evidence) and that is expected/out of scope now that the
  cycle gate for this path was dropped.

For each stage (post-characterize, post-control-rig, post-each-effector-move)
the script captures:

* A writer census diff (``humanik_retarget.collect_humanik_incoming_writer_census``
  / ``diff_humanik_connections``) showing exactly which connections HIK
  creation added.
* ``cmds.cycleCheck(all=True, list=True)`` and the current
  ``cmds.cycleCheck(query=True, evaluation=True)`` state.
* For any plugs reported in a cycle, the raw connection edges
  (``cmds.listConnections(plug, plugs=True, connections=True)``) so the SCC
  can be reconstructed from the JSON report.

Host-side usage (Maya GUI required -- ``hikCreateControlRig`` fails in
mayapy/batch, see ``humanik_builder._initialize_humanik_control_rig_ui``)::

    python tests/viewport/e2e_humanik_control_rig_cycle.py --maya 2026 --model "path/to/model.pmx"

Report JSON: ``build/reports/humanik_control_rig_cycle_e2e.json``.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.common import maya_commandport

COMMAND_PORT = 7725
COMPLETION_MARKER = "//-- HUMANIK_CONTROL_RIG_CYCLE_E2E_DONE --//"
TEST_TIMEOUT = 900
LOG_POLL_INTERVAL = 1

_EFFECTOR_NAME_PATTERN = re.compile(r"(LeftHand|RightHand|LeftFoot|RightFoot)", re.IGNORECASE)
_EFFECTOR_MOVE_DELTA = (1.0, 1.0, 1.0)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ===================================================================
# Maya-side: runs inside the live Maya GUI
# ===================================================================
def run_e2e_check(log_path: str, model_path: str, report_path: str) -> None:
    import traceback

    import maya.cmds as cmds
    import maya.mel as mel

    def _log(msg: str) -> None:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(str(msg) + "\n")
        try:
            print(msg)
        except Exception:
            pass

    report = {
        "status": "error",
        "mayaVersion": None,
        "modelPath": model_path,
        "stages": [],
        "cycleFindings": [],
        "effectorsMoved": [],
        "errors": [],
        "transactionStagePassed": False,
        "standardUiWarningStagePassed": False,
    }

    def _write_report() -> None:
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        Path(report_path).write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    try:
        _log("=== HumanIK Control Rig Cycle E2E ===")
        report["mayaVersion"] = cmds.about(version=True)

        from mmd_tools.core.humanik_builder import resolve_scene_humanik_assignments
        from mmd_tools.core.humanik_constraints import (
            classify_humanik_constraints,
            collect_humanik_constraint_facts,
        )
        from mmd_tools.core.humanik_control_rig import (
            get_active_control_rig_transaction,
            new_cycle_plugs,
        )
        from mmd_tools.core.humanik_frontend import HumanIkFrontendSession
        from mmd_tools.core.humanik_retarget import (
            collect_humanik_incoming_writer_census,
            diff_humanik_connections,
        )
        from mmd_tools.ui import humanik_menu_actions

        plugin_path = _PROJECT_ROOT / "mmd_tools" / "plugin_main.py"
        if not cmds.pluginInfo(plugin_path.stem, query=True, loaded=True):
            cmds.loadPlugin(str(plugin_path), quiet=True)
            _log(f"loaded plugin: {plugin_path}")

        # Turn on script-editor history capture for cycle warnings emitted by
        # Maya itself (these are printed, not raised, so cycleCheck's own
        # query commands are the authoritative evidence; this is corroboration).
        script_history_path = Path(log_path).with_name(Path(log_path).stem + "_script_history.log")
        try:
            cmds.scriptEditorInfo(clearHistory=True)
            cmds.scriptEditorInfo(historyFilename=str(script_history_path), writeHistory=True)
        except Exception as exc:
            _log(f"WARN scriptEditorInfo unavailable: {exc}")

        def _snapshot(assignments):
            census = collect_humanik_incoming_writer_census(assignments, cmds_module=cmds)
            return {row["destination"]: list(row["writers"]) for row in census}, census

        def _cycle_state(label: str) -> dict:
            evaluation_on = bool(cmds.cycleCheck(query=True, evaluation=True))
            cycle_plugs = cmds.cycleCheck(all=True, list=True) or []
            edges = []
            nodes = set()
            for plug in cycle_plugs:
                node = plug.split(".", 1)[0]
                nodes.add(node)
                try:
                    raw = cmds.listConnections(
                        plug, plugs=True, connections=True, source=True, destination=True
                    ) or []
                except Exception:
                    raw = []
                for i in range(0, len(raw) - 1, 2):
                    edges.append({"thisPlug": raw[i], "otherPlug": raw[i + 1]})
            node_types = {}
            for node in nodes:
                try:
                    node_types[node] = cmds.nodeType(node)
                except Exception:
                    node_types[node] = None
            finding = {
                "label": label,
                "evaluationOn": evaluation_on,
                "cyclePlugs": sorted(cycle_plugs),
                "cycleNodeTypes": node_types,
                "edges": edges,
            }
            if cycle_plugs:
                report["cycleFindings"].append(finding)
                _log(f"CYCLE at [{label}]: {sorted(cycle_plugs)}")
            else:
                _log(f"no cycle at [{label}] (evaluationOn={evaluation_on})")
            return finding

        def _import_model():
            from mmd_tools.io.mmd_importer import import_mmd_file

            cmds.file(new=True, force=True)
            root = import_mmd_file(str(model_path))
            if not root:
                raise RuntimeError(f"MMD import failed: {model_path}")
            return str(root)

        def _discover_effectors(before_transforms: set, after_transforms: set) -> list:
            new_transforms = sorted(after_transforms - before_transforms)
            candidates = [t for t in new_transforms if _EFFECTOR_NAME_PATTERN.search(t)]
            return candidates, new_transforms

        def _move_effectors(effectors: list, stage_label: str) -> list:
            moved = []
            for node in effectors:
                entry = {"node": node, "stage": stage_label, "moved": False, "error": None}
                try:
                    cmds.xform(node, relative=True, worldSpace=True, translation=list(_EFFECTOR_MOVE_DELTA))
                    cmds.refresh()
                    entry["moved"] = True
                except Exception as exc:
                    entry["error"] = str(exc)
                    _log(f"WARN move failed for {node}: {exc}")
                moved.append(entry)
                report["effectorsMoved"].append(entry)
            return moved

        def _bucket_cycle_plugs(cycle_plugs: list) -> dict:
            """Split cycle plugs into control-rig-SCC vs pre-existing physics buckets.

            The known MMD-PHYSICS-SOLVER-CYCLE-1 issue (mmdPhysicsSolver /
            mmdPhysicsBoneDriver) is unrelated to the HIK Control Rig SCC under
            test here; keep it visible but separately tagged.
            """
            physics = []
            control_rig = []
            for plug in cycle_plugs:
                node = plug.split(".", 1)[0]
                try:
                    node_type = cmds.nodeType(node)
                except Exception:
                    node_type = ""
                if "physics" in str(node_type).lower() or "Physics" in node:
                    physics.append(plug)
                else:
                    control_rig.append(plug)
            return {"controlRigRelated": sorted(control_rig), "physicsRelated": sorted(physics)}

        def _run_transaction_frontend_stage() -> dict:
            """Exercise the FIXED ``HumanIkFrontendSession.create_control_rig()``.

            Every check below raises (recorded by the caller as a script
            error, not a mere "found a cycle" finding) on failure: this path
            is expected to be cycle-free end to end, not just diagnosable.
            """
            mode = "transaction_frontend"
            stage_report = {"mode": mode, "legacy": False}
            _log(f"--- stage: {mode} ---")
            model_root = _import_model()
            stage_report["modelRoot"] = model_root
            result = resolve_scene_humanik_assignments(model_root, cmds_module=cmds)
            if not result.assignments:
                raise RuntimeError(f"[{mode}] no HumanIK assignments resolved for {model_root}")

            before_snapshot, before_census = _snapshot(result.assignments)
            stage_report["writerCensusBefore"] = before_census
            pre_finding = _cycle_state(f"{mode}:pre-characterize")
            baseline_bucket = _bucket_cycle_plugs(pre_finding["cyclePlugs"])
            stage_report["cycleBaseline"] = {
                "count": len(pre_finding["cyclePlugs"]),
                **baseline_bucket,
            }

            session = HumanIkFrontendSession(cmds_module=cmds, mel_module=mel)
            binding = session.setup_and_characterize(model_root)
            character = binding.character
            stage_report["character"] = character
            _cycle_state(f"{mode}:post-characterize")

            before_transforms = set(cmds.ls(type="transform", long=True) or [])
            session.create_control_rig(model_root)
            after_transforms = set(cmds.ls(type="transform", long=True) or [])
            if not bool(mel.eval(f'hikHasControlRig("{character}")')):
                raise RuntimeError(f"[{mode}] hikCreateControlRig did not create a control rig")

            post_rig_finding = _cycle_state(f"{mode}:post-control-rig")
            post_rig_bucket = _bucket_cycle_plugs(post_rig_finding["cyclePlugs"])
            stage_report["cyclePostControlRig"] = {
                "count": len(post_rig_finding["cyclePlugs"]),
                **post_rig_bucket,
            }
            if post_rig_bucket["controlRigRelated"]:
                raise RuntimeError(
                    f"[{mode}] control-rig-related DG cycle right after creation: "
                    f"{post_rig_bucket['controlRigRelated']}"
                )

            hik_joint_set = {str(assignment.joint) for assignment in binding.assignments}
            facts = collect_humanik_constraint_facts(cmds_module=cmds)
            ownership_report = classify_humanik_constraints(facts, hik_joint_set)
            keep_post_rows = [
                row for row in ownership_report["rows"]
                if row.get("classification") == "keep_post"
            ]
            stage_report["keepPostNodes"] = sorted(row["node"] for row in keep_post_rows)
            retention_checks = []
            for row in keep_post_rows:
                node = row["node"]
                node_ok = True
                for destination in sorted(str(value) for value in row.get("writes", [])):
                    sources = cmds.listConnections(
                        destination, source=True, destination=False, plugs=True
                    ) or []
                    connected = any(str(src).split(".", 1)[0] == node for src in sources)
                    if not connected:
                        node_ok = False
                retention_checks.append({"node": node, "allWritesRetained": node_ok})
            stage_report["keepPostRetentionCheck"] = retention_checks
            stage_report["keepPostAllRetained"] = all(
                item["allWritesRetained"] for item in retention_checks
            )
            if not stage_report["keepPostAllRetained"]:
                raise RuntimeError(
                    f"[{mode}] keep_post writer(s) lost after control rig creation: "
                    f"{retention_checks}"
                )

            effectors, new_transforms = _discover_effectors(before_transforms, after_transforms)
            stage_report["newTransformsAfterControlRig"] = new_transforms
            stage_report["discoveredEffectors"] = effectors
            _log(f"[{mode}] discovered effectors: {effectors}")
            if not effectors:
                _log(f"[{mode}] WARN: no hand/foot effector transforms matched by name pattern")

            for index, node in enumerate(effectors):
                _move_effectors([node], f"{mode}:move-{index}:{node}")
                move_finding = _cycle_state(f"{mode}:post-move-{index}:{node}")
                move_bucket = _bucket_cycle_plugs(move_finding["cyclePlugs"])
                if move_bucket["controlRigRelated"]:
                    raise RuntimeError(
                        f"[{mode}] control-rig-related DG cycle after moving {node}: "
                        f"{move_bucket['controlRigRelated']}"
                    )

            restored = session.restore_mmd_rig()
            stage_report["restoreMmdRigReturned"] = bool(restored)
            if bool(mel.eval(f'hikHasControlRig("{character}")')):
                raise RuntimeError(
                    f"[{mode}] control rig still present after restore_mmd_rig: {character}"
                )

            after_restore_snapshot, after_restore_census = _snapshot(binding.assignments)
            stage_report["writerCensusAfterRestore"] = after_restore_census
            stage_report["diffRestoreVsBaseline"] = diff_humanik_connections(
                before_snapshot, after_restore_snapshot
            )
            if stage_report["diffRestoreVsBaseline"]:
                raise RuntimeError(
                    f"[{mode}] writer topology after restore_mmd_rig does not match the "
                    f"pre-characterize baseline: {stage_report['diffRestoreVsBaseline']}"
                )

            post_restore_finding = _cycle_state(f"{mode}:post-restore")
            post_restore_bucket = _bucket_cycle_plugs(post_restore_finding["cyclePlugs"])
            stage_report["cyclePostRestore"] = {
                "count": len(post_restore_finding["cyclePlugs"]),
                **post_restore_bucket,
            }
            regressed = new_cycle_plugs(
                baseline_bucket["controlRigRelated"], post_restore_bucket["controlRigRelated"]
            )
            if regressed or sorted(post_restore_bucket["controlRigRelated"]) != sorted(
                baseline_bucket["controlRigRelated"]
            ):
                raise RuntimeError(
                    f"[{mode}] cycle state after restore_mmd_rig does not match the "
                    f"pre-characterize baseline: {post_restore_bucket['controlRigRelated']} "
                    f"vs baseline {baseline_bucket['controlRigRelated']}"
                )

            return stage_report

        def _pump_idle_events() -> None:
            """Flush Maya's idle queue so ``executeDeferred``/``scriptJob(idle)``
            callbacks scheduled by ``humanik_control_rig_watch`` actually run.

            An automated commandPort session with no real OS input focus never
            reaches a genuine idle tick on its own (verified empirically: a
            deferred job scheduled via ``maya.utils.executeDeferred`` after
            ``hikCreateControlRig()`` in this harness did not fire even after
            several real-wall-clock seconds and ``maya.utils.processIdleEvents()``
            calls). ``cmds.flushIdleQueue()`` is the mechanism that reliably
            drains it here -- ``processIdleEvents()`` alone was not enough in
            this environment. In normal interactive use this pump is
            unnecessary: mouse movement and UI interaction generate idle ticks
            naturally, so ``humanik_control_rig_watch`` never needs
            ``flushIdleQueue()`` itself.
            """
            try:
                import maya.utils as maya_utils
            except Exception as exc:
                _log(f"WARN maya.utils unavailable for idle pump: {exc}")
                maya_utils = None
            for _ in range(6):
                try:
                    cmds.refresh()
                except Exception:
                    pass
                if maya_utils is not None:
                    try:
                        maya_utils.processIdleEvents()
                    except Exception as exc:
                        _log(f"WARN processIdleEvents failed: {exc}")
                try:
                    cmds.flushIdleQueue()
                except Exception as exc:
                    _log(f"WARN flushIdleQueue failed: {exc}")

        def _pump_idle_events_until(predicate, *, max_attempts: int = 20) -> bool:
            """Repeat :func:`_pump_idle_events` until ``predicate()`` is true.

            A single ``_pump_idle_events`` pass drains whatever is queued at
            call time, but a busy session (this script runs the
            ``transaction_frontend`` stage before ``standard_ui_warning``)
            can have a deep backlog of unrelated deferred jobs queued by
            earlier stages' own Control Rig creations -- each firing a
            ``humanik_control_rig_watch`` node-added event of its own -- ahead
            of the one this stage cares about, and firing one deferred job
            can itself schedule another. Looping with a bounded attempt count
            (rather than a fixed pass count) is the robust way to wait for a
            specific outcome without guessing how deep that backlog is.
            """
            for attempt in range(max_attempts):
                if predicate():
                    return True
                _pump_idle_events()
            return bool(predicate())

        def _run_standard_ui_warning_stage() -> dict:
            """Exercise Control Rig creation via Maya's standard HumanIK UI.

            Characterizes through the mmd_tools frontend (installed as the
            module-level session via ``humanik_menu_actions.set_humanik_session``
            so ``humanik_control_rig_watch`` can resolve a binding for it, the
            same way it would from the real MMD > HumanIK menu), then creates
            the Control Rig with RAW MEL (``hikSetCurrentCharacter`` +
            ``hikCreateControlRig()``) instead of
            ``session.create_control_rig()`` -- simulating a user driving
            Character Controls directly.

            The auto-adoption requirement for this path was dropped (see
            ``TODO.md``): ``humanik_control_rig_watch`` is warn-only now, so
            this stage only asserts two things, both raising (recorded as a
            script error) on failure:

            1. The watch's warning was actually emitted (script-editor
               history capture, same mechanism used for cycle warnings
               elsewhere in this script).
            2. The watch performed NO scene mutation of its own: the writer
               census taken right after ``hikCreateControlRig()`` (before any
               idle events are pumped) is identical to the census taken after
               pumping idle events long enough for the watch's deferred
               handler (with its character-resolution retries) to run.

            This stage deliberately does NOT gate on cycle count or on
            ``mute_for_hik`` writers being disconnected: the raw-MEL rig here
            still has its known historical DG cycle (see git history /
            docs-dev for that evidence) and that is expected now that the
            cycle gate for this path was dropped -- only ``transaction_frontend``
            (the mmd_tools-owned creation path) is gated on cycle-free
            behavior.
            """
            mode = "standard_ui_warning"
            stage_report = {"mode": mode, "legacy": False}
            _log(f"--- stage: {mode} ---")
            model_root = _import_model()
            stage_report["modelRoot"] = model_root
            result = resolve_scene_humanik_assignments(model_root, cmds_module=cmds)
            if not result.assignments:
                raise RuntimeError(f"[{mode}] no HumanIK assignments resolved for {model_root}")

            session = HumanIkFrontendSession(cmds_module=cmds, mel_module=mel)
            humanik_menu_actions.set_humanik_session(session)
            binding = session.setup_and_characterize(model_root)
            character = binding.character
            stage_report["character"] = character
            _cycle_state(f"{mode}:post-characterize")

            if get_active_control_rig_transaction(character) is not None:
                raise RuntimeError(
                    f"[{mode}] unexpected pre-existing control rig transaction for {character}"
                )

            history_before_create = (
                script_history_path.read_text(encoding="utf-8", errors="replace")
                if script_history_path.exists()
                else ""
            )

            # Simulate Maya's standard HumanIK UI: raw MEL, deliberately NOT
            # session.create_control_rig().
            mel.eval(f'hikSetCurrentCharacter("{character}");')
            mel.eval("hikCreateControlRig();")

            if not bool(mel.eval(f'hikHasControlRig("{character}")')):
                raise RuntimeError(f"[{mode}] hikCreateControlRig did not create a control rig")

            snapshot_after_create, census_after_create = _snapshot(binding.assignments)
            stage_report["writerCensusAfterCreate"] = census_after_create

            def _warning_seen() -> bool:
                if not script_history_path.exists():
                    return False
                text = script_history_path.read_text(encoding="utf-8", errors="replace")
                return "outside MMD Tools" in text[len(history_before_create):]

            _pump_idle_events_until(_warning_seen)

            snapshot_after_pump, census_after_pump = _snapshot(binding.assignments)
            stage_report["writerCensusAfterPump"] = census_after_pump

            stage_report["watchMutatedWriters"] = diff_humanik_connections(
                snapshot_after_create, snapshot_after_pump
            )
            if stage_report["watchMutatedWriters"]:
                raise RuntimeError(
                    f"[{mode}] humanik_control_rig_watch mutated writer topology: "
                    f"{stage_report['watchMutatedWriters']}"
                )

            if get_active_control_rig_transaction(character) is not None:
                raise RuntimeError(
                    f"[{mode}] humanik_control_rig_watch registered a transaction "
                    f"(it must be warn-only): {character}"
                )

            stage_report["warningInScriptHistory"] = _warning_seen()
            if not stage_report["warningInScriptHistory"]:
                raise RuntimeError(
                    f"[{mode}] humanik_control_rig_watch warning text not found in "
                    "script editor history"
                )

            # Evidence only, not gated: the raw-MEL rig's own historical cycle
            # is expected here now that the cycle gate for this path was
            # dropped (see the stage docstring).
            post_create_finding = _cycle_state(f"{mode}:post-create")
            stage_report["cyclePostCreate"] = {
                "count": len(post_create_finding["cyclePlugs"]),
                **_bucket_cycle_plugs(post_create_finding["cyclePlugs"]),
            }

            return stage_report

        for stage_fn, key in (
            (_run_transaction_frontend_stage, "transactionStagePassed"),
            (_run_standard_ui_warning_stage, "standardUiWarningStagePassed"),
        ):
            passed = False
            try:
                stage = stage_fn()
                report["stages"].append(stage)
                passed = True
            except Exception:
                report["errors"].append(f"[{stage_fn.__name__}] {traceback.format_exc()}")
                _log(f"EXCEPTION in stage {stage_fn.__name__}:\n{traceback.format_exc()}")
            report[key] = passed

        try:
            cmds.scriptEditorInfo(writeHistory=False)
        except Exception:
            pass
        if script_history_path.exists():
            try:
                history_text = script_history_path.read_text(encoding="utf-8", errors="replace")
                cycle_lines = [line for line in history_text.splitlines() if "cycle" in line.lower()]
                report["scriptEditorCycleWarnings"] = cycle_lines[-200:]
            except Exception as exc:
                _log(f"WARN could not read script editor history: {exc}")

        # Status semantics: overall pass requires BOTH gates -- the real
        # mmd_tools-owned creation path stays cycle-free end to end
        # (transaction_frontend), and the out-of-band standard-UI path warns
        # without mutating the scene (standard_ui_warning). Any error in
        # either means the fix regressed.
        if report["errors"]:
            report["status"] = "error"
        elif report["transactionStagePassed"] and report["standardUiWarningStagePassed"]:
            report["status"] = "pass"
        elif report["cycleFindings"]:
            report["status"] = "stop"
        else:
            report["status"] = "pass"

        _write_report()
        _log(
            "RESULT_JSON: "
            + json.dumps(
                {
                    "status": report["status"],
                    "cycleFindingCount": len(report["cycleFindings"]),
                    "errorCount": len(report["errors"]),
                    "transactionStagePassed": report["transactionStagePassed"],
                    "standardUiWarningStagePassed": report["standardUiWarningStagePassed"],
                }
            )
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

    ap = argparse.ArgumentParser(description="E2E HumanIK Control Rig DG-cycle evidence capture")
    ap.add_argument("--maya", default="2026")
    ap.add_argument("--model", required=True)
    ap.add_argument("--port", type=int, default=COMMAND_PORT)
    args = ap.parse_args()

    project_root = Path(__file__).resolve().parents[2]
    log_dir = project_root / "build" / "e2e"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "humanik_control_rig_cycle_e2e.log"
    if log_path.exists():
        log_path.unlink()
    report_path = project_root / "build" / "reports" / "humanik_control_rig_cycle_e2e.json"

    model_posix = Path(args.model).resolve().as_posix()
    maya_exe = maya_commandport.maya_exe(args.maya)
    logger.info("Maya: %s", maya_exe)

    proc = maya_commandport.launch_maya(
        version=args.maya,
        project_root=project_root,
        output_dir=log_dir,
        port=args.port,
        launch_mode="explorer" if sys.platform == "win32" else "direct",
    )
    maya_out = log_dir / "maya_stdout.log"
    maya_err = log_dir / "maya_stderr.log"

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
            "from tests.viewport.e2e_humanik_control_rig_cycle import run_e2e_check\n"
            f"run_e2e_check(r'{log_path.as_posix()}', r'{model_posix}', r'{report_path.as_posix()}')\n"
        )
        maya_commandport.send_python(args.port, command, label="<humanik-control-rig-cycle-e2e-command>")
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
            raise TimeoutError(f"E2E check did not finish within {TEST_TIMEOUT}s")

        if result_json:
            logger.info("=== RESULT ===")
            logger.info("status: %s", result_json.get("status"))
            logger.info("cycle findings: %s", result_json.get("cycleFindingCount"))
            logger.info("errors: %s", result_json.get("errorCount"))
            logger.info("report: %s", report_path)
            # "stop" (cycle reproduced) is the expected/interesting outcome for
            # this diagnostics script, not a script failure.
            return 0 if result_json.get("status") in ("pass", "stop") else 1
        return 1

    finally:
        maya_commandport.quit_maya(args.port)
        time.sleep(3)
        if proc is not None and proc.poll() is None:
            proc.terminate()
        maya_commandport.close_process_logs(proc)

        for lf in [maya_out, maya_err]:
            if lf.exists() and lf.stat().st_size > 0:
                lines = lf.read_text(encoding="utf-8", errors="replace").splitlines()
                tail = lines[-20:] if len(lines) > 20 else lines
                logger.info("--- %s (last %d lines) ---", lf.name, len(tail))
                for ln in tail:
                    print(f"  {ln}")


if __name__ == "__main__":
    sys.exit(main())
