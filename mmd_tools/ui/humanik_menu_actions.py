"""Maya menu actions for the staged HumanIK frontend workflow.

The module keeps Maya menu callbacks thin and delegates lifecycle decisions to
``HumanIkFrontendSession``.  Dependencies are injectable so menu hierarchy and
action behavior can be tested without opening a Maya HumanIK panel.
"""

from __future__ import annotations

import json
import math
from typing import Callable, Optional

from mmd_tools.core.humanik_frontend import (
    FRONTEND_ASSIGNMENT_PROFILE,
    REFERENCE_QUALITY_DIAGNOSTICS,
    HumanIkFrontendSession,
)
from mmd_tools.services.scene_model_service import SceneModelService


HUMANIK_MENU_NAME = "MMDHumanIKMenu"
DIAGNOSTICS_WINDOW_NAME = "MMDHumanIKDiagnosticsWindow"
ACTION_LABELS = (
    ("setup_and_characterize", "Setup / Characterize"),
    ("enter_source_mode", "Enter Source Mode"),
    ("enter_target_mode", "Enter Target Mode"),
    ("create_control_rig", "Create Control Rig"),
    ("bake_to_mmd_rig", "Bake to MMD Rig"),
    ("restore_mmd_rig", "Restore MMD Rig"),
    ("diagnostics", "Diagnostics"),
)
_ACTION_MENU_IDS = {
    action: f"MMDHumanIK{index}MenuItem"
    for index, (action, _label) in enumerate(ACTION_LABELS, start=1)
}

_session: Optional[HumanIkFrontendSession] = None
_cmds_module = None
_mel_module = None
_confirm_dialog: Optional[Callable[..., str]] = None
_error_reporter: Optional[Callable[[str], None]] = None


def _maya_cmds():
    if _cmds_module is not None:
        return _cmds_module
    from maya import cmds

    return cmds


def configure_humanik_actions(
    *,
    session: Optional[HumanIkFrontendSession] = None,
    cmds_module=None,
    mel_module=None,
    confirm_dialog: Optional[Callable[..., str]] = None,
    error_reporter: Optional[Callable[[str], None]] = None,
) -> Optional[HumanIkFrontendSession]:
    """Inject menu dependencies and optionally replace the shared session."""
    global _session, _cmds_module, _mel_module
    global _confirm_dialog, _error_reporter
    if session is not None:
        _session = session
    if cmds_module is not None:
        _cmds_module = cmds_module
    if mel_module is not None:
        _mel_module = mel_module
    if confirm_dialog is not None:
        _confirm_dialog = confirm_dialog
    if error_reporter is not None:
        _error_reporter = error_reporter
    return _session


def get_humanik_session() -> HumanIkFrontendSession:
    """Return the process-owned frontend session, creating it lazily."""
    global _session
    if _session is None:
        _session = HumanIkFrontendSession(cmds_module=_cmds_module, mel_module=_mel_module)
    return _session


def set_humanik_session(session: HumanIkFrontendSession) -> HumanIkFrontendSession:
    """Replace the shared session for tests or an explicitly managed host."""
    global _session
    _session = session
    return session


def reset_humanik_session(*, restore: bool = True) -> bool:
    """Restore owned scene state before dropping the shared session.

    When restore fails the session is retained so an unload/reload path cannot
    silently discard the journal or pending character recovery state.
    """
    global _session
    if _session is None:
        _close_diagnostics_window()
        return True
    if restore:
        try:
            _session.restore_mmd_rig()
        except Exception as exc:
            _display_warning(f"HumanIK restore during unload failed: {exc}")
            return False
    _session = None
    _close_diagnostics_window()
    return True


def _close_diagnostics_window():
    """Close the owned diagnostics window when it is still registered in Maya."""
    try:
        cmds = _cmds_module or _maya_cmds()
        if _ui_exists(cmds, DIAGNOSTICS_WINDOW_NAME):
            cmds.deleteUI(DIAGNOSTICS_WINDOW_NAME)
    except Exception as exc:
        _display_warning(f"HumanIK diagnostics window close failed: {exc}")


