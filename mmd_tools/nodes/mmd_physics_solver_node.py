"""mmdPhysicsSolver — Stateful MMD physics solver DG node (Python MPxNode).

Maintains a Bullet physics world via the mmd-anim FFI and steps it in response
to Maya's time evaluation.  Outputs bone world matrices (Maya-space) as a flat
doubleArray plus metadata.

Time state machine:
- same time → idempotent (cached result)
- forward step → step_runtime(dt)
- jump / backward / first eval → reset + single step

inputMode attribute:
- 0 (rest-only): solver uses mmd-anim rest pose only, no Maya joint reading
- 1 (maya-pose): solver reads kinematic bone world matrices from Maya joints
  and injects them via apply_physics_world_matrices before each step

This is the Python prototype; a C++ version with the same TypeId will replace
it when the C++ plugin is loaded (mutual-exclusion pattern).
"""

from __future__ import annotations

import maya.api.OpenMaya as om

from mmd_tools.core.native.mmd_anim_runtime import is_native_physics_available


def maya_useNewAPI():
    pass


_TIME_EPSILON = 1e-6
_MAX_FORWARD_DT = 0.2

INPUT_MODE_REST = 0
INPUT_MODE_MAYA_POSE = 1

_SIMULATED_RB_CACHE: dict[str, om.MMatrix] = {}


