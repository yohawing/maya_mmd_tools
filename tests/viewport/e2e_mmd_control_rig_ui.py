"""Maya GUI E2E gate for the normal Animator Toolset Control Rig surface.

This probe keeps the UI contract separate from the numeric Control Rig gate.
It opens the public Animator Toolset with Development Mode disabled, verifies
the Experimental action group and model-import Control Rig option, optionally
imports a real VMD through the created rig, exercises owner-aware picker
selection and model switching, then saves/reopens and closes both windows. A
JSON report and two Qt widget captures are written below
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
import traceback
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.viewport.maya_e2e_harness import run_maya_e2e

COMMAND_PORT = 7756
COMPLETION_MARKER = "//-- MMD_CONTROL_RIG_UI_E2E_DONE --//"
TEST_TIMEOUT = 420

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
    vmd_path: str = "",
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
    original_create = settings.get(settings_keys.IMPORT_MODEL_CREATE_MMD_CONTROL_RIG, False)
    animator_window = None
    main_window = None
    original_root = None
    try:
        report["mayaVersion"] = str(cmds.about(version=True))
        log("=== MMD Control Rig normal UI E2E begin ===")
        # Isolate the contract from a prior user's persisted experimental
        # choice, while restoring both values before this Maya exits.
        settings.set(settings_keys.UI_GENERAL_DEVELOPMENT_MODE, False)
        settings.set(settings_keys.IMPORT_MODEL_CREATE_MMD_CONTROL_RIG, False)
        report["developmentMode"] = bool(settings.is_development_mode())
        _load_plugins(cmds)

        from mmd_tools.io.mmd_importer import import_mmd_file

        cmds.file(new=True, force=True)
        original_root = str(
            import_mmd_file(
                str(model_path),
                options={
                    "setup_rig": True,
                    "setup_bone_orientation": True,
                    "import_physics": False,
                    "create_mmd_control_rig": True,
                },
            )
        )
        if not original_root:
            raise RuntimeError("PMX import returned no root")
        log(f"imported first model: {original_root}")

        from mmd_tools.core.mmd_control_rig_builder import read_mmd_control_rig_metadata

        imported_rig_metadata = read_mmd_control_rig_metadata(original_root)
        control_rig_created_during_import = bool(imported_rig_metadata)
        report["models"]["controlRigCreatedDuringImport"] = control_rig_created_during_import
        if not imported_rig_metadata:
            raise RuntimeError("Create MMD Control Rig did not build a rig during model import")
        report["models"]["controlRigBoundDuringImport"] = {
            "state": imported_rig_metadata.get("state"),
            "owner": imported_rig_metadata.get("owner"),
            "passed": imported_rig_metadata.get("state") == "EDIT"
            and imported_rig_metadata.get("owner") == "CONTROL_OWNED",
        }
        if not report["models"]["controlRigBoundDuringImport"]["passed"]:
            raise RuntimeError(
                "Create MMD Control Rig did not enter EDIT / CONTROL_OWNED during model import"
            )

        if vmd_path:
            vmd_profile: dict[str, Any] = {}
            try:
                vmd_imported = bool(
                    import_mmd_file(
                        str(vmd_path),
                        options={
                            "target_model": original_root,
                            "create_mmd_control_rig": True,
                            "clear_existing_motion": True,
                            "import_camera_animation": False,
                            "import_light_animation": False,
                            "profile": vmd_profile,
                        },
                    )
                )
            except Exception:
                report["models"]["vmdImport"] = {
                    "path": str(vmd_path),
                    "passed": False,
                    "profile": vmd_profile,
                }
                raise
            vmd_warnings = list(vmd_profile.get("vmd_converter", {}).get("warnings") or [])
            report["models"]["vmdImport"] = {
                "path": str(vmd_path),
                "passed": vmd_imported,
                "warnings": vmd_warnings,
            }
            if not vmd_imported:
                raise RuntimeError("Control Rig VMD import returned false")

        from mmd_tools.plugin_main import close_animator_toolset, close_main_window, open_animator_toolset, open_main_window

        animator_window = open_animator_toolset(dockable=False)
        _safe_process_events()
        tab = animator_window.animation_tab
        common_action_buttons = dict(getattr(tab, "common_action_buttons", {}) or {})
        action_rows = {
            key: {
                "text": _safe_text(button),
                "visible": _safe_visible(button),
                "enabled": _safe_enabled(button),
            }
            for key, button in sorted(common_action_buttons.items())
        }
        manager_button = getattr(tab, "control_rig_manager_btn", None)
        legacy_group = getattr(tab, "control_rig_group", None)
        report["ui"]["animatorToolset"] = {
            "windowVisible": _safe_visible(animator_window),
            "footerManagerVisible": _safe_visible(manager_button),
            "footerManagerEnabled": _safe_enabled(manager_button),
            "footerManagerText": _safe_text(manager_button),
            "footerManagerTooltip": str(manager_button.toolTip()) if manager_button is not None else "",
            "commonActionBarVisible": _safe_visible(getattr(tab, "common_action_bar", None)),
            "commonActionBarEnabled": _safe_enabled(getattr(tab, "common_action_bar", None)),
            "actions": action_rows,
            "modelComboCount": int(tab.model_combo.count()),
        }
        menu_item = "MMDControlRigManagerMenuItem"
        menu_exists = bool(cmds.menuItem(menu_item, exists=True))
        menu_label = (
            str(cmds.menuItem(menu_item, query=True, label=True))
            if menu_exists
            else ""
        )
        report["ui"]["controlRigEntryPoints"] = {
            "mayaMenuExists": menu_exists,
            "mayaMenuLabel": menu_label,
            "animatorButtonText": _safe_text(manager_button),
            "passed": menu_exists
            and menu_label == "コントロールリグを管理"
            and _safe_text(manager_button) == "コントロールリグを管理",
        }
        if not report["ui"]["controlRigEntryPoints"]["passed"]:
            raise RuntimeError("Control Rig Manager entry points are missing or mislabeled")
        if legacy_group is not None:
            # Keep the old fields only when a third-party/headless view still
            # provides them; the production footer is the public contract.
            report["ui"]["animatorToolset"].update(
                {
                    "legacyControlRigTitle": _safe_title(legacy_group),
                    "legacyControlRigVisible": _safe_visible(legacy_group),
                    "legacyControlRigEnabled": _safe_enabled(legacy_group),
                }
            )
        report["captures"]["animatorToolset"] = str(capture_root / "animator_toolset.png")
        report["captures"]["animatorToolsetSaved"] = _safe_grab(animator_window, capture_root / "animator_toolset.png")

        # The common actions live outside the Body/Finger pages.  Switching
        # tabs must preserve one widget instance and keep the bar visible.
        body_bar = getattr(tab, "common_action_bar", None)
        body_button_ids = {key: id(button) for key, button in common_action_buttons.items()}
        body_bar_visible = _safe_visible(body_bar)
        tab.picker_tabs.setCurrentIndex(tab.TAB_FINGER)
        _safe_process_events()
        finger_bar_visible = _safe_visible(body_bar)
        finger_button_ids = {
            key: id(button)
            for key, button in dict(getattr(tab, "common_action_buttons", {}) or {}).items()
        }
        tab.picker_tabs.setCurrentIndex(tab.TAB_BODY)
        _safe_process_events()
        report["ui"]["commonActionBar"] = {
            "bodyVisible": body_bar_visible,
            "fingerVisible": finger_bar_visible,
            "singleBarInstance": bool(body_bar is getattr(tab, "common_action_bar", None)),
            "singleButtonInstances": body_button_ids == finger_button_ids,
            "passed": body_bar_visible
            and finger_bar_visible
            and body_button_ids == finger_button_ids,
        }

        # Open through the Maya-menu callback target and then through the
        # Animator footer. Both entry points must reuse the same singleton.
        import mmd_tools.plugin_main as plugin_main_module

        manager_one = plugin_main_module.open_control_rig_manager()
        _safe_process_events()
        manager_two = animator_window.open_control_rig_manager()
        _safe_process_events()
        metadata_before_manager_refresh = dict(read_mmd_control_rig_metadata(original_root) or {})
        manager_two.refresh()
        _safe_process_events()
        metadata_after_manager_refresh = dict(read_mmd_control_rig_metadata(original_root) or {})
        report["ui"]["controlRigManager"] = {
            "visible": _safe_visible(manager_one),
            "singletonIdentity": manager_one is manager_two,
            "selectedUuid": str(manager_two.selected_uuid() or ""),
            "selectedModelRoot": str(manager_two.selected_model_root() or ""),
            "refreshReadOnly": metadata_before_manager_refresh == metadata_after_manager_refresh,
            "internalMetadataLabelsAbsent": not hasattr(manager_two, "uuid_label")
            and not hasattr(manager_two, "state_label"),
            "diagnosticsActionAbsent": "diagnostics"
            not in (getattr(manager_two, "action_buttons", {}) or {}),
            "actionCount": len(getattr(manager_two, "action_buttons", {}) or {}),
        }
        report["ui"]["controlRigManager"]["passed"] = all(
            (
                report["ui"]["controlRigManager"]["visible"],
                report["ui"]["controlRigManager"]["singletonIdentity"],
                report["ui"]["controlRigManager"]["refreshReadOnly"],
                report["ui"]["controlRigManager"]["internalMetadataLabelsAbsent"],
                report["ui"]["controlRigManager"]["diagnosticsActionAbsent"],
                report["ui"]["controlRigManager"]["actionCount"] == 5,
            )
        )

        main_window = open_main_window(dockable=False)
        _safe_process_events()
        import_tab = main_window.import_export_tab
        checkbox = import_tab.create_mmd_control_rig_check
        initial_checked = bool(checkbox.isChecked())
        settings_before_toggle = bool(settings.get(settings_keys.IMPORT_MODEL_CREATE_MMD_CONTROL_RIG, False))
        checkbox.setChecked(True)
        _safe_process_events()
        toggled_on = bool(checkbox.isChecked()) and bool(settings.get(settings_keys.IMPORT_MODEL_CREATE_MMD_CONTROL_RIG, False))
        checkbox.setChecked(False)
        _safe_process_events()
        toggled_off = (not bool(checkbox.isChecked())) and not bool(settings.get(settings_keys.IMPORT_MODEL_CREATE_MMD_CONTROL_RIG, False))
        report["ui"]["modelImportOption"] = {
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
        from mmd_tools.core.mmd_control_rig_motion import enter_mmd_control_rig_edit, restore_mmd_control_rig_attached

        # The presenter is separate from the main window; make its ApplicationState
        # point at the imported model and refresh the map before clicking a region.
        animator_window.app_state.current_model_root = original_root
        animator_window.animation_presenter.refresh_for_scene_change()
        current_metadata = read_mmd_control_rig_metadata(original_root) or {}
        if current_metadata.get("state") != "EDIT":
            enter_mmd_control_rig_edit(original_root)
        animator_window.animation_presenter.refresh_for_scene_change()
        # Imported fixtures may keep the Controls display group hidden (or in
        # reference mode).  Temporarily make the owned controls selectable so
        # this picker gate tests ownership routing rather than viewport draw
        # preference.  Restore the exact display state after the click.
        control_group_for_picker = animator_window.animation_presenter._control_rig_group(
            original_root
        )
        control_group_visibility_snapshot = None
        control_group_display_state = {}
        control_inspection_error = ""
        try:
            from mmd_tools.core.mmd_control_rig_builder import inspect_mmd_control_rig

            inspected = inspect_mmd_control_rig(original_root, cmds_module=cmds)
            control_group_display_state["inspectionGroup"] = str(
                getattr(inspected, "control_group", "") or ""
            )
        except Exception as exc:
            control_inspection_error = str(exc)
        if control_group_for_picker:
            try:
                control_group_visibility_snapshot = (
                    bool(cmds.getAttr(f"{control_group_for_picker}.visibility")),
                    bool(cmds.getAttr(f"{control_group_for_picker}.overrideEnabled")),
                    int(cmds.getAttr(f"{control_group_for_picker}.overrideDisplayType")),
                )
                control_group_display_state = {
                    "group": control_group_for_picker,
                    "snapshot": control_group_visibility_snapshot,
                    "visibilitySources": [
                        str(source)
                        for source in (
                            cmds.listConnections(
                                f"{control_group_for_picker}.visibility",
                                source=True,
                                destination=False,
                                plugs=True,
                            )
                            or []
                        )
                    ],
                }
                cmds.setAttr(f"{control_group_for_picker}.visibility", True)
                cmds.setAttr(f"{control_group_for_picker}.overrideEnabled", True)
                cmds.setAttr(f"{control_group_for_picker}.overrideDisplayType", 0)
            except Exception:
                control_group_display_state["prepareError"] = traceback.format_exc()
                control_group_visibility_snapshot = None
        animator_window.animation_presenter.on_body_region_clicked("center")
        _safe_process_events()
        control_selection = [str(item) for item in (cmds.ls(selection=True, long=True) or [])]
        control_click_status = _safe_text(getattr(animator_window.animation_tab, "status_label", None))
        metadata = read_mmd_control_rig_metadata(original_root)
        control_uuid = (metadata or {}).get("controls", {}).get("center")
        expected_control = (cmds.ls(control_uuid, long=True) or [None])[0] if control_uuid else None
        control_selected = bool(expected_control and expected_control in control_selection)
        # Resolve the picker target while CONTROL_OWNED is still active.  A
        # fixture may alias the semantic center role to a waist basis control.
        control_target_joint = None
        for joint in cmds.ls(type="joint", long=True) or []:
            if not cmds.attributeQuery("mmd_bone_name", node=joint, exists=True):
                continue
            name = str(cmds.getAttr(f"{joint}.mmd_bone_name"))
            if name == "センター" or name.lower() == "center":
                control_target_joint = str(joint)
                break
        if control_target_joint:
            resolved_control = animator_window.animation_presenter._preferred_rig_control(
                control_target_joint
            )
            if resolved_control:
                expected_control = str(resolved_control)
                control_selected = expected_control in control_selection
        preferred_control_short = animator_window.animation_presenter._preferred_rig_control(
            "center"
        )
        resolved_controls = {
            str(role): str(path)
            for role, uuid in ((metadata or {}).get("controls", {}) or {}).items()
            for path in (cmds.ls(uuid, long=True) or [])
        }
        selected_control_roles = [
            role
            for role, path in resolved_controls.items()
            if path in control_selection or path.rsplit("|", 1)[-1] in control_selection
        ]
        if selected_control_roles:
            # Some Maya versions return a short DAG spelling for a selected
            # control even when ``long=True`` was requested.  It is still a
            # validated UUID-backed Control Rig selection.
            control_selected = True
        restore_mmd_control_rig_attached(original_root)
        if control_group_for_picker and control_group_visibility_snapshot is not None:
            try:
                visibility, override_enabled, display_type = control_group_visibility_snapshot
                cmds.setAttr(f"{control_group_for_picker}.visibility", visibility)
                cmds.setAttr(f"{control_group_for_picker}.overrideEnabled", override_enabled)
                cmds.setAttr(f"{control_group_for_picker}.overrideDisplayType", display_type)
            except Exception:
                pass
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

        # The Maya selection callback must drive the picker without a click.
        selection_sync_regions: list[str] = []
        if expected_joint:
            cmds.select(expected_joint, replace=True)
            _safe_process_events()
            selection_sync_regions = sorted(
                str(region)
                for region in (getattr(tab.body_picker, "_selected_regions", set()) or set())
            )
        selection_sync_passed = "center" in selection_sync_regions

        # Rest Pose is a reversible common action.  A real imported model can
        # legitimately fail closed when an unsupported writer is present; in
        # that case the test records the diagnostic instead of hiding it.
        reset_button = common_action_buttons.get("reset")
        rest_channel_diagnostics = []
        for joint in cmds.ls(type="joint", long=True) or []:
            if "twist_1" not in str(joint):
                continue
            plug = f"{joint}.rotateX"
            incoming = [
                str(source)
                for source in (cmds.listConnections(plug, source=True, destination=False, plugs=True) or [])
            ]
            rest_channel_diagnostics.append(
                {
                    "plug": plug,
                    "locked": bool(cmds.getAttr(plug, lock=True)),
                    "settable": bool(cmds.getAttr(plug, settable=True)),
                    "incoming": incoming,
                    "incomingTypes": [
                        str(cmds.nodeType(str(source).rsplit(".", 1)[0])) for source in incoming
                    ],
                }
            )
        rest_error = ""
        rest_applied = False
        rest_restored = False
        if reset_button is not None:
            try:
                reset_button.click()
                _safe_process_events()
                rest_applied = getattr(animator_window.animation_presenter, "_rest_pose_transaction", None) is not None
                if rest_applied:
                    reset_button.click()
                    _safe_process_events()
                    rest_restored = getattr(animator_window.animation_presenter, "_rest_pose_transaction", None) is None
                else:
                    rest_error = _safe_text(getattr(tab, "status_label", None))
            except Exception as exc:
                rest_error = str(exc)
        report["ui"]["restPoseToggle"] = {
            "buttonPresent": reset_button is not None,
            "applied": rest_applied,
            "restored": rest_restored,
            "status": _safe_text(getattr(tab, "status_label", None)),
            "error": rest_error,
            "channelDiagnostics": rest_channel_diagnostics,
            "passed": bool(reset_button is not None and rest_applied and rest_restored),
        }

        # Center is intentionally unpaired.  Mirror Select/Pose must reject it
        # without changing the selection or writing a pose value.
        mirror_selection_button = common_action_buttons.get("mirror_selection")
        mirror_pose_button = common_action_buttons.get("mirror")
        before_mirror_selection = [str(item) for item in (cmds.ls(selection=True, long=True) or [])]
        before_mirror_values = None
        if expected_joint:
            before_mirror_values = [
                float(cmds.getAttr(f"{expected_joint}.{plug}"))
                for plug in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ")
            ]
        mirror_selection_error = ""
        mirror_pose_error = ""
        try:
            if mirror_selection_button is not None:
                mirror_selection_button.click()
                _safe_process_events()
        except Exception as exc:
            mirror_selection_error = str(exc)
        after_mirror_selection = [str(item) for item in (cmds.ls(selection=True, long=True) or [])]
        try:
            if mirror_pose_button is not None:
                mirror_pose_button.click()
                _safe_process_events()
        except Exception as exc:
            mirror_pose_error = str(exc)
        after_mirror_values = None
        if expected_joint:
            after_mirror_values = [
                float(cmds.getAttr(f"{expected_joint}.{plug}"))
                for plug in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ")
            ]
        report["ui"]["mirrorFailClosed"] = {
            "mirrorSelectionButtonPresent": mirror_selection_button is not None,
            "mirrorPoseButtonPresent": mirror_pose_button is not None,
            "selectionUnchanged": before_mirror_selection == after_mirror_selection,
            "poseUnchanged": before_mirror_values == after_mirror_values,
            "selectionError": mirror_selection_error,
            "poseError": mirror_pose_error,
            "status": _safe_text(getattr(tab, "status_label", None)),
            "passed": bool(
                mirror_selection_button is not None
                and mirror_pose_button is not None
                and before_mirror_selection == after_mirror_selection
                and before_mirror_values == after_mirror_values
            ),
        }

        presenter = animator_window.animation_presenter
        try:
            presenter._sync_ik_picker_state(force=True)
            hidden_regions = sorted(
                str(region)
                for region in (getattr(tab.body_picker, "_hidden_regions", set()) or set())
            )
            ik_states = dict(getattr(presenter, "_last_ik_states", {}) or {})
            ik_expected = {
                "left_lower_leg": "foot:left" in ik_states and bool(ik_states.get("foot:left")),
                "right_lower_leg": "foot:right" in ik_states and bool(ik_states.get("foot:right")),
                "left_foot": "foot:left" in ik_states and bool(ik_states.get("foot:left")),
                "right_foot": "foot:right" in ik_states and bool(ik_states.get("foot:right")),
            }
            ik_knee_visibility_passed = all(
                (region not in hidden_regions) == (not should_hide)
                for region, should_hide in ik_expected.items()
                if f"foot:{'left' if region.startswith('left') else 'right'}" in ik_states
            )
            report["picker"]["ikKneeVisibility"] = {
                "hiddenRegions": hidden_regions,
                "ikStates": ik_states,
                "expectedHidden": ik_expected,
                "passed": bool(ik_states) and ik_knee_visibility_passed,
            }
        except Exception as exc:
            report["picker"]["ikKneeVisibility"] = {
                "hiddenRegions": [],
                "ikStates": {},
                "passed": False,
                "error": str(exc),
            }
        report["picker"] = {
            "controlOwnerSelection": control_selection,
            "controlClickStatus": control_click_status,
            "expectedControl": expected_control,
            "resolvedControls": resolved_controls,
            "selectedControlRoles": selected_control_roles,
            "controlMetadataRoles": sorted((metadata or {}).get("controls", {}).keys()),
            "controlGroupDisplayState": control_group_display_state,
            "controlInspectionError": control_inspection_error,
            "controlBindingRoles": sorted((metadata or {}).get("bindings", {}).keys()),
            "presenterCenterJoint": str(
                getattr(animator_window.animation_presenter, "_bone_name_to_joint", {}).get("センター")
                or getattr(animator_window.animation_presenter, "_bone_name_to_joint", {}).get("center")
                or ""
            ),
            "preferredControlShort": str(preferred_control_short or ""),
            "controlSelected": control_selected,
            "mmdOwnerSelection": joint_selection,
            "expectedJoint": expected_joint,
            "jointSelected": joint_selected,
            "ownerSwitchPassed": control_selected and joint_selected,
            "selectionSyncRegions": selection_sync_regions,
            "selectionSyncPassed": selection_sync_passed,
            "ikKneeVisibility": report["picker"].get("ikKneeVisibility", {}),
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
            "controlRigCreatedDuringImport": control_rig_created_during_import,
            "controlRigBoundDuringImport": report["models"].get("controlRigBoundDuringImport"),
            "vmdImport": report["models"].get("vmdImport"),
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
        settings.set(settings_keys.IMPORT_MODEL_CREATE_MMD_CONTROL_RIG, original_create)
        _write_report(report_file, report)
        log("RESULT_JSON: " + json.dumps(report, ensure_ascii=False, sort_keys=True))
        log(COMPLETION_MARKER)


def main() -> int:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    parser = argparse.ArgumentParser(description="MMD Control Rig normal UI Maya GUI E2E")
    parser.add_argument("--maya", default="2026")
    parser.add_argument("--model", default=str(_PROJECT_ROOT / "tests" / "data" / "mmt_test_model.pmx"))
    parser.add_argument("--vmd", default="", help="Optional real VMD imported through the created Control Rig")
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
    command = (
        "import sys\n"
        "from pathlib import Path\n"
        f"project_root = Path(r'{_PROJECT_ROOT.as_posix()}')\n"
        "sys.path.insert(0, str(project_root)) if str(project_root) not in sys.path else None\n"
        "from tests.viewport.e2e_mmd_control_rig_ui import run_ui_check\n"
        f"run_ui_check(r'{log_path.as_posix()}', r'{Path(args.model).resolve().as_posix()}', r'{report_path.as_posix()}', r'{scene_path.as_posix()}', r'{capture_dir.as_posix()}', r'{Path(args.vmd).resolve().as_posix() if args.vmd else ''}')\n"
    )
    report = run_maya_e2e(
        project_root=_PROJECT_ROOT,
        version=args.maya,
        out_dir=out_dir,
        port=args.port,
        timeout=args.timeout,
        log_path=log_path,
        report_path=report_path,
        command=command,
        marker=COMPLETION_MARKER,
        send_label="<mmd-control-rig-ui-e2e>",
        stale_paths=[report_path, log_path, scene_path],
        terminate_process=True,
        quit_delay=3.0,
        port_error=f"commandPort :{args.port} is already open",
    )
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
