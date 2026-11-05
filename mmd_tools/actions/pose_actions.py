"""Action boundaries for pose manipulation in the Animator Toolset."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import TYPE_CHECKING
from uuid import uuid4

from ..core.pose_authoring import (
    PoseAuthoringSnapshot,
    resolve_pose_routes,
    selected_pose_layer,
    validate_pose_input,
)

if TYPE_CHECKING:
    from ..adapters.maya_cmds_adapter import MayaCmdsAdapter


@dataclass(frozen=True)
class PoseTransform:
    """Local transform plus the owned pre-solver inputs used for pasting."""

    translation: tuple[float, float, float]
    rotation: tuple[float, float, float]
    authoring: PoseAuthoringSnapshot | None = None


# -- Copy Pose --------------------------------------------------------


@dataclass
class CopyPoseRequest:
    joints: list[str]
    model_root: str | None = None


@dataclass
class CopyPoseResult:
    succeeded: bool = False
    pose: dict[str, PoseTransform] = field(default_factory=dict)
    error: Exception | None = None


class CopyPoseAction:
    """Snapshot local transforms of selected joints (read-only)."""

    def __init__(self, maya_adapter: MayaCmdsAdapter):
        self._adapter = maya_adapter

    def execute(self, request: CopyPoseRequest) -> CopyPoseResult:
        if not request.joints:
            return CopyPoseResult(succeeded=True)
        try:
            cmds = self._adapter._cmds
            routes = resolve_pose_routes(cmds, request.joints, request.model_root)
            pose: dict[str, PoseTransform] = {}
            for jnt in request.joints:
                t = self._adapter.xform(jnt, query=True, translation=True)
                r = self._adapter.xform(jnt, query=True, rotation=True)
                route = routes[jnt]
                values = tuple(float(cmds.getAttr(plug)) for plug in route.plugs)
                if not all(math.isfinite(value) for value in values):
                    raise RuntimeError(f"Pose contains non-finite inputs: {jnt}")
                pose[jnt] = PoseTransform(
                    translation=(t[0], t[1], t[2]),
                    rotation=(r[0], r[1], r[2]),
                    authoring=PoseAuthoringSnapshot(route, values),
                )
            return CopyPoseResult(succeeded=True, pose=pose)
        except Exception as exc:
            return CopyPoseResult(error=exc)


# -- Paste Pose -------------------------------------------------------


@dataclass
class PastePoseRequest:
    pose: dict[str, PoseTransform]
    model_root: str | None = None


@dataclass
class PastePoseResult:
    succeeded: bool = False
    applied_count: int = 0
    error: Exception | None = None


class PastePoseAction:
    """Key owned authoring inputs on one layer as one undoable operation."""

    def __init__(self, maya_adapter: MayaCmdsAdapter):
        self._adapter = maya_adapter

    def execute(self, request: PastePoseRequest) -> PastePoseResult:
        if not request.pose:
            return PastePoseResult(succeeded=True)
        cmds = self._adapter._cmds
        chunk = "Paste Pose " + uuid4().hex
        opened = False
        try:
            routes = resolve_pose_routes(cmds, list(request.pose), request.model_root)
            writes = {}
            for jnt, xf in request.pose.items():
                route = routes[jnt]
                if xf.authoring is not None:
                    if route != xf.authoring.route:
                        raise RuntimeError(f"Pose ownership or input route changed since Copy: {jnt}")
                    values = xf.authoring.values
                elif route.owner == "DIRECT":
                    # Preserve the public plain-transform request format.
                    values = (*xf.translation, *xf.rotation)
                else:
                    raise RuntimeError(f"Copy the owned pose again before pasting: {jnt}")
                if len(values) != len(route.plugs) or not all(math.isfinite(value) for value in values):
                    raise RuntimeError(f"Pose inputs are invalid: {jnt}")
                for plug, value in zip(route.plugs, values):
                    validate_pose_input(cmds, plug)
                    if plug in writes and writes[plug] != value:
                        raise RuntimeError(f"Pose targets disagree on a shared input: {plug}")
                    writes[plug] = value
            layer = selected_pose_layer(cmds)
            base = cmds.animLayer(query=True, root=True) if layer else None
            if layer:
                if cmds.animLayer(layer, query=True, lock=True):
                    raise RuntimeError(f"Paste Pose layer is locked: {layer}")
                if cmds.animLayer(layer, query=True, mute=True) or cmds.animLayer(layer, query=True, weight=True) <= 0:
                    raise RuntimeError(f"Paste Pose layer is muted or has zero weight: {layer}")
            if not cmds.undoInfo(query=True, state=True):
                raise RuntimeError("Paste Pose requires Undo to be enabled")
            frame = cmds.currentTime(query=True)
            cmds.undoInfo(openChunk=True, chunkName=chunk)
            opened = True
            if layer and layer != base:
                for plug in writes:
                    cmds.animLayer(layer, edit=True, attribute=plug)
            for plug, value in writes.items():
                kwargs = {"time": frame, "value": value}
                if layer:
                    kwargs["animLayer"] = layer
                # With no animLayer nodes, ordinary Maya keying is the Base route.
                if not cmds.setKeyframe(plug, **kwargs):
                    raise RuntimeError(f"Paste Pose did not create a key: {plug}")
            # A setKeyframe success can still be hidden by a higher Override.
            cmds.dgdirty(list(writes))
            cmds.currentTime(frame, edit=True, update=True)
            for plug, value in writes.items():
                if not math.isclose(float(cmds.getAttr(plug)), value, rel_tol=1e-6, abs_tol=1e-5):
                    raise RuntimeError(f"Paste Pose could not reproduce the copied input on this layer: {plug}")
            cmds.undoInfo(closeChunk=True)
            opened = False
            return PastePoseResult(succeeded=True, applied_count=len(request.pose))
        except Exception as exc:
            if opened:
                cmds.undoInfo(closeChunk=True)
                opened = False
                # Empty failed chunks do not enter Maya's queue. Never undo
                # the user's preceding action in that case.
                if cmds.undoInfo(query=True, undoName=True) == chunk:
                    try:
                        cmds.undo()
                    except Exception as rollback_error:
                        return PastePoseResult(error=RuntimeError(f"{exc}; Paste Pose rollback failed: {rollback_error}"))
            return PastePoseResult(error=exc)


# -- Reset Pose -------------------------------------------------------


@dataclass
class ResetPoseRequest:
    joints: list[str]
    bind_translations: dict[str, tuple[float, float, float]] = field(default_factory=dict)


@dataclass
class ResetPoseResult:
    succeeded: bool = False
    reset_count: int = 0
    error: Exception | None = None


class ResetPoseAction:
    """Reset selected joints to zero rotation and their captured bind translation."""

    def __init__(self, maya_adapter: MayaCmdsAdapter):
        self._adapter = maya_adapter

    def execute(self, request: ResetPoseRequest) -> ResetPoseResult:
        if not request.joints:
            return ResetPoseResult(succeeded=True)
        try:
            self._adapter.undo_info(openChunk=True, chunkName="Reset Pose")
            count = 0
            for jnt in request.joints:
                changed = False
                bind_translate = request.bind_translations.get(jnt)
                if bind_translate is not None:
                    try:
                        self._adapter.xform(jnt, translation=bind_translate)
                        changed = True
                    except Exception:
                        pass
                try:
                    self._adapter.xform(jnt, rotation=(0, 0, 0))
                    changed = True
                except Exception:
                    pass
                if changed:
                    count += 1
            return ResetPoseResult(succeeded=True, reset_count=count)
        except Exception as exc:
            return ResetPoseResult(error=exc)
        finally:
            try:
                self._adapter.undo_info(closeChunk=True)
            except Exception:
                pass


# -- Mirror Pose (stub) -----------------------------------------------


@dataclass
class MirrorPoseRequest:
    joints: list[str]


@dataclass
class MirrorPoseResult:
    succeeded: bool = False
    error: Exception | None = None


class MirrorPoseAction:
    """Mirror pose across left/right axis. Not yet implemented."""

    def __init__(self, maya_adapter: MayaCmdsAdapter):
        self._adapter = maya_adapter

    def execute(self, request: MirrorPoseRequest) -> MirrorPoseResult:
        return MirrorPoseResult(
            error=NotImplementedError("Mirror Pose not yet implemented")
        )


# -- Bake Animation (stub) --------------------------------------------


@dataclass
class BakeAnimationRequest:
    joints: list[str]
    start_frame: float = 0
    end_frame: float = 100


@dataclass
class BakeAnimationResult:
    succeeded: bool = False
    error: Exception | None = None


class BakeAnimationAction:
    """Bake animation keys for selected joints. Not yet implemented."""

    def __init__(self, maya_adapter: MayaCmdsAdapter):
        self._adapter = maya_adapter

    def execute(self, request: BakeAnimationRequest) -> BakeAnimationResult:
        return BakeAnimationResult(
            error=NotImplementedError("Bake Animation not yet implemented")
        )


# -- Clean Curves (stub) ----------------------------------------------


@dataclass
class CleanCurvesRequest:
    joints: list[str]


@dataclass
class CleanCurvesResult:
    succeeded: bool = False
    removed_count: int = 0
    error: Exception | None = None


class CleanCurvesAction:
    """Remove redundant animation keys. Not yet implemented."""

    def __init__(self, maya_adapter: MayaCmdsAdapter):
        self._adapter = maya_adapter

    def execute(self, request: CleanCurvesRequest) -> CleanCurvesResult:
        return CleanCurvesResult(
            error=NotImplementedError("Clean Curves not yet implemented")
        )