class MmdPhysicsSolverNode(om.MPxNode):
    kTypeName = "mmdPhysicsSolver"
    kTypeId = om.MTypeId(0x00128008)
    kClassify = "utility/general"

    aEnable = None
    aInputMode = None
    aInTime = None
    aModelRoot = None

    aInWorldSettings = None

    aOutBoneMatrices = None
    aOutBoneCount = None
    aOutStatus = None
    aOutSolved = None

    def __init__(self):
        super().__init__()
        self._world = None
        self._model = None
        self._instance = None
        self._bone_count = 0
        self._bone_joints = []
        self._kinematic_corrections = {}
        self._rb_shape_paths = {}
        self._last_time = None
        self._cached_flat = None
        self._initialized = False
        self._last_reset_generation = -1

    def compute(self, plug, data):
        attr = plug.attribute()
        if attr not in (
            self.aOutBoneMatrices,
            self.aOutBoneCount,
            self.aOutStatus,
            self.aOutSolved,
        ):
            return None

        enable = data.inputValue(self.aEnable).asBool()
        if not enable:
            self._write_outputs(data, solved=False, status="disabled")
            return

        input_mode = data.inputValue(self.aInputMode).asShort()
        current_time = data.inputValue(self.aInTime).asTime().asUnits(om.MTime.kSeconds)

        if not self._initialized:
            self._try_initialize()

        if self._world is None or self._instance is None:
            self._write_outputs(data, solved=False, status="no physics data")
            return

        world_enable, reset_gen = self._read_world_settings()
        if not world_enable:
            self._write_outputs(data, solved=False, status="disabled")
            return

        force_reset = False
        if reset_gen != self._last_reset_generation:
            self._last_reset_generation = reset_gen
            force_reset = True

        if (
            not force_reset
            and self._last_time is not None
            and abs(current_time - self._last_time) < _TIME_EPSILON
        ):
            self._write_outputs(data, solved=True, status="cached")
            return

        dt = current_time - self._last_time if self._last_time is not None else None

        if not force_reset and dt is not None and 0 < dt < _MAX_FORWARD_DT:
            self._forward_step(dt, input_mode)
            status = "stepped"
        else:
            self._reset_world(input_mode)
            status = "reset"

        self._last_time = current_time
        self._update_cached_matrices()
        self._update_rigid_body_visual_cache()
        self._write_outputs(data, solved=True, status=status)

    def _forward_step(self, dt: float, input_mode: int) -> None:
        self._instance.evaluate_rest_pose()
        if input_mode == INPUT_MODE_MAYA_POSE and self._kinematic_corrections:
            self._inject_kinematic_poses()
            self._instance.evaluate_current_pose_before_physics()
        self._world.step_runtime(self._instance, dt)
        self._instance.evaluate_current_pose_after_physics()

    def _reset_world(self, input_mode: int) -> None:
        from mmd_tools.core.native.mmd_anim_runtime_types import MMD_RUNTIME_PHYSICS_MODE_LIVE

        self._instance.set_physics_mode(MMD_RUNTIME_PHYSICS_MODE_LIVE)
        self._instance.evaluate_rest_pose()
        if input_mode == INPUT_MODE_MAYA_POSE and self._kinematic_corrections:
            self._inject_kinematic_poses()
            self._instance.evaluate_current_pose_before_physics()
        self._world.reset(self._instance)

    def _update_cached_matrices(self) -> None:
        raw = self._instance.get_world_matrices()
        if raw is None:
            self._cached_flat = None
            return
        from mmd_tools.core.coordinate_transform import mmd_matrix_to_maya

        flat = []
        for mat16 in raw:
            flat.extend(mmd_matrix_to_maya(mat16))
        self._cached_flat = flat

    def _try_initialize(self) -> None:
        model_root = self._get_connected_model_root()
        if not model_root:
            self._initialized = True
            return

        from mmd_tools.core.physics_solver import _collect_bone_joints, read_source_pmx_payload
        from mmd_tools.core.native.mmd_anim_runtime_handles import (
            MmdRuntimeInstance,
            MmdRuntimeModel,
            MmdRuntimePhysicsWorld,
        )

        pmx_bytes = read_source_pmx_payload(model_root)
        if not pmx_bytes:
            self._initialized = True
            return

        bone_joints = _collect_bone_joints(model_root)
        self._bone_count = len(bone_joints)
        self._bone_joints = bone_joints

        world = MmdRuntimePhysicsWorld.from_pmx_bytes(pmx_bytes)
        if world is None:
            self._initialized = True
            return

        model = MmdRuntimeModel.from_pmx_bytes(pmx_bytes)
        if model is None:
            world.free()
            self._initialized = True
            return

        instance = MmdRuntimeInstance.for_model(model)
        if instance is None:
            world.free()
            model.free()
            self._initialized = True
            return

        self._world = world
        self._model = model
        self._instance = instance
        self._initialized = True

        self._build_kinematic_pose_data(model_root)
        self._build_rigid_body_shape_mapping(model_root)

    def _build_kinematic_pose_data(self, model_root: str) -> None:
        """Identify kinematic bones and precompute bind corrections.

        Bind correction maps Maya joint world space to the mmd-anim solver's
        internal bone world space.  Only bones attached to physics_mode=0
        (follows-bone / kinematic) rigid bodies are read — these joints are
        never written by mmdPhysicsBoneDriver, so reading them is cycle-safe.

        correction = mmd_matrix_to_maya(mmd_rest) * maya_bind^(-1)
        At runtime:  mmd_world = maya_matrix_to_mmd(correction * maya_animated)
        """
        from maya import cmds
        from mmd_tools.core.coordinate_transform import mmd_matrix_to_maya

        kinematic_bone_indices = self._find_kinematic_bone_indices(model_root)
        if not kinematic_bone_indices:
            return

        self._instance.evaluate_rest_pose()
        mmd_rest_matrices = self._instance.get_world_matrices()
        if not mmd_rest_matrices:
            return

        for bone_idx in kinematic_bone_indices:
            if bone_idx >= len(mmd_rest_matrices) or bone_idx >= len(self._bone_joints):
                continue
            joint = self._bone_joints[bone_idx]
            if not joint or not cmds.objExists(joint):
                continue
            try:
                mmd_rest_maya = mmd_matrix_to_maya(mmd_rest_matrices[bone_idx])
                mmd_rest_maya_mat = om.MMatrix(mmd_rest_maya)

                maya_bind = [float(v) for v in cmds.getAttr(f"{joint}.worldMatrix[0]")]
                bind_mat = om.MMatrix(maya_bind)

                self._kinematic_corrections[bone_idx] = mmd_rest_maya_mat * bind_mat.inverse()
            except Exception:
                continue

    @staticmethod
    def _find_kinematic_bone_indices(model_root: str) -> set:
        from maya import cmds

        result = set()
        try:
            children = cmds.listRelatives(
                model_root, children=True, fullPath=True, type="transform",
            ) or []
            physics_group = None
            for c in children:
                if c.rsplit("|", 1)[-1].rsplit(":", 1)[-1] == "Physics":
                    physics_group = c
                    break
            if not physics_group:
                return result

            children = cmds.listRelatives(
                physics_group, children=True, fullPath=True, type="transform",
            ) or []
            rb_group = None
            for c in children:
                if c.rsplit("|", 1)[-1].rsplit(":", 1)[-1] == "RigidBodies":
                    rb_group = c
                    break
            if not rb_group:
                return result

            rb_transforms = cmds.listRelatives(
                rb_group, children=True, fullPath=True, type="transform",
            ) or []
            for xform in rb_transforms:
                shapes = cmds.listRelatives(
                    xform, shapes=True, fullPath=True, type="mmdRigidBodyShape",
                ) or []
                for shape in shapes:
                    if cmds.getAttr(f"{shape}.physicsMode") == 0:
                        idx = cmds.getAttr(f"{shape}.relatedBoneIndex")
                        if idx >= 0:
                            result.add(idx)
        except Exception:
            pass
        return result

    def _inject_kinematic_poses(self) -> None:
        """Read kinematic bone world matrices from Maya and inject into the instance."""
        from maya import cmds
        from mmd_tools.core.coordinate_transform import maya_matrix_to_mmd

        bone_count = self._bone_count
        if bone_count <= 0:
            return

        flat = [0.0] * (bone_count * 16)
        mask = [0] * bone_count

        for bone_idx, correction_inv in self._kinematic_corrections.items():
            joint = self._bone_joints[bone_idx]
            if not joint:
                continue
            try:
                maya_world = [float(v) for v in cmds.getAttr(f"{joint}.worldMatrix[0]")]
                corrected = correction_inv * om.MMatrix(maya_world)
                corrected_flat = [
                    corrected.getElement(r, c) for r in range(4) for c in range(4)
                ]
                offset = bone_idx * 16
                flat[offset : offset + 16] = maya_matrix_to_mmd(corrected_flat)
                mask[bone_idx] = 1
            except Exception:
                continue

        if any(mask):
            self._instance.apply_physics_world_matrices(flat, mask)

    def _build_rigid_body_shape_mapping(self, model_root: str) -> None:
        """Build pmxIndex → shape DAG path mapping for visual cache updates."""
        from maya import cmds

        try:
            children = cmds.listRelatives(
                model_root, children=True, fullPath=True, type="transform",
            ) or []
            physics_group = None
            for c in children:
                if c.rsplit("|", 1)[-1].rsplit(":", 1)[-1] == "Physics":
                    physics_group = c
                    break
            if not physics_group:
                return

            children = cmds.listRelatives(
                physics_group, children=True, fullPath=True, type="transform",
            ) or []
            rb_group = None
            for c in children:
                if c.rsplit("|", 1)[-1].rsplit(":", 1)[-1] == "RigidBodies":
                    rb_group = c
                    break
            if not rb_group:
                return

            rb_transforms = cmds.listRelatives(
                rb_group, children=True, fullPath=True, type="transform",
            ) or []
            for xform in rb_transforms:
                shapes = cmds.listRelatives(
                    xform, shapes=True, fullPath=True, type="mmdRigidBodyShape",
                ) or []
                for shape in shapes:
                    idx = cmds.getAttr(f"{shape}.pmxIndex")
                    if idx >= 0:
                        self._rb_shape_paths[idx] = shape
        except Exception:
            pass

    def _update_rigid_body_visual_cache(self) -> None:
        """Populate the module-level cache with simulated rigid body world matrices."""
        if not self._rb_shape_paths or self._world is None:
            return
        states = self._world.copy_rigidbody_states()
        if states is None:
            return
        from mmd_tools.core.coordinate_transform import mmd_point_to_maya

        for pmx_idx, shape_path in self._rb_shape_paths.items():
            if pmx_idx >= len(states):
                continue
            pos_mmd, quat_xyzw_mmd = states[pmx_idx]
            pos_maya = mmd_point_to_maya(pos_mmd)
            qx, qy, qz, qw = quat_xyzw_mmd
            tmat = om.MTransformationMatrix()
            tmat.setTranslation(om.MVector(*pos_maya), om.MSpace.kWorld)
            tmat.setRotation(om.MQuaternion(-qx, -qy, qz, qw))
            _SIMULATED_RB_CACHE[shape_path] = tmat.asMatrix()

    def _read_world_settings(self):
        """Read enable and resetGeneration from connected world node."""
        try:
            fn = om.MFnDependencyNode(self.thisMObject())
            plug = fn.findPlug("inWorldSettings", False)
            connections = plug.connectedTo(True, False)
            if not connections:
                return True, self._last_reset_generation
            world_fn = om.MFnDependencyNode(connections[0].node())
            enable = world_fn.findPlug("enable", False).asBool()
            reset_gen = world_fn.findPlug("resetGeneration", False).asInt()
            return enable, reset_gen
        except Exception:
            return True, self._last_reset_generation

    def _get_connected_model_root(self):
        try:
            fn = om.MFnDependencyNode(self.thisMObject())
            plug = fn.findPlug("modelRoot", False)
            connections = plug.connectedTo(True, False)
            if connections:
                return om.MFnDependencyNode(connections[0].node()).name()
        except Exception:
            pass
        return None

    def _write_outputs(self, data, solved: bool, status: str) -> None:
        data.outputValue(self.aOutSolved).setBool(solved)
        data.outputValue(self.aOutStatus).setString(status)
        data.outputValue(self.aOutBoneCount).setInt(self._bone_count)

        mat_handle = data.outputValue(self.aOutBoneMatrices)
        if self._cached_flat:
            fn = om.MFnDoubleArrayData()
            arr = om.MDoubleArray(self._cached_flat)
            mat_handle.setMObject(fn.create(arr))
        else:
            fn = om.MFnDoubleArrayData()
            mat_handle.setMObject(fn.create(om.MDoubleArray()))

        data.setClean(self.aOutBoneMatrices)
        data.setClean(self.aOutBoneCount)
        data.setClean(self.aOutStatus)
        data.setClean(self.aOutSolved)

    def _free_handles(self) -> None:
        if self._world is not None:
            self._world.free()
            self._world = None
        if self._instance is not None:
            self._instance.free()
            self._instance = None
        if self._model is not None:
            self._model.free()
            self._model = None
        self._initialized = False
        self._bone_joints = []
        self._kinematic_corrections = {}
        self._rb_shape_paths = {}
        self._last_time = None
        self._cached_flat = None

    def __del__(self):
        try:
            self._free_handles()
        except Exception:
            pass


