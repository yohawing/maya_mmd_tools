"""Non-destructive model-wide Rest Pose display session.

The session isolates incoming translate/rotate writers on joints owned by one
MMD model, restores the bind pose for display, then restores the exact channel
values, lock states, and connection topology when motion display resumes.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Optional

from maya import cmds as maya_cmds

from ..converters.vmd_import_state import get_stored_bind_translate
from ..core.logger import get_logger

logger = get_logger(__name__)

_TRANSFORM_ATTRS = (
    "translate",
    "translateX",
    "translateY",
    "translateZ",
    "rotate",
    "rotateX",
    "rotateY",
    "rotateZ",
)


@dataclass(frozen=True)
class RestPoseEdge:
    """One exact incoming connection isolated by the Rest Pose session."""

    source: str
    destination: str


@dataclass
class RestPoseJointSnapshot:
    """Restorable state for one model-owned joint."""

    joint: str
    translate: tuple[float, float, float]
    rotate: tuple[float, float, float]
    locks: dict[str, bool]
    edges: list[RestPoseEdge] = field(default_factory=list)
    bind_translate: Optional[tuple[float, float, float]] = None


@dataclass
class RestPoseSession:
    """In-memory transaction for one active Rest Pose display."""

    model_root: str
    joints: list[RestPoseJointSnapshot]


@dataclass(frozen=True)
class RestPoseResult:
    """Result returned by Rest Pose enter/return operations."""

    succeeded: bool
    active: bool
    model_root: str = ""
    joint_count: int = 0
    error: str = ""


class RestPoseManager:
    """Own the single scene-wide temporary Rest Pose display session."""

    def __init__(self, cmds_module=None):
        self.cmds = cmds_module or maya_cmds
        self._session: Optional[RestPoseSession] = None
        self._history_session: Optional[RestPoseSession] = None
        self._listeners: list[Callable[[RestPoseResult], None]] = []
        self._scene_callback_ids: list[object] = []

    @property
    def active(self) -> bool:
        return self._session is not None

    @property
    def model_root(self) -> str:
        return self._session.model_root if self._session else ""

    def add_listener(self, callback: Callable[[RestPoseResult], None]) -> None:
        """Subscribe to active-state changes without adding duplicates."""
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[RestPoseResult], None]) -> None:
        """Remove a previously registered state listener."""
        if callback in self._listeners:
            self._listeners.remove(callback)

    def state(self) -> RestPoseResult:
        """Return the current session state."""
        if not self._session:
            return RestPoseResult(True, False)
        return RestPoseResult(
            True,
            True,
            self._session.model_root,
            len(self._session.joints),
        )

    def toggle(self, model_root: str) -> RestPoseResult:
        """Enter Rest Pose, or return the active model to motion."""
        if self._session:
            return self.return_to_motion()
        return self.enter_rest_pose(model_root)

    def ensure_model(self, model_root: str) -> RestPoseResult:
        """Return active motion before the owning UI changes model."""
        if self._session and self._session.model_root != model_root:
            return self.return_to_motion()
        return self.state()

    def enter_rest_pose(self, model_root: str) -> RestPoseResult:
        """Snapshot, isolate, and display the selected model's bind pose."""
        if not model_root or not self.cmds.objExists(model_root):
            return RestPoseResult(False, False, error="No valid MMD model selected")
        if self._session:
            return RestPoseResult(False, True, self.model_root, error="Rest Pose is already active")

        joints = self.cmds.listRelatives(
            model_root,
            allDescendents=True,
            type="joint",
            fullPath=True,
        ) or []
        joints = sorted(set(str(joint) for joint in joints))
        if not joints:
            return RestPoseResult(False, False, model_root, error="Selected model has no joints")

        session = RestPoseSession(
            model_root=str(model_root),
            joints=[self._capture_joint(joint) for joint in joints],
        )
        try:
            with self._undo_chunk("MMD Rest Pose"):
                self._isolate(session)
                self._apply_rest_pose(session)
        except Exception as exc:
            logger.error("Failed to enter Rest Pose", exc_info=True)
            try:
                self._restore(session)
            except Exception:
                logger.error("Rest Pose rollback failed", exc_info=True)
            return RestPoseResult(False, False, model_root, error=str(exc))

        self._session = session
        self._history_session = session
        result = self.state()
        self._notify(result)
        return result

    def return_to_motion(self) -> RestPoseResult:
        """Restore the active model's exact pre-session motion state."""
        session = self._session
        if session is None:
            return RestPoseResult(True, False)
        if self._incoming_session_edges(session):
            result = RestPoseResult(
                False,
                True,
                session.model_root,
                len(session.joints),
                "Rest Pose topology changed; remove new incoming joint connections first",
            )
            self._notify(result)
            return result
        try:
            with self._undo_chunk("MMD Return to Motion"):
                self._restore(session)
        except Exception as exc:
            logger.error("Failed to return from Rest Pose", exc_info=True)
            result = RestPoseResult(
                False,
                True,
                session.model_root,
                len(session.joints),
                str(exc),
            )
            self._notify(result)
            return result

        self._session = None
        result = RestPoseResult(True, False, session.model_root, len(session.joints))
        self._notify(result)
        return result

    def install_scene_callbacks(self) -> None:
        """Return motion before scene changes and track Undo/Redo state."""
        if self._scene_callback_ids:
            return
        try:
            import maya.api.OpenMaya as om

            for message in (
                om.MSceneMessage.kBeforeNew,
                om.MSceneMessage.kBeforeOpen,
                om.MSceneMessage.kBeforeSave,
                om.MSceneMessage.kMayaExiting,
            ):
                self._scene_callback_ids.append(
                    om.MSceneMessage.addCallback(message, self._before_scene_change)
                )
            self._scene_callback_ids.append(
                om.MEventMessage.addEventCallback("Undo", self._after_undo)
            )
            self._scene_callback_ids.append(
                om.MEventMessage.addEventCallback("Redo", self._after_redo)
            )
        except Exception:
            logger.debug("Rest Pose scene callbacks unavailable", exc_info=True)

    def _before_scene_change(self, *_args) -> None:
        if self._session:
            self.return_to_motion()
        self._history_session = None

    def _after_undo(self, *_args) -> None:
        self._sync_history_state()

    def _after_redo(self, *_args) -> None:
        self._sync_history_state()

    def _sync_history_state(self) -> None:
        session = self._history_session
        if session is None:
            return
        scene_is_active = not self._matches_motion_state(session)
        if scene_is_active and self._session is None:
            self._session = session
            self._notify(self.state())
        elif not scene_is_active and self._session is not None:
            self._session = None
            self._notify(
                RestPoseResult(True, False, session.model_root, len(session.joints))
            )

    def _capture_joint(self, joint: str) -> RestPoseJointSnapshot:
        locks = {}
        for attr in _TRANSFORM_ATTRS:
            plug = f"{joint}.{attr}"
            try:
                locks[attr] = bool(self.cmds.getAttr(plug, lock=True))
            except Exception:
                locks[attr] = False
        stored = get_stored_bind_translate(joint)
        return RestPoseJointSnapshot(
            joint=joint,
            translate=self._vector_value(f"{joint}.translate"),
            rotate=self._vector_value(f"{joint}.rotate"),
            locks=locks,
            edges=self._incoming_transform_edges(joint),
            bind_translate=tuple(stored) if stored is not None else None,
        )

    def _incoming_transform_edges(self, joint: str) -> list[RestPoseEdge]:
        raw = self.cmds.listConnections(
            joint,
            source=True,
            destination=False,
            plugs=True,
            connections=True,
            skipConversionNodes=False,
        ) or []
        edges = []
        joint_leaf = joint.rsplit("|", 1)[-1]

        def _is_joint_plug(plug: str) -> bool:
            node = plug.rsplit(".", 1)[0]
            return node == joint or node == joint_leaf

        for index in range(0, len(raw) - 1, 2):
            first, second = str(raw[index]), str(raw[index + 1])
            if _is_joint_plug(first):
                destination, source = first, second
            elif _is_joint_plug(second):
                destination, source = second, first
            else:
                continue
            attr = destination.rsplit(".", 1)[-1]
            if attr in _TRANSFORM_ATTRS:
                edges.append(RestPoseEdge(source, f"{joint}.{attr}"))
        return sorted(set(edges), key=lambda edge: (edge.destination, edge.source))

    def _isolate(self, session: RestPoseSession) -> None:
        for snapshot in session.joints:
            self._set_locks(snapshot, False)
            for edge in snapshot.edges:
                if self._is_connected(edge):
                    self.cmds.disconnectAttr(edge.source, edge.destination)

    def _apply_rest_pose(self, session: RestPoseSession) -> None:
        restored = False
        for snapshot in session.joints:
            poses = self.cmds.dagPose(snapshot.joint, query=True, bindPose=True) or []
            if not poses:
                continue
            try:
                self.cmds.dagPose(poses[0], restore=True)
            except Exception:
                logger.debug(
                    "Bind dagPose restore failed; using stored bind transforms",
                    exc_info=True,
                )
                continue
            else:
                restored = True
                break
        if restored:
            return
        missing_bind = [
            snapshot.joint
            for snapshot in session.joints
            if snapshot.bind_translate is None
        ]
        if missing_bind:
            raise RuntimeError(
                "No bind dagPose or stored bind translate for: "
                + ", ".join(missing_bind[:3])
            )
        for snapshot in session.joints:
            self.cmds.setAttr(f"{snapshot.joint}.rotate", 0.0, 0.0, 0.0)
            self.cmds.setAttr(f"{snapshot.joint}.translate", *snapshot.bind_translate)

    def _restore(self, session: RestPoseSession) -> None:
        failures = []
        for snapshot in session.joints:
            if not self.cmds.objExists(snapshot.joint):
                failures.append(f"missing joint: {snapshot.joint}")
                continue
            self._set_locks(snapshot, False)
            for destination in self._transform_destinations(snapshot.joint):
                for source in self.cmds.listConnections(
                    destination,
                    source=True,
                    destination=False,
                    plugs=True,
                ) or []:
                    self.cmds.disconnectAttr(str(source), destination)
            try:
                self.cmds.setAttr(f"{snapshot.joint}.translate", *snapshot.translate)
                self.cmds.setAttr(f"{snapshot.joint}.rotate", *snapshot.rotate)
                for edge in snapshot.edges:
                    if not self._is_connected(edge):
                        self.cmds.connectAttr(edge.source, edge.destination, force=True)
            except Exception as exc:
                failures.append(f"{snapshot.joint}: {exc}")
            finally:
                self._set_locks(snapshot, None)
        if failures:
            raise RuntimeError("; ".join(failures))
        try:
            current = self.cmds.currentTime(query=True)
            self.cmds.currentTime(current, edit=True, update=True)
        except Exception:
            logger.debug("Could not refresh restored motion evaluation", exc_info=True)

    @staticmethod
    def _transform_destinations(joint: str) -> tuple[str, ...]:
        """Return every transform plug whose incoming topology is session-owned."""
        return tuple(f"{joint}.{attr}" for attr in _TRANSFORM_ATTRS)

    def _set_locks(self, snapshot: RestPoseJointSnapshot, value: Optional[bool]) -> None:
        for attr in _TRANSFORM_ATTRS:
            lock = snapshot.locks.get(attr, False) if value is None else value
            try:
                self.cmds.setAttr(f"{snapshot.joint}.{attr}", lock=lock)
            except Exception:
                logger.debug("Could not set lock for %s.%s", snapshot.joint, attr)

    def _vector_value(self, plug: str) -> tuple[float, float, float]:
        value = self.cmds.getAttr(plug)
        if isinstance(value, (list, tuple)) and value and isinstance(value[0], (list, tuple)):
            value = value[0]
        return tuple(float(component) for component in value[:3])

    def _is_connected(self, edge: RestPoseEdge) -> bool:
        try:
            if self.cmds.isConnected(edge.source, edge.destination):
                return True
        except Exception:
            pass
        sources = self.cmds.listConnections(
            edge.destination,
            source=True,
            destination=False,
            plugs=True,
        ) or []
        expected = edge.source.rsplit("|", 1)[-1]
        return any(str(source).rsplit("|", 1)[-1] == expected for source in sources)

    def _all_edges_connected(self, session: RestPoseSession) -> bool:
        edges = [edge for snapshot in session.joints for edge in snapshot.edges]
        return all(self._is_connected(edge) for edge in edges)

    def _incoming_session_edges(self, session: RestPoseSession) -> list[RestPoseEdge]:
        """Return connections created after the session isolated its snapshot."""
        edges = []
        for snapshot in session.joints:
            for destination in self._transform_destinations(snapshot.joint):
                for source in self.cmds.listConnections(
                    destination,
                    source=True,
                    destination=False,
                    plugs=True,
                ) or []:
                    edges.append(RestPoseEdge(str(source), destination))
        return edges

    def _matches_motion_state(self, session: RestPoseSession) -> bool:
        """Whether Maya currently reflects the captured pre-session motion state."""
        if not self._all_edges_connected(session):
            return False
        for snapshot in session.joints:
            if not self.cmds.objExists(snapshot.joint):
                return False
            for plug, expected in (
                (f"{snapshot.joint}.translate", snapshot.translate),
                (f"{snapshot.joint}.rotate", snapshot.rotate),
            ):
                actual = self._vector_value(plug)
                if any(
                    abs(value - target) > 1e-6
                    for value, target in zip(actual, expected)
                ):
                    return False
        return True

    def _notify(self, result: RestPoseResult) -> None:
        for callback in tuple(self._listeners):
            try:
                callback(result)
            except Exception:
                logger.error("Rest Pose listener failed", exc_info=True)

    @contextmanager
    def _undo_chunk(self, name: str):
        opened = False
        try:
            self.cmds.undoInfo(openChunk=True, chunkName=name)
            opened = True
        except Exception:
            pass
        try:
            yield
        finally:
            if opened:
                self.cmds.undoInfo(closeChunk=True)


_MANAGER: Optional[RestPoseManager] = None


def get_rest_pose_manager() -> RestPoseManager:
    """Return the shared scene-wide Rest Pose manager."""
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = RestPoseManager()
        _MANAGER.install_scene_callbacks()
    return _MANAGER
