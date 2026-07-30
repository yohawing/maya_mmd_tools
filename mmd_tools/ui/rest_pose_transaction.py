"""Non-destructive, reversible Rest Pose transaction for Animator pickers.

The helper intentionally owns no process-wide mode.  One instance represents
one model UUID and one explicit selection snapshot; applying/restoring it is
therefore deterministic and fail-closed when a scene is replaced.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


_CHANNELS = (
    "translateX",
    "translateY",
    "translateZ",
    "rotateX",
    "rotateY",
    "rotateZ",
)


class RestPoseTransactionError(RuntimeError):
    """Raised when a Rest Pose snapshot or rollback cannot be proven exact."""


@dataclass(frozen=True)
class _ChannelSnapshot:
    plug: str
    value: Any
    incoming: tuple[str, ...]
    curves: tuple[Mapping[str, Any], ...]
    locked: bool
    rest_writable: bool


class RestPoseTransaction:
    """Snapshot and temporarily rest selected transform channels."""

    def __init__(
        self,
        adapter,
        *,
        model_root: str,
        model_uuid: str,
        targets: list[str],
        bind_translations: Mapping[str, tuple[float, float, float]] | None = None,
        scope_roots: tuple[str, ...] | None = None,
    ):
        self.adapter = adapter
        self.model_root = str(model_root)
        self.model_uuid = str(model_uuid)
        self.targets = tuple(dict.fromkeys(str(target) for target in targets))
        self.bind_translations = dict(bind_translations or {})
        self.scope_roots = tuple(
            dict.fromkeys(str(scope) for scope in (scope_roots or (model_root,)))
        )
        self._channels: tuple[_ChannelSnapshot, ...] = ()
        self._selection: tuple[str, ...] = ()
        self._current_time: Any = None
        self._restored = False
        # Only channels actually mutated by ``apply`` participate in
        # rollback. This lets a late failure restore the completed prefix
        # without touching channels that were never written.
        self._applied_plugs: set[str] = set()

    @property
    def channels(self) -> tuple[_ChannelSnapshot, ...]:
        """Expose immutable channel snapshots for diagnostics/tests."""

        return self._channels

    def apply(self) -> int:
        """Snapshot and apply rest values without deleting keys or curves."""

        cmds = self._cmds()
        self._assert_model_uuid(cmds)
        if not self.targets:
            return 0
        if self._channels and not self._restored:
            raise RestPoseTransactionError("Rest Pose transaction is already active")
        self._selection = tuple(self._ls_selection())
        try:
            self._current_time = self.adapter.current_time()
        except Exception:
            self._current_time = None
        self._channels = self._capture_channels(cmds)
        self._applied_plugs = set()
        opened = self._open_undo("Animator Rest Pose")
        try:
            applied_targets = set()
            for snapshot in self._channels:
                # Procedural outputs (pairBlend, constraints, MMD append/
                # twist nodes, and similar) are evaluated from their source
                # bones.  Disconnecting them would destroy that contract and
                # can fail on locked destination plugs.  Rest only owns direct
                # channels and animCurve-owned channels; procedural outputs
                # remain connected and are restored by evaluation instead.
                if not snapshot.rest_writable:
                    continue
                # Record before the first destructive operation so a failed
                # value write after disconnect still rolls back this plug.
                self._applied_plugs.add(snapshot.plug)
                for source in snapshot.incoming:
                    cmds.disconnectAttr(source, snapshot.plug)
                if snapshot.locked:
                    cmds.setAttr(snapshot.plug, lock=False)
                value = self._rest_value(snapshot.plug)
                cmds.setAttr(snapshot.plug, value)
                if snapshot.locked:
                    cmds.setAttr(snapshot.plug, lock=True)
                applied_targets.add(snapshot.plug.rsplit(".", 1)[0])
            self._restored = False
            return len(applied_targets)
        except Exception as exc:
            try:
                self._restore_impl(cmds)
                self._applied_plugs.clear()
                self._restored = True
            except Exception as rollback_error:
                raise RestPoseTransactionError(
                    f"Rest Pose failed and rollback was incomplete: {rollback_error}"
                ) from exc
            raise RestPoseTransactionError(str(exc)) from exc
        finally:
            self._close_undo(opened)

    def restore(self) -> None:
        """Restore values, incoming connections, frame, and selection exactly."""

        if not self._channels:
            return
        if self._restored:
            return
        cmds = self._cmds()
        self._assert_model_uuid(cmds)
        opened = self._open_undo("Restore Animator Rest Pose")
        try:
            self._restore_impl(cmds)
            self._restored = True
            self._applied_plugs.clear()
        finally:
            self._close_undo(opened)

    def _capture_channels(self, cmds) -> tuple[_ChannelSnapshot, ...]:
        snapshots = []
        for target in self.targets:
            paths = cmds.ls(target, long=True) or []
            if len(paths) != 1:
                raise RestPoseTransactionError(f"ambiguous Rest Pose target: {target}")
            resolved = str(paths[0])
            for channel in _CHANNELS:
                plug = f"{resolved}.{channel}"
                try:
                    value = cmds.getAttr(plug)
                except Exception as exc:
                    raise RestPoseTransactionError(
                        f"Rest Pose channel is unavailable: {plug}"
                    ) from exc
                incoming = tuple(
                    str(source)
                    for source in (cmds.listConnections(plug, source=True, destination=False, plugs=True) or [])
                )
                if len(incoming) > 1:
                    raise RestPoseTransactionError(
                        f"multiple Rest Pose writers on {plug}"
                    )
                curves = tuple(self._curve_snapshot(cmds, source) for source in incoming)
                rest_writable = (
                    self._is_channel_settable(cmds, plug)
                    if not incoming
                    else self._is_direct_anim_curve(cmds, incoming[0])
                )
                try:
                    locked = bool(cmds.getAttr(plug, lock=True))
                except Exception:
                    locked = False
                snapshots.append(
                    _ChannelSnapshot(
                        plug,
                        value,
                        incoming,
                        curves,
                        locked,
                        rest_writable,
                    )
                )
        if not snapshots:
            raise RestPoseTransactionError("selected Rest Pose channels are unavailable")
        return tuple(snapshots)

    @staticmethod
    def _is_direct_anim_curve(cmds, source: str) -> bool:
        """Return whether one incoming edge is an authored animation curve."""

        try:
            node = str(source).rsplit(".", 1)[0]
            return str(cmds.nodeType(node)).startswith("animCurve")
        except Exception:
            return False

    @staticmethod
    def _is_channel_settable(cmds, plug: str) -> bool:
        """Return whether Maya allows a direct value write to this channel."""

        try:
            return bool(cmds.getAttr(plug, settable=True))
        except Exception:
            # Lightweight adapters do not expose Maya's settable query; their
            # lock/connection snapshots remain the authoritative contract.
            return True

    @staticmethod
    def _curve_snapshot(cmds, source: str) -> Mapping[str, Any]:
        """Record curve payload without modifying the animation graph."""

        try:
            node = str(source).rsplit(".", 1)[0]
            node_type = str(cmds.nodeType(node))
            if not node_type.startswith("animCurve"):
                return {"node": source, "type": node_type}
            return {
                "node": source,
                "type": node_type,
                "times": tuple(cmds.keyframe(source, query=True, timeChange=True) or ()),
                "values": tuple(cmds.keyframe(source, query=True, valueChange=True) or ()),
            }
        except Exception:
            return {"node": source, "type": "unknown"}

    def _restore_impl(self, cmds) -> None:
        self._assert_model_uuid(cmds)
        active = tuple(
            snapshot
            for snapshot in self._channels
            if snapshot.rest_writable and snapshot.plug in self._applied_plugs
        )

        # Validate every mutated plug before making any rollback mutation. A
        # foreign writer added/replaced after apply must remain untouched.
        drift = []
        for snapshot in active:
            current = tuple(
                str(source)
                for source in (
                    cmds.listConnections(
                        snapshot.plug,
                        source=True,
                        destination=False,
                        plugs=True,
                    )
                    or []
                )
            )
            if current:
                drift.append(
                    f"{snapshot.plug}: expected no incoming writer, found {current}"
                )
        if drift:
            raise RestPoseTransactionError(
                "Rest Pose topology drift; rollback refused: " + "; ".join(drift)
            )

        for snapshot in active:
            current = tuple(
                str(source)
                for source in (cmds.listConnections(snapshot.plug, source=True, destination=False, plugs=True) or [])
            )
            for source in current:
                if source not in snapshot.incoming:
                    cmds.disconnectAttr(source, snapshot.plug)
            if snapshot.locked:
                cmds.setAttr(snapshot.plug, lock=False)
            try:
                if snapshot.incoming:
                    for source in snapshot.incoming:
                        if source not in current:
                            cmds.connectAttr(source, snapshot.plug, force=False)
                else:
                    cmds.setAttr(snapshot.plug, snapshot.value)
            finally:
                if snapshot.locked:
                    cmds.setAttr(snapshot.plug, lock=True)
        if self._current_time is not None:
            self.adapter._cmds.currentTime(self._current_time, edit=True)
        self.adapter.select(list(self._selection), replace=True)

    def _rest_value(self, plug: str) -> float:
        attribute = plug.rsplit(".", 1)[-1]
        if attribute.startswith("translate"):
            node = plug.rsplit(".", 1)[0]
            translation = self.bind_translations.get(node)
            if translation is None:
                translation = self.bind_translations.get(
                    node.rsplit("|", 1)[-1]
                )
            if translation is not None:
                return float(translation["XYZ".index(attribute[-1])])
        return 0.0

    def _assert_model_uuid(self, cmds) -> None:
        roots = cmds.ls(self.model_root, uuid=True) or []
        if len(roots) != 1 or str(roots[0]) != self.model_uuid:
            raise RestPoseTransactionError("MMD model UUID changed during Rest Pose")
        root_paths = cmds.ls(self.model_root, long=True) or []
        if len(root_paths) != 1:
            raise RestPoseTransactionError("MMD model root is ambiguous during Rest Pose")
        root_path = str(root_paths[0])
        scope_paths = []
        for scope in self.scope_roots:
            paths = cmds.ls(scope, long=True) or []
            if len(paths) != 1:
                raise RestPoseTransactionError(
                    f"Rest Pose scope is ambiguous: {scope}"
                )
            scope_paths.append(str(paths[0]))
        if root_path not in scope_paths:
            scope_paths.insert(0, root_path)
        for target in self.targets:
            target_paths = cmds.ls(target, long=True) or []
            if len(target_paths) != 1:
                raise RestPoseTransactionError(f"ambiguous Rest Pose target: {target}")
            target_path = str(target_paths[0])
            if not any(
                target_path == scope or target_path.startswith(scope + "|")
                for scope in scope_paths
            ):
                raise RestPoseTransactionError(
                    f"Rest Pose target is outside model UUID: {target}"
                )

    def _ls_selection(self) -> list[str]:
        try:
            return list(self.adapter.ls(selection=True) or [])
        except Exception:
            return []

    def _cmds(self):
        cmds = getattr(self.adapter, "_cmds", None)
        if cmds is None:
            raise RestPoseTransactionError("Rest Pose requires a Maya command adapter")
        return cmds

    def _open_undo(self, name: str) -> bool:
        try:
            self.adapter.undo_info(openChunk=True, chunkName=name)
            return True
        except Exception:
            return False

    def _close_undo(self, opened: bool) -> None:
        if opened:
            try:
                self.adapter.undo_info(closeChunk=True)
            except Exception:
                pass