def creator():
    return MmdPhysicsSolverNode()


def initialize():
    tAttr = om.MFnTypedAttribute()
    nAttr = om.MFnNumericAttribute()
    uAttr = om.MFnUnitAttribute()
    msgAttr = om.MFnMessageAttribute()

    MmdPhysicsSolverNode.aEnable = nAttr.create(
        "enable", "en", om.MFnNumericData.kBoolean, True
    )
    nAttr.storable = True
    nAttr.keyable = True
    MmdPhysicsSolverNode.addAttribute(MmdPhysicsSolverNode.aEnable)

    eAttr = om.MFnEnumAttribute()
    MmdPhysicsSolverNode.aInputMode = eAttr.create("inputMode", "im", INPUT_MODE_MAYA_POSE)
    eAttr.addField("rest-only", INPUT_MODE_REST)
    eAttr.addField("maya-pose", INPUT_MODE_MAYA_POSE)
    eAttr.storable = True
    eAttr.keyable = False
    MmdPhysicsSolverNode.addAttribute(MmdPhysicsSolverNode.aInputMode)

    MmdPhysicsSolverNode.aInTime = uAttr.create("inTime", "it", om.MFnUnitAttribute.kTime, 0.0)
    uAttr.storable = False
    MmdPhysicsSolverNode.addAttribute(MmdPhysicsSolverNode.aInTime)

    MmdPhysicsSolverNode.aModelRoot = msgAttr.create("modelRoot", "mr")
    MmdPhysicsSolverNode.addAttribute(MmdPhysicsSolverNode.aModelRoot)

    MmdPhysicsSolverNode.aInWorldSettings = msgAttr.create("inWorldSettings", "iws")
    MmdPhysicsSolverNode.addAttribute(MmdPhysicsSolverNode.aInWorldSettings)

    MmdPhysicsSolverNode.aOutBoneMatrices = tAttr.create(
        "outBoneMatrices", "obm", om.MFnData.kDoubleArray
    )
    tAttr.writable = False
    tAttr.storable = False
    MmdPhysicsSolverNode.addAttribute(MmdPhysicsSolverNode.aOutBoneMatrices)

    MmdPhysicsSolverNode.aOutBoneCount = nAttr.create(
        "outBoneCount", "obc", om.MFnNumericData.kInt, 0
    )
    nAttr.writable = False
    nAttr.storable = False
    MmdPhysicsSolverNode.addAttribute(MmdPhysicsSolverNode.aOutBoneCount)

    MmdPhysicsSolverNode.aOutStatus = tAttr.create(
        "outStatus", "ost", om.MFnData.kString
    )
    tAttr.writable = False
    tAttr.storable = False
    MmdPhysicsSolverNode.addAttribute(MmdPhysicsSolverNode.aOutStatus)

    MmdPhysicsSolverNode.aOutSolved = nAttr.create(
        "outSolved", "osv", om.MFnNumericData.kBoolean, False
    )
    nAttr.writable = False
    nAttr.storable = False
    MmdPhysicsSolverNode.addAttribute(MmdPhysicsSolverNode.aOutSolved)

    MmdPhysicsSolverNode.attributeAffects(
        MmdPhysicsSolverNode.aEnable, MmdPhysicsSolverNode.aOutBoneMatrices
    )
    MmdPhysicsSolverNode.attributeAffects(
        MmdPhysicsSolverNode.aEnable, MmdPhysicsSolverNode.aOutBoneCount
    )
    MmdPhysicsSolverNode.attributeAffects(
        MmdPhysicsSolverNode.aEnable, MmdPhysicsSolverNode.aOutStatus
    )
    MmdPhysicsSolverNode.attributeAffects(
        MmdPhysicsSolverNode.aEnable, MmdPhysicsSolverNode.aOutSolved
    )
    MmdPhysicsSolverNode.attributeAffects(
        MmdPhysicsSolverNode.aInTime, MmdPhysicsSolverNode.aOutBoneMatrices
    )
    MmdPhysicsSolverNode.attributeAffects(
        MmdPhysicsSolverNode.aInTime, MmdPhysicsSolverNode.aOutBoneCount
    )
    MmdPhysicsSolverNode.attributeAffects(
        MmdPhysicsSolverNode.aInTime, MmdPhysicsSolverNode.aOutStatus
    )
    MmdPhysicsSolverNode.attributeAffects(
        MmdPhysicsSolverNode.aInTime, MmdPhysicsSolverNode.aOutSolved
    )
    MmdPhysicsSolverNode.attributeAffects(
        MmdPhysicsSolverNode.aInputMode, MmdPhysicsSolverNode.aOutBoneMatrices
    )
    MmdPhysicsSolverNode.attributeAffects(
        MmdPhysicsSolverNode.aInputMode, MmdPhysicsSolverNode.aOutBoneCount
    )
    MmdPhysicsSolverNode.attributeAffects(
        MmdPhysicsSolverNode.aInputMode, MmdPhysicsSolverNode.aOutStatus
    )
    MmdPhysicsSolverNode.attributeAffects(
        MmdPhysicsSolverNode.aInputMode, MmdPhysicsSolverNode.aOutSolved
    )


def register(plugin_fn):
    if not is_native_physics_available():
        om.MGlobal.displayWarning(
            "mmd-anim physics not available — mmdPhysicsSolver not registered"
        )
        return
    plugin_fn.registerNode(
        MmdPhysicsSolverNode.kTypeName,
        MmdPhysicsSolverNode.kTypeId,
        creator,
        initialize,
        om.MPxNode.kDependNode,
        MmdPhysicsSolverNode.kClassify,
    )


def deregister(plugin_fn):
    try:
        plugin_fn.deregisterNode(MmdPhysicsSolverNode.kTypeId)
    except Exception:
        pass
