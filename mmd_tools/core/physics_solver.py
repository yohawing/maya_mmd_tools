"""Python physics solver session — per-frame stepping via mmd-anim FFI.

Provides a stateful session that builds a Bullet world from DAG descriptors,
steps it per frame using the split-evaluation pipeline, and writes bone world
matrices back to Maya joints.

This is a prototype / fallback path; PHS-2 replaces it with a C++ DG solver
node for production use.
"""

from __future__ import annotations

from typing import Optional, Sequence

from maya import cmds

from mmd_tools.core.constants import ATTR_MMD_SOURCE_PMX_PAYLOAD
from mmd_tools.core.coordinate_transform import mmd_matrix_to_maya
from mmd_tools.core.logger import get_logger
from mmd_tools.core.native.mmd_anim_runtime import is_native_physics_available
from mmd_tools.core.native.mmd_anim_runtime_handles import (
    MmdRuntimeInstance,
    MmdRuntimeModel,
    MmdRuntimePhysicsWorld,
)
from mmd_tools.core.native.mmd_anim_runtime_types import (
    MMD_RUNTIME_PHYSICS_MODE_LIVE,
)
from mmd_tools.core.physics_dag_descriptor import build_descriptors_from_dag

logger = get_logger(__name__)


def read_source_pmx_payload(root_group: str) -> Optional[bytes]:
    """Read the stored PMX payload from the model root, or None if absent."""
    import base64

    if not cmds.objExists(root_group):
        return None
    if not cmds.attributeQuery(ATTR_MMD_SOURCE_PMX_PAYLOAD, node=root_group, exists=True):
        return None
    try:
        data = cmds.getAttr(f"{root_group}.{ATTR_MMD_SOURCE_PMX_PAYLOAD}")
        if not data:
            return None
        return base64.b64decode(data)
    except Exception:
        return None


def _collect_bone_joints(root_group: str) -> list[Optional[str]]:
    """Collect Maya joints indexed by PMX bone index from the scene."""
    from mmd_tools.core.constants import ATTR_MMD_BONE_INDEX
    all_joints = cmds.listRelatives(root_group, allDescendents=True, type="joint", fullPath=True) or []
    max_index = -1
    index_map: dict[int, str] = {}
    for jnt in all_joints:
        if cmds.attributeQuery(ATTR_MMD_BONE_INDEX, node=jnt, exists=True):
            idx = int(cmds.getAttr(f"{jnt}.{ATTR_MMD_BONE_INDEX}"))
            index_map[idx] = jnt
            if idx > max_index:
                max_index = idx
    if max_index < 0:
        return []
    result: list[Optional[str]] = [None] * (max_index + 1)
    for idx, jnt in index_map.items():
        result[idx] = jnt
    return result


