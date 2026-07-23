"""Action boundaries for pose manipulation in the Animator Toolset."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..adapters.maya_cmds_adapter import MayaCmdsAdapter


@dataclass(frozen=True)
class PoseTransform:
    """Per-joint local-space transform snapshot."""

    translation: tuple[float, float, float]
    rotation: tuple[float, float, float]


# -- Copy Pose --------------------------------------------------------


@dataclass
class CopyPoseRequest:
    joints: list[str]


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
            pose: dict[str, PoseTransform] = {}
            for jnt in request.joints:
                t = self._adapter.xform(jnt, query=True, translation=True)
                r = self._adapter.xform(jnt, query=True, rotation=True)
                pose[jnt] = PoseTransform(
                    translation=(t[0], t[1], t[2]),
                    rotation=(r[0], r[1], r[2]),
                )
            return CopyPoseResult(succeeded=True, pose=pose)
        except Exception as exc:
            return CopyPoseResult(error=exc)


# -- Paste Pose -------------------------------------------------------


@dataclass
class PastePoseRequest:
    pose: dict[str, PoseTransform]


@dataclass
class PastePoseResult:
    succeeded: bool = False
    applied_count: int = 0
    error: Exception | None = None


class PastePoseAction:
    """Apply a previously copied pose to matching joints."""

    def __init__(self, maya_adapter: MayaCmdsAdapter):
        self._adapter = maya_adapter

    def execute(self, request: PastePoseRequest) -> PastePoseResult:
        if not request.pose:
            return PastePoseResult(succeeded=True)
        try:
            self._adapter.undo_info(openChunk=True, chunkName="Paste Pose")
            count = 0
            for jnt, xf in request.pose.items():
                try:
                    self._adapter.xform(jnt, translation=xf.translation)
                    self._adapter.xform(jnt, rotation=xf.rotation)
                    count += 1
                except Exception:
                    continue
            return PastePoseResult(succeeded=True, applied_count=count)
        except Exception as exc:
            return PastePoseResult(error=exc)
        finally:
            try:
                self._adapter.undo_info(closeChunk=True)
            except Exception:
                pass


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
