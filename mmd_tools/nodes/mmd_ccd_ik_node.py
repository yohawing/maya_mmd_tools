"""mmdCcdIk — MMD CCD-IK ソルバー DG ノード (Python MPxNode prototype).

mmd-anim FFI の MmdIkChain を内部で呼び出し、
ゴール位置に向かって IK リンクの回転を解く。

チェーン定義は chainJson 属性で設定（import 時に1回書き込む）。
per-frame は goal (ターゲット位置) + inputRotate[] (全ボーンのローカル回転)。

solve するかどうかは「FK input pose から計算した target ボーンの world 位置が
goal と一致しているか」で判定する。一致していれば pass-through（VMD bake 済み
final pose の二重 solve 防止）、ズレていれば solve。腰などチェーン祖先の移動は
target だけを動かすので solve が走り、全ての親の移動は target と goal を一緒に
動かすので pass-through のまま — MMD のセマンティクスと一致する。

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
from ctypes import c_float

import maya.api.OpenMaya as om

from mmd_tools.core.native.mmd_anim_runtime import MmdIkChain, is_rig_primitive_available


# Keep this contract in sync with cpp/src/MmdCcdIkNode.cpp.  This grid is
# roughly 1e-4 degrees at quaternion scale: large enough to absorb the
# sub-microdegree Euler noise introduced by VMD export/reimport, but far below
# the native solver's meaningful angular accuracy.
_RUNTIME_QUATERNION_QUANTUM = c_float(1.0e-6).value
_RUNTIME_POSITION_QUANTUM = c_float(2.0e-6).value


def _canonicalize_runtime_position(value):
    """Return one deterministic float position for the native IK input."""
    value_f32 = c_float(value).value
    if not math.isfinite(value_f32):
        return value_f32
    scaled = float(value_f32) / float(_RUNTIME_POSITION_QUANTUM)
    cell = math.floor(scaled + 0.5) if scaled >= 0.0 else math.ceil(scaled - 0.5)
    snapped = c_float(cell * float(_RUNTIME_POSITION_QUANTUM)).value
    return 0.0 if snapped == 0.0 else snapped


def _canonicalize_runtime_quaternion(values):
    """Return one deterministic float quaternion for the native IK input."""
    quaternion = [c_float(value).value for value in values]
    if quaternion[3] < 0.0:
        quaternion = [-value for value in quaternion]
    snapped = []
    for value in quaternion:
        scaled = c_float(value / _RUNTIME_QUATERNION_QUANTUM).value
        snapped.append(
            c_float(round(scaled) * _RUNTIME_QUATERNION_QUANTUM).value
        )
    length_squared = sum(float(value) * float(value) for value in snapped)
    if length_squared <= 0.0:
        return [0.0, 0.0, 0.0, 1.0]
    inverse_length = c_float(1.0 / math.sqrt(length_squared)).value
    return [c_float(value * inverse_length).value for value in snapped]


def maya_useNewAPI():
    pass


class MmdCcdIkNode(om.MPxNode):
    kTypeName = "mmdCcdIk"
    kTypeId = om.MTypeId(0x00128002)
    kClassify = "utility/general"

    # FK target と goal の一致判定 (MMD units)。VMD bake の Euler/animCurve
    # 丸め誤差より十分大きく、目視で分かる足のズレより十分小さい値。
    GOAL_MATCH_EPSILON = 1e-3

    aChainJson = None

    aGoal = None
    aGoalX = None
    aGoalY = None
    aGoalZ = None
    aGoalWorldMatrix = None

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
        self._controller_slot = -1
        self._target_slot = -1
        self._rest_positions = []
        self._maya_rest_translates = []
        self._parent_slots = []
        self._bone_joint_orients = []
        self._maya_bind_world_matrices = []
        self._no_orient_bind_world_matrices = []
        self._link_joint_orients = []
        self._link_slots = []
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
        self._controller_slot = int(cfg.get("controllerBoneSlot", -1))
        target_slot = cfg.get("targetBoneSlot", 0)
        self._target_slot = int(target_slot)
        iterations = cfg.get("iterationCount", 40)
        limit_angle = cfg.get("limitAngle", 0.0628)

        self._bone_count = len(bones)
        self._link_count = len(links)

        self._rest_positions = [b.get("rest_position", [0, 0, 0]) for b in bones]
        self._maya_rest_translates = [
            b.get("maya_rest_translate", b.get("rest_position", [0, 0, 0]))
            for b in bones
        ]
        self._parent_slots = [int(b.get("parent_slot", -1)) for b in bones]
        self._maya_bind_world_matrices = [
            self._matrix_from_json(b.get("maya_bind_world_matrix"))
            for b in bones
        ]
        self._no_orient_bind_world_matrices = [
            self._matrix_from_json(b.get("no_orient_bind_world_matrix"))
            for b in bones
        ]

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
        self._link_slots = []
        self._link_joint_orients = []
        for lk in links:
            slot = lk["bone_slot"]
            self._link_slots.append(slot)
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
            try:
                if p.attribute() == self.aOutputRotate:
                    return True
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

        chain_json = data.inputValue(self.aChainJson).asString()
        self._ensure_solver(chain_json)

        enabled = data.inputValue(self.aEnabled).asBool()
        if not enabled:
            self._copy_input_rotate_to_output(data, plug)
            return

        if self._solver is None:
            data.setClean(plug)
            return

        goal_x, goal_y, goal_z = self._read_goal(data)

        this_obj = self.thisMObject()
        fn_dep = om.MFnDependencyNode(this_obj)

        maya_translates = []
        it_plug = fn_dep.findPlug("inputTranslate", False)
        for bone_i in range(self._bone_count):
            maya_rest = self._maya_rest_translates[bone_i]
            tx, ty, tz = maya_rest[0], maya_rest[1], maya_rest[2]
            try:
                elem = it_plug.elementByLogicalIndex(bone_i)
                if elem.isDestination or elem.child(0).isDestination:
                    tx = elem.child(0).asDouble()
                    ty = elem.child(1).asDouble()
                    tz = elem.child(2).asDouble()
            except Exception:
                pass
            maya_translates.append((tx, ty, tz))

        maya_rotate_eulers = []
        ir_plug = fn_dep.findPlug("inputRotate", False)
        for bone_i in range(self._bone_count):
            rx = ry = rz = 0.0
            try:
                elem_plug = ir_plug.elementByLogicalIndex(bone_i)
                if elem_plug.isDestination or elem_plug.child(0).isDestination:
                    rx = elem_plug.child(0).asDouble()
                    ry = elem_plug.child(1).asDouble()
                    rz = elem_plug.child(2).asDouble()
            except Exception:
                pass
            maya_rotate_eulers.append(om.MEulerRotation(rx, ry, rz))

        # Convert the current Maya input pose back to the PMX/no-JO local space
        # expected by the native MMD IK solver.  The connected joints may already
        # contain bind-space-corrected JO values from runtime VMD import, so
        # reading translate/rotate as raw MMD local deltas is not valid.
        positions, rotations = self._solver_pose_from_maya_inputs(
            maya_translates,
            maya_rotate_eulers,
        )
        positions = [_canonicalize_runtime_position(value) for value in positions]

        use_controller_goal = (
            0 <= self._controller_slot < self._bone_count
            and not self._goal_has_input_connection()
        )
        if use_controller_goal:
            goal_x, goal_y, goal_z = self._compute_pre_ik_goal(positions, rotations)
        goal_x, goal_y, goal_z = (
            _canonicalize_runtime_position(value)
            for value in (goal_x, goal_y, goal_z)
        )

        # Pass-through gate: FK input pose が target を既に goal 上に置いている
        # なら solve しない（VMD bake 済み final pose の二重 solve 防止）。
        # ズレていれば solve する — 腰などチェーン祖先の移動は target だけを
        # 動かすので solve が走り、全ての親は target と goal を一緒に動かす
        # ので pass-through のまま。
        if self._fk_target_matches_goal(positions, rotations, goal_x, goal_y, goal_z):
            self._copy_input_rotate_to_output(data, plug)
            return

        result = self._solver.solve(
            positions=positions,
            rotations=rotations,
            goal=[goal_x, goal_y, goal_z],
        )
        if result is None:
            data.setClean(plug)
            return

        out_rots, _stats = result

        # Output rotations: MMD quaternion → Maya joint.rotate.
        #
        # The native IK primitive solves in PMX/no-JO space.  A generated Maya
        # skeleton with jointOrient has different bind worlds, so the solved
        # world matrices must go through the same bind-space conversion used by
        # runtime VMD bake: B_maya * inverse(B_noJO) * W_mmd.
        solved_worlds = self._compute_solved_maya_worlds(positions, rotations, out_rots)
        out_array = data.outputArrayValue(self.aOutputRotate)
        builder = out_array.builder()
        for link_i in range(self._link_count):
            slot = self._link_slots[link_i] if link_i < len(self._link_slots) else -1
            out_euler = self._output_euler_from_solved_world(slot, solved_worlds)
            if out_euler is None:
                offset = link_i * 4
                qx = out_rots[offset]
                qy = out_rots[offset + 1]
                qz = out_rots[offset + 2]
                qw = out_rots[offset + 3]
                out_quat = om.MQuaternion(-qx, -qy, qz, qw)
                out_euler = out_quat.asEulerRotation()

            elem_handle = builder.addElement(link_i)
            elem_handle.set3Double(out_euler.x, out_euler.y, out_euler.z)

        out_array.set(builder)
        out_array.setAllClean()
        data.setClean(plug)

    @staticmethod
    def _matrix_from_json(values):
        if not isinstance(values, (list, tuple)) or len(values) != 16:
            return None
        try:
            return om.MMatrix([float(v) for v in values])
        except Exception:
            return None

    @staticmethod
    def _mmd_world_to_maya(matrix: om.MMatrix) -> om.MMatrix:
        signs = (1.0, 1.0, -1.0)
        values = [float(matrix[i]) for i in range(16)]
        for row in range(3):
            for col in range(3):
                idx = row * 4 + col
                values[idx] *= signs[row] * signs[col]
        for col in range(3):
            values[12 + col] *= signs[col]
        return om.MMatrix(values)

    @classmethod
    def _maya_world_to_mmd(cls, matrix: om.MMatrix) -> om.MMatrix:
        return cls._mmd_world_to_maya(matrix)

    def _maya_goal_matrix_to_mmd_point(self, matrix: om.MMatrix):
        """Convert a Maya-space goal through root-relative bind correction.

        ``inputTranslate`` is sampled from joint-local values, while
        ``goalWorldMatrix`` is a world-space controller plug.  Normalize the
        latter by the imported model root before applying the static bind/J-O
        conversion; otherwise moving the model root makes the solver see a
        goal offset that is not present in its root-relative input pose.
        """
        goal_matrix = matrix
        root_world = self._connected_goal_model_root_world_matrix()
        if root_world is not None:
            try:
                goal_matrix = goal_matrix * root_world.inverse()
            except Exception:
                pass
        slot = self._controller_slot
        if 0 <= slot < self._bone_count:
            bind_world = (
                self._maya_bind_world_matrices[slot]
                if slot < len(self._maya_bind_world_matrices)
                else None
            )
            bind_no_orient = (
                self._no_orient_bind_world_matrices[slot]
                if slot < len(self._no_orient_bind_world_matrices)
                else None
            )
            if bind_world is not None and bind_no_orient is not None:
                goal_matrix = bind_no_orient * bind_world.inverse() * goal_matrix

        mmd_matrix = self._maya_world_to_mmd(goal_matrix)
        point = om.MTransformationMatrix(mmd_matrix).translation(om.MSpace.kWorld)
        return point.x, point.y, point.z

    def _connected_goal_model_root_world_matrix(self):
        """Return the ``*_root`` ancestor of a connected goal DAG node.

        Unit tests commonly connect a top-level locator as an external goal;
        that has no model-root ancestor and deliberately returns ``None``.
        Production MMD controllers live below the imported ``Model_root`` DAG
        node, whose inclusive matrix is the transform omitted by local IK
        inputs.
        """
        try:
            fn_dep = om.MFnDependencyNode(self.thisMObject())
            matrix_plug = fn_dep.findPlug("goalWorldMatrix", False)
            sources = matrix_plug.connectedTo(True, False)
            if not sources:
                return None
            dag_path = om.MDagPath.getAPathTo(sources[0].node())
            for _ in range(max(0, dag_path.length() - 1)):
                dag_path.pop()
                leaf = dag_path.fullPathName().rsplit("|", 1)[-1].lower()
                if leaf.endswith("_root") or leaf.endswith("root"):
                    return dag_path.inclusiveMatrix()
        except Exception:
            return None
        return None

    def _compute_solved_maya_worlds(self, positions, input_rotations, out_rots):
        world_mmd = [om.MMatrix() for _ in range(self._bone_count)]
        solved_rotations = list(input_rotations)
        for link_i, slot in enumerate(self._link_slots):
            if 0 <= slot < self._bone_count:
                src = link_i * 4
                dst = slot * 4
                solved_rotations[dst:dst + 4] = out_rots[src:src + 4]

        for bone_i in range(self._bone_count):
            rest = self._rest_positions[bone_i]
            local_t = om.MVector(
                rest[0] + positions[bone_i * 3],
                rest[1] + positions[bone_i * 3 + 1],
                rest[2] + positions[bone_i * 3 + 2],
            )
            q_off = bone_i * 4
            quat = om.MQuaternion(
                solved_rotations[q_off],
                solved_rotations[q_off + 1],
                solved_rotations[q_off + 2],
                solved_rotations[q_off + 3],
            )
            local_tfm = om.MTransformationMatrix()
            local_tfm.setTranslation(local_t, om.MSpace.kTransform)
            local_tfm.setRotation(quat)
            local_m = local_tfm.asMatrix()
            parent = self._parent_slots[bone_i] if bone_i < len(self._parent_slots) else -1
            world_mmd[bone_i] = local_m * world_mmd[parent] if 0 <= parent < bone_i else local_m

        maya_worlds = {}
        for bone_i, mmd_world in enumerate(world_mmd):
            runtime_world = self._mmd_world_to_maya(mmd_world)
            bind_world = self._maya_bind_world_matrices[bone_i] if bone_i < len(self._maya_bind_world_matrices) else None
            bind_no_orient = (
                self._no_orient_bind_world_matrices[bone_i]
                if bone_i < len(self._no_orient_bind_world_matrices)
                else None
            )
            if bind_world is not None and bind_no_orient is not None:
                maya_worlds[bone_i] = bind_world * bind_no_orient.inverse() * runtime_world
            else:
                maya_worlds[bone_i] = runtime_world
        return maya_worlds

    def _solver_pose_from_maya_inputs(self, maya_translates, maya_rotate_eulers):
        positions = [0.0] * (self._bone_count * 3)
        rotations = [0.0] * (self._bone_count * 4)
        if not (
            self._maya_bind_world_matrices
            and self._no_orient_bind_world_matrices
            and len(self._maya_bind_world_matrices) >= self._bone_count
            and len(self._no_orient_bind_world_matrices) >= self._bone_count
        ):
            for bone_i, euler in enumerate(maya_rotate_eulers):
                maya_rest = self._maya_rest_translates[bone_i]
                tx, ty, tz = maya_translates[bone_i]
                positions[bone_i * 3] = tx - maya_rest[0]
                positions[bone_i * 3 + 1] = ty - maya_rest[1]
                positions[bone_i * 3 + 2] = -(tz - maya_rest[2])
                q = euler.asQuaternion()
                q_off = bone_i * 4
                rotations[q_off:q_off + 4] = _canonicalize_runtime_quaternion(
                    [-q.x, -q.y, q.z, q.w]
                )
            return positions, rotations

        maya_worlds = [om.MMatrix() for _ in range(self._bone_count)]
        mmd_worlds = [om.MMatrix() for _ in range(self._bone_count)]
        for bone_i in range(self._bone_count):
            local_tfm = om.MTransformationMatrix()
            tx, ty, tz = maya_translates[bone_i]
            local_tfm.setTranslation(om.MVector(tx, ty, tz), om.MSpace.kTransform)
            q_total = maya_rotate_eulers[bone_i].asQuaternion()
            if bone_i < len(self._bone_joint_orients):
                q_jo = self._bone_joint_orients[bone_i]
                if q_jo is not None:
                    q_total = q_total * q_jo
            local_tfm.setRotation(q_total)
            local_maya = local_tfm.asMatrix()

            parent = self._parent_slots[bone_i] if bone_i < len(self._parent_slots) else -1
            maya_world = local_maya * maya_worlds[parent] if 0 <= parent < bone_i else local_maya
            maya_worlds[bone_i] = maya_world

            bind_world = self._maya_bind_world_matrices[bone_i]
            bind_no_orient = self._no_orient_bind_world_matrices[bone_i]
            if bind_world is not None and bind_no_orient is not None:
                runtime_world = bind_no_orient * bind_world.inverse() * maya_world
            else:
                runtime_world = maya_world
            mmd_worlds[bone_i] = self._maya_world_to_mmd(runtime_world)

        for bone_i in range(self._bone_count):
            parent = self._parent_slots[bone_i] if bone_i < len(self._parent_slots) else -1
            parent_world = mmd_worlds[parent] if 0 <= parent < bone_i else None
            local_mmd = mmd_worlds[bone_i] * parent_world.inverse() if parent_world is not None else mmd_worlds[bone_i]
            local_tfm = om.MTransformationMatrix(local_mmd)
            local_t = local_tfm.translation(om.MSpace.kTransform)
            rest = self._rest_positions[bone_i]
            positions[bone_i * 3] = float(local_t.x) - float(rest[0])
            positions[bone_i * 3 + 1] = float(local_t.y) - float(rest[1])
            positions[bone_i * 3 + 2] = float(local_t.z) - float(rest[2])

            q = local_tfm.rotation(asQuaternion=True)
            q_off = bone_i * 4
            rotations[q_off:q_off + 4] = _canonicalize_runtime_quaternion(
                [q.x, q.y, q.z, q.w]
            )

        return positions, rotations

    def _output_euler_from_solved_world(self, slot: int, solved_worlds):
        if slot < 0 or slot not in solved_worlds:
            return None
        parent_slot = self._parent_slots[slot] if slot < len(self._parent_slots) else -1
        parent_world = solved_worlds.get(parent_slot) if parent_slot >= 0 else None
        world = solved_worlds[slot]
        local = world * parent_world.inverse() if parent_world is not None else world
        quat = om.MTransformationMatrix(local).rotation(asQuaternion=True)
        if slot < len(self._bone_joint_orients):
            q_jo = self._bone_joint_orients[slot]
            if q_jo is not None:
                quat = quat * q_jo.inverse()
        return quat.asEulerRotation()

    def _copy_input_rotate_to_output(self, data, plug):
        """IK disabled / pass-through 時に link joint の FK/VMD 回転を保持する。

        gate の FK 計算は接続済み (isDestination) の inputRotate 要素だけを
        読むのに対し、ここは生の attr 値をそのままコピーする（未接続で
        setAttr された link 回転は gate に見えないが出力には残る、という
        旧来からの非対称を維持している）。
        """
        out_array = data.outputArrayValue(self.aOutputRotate)
        builder = out_array.builder()
        fn_dep = om.MFnDependencyNode(self.thisMObject())
        input_plug = fn_dep.findPlug("inputRotate", False)

        for link_i, slot in enumerate(self._link_slots):
            rx = ry = rz = 0.0
            try:
                elem_plug = input_plug.elementByLogicalIndex(slot)
                rx = elem_plug.child(0).asDouble()
                ry = elem_plug.child(1).asDouble()
                rz = elem_plug.child(2).asDouble()
            except Exception:
                pass

            elem_handle = builder.addElement(link_i)
            elem_handle.set3Double(rx, ry, rz)

        out_array.set(builder)
        out_array.setAllClean()
        data.setClean(plug)

    def _goal_has_input_connection(self) -> bool:
        try:
            fn_dep = om.MFnDependencyNode(self.thisMObject())
            matrix_plug = fn_dep.findPlug("goalWorldMatrix", False)
            if matrix_plug.connectedTo(True, False):
                return True
            goal_plug = fn_dep.findPlug("goal", False)
            if goal_plug.connectedTo(True, False):
                return True
            for child_index in range(goal_plug.numChildren()):
                if goal_plug.child(child_index).connectedTo(True, False):
                    return True
        except Exception:
            return False
        return False

    def _read_goal(self, data):
        """Read public Maya-space goal inputs and convert to solver MMD space."""
        try:
            matrix_data = data.inputValue(self.aGoalWorldMatrix).asMatrix()
            if self._goal_world_matrix_has_input_connection():
                return self._maya_goal_matrix_to_mmd_point(matrix_data)
        except Exception:
            pass
        goal_tfm = om.MTransformationMatrix()
        goal_tfm.setTranslation(
            om.MVector(
                data.inputValue(self.aGoalX).asDouble(),
                data.inputValue(self.aGoalY).asDouble(),
                data.inputValue(self.aGoalZ).asDouble(),
            ),
            om.MSpace.kTransform,
        )
        return self._maya_goal_matrix_to_mmd_point(goal_tfm.asMatrix())

    def _goal_world_matrix_has_input_connection(self) -> bool:
        try:
            matrix_plug = om.MFnDependencyNode(self.thisMObject()).findPlug("goalWorldMatrix", False)
            return bool(matrix_plug.connectedTo(True, False))
        except Exception:
            return False

    def _fk_target_matches_goal(
        self, positions, rotations, goal_x: float, goal_y: float, goal_z: float
    ) -> bool:
        """FK input pose の target world 位置が goal と一致しているか。"""
        if not (0 <= self._target_slot < self._bone_count):
            return False
        tx, ty, tz = self._fk_world_position(self._target_slot, positions, rotations)
        return (
            abs(tx - goal_x) <= self.GOAL_MATCH_EPSILON
            and abs(ty - goal_y) <= self.GOAL_MATCH_EPSILON
            and abs(tz - goal_z) <= self.GOAL_MATCH_EPSILON
        )

    def _compute_pre_ik_goal(self, positions, rotations):
        """input pose だけから controller bone の pre-IK world 位置を得る。"""
        return self._fk_world_position(self._controller_slot, positions, rotations)

    def _fk_world_position(self, slot: int, positions, rotations):
        """input pose だけから指定 slot の FK world 位置 (MMD space) を得る。"""
        world_mats = [om.MMatrix() for _ in range(self._bone_count)]
        for bone_i in range(self._bone_count):
            rest = self._rest_positions[bone_i]
            local_t = om.MVector(
                rest[0] + positions[bone_i * 3],
                rest[1] + positions[bone_i * 3 + 1],
                rest[2] + positions[bone_i * 3 + 2],
            )
            q_off = bone_i * 4
            quat = om.MQuaternion(
                rotations[q_off],
                rotations[q_off + 1],
                rotations[q_off + 2],
                rotations[q_off + 3],
            )
            local_tfm = om.MTransformationMatrix()
            local_tfm.setRotation(quat)
            local_tfm.setTranslation(local_t, om.MSpace.kTransform)
            local_mat = local_tfm.asMatrix()
            parent_slot = self._parent_slots[bone_i] if bone_i < len(self._parent_slots) else -1
            world_mats[bone_i] = local_mat * world_mats[parent_slot] if 0 <= parent_slot < bone_i else local_mat

        pos = om.MTransformationMatrix(world_mats[slot]).translation(om.MSpace.kWorld)
        return pos.x, pos.y, pos.z

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

    mAttr = om.MFnMatrixAttribute()
    MmdCcdIkNode.aGoalWorldMatrix = mAttr.create("goalWorldMatrix", "gwm")
    mAttr.storable = False
    MmdCcdIkNode.addAttribute(MmdCcdIkNode.aGoalWorldMatrix)

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
    MmdCcdIkNode.attributeAffects(MmdCcdIkNode.aGoalWorldMatrix, MmdCcdIkNode.aOutputRotate)
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
