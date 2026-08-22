"""Fail-closed scene mutation tracking for prepared export payloads.

The exporter cannot safely reuse a prepared scene collection after a Maya
mutation.  This module keeps that boundary deliberately small: a caller arms
one watch for the MObjects which were used by a prepare operation, then
compares the service revision before consuming the prepared payload.

Maya is imported lazily so the service is also useful from pure Python unit
tests.  Every callback is registered through an injectable OpenMaya-like
module; a partial registration is treated as disabled and all callbacks
already installed for that handle are removed.
"""

from __future__ import annotations

import uuid
from typing import Any, Callable, Iterable, Optional, Set, Tuple


# These are names rather than values because Maya's enum values are supplied
# by the host and this keeps tests (and future C++ adapters) injectable.
DEFAULT_RELEVANT_ATTRIBUTE_FLAGS = frozenset(
    {
        "kAttributeAdded",
        "kAttributeArrayAdded",
        "kAttributeArrayRemoved",
        "kAttributeRemoved",
        "kAttributeRenamed",
        "kAttributeSet",
        "kConnectionBroken",
        "kConnectionMade",
    }
)
DEFAULT_IGNORED_ATTRIBUTE_FLAGS = frozenset({"kAttributeEval"})
UNDO_EVENT = "Undo"
REDO_EVENT = "Redo"


def _load_open_maya() -> Any:
    """Import Maya API 2.0 only when a real watch is armed."""

    import maya.api.OpenMaya as om

    return om


def _load_open_maya_anim() -> Any:
    """Import Maya animation API 2.0 only when a watch is armed."""

    import maya.api.OpenMayaAnim as oma

    return oma


def _uuid_from_mobject(om: Any, node: Any) -> Optional[str]:
    """Return a stable UUID for an MObject-like value."""

    if isinstance(node, str):
        return node.strip() or None
    node_method = getattr(node, "node", None)
    if callable(node_method):
        node = node_method()
    fn = om.MFnDependencyNode(node)
    value = fn.uuid()
    if hasattr(value, "asString"):
        value = value.asString()
    return str(value) if value else None


def _mobject_from_uuid(om: Any, value: str) -> Any:
    """Resolve a UUID through an injected or real MSelectionList."""

    selection = om.MSelectionList()
    selection.add(value)
    return selection.getDependNode(0)


class SceneRevisionWatch:
    """One armed dependency watch.

    A disabled or stale watch is never reusable.  ``close`` is intentionally
    idempotent because scene teardown and normal action cleanup can race.
    """

    def __init__(self, service: "SceneRevisionService", dependency_uuids: Set[str]):
        self._service = service
        self.dependency_uuids = frozenset(dependency_uuids)
        self.session_id = service.session_id
        self.revision = service.revision
        self.stale = False
        self.disabled = False
        self.closed = False
        self._callback_ids: list[Tuple[Any, Any]] = []

    @property
    def usable(self) -> bool:
        """Whether this watch may still validate a prepared payload."""

        return not self.closed and not self.disabled and not self.stale

    @property
    def current(self) -> bool:
        """Whether its snapshot still describes the service's current scene."""

        return self.usable and self._service.matches(self.session_id, self.revision)

    def close(self) -> None:
        """Remove every callback registered for this handle."""

        if self.closed:
            return
        self.closed = True
        callback_ids = self._callback_ids
        self._callback_ids = []
        self._service._remove_callback_ids(callback_ids)
        self._service._handles.discard(self)

    def _mark_stale(self) -> None:
        if not self.closed:
            self.stale = True

    def _disable(self) -> None:
        if not self.closed:
            self.disabled = True
            self.stale = True