class PhysicsSolverSession:
    """Stateful physics simulation session driven by DAG descriptors.

    Lifecycle::

        session = PhysicsSolverSession.create(root_group, pmx_bytes, bone_joints)
        session.reset()
        for frame in range(start, end):
            session.step(dt=1/30)
            session.apply_to_joints()
        session.free()
    """

    def __init__(
        self,
        world: MmdRuntimePhysicsWorld,
        model: MmdRuntimeModel,
        instance: MmdRuntimeInstance,
        bone_index_to_joint: dict[int, str],
        bone_count: int,
    ):
        self._world = world
        self._model = model
        self._instance = instance
        self._bone_index_to_joint = bone_index_to_joint
        self._bone_count = bone_count
        self._stepped = False

    @classmethod
    def create(
        cls,
        root_group: str,
        pmx_bytes: bytes,
        bone_joints: Sequence[Optional[str]],
    ) -> Optional["PhysicsSolverSession"]:
        """Build a solver session from DAG physics and PMX model data.

        Args:
            root_group: Model root transform containing the Physics hierarchy.
            pmx_bytes: Raw PMX file bytes for creating the runtime model/instance.
            bone_joints: Maya joint paths indexed by PMX bone index.

        Returns:
            A ready-to-use session, or None if physics is unavailable.
        """
        if not is_native_physics_available():
            logger.warning("Native physics not available")
            return None

        desc_set = build_descriptors_from_dag(
            root_group,
            bone_joints=bone_joints,
            bone_count=len(bone_joints),
        )
        if not desc_set.rigid_bodies:
            logger.warning("No rigid bodies found in DAG")
            return None

        world = MmdRuntimePhysicsWorld.from_descriptors(
            desc_set.rigid_bodies, desc_set.joints,
        )
        if world is None:
            logger.error("Failed to create physics world from DAG descriptors")
            return None

        model = MmdRuntimeModel.from_pmx_bytes(pmx_bytes)
        if model is None:
            world.free()
            logger.error("Failed to create runtime model from PMX bytes")
            return None

        instance = MmdRuntimeInstance.for_model(model)
        if instance is None:
            world.free()
            model.free()
            logger.error("Failed to create runtime instance")
            return None

        bone_index_to_joint: dict[int, str] = {}
        for idx, jnt in enumerate(bone_joints):
            if jnt and cmds.objExists(jnt):
                bone_index_to_joint[idx] = jnt

        return cls(world, model, instance, bone_index_to_joint, len(bone_joints))

    @classmethod
    def from_scene(cls, root_group: str) -> Optional["PhysicsSolverSession"]:
        """Create a solver session from the scene, reading PMX payload from the model root.

        Requires that the model root has the ``mmd_source_pmx_payload`` attribute
        (written by the import pipeline when physics nodes are enabled).
        """
        pmx_bytes = read_source_pmx_payload(root_group)
        if pmx_bytes is None:
            logger.error("No PMX payload found on '%s'", root_group)
            return None

        bone_joints = _collect_bone_joints(root_group)
        if not bone_joints:
            logger.error("No bone joints found under '%s'", root_group)
            return None

        return cls.create(root_group, pmx_bytes, bone_joints)

    def reset(self) -> bool:
        """Prepare the physics world for simulation from rest pose."""
        self._instance.set_physics_mode(MMD_RUNTIME_PHYSICS_MODE_LIVE)
        if not self._instance.evaluate_rest_pose():
            return False
        result = self._world.reset(self._instance)
        self._stepped = False
        return result is not None

    def step(self, dt: float = 1.0 / 30.0) -> bool:
        """Advance the physics simulation by dt seconds.

        Uses the split-evaluation pipeline:
        1. evaluate_rest_pose (pre-physics pose)
        2. step_runtime (Bullet step + bone write-back)
        3. evaluate_current_pose_after_physics (post-physics IK/append)
        """
        if not self._instance.evaluate_rest_pose():
            return False
        report = self._world.step_runtime(self._instance, dt)
        if report is None:
            return False
        self._instance.evaluate_current_pose_after_physics()
        self._stepped = True
        return True

    def get_bone_world_matrices(self) -> Optional[list[list[float]]]:
        """Return current bone world matrices from the runtime instance."""
        return self._instance.get_world_matrices()

    def apply_to_joints(self) -> int:
        """Write current bone world matrices to Maya joints.

        Iterates in ascending bone index order so parent transforms are
        committed before children.

        Returns:
            Number of joints updated.
        """
        matrices = self._instance.get_world_matrices()
        if matrices is None:
            return 0

        updated = 0
        for bone_idx in sorted(self._bone_index_to_joint.keys()):
            if bone_idx >= len(matrices):
                continue
            joint = self._bone_index_to_joint[bone_idx]
            if not cmds.objExists(joint):
                continue
            maya_matrix = mmd_matrix_to_maya(matrices[bone_idx])
            cmds.xform(joint, worldSpace=True, matrix=maya_matrix)
            updated += 1

        return updated

    def free(self) -> None:
        """Release all native handles."""
        if self._world is not None:
            self._world.free()
            self._world = None
        if self._instance is not None:
            self._instance.free()
            self._instance = None
        if self._model is not None:
            self._model.free()
            self._model = None

    def __del__(self):
        self.free()
