"""mmdPhysicsSolver — Stateful MMD physics solver DG node (Python MPxNode).

Maintains a Bullet physics world via the mmd-anim FFI and steps it in response
to Maya's time evaluation.  Outputs bone world matrices (Maya-space) as a flat
doubleArray plus metadata.

Time state machine:
- same time → idempotent (cached result)
- forward step → step_runtime(dt)
- jump / backward / first eval → reset + single step

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


class MmdPhysicsSolverNode(om.MPxNode):
    kTypeName = "mmdPhysicsSolver"
    kTypeId = om.MTypeId(0x00128008)
    kClassify = "utility/general"

    aEnable = None
    aInTime = None
    aModelRoot = None

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
        self._last_time = None
        self._cached_flat = None
        self._initialized = False

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

        current_time = data.inputValue(self.aInTime).asTime().asUnits(om.MTime.kSeconds)

        if not self._initialized:
            self._try_initialize()

        if self._world is None or self._instance is None:
            self._write_outputs(data, solved=False, status="no physics data")
            return

        if self._last_time is not None and abs(current_time - self._last_time) < _TIME_EPSILON:
            self._write_outputs(data, solved=True, status="cached")
            return

        dt = current_time - self._last_time if self._last_time is not None else None

        if dt is not None and 0 < dt < _MAX_FORWARD_DT:
            self._forward_step(dt)
            status = "stepped"
        else:
            self._reset_world()
            status = "reset"

        self._last_time = current_time
        self._update_cached_matrices()
        self._write_outputs(data, solved=True, status=status)

    def _forward_step(self, dt: float) -> None:
        self._instance.evaluate_rest_pose()
        self._world.step_runtime(self._instance, dt)
        self._instance.evaluate_current_pose_after_physics()

    def _reset_world(self) -> None:
        from mmd_tools.core.native.mmd_anim_runtime_types import MMD_RUNTIME_PHYSICS_MODE_LIVE

        self._instance.set_physics_mode(MMD_RUNTIME_PHYSICS_MODE_LIVE)
        self._instance.evaluate_rest_pose()
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

    MmdPhysicsSolverNode.aInTime = uAttr.create("inTime", "it", om.MFnUnitAttribute.kTime, 0.0)
    uAttr.storable = False
    MmdPhysicsSolverNode.addAttribute(MmdPhysicsSolverNode.aInTime)

    MmdPhysicsSolverNode.aModelRoot = msgAttr.create("modelRoot", "mr")
    MmdPhysicsSolverNode.addAttribute(MmdPhysicsSolverNode.aModelRoot)

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