def resolve_model_root(
    *,
    cmds_module=None,
) -> str:
    """Resolve an MMD root from the current Maya selection only.

    Multiple selected roots or a selection that cannot be mapped to an MMD root
    are rejected rather than choosing an arbitrary scene or application-state
    fallback.
    """
    cmds = cmds_module or _maya_cmds()
    service = SceneModelService(cmds_module=cmds)
    try:
        available_models = set(service.list_mmd_models())
    except Exception:
        available_models = set()
    selected_roots = set()
    for node in cmds.ls(selection=True, long=True) or []:
        node_name = str(node)
        root = service.get_parent_mmd_root(node_name)
        if not root and node_name in available_models:
            root = node_name
        if root:
            selected_roots.add(str(root))
    if len(selected_roots) > 1:
        raise ValueError(
            "Multiple MMD model roots are selected; select exactly one model root"
        )
    if selected_roots:
        return next(iter(selected_roots))
    raise ValueError("Select an MMD model root or one of its joints before using HumanIK")


def install_humanik_menu(parent="MMD", *, cmds_module=None, callback_dispatcher=None):
    """Install the HumanIK submenu and exactly its seven staged actions."""
    cmds = cmds_module or _maya_cmds()
    if _ui_exists(cmds, HUMANIK_MENU_NAME):
        cmds.deleteUI(HUMANIK_MENU_NAME)
    submenu = cmds.menuItem(
        HUMANIK_MENU_NAME,
        label="HumanIK",
        parent=parent,
        subMenu=True,
    )
    dispatcher = callback_dispatcher or dispatch_action
    for action, label in ACTION_LABELS:
        menu_id = _ACTION_MENU_IDS[action]
        cmds.menuItem(
            menu_id,
            label=label,
            parent=submenu,
            command=lambda *_args, _action=action: dispatcher(_action),
        )
    return submenu


def dispatch_action(action: str):
    """Dispatch a menu action by its stable internal action name."""
    function = globals().get(str(action)) or _ACTION_FUNCTIONS.get(str(action))
    if function is None:
        raise ValueError(f"Unknown HumanIK menu action: {action}")
    return function()


def setup_and_characterize():
    """Show read-only setup preflight and characterize only after confirmation."""
    return _run_action("Setup / Characterize", _setup_and_characterize)


def enter_source_mode():
    """Select a previously characterized model as the HumanIK source."""
    return _run_action("Enter Source Mode", _enter_source_mode)


def enter_target_mode():
    """Show ownership facts and start target preview only when unblocked."""
    return _run_action("Enter Target Mode", _enter_target_mode)


def create_control_rig():
    """Create a control rig on the selected characterized model after confirmation."""
    return _run_action("Create Control Rig", _create_control_rig)


def bake_to_mmd_rig():
    """Bake the active target preview over Maya's integer playback range."""
    return _run_action("Bake to MMD Rig", _bake_to_mmd_rig)


def restore_mmd_rig():
    """Restore active preview state or pending setup characters without a model selection."""
    return _run_action("Restore MMD Rig", lambda: get_humanik_session().restore_mmd_rig())


def diagnostics():
    """Show a JSON-safe session diagnostics report in a scrollable Maya window."""
    return _run_action("Diagnostics", _show_diagnostics)


def _setup_and_characterize():
    root = resolve_model_root(cmds_module=_cmds_module)
    session = get_humanik_session()
    ownership_report = session.inspect_target_ownership(root)
    message = _setup_confirmation_message(root, ownership_report, ownership_report)
    if not _confirm("Setup / Characterize", message):
        return None
    return session.setup_and_characterize(root, stance_confirmed=True)


def _enter_source_mode():
    root = resolve_model_root(cmds_module=_cmds_module)
    return get_humanik_session().enter_source_mode(root)


