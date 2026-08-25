"""Keyless, one-shot Reset Pose transaction.

Reset Pose changes only the value evaluated at the current Maya time.  It does
not author animation.  Connected animation therefore resumes on the next time
change, while static channels retain the assigned rest value.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping


_CHANNELS = (
    "translateX",
    "translateY",
    "translateZ",
    "rotateX",
    "rotateY",
    "rotateZ",
)
_TIME_ANIM_CURVE_TYPES = frozenset(
    {"animCurveTA", "animCurveTL", "animCurveTT", "animCurveTU"}
)


class ResetPoseTransactionError(RuntimeError):
    """Raised when Reset Pose cannot be applied or rolled back exactly."""


@dataclass(frozen=True)
class _ChannelSnapshot:
    target: str
    plug: str
    value: Any
    incoming: tuple[str, ...]
    locked: bool


class ResetPoseTransaction:
    """Temporarily apply rest values without editing animation keys."""

    def __init__(
        self,
        adapter,
        *,
        model_root: str,
        model_uuid: str,
        targets: list[str],
        bind_translations: Mapping[str, tuple[float, float, float]] | None = None,
        authored_plugs_by_target: Mapping[str, tuple[str, ...]] | None = None,
        scope_roots: tuple[str, ...] | None = None,
    ):
        self.adapter = adapter
        self.model_root = str(model_root)
        self.model_uuid = str(model_uuid)
        self.targets = tuple(dict.fromkeys(str(target) for target in targets))
        self.bind_translations = dict(bind_translations or {})
        self.authored_plugs_by_target = (
            None
            if authored_plugs_by_target is None
            else {
                str(target): tuple(str(plug) for plug in plugs)
                for target, plugs in authored_plugs_by_target.items()
            }
        )
        self.scope_roots = tuple(
            dict.fromkeys(str(scope) for scope in (scope_roots or (model_root,)))
        )
        self._snapshots: tuple[_ChannelSnapshot, ...] = ()
        self._applied: list[_ChannelSnapshot] = []

    def apply(self) -> int:
        """Apply a keyless current-evaluation reset and return changed targets."""

        cmds = self._cmds()
        self._assert_model_uuid(cmds)
        if not self.targets:
            return 0
        snapshots = self._capture_channels(cmds)
        self._snapshots = snapshots
        reset_values = tuple(
            (snapshot, self._rest_value(snapshot)) for snapshot in snapshots
        )
        changed = tuple(
            (snapshot, value)
            for snapshot, value in reset_values
            if self._channel_value_changed(snapshot.value, value)
        )
        changed_targets = {snapshot.target for snapshot, _value in changed}
        if not changed:
            self._assert_results(cmds, reset_values)
            return 0
        for snapshot, _value in changed:
            if snapshot.incoming:
                self._assert_transient_writer(cmds, snapshot.plug, snapshot.incoming[0])

        transient = tuple(item for item in changed if item[0].incoming)
        static = tuple(item for item in changed if not item[0].incoming)
        opened = False
        self._applied = []
        try:
            self._write_transient_without_undo(cmds, transient)
            opened = bool(static) and self._open_undo("Animator Reset Pose")
            for snapshot, value in static:
                self._assert_topology(cmds, snapshot)
                self._applied.append(snapshot)
                self._set_value(cmds, snapshot, value)
            self._assert_results(cmds, reset_values)
        except Exception as exc:
            try:
                static_restored_by_undo = False
                if opened:
                    self._close_undo(True)
                    opened = False
                    cmds.undo()
                    static_restored_by_undo = True
                self._restore_values(
                    cmds,
                    include_static=not static_restored_by_undo,
                )
                self._dirty_all(cmds)
                self._assert_rollback(cmds)
            except Exception as rollback_error:
                raise ResetPoseTransactionError(
                    f"Reset Pose failed and rollback was incomplete: {rollback_error}"
                ) from exc
            raise ResetPoseTransactionError(str(exc)) from exc
        if opened:
            try:
                self._close_undo(True)
            except Exception as exc:
                try:
                    self._restore_values(cmds)
                    self._dirty_all(cmds)
                    self._assert_rollback(cmds)
                except Exception as rollback_error:
                    raise ResetPoseTransactionError(
                        "Reset Pose Undo close failed and rollback was incomplete: "
                        f"{rollback_error}"
                    ) from exc
                raise ResetPoseTransactionError(f"Reset Pose Undo close failed: {exc}") from exc
        return len(changed_targets)

    def _write_transient_without_undo(self, cmds, writes) -> None:
        self._set_transient_values_without_undo(
            cmds,
            writes,
            track=True,
            validate_topology=True,
        )

    def _set_transient_values_without_undo(
        self,
        cmds,
        writes,
        *,
        track: bool,
        validate_topology: bool,
    ) -> None:
        if not writes:
            return
        try:
            previous_state = bool(self.adapter.undo_info(query=True, state=True))
        except Exception as exc:
            raise ResetPoseTransactionError(
                f"Reset Pose Undo state is unavailable: {exc}"
            ) from exc
        try:
            if previous_state:
                self.adapter.undo_info(stateWithoutFlush=False)
            for snapshot, value in writes:
                if validate_topology:
                    self._assert_topology(cmds, snapshot)
                if track:
                    self._applied.append(snapshot)
                self._set_value(cmds, snapshot, value)
        finally:
            if previous_state:
                try:
                    self.adapter.undo_info(stateWithoutFlush=True)
                except Exception as exc:
                    try:
                        self.adapter.undo_info(stateWithoutFlush=True)
                    except Exception as retry_error:
                        raise ResetPoseTransactionError(
                            "Reset Pose Undo state restoration failed: "
                            f"{retry_error}"
                        ) from exc
                    raise ResetPoseTransactionError(
                        f"Reset Pose Undo state restoration failed: {exc}"
                    ) from exc
            try:
                restored_state = bool(
                    self.adapter.undo_info(query=True, state=True)
                )
            except Exception as exc:
                raise ResetPoseTransactionError(
                    f"Reset Pose Undo state restoration is unavailable: {exc}"
                ) from exc
            if restored_state != previous_state:
                raise ResetPoseTransactionError(
                    "Reset Pose Undo state restoration failed"
                )

    def _capture_channels(self, cmds) -> tuple[_ChannelSnapshot, ...]:
        snapshots = []
        for target in self.targets:
            paths = cmds.ls(target, long=True) or []
            if len(paths) != 1:
                raise ResetPoseTransactionError(f"ambiguous Reset Pose target: {target}")
            resolved = str(paths[0])
            for plug in self._target_plugs(target, resolved):
                try:
                    value = cmds.getAttr(plug)
                    incoming = self._incoming(cmds, plug)
                except Exception as exc:
                    raise ResetPoseTransactionError(
                        f"Reset Pose channel is unavailable: {plug}"
                    ) from exc
                if len(incoming) > 1:
                    raise ResetPoseTransactionError(
                        f"multiple Reset Pose writers on {plug}"
                    )
                try:
                    locked = bool(cmds.getAttr(plug, lock=True))
                except Exception:
                    locked = False
                snapshots.append(_ChannelSnapshot(resolved, plug, value, incoming, locked))
        if not snapshots:
            raise ResetPoseTransactionError("Reset Pose channels are unavailable")
        return tuple(snapshots)

    @staticmethod
    def _assert_transient_writer(cmds, plug: str, writer: str) -> None:
        node = writer.rsplit(".", 1)[0]
        try:
            writer_type = str(cmds.nodeType(node))
        except Exception as exc:
            raise ResetPoseTransactionError(
                f"Reset Pose writer type is unavailable: {writer}"
            ) from exc
        if writer_type in _TIME_ANIM_CURVE_TYPES:
            return
        if writer_type.startswith("animCurve"):
            raise ResetPoseTransactionError(
                f"unsupported Reset Pose driven-key curve: {writer_type} ({writer})"
            )
        if writer_type.startswith("animBlendNode"):
            try:
                history = cmds.listHistory(node, pruneDagObjects=True) or []
                history_types = tuple(
                    (str(item), str(cmds.nodeType(item))) for item in history
                )
            except Exception as exc:
                raise ResetPoseTransactionError(
                    f"Reset Pose animation history is unavailable: {writer}"
                ) from exc
            curve_types = tuple(
                curve_type
                for _item, curve_type in history_types
                if curve_type.startswith("animCurve")
            )
            if curve_types and all(
                curve_type in _TIME_ANIM_CURVE_TYPES for curve_type in curve_types
            ):
                return
            raise ResetPoseTransactionError(
                f"unsupported static or driven-key animation blend: {writer}"
            )
        if writer_type == "pairBlend":
            raise ResetPoseTransactionError(
                f"unsupported Reset Pose pairBlend writer: {writer}"
            )
        raise ResetPoseTransactionError(
            f"unsupported Reset Pose writer on {plug}: {writer_type} ({writer})"
        )

    def _target_plugs(self, target: str, resolved: str) -> tuple[str, ...]:
        if self.authored_plugs_by_target is None:
            return tuple(f"{resolved}.{channel}" for channel in _CHANNELS)
        authored = None
        for candidate in (resolved, target, resolved.rsplit("|", 1)[-1]):
            if candidate in self.authored_plugs_by_target:
                authored = self.authored_plugs_by_target[candidate]
                break
        if not authored:
            raise ResetPoseTransactionError(
                f"Reset Pose authored inputs are unavailable: {resolved}"
            )
        expanded = []
        for plug in authored:
            expanded.extend(self._expand_authored_plug(plug))
        unique = tuple(dict.fromkeys(expanded))
        if not unique:
            raise ResetPoseTransactionError(
                f"Reset Pose authored inputs are unavailable: {resolved}"
            )
        return unique

    @staticmethod
    def _expand_authored_plug(plug: str) -> tuple[str, ...]:
        attribute = plug.rsplit(".", 1)[-1]
        if attribute in {"translate", "rotate", "baseTranslate", "baseRotate"}:
            return tuple(f"{plug}{axis}" for axis in "XYZ")
        if attribute.startswith("inputRotate[") and attribute.endswith("]"):
            index = attribute[len("inputRotate[") : -1]
            if index.isdigit():
                return tuple(f"{plug}.inputRotateElement{axis}" for axis in "XYZ")
        scalar_prefixes = ("translate", "rotate", "baseTranslate", "baseRotate")
        if attribute[-1:] in "XYZ" and attribute.startswith(scalar_prefixes):
            return (plug,)
        if attribute.startswith("inputRotateElement") and attribute[-1:] in "XYZ":
            return (plug,)
        raise ResetPoseTransactionError(
            f"unsupported Reset Pose authored plug: {plug}"
        )

    @staticmethod
    def _incoming(cmds, plug: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                str(source)
                for source in (
                    cmds.listConnections(
                        plug, source=True, destination=False, plugs=True
                    )
                    or []
                )
            )
        )

    @staticmethod
    def _set_value(cmds, snapshot: _ChannelSnapshot, value: float) -> None:
        if snapshot.locked:
            cmds.setAttr(snapshot.plug, lock=False)
        try:
            cmds.setAttr(snapshot.plug, float(value))
        finally:
            if snapshot.locked:
                cmds.setAttr(snapshot.plug, lock=True)

    def _assert_results(self, cmds, reset_values) -> None:
        for snapshot, value in reset_values:
            self._assert_topology(cmds, snapshot)
            if bool(cmds.getAttr(snapshot.plug, lock=True)) != snapshot.locked:
                raise ResetPoseTransactionError(
                    f"Reset Pose channel lock changed: {snapshot.plug}"
                )
            try:
                current = float(cmds.getAttr(snapshot.plug))
                desired = float(value)
            except (TypeError, ValueError) as exc:
                raise ResetPoseTransactionError(
                    f"Reset Pose result is not scalar: {snapshot.plug}"
                ) from exc
            if not (
                math.isfinite(current)
                and math.isfinite(desired)
                and math.isclose(current, desired, rel_tol=0.0, abs_tol=1.0e-6)
            ):
                raise ResetPoseTransactionError(
                    f"Reset Pose result did not evaluate: {snapshot.plug}"
                )

    def _assert_topology(self, cmds, snapshot: _ChannelSnapshot) -> None:
        if self._incoming(cmds, snapshot.plug) != snapshot.incoming:
            raise ResetPoseTransactionError(
                f"Reset Pose writer topology changed: {snapshot.plug}"
            )

    def _restore_values(self, cmds, *, include_static: bool = True) -> None:
        transient = tuple(
            (snapshot, float(snapshot.value))
            for snapshot in reversed(self._applied)
            if snapshot.incoming
        )
        self._set_transient_values_without_undo(
            cmds,
            transient,
            track=False,
            validate_topology=False,
        )
        if include_static:
            for snapshot in reversed(self._applied):
                if not snapshot.incoming:
                    self._set_value(cmds, snapshot, float(snapshot.value))

    def _assert_rollback(self, cmds) -> None:
        for snapshot in self._applied:
            self._assert_topology(cmds, snapshot)
            current = float(cmds.getAttr(snapshot.plug))
            if self._channel_value_changed(current, float(snapshot.value)):
                raise ResetPoseTransactionError(
                    f"Reset Pose rollback value changed: {snapshot.plug}"
                )
            if bool(cmds.getAttr(snapshot.plug, lock=True)) != snapshot.locked:
                raise ResetPoseTransactionError(
                    f"Reset Pose rollback lock changed: {snapshot.plug}"
                )

    @staticmethod
    def _dirty_all(cmds) -> None:
        dirty = getattr(cmds, "dgdirty", None)
        if callable(dirty):
            dirty(allPlugs=True)

    def _rest_value(self, snapshot: _ChannelSnapshot) -> float:
        attribute = snapshot.plug.rsplit(".", 1)[-1]
        if attribute.startswith(("translate", "baseTranslate")):
            translation = self.bind_translations.get(snapshot.target)
            if translation is None:
                translation = self.bind_translations.get(
                    snapshot.target.rsplit("|", 1)[-1]
                )
            if translation is not None:
                return float(translation["XYZ".index(attribute[-1])])
            if attribute.startswith("baseTranslate") or self.authored_plugs_by_target is not None:
                raise ResetPoseTransactionError(
                    f"Reset Pose bind translation is unavailable: {snapshot.target}"
                )
            return float(snapshot.value)
        return 0.0

    @staticmethod
    def _channel_value_changed(before: Any, rest: float) -> bool:
        try:
            before_value = float(before)
            rest_value = float(rest)
        except (TypeError, ValueError):
            return True
        return not (
            math.isfinite(before_value)
            and math.isfinite(rest_value)
            and math.isclose(before_value, rest_value, rel_tol=0.0, abs_tol=1.0e-6)
        )

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
                raise ResetPoseTransactionError(f"Reset Pose scope is ambiguous: {scope}")
            scope_paths.append(str(paths[0]))
        root_path = str(root_paths[0])
        if root_path not in scope_paths:
            scope_paths.insert(0, root_path)
        for target in self.targets:
            paths = cmds.ls(target, long=True) or []
            if len(paths) != 1:
                raise ResetPoseTransactionError(f"ambiguous Reset Pose target: {target}")
            path = str(paths[0])
            if not any(path == scope or path.startswith(scope + "|") for scope in scope_paths):
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
            self.adapter.undo_info(closeChunk=True)
