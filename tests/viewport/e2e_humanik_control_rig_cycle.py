"""E2E evidence capture: DG cycle around HumanIK Control Rig creation.

This script reproduces (or rules out) the reported DG cycle that spans
``HIKState2SK.*``, ``pairBlend.outTranslate*``, ``mmdCcdIk.outputRotate``, and
primary joint ``translate``/``parentMatrix`` after ``hikCreateControlRig()``
is run on an MMD-imported model, then moving Control Rig effectors
(hands/feet).

It does NOT disable ``cycleCheck`` -- the goal is to capture the exact
cyclic plugs/edges as JSON evidence, not to hide them.

Two code paths are exercised in the same Maya session on fresh imports:

* ``frontend`` -- ``HumanIkFrontendSession.setup_and_characterize()`` then
  ``.create_control_rig()`` (the mmd_tools UI-neutral frontend, which
  initializes the Character Controls UI before calling
  ``hikCreateControlRig()``; see ``humanik_builder.create_humanik_control_rig``).
* ``raw_mel`` -- ``humanik_builder.create_humanik_definition_from_scene(...,
  create_control_rig=True)``, which characterizes and calls
  ``hikCreateControlRig();`` directly via the MEL command builder without the
  UI-init guard (closer to a bare ``hikSetCurrentCharacter`` +
  ``hikCreateControlRig`` MEL sequence).

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

        from mmd_tools.core.humanik_builder import (
            create_humanik_definition_from_scene,
            resolve_scene_humanik_assignments,
        )
        from mmd_tools.core.humanik_constraints import (
            classify_humanik_constraints,
            collect_humanik_constraint_facts,
        )
        from mmd_tools.core.humanik_frontend import HumanIkFrontendSession
        from mmd_tools.core.humanik_preview import _disconnect_reviewed_writers
        from mmd_tools.core.humanik_retarget import (
            collect_humanik_incoming_writer_census,
            diff_humanik_connections,
        )

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

        def _run_stage(mode: str) -> dict:
            """Run one full characterize -> control-rig -> move-effectors pass."""
            stage_report = {"mode": mode}
            _log(f"--- stage: {mode} ---")
            model_root = _import_model()
            stage_report["modelRoot"] = model_root
            result = resolve_scene_humanik_assignments(model_root, cmds_module=cmds)
            if not result.assignments:
                raise RuntimeError(f"[{mode}] no HumanIK assignments resolved for {model_root}")
            stage_report["assignmentCount"] = len(result.assignments)

            before_snapshot, before_census = _snapshot(result.assignments)
            stage_report["writerCensusBefore"] = before_census
            _cycle_state(f"{mode}:pre-characterize")

            character = None
            if mode == "frontend":
                session = HumanIkFrontendSession(cmds_module=cmds, mel_module=mel)
                binding = session.setup_and_characterize(model_root)
                character = binding.character
                stage_report["character"] = character
                after_characterize_snapshot, after_characterize_census = _snapshot(binding.result.assignments)
                stage_report["writerCensusAfterCharacterize"] = after_characterize_census
                stage_report["diffAfterCharacterize"] = diff_humanik_connections(
                    before_snapshot, after_characterize_snapshot
                )
                _cycle_state(f"{mode}:post-characterize")

                before_transforms = set(cmds.ls(type="transform", long=True) or [])
                session.create_control_rig(model_root)
                after_transforms = set(cmds.ls(type="transform", long=True) or [])
                after_rig_snapshot, after_rig_census = _snapshot(binding.result.assignments)
            else:  # raw_mel
                character = create_humanik_definition_from_scene(
                    model_root,
                    name_hint="MMDToolsCycleE2E_Raw",
                    cmds_module=cmds,
                    mel_module=mel,
                    create_control_rig=False,
                    update_ui=False,
                )
                stage_report["character"] = character
                after_characterize_snapshot, after_characterize_census = _snapshot(result.assignments)
                stage_report["writerCensusAfterCharacterize"] = after_characterize_census
                stage_report["diffAfterCharacterize"] = diff_humanik_connections(
                    before_snapshot, after_characterize_snapshot
                )
                _cycle_state(f"{mode}:post-characterize")

                before_transforms = set(cmds.ls(type="transform", long=True) or [])
                mel.eval(f'hikSetCurrentCharacter("{character}");')
                mel.eval("hikCreateControlRig();")
                after_transforms = set(cmds.ls(type="transform", long=True) or [])
                if not bool(mel.eval(f'hikHasControlRig("{character}")')):
                    raise RuntimeError(f"[{mode}] hikCreateControlRig did not create a control rig")
                after_rig_snapshot, after_rig_census = _snapshot(result.assignments)

            stage_report["writerCensusAfterControlRig"] = after_rig_census
            stage_report["diffAfterControlRig"] = diff_humanik_connections(
                after_characterize_snapshot, after_rig_snapshot
            )
            _cycle_state(f"{mode}:post-control-rig")

            effectors, new_transforms = _discover_effectors(before_transforms, after_transforms)
            stage_report["newTransformsAfterControlRig"] = new_transforms
            stage_report["discoveredEffectors"] = effectors
            _log(f"[{mode}] discovered effectors: {effectors}")

            if not effectors:
                _log(f"[{mode}] WARN: no hand/foot effector transforms matched by name pattern")

            for index, node in enumerate(effectors):
                _move_effectors([node], f"{mode}:move-{index}:{node}")
                _cycle_state(f"{mode}:post-move-{index}:{node}")

            stage_report["controlRigCreated"] = bool(
                mel.eval(f'hikHasControlRig("{character}")')
            )
            return stage_report

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

        def _run_isolation_validation() -> dict:
            """Validate Option A: mute mmdAppend/mmdCcdIk writers into HIK-owned
            joint channels (the same edges begin_humanik_target_preview mutes
            via _disconnect_reviewed_writers), then re-check for the SCC.
            """
            mode = "isolation_validation"
            stage_report = {"mode": mode}
            _log(f"--- stage: {mode} ---")
            model_root = _import_model()
            stage_report["modelRoot"] = model_root
            result = resolve_scene_humanik_assignments(model_root, cmds_module=cmds)
            if not result.assignments:
                raise RuntimeError(f"[{mode}] no HumanIK assignments resolved for {model_root}")

            session = HumanIkFrontendSession(cmds_module=cmds, mel_module=mel)
            binding = session.setup_and_characterize(model_root)
            character = binding.character
            stage_report["character"] = character
            _cycle_state(f"{mode}:post-characterize")

            session.create_control_rig(model_root)
            if not bool(mel.eval(f'hikHasControlRig("{character}")')):
                raise RuntimeError(f"[{mode}] hikCreateControlRig did not create a control rig")
            post_rig_finding = _cycle_state(f"{mode}:post-control-rig")
            stage_report["cyclePostControlRig"] = {
                "count": len(post_rig_finding["cyclePlugs"]),
                "plugs": post_rig_finding["cyclePlugs"],
                **_bucket_cycle_plugs(post_rig_finding["cyclePlugs"]),
            }

            hik_joint_set = {str(assignment.joint) for assignment in binding.result.assignments}
            facts = collect_humanik_constraint_facts(cmds_module=cmds)
            ownership_report = classify_humanik_constraints(facts, hik_joint_set)
            mute_rows = [
                row for row in ownership_report["rows"]
                if row.get("classification") == "mute_for_hik"
            ]
            keep_post_rows = [
                row for row in ownership_report["rows"]
                if row.get("classification") == "keep_post"
            ]
            stage_report["muteForHikNodes"] = sorted(row["node"] for row in mute_rows)
            stage_report["keepPostNodes"] = sorted(row["node"] for row in keep_post_rows)

            disconnected: list = []
            _disconnect_reviewed_writers(cmds, mute_rows, disconnected)
            disconnected = sorted(disconnected, key=lambda row: (row["destination"], row["source"]))
            stage_report["disconnectedEdges"] = disconnected
            _log(f"[{mode}] disconnected {len(disconnected)} mute_for_hik writer edges")

            post_isolation_finding = _cycle_state(f"{mode}:post-isolation")
            stage_report["cyclePostIsolation"] = {
                "count": len(post_isolation_finding["cyclePlugs"]),
                "plugs": post_isolation_finding["cyclePlugs"],
                **_bucket_cycle_plugs(post_isolation_finding["cyclePlugs"]),
            }

            retention_checks = []
            for row in keep_post_rows:
                node = row["node"]
                node_ok = True
                details = []
                for destination in sorted(str(value) for value in row.get("writes", [])):
                    sources = cmds.listConnections(
                        destination, source=True, destination=False, plugs=True
                    ) or []
                    connected = any(str(src).split(".", 1)[0] == node for src in sources)
                    details.append({"destination": destination, "stillConnected": connected})
                    if not connected:
                        node_ok = False
                retention_checks.append({"node": node, "allWritesRetained": node_ok, "details": details})
            stage_report["keepPostRetentionCheck"] = retention_checks
            stage_report["keepPostAllRetained"] = all(
                item["allWritesRetained"] for item in retention_checks
            )
            _log(
                f"[{mode}] keep_post retention: "
                f"{stage_report['keepPostAllRetained']} ({len(retention_checks)} nodes)"
            )

            reconnected = []
            for edge in disconnected:
                try:
                    cmds.connectAttr(edge["source"], edge["destination"], force=True)
                    reconnected.append({**edge, "reconnected": True})
                except Exception as exc:
                    reconnected.append({**edge, "reconnected": False, "error": str(exc)})
                    _log(f"WARN reconnect failed {edge}: {exc}")
            stage_report["reconnectedEdges"] = reconnected

            post_reconnect_finding = _cycle_state(f"{mode}:post-reconnect")
            stage_report["cyclePostReconnect"] = {
                "count": len(post_reconnect_finding["cyclePlugs"]),
                "plugs": post_reconnect_finding["cyclePlugs"],
                **_bucket_cycle_plugs(post_reconnect_finding["cyclePlugs"]),
            }
            return stage_report

        for mode in ("frontend", "raw_mel"):
            try:
                stage = _run_stage(mode)
                report["stages"].append(stage)
            except Exception:
                report["errors"].append(f"[{mode}] {traceback.format_exc()}")
                _log(f"EXCEPTION in stage {mode}:\n{traceback.format_exc()}")

        try:
            stage = _run_isolation_validation()
            report["stages"].append(stage)
        except Exception:
            report["errors"].append(f"[isolation_validation] {traceback.format_exc()}")
            _log(f"EXCEPTION in stage isolation_validation:\n{traceback.format_exc()}")

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

        if report["errors"]:
            report["status"] = "error"
        elif report["cycleFindings"]:
            report["status"] = "stop"
        else:
            report["status"] = "pass"

        _write_report()
        _log(f"RESULT_JSON: {json.dumps({'status': report['status'], 'cycleFindingCount': len(report['cycleFindings']), 'errorCount': len(report['errors'])})}")
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