def _enter_target_mode():
    root = resolve_model_root(cmds_module=_cmds_module)
    session = get_humanik_session()
    report = session.inspect_target_ownership(root)
    blockers = report.get("blockers", [])
    if blockers:
        labels = ", ".join(
            f"{row.get('node', '')}:{row.get('classification', '')}" for row in blockers
        )
        raise RuntimeError(f"HumanIK target preview blocked: {labels}")
    if not _confirm("Enter Target Mode", _target_confirmation_message(root, report)):
        return None
    return session.enter_target_mode(root)


def _create_control_rig():
    root = resolve_model_root(cmds_module=_cmds_module)
    if not _confirm(
        "Create Control Rig",
        f"Create a HumanIK Control Rig for {root}? An active preview must be restored first.",
    ):
        return None
    return get_humanik_session().create_control_rig(root)


def _bake_to_mmd_rig():
    cmds = _cmds_module or _maya_cmds()
    start = math.ceil(float(cmds.playbackOptions(query=True, minTime=True)))
    end = math.floor(float(cmds.playbackOptions(query=True, maxTime=True)))
    if end < start:
        raise ValueError(f"Playback range is empty after integer conversion: {start}..{end}")
    message = (
        f"Bake the active HumanIK target preview from frame {start} to {end}?\n"
        f"Profile: {FRONTEND_ASSIGNMENT_PROFILE}; finger assignments are excluded/deferred.\n"
        f"Quality: {REFERENCE_QUALITY_DIAGNOSTICS['status']} "
        f"(reference residual {REFERENCE_QUALITY_DIAGNOSTICS['referenceS5bBodyMatrixResidual']})."
    )
    if not _confirm("Bake to MMD Rig", message):
        return None
    result = get_humanik_session().bake_to_mmd_rig(start, end)
    result_dict = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    _display_info("HumanIK bake complete: " + json.dumps(result_dict, sort_keys=True))
    return result


def _show_diagnostics():
    session = get_humanik_session()
    try:
        root = resolve_model_root(cmds_module=_cmds_module)
    except Exception:
        root = None
    report = session.diagnostics(root)
    cmds = _cmds_module or _maya_cmds()
    if _ui_exists(cmds, DIAGNOSTICS_WINDOW_NAME):
        cmds.deleteUI(DIAGNOSTICS_WINDOW_NAME)
    cmds.window(DIAGNOSTICS_WINDOW_NAME, title="HumanIK Diagnostics", sizeable=True)
    cmds.columnLayout(adjustableColumn=True)
    cmds.scrollField(
        editable=False,
        wordWrap=True,
        text=json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True),
        height=560,
    )
    cmds.button(
        label="Close",
        command=lambda *_args: cmds.deleteUI(DIAGNOSTICS_WINDOW_NAME),
    )
    cmds.showWindow(DIAGNOSTICS_WINDOW_NAME)
    return report


def _setup_confirmation_message(root, model_report, ownership_report):
    counts = ownership_report.get("constraintCounts", {})
    blockers = ownership_report.get("blockers", [])
    rows = ownership_report.get("constraintRows", [])
    mute_rows = _ownership_rows(rows, "mute_for_hik")
    keep_rows = _ownership_rows(rows, "keep_post")
    blocker_labels = ", ".join(
        f"{row.get('node', '')}:{row.get('classification', '')}" for row in blockers
    ) or "none"
    return (
        f"Model root: {root}\n"
        "Apply the same user-confirmed common T-pose to both Source and Target before continuing.\n"
        f"Characterize body + roll assignments: {model_report.get('assignmentCount', 0)}; "
        f"finger assignments excluded/deferred: {model_report.get('excludedFingerCount', 0)}.\n"
        f"Unresolved: {len(model_report.get('missingMmdBones', []))}; "
        f"ambiguous: {len(model_report.get('ambiguous', []))}.\n"
        f"mute_for_hik: {counts.get('mute_for_hik', 0)}; "
        f"keep_post: {counts.get('keep_post', 0)}; blockers: {len(blockers)}.\n"
        f"mute_for_hik nodes/edges: {mute_rows or 'none'}; keep_post: {keep_rows or 'none'}; "
        f"blocker details: {blocker_labels}.\n"
        "Target preview captures an ownership journal and Restore MMD Rig can recover it.\n"
        f"Known S5b reference residual: {REFERENCE_QUALITY_DIAGNOSTICS['referenceS5bBodyMatrixResidual']} "
        f"({REFERENCE_QUALITY_DIAGNOSTICS['status']})."
    )


