"""Maya GUI/commandPort probe for baking a live HumanIK retarget to Control Rig.

The probe uses the smallest checked-in PMX/VMD pair by default, imports motion
onto SOURCE, characterizes SOURCE/TARGET, creates the target Control Rig through
the frontend transaction, starts TARGET preview, and invokes the public
``HumanIkFrontendSession.bake_to_control_rig`` route.  It verifies that native
``hikBakeToControlRig`` leaves the Control Rig and transaction alive/editable,
then restores the full topology.  Control Rig creation requires an interactive
Maya GUI; a batch/licensing/GUI obstacle is recorded verbatim as ``blocked`` in
the JSON report rather than being reported as a fabricated pass.

Host-side usage::

    python tests/viewport/humanik_bake_to_control_rig_probe.py --maya 2024

Report JSON defaults to ``build/reports/humanik_bake_to_control_rig_probe.json``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.common import maya_commandport

COMMAND_PORT = 7727
COMPLETION_MARKER = "//-- HUMANIK_BAKE_TO_CONTROL_RIG_PROBE_DONE --//"
TEST_TIMEOUT = 900
LOG_POLL_INTERVAL = 1.0

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _is_gui_obstacle(error: str) -> bool:
    """Classify known commandPort/HumanIK interactive-runtime blockers."""
    text = str(error).lower()
    return any(
        marker in text
        for marker in (
            "about -batch",
            "batch mode",
            "requires an interactive",
            "humanik character controls ui",
            "license",
            "licensing",
            "could not open commandport",
            "maya executable not found",
        )
    )


def _write_report(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


# ===================================================================
# Maya-side: called inside the live GUI through commandPort
# ===================================================================
def run_probe(
    log_path: str,
    pmx_path: str,
    vmd_path: str,
    report_path: str,
    end: int = 10,
    setup_rig: bool = False,
) -> None:
    import traceback

    import maya.cmds as cmds
    import maya.mel as mel

    from mmd_tools.core.humanik_frontend import HumanIkFrontendSession

    report: Dict[str, Any] = {
        "status": "error",
        "mayaVersion": None,
        "pmxPath": pmx_path,
        "vmdPath": vmd_path,
        "setupRig": bool(setup_rig),
        "frameRange": {"start": 0, "end": int(end)},
        "checks": {},
        "errors": [],
    }
    session: Optional[HumanIkFrontendSession] = None

    def _log(message: str) -> None:
        with open(log_path, "a", encoding="utf-8") as stream:
            stream.write(str(message) + "\n")
        try:
            print(message)
        except Exception:
            pass

    def _load_plugin() -> None:
        plugin_path = _PROJECT_ROOT / "mmd_tools" / "plugin_main.py"
        if not cmds.pluginInfo(plugin_path.stem, query=True, loaded=True):
            cmds.loadPlugin(str(plugin_path), quiet=True)

    def _import_model(path: Path, *, setup_rig: bool = False) -> str:
        from mmd_tools.io.mmd_importer import import_mmd_file

        root = import_mmd_file(
            str(path),
            options={
                "use_namespace": True,
                "setup_rig": bool(setup_rig),
                "import_physics": False,
                "create_mmd_shaders": False,
                "use_native_pmx_parse": False,
                "require_native_pmx_parse": False,
            },
        )
        if not root:
            raise RuntimeError(f"PMX import failed: {path}")
        return str(root)

    def _import_motion(path: Path, model: Path, target_root: str) -> None:
        from mmd_tools.io.mmd_importer import import_mmd_file

        if not import_mmd_file(
            str(path),
            options={
                "target_model": target_root,
                "pmx_path": str(model),
                "bake_mode": False,
                "clear_existing_motion": True,
                "use_native_pmx_parse": False,
                "require_native_pmx_parse": False,
            },
        ):
            raise RuntimeError(f"VMD import failed: {path}")

    def _restore() -> None:
        if session is None:
            return
        try:
            report["restoreMmdRigReturned"] = bool(session.restore_mmd_rig())
            report["stateAfterRestore"] = session.describe_frontend_state(report.get("targetRoot"))
            target_character = report.get("targetCharacter")
            report["controlRigClearedAfterRestore"] = not bool(
                target_character
                and mel.eval(f'hikHasControlRig("{target_character}")')
            )
            report["previewClearedAfterRestore"] = session.active_preview is None
            report["transactionCountAfterRestore"] = len(
                [item for item in session._control_rig_transactions.values() if item.active]
            )
        except Exception as exc:  # noqa: BLE001 - report exact restore obstacle
            report["restoreError"] = str(exc)
            report["errors"].append("restore: " + traceback.format_exc())

    try:
        report["mayaVersion"] = cmds.about(version=True)
        pmx = Path(pmx_path).resolve()
        vmd = Path(vmd_path).resolve()
        if not pmx.is_file() or not vmd.is_file():
            raise FileNotFoundError(f"Fixtures not found: pmx={pmx} vmd={vmd}")
        cmds.file(new=True, force=True)
        _load_plugin()

        # The compact fixture's optional MMD IK rig contributes manual writer
        # blockers to TARGET preview.  HIK bake only needs characterized joints
        # and source animCurves, so keep both imports in the minimal direct-joint
        # mode used by the retarget smoke paths.
        source_root = _import_model(pmx, setup_rig=setup_rig)
        target_root = _import_model(pmx, setup_rig=setup_rig)
        report.update({"sourceRoot": source_root, "targetRoot": target_root})

        session = HumanIkFrontendSession(cmds_module=cmds, mel_module=mel)
        source_binding = session.setup_and_characterize(source_root)
        session.enter_source_mode(source_root)
        _import_motion(vmd, pmx, source_root)
        source_anim_curve_count = len(cmds.ls(type="animCurve") or [])
        target_binding = session.setup_and_characterize(target_root)
        report.update(
            {
                "sourceCharacter": source_binding.character,
                "targetCharacter": target_binding.character,
                "sourceAssignmentCount": len(source_binding.assignments),
                "targetAssignmentCount": len(target_binding.assignments),
                "sourceAnimCurveCountAfterVmd": source_anim_curve_count,
            }
        )

        session.create_control_rig(target_root)
        target_character = target_binding.character
        rig_before_preview = bool(mel.eval(f'hikHasControlRig("{target_character}")'))
        session.enter_target_mode(target_root)
        state_before = session.describe_frontend_state(target_root)
        transaction = session._control_rig_transactions.get(target_root)
        report["stateBeforeBake"] = state_before
        report["checks"].update(
            {
                "sourceMotionImported": source_anim_curve_count > 0,
                "targetControlRigBeforePreview": rig_before_preview,
                "previewActiveBeforeBake": session.active_preview is not None,
                "transactionActiveBeforeBake": bool(transaction and transaction.active),
            }
        )

        bake_result = session.bake_to_control_rig(0, int(end))
        transaction_after = session._control_rig_transactions.get(target_root)
        input_type = int(mel.eval(f'hikGetInputType("{target_character}")'))
        state_after = session.describe_frontend_state(target_root)
        report.update(
            {
                "bakeResult": bake_result.to_dict(),
                "stateAfterBake": state_after,
                "targetInputTypeAfterBake": input_type,
            }
        )
        report["checks"].update(
            {
                "nativeControlRigAfterBake": bool(
                    mel.eval(f'hikHasControlRig("{target_character}")')
                ),
                "transactionActiveAfterBake": bool(transaction_after and transaction_after.active),
                "previewActiveAfterBake": session.active_preview is not None,
                "controlRigCountAfterBake": len(state_after.get("controlRigs") or []),
                # hikBakeToControlRigPost switches the character to the live
                # Control Rig input (Maya 2024 reports the native enum as 1;
                # direct SOURCE input is 3).
                "controlRigInputAfterBake": input_type == 1,
            }
        )
        if not all(report["checks"].values()):
            raise RuntimeError(f"Control Rig bake acceptance failed: {report['checks']}")
        report["status"] = "pass"
    except Exception as exc:  # noqa: BLE001 - completion/report must always be emitted
        text = str(exc)
        report["error"] = text
        report["errors"].append(traceback.format_exc())
        report["status"] = "blocked" if _is_gui_obstacle(text) else "error"
    finally:
        _restore()
        if report["status"] == "pass":
            restore_ok = all(
                (
                    report.get("restoreMmdRigReturned"),
                    report.get("controlRigClearedAfterRestore"),
                    report.get("previewClearedAfterRestore"),
                    report.get("transactionCountAfterRestore") == 0,
                )
            )
            if not restore_ok:
                report["status"] = "error"
                report["errors"].append("Restore acceptance failed")
        _write_report(Path(report_path), report)
        _log("RESULT_JSON: " + json.dumps({"status": report["status"], "error": report.get("error")}))
        _log(COMPLETION_MARKER)


# ===================================================================
# Host-side launcher/commandPort driver
# ===================================================================
def main() -> int:
    parser = argparse.ArgumentParser(description="HumanIK bake-to-Control-Rig GUI probe")
    parser.add_argument("--maya", default="2024")
    parser.add_argument("--pmx", default="tests/data/mmt_test_model.pmx")
    parser.add_argument("--vmd", default="tests/data/mmt_test_model_test_motion.vmd")
    parser.add_argument("--end", type=int, default=10)
    parser.add_argument(
        "--setup-rig",
        action="store_true",
        help="Opt into importer setup_rig=True (retains MMD IK nodes to reproduce blocker evidence).",
    )
    parser.add_argument("--port", type=int, default=COMMAND_PORT)
    parser.add_argument(
        "--out",
        default="build/reports/humanik_bake_to_control_rig_probe.json",
    )
    args = parser.parse_args()

    pmx_path = (_PROJECT_ROOT / args.pmx).resolve()
    vmd_path = (_PROJECT_ROOT / args.vmd).resolve()
    output = (_PROJECT_ROOT / args.out).resolve()
    log_dir = _PROJECT_ROOT / "build" / "e2e"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "humanik_bake_to_control_rig_probe.log"
    if log_path.exists():
        log_path.unlink()

    base_report: Dict[str, Any] = {
        "status": "error",
        "mayaVersion": None,
        "pmxPath": str(pmx_path),
        "vmdPath": str(vmd_path),
        "setupRig": bool(args.setup_rig),
        "frameRange": {"start": 0, "end": args.end},
        "errors": [],
    }
    proc = None
    try:
        if not pmx_path.is_file() or not vmd_path.is_file():
            obstacle = f"Fixtures not found: pmx={pmx_path} vmd={vmd_path}"
            base_report.update({"status": "blocked", "obstacle": obstacle})
            _write_report(output, base_report)
            logger.error(obstacle)
            return 2
        proc = maya_commandport.launch_maya(
            version=args.maya,
            project_root=_PROJECT_ROOT,
            output_dir=log_dir,
            port=args.port,
            launch_mode="explorer" if sys.platform == "win32" else "direct",
        )
        maya_commandport.wait_for_port(args.port, timeout=120, process=proc)
        command = (
            "import sys; "
            f"sys.path.insert(0, {json.dumps(str(_PROJECT_ROOT))}); "
            "from tests.viewport.humanik_bake_to_control_rig_probe import run_probe; "
            f"run_probe({json.dumps(str(log_path))}, {json.dumps(str(pmx_path))}, "
            f"{json.dumps(str(vmd_path))}, {json.dumps(str(output))}, {int(args.end)}, "
            f"{bool(args.setup_rig)})"
        )
        maya_commandport.send_python(args.port, command, label="<humanik-bake-to-control-rig-probe>")

        start = time.time()
        done = False
        while time.time() - start < TEST_TIMEOUT:
            if log_path.exists():
                for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
                    if COMPLETION_MARKER in line:
                        done = True
            if done:
                break
            time.sleep(LOG_POLL_INTERVAL)
        if not done:
            raise TimeoutError(f"Probe did not finish within {TEST_TIMEOUT}s")
        if output.exists():
            payload = json.loads(output.read_text(encoding="utf-8"))
            logger.info("probe status=%s report=%s", payload.get("status"), output)
            return 0 if payload.get("status") == "pass" else (2 if payload.get("status") == "blocked" else 1)
        return 1
    except Exception as exc:  # exact launcher/GUI obstacle goes into report
        obstacle = str(exc)
        base_report.update(
            {
                "status": "blocked" if _is_gui_obstacle(obstacle) else "error",
                "obstacle": obstacle,
                "errors": [obstacle],
            }
        )
        _write_report(output, base_report)
        logger.error("probe did not run: %s", obstacle)
        return 2 if base_report["status"] == "blocked" else 1
    finally:
        if proc is not None:
            try:
                maya_commandport.quit_maya(args.port)
            except Exception:
                pass
            maya_commandport.close_process_logs(proc)


if __name__ == "__main__":
    raise SystemExit(main())
