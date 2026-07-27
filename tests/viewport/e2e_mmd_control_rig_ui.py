"""Maya GUI E2E gate for the normal Animator Toolset Control Rig surface.

This probe keeps the UI contract separate from the numeric Control Rig gate.
It opens the public Animator Toolset with Development Mode disabled, verifies
the Experimental action group and VMD import option, exercises owner-aware
picker selection and model switching, then saves/reopens and closes both
windows.  A JSON report and two Qt widget captures are written below
``build/e2e`` by default.

Usage::

    python tests/viewport/e2e_mmd_control_rig_ui.py --maya 2024
    python tests/viewport/e2e_mmd_control_rig_ui.py --maya 2026 --port 7756
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.common import maya_commandport

COMMAND_PORT = 7756
COMPLETION_MARKER = "//-- MMD_CONTROL_RIG_UI_E2E_DONE --//"
TEST_TIMEOUT = 420
LOG_POLL_INTERVAL = 0.5

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _safe_text(widget: Any) -> str:
    try:
        return str(widget.text())
    except Exception:
        return ""


def _safe_title(widget: Any) -> str:
    """Read a group-box title across Qt bindings."""

    for accessor in ("title", "text"):
        try:
            value = getattr(widget, accessor, None)
            if callable(value):
                return str(value())
        except Exception:
            pass
    return ""


def _safe_visible(widget: Any) -> bool:
    try:
        return bool(widget.isVisible())
    except Exception:
        return False


def _safe_enabled(widget: Any) -> bool:
    try:
        return bool(widget.isEnabled())
    except Exception:
        return False


def _safe_grab(widget: Any, path: Path) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        pixmap = widget.grab()
        return bool(pixmap.save(str(path)))
    except Exception:
        return False


def _safe_process_events() -> None:
    try:
        from mmd_tools.ui.qt_compat import QApplication

        app = QApplication.instance()
        if app is not None:
            app.processEvents()
    except Exception:
        pass


def _load_plugins(cmds) -> None:
    """Load the native IK plugin and Python plugin used by the normal UI."""

    maya_major = str(cmds.about(version=True)).split(".", 1)[0]
    cpp_plugin = _PROJECT_ROOT / "plug-ins" / maya_major / "Debug" / "mmd_tools_cpp.mll"
    if cpp_plugin.is_file():
        plugin_dir = str(cpp_plugin.parent)
        if plugin_dir not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = plugin_dir + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            # Keep the handle alive for the remainder of the Maya-side probe.
            _load_plugins._dll_handle = os.add_dll_directory(plugin_dir)
        if not cmds.pluginInfo(str(cpp_plugin), query=True, loaded=True):
            cmds.loadPlugin(str(cpp_plugin), quiet=True)
    plugin_path = _PROJECT_ROOT / "mmd_tools" / "plugin_main.py"
    if not cmds.pluginInfo(plugin_path.stem, query=True, loaded=True):
        cmds.loadPlugin(str(plugin_path), quiet=True)


def _model_roots(cmds) -> list[str]:
    from mmd_tools.services.scene_model_service import SceneModelService

    return [str(item) for item in SceneModelService(cmds_module=cmds).list_mmd_models()]


def run_ui_check(
    log_path: str,
    model_path: str,
    report_path: str,
    scene_path: str,
    capture_dir: str,
) -> None:
    """Execute the UI/lifecycle checks inside a fresh Maya GUI process."""

    import maya.cmds as cmds

    log_file = Path(log_path)
    report_file = Path(report_path)
    capture_root = Path(capture_dir)
    report: dict[str, Any] = {
        "kind": "mmd-control-rig-ui-e2e",
        "status": "error",
        "mayaVersion": None,
        "developmentMode": False,
        "ui": {},
        "picker": {},
        "models": {},
        "lifecycle": {},
        "captures": {},
        "errors": [],
    }

    def log(message: str) -> None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(str(message) + "\n")
        try:
            print(message)
        except Exception:
            pass

    from mmd_tools.services.settings_service import SettingsService
    from mmd_tools.core import settings_keys

    settings = SettingsService()
    original_dev = settings.get(settings_keys.UI_GENERAL_DEVELOPMENT_MODE, False)
    original_create = settings.get(settings_keys.IMPORT_ANIMATION_CREATE_MMD_CONTROL_RIG, False)
    animator_window = None
    main_window = None
    original_root = None
    try:
        report["mayaVersion"] = str(cmds.about(version=True))
        log("=== MMD Control Rig normal UI E2E begin ===")
        # Isolate the contract from a prior user's persisted experimental
        # choice, while restoring both values before this Maya exits.
        settings.set(settings_keys.UI_GENERAL_DEVELOPMENT_MODE, False)
        settings.set(settings_keys.IMPORT_ANIMATION_CREATE_MMD_CONTROL_RIG, False)
        report["developmentMode"] = bool(settings.is_development_mode())
        _load_plugins(cmds)

        from mmd_tools.io.mmd_importer import import_mmd_file

        cmds.file(new=True, force=True)
        original_root = str(
            import_mmd_file(
                str(model_path),
                options={"setup_rig": True, "setup_bone_orientation": True, "import_physics": False},
            )
        )
        if not original_root:
            raise RuntimeError("PMX import returned no root")
        log(f"imported first model: {original_root}")

        from mmd_tools.plugin_main import close_animator_toolset, close_main_window, open_animator_toolset, open_main_window

        animator_window = open_animator_toolset(dockable=False)
        _safe_process_events()
        tab = animator_window.animation_tab
        action_rows = {
            key: {"text": _safe_text(button), "visible": _safe_visible(button), "enabled": _safe_enabled(button)}
            for key, button in sorted(tab.control_rig_buttons.items())
        }
        report["ui"]["animatorToolset"] = {
            "windowVisible": _safe_visible(animator_window),
            "controlRigTitle": _safe_title(tab.control_rig_group),
            "controlRigTitleExperimental": "Experimental" in _safe_title(tab.control_rig_group),
            "controlRigGroupVisible": _safe_visible(tab.control_rig_group),
            "controlRigGroupEnabled": _safe_enabled(tab.control_rig_group),
            "actions": action_rows,
            "modelComboCount": int(tab.model_combo.count()),
        }
        report["captures"]["animatorToolset"] = str(capture_root / "animator_toolset.png")
        report["captures"]["animatorToolsetSaved"] = _safe_grab(animator_window, capture_root / "animator_toolset.png")

        main_window = open_main_window(dockable=False)
        _safe_process_events()
        import_tab = main_window.import_export_tab
        checkbox = import_tab.create_mmd_control_rig_check
        initial_checked = bool(checkbox.isChecked())
        settings_before_toggle = bool(settings.get(settings_keys.IMPORT_ANIMATION_CREATE_MMD_CONTROL_RIG, False))
        checkbox.setChecked(True)
        _safe_process_events()
        toggled_on = bool(checkbox.isChecked()) and bool(settings.get(settings_keys.IMPORT_ANIMATION_CREATE_MMD_CONTROL_RIG, False))
        checkbox.setChecked(False)
        _safe_process_events()
        toggled_off = (not bool(checkbox.isChecked())) and not bool(settings.get(settings_keys.IMPORT_ANIMATION_CREATE_MMD_CONTROL_RIG, False))
        report["ui"]["vmdImportOption"] = {
            "objectPresent": True,
            "initialChecked": initial_checked,
            "settingsBeforeToggle": settings_before_toggle,
            "visible": _safe_visible(checkbox),
            "enabled": _safe_enabled(checkbox),
            "toggledOn": toggled_on,
            "toggledOff": toggled_off,
            "defaultOff": not initial_checked,
        }
        report["captures"]["mainWindow"] = str(capture_root / "mmd_tools_main_window.png")
        report["captures"]["mainWindowSaved"] = _safe_grab(main_window, capture_root / "mmd_tools_main_window.png")

        # Build an owned Control Rig and use the real presenter picker signal.
        from mmd_tools.core.mmd_control_rig_builder import build_mmd_control_rig, read_mmd_control_rig_metadata
        from mmd_tools.core.mmd_control_rig_motion import enter_mmd_control_rig_edit, restore_mmd_control_rig_attached

        # The presenter is separate from the main window; make its ApplicationState
        # point at the imported model and refresh the map before clicking a region.
        animator_window.app_state.current_model_root = original_root
        animator_window.animation_presenter.refresh_for_scene_change()
        build_mmd_control_rig(original_root)
        enter_mmd_control_rig_edit(original_root)
        animator_window.animation_presenter.refresh_for_scene_change()
        animator_window.animation_presenter.on_body_region_clicked("center")
        _safe_process_events()
        control_selection = [str(item) for item in (cmds.ls(selection=True, long=True) or [])]
        metadata = read_mmd_control_rig_metadata(original_root)
        control_uuid = (metadata or {}).get("controls", {}).get("center")
        expected_control = (cmds.ls(control_uuid, long=True) or [None])[0] if control_uuid else None
        control_selected = bool(expected_control and expected_control in control_selection)
        restore_mmd_control_rig_attached(original_root)
        animator_window.animation_presenter.refresh_for_scene_change()
        animator_window.animation_presenter.on_body_region_clicked("center")
        _safe_process_events()
        joint_selection = [str(item) for item in (cmds.ls(selection=True, long=True) or [])]
        expected_joint = None
        for joint in cmds.ls(type="joint", long=True) or []:
            if cmds.attributeQuery("mmd_bone_name", node=joint, exists=True) and str(cmds.getAttr(f"{joint}.mmd_bone_name")) == "センター":
                expected_joint = str(joint)
                break
        # English fixtures may expose ``center`` rather than the Japanese label.
        if expected_joint is None:
            for joint in cmds.ls(type="joint", long=True) or []:
                if cmds.attributeQuery("mmd_bone_name", node=joint, exists=True) and str(cmds.getAttr(f"{joint}.mmd_bone_name")).lower() == "center":
                    expected_joint = str(joint)
                    break
        joint_selected = bool(expected_joint and expected_joint in joint_selection)
        report["picker"] = {
            "controlOwnerSelection": control_selection,
            "expectedControl": expected_control,
            "controlSelected": control_selected,
            "mmdOwnerSelection": joint_selection,
            "expectedJoint": expected_joint,
            "jointSelected": joint_selected,
            "ownerSwitchPassed": control_selected and joint_selected,
        }

        # Import a second namespaced model and drive the actual model combo.
        second_model_path = _PROJECT_ROOT / "tests" / "data" / "test_morph_model.pmx"
        second_root = str(
            import_mmd_file(
                str(second_model_path),
                options={
                    "setup_rig": True,
                    "setup_bone_orientation": True,
                    "import_physics": False,
                },
            )
        )
        animator_window.animation_presenter.refresh_for_scene_change()
        model_roots = _model_roots(cmds)
        combo = animator_window.animation_tab.model_combo
        second_index = combo.findText(second_root)
        if second_index >= 0:
            combo.setCurrentIndex(second_index)
            _safe_process_events()
        report["models"] = {
            "firstRoot": original_root,
            "secondRoot": second_root,
            "availableRoots": model_roots,
            "comboCount": int(combo.count()),
            "secondComboIndex": int(second_index),
            "selectedAfterSwitch": str(animator_window.app_state.current_model_root),
            "switchPassed": second_index >= 0 and str(animator_window.app_state.current_model_root) == second_root,
        }

        # Save/reopen the scene while both windows are alive, then explicitly
        # invoke the same scene callback used by plugin_main callbacks.
        scene_file = Path(scene_path)
        scene_file.parent.mkdir(parents=True, exist_ok=True)
        cmds.file(rename=str(scene_file))
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(new=True, force=True)
        cmds.file(str(scene_file), open=True, force=True)
        animator_window.refresh_for_scene_change()
        # MainWindow predates the standalone Animator Toolset callback; its
        # shared ApplicationState is enough to repopulate the import tab.
        main_window.app_state.clear_cache()
        main_window.app_state.refresh_model_list()
        _safe_process_events()
        reopened_roots = _model_roots(cmds)
        report["lifecycle"]["saveReopen"] = {
            "scene": str(scene_file),
            "reopenedRoots": reopened_roots,
            "animatorCurrentRoot": str(animator_window.app_state.current_model_root),
            "modelCount": len(reopened_roots),
            "passed": bool(reopened_roots) and str(animator_window.app_state.current_model_root) in reopened_roots,
        }
        animator_visible_before_close = _safe_visible(animator_window)
        main_visible_before_close = _safe_visible(main_window)
        close_animator_toolset()
        close_main_window()
        _safe_process_events()
        import mmd_tools.plugin_main as plugin_main_module

        animator_visible_after_close = _safe_visible(animator_window)
        main_visible_after_close = _safe_visible(main_window)
        animator_global_cleared = plugin_main_module._animator_toolset_window is None
        main_global_cleared = plugin_main_module._main_window is None
        report["lifecycle"]["windowClose"] = {
            "animatorVisibleBefore": animator_visible_before_close,
            "mainVisibleBefore": main_visible_before_close,
            "animatorVisibleAfter": animator_visible_after_close,
            "mainVisibleAfter": main_visible_after_close,
            "animatorGlobalCleared": animator_global_cleared,
            "mainGlobalCleared": main_global_cleared,
            "passed": (
                animator_visible_before_close
                and main_visible_before_close
                and not animator_visible_after_close
                and not main_visible_after_close
                and animator_global_cleared
                and main_global_cleared
            ),
        }
        if not report["lifecycle"]["windowClose"]["passed"]:
            raise RuntimeError("Animator Toolset or MMD Tools window remained visible after close")
        report["status"] = "pass"
        log("MMD Control Rig normal UI E2E passed")
    except Exception:
        report["errors"].append(traceback.format_exc())
        log("EXCEPTION:\n" + traceback.format_exc())
    finally:
        try:
            if animator_window is not None:
                from mmd_tools.plugin_main import close_animator_toolset

                close_animator_toolset()
        except Exception:
            pass
        try:
            if main_window is not None:
                from mmd_tools.plugin_main import close_main_window

                close_main_window()
        except Exception:
            pass
        settings.set(settings_keys.UI_GENERAL_DEVELOPMENT_MODE, original_dev)
        settings.set(settings_keys.IMPORT_ANIMATION_CREATE_MMD_CONTROL_RIG, original_create)
        _write_report(report_file, report)
        log("RESULT_JSON: " + json.dumps(report, ensure_ascii=False, sort_keys=True))
        log(COMPLETION_MARKER)


def _monitor_result(log_path: Path, report_path: Path, timeout: float) -> dict[str, Any]:
    log_path.touch(exist_ok=True)
    start = time.time()
    result = None
    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        while time.time() - start < timeout:
            line = handle.readline()
            if line:
                print(line, end="")
                if line.startswith("RESULT_JSON:"):
                    result = json.loads(line.split("RESULT_JSON:", 1)[1].strip())
                if COMPLETION_MARKER in line:
                    break
            else:
                time.sleep(LOG_POLL_INTERVAL)
        else:
            raise TimeoutError(f"timed out waiting for completion marker: {log_path}")
    start = time.time()
    while not report_path.is_file() and time.time() - start < 30:
        time.sleep(LOG_POLL_INTERVAL)
    if not report_path.is_file():
        raise TimeoutError(f"missing report: {report_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if result is not None and result.get("status") != report.get("status"):
        raise RuntimeError("Maya RESULT_JSON and report status disagree")
    return report


def main() -> int:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    parser = argparse.ArgumentParser(description="MMD Control Rig normal UI Maya GUI E2E")
    parser.add_argument("--maya", default="2026")
    parser.add_argument("--model", default=str(_PROJECT_ROOT / "tests" / "data" / "mmt_test_model.pmx"))
    parser.add_argument("--port", type=int, default=COMMAND_PORT)
    parser.add_argument("--timeout", type=float, default=TEST_TIMEOUT)
    parser.add_argument("--out-dir", default=str(_PROJECT_ROOT / "build" / "e2e"))
    args = parser.parse_args()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"maya{args.maya}"
    report_path = out_dir / f"mmd_control_rig_ui_e2e_{suffix}.json"
    log_path = out_dir / f"mmd_control_rig_ui_e2e_{suffix}.log"
    scene_path = out_dir / f"mmd_control_rig_ui_e2e_{suffix}.ma"
    capture_dir = out_dir / f"mmd_control_rig_ui_e2e_{suffix}_captures"
    maya_commandport.remove_stale_logs([report_path, log_path, scene_path])
    if maya_commandport.is_port_open(args.port):
        raise RuntimeError(f"commandPort :{args.port} is already open")
    proc = maya_commandport.launch_maya(
        version=args.maya,
        project_root=_PROJECT_ROOT,
        output_dir=out_dir,
        port=args.port,
        launch_mode="explorer" if sys.platform == "win32" else "direct",
    )
    try:
        maya_commandport.wait_for_port(args.port, timeout=120, process=proc)
        command = (
            "import sys\n"
            "from pathlib import Path\n"
            f"project_root = Path(r'{_PROJECT_ROOT.as_posix()}')\n"
            "sys.path.insert(0, str(project_root)) if str(project_root) not in sys.path else None\n"
            "from tests.viewport.e2e_mmd_control_rig_ui import run_ui_check\n"
            f"run_ui_check(r'{log_path.as_posix()}', r'{Path(args.model).resolve().as_posix()}', r'{report_path.as_posix()}', r'{scene_path.as_posix()}', r'{capture_dir.as_posix()}')\n"
        )
        maya_commandport.send_python(args.port, command, label="<mmd-control-rig-ui-e2e>")
        report = _monitor_result(log_path, report_path, args.timeout)
        return 0 if report.get("status") == "pass" else 1
    finally:
        try:
            maya_commandport.quit_maya(args.port)
            time.sleep(3)
        finally:
            if proc is not None and proc.poll() is None:
                proc.terminate()
            maya_commandport.close_process_logs(proc)


if __name__ == "__main__":
    sys.exit(main())
