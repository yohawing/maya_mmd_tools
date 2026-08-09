"""One-shot Reset Pose transaction for Animator controls and MMD joints.

The action writes rest values at the current frame. Static channels receive
direct values, while directly connected animation curves receive keys. Maya
Undo is the only way to return to the previous pose; this module intentionally
owns no temporary display mode or second-click restoration state.
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
_DIRECT_ANIM_CURVE_TYPES = frozenset(
    {"animCurveTA", "animCurveTL", "animCurveTT", "animCurveTU"}
)


class ResetPoseTransactionError(RuntimeError):
    """Raised when Reset Pose cannot be applied or rolled back exactly."""


@dataclass(frozen=True)
class _ChannelSnapshot:
    plug: str
    value: Any
    writer: str | None
    locked: bool
    writable: bool


class ResetPoseTransaction:
    """Apply current-frame rest values to validated model-owned transforms."""

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
        self._snapshots: tuple[_ChannelSnapshot, ...] = ()
        self._applied: list[_ChannelSnapshot] = []

    def apply(self) -> int:
        """Reset static values or set rest keys at the current Maya frame."""

        cmds = self._cmds()
        self._assert_model_uuid(cmds)
        if not self.targets:
            return 0
        self._snapshots = self._capture_channels(cmds)
        opened = self._open_undo("Animator Reset Pose")
        if not opened and any(snapshot.writer for snapshot in self._snapshots):
            raise ResetPoseTransactionError(
                "Reset Pose requires Maya Undo for animated channels"
            )
        self._applied = []
        try:
            applied_targets = set()
            current_time = float(cmds.currentTime(query=True))
            for snapshot in self._snapshots:
                if not snapshot.writable:
                    continue
                value = self._rest_value(snapshot)
                self._applied.append(snapshot)
                if snapshot.writer:
                    curve = snapshot.writer.rsplit(".", 1)[0]
                    cmds.setKeyframe(
                        curve,
                        time=(current_time, current_time),
                        value=float(value),
                    )
                else:
                    self._set_static(cmds, snapshot, value)
                applied_targets.add(snapshot.plug.rsplit(".", 1)[0])
            return len(applied_targets)
        except Exception as exc:
            try:
                if opened:
                    self._close_undo(opened)
                    opened = False
                    cmds.undo()
                else:
                    self._restore_static(cmds)
            except Exception as rollback_error:
                raise ResetPoseTransactionError(
                    f"Reset Pose failed and rollback was incomplete: {rollback_error}"
                ) from exc
            raise ResetPoseTransactionError(str(exc)) from exc
        finally:
            self._close_undo(opened)

    def _capture_channels(self, cmds) -> tuple[_ChannelSnapshot, ...]:
        snapshots = []
        for target in self.targets:
            paths = cmds.ls(target, long=True) or []
            if len(paths) != 1:
                raise ResetPoseTransactionError(f"ambiguous Reset Pose target: {target}")
            resolved = str(paths[0])
            for channel in _CHANNELS:
                plug = f"{resolved}.{channel}"
                try:
                    value = cmds.getAttr(plug)
                    incoming = tuple(
                        str(source)
                        for source in (
                            cmds.listConnections(
                                plug,
                                source=True,
                                destination=False,
                                plugs=True,
                            )
                            or []
                        )
                    )
                except Exception as exc:
                    raise ResetPoseTransactionError(
                        f"Reset Pose channel is unavailable: {plug}"
                    ) from exc
                if len(incoming) > 1:
                    raise ResetPoseTransactionError(
                        f"multiple Reset Pose writers on {plug}"
                    )
                writer = incoming[0] if incoming else None
                writable = (
                    self._is_direct_anim_curve(cmds, writer)
                    if writer
                    else self._is_channel_settable(cmds, plug)
                )
                try:
                    locked = bool(cmds.getAttr(plug, lock=True))
                except Exception:
                    locked = False
                snapshots.append(
                    _ChannelSnapshot(plug, value, writer, locked, writable)
                )
        if not snapshots:
            raise ResetPoseTransactionError("Reset Pose channels are unavailable")
        return tuple(snapshots)

    @staticmethod
    def _is_direct_anim_curve(cmds, writer: str) -> bool:
        try:
            node = writer.rsplit(".", 1)[0]
            return str(cmds.nodeType(node)) in _DIRECT_ANIM_CURVE_TYPES
        except Exception:
            return False

    @staticmethod
    def _is_channel_settable(cmds, plug: str) -> bool:
        try:
            return bool(cmds.getAttr(plug, settable=True))
        except Exception:
            return True

    @staticmethod
    def _set_static(cmds, snapshot: _ChannelSnapshot, value: float) -> None:
        if snapshot.locked:
            cmds.setAttr(snapshot.plug, lock=False)
        try:
            cmds.setAttr(snapshot.plug, float(value))
        finally:
            if snapshot.locked:
                cmds.setAttr(snapshot.plug, lock=True)

    def _restore_static(self, cmds) -> None:
        for snapshot in reversed(self._applied):
            if snapshot.writer:
                raise ResetPoseTransactionError(
                    f"animated Reset Pose rollback requires Maya Undo: {snapshot.plug}"
                )
            self._set_static(cmds, snapshot, snapshot.value)

    def _rest_value(self, snapshot: _ChannelSnapshot) -> float:
        plug = snapshot.plug
        attribute = plug.rsplit(".", 1)[-1]
        if attribute.startswith("translate"):
            node = plug.rsplit(".", 1)[0]
            translation = self.bind_translations.get(node)
            if translation is None:
                translation = self.bind_translations.get(node.rsplit("|", 1)[-1])
            if translation is not None:
                return float(translation["XYZ".index(attribute[-1])])
            return float(snapshot.value)
        return 0.0

    def _assert_model_uuid(self, cmds) -> None:
        roots = cmds.ls(self.model_root, uuid=True) or []
        if len(roots) != 1 or str(roots[0]) != self.model_uuid:
            raise ResetPoseTransactionError("MMD model UUID changed during Reset Pose")
        root_paths = cmds.ls(self.model_root, long=True) or []
        if len(root_paths) != 1:
            raise ResetPoseTransactionError("MMD model root is ambiguous during Reset Pose")
        scope_paths = []
        for scope in self.scope_roots:
            paths = cmds.ls(scope, long=True) or []
            if len(paths) != 1:
                raise ResetPoseTransactionError(
                    f"Reset Pose scope is ambiguous: {scope}"
                )
            scope_paths.append(str(paths[0]))
        root_path = str(root_paths[0])
        if root_path not in scope_paths:
            scope_paths.insert(0, root_path)
        for target in self.targets:
            paths = cmds.ls(target, long=True) or []
            if len(paths) != 1:
                raise ResetPoseTransactionError(f"ambiguous Reset Pose target: {target}")
            path = str(paths[0])
            if not any(
                path == scope or path.startswith(scope + "|")
                for scope in scope_paths
            ):
                raise ResetPoseTransactionError(
                    f"Reset Pose target is outside model UUID: {target}"
                )

    def _cmds(self):
        cmds = getattr(self.adapter, "_cmds", None)
        if cmds is None:
            raise ResetPoseTransactionError("Reset Pose requires a Maya command adapter")
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
