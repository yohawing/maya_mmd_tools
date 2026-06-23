"""mmdCcdIk — MMD CCD-IK ソルバー DG ノード (Python MPxNode prototype).

mmd-anim FFI の MmdIkChain を内部で呼び出し、
ゴール位置に向かって IK リンクの回転を解く。

チェーン定義は chainJson 属性で設定（import 時に1回書き込む）。
per-frame は goal (ターゲット位置) + inputRotate[] (全ボーンのローカル回転)。

DG 接続例:
    ikController.translate → mmdCcdIk1.goal
    animCurve → mmdCcdIk1.inputRotate[0]   (ボーン0のアニメ回転)
    animCurve → mmdCcdIk1.inputRotate[1]   (ボーン1のアニメ回転)
    mmdCcdIk1.outputRotate[0] → linkJoint0.rotate
    mmdCcdIk1.outputRotate[1] → linkJoint1.rotate
"""

from __future__ import annotations

import json
import math

import maya.api.OpenMaya as om

from mmd_tools.core.native.mmd_anim_runtime import MmdIkChain, is_rig_primitive_available


def maya_useNewAPI():
    pass


class MmdCcdIkNode(om.MPxNode):
    kTypeName = "mmdCcdIk"
    kTypeId = om.MTypeId(0x00128002)
    kClassify = "utility/general"

    aChainJson = None

    aGoal = None
    aGoalX = None
    aGoalY = None
    aGoalZ = None

    aInputRotate = None
    aInputTranslate = None

    aOutputRotate = None

    aEnabled = None

    def __init__(self):
        super().__init__()
        self._solver = None
        self._chain_def = None
        self._bone_count = 0
        self._link_count = 0
        self._rest_positions = []
        self._bone_joint_orients = []
        self._link_joint_orients = []
        self._ik_link_slots = set()

    def _ensure_solver(self, chain_json: str):
        if self._solver is not None and self._chain_def == chain_json:
            return
        if self._solver is not None:
            self._solver.free()
            self._solver = None
            self._chain_def = None

        if not chain_json:
            return

        try:
            cfg = json.loads(chain_json)
        except (json.JSONDecodeError, TypeError):
            om.MGlobal.displayWarning("mmdCcdIk: invalid chainJson")
            return

        bones = cfg.get("bones", [])
        links = cfg.get("links", [])
        target_slot = cfg.get("targetBoneSlot", 0)
        iterations = cfg.get("iterationCount", 40)
        limit_angle = cfg.get("limitAngle", 0.0628)

        self._bone_count = len(bones)
        self._link_count = len(links)

        self._rest_positions = [b.get("rest_position", [0, 0, 0]) for b in bones]

        self._bone_joint_orients = []
        for b in bones:
            jo_deg = b.get("joint_orient_deg", [0, 0, 0])
            if any(abs(v) > 1e-8 for v in jo_deg):
                q_jo = om.MEulerRotation(
                    math.radians(jo_deg[0]),
                    math.radians(jo_deg[1]),
                    math.radians(jo_deg[2]),
                ).asQuaternion()
                self._bone_joint_orients.append(q_jo)
            else:
                self._bone_joint_orients.append(None)

        self._ik_link_slots = set()
        self._link_joint_orients = []
        for lk in links:
            slot = lk["bone_slot"]
            self._ik_link_slots.add(slot)
            jo = self._bone_joint_orients[slot] if slot < len(self._bone_joint_orients) else None
            self._link_joint_orients.append(jo)

        solver = MmdIkChain.create(
            bones=bones,
            target_bone_slot=target_slot,
            links=links,
            iteration_count=iterations,
            limit_angle=limit_angle,
        )
        if solver is None:
            om.MGlobal.displayWarning("mmdCcdIk: MmdIkChain.create returned None")
            return

        self._solver = solver
        self._chain_def = chain_json

    def _is_output_plug(self, plug):
        """outputRotate 配列階層のいずれかのプラグか判定。"""
        p = plug
        for _ in range(4):
            if p == self.aOutputRotate:
                return True
            try:
                if p.isElement:
                    p = p.array()
                elif p.isChild:
                    p = p.parent()
                else:
                    return False
            except Exception:
                return False
        return False

    def compute(self, plug, data):
        if not self._is_output_plug(plug):
            return None

        enabled = data.inputValue(self.aEnabled).asBool()
        if not enabled:
            data.setClean(plug)
            return

        chain_json = data.inputValue(self.aChainJson).asString()
        self._ensure_solver(chain_json)
        if self._solver is None:
            data.setClean(plug)
            return

        goal_x = data.inputValue(self.aGoalX).asDouble()
        goal_y = data.inputValue(self.aGoalY).asDouble()
        goal_z = -data.inputValue(self.aGoalZ).asDouble()

        this_obj = self.thisMObject()
        fn_dep = om.MFnDependencyNode(this_obj)

        # Position offsets: Maya joint.translate → MMD local offset
        # offset_mmd = [tx, ty, -tz] - rest_position_mmd
        positions = [0.0] * (self._bone_count * 3)
        it_plug = fn_dep.findPlug("inputTranslate", False)
        for bone_i in range(self._bone_count):
            try:
                elem = it_plug.elementByLogicalIndex(bone_i)
                if elem.isDestination or elem.child(0).isDestination:
                    tx = elem.child(0).asDouble()
                    ty = elem.child(1).asDouble()
                    tz = elem.child(2).asDouble()
                    rest = self._rest_positions[bone_i]
                    positions[bone_i * 3] = tx - rest[0]
                    positions[bone_i * 3 + 1] = ty - rest[1]
                    positions[bone_i * 3 + 2] = -tz - rest[2]
            except Exception:
                pass

        # Input rotations: Maya euler → MMD quaternion
        # Non-link bones: q_mmd = z_mirror(q_rotate * q_jointOrient)
        # IK link bones: excluded from inputRotate → identity
        rotations = []
        ir_plug = fn_dep.findPlug("inputRotate", False)
        for bone_i in range(self._bone_count):
            rx = ry = rz = 0.0
            try:
                elem_plug = ir_plug.elementByLogicalIndex(bone_i)
                rx = elem_plug.child(0).asDouble()
                ry = elem_plug.child(1).asDouble()
                rz = elem_plug.child(2).asDouble()
            except Exception:
                pass
            euler = om.MEulerRotation(rx, ry, rz)
            q = euler.asQuaternion()
            if bone_i not in self._ik_link_slots and bone_i < len(self._bone_joint_orients):
                q_jo = self._bone_joint_orients[bone_i]
                if q_jo is not None:
                    q = q * q_jo
            rotations.extend([-q.x, -q.y, q.z, q.w])

        result = self._solver.solve(
            positions=positions,
            rotations=rotations,
            goal=[goal_x, goal_y, goal_z],
        )
        if result is None:
            data.setClean(plug)
            return

        out_rots, stats = result

        # Output rotations: MMD quaternion → Maya joint.rotate
        # q_rotate = z_mirror(q_mmd) * jointOrient^-1
        out_array = data.outputArrayValue(self.aOutputRotate)
        builder = out_array.builder()
        for link_i in range(self._link_count):
            offset = link_i * 4
            qx = out_rots[offset]
            qy = out_rots[offset + 1]
            qz = out_rots[offset + 2]
            qw = out_rots[offset + 3]
            out_quat = om.MQuaternion(-qx, -qy, qz, qw)
            if link_i < len(self._link_joint_orients):
                q_jo = self._link_joint_orients[link_i]
                if q_jo is not None:
                    out_quat = out_quat * q_jo.inverse()
            out_euler = out_quat.asEulerRotation()

            elem_handle = builder.addElement(link_i)
            elem_handle.set3Double(out_euler.x, out_euler.y, out_euler.z)

        out_array.set(builder)
        out_array.setAllClean()
        data.setClean(plug)

    def __del__(self):
        if self._solver is not None:
            try:
                self._solver.free()
            except Exception:
                pass