def _target_confirmation_message(root, report):
    counts = report.get("constraintCounts", {})
    rows = report.get("constraintRows", [])
    return (
        f"Target model root: {root}\n"
        "The target preview will journal changes, disconnect mute_for_hik writer edges, "
        "mute only those writers, "
        "retain keep_post writers, and restore all ownership on Restore MMD Rig.\n"
        f"mute_for_hik: {counts.get('mute_for_hik', 0)}; "
        f"keep_post: {counts.get('keep_post', 0)}; blockers: {len(report.get('blockers', []))}.\n"
        f"mute_for_hik nodes/edges: {_ownership_rows(rows, 'mute_for_hik') or 'none'}; "
        f"keep_post nodes/edges: {_ownership_rows(rows, 'keep_post') or 'none'}.\n"
        "Continue?"
    )


def _ownership_rows(rows, classification):
    """Format ownership rows as compact node-to-edge text for confirmations."""
    values = []
    for row in rows:
        if row.get("classification") != classification:
            continue
        node = str(row.get("node", ""))
        edges = list(row.get("writes", [])) or list(row.get("reads", []))
        values.append(f"{node} -> {', '.join(str(edge) for edge in edges) or 'n/a'}")
    return "; ".join(values)


def _confirm(title, message):
    if _confirm_dialog is not None:
        return _confirm_dialog(title=title, message=message) == "Continue"
    cmds = _cmds_module or _maya_cmds()
    result = cmds.confirmDialog(
        title=title,
        message=message,
        button=["Continue", "Cancel"],
        defaultButton="Continue",
        cancelButton="Cancel",
        dismissString="Cancel",
    )
    return result == "Continue"


def _run_action(label, operation):
    try:
        return operation()
    except Exception as exc:
        _display_error(f"HumanIK {label} failed: {exc}")
        return None


def _display_error(message):
    if _error_reporter is not None:
        _error_reporter(message)
        return
    try:
        import maya.api.OpenMaya as om

        om.MGlobal.displayError(message)
    except Exception:
        pass
    try:
        cmds = _cmds_module or _maya_cmds()
        cmds.confirmDialog(title="HumanIK Error", message=message, button=["OK"], icon="critical")
    except Exception:
        pass


def _display_info(message):
    try:
        import maya.api.OpenMaya as om

        om.MGlobal.displayInfo(message)
    except Exception:
        pass


def _display_warning(message):
    try:
        import maya.api.OpenMaya as om

        om.MGlobal.displayWarning(message)
    except Exception:
        pass


def _ui_exists(cmds, name):
    menu_item_exists = False
    try:
        menu_item_exists = bool(cmds.menuItem(name, exists=True))
    except Exception:
        pass
    if menu_item_exists:
        return True
    try:
        if bool(cmds.menu(name, exists=True)):
            return True
    except Exception:
        pass
    try:
        return bool(cmds.window(name, exists=True))
    except Exception:
        return False


_ACTION_FUNCTIONS = {
    "setup_and_characterize": setup_and_characterize,
    "enter_source_mode": enter_source_mode,
    "enter_target_mode": enter_target_mode,
    "create_control_rig": create_control_rig,
    "bake_to_mmd_rig": bake_to_mmd_rig,
    "restore_mmd_rig": restore_mmd_rig,
    "diagnostics": diagnostics,
}
