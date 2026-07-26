"""One-shot model-wide bind-pose restore for skinning inspection.

The action delegates to Maya's bind ``dagPose`` and deliberately keeps every
animation and rig connection intact.  It owns no mode or restoration state;
normal DG evaluation or a timeline change can therefore show motion again.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass

from maya import cmds as maya_cmds

from ..core.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class GoToBindPoseResult:
    """Result of one model-scoped bind-pose restore."""

    succeeded: bool
    model_root: str = ""
    joint_count: int = 0
    error: str = ""


class GoToBindPoseAction:
    """Restore one model's Maya bind pose without creating a persistent mode."""

    def __init__(self, cmds_module=None):
        self.cmds = cmds_module or maya_cmds

    def execute(self, model_root: str) -> GoToBindPoseResult:
        """Run Maya's bind-pose restore while preserving the live rig graph."""

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
        try:
            with self._undo_chunk("MMD Go to Bind Pose"):
                restore_flags = {"restore": True}
                members = self.cmds.dagPose(poses[0], query=True, members=True) or []
                if not any(
                    self.cmds.listConnections(
                        f"{member}.offsetParentMatrix",
                        source=True,
                        destination=False,
                    )
                    for member in members
                ):
                    # Match Maya's native Go to Bind Pose: global restore is
                    # preferred unless offsetParentMatrix has a live driver.
                    restore_flags["global"] = True
                self.cmds.dagPose(poses[0], **restore_flags)
        except Exception as exc:
            logger.error("Go to Bind Pose failed", exc_info=True)
            return GoToBindPoseResult(
                False,
                str(model_root),
                len(joints),
                str(exc),
            )
        return GoToBindPoseResult(True, str(model_root), len(joints))

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