def creator():
    return MmdCcdIkNode()


def initialize():
    tAttr = om.MFnTypedAttribute()
    nAttr = om.MFnNumericAttribute()
    uAttr = om.MFnUnitAttribute()
    cAttr = om.MFnCompoundAttribute()

    MmdCcdIkNode.aChainJson = tAttr.create("chainJson", "cj", om.MFnData.kString)
    tAttr.storable = True
    MmdCcdIkNode.addAttribute(MmdCcdIkNode.aChainJson)

    MmdCcdIkNode.aGoalX = nAttr.create("goalX", "gx", om.MFnNumericData.kDouble, 0.0)
    MmdCcdIkNode.aGoalY = nAttr.create("goalY", "gy", om.MFnNumericData.kDouble, 0.0)
    MmdCcdIkNode.aGoalZ = nAttr.create("goalZ", "gz", om.MFnNumericData.kDouble, 0.0)
    MmdCcdIkNode.aGoal = cAttr.create("goal", "g")
    cAttr.addChild(MmdCcdIkNode.aGoalX)
    cAttr.addChild(MmdCcdIkNode.aGoalY)
    cAttr.addChild(MmdCcdIkNode.aGoalZ)
    cAttr.keyable = True
    MmdCcdIkNode.addAttribute(MmdCcdIkNode.aGoal)

    _irx = uAttr.create("inputRotateElementX", "ierx", om.MFnUnitAttribute.kAngle, 0.0)
    _iry = uAttr.create("inputRotateElementY", "iery", om.MFnUnitAttribute.kAngle, 0.0)
    _irz = uAttr.create("inputRotateElementZ", "ierz", om.MFnUnitAttribute.kAngle, 0.0)
    MmdCcdIkNode.aInputRotate = cAttr.create("inputRotate", "ir")
    cAttr.addChild(_irx)
    cAttr.addChild(_iry)
    cAttr.addChild(_irz)
    cAttr.array = True
    MmdCcdIkNode.addAttribute(MmdCcdIkNode.aInputRotate)

    _itx = nAttr.create("inputTranslateElementX", "ietx", om.MFnNumericData.kDouble, 0.0)
    _ity = nAttr.create("inputTranslateElementY", "iety", om.MFnNumericData.kDouble, 0.0)
    _itz = nAttr.create("inputTranslateElementZ", "ietz", om.MFnNumericData.kDouble, 0.0)
    MmdCcdIkNode.aInputTranslate = cAttr.create("inputTranslate", "it_ik")
    cAttr.addChild(_itx)
    cAttr.addChild(_ity)
    cAttr.addChild(_itz)
    cAttr.array = True
    MmdCcdIkNode.addAttribute(MmdCcdIkNode.aInputTranslate)

    _orx = uAttr.create("outputRotateElementX", "oerx", om.MFnUnitAttribute.kAngle, 0.0)
    uAttr.writable = False
    uAttr.storable = False
    _ory = uAttr.create("outputRotateElementY", "oery", om.MFnUnitAttribute.kAngle, 0.0)
    uAttr.writable = False
    uAttr.storable = False
    _orz = uAttr.create("outputRotateElementZ", "oerz", om.MFnUnitAttribute.kAngle, 0.0)
    uAttr.writable = False
    uAttr.storable = False
    MmdCcdIkNode.aOutputRotate = cAttr.create("outputRotate", "or_ik")
    cAttr.writable = False
    cAttr.storable = False
    cAttr.addChild(_orx)
    cAttr.addChild(_ory)
    cAttr.addChild(_orz)
    cAttr.array = True
    cAttr.usesArrayDataBuilder = True
    MmdCcdIkNode.addAttribute(MmdCcdIkNode.aOutputRotate)

    MmdCcdIkNode.aEnabled = nAttr.create("enabled", "en", om.MFnNumericData.kBoolean, True)
    MmdCcdIkNode.addAttribute(MmdCcdIkNode.aEnabled)

    MmdCcdIkNode.attributeAffects(MmdCcdIkNode.aChainJson, MmdCcdIkNode.aOutputRotate)
    MmdCcdIkNode.attributeAffects(MmdCcdIkNode.aGoal, MmdCcdIkNode.aOutputRotate)
    MmdCcdIkNode.attributeAffects(MmdCcdIkNode.aInputRotate, MmdCcdIkNode.aOutputRotate)
    MmdCcdIkNode.attributeAffects(MmdCcdIkNode.aInputTranslate, MmdCcdIkNode.aOutputRotate)
    MmdCcdIkNode.attributeAffects(MmdCcdIkNode.aEnabled, MmdCcdIkNode.aOutputRotate)


def register(plugin_fn):
    if not is_rig_primitive_available():
        om.MGlobal.displayWarning("mmd-anim DLL not available — mmdCcdIk node not registered")
        return
    plugin_fn.registerNode(
        MmdCcdIkNode.kTypeName,
        MmdCcdIkNode.kTypeId,
        creator,
        initialize,
        om.MPxNode.kDependNode,
        MmdCcdIkNode.kClassify,
    )


def deregister(plugin_fn):
    try:
        plugin_fn.deregisterNode(MmdCcdIkNode.kTypeId)
    except Exception:
        pass