class SceneRevisionService:
    """Track relevant mutations for one process-local Maya scene session."""

    def __init__(
        self,
        om_module: Any = None,
        oma_module: Any = None,
        *,
        target_uuid_lookup: Optional[Callable[[Any], Optional[str]]] = None,
        session_id_factory: Optional[Callable[[], str]] = None,
        relevant_attribute_flags: Optional[Iterable[Any]] = None,
        ignored_attribute_flags: Optional[Iterable[Any]] = None,
    ) -> None:
        self._om = om_module
        self._oma = oma_module
        self._target_uuid_lookup = target_uuid_lookup
        self._session_id_factory = session_id_factory or (lambda: str(uuid.uuid4()))
        self._relevant_attribute_flags = relevant_attribute_flags
        self._ignored_attribute_flags = ignored_attribute_flags
        self._session_id = self._session_id_factory()
        self._revision = 0
        self._handles: Set[SceneRevisionWatch] = set()

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def revision(self) -> int:
        return self._revision

    def matches(self, session_id: str, revision: int) -> bool:
        """Check a caller's token without arming another watcher."""

        return session_id == self.session_id and revision == self.revision

    def current_revision(self, *_args: Any) -> int:
        """Return the current monotonic revision for prepare providers."""

        # Maya batches animation-edit notifications until idle.  A prepared
        # payload is consumed synchronously, so flush that queue before the
        # revision is compared or a just-edited key could look fresh.
        self._get_oma().MAnimMessage.flushAnimKeyframeEditedCallbacks()
        return self.revision

    def target_uuid(self, target: Any) -> Optional[str]:
        """Resolve an MObject/UUID to the stable target UUID used by callbacks."""

        if self._target_uuid_lookup is not None:
            try:
                result = self._target_uuid_lookup(target)
            except Exception:
                return None
            return str(result) if result else None
        try:
            return _uuid_from_mobject(self._get_om(), target)
        except Exception:
            return None

    def arm(self, dependencies: Iterable[Any]) -> SceneRevisionWatch:
        """Install target and global callbacks for *dependencies*.

        ``dependencies`` accepts MObjects or UUID strings.  A UUID is resolved
        back to an MObject before target-local callbacks are installed.  Any
        missing target or registration error disables the returned handle and
        removes all callbacks which were successfully installed beforehand.
        """

        try:
            om = self._get_om()
        except Exception:
            watch = SceneRevisionWatch(self, set())
            watch._disable()
            return watch
        dependency_uuids: Set[str] = set()
        target_objects = []
        try:
            for target in dependencies or ():
                node_uuid = self.target_uuid(target)
                if not node_uuid:
                    raise ValueError("dependency has no stable UUID")
                dependency_uuids.add(node_uuid)
                if isinstance(target, str):
                    target = _mobject_from_uuid(om, node_uuid)
                target_objects.append((node_uuid, target))
        except Exception:
            watch = SceneRevisionWatch(self, dependency_uuids)
            watch._disable()
            return watch

        # Maya may defer animation-edit notifications until an explicit
        # flush. Drain edits which happened before this watch existed before
        # registering its callback, so the new watch starts from a clean
        # baseline. Edits queued after registration are still flushed by
        # ``current_revision`` and invalidate the watch normally.
        try:
            self._get_oma().MAnimMessage.flushAnimKeyframeEditedCallbacks()
        except Exception:
            watch = SceneRevisionWatch(self, dependency_uuids)
            watch._disable()
            return watch

        watch = SceneRevisionWatch(self, dependency_uuids)
        self._handles.add(watch)
        try:
            for node_uuid, target in target_objects:
                self._register_node_callbacks(watch, node_uuid, target)
            self._register_global_callbacks(watch)
        except Exception:
            watch._disable()
            watch.close()
            # ``close`` removes the handle from the service, but the returned
            # object remains disabled and provides fail-closed evidence.
        return watch

    def reset_session(self) -> None:
        """Start a new scene session and invalidate all existing watches."""

        self._session_id = self._session_id_factory()
        # Keep the revision monotonic across sessions; the session token is
        # what prevents a token from one scene from matching a later scene.
        self._revision += 1
        for watch in list(self._handles):
            watch._mark_stale()

    def _get_om(self) -> Any:
        if self._om is None:
            self._om = _load_open_maya()
        return self._om

    def _get_oma(self) -> Any:
        if self._oma is None:
            self._oma = _load_open_maya_anim()
        return self._oma

    def _bump(self, _reason: str = "mutation") -> None:
        # Python ints are unbounded; this is a monotonic uint by construction.
        self._revision += 1
        for watch in list(self._handles):
            watch._mark_stale()

    def _register_node_callbacks(self, watch: SceneRevisionWatch, node_uuid: str, node: Any) -> None:
        om = self._get_om()
        node_message = om.MNodeMessage
        watch._callback_ids.append(
            (node_message, node_message.addAttributeChangedCallback(node, self._attribute_changed_callback, watch))
        )
        watch._callback_ids.append(
            (node_message, node_message.addNameChangedCallback(node, self._name_changed_callback, watch))
        )
        watch._callback_ids.append(
            (node_message, node_message.addNodeDestroyedCallback(node, self._node_destroyed_callback, watch))
        )

    def _register_global_callbacks(self, watch: SceneRevisionWatch) -> None:
        om = self._get_om()
        oma = self._get_oma()
        anim_message = oma.MAnimMessage
        watch._callback_ids.append(
            (
                anim_message,
                anim_message.addAnimCurveEditedCallback(
                    self._anim_curve_edited_callback,
                    watch,
                ),
            )
        )
        dg_message = om.MDGMessage
        watch._callback_ids.append(
            (dg_message, dg_message.addConnectionCallback(self._connection_callback, watch))
        )
        watch._callback_ids.append(
            (dg_message, dg_message.addNodeAddedCallback(self._node_added_callback, "dependNode", watch))
        )
        watch._callback_ids.append(
            (dg_message, dg_message.addNodeRemovedCallback(self._node_removed_callback, "dependNode", watch))
        )

        dag_message = getattr(om, "MDagMessage", None)
        if dag_message is None or not hasattr(dag_message, "addAllDagChangesCallback"):
            raise RuntimeError("Maya DAG callback API is unavailable")
        watch._callback_ids.append(
            (dag_message, dag_message.addAllDagChangesCallback(self._dag_changed_callback, watch))
        )

        scene_message = om.MSceneMessage
        scene_events = tuple(
            getattr(scene_message, name)
            for name in ("kBeforeOpen", "kAfterOpen", "kBeforeNew", "kAfterNew")
            if hasattr(scene_message, name)
        )
        if not scene_events:
            raise RuntimeError("Maya scene open/new callback API is unavailable")
        for scene_event in scene_events:
            watch._callback_ids.append(
                (scene_message, scene_message.addCallback(scene_event, self._scene_reset_callback, watch))
            )

        event_message = om.MEventMessage
        for event_name in (UNDO_EVENT, REDO_EVENT):
            watch._callback_ids.append(
                (event_message, event_message.addEventCallback(event_name, self._undo_redo_callback, watch))
            )

    def _remove_callback_ids(self, callback_ids: Iterable[Tuple[Any, Any]]) -> None:
        try:
            om = self._get_om()
        except Exception:
            om = None
        for owner, callback_id in reversed(list(callback_ids)):
            try:
                remover = getattr(owner, "removeCallback", None)
                if remover is None and om is not None:
                    remover = getattr(om.MMessage, "removeCallback")
                if remover is not None:
                    remover(callback_id)
            except Exception:
                # Teardown is best-effort; Maya may already have removed a
                # callback as part of scene destruction.
                continue

    def _attribute_changed_callback(self, message: Any, _plug: Any = None, _other: Any = None, client_data: Any = None) -> None:
        watch = client_data
        if not isinstance(watch, SceneRevisionWatch) or not watch.usable:
            return
        relevant = self._resolve_flags(self._relevant_attribute_flags, DEFAULT_RELEVANT_ATTRIBUTE_FLAGS)
        ignored = self._resolve_flags(self._ignored_attribute_flags, DEFAULT_IGNORED_ATTRIBUTE_FLAGS)
        if not isinstance(message, int):
            return
        relevant_mask = 0
        for flag in relevant:
            relevant_mask |= flag
        ignored_mask = 0
        for flag in ignored:
            ignored_mask |= flag
        mutation_bits = message & relevant_mask
        effective_mutation_bits = mutation_bits & ~ignored_mask
        # AttributeMessage is a bit field.  Maya combines kAttributeEval with
        # modifiers such as kIncomingDirection (2052 in Maya 2024), so an
        # exact-value comparison would let evaluation callbacks through.
        # Subtract ignored bits from the mutation mask, so Eval|Set,
        # connection, and array-removal evidence survives when those bits are
        # not explicitly configured as ignored.
        if not effective_mutation_bits:
            return
        incoming_direction = self._resolve_flags(None, ("kIncomingDirection",))
        array_added = self._resolve_flags(None, ("kAttributeArrayAdded",))
        incoming_mask = next(iter(incoming_direction), 0)
        array_added_mask = next(iter(array_added), 0)
        # Runtime array nodes can materialize an incoming element while Maya
        # evaluates a frame (6144 = kIncomingDirection | kAttributeArrayAdded).
        # Connection callbacks still cover topology edits; a standalone array
        # addition and all explicit kAttributeSet/connection messages remain
        # mutation evidence.
        if (
            incoming_mask
            and array_added_mask
            and message & incoming_mask
            and message & array_added_mask
            and not (effective_mutation_bits & ~array_added_mask)
        ):
            return
        self._bump("attribute")

    def _name_changed_callback(self, _node: Any = None, client_data: Any = None) -> None:
        self._bump_for_watch(client_data, "rename")

    def _node_destroyed_callback(self, _node: Any = None, client_data: Any = None) -> None:
        self._bump_for_watch(client_data, "destroy")

    def _connection_callback(self, source: Any, destination: Any, _made: bool, client_data: Any = None) -> None:
        watch = client_data
        if not isinstance(watch, SceneRevisionWatch) or not watch.usable:
            return
        if self._plug_matches_watch(source, watch) or self._plug_matches_watch(destination, watch):
            self._bump("connection")

    def _anim_curve_edited_callback(self, objects: Any, client_data: Any = None) -> None:
        watch = client_data
        if not isinstance(watch, SceneRevisionWatch) or not watch.usable:
            return
        try:
            edited = list(objects or ())
        except TypeError:
            edited = [objects]
        if any(self._node_matches_watch(node, watch) for node in edited):
            self._bump("anim-curve")

    def _node_added_callback(self, node: Any, client_data: Any = None) -> None:
        self._bump_if_uuid_matches(node, client_data, "node-added")

    def _node_removed_callback(self, node: Any, client_data: Any = None) -> None:
        self._bump_if_uuid_matches(node, client_data, "node-removed")

    def _dag_changed_callback(self, _message: Any, parent: Any, child: Any, client_data: Any = None) -> None:
        watch = client_data
        if not isinstance(watch, SceneRevisionWatch) or not watch.usable:
            return
        if self._node_matches_watch(parent, watch) or self._node_matches_watch(child, watch):
            self._bump("dag")

    def _scene_reset_callback(self, *args: Any) -> None:
        watch = args[-1] if args else None
        if isinstance(watch, SceneRevisionWatch) and not watch.usable:
            return
        self.reset_session()

    def _undo_redo_callback(self, client_data: Any = None, *_args: Any) -> None:
        self._bump_for_watch(client_data, "undo-redo")

    def _bump_for_watch(self, watch: Any, reason: str) -> None:
        if isinstance(watch, SceneRevisionWatch) and watch.usable:
            self._bump(reason)

    def _bump_if_uuid_matches(self, node: Any, watch: Any, reason: str) -> None:
        if not isinstance(watch, SceneRevisionWatch) or not watch.usable:
            return
        node_uuid = self.target_uuid(node)
        if node_uuid in watch.dependency_uuids:
            self._bump(reason)

    def _node_matches_watch(self, node: Any, watch: SceneRevisionWatch) -> bool:
        return self.target_uuid(node) in watch.dependency_uuids

    def _plug_matches_watch(self, plug: Any, watch: SceneRevisionWatch) -> bool:
        if plug is None:
            return False
        try:
            node = plug.node() if callable(getattr(plug, "node", None)) else plug.node
        except Exception:
            return False
        return self._node_matches_watch(node, watch)

    def _resolve_flags(self, configured: Optional[Iterable[Any]], defaults: Iterable[str]) -> Set[int]:
        values = configured if configured is not None else defaults
        result: Set[int] = set()
        om = None
        for value in values:
            if isinstance(value, int):
                result.add(value)
            else:
                if om is None:
                    om = self._get_om()
                flag = getattr(om.MNodeMessage, str(value), None)
                if flag is not None:
                    result.add(int(flag))
        return result


__all__ = [
    "DEFAULT_IGNORED_ATTRIBUTE_FLAGS",
    "DEFAULT_RELEVANT_ATTRIBUTE_FLAGS",
    "REDO_EVENT",
    "SceneRevisionService",
    "SceneRevisionWatch",
    "UNDO_EVENT",
]
