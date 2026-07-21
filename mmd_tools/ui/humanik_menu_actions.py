"""Maya menu actions for the staged HumanIK frontend workflow.

The module keeps Maya menu callbacks thin and delegates lifecycle decisions to
``HumanIkFrontendSession``.  Dependencies are injectable so menu hierarchy and
action behavior can be tested without opening a Maya HumanIK panel.
"""

from __future__ import annotations

import json
import math
from typing import Any, Callable, Mapping, Optional

from mmd_tools.core.humanik_frontend import (
    FRONTEND_ASSIGNMENT_PROFILE,
    FULL_ASSIGNMENT_PROFILE,
    REASON_NOT_CHARACTERIZED,
    HumanIkFrontendSession,
)
from mmd_tools.core.logger import get_logger, install_maya_script_editor_handler
from mmd_tools.services.scene_model_service import SceneModelService


HUMANIK_MENU_NAME = "MMDHumanIKMenu"
DIAGNOSTICS_WINDOW_NAME = "MMDHumanIKDiagnosticsWindow"

# HUMANIK-EXTERNAL-SOURCE-1 ES-3: the Source combo's item data distinguishes
# an MMD model root from a scene HIK character that is not MMD-driven (e.g. a
# mocap performer characterized outside mmd_tools) via a ``(kind, value)``
# pair. ``connect_retarget`` accepts a bare string too (legacy/MMD-only
# callers) for backward compatibility -- see ``_normalize_source_selector``.
SOURCE_KIND_MMD = "mmd"
SOURCE_KIND_EXTERNAL = "external"
ACTION_LABELS = (
    ("open_humanik_editor", "HumanIK Editor..."),
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
logger = get_logger(__name__)


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


def _resolve_selected_mmd_roots(cmds, service) -> set:
    """Return the set of distinct MMD model roots implied by the current selection.

    Shared by ``resolve_model_root`` (which acts on the result) and
    ``resolve_selected_model_root_for_display`` (which only reads it), so the
    selection-to-root mapping rules live in exactly one place.
    """
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
    return selected_roots


def resolve_model_root(
    *,
    cmds_module=None,
) -> Optional[str]:
    """Resolve an MMD root the HumanIK menu should act on.

    Priority order:

    1. An explicit Maya selection wins. A selection that maps to more than one
       distinct MMD root is rejected (ambiguous) rather than guessing.
    2. With no selection and exactly one MMD model in the scene, that model is
       adopted automatically -- no dialog, no error.
    3. With no selection and two or more MMD models in the scene, a picker
       dialog (``_dialog_choice``) is shown. Cancelling returns ``None``
       (a quiet abort, not an error) after notifying the user via
       ``_display_info``.
    4. With no selection and no MMD models in the scene, a ``ValueError`` is
       raised with a message explaining what to select.
    """
    cmds = cmds_module or _maya_cmds()
    service = SceneModelService(cmds_module=cmds)
    try:
        available_models = set(service.list_mmd_models())
    except Exception:
        available_models = set()
    selected_roots = _resolve_selected_mmd_roots(cmds, service)
    if len(selected_roots) > 1:
        raise ValueError(
            "Multiple MMD model roots are selected; select exactly one model root"
        )
    if selected_roots:
        return next(iter(selected_roots))
    if not available_models:
        raise ValueError("Select an MMD model root or one of its joints before using HumanIK")
    if len(available_models) == 1:
        return next(iter(available_models))
    return _choose_model_from_scene(available_models)


def list_scene_mmd_models(*, cmds_module=None) -> list:
    """Return every current scene MMD model root, sorted for stable combo ordering.

    Used by the HumanIK tab presenter to populate the Character/Source
    combos (HUMANIK-FRONTEND-1 Phase B4). Never raises -- a Maya query
    failure (no scene, plugin not loaded in a non-Maya test process) is
    reported as an empty list, matching ``resolve_selected_model_root_for_display``'s
    fail-soft policy.
    """
    try:
        cmds = cmds_module or _maya_cmds()
        service = SceneModelService(cmds_module=cmds)
        return sorted(str(root) for root in service.list_mmd_models())
    except Exception:
        return []


def resolve_selected_model_root_for_display(*, cmds_module=None) -> Optional[str]:
    """Resolve an MMD root from the current Maya selection, for read-only display only.

    Used by the HumanIK tab presenter to decide which model's state to show.
    Unlike ``resolve_model_root`` this never auto-adopts the lone scene model,
    never shows a picker dialog, and never raises -- a UI status refresh
    should not surprise the user with a dialog or an error just for looking.
    An empty or ambiguous (multiple distinct roots) selection both resolve to
    ``None``, which callers should treat as "no model implied by selection".
    """
    cmds = cmds_module or _maya_cmds()
    service = SceneModelService(cmds_module=cmds)
    selected_roots = _resolve_selected_mmd_roots(cmds, service)
    if len(selected_roots) == 1:
        return next(iter(selected_roots))
    return None


def _choose_model_from_scene(available_models) -> Optional[str]:
    """Show a picker dialog for two or more scene MMD models; ``None`` on cancel."""
    sorted_roots = sorted(available_models)
    labels = []
    seen = set()
    for root in sorted_roots:
        label = _short_model_label(root)
        if label in seen:
            label = str(root).strip("|")
        seen.add(label)
        labels.append(label)
    choice = _dialog_choice(
        "Select MMD Model",
        "Multiple MMD models were found in the scene. Select one to use with HumanIK.",
        tuple(labels) + ("Cancel",),
    )
    if choice in labels:
        return sorted_roots[labels.index(choice)]
    _display_info("HumanIK: model selection cancelled.")
    return None


def install_humanik_menu(parent="MMD", *, cmds_module=None, callback_dispatcher=None):
    """Install the HumanIK submenu with the "Open HumanIK Editor" entry plus
    its seven staged workflow actions."""
    global _cmds_module
    cmds = cmds_module or _maya_cmds()
    _cmds_module = cmds
    install_maya_script_editor_handler()
    if _ui_exists(cmds, HUMANIK_MENU_NAME):
        cmds.deleteUI(HUMANIK_MENU_NAME)
    submenu = cmds.menuItem(
        HUMANIK_MENU_NAME,
        label="HumanIK (Experimental)",
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


def open_humanik_editor():
    """Open (or focus) the standalone HumanIK Editor window.

    The window is imported lazily here -- and only here -- because
    ``mmd_tools.ui.humanik_window`` imports ``HumanIkPresenter``, which in
    turn imports this module at module scope (to dispatch the other six
    staged actions). Importing the window at this module's top level would
    therefore be a circular import; deferring it to call time breaks the
    cycle since by the time this function runs, this module has already
    finished loading.
    """
    return _run_action("Open HumanIK Editor", _open_humanik_editor)


def _open_humanik_editor():
    from .humanik_window import show_humanik_window

    return show_humanik_window(dockable=True)


def setup_and_characterize(model_root: Optional[str] = None):
    """Show read-only setup preflight and characterize only after confirmation.

    Args:
        model_root: Explicit model to act on (skips Maya-selection resolution
            entirely). ``None`` falls back to ``resolve_model_root`` as
            before. Confirmation dialogs, auto-characterize, and error
            reporting are unchanged either way.
    """
    return _run_action("Setup / Characterize", lambda: _setup_and_characterize(model_root))


def enter_source_mode(model_root: Optional[str] = None):
    """Select a previously characterized model as the HumanIK source.

    Args:
        model_root: Explicit model to act on (skips Maya-selection
            resolution). ``None`` falls back to ``resolve_model_root``.
    """
    return _run_action("Enter Source Mode", lambda: _enter_source_mode(model_root))


def enter_target_mode(model_root: Optional[str] = None):
    """Show ownership facts and start target preview only when unblocked.

    Args:
        model_root: Explicit model to act on (skips Maya-selection
            resolution). ``None`` falls back to ``resolve_model_root``.
    """
    return _run_action("Enter Target Mode", lambda: _enter_target_mode(model_root))


def create_control_rig(model_root: Optional[str] = None):
    """Create a control rig on the selected characterized model after confirmation.

    Args:
        model_root: Explicit model to act on (skips Maya-selection
            resolution). ``None`` falls back to ``resolve_model_root``.
    """
    return _run_action("Create Control Rig", lambda: _create_control_rig(model_root))


def enter_external_source_mode(character: Optional[str] = None):
    """Select a scene HIK character (not MMD-driven) already locked as SOURCE.

    HUMANIK-EXTERNAL-SOURCE-1 ES-3: unlike ``enter_source_mode`` this never
    auto-characterizes anything -- an external character (mocap performer,
    plain HumanIK UI use, ...) is never mutated by mmd_tools.
    """
    return _run_action("Enter External Source Mode", lambda: _enter_external_source_mode(character))


def _enter_external_source_mode(character: Optional[str] = None):
    if not character:
        raise ValueError("HumanIK external source requires a character name")
    return get_humanik_session().enter_external_source_mode(character)


def connect_retarget(source, target_model_root: str):
    """Bind ``source`` as SOURCE and start a TARGET preview onto ``target_model_root``.

    The composite action the HumanIK tab's Source combo triggers when the
    user picks an item there (HUMANIK-FRONTEND-1 Phase B4; HUMANIK-EXTERNAL-
    SOURCE-1 ES-3 for the external branch).

    Args:
        source: Either a bare MMD model-root string (legacy/MMD-only
            callers), or a ``(kind, value)`` pair where ``kind`` is
            ``SOURCE_KIND_MMD``/``SOURCE_KIND_EXTERNAL`` and ``value`` is the
            model root or the scene HIK character name respectively -- see
            ``_normalize_source_selector``.
        target_model_root: The MMD model (Character combo) to retarget onto.

    For an MMD source: auto-characterize + ``enter_source_mode`` on the
    source, then auto-characterize + ``enter_target_mode`` on the target (no
    confirmation dialogs -- HUMANIK-FRONTEND-1 Phase B6). For an external
    source: no auto-characterize of the source (it is never mutated), and --
    only for this branch -- a one-time check for existing animCurves on the
    target's HIK-assigned joints, since baking an external retarget onto an
    already-keyed channel fails (HUMANIK-EXTERNAL-SOURCE-1 ES-2 probe
    finding). The user is asked whether to clear that animation first, keep
    it and try anyway, or cancel; MMD-to-MMD connects are unaffected.

    Reuses the already-wrapped ``enter_source_mode``/``enter_target_mode``/
    ``enter_external_source_mode`` public functions, so a failure at either
    step is already reported to the user by that step's own ``_run_action``/
    ``_report_action_failure`` -- this function only decides whether to
    continue to the next step, never duplicates the error reporting. On any
    failure (or a mid-flow dialog cancel) this returns ``None`` without
    raising; callers must re-read ``describe_frontend_state`` to learn the
    real resulting state rather than trusting that SOURCE ended up bound.
    """
    return _run_action("Connect Retarget", lambda: _connect_retarget(source, target_model_root))


def _normalize_source_selector(source):
    """Return ``(kind, value)`` for any Source-combo item-data shape.

    Accepts a bare string (legacy MMD-only callers/tests), a ``(kind,
    value)`` tuple/list, or a ``{"kind": ..., "modelRoot"/"character": ...}``
    mapping -- see ``connect_retarget``.
    """
    if isinstance(source, (tuple, list)) and len(source) == 2:
        return str(source[0]), source[1]
    if isinstance(source, Mapping):
        kind = str(source.get("kind", SOURCE_KIND_MMD))
        value = source.get("modelRoot") if kind == SOURCE_KIND_MMD else source.get("character")
        return kind, value
    return SOURCE_KIND_MMD, source


def _connect_retarget(source, target_model_root: str):
    kind, value = _normalize_source_selector(source)
    if not value or not target_model_root:
        raise ValueError("HumanIK retarget requires both a Character and a Source model")
    if kind == SOURCE_KIND_EXTERNAL:
        if not _confirm_clear_existing_target_animation(target_model_root):
            return None
        if enter_external_source_mode(character=value) is None:
            # enter_external_source_mode already reported its own failure;
            # do not proceed to target mode with no SOURCE actually bound.
            return None
    else:
        if value == target_model_root:
            raise ValueError("HumanIK Source and Character models must differ")
        if enter_source_mode(model_root=value) is None:
            return None
    return enter_target_mode(model_root=target_model_root)


def _confirm_clear_existing_target_animation(target_model_root: str) -> bool:
    """Ask before connecting an external SOURCE onto an already-keyed TARGET.

    HUMANIK-EXTERNAL-SOURCE-1 ES-3: the ES-2 probe found that baking an
    external HumanIK retarget onto a TARGET joint that already carries a VMD
    ``animCurve`` hits the bake path's write-conflict guard. This is a light,
    read-only scan (``cmds.listConnections`` per HIK-assigned joint) run only
    on the external-source connect path -- MMD-to-MMD connects keep their
    existing behavior unchanged.

    Returns ``True`` to proceed (nothing found, or the user chose to clear or
    keep the animation), ``False`` on cancel.
    """
    session = get_humanik_session()
    curves = _find_target_animcurves(session, target_model_root)
    if not curves:
        return True
    choice = _dialog_choice(
        "Existing Animation",
        (
            f"{_short_model_label(target_model_root)} already has {len(curves)} animation "
            "curve(s) on its HumanIK-assigned joints. Baking an external HumanIK source onto "
            "already-keyed channels will fail.\n"
            "Clear the existing animation before connecting?"
        ),
        ("Clear and connect", "Connect anyway", "Cancel"),
    )
    if choice == "Clear and connect":
        _clear_animcurves(curves)
        return True
    if choice == "Connect anyway":
        return True
    return False


def _find_target_animcurves(session, target_model_root: str) -> list:
    """Return every distinct ``animCurve`` feeding a joint HumanIK would assign.

    Uses ``inspect_model`` (a pure resolve, no characterization required) so
    this works whether or not ``target_model_root`` is characterized yet.
    Fails soft to an empty list on any query error, matching every other
    scene-fact helper this module and ``humanik_frontend`` use.
    """
    try:
        report = session.inspect_model(target_model_root)
    except Exception:
        return []
    joints = [row.get("joint") for row in report.get("assignments", []) if row.get("joint")]
    cmds = _cmds_module or _maya_cmds()
    seen = set()
    curves = []
    for joint in joints:
        try:
            connected = cmds.listConnections(joint, source=True, destination=False, type="animCurve") or []
        except Exception:
            connected = []
        for curve in connected:
            curve_name = str(curve)
            if curve_name not in seen:
                seen.add(curve_name)
                curves.append(curve_name)
    return curves


def _clear_animcurves(curves) -> None:
    """Delete ``curves`` inside one undo chunk so the clear is a single undo step."""
    cmds = _cmds_module or _maya_cmds()
    existing = []
    for curve in curves:
        try:
            if cmds.objExists(curve):
                existing.append(curve)
        except Exception:
            continue
    if not existing:
        return
    cmds.undoInfo(openChunk=True)
    try:
        cmds.delete(existing)
    finally:
        cmds.undoInfo(closeChunk=True)


def disconnect_retarget():
    """Restore the MMD rig to disconnect the active retarget (Source combo -> "None").

    HUMANIK-FRONTEND-1 Phase B6: confirmation is shown only when a Control
    Rig transaction is currently active (deleting it is the one
    irreversible-in-this-call side effect); otherwise this restores
    immediately, matching the other de-popup-ified actions. A cancelled
    confirmation returns ``None`` without mutating the scene.
    """
    return _run_action("Disconnect Retarget", _disconnect_retarget)


def _disconnect_retarget():
    session = get_humanik_session()
    if _has_active_control_rig(session):
        message = (
            "Disconnect the HumanIK retarget and restore the MMD rig?\n"
            "The active Control Rig will also be deleted."
        )
        if not _confirm("Disconnect Retarget", message):
            return None
    return session.restore_mmd_rig()


def _has_active_control_rig(session) -> bool:
    """Return whether ``session`` currently has at least one active Control Rig.

    Read-only: reuses ``describe_frontend_state()['controlRigs']`` (already
    computed from live transactions) rather than reaching into the session's
    private state. Fails soft to ``False`` -- if this cannot be determined,
    the confirmation is skipped rather than raising, matching every other
    ``describe_frontend_state`` consumer in this module.
    """
    describe = getattr(session, "describe_frontend_state", None)
    if not callable(describe):
        return False
    try:
        state = describe() or {}
    except Exception:
        return False
    return bool(state.get("controlRigs"))


def bake_to_mmd_rig(start=None, end=None):
    """Bake the active target preview over an explicit or playback frame range.

    Args:
        start: Optional explicit integer start frame. When ``None`` (the
            menu path), Maya's current integer playback range is used.
        end: Optional explicit integer end frame; same default as ``start``.
    """
    return _run_action("Bake to MMD Rig", lambda: _bake_to_mmd_rig(start=start, end=end))


def restore_mmd_rig():
    """Restore active preview state or pending setup characters without a model selection.

    HUMANIK-FRONTEND-1 Phase B6: confirmation is shown only when a Control
    Rig transaction is currently active, matching ``disconnect_retarget``.
    """
    return _run_action("Restore MMD Rig", _restore_mmd_rig)


def _restore_mmd_rig():
    session = get_humanik_session()
    if _has_active_control_rig(session):
        if not _confirm(
            "Restore MMD Rig",
            "This will delete the active HumanIK Control Rig and restore the journal. Continue?",
        ):
            return None
    return session.restore_mmd_rig()


def diagnostics():
    """Show a JSON-safe session diagnostics report in a scrollable Maya window."""
    return _run_action("Diagnostics", _show_diagnostics)


def _ensure_characterized(session, root: str, action: str) -> None:
    """Auto-characterize ``root`` with the default profile if ``action`` needs it.

    ``describe_frontend_state`` mirrors the same not-characterized guard that
    ``enter_source_mode``/``enter_target_mode``/``create_control_rig`` would
    otherwise raise on, so a missing characterization is treated as a
    recoverable state rather than an error: this runs ``setup_and_characterize``
    with the default profile and notifies the user, then lets the caller
    continue with its own operation. Reason codes other than
    ``not_characterized`` (for example a missing SOURCE model, or a profile
    mismatch) are left alone; only the one guard this action can self-heal is
    handled here.

    HUMANIK-FRONTEND-1 Phase B6: the default profile is now
    ``FULL_ASSIGNMENT_PROFILE`` (body + fingers) -- there is no existing
    binding to preserve here (this only runs when ``not_characterized`` is
    true), so there is nothing to default *to* except the new full default.
    """
    describe = getattr(session, "describe_frontend_state", None)
    if not callable(describe):
        return
    try:
        state = describe(root) or {}
    except Exception:
        return
    action_state = (state.get("actions") or {}).get(action) or {}
    if action_state.get("reasonCode") != REASON_NOT_CHARACTERIZED:
        return
    _display_info(
        f"HumanIK: {_short_model_label(root)} is not characterized yet; "
        "auto-characterizing with the default Full (body + fingers) profile."
    )
    session.setup_and_characterize(
        root,
        profile=FULL_ASSIGNMENT_PROFILE,
        include_fingers=True,
    )


def _resolve_setup_profile(session, root: str):
    """Return ``(profile, include_fingers)`` for an explicit Setup / Characterize call.

    HUMANIK-FRONTEND-1 Phase B6: characterize always uses the full (body +
    fingers) profile now, and the previous "Body only / Body + fingers /
    Cancel" picker dialog is gone -- but a model already characterized with
    a different profile (body-only, characterized before this change, or by
    an older session) must not be silently re-characterized: ``diagnostics``
    reports the existing binding's profile when one exists, and that wins.
    """
    diagnostics_fn = getattr(session, "diagnostics", None)
    if callable(diagnostics_fn):
        try:
            diag = diagnostics_fn(root) or {}
        except Exception:
            diag = {}
        if diag.get("character"):
            existing_profile = diag.get("profile") or FULL_ASSIGNMENT_PROFILE
            return existing_profile, existing_profile == FULL_ASSIGNMENT_PROFILE
    return FULL_ASSIGNMENT_PROFILE, True


def _setup_and_characterize(model_root: Optional[str] = None):
    """Characterize with the default full profile; no selection dialog (Phase B6).

    Preflight information that used to gate a "Body only / Body + fingers /
    Cancel" confirmation is now shown as a plain info message after a
    successful characterize -- there is nothing left to confirm since the
    profile choice itself is gone (see ``_resolve_setup_profile``).
    """
    root = model_root if model_root is not None else resolve_model_root(cmds_module=_cmds_module)
    if root is None:
        return None
    session = get_humanik_session()
    profile, include_fingers = _resolve_setup_profile(session, root)
    body_report = session.inspect_model(
        root,
        profile=FRONTEND_ASSIGNMENT_PROFILE,
        include_fingers=False,
    )
    full_report = session.inspect_model(
        root,
        profile=FULL_ASSIGNMENT_PROFILE,
        include_fingers=True,
    )
    try:
        # Run ownership preflight for the selected profile before mutating the scene.
        ownership_report = session.inspect_target_ownership(
            root,
            profile=profile,
            include_fingers=include_fingers,
        )
        binding = session.setup_and_characterize(
            root,
            profile=profile,
            include_fingers=include_fingers,
        )
    except Exception as exc:
        summary = _report_action_failure("Setup / Characterize", exc, model_root=root)
        return {
            "success": False,
            "action": "setup_and_characterize",
            "modelRoot": root,
            "profile": profile,
            "error": summary,
        }
    _display_info(_setup_confirmation_message(root, body_report, ownership_report, full_report))
    warning = _stance_warning_message(binding)
    if warning:
        _display_warning(warning)
    return {
        "success": True,
        "action": "setup_and_characterize",
        "modelRoot": root,
        "profile": profile,
        "binding": binding,
    }


def _stance_warning_message(binding: Any) -> Optional[str]:
    """Return a short user warning for a usable but numerically imperfect stance."""
    stance = getattr(binding, "stance", None)
    if stance is None and isinstance(binding, Mapping):
        stance = binding.get("stance")
    pose = stance.get("pose", {}) if isinstance(stance, Mapping) else {}
    if not pose.get("warning"):
        return None
    slots = ", ".join(str(value) for value in pose.get("warningRows", [])) or "arm"
    return (
        "HumanIK T-pose is usable but did not reach the strict numeric tolerance "
        f"({slots}). Characterization continued."
    )


def _enter_source_mode(model_root: Optional[str] = None):
    root = model_root if model_root is not None else resolve_model_root(cmds_module=_cmds_module)
    if root is None:
        return None
    session = get_humanik_session()
    _ensure_characterized(session, root, "enter_source_mode")
    return session.enter_source_mode(root)


def _enter_target_mode(model_root: Optional[str] = None):
    """Start a TARGET preview after ownership/profile preflight; no confirmation (Phase B6).

    The ownership summary that used to gate a "Continue/Cancel" dialog is now
    shown as a plain info message after the preview actually starts.
    """
    root = model_root if model_root is not None else resolve_model_root(cmds_module=_cmds_module)
    if root is None:
        return None
    session = get_humanik_session()
    _ensure_characterized(session, root, "enter_target_mode")
    report = session.inspect_target_ownership(root)
    diagnostics_fn = getattr(session, "diagnostics", None)
    lifecycle_diagnostics = diagnostics_fn() if callable(diagnostics_fn) else {}
    source_profile = (lifecycle_diagnostics or {}).get("source", {}).get("profile")
    source_external = bool((lifecycle_diagnostics or {}).get("source", {}).get("external"))
    target_profile = report.get("profile")
    if not source_external and source_profile and target_profile and source_profile != target_profile:
        raise ValueError(
            "HumanIK source/target assignment profile mismatch: "
            f"source={source_profile}, target={target_profile}; "
            "Restore both models and reconnect them so they both characterize with "
            f"the same profile (default: {FULL_ASSIGNMENT_PROFILE}) before target mode"
        )
    blockers = report.get("blockers", [])
    if blockers:
        labels = ", ".join(
            f"{row.get('node', '')}:{row.get('classification', '')}" for row in blockers
        )
        raise RuntimeError(f"HumanIK target preview blocked: {labels}")
    result = session.enter_target_mode(root)
    _display_info(_target_confirmation_message(root, report))
    return result


def _create_control_rig(model_root: Optional[str] = None):
    """Create a control rig on the (auto-characterized) model; no confirmation (Phase B6)."""
    root = model_root if model_root is not None else resolve_model_root(cmds_module=_cmds_module)
    if root is None:
        return None
    session = get_humanik_session()
    _ensure_characterized(session, root, "create_control_rig")
    return session.create_control_rig(root)


def _bake_to_mmd_rig(start=None, end=None):
    """Bake the active target preview; no confirmation (Phase B6, no configurable options)."""
    cmds = _cmds_module or _maya_cmds()
    session = get_humanik_session()
    if start is None:
        start = math.ceil(float(cmds.playbackOptions(query=True, minTime=True)))
    if end is None:
        end = math.floor(float(cmds.playbackOptions(query=True, maxTime=True)))
    start = int(start)
    end = int(end)
    if end < start:
        raise ValueError(f"Bake frame range is empty after integer conversion: {start}..{end}")
    result = session.bake_to_mmd_rig(start, end)
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
    cmds.window(DIAGNOSTICS_WINDOW_NAME, title="HumanIK Diagnostics (Experimental)", sizeable=True)
    cmds.columnLayout(adjustableColumn=True)
    cmds.text(label="HumanIK is experimental.", align="left")
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


def _setup_confirmation_message(root, model_report, ownership_report, full_report=None):
    blockers = ownership_report.get("blockers", [])
    unresolved = len(model_report.get("missingMmdBones", []))
    ambiguous = len(model_report.get("ambiguous", []))
    body_count = len(model_report.get("bodyAssignments") or []) or model_report.get("assignmentCount", 0)
    if full_report is None:
        if model_report.get("profile") == FULL_ASSIGNMENT_PROFILE:
            finger_count = max(model_report.get("assignmentCount", 0) - body_count, 0)
        else:
            finger_count = model_report.get("excludedFingerCount", 0)
    else:
        full_count = len(full_report.get("assignments") or [])
        finger_count = max(full_count - body_count, 0)
    lines = [
        "HumanIK is experimental.",
        f"Set up HumanIK for {_short_model_label(root)}?",
        f"Body only: {body_count} bones (default)",
        f"Body + fingers: {body_count + finger_count} bones ({finger_count} finger bones)",
        "The arms are aligned temporarily, then the original pose is restored.",
    ]
    if unresolved or ambiguous or blockers:
        lines.append(
            f"Issues: unresolved {unresolved}, ambiguous {ambiguous}, blockers {len(blockers)}"
        )
    return "\n".join(lines)


def _target_confirmation_message(root, report):
    """Return the informational summary shown after a TARGET preview starts.

    HUMANIK-FRONTEND-1 Phase B6: this used to be a "Continue?" confirmation
    message; entering TARGET mode no longer asks first (the ownership
    preflight/blocker check above already ran and would have raised), so this
    is now shown via ``_display_info`` after the preview is already active.
    """
    counts = report.get("constraintCounts", {})
    return (
        f"Target model root: {_short_model_label(root)}\n"
        "The target preview journaled changes, disconnected mute_for_hik writer edges, "
        "muted only those writers, "
        "retained keep_post writers, and will restore all ownership on Restore MMD Rig.\n"
        f"mute_for_hik: {counts.get('mute_for_hik', 0)}; "
        f"keep_post: {counts.get('keep_post', 0)}; "
        f"blocker summary: {_blocker_summary(report.get('blockers', []))}."
    )


def _blocker_summary(blockers, limit: int = 3) -> str:
    """Return a compact blocker classification summary without node paths."""
    counts = {}
    for row in blockers:
        classification = str(row.get("classification", "unknown"))
        counts[classification] = counts.get(classification, 0) + 1
    values = [f"{classification} ({count})" for classification, count in sorted(counts.items())]
    if len(values) > limit:
        return ", ".join(values[:limit]) + f", +{len(values) - limit} more"
    return ", ".join(values) or "none"


def _short_model_label(root: str, limit: int = 80) -> str:
    """Keep confirmation titles readable for deeply nested Maya paths."""
    value = str(root or "").strip("|")
    label = value.rsplit("|", 1)[-1] or str(root)
    if len(label) > limit:
        return label[: limit - 3].rstrip() + "..."
    return label


def _short_exception_summary(exc: Exception, limit: int = 180) -> str:
    """Normalize exception text for a safe, bounded user-facing dialog."""
    summary = " ".join(str(exc).split()) or exc.__class__.__name__
    if len(summary) > limit:
        return summary[: limit - 3].rstrip() + "..."
    return summary


def _confirm(title, message):
    return _dialog_choice(title, message, ("Continue", "Cancel")) == "Continue"


def _dialog_choice(title, message, buttons):
    """Show an injectable choice dialog and return its raw button label."""
    if _confirm_dialog is not None:
        try:
            return _confirm_dialog(
                title=title,
                message=message,
                button=list(buttons),
                defaultButton=buttons[0],
                cancelButton="Cancel",
                dismissString="Cancel",
            )
        except TypeError:
            # Preserve compatibility with older injectable callbacks accepting
            # only title/message while keeping the production button contract.
            return _confirm_dialog(title=title, message=message)
    cmds = _cmds_module or _maya_cmds()
    return cmds.confirmDialog(
        title=title,
        message=message,
        button=list(buttons),
        defaultButton=buttons[0],
        cancelButton="Cancel",
        dismissString="Cancel",
    )


def _run_action(label, operation):
    try:
        return operation()
    except Exception as exc:
        _report_action_failure(label, exc)
        return None


def _report_action_failure(label: str, exc: Exception, *, model_root: Optional[str] = None) -> str:
    """Log a full action traceback and show only a bounded user-facing summary."""
    prefix = f"HumanIK {label} failed: "
    suffix = ". See the Maya Script Editor for details."
    summary = _short_exception_summary(exc, limit=max(4, 180 - len(prefix) - len(suffix)))
    try:
        install_maya_script_editor_handler()
    except Exception:
        pass
    logger.error(
        "HumanIK %s failed%s: %s",
        label,
        f" for {model_root}" if model_root else "",
        exc,
        exc_info=True,
    )
    _display_error(prefix + summary + suffix)
    return summary


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
    "open_humanik_editor": open_humanik_editor,
    "setup_and_characterize": setup_and_characterize,
    "enter_source_mode": enter_source_mode,
    "enter_target_mode": enter_target_mode,
    "create_control_rig": create_control_rig,
    "bake_to_mmd_rig": bake_to_mmd_rig,
    "restore_mmd_rig": restore_mmd_rig,
    "diagnostics": diagnostics,
}
