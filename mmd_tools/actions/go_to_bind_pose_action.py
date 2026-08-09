"""Reversible model-wide bind-pose display session for the Bone tab.

The session temporarily isolates transform writers, delegates the actual bind
restore to Maya's ``dagPose``, and restores values, locks, and topology exactly
when motion display resumes.  Animation curves and rig nodes are never edited.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass

from maya import cmds as maya_cmds

from ..core.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class GoToBindPoseResult:
    """Result of a model-scoped bind-pose session transition."""

    succeeded: bool
    model_root: str = ""
    joint_count: int = 0
    error: str = ""
    active: bool = False


@dataclass(frozen=True)
class _PlugSnapshot:
    """Original value and lock state for one transform plug."""

    node_uuid: str
    attribute: str
    value: object


@dataclass(frozen=True)
class _LockSnapshot:
    """Original lock state for a compound or leaf transform plug."""

    node_uuid: str
    attribute: str
    locked: bool


@dataclass(frozen=True)
class _EdgeSnapshot:
    """One incoming writer edge isolated for the session."""

    source_uuid: str
    source_attribute: str
    destination_uuid: str
    destination_attribute: str


@dataclass(frozen=True)
class _SelectionSnapshot:
    """UUID-backed selection item with an optional component suffix."""

    node_uuid: str
    suffix: str = ""


class GoToBindPoseAction:
    """Enter and leave one reversible model bind-pose display session."""

    def __init__(self, cmds_module=None):
        self.cmds = cmds_module or maya_cmds
        self._model_root = ""
        self._model_uuid = ""
        self._joint_count = 0
        self._plugs: tuple[_PlugSnapshot, ...] = ()
        self._locks: tuple[_LockSnapshot, ...] = ()
        self._edges: tuple[_EdgeSnapshot, ...] = ()
        self._selection: tuple[_SelectionSnapshot, ...] = ()
        self._current_time: float | None = None

    @property
    def active(self) -> bool:
        """Return whether this action currently owns an isolated rest session."""

        return bool(self._model_uuid)

    def execute(self, model_root: str) -> GoToBindPoseResult:
        """Enter bind-pose display while preserving the live rig graph payload."""

        if self.active:
            return GoToBindPoseResult(
                False,
                self._model_root,
                self._joint_count,
                "A Bind Pose session is already active",
                True,
            )

        if not model_root or not self.cmds.objExists(model_root):
            return GoToBindPoseResult(False, error="No valid MMD model selected")
        joints = sorted(
            set(
                str(joint)
                for joint in (
                    self.cmds.listRelatives(
                        model_root,
                        allDescendents=True,
                        type="joint",
                        fullPath=True,
                    )
                    or []
                )
            )
        )
        if not joints:
            return GoToBindPoseResult(
                False,
                str(model_root),
                error="Selected model has no joints",
            )
        poses = []
        for joint in joints:
            for pose in self.cmds.dagPose(joint, query=True, bindPose=True) or []:
                if pose not in poses:
                    poses.append(str(pose))
        if not poses:
            return GoToBindPoseResult(
                False,
                str(model_root),
                len(joints),
                "Selected model has no bind pose",
            )
        plugs: tuple[_PlugSnapshot, ...] = ()
        locks: tuple[_LockSnapshot, ...] = ()
        edges: tuple[_EdgeSnapshot, ...] = ()
        selection: tuple[_SelectionSnapshot, ...] = ()
        current_time: float | None = None
        try:
            model_uuid = self._unique_uuid(model_root)
            pose = self._select_complete_pose(poses, model_root, joints)
            plugs = self._capture_plugs(joints)
            locks = self._capture_locks(joints)
            edges = self._capture_edges(joints)
            selection = self._capture_selection()
            current_time = float(self.cmds.currentTime(query=True))
            with self._undo_suppressed():
                self._isolate(edges, locks)
                self.cmds.dagPose(pose, **{"restore": True, "global": True})
                self._restore_locks(locks)
                self._restore_context(selection, current_time)
            self._model_root = str(model_root)
            self._model_uuid = model_uuid
            self._joint_count = len(joints)
            self._plugs = plugs
            self._locks = locks
            self._edges = edges
            self._selection = selection
            self._current_time = current_time
        except Exception as exc:
            try:
                if plugs and current_time is not None:
                    self._rollback_enter(plugs, locks, edges, selection, current_time)
            except Exception as rollback_error:
                exc = RuntimeError(f"{exc}; rollback failed: {rollback_error}")
            logger.error("Go to Bind Pose failed", exc_info=True)
            return GoToBindPoseResult(
                False,
                str(model_root),
                len(joints),
                str(exc),
            )
        return GoToBindPoseResult(True, str(model_root), len(joints), active=True)

    def return_to_motion(self) -> GoToBindPoseResult:
        """Restore the exact pre-session transform values, locks, and writers."""

        if not self.active:
            return GoToBindPoseResult(False, error="No Bind Pose session is active")
        model_root = self._model_root
        joint_count = self._joint_count
        try:
            self._assert_active_model()
            self._assert_no_foreign_writers()
            with self._undo_suppressed():
                self._restore_snapshot_transactional()
        except Exception as exc:
            logger.error("Return to Motion failed", exc_info=True)
            return GoToBindPoseResult(
                False,
                model_root,
                joint_count,
                str(exc),
                active=True,
            )
        self._clear_session()
        return GoToBindPoseResult(True, model_root, joint_count, active=False)

    def _unique_uuid(self, node: str) -> str:
        uuids = self.cmds.ls(node, uuid=True) or []
        if len(uuids) != 1:
            raise RuntimeError(f"Bind Pose model UUID is unavailable: {node}")
        return str(uuids[0])

    def _select_complete_pose(self, poses, model_root: str, joints: list[str]) -> str:
        roots = self.cmds.ls(model_root, long=True) or []
        if len(roots) != 1:
            raise RuntimeError(f"Bind Pose model root is ambiguous: {model_root}")
        root = str(roots[0])
        joint_set = set(joints)
        for pose in poses:
            members = set()
            invalid = False
            for member in self.cmds.dagPose(pose, query=True, members=True) or []:
                paths = self.cmds.ls(member, long=True) or []
                if len(paths) != 1:
                    invalid = True
                    break
                path = str(paths[0])
                if path != root and not path.startswith(root + "|"):
                    invalid = True
                    break
                members.add(path)
            if not invalid and joint_set <= members:
                return str(pose)
        raise RuntimeError("No model-scoped bind pose covers every selected-model joint")

    @staticmethod
    def _value_plugs(joint: str) -> tuple[str, ...]:
        channels = tuple(
            f"{joint}.{attribute}{axis}"
            for attribute in ("translate", "rotate", "scale")
            for axis in "XYZ"
        )
        shear = tuple(f"{joint}.shear{axis}" for axis in ("XY", "XZ", "YZ"))
        return (*channels, *shear, f"{joint}.offsetParentMatrix")

    def _capture_plugs(self, joints: list[str]) -> tuple[_PlugSnapshot, ...]:
        snapshots = []
        for joint in joints:
            for plug in self._value_plugs(joint):
                node, attribute = plug.rsplit(".", 1)
                snapshots.append(_PlugSnapshot(self._unique_uuid(node), attribute, self.cmds.getAttr(plug)))
        return tuple(snapshots)

    def _capture_locks(self, joints: list[str]) -> tuple[_LockSnapshot, ...]:
        snapshots = []
        for joint in joints:
            node_uuid = self._unique_uuid(joint)
            attributes = ("translate", "rotate", "scale", "shear") + tuple(
                plug.rsplit(".", 1)[1] for plug in self._value_plugs(joint)
            )
            for attribute in attributes:
                plug = f"{joint}.{attribute}"
                snapshots.append(_LockSnapshot(node_uuid, attribute, bool(self.cmds.getAttr(plug, lock=True))))
        return tuple(snapshots)

    def _capture_edges(self, joints: list[str]) -> tuple[_EdgeSnapshot, ...]:
        allowed = set()
        for joint in joints:
            allowed.update(self._value_plugs(joint))
            allowed.update(f"{joint}.{name}" for name in ("translate", "rotate", "scale", "shear"))
        edges = []
        seen = set()
        for joint in joints:
            pairs = self.cmds.listConnections(
                joint,
                source=True,
                destination=False,
                plugs=True,
                connections=True,
            ) or []
            if len(pairs) % 2:
                raise RuntimeError(f"Invalid connection pair data returned for {joint}")
            for index in range(0, len(pairs), 2):
                destination = self._canonical_plug(str(pairs[index]))
                source = self._canonical_plug(str(pairs[index + 1]))
                if destination not in allowed:
                    continue
                edge = (source, destination)
                if edge not in seen:
                    seen.add(edge)
                    source_node, source_attribute = source.rsplit(".", 1)
                    destination_node, destination_attribute = destination.rsplit(".", 1)
                    edges.append(
                        _EdgeSnapshot(
                            self._unique_uuid(source_node),
                            source_attribute,
                            self._unique_uuid(destination_node),
                            destination_attribute,
                        )
                    )
        return tuple(edges)

    def _isolate(
        self,
        edges: tuple[_EdgeSnapshot, ...],
        locks: tuple[_LockSnapshot, ...],
    ) -> None:
        self._unlock_plugs(locks)
        for edge in edges:
            source, destination = self._edge_plugs(edge)
            if self.cmds.isConnected(source, destination):
                self.cmds.disconnectAttr(source, destination)
        identity = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
        for snapshot in self._plugs_from_locks(locks, "offsetParentMatrix"):
            self.cmds.setAttr(self._resolve_plug(snapshot.node_uuid, snapshot.attribute), *identity, type="matrix")

    def _restore_locks(self, locks: tuple[_LockSnapshot, ...]) -> None:
        for snapshot in reversed(locks):
            self.cmds.setAttr(
                self._resolve_plug(snapshot.node_uuid, snapshot.attribute),
                lock=snapshot.locked,
            )

    def _rollback_enter(self, plugs, locks, edges, selection, current_time) -> None:
        errors = []
        with self._undo_suppressed():
            self._best_effort(errors, self._disconnect_edges, edges)
            self._best_effort(errors, self._unlock_plugs, locks)
            self._best_effort(errors, self._restore_values, plugs)
            self._best_effort(errors, self._restore_edges, edges)
            self._best_effort(errors, self._restore_locks, locks)
            self._best_effort(errors, self._restore_context, selection, current_time)
        if errors:
            raise RuntimeError("; ".join(errors))

    def _restore_snapshot_transactional(self) -> None:
        bind_values = self._capture_current_values(self._plugs)
        bind_locks = self._capture_current_locks(self._locks)
        bind_selection = self._capture_selection()
        bind_time = float(self.cmds.currentTime(query=True))
        try:
            self._disconnect_edges(self._edges)
            self._unlock_plugs(self._locks)
            self._restore_values(self._plugs)
            self._restore_edges(self._edges)
            self._restore_locks(self._locks)
            self._restore_context(self._selection, self._current_time)
        except Exception as exc:
            errors = []
            self._best_effort(errors, self._disconnect_edges, self._edges)
            self._best_effort(errors, self._unlock_plugs, bind_locks)
            self._best_effort(errors, self._restore_values, bind_values)
            self._best_effort(errors, self._restore_locks, bind_locks)
            self._best_effort(errors, self._restore_context, bind_selection, bind_time)
            if errors:
                raise RuntimeError(f"{exc}; bind-session rollback failed: {'; '.join(errors)}") from exc
            raise

    def _unlock_plugs(self, locks) -> None:
        for snapshot in locks:
            self.cmds.setAttr(
                self._resolve_plug(snapshot.node_uuid, snapshot.attribute),
                lock=False,
            )

    def _restore_values(self, plugs) -> None:
        for snapshot in plugs:
            plug = self._resolve_plug(snapshot.node_uuid, snapshot.attribute)
            if snapshot.attribute == "offsetParentMatrix":
                values = snapshot.value[0] if len(snapshot.value) == 1 and isinstance(snapshot.value[0], (tuple, list)) else snapshot.value
                self.cmds.setAttr(plug, *values, type="matrix")
            else:
                self.cmds.setAttr(plug, snapshot.value)

    def _restore_edges(self, edges) -> None:
        for edge in edges:
            source, destination = self._edge_plugs(edge)
            if not self.cmds.isConnected(source, destination):
                self.cmds.connectAttr(source, destination, force=False)

    def _disconnect_edges(self, edges) -> None:
        for edge in edges:
            source, destination = self._edge_plugs(edge)
            if self.cmds.isConnected(source, destination):
                self.cmds.disconnectAttr(source, destination)

    def _restore_context(self, selection, current_time) -> None:
        if current_time is not None:
            self.cmds.currentTime(current_time, edit=True)
        items = []
        for snapshot in selection:
            paths = self.cmds.ls(snapshot.node_uuid, long=True) or []
            if len(paths) != 1:
                raise RuntimeError(f"Bind Pose selection node changed or was deleted: {snapshot.node_uuid}")
            items.append(f"{paths[0]}{snapshot.suffix}")
        self.cmds.select(items, replace=True)

    def _assert_active_model(self) -> None:
        paths = self.cmds.ls(self._model_uuid, long=True) or []
        if len(paths) != 1:
            raise RuntimeError("Bind Pose model changed while the session was active")

    def _assert_no_foreign_writers(self) -> None:
        original = {self._edge_plugs(edge) for edge in self._edges}
        destinations = {
            self._resolve_plug(snapshot.node_uuid, snapshot.attribute)
            for snapshot in self._locks
        }
        foreign = []
        nodes = {destination.rsplit(".", 1)[0] for destination in destinations}
        for node in nodes:
            pairs = self.cmds.listConnections(
                node, source=True, destination=False, plugs=True, connections=True
            ) or []
            for index in range(0, len(pairs), 2):
                destination = self._canonical_plug(str(pairs[index]))
                source = self._canonical_plug(str(pairs[index + 1]))
                if destination in destinations and (source, destination) not in original:
                    foreign.append(f"{source} -> {destination}")
        if foreign:
            raise RuntimeError("Bind Pose topology drift; Return to Motion refused: " + "; ".join(sorted(foreign)))

    def _clear_session(self) -> None:
        self._model_root = ""
        self._model_uuid = ""
        self._joint_count = 0
        self._plugs = ()
        self._locks = ()
        self._edges = ()
        self._selection = ()
        self._current_time = None

    def _resolve_plug(self, node_uuid: str, attribute: str) -> str:
        paths = self.cmds.ls(node_uuid, long=True) or []
        if len(paths) != 1:
            raise RuntimeError(f"Bind Pose node changed or was deleted: {node_uuid}")
        return f"{paths[0]}.{attribute}"

    def _canonical_plug(self, plug: str) -> str:
        node, attribute = plug.rsplit(".", 1)
        paths = self.cmds.ls(node, long=True) or []
        if len(paths) != 1:
            raise RuntimeError(f"Bind Pose connection node is ambiguous: {node}")
        return f"{paths[0]}.{attribute}"

    def _edge_plugs(self, edge: _EdgeSnapshot) -> tuple[str, str]:
        return (
            self._resolve_plug(edge.source_uuid, edge.source_attribute),
            self._resolve_plug(edge.destination_uuid, edge.destination_attribute),
        )

    @staticmethod
    def _plugs_from_locks(locks, attribute):
        return tuple(snapshot for snapshot in locks if snapshot.attribute == attribute)

    def _capture_current_values(self, templates) -> tuple[_PlugSnapshot, ...]:
        return tuple(
            _PlugSnapshot(
                snapshot.node_uuid,
                snapshot.attribute,
                self.cmds.getAttr(self._resolve_plug(snapshot.node_uuid, snapshot.attribute)),
            )
            for snapshot in templates
        )

    def _capture_current_locks(self, templates) -> tuple[_LockSnapshot, ...]:
        return tuple(
            _LockSnapshot(
                snapshot.node_uuid,
                snapshot.attribute,
                bool(self.cmds.getAttr(self._resolve_plug(snapshot.node_uuid, snapshot.attribute), lock=True)),
            )
            for snapshot in templates
        )

    def _capture_selection(self) -> tuple[_SelectionSnapshot, ...]:
        snapshots = []
        for item in self.cmds.ls(selection=True, long=True) or []:
            item = str(item)
            node, separator, component = item.partition(".")
            snapshots.append(
                _SelectionSnapshot(
                    self._unique_uuid(node),
                    f".{component}" if separator else "",
                )
            )
        return tuple(snapshots)

    @staticmethod
    def _best_effort(errors, function, *args) -> None:
        try:
            function(*args)
        except Exception as exc:
            errors.append(str(exc))

    @contextmanager
    def _undo_suppressed(self):
        """Keep the two-step Python session state out of Maya's undo queue."""
        try:
            previous = bool(self.cmds.undoInfo(query=True, state=True))
        except Exception as exc:
            raise RuntimeError("Cannot query Maya Undo state for Bind Pose session") from exc
        if not previous:
            yield
            return
        try:
            self._set_undo_state_without_flush(False)
        except Exception as exc:
            try:
                self._set_undo_state_without_flush(True)
            except Exception:
                logger.error("Failed to recover Maya Undo after suppression failure", exc_info=True)
            raise RuntimeError("Cannot suppress Maya Undo for Bind Pose session") from exc
        try:
            yield
        finally:
            self._set_undo_state_without_flush(True)

    def _set_undo_state_without_flush(self, enabled: bool) -> None:
        """Set and verify Maya Undo state, retrying one transient command failure."""
        errors = []
        for _attempt in range(2):
            try:
                self.cmds.undoInfo(stateWithoutFlush=enabled)
                if bool(self.cmds.undoInfo(query=True, state=True)) == enabled:
                    return
                errors.append(f"Undo state did not become {enabled}")
            except Exception as exc:
                errors.append(str(exc))
        raise RuntimeError("; ".join(errors))
