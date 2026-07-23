"""Detect unsupported HumanIK Control Rigs without mutating the scene.

The supported plugin path isolates MMD writers before creating a Control Rig.
Rigs created from Maya's standard HumanIK UI bypass that transaction, so this
module observes new ``HIKControlSetNode`` nodes and warns for characterized MMD
models that have no active plugin transaction.

The callback is registered for ``dependNode`` because HumanIK node types load
lazily and cannot be used safely as the registration filter. Scene inspection
is deferred until Maya finishes wiring the new node, with bounded retries while
its character connection is still unavailable. Registration and teardown are
fail-soft for batch hosts where HumanIK UI facilities are absent.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from mmd_tools.core.humanik_control_rig import get_active_control_rig_transaction
from mmd_tools.core.logger import get_logger


logger = get_logger(__name__)

HIK_CONTROL_SET_NODE_TYPE = "HIKControlSetNode"

_node_added_callback_id = None

# --- HUMANIK-FRONTEND-1 Phase C: pluggable warning callbacks ----------------
#
# The default warning path (logger + ``cmds.warning``, see ``_warn``) always
# runs and is never removed by registering a callback here -- this list is
# purely additive, so a UI (the HumanIK tab) can *also* surface the same
# warning without this module gaining any UI dependency or ever mutating the
# scene itself. Callbacks are invoked from ``_emit_warning``, which itself
# only ever runs from ``_handle_new_hik_control_set_node`` -- the
# ``maya.utils.executeDeferred`` handler described in this module's docstring
# -- so callbacks observe the same "main thread, scene fully settled" context
# as the default path; a callback that raises is logged and swallowed so one
# broken subscriber can never suppress the warning for others or for the
# default path.
_warning_callbacks: List[Callable[..., None]] = []


def register_control_rig_warning_callback(callback: Callable[..., None]) -> None:
    """Add a callback invoked whenever this module warns about an out-of-band Control Rig.

    Called as ``callback(message, character=character, model_root=model_root)``
    -- ``model_root`` is the mmd_tools model root the Control Rig's character
    resolves to (via ``HumanIkFrontendSession.find_binding_by_character``), or
    ``None`` if that lookup fails. Safe to call more than once with the same
    callable (de-duplicated); the default logger/``cmds.warning`` path is
    unaffected either way.
    """
    if callback not in _warning_callbacks:
        _warning_callbacks.append(callback)


def deregister_control_rig_warning_callback(callback: Callable[..., None]) -> None:
    """Remove a callback registered via :func:`register_control_rig_warning_callback`.

    A no-op if ``callback`` is not currently registered.
    """
    try:
        _warning_callbacks.remove(callback)
    except ValueError:
        pass


def register_humanik_control_rig_watch(om_module=None) -> bool:
    """Register the node-added watch callback (filtered to ``HIKControlSetNode``
    inside the callback itself, not at registration).

    ``MDGMessage.addNodeAddedCallback(callback, "HIKControlSetNode")`` raises
    ``kInvalidParameter`` unless a node type of that name is already
    registered in the DG -- and ``HIKControlSetNode`` (like every other HIK
    node type) is only registered once Maya's HumanIK plugin has loaded,
    which does not happen at mmd_tools plugin-load time (HIK loads lazily,
    the first time HIK MEL/commands actually run -- see
    ``humanik_builder.ensure_humanik_mel_loaded``). So this registers against
    the always-present base type ``dependNode`` (every node), and
    ``_on_hik_control_set_node_added`` does the type check itself -- a single
    string comparison, still cheap enough to run synchronously for every
    node the scene ever creates.

    Safe to call more than once (a no-op if already registered) and safe to
    call in mayapy/batch hosts -- any ``OpenMaya`` failure is logged and
    swallowed, returning ``False`` instead of raising, since the watch is a
    best-effort safety net, not a hard plugin-load requirement.

    Args:
        om_module: Optional ``maya.api.OpenMaya`` compatible module for
            tests. Maya API 2.0 classes are immutable extension types, so
            ``unittest.mock.patch`` cannot monkeypatch their static methods
            directly under real ``mayapy`` -- tests inject a fake module here
            instead, the same pattern ``cmds_module``/``mel_module`` use
            elsewhere in this codebase.
    """
    global _node_added_callback_id
    if _node_added_callback_id is not None:
        return True
    om = om_module
    if om is None:
        try:
            import maya.api.OpenMaya as om
        except Exception:
            logger.debug(
                "HumanIK control rig watch unavailable: OpenMaya import failed", exc_info=True
            )
            return False
    try:
        _node_added_callback_id = om.MDGMessage.addNodeAddedCallback(
            _on_hik_control_set_node_added, "dependNode"
        )
    except Exception:
        logger.warning("Failed to register HumanIK control rig watch callback", exc_info=True)
        _node_added_callback_id = None
        return False
    return True


def deregister_humanik_control_rig_watch(om_module=None) -> None:
    """Remove the watch callback if registered; safe to call repeatedly.

    Args:
        om_module: Optional ``maya.api.OpenMaya`` compatible module for
            tests; see :func:`register_humanik_control_rig_watch`.
    """
    global _node_added_callback_id
    callback_id = _node_added_callback_id
    _node_added_callback_id = None
    if callback_id is None:
        return
    om = om_module
    try:
        if om is None:
            import maya.api.OpenMaya as om
        om.MMessage.removeCallback(callback_id)
    except Exception:
        logger.debug(
            "Failed to deregister HumanIK control rig watch callback", exc_info=True
        )


def _on_hik_control_set_node_added(mobject, _client_data=None, om_module=None) -> None:
    """Raw node-added callback: cheap type filter + capture, all real work deferred.

    Registered against ``dependNode`` (see
    :func:`register_humanik_control_rig_watch` for why), so this fires for
    every node the scene creates -- the first thing it does is a plain string
    comparison against ``HIK_CONTROL_SET_NODE_TYPE`` and returns immediately
    for anything else. For an actual match, Maya is still mid-operation --
    ``hikCreateControlRig`` is still wiring connections when this runs -- so
    this must never touch the scene directly; it only reads the new node's
    persistent UUID and schedules ``_handle_new_hik_control_set_node`` via
    ``maya.utils.executeDeferred``.

    A UUID -- not the node's name at creation time -- is what gets deferred:
    empirically, ``hikCreateControlRig()`` creates the node under a generic
    default name (``HIKControlSetNode1``) and renames it to
    ``"{character}_ControlRig"`` before this callback's deferred work ever
    runs, so resolving by the captured name later would find nothing and
    silently treat a real rig as "transient, ignored" (see
    :func:`_handle_new_hik_control_set_node`). ``MUuid`` survives the rename.

    ``executeDeferred`` (not ``cmds.evalDeferred``) is used deliberately: this
    codebase already relies on ``maya.utils.executeDeferred`` +
    ``maya.utils.processIdleEvents()`` as a verified-working deferred/flush
    pair (see ``tests/viewport/material_morph_e2e.py``), whereas
    ``cmds.evalDeferred`` given a Python callable was not reliably flushed by
    ``processIdleEvents()`` in manual verification against Maya 2026.

    Args:
        om_module: Optional ``maya.api.OpenMaya`` compatible module for
            tests; see :func:`register_humanik_control_rig_watch`.
    """
    try:
        node_uuid = _hik_control_set_node_uuid(mobject, om_module)
    except Exception:
        logger.debug(
            "HumanIK control rig watch: failed to read node-added UUID", exc_info=True
        )
        return
    if node_uuid is None:
        return
    try:
        from maya import utils as maya_utils

        maya_utils.executeDeferred(_handle_new_hik_control_set_node, node_uuid)
    except Exception:
        logger.debug(
            "HumanIK control rig watch: executeDeferred scheduling failed", exc_info=True
        )


def _hik_control_set_node_uuid(mobject, om_module=None) -> Optional[str]:
    """Return the new node's UUID string if it is a ``HIKControlSetNode``, else ``None``.

    A UUID is captured instead of the node's name because
    ``hikCreateControlRig()`` renames the node (default name ->
    ``"{character}_ControlRig"``) before the deferred handler runs -- see
    :func:`_on_hik_control_set_node_added`'s docstring.
    """
    om = om_module
    if om is None:
        import maya.api.OpenMaya as om
    node_fn = om.MFnDependencyNode(mobject)
    if node_fn.typeName != HIK_CONTROL_SET_NODE_TYPE:
        return None
    return node_fn.uuid().asString()


MAX_CHARACTER_RESOLUTION_RETRIES = 10


def _handle_new_hik_control_set_node(node_uuid: str, retry: int = 0) -> None:
    """Deferred handler: classify ownership and warn if the rig is out-of-band.

    This never mutates the scene -- it only decides whether to log a warning
    directing the user to the supported mmd_tools path.

    Args:
        node_uuid: Persistent UUID of the new ``HIKControlSetNode``, captured
            by the raw callback (see :func:`_on_hik_control_set_node_added`).
        retry: How many times this specific node has already been
            rescheduled after failing to resolve its owning character. See
            the ``if not character`` branch below for why this exists.
    """
    from maya import cmds

    matches = cmds.ls(node_uuid) or []
    if not matches:
        # Transient node (e.g. the create was undone) by the time Maya
        # flushed idle events; nothing to warn about.
        return
    node_name = str(matches[0])

    try:
        character = _resolve_character_for_hik_control_set_node(node_name, cmds)
    except Exception:
        logger.error(
            "HumanIK control rig watch: failed to resolve character for %s",
            node_name,
            exc_info=True,
        )
        return
    if not character:
        # Empirically (E2E reruns), hikCreateControlRig() does not always
        # finish wiring HIKControlSetNode -> HIKCharacterNode by the time our
        # own executeDeferred callback runs -- HIK appears to complete some
        # of its own post-create wiring through its own idle-queue work,
        # which can land on a later idle tick than ours. Treating a miss on
        # the very first attempt as "not a Control Rig node" silently
        # abandoned real, still-forming Control Rigs (observed as flaky E2E
        # behavior). Reschedule a bounded number of times instead of giving
        # up after a single miss; each retry is another cheap
        # executeDeferred hop, not a busy-wait.
        if retry < MAX_CHARACTER_RESOLUTION_RETRIES:
            try:
                from maya import utils as maya_utils

                maya_utils.executeDeferred(
                    _handle_new_hik_control_set_node, node_uuid, retry + 1
                )
            except Exception:
                logger.debug(
                    "HumanIK control rig watch: failed to reschedule character "
                    "resolution for %s",
                    node_name,
                    exc_info=True,
                )
            return
        logger.debug(
            "HumanIK control rig watch: no HIK character found for %s after %d retries",
            node_name,
            retry,
        )
        return

    if get_active_control_rig_transaction(character) is not None:
        # The plugin/frontend path (begin_humanik_control_rig) already owns
        # writer isolation for this rig -- nothing to warn about.
        return

    binding = _find_frontend_binding_for_character(character)
    if binding is None:
        logger.debug(
            "HumanIK control rig watch: '%s' is not a characterized mmd_tools "
            "binding; skipping warning",
            character,
        )
        return

    message = (
        "MMD Tools detected a HumanIK Control Rig created outside MMD Tools "
        f"(Maya's standard HumanIK UI or a raw hikCreateControlRig() call) for "
        f"character '{character}'. This can create a DG cycle with the MMD rig "
        "(the same kind of cycle through mmdCcdIk writers described by "
        "HUMANIK-CONTROL-RIG-CYCLE-1). MMD Tools did not modify this Control "
        "Rig. Supported path: delete it via Character Controls, then use "
        "MMD > HumanIK > 'Create Control Rig' (or 'Restore MMD Rig' first if "
        "one already exists from that path)."
    )
    # ``getattr`` with a default: test doubles for ``HumanIkFrontendBinding``
    # (see ``FakeBinding`` in ``test_humanik_control_rig_watch.py``) may omit
    # ``model_root`` entirely; production bindings always have it.
    _emit_warning(message, character=character, model_root=getattr(binding, "model_root", None))


def _emit_warning(message: str, *, character: str, model_root: Optional[str]) -> None:
    """Run the default logger/``cmds.warning`` path, then notify subscribers.

    The default path always runs first and unconditionally -- registering a
    callback (see :func:`register_control_rig_warning_callback`) is purely
    additive. Each callback is isolated in its own ``try``/``except`` so a
    broken subscriber (for example a UI callback raising because a widget was
    already destroyed) can never prevent the default warning, or a *different*
    subscriber, from running.
    """
    logger.warning(message)
    _warn(message)
    for callback in list(_warning_callbacks):
        try:
            callback(message, character=character, model_root=model_root)
        except Exception:
            logger.debug(
                "HumanIK control rig watch: warning callback failed", exc_info=True
            )


def _warn(message: str) -> None:
    try:
        from maya import cmds

        cmds.warning(message)
    except Exception:
        pass


def _resolve_character_for_hik_control_set_node(node_name: str, cmds) -> Optional[str]:
    """Return the ``HIKCharacterNode`` name feeding ``node_name``, if any."""
    connected = cmds.listConnections(node_name, type="HIKCharacterNode") or []
    if not connected:
        return None
    return str(connected[0])


def _find_frontend_binding_for_character(character: str):
    """Return the active frontend session's binding for ``character``, if tracked.

    A lazy import keeps ``core`` decoupled from the ``ui`` layer at module
    load time -- the same pattern ``plugin_main.py`` already uses for its own
    lazy ``mmd_tools.ui.humanik_menu_actions`` imports. The frontend session
    singleton is, for this cycle, the only place mmd_tools tracks which
    model_root/HIK character/joint bindings exist, so this warning is limited
    to models characterized through it -- an unrelated, non-mmd_tools HIK
    character in the scene never triggers it.
    """
    from mmd_tools.ui import humanik_menu_actions

    session = humanik_menu_actions.get_humanik_session()
    return session.find_binding_by_character(character)
