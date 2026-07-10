"""
MMD物理データをMaya BulletまたはnClothに変換するモジュール。

Bullet プラグインが利用可能な場合は Maya Bullet (bulletRigidBodyShape /
bulletRigidBodyConstraintShape) 経路を使い、利用不可の場合は既存の
Nucleus/nCloth fallback 経路に graceful fallback する。
"""

import math
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union

import maya.cmds as cmds

from ..core import maya_attribute_utils, maya_name_utils, maya_physics_utils, maya_scene_utils
from ..core.constants import (
    ATTR_MMD_BONE_INDEX,
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_BONE_NAME_EN,
    CONSTRAINTS_GROUP,
    PHYSICS_GROUP,
    PHYSICS_TYPE_CLOTH,
    PHYSICS_TYPE_HAIR,
    PHYSICS_TYPE_RIGID,
    PHYSICS_TYPE_SOFT,
    RIGID_BODIES_GROUP,
    SKELETON_GROUP,
)
from ..adapters.maya_cmds_adapter import MayaCmdsAdapter
from ..core.coordinate_transform import (
    maya_point_to_mmd,
    mmd_point_to_maya,
)
from ..core.logger import get_logger, safe_log_error
from ..core.pmd_data.rigid_body import PmdRigidBody
from ..core.pmx_data.rigid_body import PmxRigidBody
from ..core.visibility_state import (
    connect_visibility_attr_to_node,
    ensure_visibility_attrs,
)

# ---------------------------------------------------------------------------
# Maya Bullet の enum 値 (Maya 2024 mayapy 実機確認)
# ---------------------------------------------------------------------------
# bulletRigidBodyShape.colliderShapeType: box=1, sphere=2, capsule=3, ...
# bulletRigidBodyShape.bodyType: Static Body=0, Kinematic RigidBody=1, Dynamic RigidBody=2
# bulletRigidBodyConstraintShape.constraintType: Point=0,Hinge=1,Slider=2,ConeTwist=3,SixDOF=4,SpringSixDOF=5,SpringHinge=6
# bulletRigidBodyConstraintShape.linearConstraint{X,Y,Z}: Free=0, Locked=1, Limited=2

_BULLET_PLUGIN = "bullet"

# MMD shape_type → Bullet colliderShapeType
# MMD: 0=Sphere, 1=Box, 2=Capsule
_MMD_SHAPE_TO_BULLET = {0: 2, 1: 1, 2: 3}

# MMD physics_mode → Bullet bodyType
# 0=ボーン追従(kinematic), 1=物理演算(dynamic), 2=物理+位置合わせ(dynamic)
_MMD_MODE_TO_BULLET = {0: 1, 1: 2, 2: 2}

# Bullet bodyType → MMD physics_mode (逆変換)
_BULLET_MODE_TO_MMD = {0: 0, 1: 0, 2: 1}

# Bullet colliderShapeType → MMD shape_type (逆変換)
_BULLET_SHAPE_TO_MMD = {1: 1, 2: 0, 3: 2}

# PMX joint_type → Bullet constraintType (暫定)
_PMX_JOINT_TO_BULLET = {0: 4, 1: 5, 2: 0, 3: 3, 4: 2, 5: 1}

# linear/angularConstraint{X,Y,Z} enum: Free=0, Locked=1, Limited=2
_CONSTRAINT_FREE = 0
_CONSTRAINT_LOCKED = 1
_CONSTRAINT_LIMITED = 2
_Z_REFLECTION_MATRIX = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, -1.0))
_RIGID_BODY_LOCATOR_TYPE = "mmdRigidBodyLocator"

_OPT_ENABLE_HAIR_PHYSICS = "enable_hair_physics"
_OPT_ENABLE_CLOTH_PHYSICS = "enable_cloth_physics"
_OPT_SIMULATION_QUALITY = "simulation_quality"
_OPT_AUTO_DETECT_TYPE = "auto_detect_type"
_OPT_SOLVER_ITERATIONS = "solver_iterations"
_OPT_SUBSTEPS = "substeps"
_OPT_START_FRAME = "start_frame"
_OPT_TIME_SCALE = "time_scale"
_OPT_GRAVITY = "gravity"
_OPT_BULLET_FIXED_FRAME_RATE = "bullet_fixed_frame_rate"
_OPT_SPLIT_IMPULSE = "split_impulse"
_OPT_CREATE_PHYSICS_JOINTS = "create_physics_joints"
_OPT_SCALE = "scale"
_MODEL_MOTION_PARENT_NAMES = {"master", "全ての親", "マスター"}
_PREVIEW_CONSTRAINT_ATTR = "mmd_physics_preview_constraint"
_NATIVE_BAKE_DISABLED_ATTR = "mmd_native_physics_bake_disabled"
_NATIVE_BAKE_PREVIOUS_NODE_STATE_ATTR = "mmd_native_physics_bake_previous_node_state"
_NODE_STATE_BLOCKING = 2
# Persistent per-model preference for the full legacy Maya Bullet setup under Root.
# Native physics bake exclusion is temporary feedback-only and must not overwrite this.
_LEGACY_BULLET_ENABLED_ATTR = "mmd_legacy_bullet_enabled"
# Per-node metadata used when Root.mmd_legacy_bullet_enabled suspends the legacy setup.
# Named separately from mmd_native_physics_bake_* so bake exclusion cannot re-enable
# nodes the user turned off, and cannot touch rigid bodies / Bullet joints.
_LEGACY_BULLET_DISABLED_ATTR = "mmd_legacy_bullet_disabled"
_LEGACY_BULLET_PREVIOUS_NODE_STATE_ATTR = "mmd_legacy_bullet_previous_node_state"
# Bullet solver evaluation nodes suspended by legacy OFF (never bulletSolver itself).
_LEGACY_BULLET_SOLVER_NODE_TYPES = (
    "bulletRigidBodyShape",
    "bulletRigidBodyConstraintShape",
)
# Live Attribute Editor control: root long path → scriptJob id.
# killWithScene jobs die with the scene; this map keeps registration idempotent.
_LEGACY_BULLET_WATCH_JOBS: Dict[str, int] = {}


def _has_nonzero_spring(*spring_vectors) -> bool:
    """Return whether any joint spring vector contains a non-zero value."""
    for spring_vector in spring_vectors:
        if not spring_vector:
            continue
        for value in spring_vector:
            try:
                if float(value) != 0.0:
                    return True
            except (TypeError, ValueError):
                continue
    return False


def _resolve_pmx_bullet_constraint_type(
    pmx_joint_type: int,
    spring_trans: Optional[Tuple[float, float, float]] = None,
    spring_rot: Optional[Tuple[float, float, float]] = None,
) -> int:
    """Choose the Bullet preview constraint type while preserving PMX metadata."""
    bullet_constraint_type = _PMX_JOINT_TO_BULLET.get(pmx_joint_type, 4)
    if bullet_constraint_type == 4 and _has_nonzero_spring(spring_trans, spring_rot):
        return 5
    return bullet_constraint_type


def _mat3_mul(a, b):
    return tuple(
        tuple(sum(float(a[row][k]) * float(b[k][col]) for k in range(3)) for col in range(3))
        for row in range(3)
    )


def _euler_xyz_radians_to_matrix(euler_xyz: Tuple[float, float, float]):
    """Return a row-major rotation matrix for XYZ Euler channels."""
    x, y, z = (float(euler_xyz[0]), float(euler_xyz[1]), float(euler_xyz[2]))
    cx, sx = math.cos(x), math.sin(x)
    cy, sy = math.cos(y), math.sin(y)
    cz, sz = math.cos(z), math.sin(z)
    rx = ((1.0, 0.0, 0.0), (0.0, cx, -sx), (0.0, sx, cx))
    ry = ((cy, 0.0, sy), (0.0, 1.0, 0.0), (-sy, 0.0, cy))
    rz = ((cz, -sz, 0.0), (sz, cz, 0.0), (0.0, 0.0, 1.0))
    return _mat3_mul(rz, _mat3_mul(ry, rx))


def _matrix_to_euler_xyz_radians(matrix) -> Tuple[float, float, float]:
    """Extract XYZ Euler channels from a row-major rotation matrix."""
    sy = max(-1.0, min(1.0, -float(matrix[2][0])))
    y = math.asin(sy)
    cy = math.cos(y)
    if abs(cy) > 1.0e-8:
        x = math.atan2(float(matrix[2][1]), float(matrix[2][2]))
        z = math.atan2(float(matrix[1][0]), float(matrix[0][0]))
    else:
        x = 0.0
        z = math.atan2(-float(matrix[0][1]), float(matrix[1][1]))
    return (x, y, z)


def _mmd_euler_radians_to_maya_degrees(euler_xyz: Tuple[float, float, float]) -> Tuple[float, float, float]:
    """Convert MMD rigid-body Euler radians to Maya Euler degrees via matrix reflection."""
    mmd_matrix = _euler_xyz_radians_to_matrix(euler_xyz)
    maya_matrix = _mat3_mul(_Z_REFLECTION_MATRIX, _mat3_mul(mmd_matrix, _Z_REFLECTION_MATRIX))
    return tuple(math.degrees(value) for value in _matrix_to_euler_xyz_radians(maya_matrix))


def _maya_euler_degrees_to_mmd_radians(euler_xyz: Tuple[float, float, float]) -> Tuple[float, float, float]:
    """Convert Maya Euler degrees to MMD rigid-body Euler radians via matrix reflection."""
    maya_radians = tuple(math.radians(float(value)) for value in euler_xyz)
    maya_matrix = _euler_xyz_radians_to_matrix(maya_radians)
    mmd_matrix = _mat3_mul(_Z_REFLECTION_MATRIX, _mat3_mul(maya_matrix, _Z_REFLECTION_MATRIX))
    return _matrix_to_euler_xyz_radians(mmd_matrix)


def _mmd_collision_group_to_bullet_filter_group(collision_group: int) -> int:
    """Convert PMX/PMD collision group index to Bullet's filter bit."""
    try:
        group = int(collision_group)
    except (TypeError, ValueError):
        group = 0
    return 1 << max(0, min(group, 15))


def _mmd_collision_mask_to_bullet_filter_mask(collision_mask: int) -> int:
    """Convert PMX/PMD collision mask to Bullet's collide-with bit mask.

    Despite the PMX spec naming (非衝突グループフラグ), the field is a positive
    collide-with mask: bit N set means this body collides with group N.
    All reference implementations (nanoem, babylon-mmd, saba, three-mmd) pass
    the raw value directly to Bullet.
    """
    try:
        mask = int(collision_mask)
    except (TypeError, ValueError):
        mask = 0xFFFF
    return max(0, min(mask, 0xFFFF)) & 0xFFFF


def _clamp_to_range(
    value: float,
    min_val: Optional[float] = None,
    max_val: Optional[float] = None,
) -> float:
    """
    値を [min_val, max_val] の範囲にクランプする純粋関数。

    min_val / max_val が None の場合、その側の制限は適用しない。
    Maya 非依存なのでユニットテストから直接検証できる。

    Args:
        value: 入力値
        min_val: 下限（None なら無制限）
        max_val: 上限（None なら無制限）

    Returns:
        float: クランプ後の値
    """
    result = float(value)
    if min_val is not None:
        result = max(result, float(min_val))
    if max_val is not None:
        result = min(result, float(max_val))
    return result


class PhysicsConverter:
    """
    MMD の物理演算データを Maya の物理システムに変換する。

    Bullet プラグインがロード可能な場合は Maya Bullet 経路 (bulletRigidBodyShape /
    bulletRigidBodyConstraintShape) を使い、失敗時は既存の Nucleus/nCloth
    fallback へ warning を出して切り替える。
    """

    def __init__(self, settings: Optional[Dict] = None):
        """
        PhysicsConverter を初期化する。

        Args:
            settings: 物理設定辞書。
                create_physics_joints (bool): True の場合ジョイントも作成する (default True)。
        """
        self.logger = get_logger(__class__.__name__)

        self._default_settings: Dict = {
            _OPT_ENABLE_HAIR_PHYSICS: True,
            _OPT_ENABLE_CLOTH_PHYSICS: True,
            _OPT_SIMULATION_QUALITY: "medium",
            _OPT_AUTO_DETECT_TYPE: True,
            _OPT_SOLVER_ITERATIONS: 10,
            _OPT_SUBSTEPS: 3,
            _OPT_START_FRAME: 1,
            _OPT_TIME_SCALE: 1.0,
            _OPT_GRAVITY: 98.0,
            _OPT_BULLET_FIXED_FRAME_RATE: 120,
            _OPT_SPLIT_IMPULSE: True,
            _OPT_CREATE_PHYSICS_JOINTS: True,
            _OPT_SCALE: 1.0,
        }

        self.settings = self._default_settings.copy()
        if settings:
            self.settings.update(settings)
        self.physics_scale = float(self.settings.get(_OPT_SCALE, 1.0) or 1.0)

        # nCloth ソルバーの名前 (fallback 時のみ使用)
        self.nucleus_solver: Optional[str] = None

        # Nucleus fallback 追跡
        self.created_ncloth_nodes: List[str] = []
        self.created_nrigid_nodes: List[str] = []
        self.created_constraint_nodes: List[str] = []

        # Bullet 経路追跡
        self.created_bullet_rigid_bodies: List[str] = []
        self.created_bullet_constraints: List[str] = []
        self.bullet_visual_locator_failures: List[Dict[str, object]] = []
        self._bullet_visual_locator_warning_keys: Set[str] = set()
        self._bullet_rigid_body_shapes_by_index: Dict[int, str] = {}
        self.bullet_solver: Optional[str] = None
        self._current_model_root: Optional[str] = None

        self._bullet_available: Optional[bool] = None

        self.logger.info("Initialized PhysicsConverter")

    # ------------------------------------------------------------------
    # 公開エントリポイント
    # ------------------------------------------------------------------

    def convert_pmd_physics(self, pmd_data, bone_joints: Dict[str, str], root_group: str) -> Tuple[List[str], List[str]]:
        """
        PMD 物理データを変換。Bullet 優先、不可なら Nucleus fallback。
        Returns: (rigid_body_nodes, constraint_nodes)
        """
        self.logger.info("Starting PMD physics data conversion")
        if self._try_load_bullet():
            return self._convert_pmd_physics_bullet(pmd_data, bone_joints, root_group)
        self.logger.warning("Bullet plugin is unavailable. Using Nucleus/nCloth fallback")
        return self._convert_pmd_physics_nucleus(pmd_data, bone_joints, root_group)

    def convert_pmx_physics(self, pmx_data, bone_joints: Dict[str, str], root_group: str) -> Tuple[List[str], List[str]]:
        """
        PMX 物理データを変換。Bullet 優先、不可なら Nucleus fallback。
        Returns: (rigid_body_nodes, constraint_nodes)
        """
        self.logger.info("Starting PMX physics data conversion")
        if self._try_load_bullet():
            return self._convert_pmx_physics_bullet(pmx_data, bone_joints, root_group)
        self.logger.warning("Bullet plugin is unavailable. Using Nucleus/nCloth fallback")
        return self._convert_pmx_physics_nucleus(pmx_data, bone_joints, root_group)

    def collect_physics_from_scene_for_export(
        self,
        root_group: Optional[str] = None,
    ) -> Dict[str, List[Dict[str, object]]]:
        """Bullet 経路の Maya ノードから exporter 用 physics dict を収集する。"""

        def _safe_get_attr(node_attr: str, default=None):
            try:
                return cmds.getAttr(node_attr)
            except Exception:
                return default

        def _safe_to_float(value, default: float = 0.0) -> float:
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        def _safe_to_int(value, default: int = 0) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        rigid_body_entries: List[tuple] = []
        rigid_body_shape_to_index: Dict[str, int] = {}

        root_paths = cmds.ls(root_group, long=True) if root_group else []
        root_path = root_paths[0] if root_paths else None

        def _is_under_root(node: str) -> bool:
            if not root_path:
                return True
            node_paths = cmds.ls(node, long=True) or []
            if not node_paths:
                return False
            node_path = node_paths[0]
            return node_path == root_path or node_path.startswith(f"{root_path}|")

        # 剛体: transform に mmd_rigid_body_index があり、子に bulletRigidBodyShape があるものを対象
        for rb_transform in cmds.ls(type="transform", long=True) or []:
            try:
                if not cmds.attributeQuery("mmd_rigid_body_index", node=rb_transform, exists=True):
                    continue
                if not _is_under_root(rb_transform):
                    continue

                shapes = cmds.listRelatives(rb_transform, shapes=True, type="bulletRigidBodyShape", fullPath=True) or []
                if not shapes:
                    self.logger.warning(
                        f"skip rigid body '{rb_transform}': child bulletRigidBodyShape not found"
                    )
                    continue

                shape = shapes[0]
                raw_index = _safe_get_attr(f"{rb_transform}.mmd_rigid_body_index", None)
                if raw_index is None:
                    self.logger.warning(f"skip rigid body '{rb_transform}': mmd_rigid_body_index is unavailable")
                    continue
                rb_index = _safe_to_int(raw_index, -1)
                if rb_index < 0:
                    self.logger.warning(f"skip rigid body '{rb_transform}': mmd_rigid_body_index is invalid ({raw_index})")
                    continue

                rb_name = _safe_get_attr(f"{rb_transform}.mmd_rigid_body_name", None)
                if not rb_name:
                    rb_name = rb_transform.split("|")[-1]

                name_english = _safe_get_attr(f"{rb_transform}.mmd_rigid_body_name_english", rb_name)
                related_bone_index = _safe_get_attr(f"{rb_transform}.mmd_related_bone_index", -1)
                related_bone_index = _safe_to_int(related_bone_index, -1)

                transform_position = cmds.xform(rb_transform, query=True, worldSpace=True, translation=True) or [0.0, 0.0, 0.0]
                transform_rotation = cmds.xform(rb_transform, query=True, worldSpace=True, rotation=True) or [0.0, 0.0, 0.0]

                if cmds.attributeQuery("mmd_physics_mode", node=rb_transform, exists=True):
                    physics_mode = _safe_to_int(_safe_get_attr(f"{rb_transform}.mmd_physics_mode", 0), 0)
                else:
                    body_type = _safe_to_int(_safe_get_attr(f"{shape}.bodyType", 0), 0)
                    physics_mode = _BULLET_MODE_TO_MMD.get(body_type, 0)

                collider_shape_type = _safe_to_int(_safe_get_attr(f"{shape}.colliderShapeType", 1), 1)
                shape_type = _BULLET_SHAPE_TO_MMD.get(collider_shape_type, 0)

                if collider_shape_type == 2:
                    radius = _safe_to_float(_safe_get_attr(f"{shape}.radius", 0.0), 0.0)
                    size = [radius, radius, radius]
                elif collider_shape_type == 3:
                    radius = _safe_to_float(_safe_get_attr(f"{shape}.radius", 0.0), 0.0)
                    length = _safe_to_float(_safe_get_attr(f"{shape}.length", 0.0), 0.0)
                    size = [radius, max(0.0, length - radius * 2.0), radius]
                else:
                    scale = cmds.xform(rb_transform, query=True, worldSpace=True, scale=True) or [1.0, 1.0, 1.0]
                    size = [_safe_to_float(scale[0], 1.0) * 0.5, _safe_to_float(scale[1], 1.0) * 0.5, _safe_to_float(scale[2], 1.0) * 0.5]

                rigid_body_entry = {
                    "name": str(rb_name),
                    "name_english": str(name_english),
                    "related_bone_index": related_bone_index,
                    "group": _safe_to_int(_safe_get_attr(f"{rb_transform}.mmd_collision_group", 0), 0),
                    "collision_mask": _safe_to_int(_safe_get_attr(f"{rb_transform}.mmd_collision_mask", 0xFFFF), 0xFFFF),
                    "shape_type": shape_type,
                    "size": size,
                    "position": self._maya_to_mmd_position(transform_position),
                    "rotation": self._maya_to_mmd_rotation(transform_rotation),
                    "mass": _safe_to_float(_safe_get_attr(f"{shape}.mass", 0.0), 0.0),
                    "velocity_attenuation": _safe_to_float(_safe_get_attr(f"{shape}.linearDamping", 0.0), 0.0),
                    "rotation_attenuation": _safe_to_float(_safe_get_attr(f"{shape}.angularDamping", 0.0), 0.0),
                    "elasticity": _safe_to_float(_safe_get_attr(f"{shape}.restitution", 0.0), 0.0),
                    "friction": _safe_to_float(_safe_get_attr(f"{shape}.friction", 0.0), 0.0),
                    "physics_mode": physics_mode,
                }

                rigid_body_entries.append((rb_index, rigid_body_entry))
                rigid_body_shape_to_index[shape] = rb_index
            except Exception as e:
                self.logger.warning(f"skip rigid body '{rb_transform}': {e}")

        sorted_rigid_body_entries = sorted(rigid_body_entries, key=lambda item: item[0])
        rigid_bodies = [entry for _, entry in sorted_rigid_body_entries]
        rigid_body_index_remap = {
            original_index: export_index
            for export_index, (original_index, _) in enumerate(sorted_rigid_body_entries)
        }

        # ジョイント: transform に mmd_joint_name があり、子に bulletRigidBodyConstraintShape があるものを対象
        joints = []
        for joint_transform in cmds.ls(type="transform", long=True) or []:
            try:
                if not cmds.attributeQuery("mmd_joint_name", node=joint_transform, exists=True):
                    continue
                if not _is_under_root(joint_transform):
                    continue

                shapes = cmds.listRelatives(
                    joint_transform,
                    shapes=True,
                    type="bulletRigidBodyConstraintShape",
                    fullPath=True,
                ) or []
                if not shapes:
                    self.logger.warning(
                        f"skip joint '{joint_transform}': child bulletRigidBodyConstraintShape not found"
                    )
                    continue

                shape = shapes[0]
                joint_name = _safe_get_attr(f"{joint_transform}.mmd_joint_name", joint_transform.split("|")[-1])
                if not joint_name:
                    joint_name = joint_transform.split("|")[-1]
                name_english = _safe_get_attr(f"{joint_transform}.mmd_joint_name_english", joint_name)

                joint_type = _safe_to_int(
                    _safe_get_attr(f"{joint_transform}.mmd_joint_type", None),
                    _safe_to_int(_safe_get_attr(f"{shape}.constraintType", 0), 0),
                )
                is_pmx_joint = bool(_safe_to_int(_safe_get_attr(f"{joint_transform}.mmd_joint_is_pmx", 0), 0))

                transform_position = cmds.xform(joint_transform, query=True, worldSpace=True, translation=True) or [0.0, 0.0, 0.0]
                transform_rotation = cmds.xform(joint_transform, query=True, worldSpace=True, rotation=True) or [0.0, 0.0, 0.0]

                translation_limit_min = [0.0, 0.0, 0.0]
                translation_limit_max = [0.0, 0.0, 0.0]
                rotation_limit_min = [0.0, 0.0, 0.0]
                rotation_limit_max = [0.0, 0.0, 0.0]

                axes = ["X", "Y", "Z"]
                for i, axis in enumerate(axes):
                    linear_state = _safe_to_int(_safe_get_attr(f"{shape}.linearConstraint{axis}", 0), 0)
                    if linear_state == _CONSTRAINT_LIMITED:
                        translation_limit_min[i] = _safe_to_float(_safe_get_attr(f"{shape}.linearConstraintMin{axis}", 0.0), 0.0)
                        translation_limit_max[i] = _safe_to_float(_safe_get_attr(f"{shape}.linearConstraintMax{axis}", 0.0), 0.0)
                    else:
                        translation_limit_min[i] = 0.0
                        translation_limit_max[i] = 0.0

                    angular_state = _safe_to_int(_safe_get_attr(f"{shape}.angularConstraint{axis}", 0), 0)
                    if angular_state == _CONSTRAINT_LIMITED:
                        angular_min = _safe_to_float(_safe_get_attr(f"{shape}.angularConstraintMin{axis}", 0.0), 0.0)
                        angular_max = _safe_to_float(_safe_get_attr(f"{shape}.angularConstraintMax{axis}", 0.0), 0.0)
                        if is_pmx_joint:
                            angular_min = math.radians(angular_min)
                            angular_max = math.radians(angular_max)
                        rotation_limit_min[i] = angular_min
                        rotation_limit_max[i] = angular_max
                    else:
                        rotation_limit_min[i] = 0.0
                        rotation_limit_max[i] = 0.0

                spring_translation = [
                    _safe_to_float(_safe_get_attr(f"{shape}.linearSpringStiffnessX", 0.0), 0.0),
                    _safe_to_float(_safe_get_attr(f"{shape}.linearSpringStiffnessY", 0.0), 0.0),
                    _safe_to_float(_safe_get_attr(f"{shape}.linearSpringStiffnessZ", 0.0), 0.0),
                ]
                spring_rotation = [
                    _safe_to_float(_safe_get_attr(f"{shape}.angularSpringStiffnessX", 0.0), 0.0),
                    _safe_to_float(_safe_get_attr(f"{shape}.angularSpringStiffnessY", 0.0), 0.0),
                    _safe_to_float(_safe_get_attr(f"{shape}.angularSpringStiffnessZ", 0.0), 0.0),
                ]

                def _resolve_joint_body_index(attr_name: str) -> int:
                    connections = cmds.listConnections(f"{shape}.{attr_name}", source=True, destination=False, plugs=True) or []
                    for connection in connections:
                        rb_node = connection.split(".")[0]
                        if rb_node in rigid_body_shape_to_index:
                            return rigid_body_shape_to_index[rb_node]

                        if cmds.nodeType(rb_node) == "bulletRigidBodyShape":
                            parents = cmds.listRelatives(rb_node, parent=True, fullPath=True) or []
                            if parents:
                                parent = parents[0]
                                if cmds.attributeQuery("mmd_rigid_body_index", node=parent, exists=True):
                                    parent_index = _safe_get_attr(f"{parent}.mmd_rigid_body_index", None)
                                    if parent_index is not None:
                                        return _safe_to_int(parent_index, -1)

                    return -1

                rigid_body_a_index = rigid_body_index_remap.get(_resolve_joint_body_index("rigidBodyA"), -1)
                rigid_body_b_index = rigid_body_index_remap.get(_resolve_joint_body_index("rigidBodyB"), -1)

                joints.append(
                    {
                        "name": str(joint_name),
                        "name_english": str(name_english),
                        "joint_type": joint_type,
                        "rigid_body_a_index": rigid_body_a_index,
                        "rigid_body_b_index": rigid_body_b_index,
                        "position": self._maya_to_mmd_position(transform_position),
                        "rotation": self._maya_to_mmd_rotation(transform_rotation),
                        "translation_limit_min": translation_limit_min,
                        "translation_limit_max": translation_limit_max,
                        "rotation_limit_min": rotation_limit_min,
                        "rotation_limit_max": rotation_limit_max,
                        "spring_translation": spring_translation,
                        "spring_rotation": spring_rotation,
                    }
                )
            except Exception as e:
                self.logger.warning(f"skip joint '{joint_transform}': {e}")

        return {
            "rigid_bodies": rigid_bodies,
            "joints": joints,
        }

    # ------------------------------------------------------------------
    # Bullet 可用性
    # ------------------------------------------------------------------

    @classmethod
    def is_bullet_available(cls) -> bool:
        """Bullet プラグインがロード可能かを返す (副作用なし)。"""
        try:
            if cmds.pluginInfo(_BULLET_PLUGIN, query=True, loaded=True):
                return True
            cmds.loadPlugin(_BULLET_PLUGIN, quiet=True)
            return True
        except Exception:
            return False

    def _try_load_bullet(self) -> bool:
        """インスタンス内で一度だけ Bullet 可用性を確認してキャッシュする。"""
        if self._bullet_available is None:
            try:
                if cmds.pluginInfo(_BULLET_PLUGIN, query=True, loaded=True):
                    self._bullet_available = True
                else:
                    cmds.loadPlugin(_BULLET_PLUGIN, quiet=True)
                    self._bullet_available = True
            except Exception:
                self._bullet_available = False
        return self._bullet_available

    # ------------------------------------------------------------------
    # Bullet 経路: PMD
    # ------------------------------------------------------------------

    def _convert_pmd_physics_bullet(
        self, pmd_data, bone_joints: Dict[str, str], root_group: str
    ) -> Tuple[List[str], List[str]]:
        self.created_bullet_rigid_bodies = []
        self.created_bullet_constraints = []
        self.bullet_visual_locator_failures = []
        self._bullet_visual_locator_warning_keys = set()
        self._bullet_rigid_body_shapes_by_index = {}
        self._source_joints = list(getattr(pmd_data, "joints", None) or [])

        physics_group = cmds.group(empty=True, name=PHYSICS_GROUP, parent=root_group)
        rigid_bodies_group = cmds.group(empty=True, name=RIGID_BODIES_GROUP, parent=physics_group)
        constraints_group = cmds.group(empty=True, name=CONSTRAINTS_GROUP, parent=physics_group)
        self._current_model_root = root_group
        ensure_visibility_attrs(MayaCmdsAdapter(cmds), root_group)
        self.ensure_legacy_bullet_enabled_control(root_group)

        bone_index_map = self._create_bone_index_mapping(pmd_data.bones, bone_joints)
        self._attach_physics_group_to_model_motion_parent(physics_group, root_group, bone_index_map)

        if hasattr(pmd_data, "rigid_bodies") and pmd_data.rigid_bodies:
            for idx, rb in enumerate(pmd_data.rigid_bodies):
                # 1 剛体の失敗で物理変換全体を止めない（ログ失敗も含めて握る）。
                try:
                    self._create_bullet_rigid_body(rb, idx, rigid_bodies_group, "pmd", bone_index_map)
                except Exception as e:
                    safe_log_error(self.logger, "Bullet 剛体作成エラー", rb, e)

        create_joints = self.settings.get(_OPT_CREATE_PHYSICS_JOINTS, True)
        if create_joints and hasattr(pmd_data, "joints") and pmd_data.joints:
            for joint in pmd_data.joints:
                try:
                    self._create_bullet_constraint_pmd(joint, pmd_data.rigid_bodies, constraints_group)
                except Exception as e:
                    safe_log_error(self.logger, "PMD joint 作成エラー", joint, e)

        self._log_bullet_visual_locator_summary()
        self.logger.info(
            f"PMD Bullet physics conversion complete: Rb={len(self.created_bullet_rigid_bodies)} "
            f"Constraint={len(self.created_bullet_constraints)}"
        )
        return (self.created_bullet_rigid_bodies, self.created_bullet_constraints)

    # ------------------------------------------------------------------
    # Bullet 経路: PMX
    # ------------------------------------------------------------------

    def _convert_pmx_physics_bullet(
        self, pmx_data, bone_joints: Dict[str, str], root_group: str
    ) -> Tuple[List[str], List[str]]:
        self.created_bullet_rigid_bodies = []
        self.created_bullet_constraints = []
        self.bullet_visual_locator_failures = []
        self._bullet_visual_locator_warning_keys = set()
        self._bullet_rigid_body_shapes_by_index = {}
        self._source_joints = list(getattr(pmx_data, "joints", None) or [])

        physics_group = cmds.group(empty=True, name=PHYSICS_GROUP, parent=root_group)
        rigid_bodies_group = cmds.group(empty=True, name=RIGID_BODIES_GROUP, parent=physics_group)
        constraints_group = cmds.group(empty=True, name=CONSTRAINTS_GROUP, parent=physics_group)
        self._current_model_root = root_group
        ensure_visibility_attrs(MayaCmdsAdapter(cmds), root_group)
        self.ensure_legacy_bullet_enabled_control(root_group)

        bone_index_map = self._create_bone_index_mapping(pmx_data.bones, bone_joints)
        self._attach_physics_group_to_model_motion_parent(physics_group, root_group, bone_index_map)

        if hasattr(pmx_data, "rigid_bodies") and pmx_data.rigid_bodies:
            for idx, rb in enumerate(pmx_data.rigid_bodies):
                # 1 剛体の失敗で物理変換全体を止めない（ログ失敗も含めて握る）。
                try:
                    self._create_bullet_rigid_body(rb, idx, rigid_bodies_group, "pmx", bone_index_map)
                except Exception as e:
                    safe_log_error(self.logger, "Bullet 剛体作成エラー", rb, e)

        create_joints = self.settings.get(_OPT_CREATE_PHYSICS_JOINTS, True)
        if create_joints and hasattr(pmx_data, "joints") and pmx_data.joints:
            for joint in pmx_data.joints:
                try:
                    self._create_bullet_constraint_pmx(joint, pmx_data.rigid_bodies, constraints_group)
                except Exception as e:
                    safe_log_error(self.logger, "PMX joint 作成エラー", joint, e)

        self._log_bullet_visual_locator_summary()
        self.logger.info(
            f"PMX Bullet physics conversion complete: Rb={len(self.created_bullet_rigid_bodies)} "
            f"Constraint={len(self.created_bullet_constraints)}"
        )
        return (self.created_bullet_rigid_bodies, self.created_bullet_constraints)

    # ------------------------------------------------------------------
    # Bullet 剛体作成
    # ------------------------------------------------------------------

    def _set_attr_clamped(self, node: str, attr: str, value: float) -> None:
        """
        数値アトリビュートを設定する。属性に min/max が定義されている場合は
        その有効範囲へクランプしてから設定する。

        MMD の剛体パラメータ（回転減衰など）は Maya Bullet が許容する範囲
        ([0,1] など) を超えることがあり、そのまま setAttr すると
        RuntimeError で剛体作成全体が失敗するため、ここで安全側に丸める。

        Args:
            node: 対象ノード名（shape など）
            attr: アトリビュート名（例: "angularDamping"）
            value: 設定したい値
        """
        plug = f"{node}.{attr}"
        try:
            clamped = float(value)
        except (TypeError, ValueError):
            clamped = 0.0

        min_val = None
        max_val = None
        try:
            if cmds.attributeQuery(attr, node=node, minExists=True):
                queried_min = cmds.attributeQuery(attr, node=node, minimum=True)
                if queried_min:
                    min_val = float(queried_min[0])
            if cmds.attributeQuery(attr, node=node, maxExists=True):
                queried_max = cmds.attributeQuery(attr, node=node, maximum=True)
                if queried_max:
                    max_val = float(queried_max[0])
        except Exception as exc:
            # 範囲取得に失敗してもクランプ無しで続行する
            self.logger.debug(f"Failed to get valid range for '{plug}': {exc}")

        clamped = _clamp_to_range(clamped, min_val, max_val)
        cmds.setAttr(plug, clamped)

    def _create_bullet_rigid_body(
        self,
        rb,
        rigid_body_index: int,
        parent_group: str,
        model_type: str,
        bone_index_map: Optional[Dict[int, str]] = None,
    ) -> Optional[str]:
        """
        bulletRigidBodyShape ノードを作成する。

        Actual attributes (Maya 2024):
          - colliderShapeType: box=1, sphere=2, capsule=3
          - bodyType: static=0, kinematic=1, dynamic=2
          - radius (sphere/capsule), length (capsule), colliderShapeOffset{X,Y,Z}
          - mass, linearDamping, angularDamping, friction, restitution
        """
        try:
            rb_name = maya_name_utils.sanitize_text(rb.name) if rb.name else f"rigid_{rigid_body_index}"
            transform = cmds.createNode("transform", name=f"{rb_name}_bulletRb")
            shape = cmds.createNode("bulletRigidBodyShape", name=f"{rb_name}_bulletRbShape", parent=transform)

            # 位置・回転
            if hasattr(rb, "rotation") and rb.rotation:
                cmds.xform(
                    transform,
                    ws=True,
                    matrix=self._mmd_to_maya_transform_matrix(rb.position, rb.rotation, self.physics_scale),
                )
            else:
                maya_pos = self._mmd_to_maya_position(rb.position, self.physics_scale)
                cmds.xform(transform, ws=True, t=maya_pos)

            # colliderShapeType
            mmd_shape = getattr(rb, "shape_type", 0)
            bullet_shape = _MMD_SHAPE_TO_BULLET.get(mmd_shape, 2)

            # サイズ設定
            size = self._scaled_rigid_body_size(getattr(rb, "size", (1.0, 1.0, 1.0)))
            if bullet_shape == 2:  # sphere
                cmds.setAttr(f"{shape}.radius", size[0])
            elif bullet_shape == 3:  # capsule
                cmds.setAttr(f"{shape}.radius", size[0])
                # PMX/MMD sizeY maps to Bullet's cylinder height. Maya Bullet's
                # length attr is the full capsule height shown in the bbox.
                cmds.setAttr(f"{shape}.length", size[1] + size[0] * 2.0)
            elif bullet_shape == 1:  # box: scale transform
                cmds.setAttr(f"{shape}.colliderShapeType", bullet_shape)
                cmds.xform(transform, ws=True, s=[size[0] * 2.0, size[1] * 2.0, size[2] * 2.0], relative=True)
                # No need to set radius/length for box
                cmds.setAttr(f"{shape}.colliderShapeOffsetX", 0.0)
                cmds.setAttr(f"{shape}.colliderShapeOffsetY", 0.0)
                cmds.setAttr(f"{shape}.colliderShapeOffsetZ", 0.0)
            else:
                # Fallback: sphere
                cmds.setAttr(f"{shape}.radius", size[0])

            # Set colliderShapeType AFTER sizes (box enum set args conflict with radius/length)
            # Use a single setAttr unless it's box (already done above)
            if bullet_shape != 1:
                cmds.setAttr(f"{shape}.colliderShapeType", bullet_shape)

            self._create_bullet_visual_locator(transform, bullet_shape, size)

            # bodyType
            physics_mode = getattr(rb, "physics_mode", 0)
            self._set_bullet_collision_filter(shape, rb, model_type)
            bullet_body = _MMD_MODE_TO_BULLET.get(physics_mode, 0)
            cmds.setAttr(f"{shape}.bodyType", bullet_body)

            # 物理パラメータ
            # MMD の減衰/摩擦/反発は Maya Bullet の許容範囲（多くは [0,1]）を
            # 超えることがあるため、属性の有効範囲にクランプして設定する。
            self._set_attr_clamped(shape, "mass", getattr(rb, "mass", 0.0))
            self._set_attr_clamped(shape, "linearDamping", getattr(rb, "velocity_attenuation", 0.0))
            self._set_attr_clamped(shape, "angularDamping", getattr(rb, "rotation_attenuation", 0.0))
            self._set_attr_clamped(shape, "friction", getattr(rb, "friction", 0.0))
            self._set_attr_clamped(shape, "restitution", getattr(rb, "elasticity", 0.0))

            self._connect_bullet_rigid_body(transform, shape)

            # カスタムアトリビュート
            self._add_rigid_body_custom_attrs(transform, rb, rigid_body_index, physics_mode, model_type)

            parented = cmds.parent(transform, parent_group)
            if parented:
                transform = parented[0]
            if physics_mode != 0:
                drive_solved_translation = int(physics_mode) != 2
                self._connect_bullet_simulation_output(
                    transform,
                    shape,
                    drive_translation=drive_solved_translation,
                )
                if int(physics_mode) == 2:
                    self._attach_mode2_bullet_body_translation_to_bone(transform, rb, bone_index_map)
                self._attach_dynamic_bullet_body_to_bone(
                    transform,
                    rb,
                    bone_index_map,
                    physics_mode,
                    rigid_body_index,
                )
            if physics_mode == 0:
                self._attach_static_bullet_body_to_bone(transform, rb, bone_index_map)
            shapes = cmds.listRelatives(transform, shapes=True, type="bulletRigidBodyShape", fullPath=True) or []
            self._bullet_rigid_body_shapes_by_index[rigid_body_index] = shapes[0] if shapes else shape
            self.created_bullet_rigid_bodies.append(transform)
            return transform

        except Exception as e:
            # 1 剛体の失敗は握りつぶし、残りの剛体変換を続行する。
            # ログ出力自体が失敗しても import 全体を止めないよう防御する。
            safe_log_error(self.logger, "Bullet 剛体作成エラー", rb, e)
            return None

    def _attach_static_bullet_body_to_bone(
        self,
        transform: str,
        rb,
        bone_index_map: Optional[Dict[int, str]],
    ) -> None:
        """MMD physics_mode=0 の static 剛体を関連ボーンに追従させる。"""
        if not bone_index_map:
            return

        bone_index = None
        if hasattr(rb, "bone_index"):
            bone_index = getattr(rb, "bone_index")
        elif hasattr(rb, "related_bone_index"):
            bone_index = getattr(rb, "related_bone_index")

        if bone_index is None or bone_index < 0:
            return

        joint = bone_index_map.get(bone_index)
        if not joint or not cmds.objExists(joint):
            return

        try:
            constraint = cmds.parentConstraint(joint, transform, maintainOffset=True)
            if constraint:
                self.logger.debug(
                    f"Connected physics_mode=0 Bullet rigid body '{transform}' to follow related bone '{joint}'"
                )
        except Exception as exc:
            self.logger.warning(
                f"Skipped related-bone follow connection for physics_mode=0 Bullet rigid body '{transform}': {exc}"
            )

    def _attach_mode2_bullet_body_translation_to_bone(
        self,
        transform: str,
        rb,
        bone_index_map: Optional[Dict[int, str]],
    ) -> None:
        """Keep physics_mode=2 rigid-body translation aligned with its related bone."""
        if not bone_index_map:
            return

        bone_index = None
        if hasattr(rb, "bone_index"):
            bone_index = getattr(rb, "bone_index")
        elif hasattr(rb, "related_bone_index"):
            bone_index = getattr(rb, "related_bone_index")

        if bone_index is None or bone_index < 0:
            return

        joint = bone_index_map.get(bone_index)
        if not joint or not cmds.objExists(joint):
            return

        try:
            constraint = cmds.pointConstraint(joint, transform, maintainOffset=True)
            if constraint:
                self.logger.debug(
                    f"Connected physics_mode=2 Bullet rigid body '{transform}' translation to related bone '{joint}'"
                )
        except Exception as exc:
            self.logger.warning(
                f"Skipped physics_mode=2 related-bone translation alignment '{joint}' -> '{transform}': {exc}"
            )

    def _attach_dynamic_bullet_body_to_bone(
        self,
        transform: str,
        rb,
        bone_index_map: Optional[Dict[int, str]],
        physics_mode: int,
        rigid_body_index: Optional[int] = None,
    ) -> None:
        """Drive the related Maya joint from a simulated MMD rigid body.

        MMD dynamic rigid bodies are useful only when their solved transform is
        fed back into the related bone that skins the mesh.  The Bullet preview
        body is already driven from ``outSolvedTranslate/Rotate``; this method
        adds the final preview link from that body to the Maya joint.
        """
        if not bone_index_map:
            return

        bone_index = None
        if hasattr(rb, "bone_index"):
            bone_index = getattr(rb, "bone_index")
        elif hasattr(rb, "related_bone_index"):
            bone_index = getattr(rb, "related_bone_index")

        if bone_index is None or bone_index < 0:
            return

        joint = bone_index_map.get(bone_index)
        if not joint or not cmds.objExists(joint):
            return

        try:
            constraint = self._create_physics_preview_constraint(
                transform,
                joint,
                physics_mode,
                rigid_body_index=rigid_body_index,
            )
            if constraint:
                self.logger.debug(
                    f"Connected dynamic Bullet rigid body '{transform}' to drive related bone '{joint}'"
                )
        except Exception as exc:
            self.logger.warning(
                f"Skipped dynamic Bullet rigid body to related-bone connection '{transform}' -> '{joint}': {exc}"
            )

    def _joints_lock_rigid_body_translation(self, rigid_body_index: Optional[int]) -> bool:
        """Return True when source joints fully lock translation for a rigid body."""
        if rigid_body_index is None or rigid_body_index < 0:
            return False

        for joint in getattr(self, "_source_joints", ()) or ():
            idx_a = getattr(joint, "rigid_body_a_index", getattr(joint, "rigid_body_index_a", -1))
            idx_b = getattr(joint, "rigid_body_b_index", getattr(joint, "rigid_body_index_b", -1))
            if rigid_body_index not in (idx_a, idx_b):
                continue

            trans_lo = getattr(joint, "translation_limit_min", None)
            trans_hi = getattr(joint, "translation_limit_max", None)
            if not trans_lo or not trans_hi:
                continue
            if all(float(lo) == float(hi) == 0.0 for lo, hi in zip(trans_lo, trans_hi)):
                return True
        return False

    def connect_existing_bullet_preview_to_bones(self, root_group: Optional[str] = None) -> int:
        """Connect existing dynamic Bullet bodies to indexed MMD joints.

        This is a repair/refresh path for scenes imported before preview
        feedback existed, and it is also useful after manual Bullet edits.
        It relies on ``mmd_related_bone_index`` on rigid body transforms and
        ``mmd_bone_index`` on joints, so it is independent of sanitized names.
        """
        bone_index_map = self._create_bone_index_mapping_from_scene(root_group)
        if not bone_index_map:
            return 0

        shapes = cmds.ls(type="bulletRigidBodyShape", long=True) or []
        if root_group and cmds.objExists(root_group):
            descendants = set(cmds.listRelatives(root_group, allDescendents=True, fullPath=True) or [])
            shapes = [shape for shape in shapes if shape in descendants]

        connected = 0
        for shape in shapes:
            parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
            if not parents:
                continue
            transform = parents[0]
            try:
                body_type = cmds.getAttr(f"{shape}.bodyType")
                if body_type not in (2, 3):
                    continue
                if not cmds.attributeQuery("mmd_related_bone_index", node=transform, exists=True):
                    continue
                bone_index = int(cmds.getAttr(f"{transform}.mmd_related_bone_index"))
                if bone_index < 0:
                    continue
                joint = bone_index_map.get(bone_index)
                if not joint or not cmds.objExists(joint):
                    continue
                physics_mode = 1
                if cmds.attributeQuery("mmd_physics_mode", node=transform, exists=True):
                    physics_mode = int(cmds.getAttr(f"{transform}.mmd_physics_mode"))
                rigid_body_index = None
                if cmds.attributeQuery("mmd_rigid_body_index", node=transform, exists=True):
                    rigid_body_index = int(cmds.getAttr(f"{transform}.mmd_rigid_body_index"))
                if self._has_physics_preview_constraint(
                    transform,
                    joint,
                    physics_mode,
                    rigid_body_index=rigid_body_index,
                ):
                    continue
                self._create_physics_preview_constraint(
                    transform,
                    joint,
                    physics_mode,
                    rigid_body_index=rigid_body_index,
                )
                connected += 1
            except Exception as exc:
                self.logger.warning(f"Skipped Bullet preview repair for '{shape}': {exc}")
        return connected

    @staticmethod
    def ensure_legacy_bullet_enabled_attr(root_group: Optional[str]) -> None:
        """Ensure Root.mmd_legacy_bullet_enabled exists (default True).

        Creates the attribute only when missing. Never overwrites a value the
        user already chose — including after native physics bake exclusion.
        """
        if not root_group or not cmds.objExists(root_group):
            return
        try:
            if cmds.attributeQuery(_LEGACY_BULLET_ENABLED_ATTR, node=root_group, exists=True):
                return
            cmds.addAttr(
                root_group,
                longName=_LEGACY_BULLET_ENABLED_ATTR,
                attributeType="bool",
                defaultValue=True,
                keyable=True,
            )
            cmds.setAttr(f"{root_group}.{_LEGACY_BULLET_ENABLED_ATTR}", True)
        except Exception:
            return

    @classmethod
    def ensure_legacy_bullet_enabled_control(cls, root_group: Optional[str]) -> Optional[int]:
        """Ensure the Root preference attr exists and its live AE control is registered.

        Safe entry point for physics setup and VMD import. Never overwrites an
        existing user value. Returns the scriptJob id when a live watch is
        active, or None when skipped (batch mode / missing root / job failure).
        """
        cls.ensure_legacy_bullet_enabled_attr(root_group)
        return cls.ensure_legacy_bullet_enabled_watch(root_group)

    @classmethod
    def ensure_legacy_bullet_enabled_watch(cls, root_group: Optional[str]) -> Optional[int]:
        """Register an idempotent attributeChange job for live AE control.

        When the user toggles ``mmd_legacy_bullet_enabled`` in the Attribute
        Editor, immediately apply the root-scoped full legacy Bullet setup
        suspension (preview feedback + rigid bodies / Bullet joints).
        Jobs use ``killWithScene`` so they do not outlive the scene.

        In mayapy / batch mode, scriptJob is skipped: the persistent attribute
        plus VMD-import evaluation remains sufficient. Callers and tests can
        still invoke :meth:`apply_legacy_bullet_enabled_from_attr` directly.
        """
        if not root_group or not cmds.objExists(root_group):
            return None
        try:
            root_long = (cmds.ls(root_group, long=True) or [root_group])[0]
        except Exception:
            root_long = root_group

        cls.ensure_legacy_bullet_enabled_attr(root_long)

        existing = _LEGACY_BULLET_WATCH_JOBS.get(root_long)
        if existing is not None:
            try:
                if cmds.scriptJob(exists=existing):
                    return existing
            except Exception:
                pass
            _LEGACY_BULLET_WATCH_JOBS.pop(root_long, None)

        # Batch/mayapy: avoid scriptJob failures; attr + import-time eval is enough.
        try:
            if cmds.about(batch=True):
                return None
        except Exception:
            return None

        attr_path = f"{root_long}.{_LEGACY_BULLET_ENABLED_ATTR}"

        def _on_attr_changed(*_args, _root=root_long):
            cls.apply_legacy_bullet_enabled_from_attr(_root)

        try:
            job_id = int(
                cmds.scriptJob(
                    attributeChange=[attr_path, _on_attr_changed],
                    killWithScene=True,
                )
            )
        except Exception:
            return None

        _LEGACY_BULLET_WATCH_JOBS[root_long] = job_id
        return job_id

    @classmethod
    def apply_legacy_bullet_enabled_from_attr(cls, root_group: Optional[str]) -> int:
        """Apply the current Root ``mmd_legacy_bullet_enabled`` to the full legacy setup.

        Intended for the live AE attributeChange callback and for focused tests
        that cannot rely on GUI interaction. Does not write the preference.
        Suspends or restores marked preview feedback and Bullet rigid-body /
        rigid-body-constraint nodes under this root only.
        """
        if not root_group or not cmds.objExists(root_group):
            return 0
        try:
            enabled = cls.get_legacy_bullet_enabled(root_group)
            return cls().set_legacy_bullet_setup_enabled(root_group, enabled)
        except Exception as exc:
            try:
                get_logger(cls.__name__).warning(
                    f"Failed to apply {_LEGACY_BULLET_ENABLED_ATTR} on '{root_group}': {exc}"
                )
            except Exception:
                pass
            return 0

    @staticmethod
    def get_legacy_bullet_enabled(root_group: Optional[str]) -> bool:
        """Read the persistent legacy Maya Bullet setup preference.

        Missing attribute or unreadable root means the legacy setup is allowed
        (default True), matching historical VMD import behaviour.
        """
        if not root_group or not cmds.objExists(root_group):
            return True
        try:
            if not cmds.attributeQuery(_LEGACY_BULLET_ENABLED_ATTR, node=root_group, exists=True):
                return True
            return bool(cmds.getAttr(f"{root_group}.{_LEGACY_BULLET_ENABLED_ATTR}"))
        except Exception:
            return True

    def set_legacy_bullet_enabled(self, root_group: Optional[str], enabled: bool) -> int:
        """Persist the user's legacy Bullet setup preference and apply it.

        This is the reversible, root-scoped user control. It updates
        ``mmd_legacy_bullet_enabled`` and suspends or restores the full legacy
        Maya Bullet setup for that root only (marked Bullet-to-bone preview
        constraints plus ``bulletRigidBodyShape`` /
        ``bulletRigidBodyConstraintShape`` descendants). It never disables the
        global ``bulletSolver``.

        Native physics bake must use
        :meth:`set_existing_bullet_preview_feedback_enabled` instead so it does
        not overwrite this preference or touch Bullet solver nodes.
        """
        if not root_group or not cmds.objExists(root_group):
            return 0
        self.ensure_legacy_bullet_enabled_control(root_group)
        try:
            cmds.setAttr(f"{root_group}.{_LEGACY_BULLET_ENABLED_ATTR}", bool(enabled))
        except Exception as exc:
            self.logger.warning(
                f"Failed to set {_LEGACY_BULLET_ENABLED_ATTR} on '{root_group}': {exc}"
            )
        return self.set_legacy_bullet_setup_enabled(root_group, bool(enabled))

    def set_legacy_bullet_setup_enabled(self, root_group: Optional[str], enabled: bool) -> int:
        """Suspend or restore the full legacy Bullet setup under one model root.

        Targets, root-scoped only:

        * marked Bullet-to-bone preview constraints
        * ``bulletRigidBodyShape`` and ``bulletRigidBodyConstraintShape`` nodes

        Uses ``mmd_legacy_bullet_disabled`` / previous-state metadata so restore
        only re-enables nodes this path suspended. Pre-existing non-zero
        ``nodeState`` values (user-disabled) are left alone. Does not write
        ``mmd_legacy_bullet_enabled``, does not touch ``bulletSolver``, and does
        not use the native-bake metadata attrs.
        """
        if not root_group or not cmds.objExists(root_group):
            return 0
        root_long = self._root_long_name(root_group)
        descendants = self._root_descendant_set(root_group)
        if descendants is None:
            return 0

        changed = 0
        for node in self._iter_legacy_bullet_setup_nodes(root_long, descendants):
            try:
                if self._set_node_legacy_bullet_suspended(node, suspend=not enabled):
                    changed += 1
            except Exception as exc:
                self.logger.warning(f"Skipped legacy Bullet setup toggle for '{node}': {exc}")
        return changed

    def set_existing_bullet_preview_feedback_enabled(self, root_group: Optional[str], enabled: bool) -> int:
        """Toggle this model's marked Bullet-to-bone preview constraints reversibly.

        Temporary exclusion for native physics bake (and similar automatic
        paths). Disables only generated preview constraints, without suspending
        Bullet rigid bodies / joints, deleting the legacy Bullet setup, or
        changing ``mmd_legacy_bullet_enabled``. A later restore only re-enables
        constraints this method disabled, and still respects user-disabled
        (non-zero nodeState) nodes.
        """
        descendants = self._root_descendant_set(root_group)

        changed = 0
        for constraint in self._iter_marked_preview_constraints(descendants):
            try:
                if enabled:
                    if not cmds.attributeQuery(_NATIVE_BAKE_DISABLED_ATTR, node=constraint, exists=True):
                        continue
                    if not cmds.getAttr(f"{constraint}.{_NATIVE_BAKE_DISABLED_ATTR}"):
                        continue
                    previous_state = 0
                    if cmds.attributeQuery(_NATIVE_BAKE_PREVIOUS_NODE_STATE_ATTR, node=constraint, exists=True):
                        previous_state = int(cmds.getAttr(f"{constraint}.{_NATIVE_BAKE_PREVIOUS_NODE_STATE_ATTR}"))
                    cmds.setAttr(f"{constraint}.nodeState", previous_state)
                    cmds.setAttr(f"{constraint}.{_NATIVE_BAKE_DISABLED_ATTR}", False)
                    changed += 1
                    continue

                if cmds.attributeQuery(_NATIVE_BAKE_DISABLED_ATTR, node=constraint, exists=True) and cmds.getAttr(
                    f"{constraint}.{_NATIVE_BAKE_DISABLED_ATTR}"
                ):
                    continue
                previous_state = int(cmds.getAttr(f"{constraint}.nodeState"))
                # Respect a user-disabled constraint. Only normally active
                # generated preview feedback is suspended for native bake.
                if previous_state != 0:
                    continue
                if not cmds.attributeQuery(_NATIVE_BAKE_PREVIOUS_NODE_STATE_ATTR, node=constraint, exists=True):
                    cmds.addAttr(constraint, longName=_NATIVE_BAKE_PREVIOUS_NODE_STATE_ATTR, attributeType="long")
                if not cmds.attributeQuery(_NATIVE_BAKE_DISABLED_ATTR, node=constraint, exists=True):
                    cmds.addAttr(constraint, longName=_NATIVE_BAKE_DISABLED_ATTR, attributeType="bool")
                cmds.setAttr(f"{constraint}.{_NATIVE_BAKE_PREVIOUS_NODE_STATE_ATTR}", previous_state)
                # Constraints do not support nodeState=1 (pass-through); Maya
                # converts that request to blocking anyway. Use blocking
                # explicitly so the baked joint channels have no Bullet input.
                cmds.setAttr(f"{constraint}.nodeState", _NODE_STATE_BLOCKING)
                cmds.setAttr(f"{constraint}.{_NATIVE_BAKE_DISABLED_ATTR}", True)
                changed += 1
            except Exception as exc:
                self.logger.warning(f"Skipped Bullet preview feedback toggle for '{constraint}': {exc}")
        return changed

    @staticmethod
    def _root_long_name(root_group: str) -> str:
        """Return the long DAG path for a root group."""
        try:
            return (cmds.ls(root_group, long=True) or [root_group])[0]
        except Exception:
            return root_group

    @classmethod
    def _root_descendant_set(cls, root_group: Optional[str]) -> Optional[Set[str]]:
        """Return long-path descendants (transforms + shapes) for a model root.

        Returns None when unscoped / missing. Shape nodes are included explicitly
        because ``listRelatives(allDescendents=True)`` omits shapes by default,
        and legacy OFF must reach ``bulletRigidBodyShape`` nodes.
        """
        if not root_group or not cmds.objExists(root_group):
            return None
        root_long = cls._root_long_name(root_group)
        descendants = set(cmds.listRelatives(root_group, allDescendents=True, fullPath=True) or [])
        descendants.update(
            cmds.listRelatives(root_group, allDescendents=True, fullPath=True, shapes=True) or []
        )
        descendants.add(root_long)
        return descendants

    @classmethod
    def _node_is_under_root(cls, node: str, root_long: str, descendants: Set[str]) -> bool:
        """Return whether *node* belongs under the model root hierarchy."""
        if node in descendants:
            return True
        # Fallback for long-path prefix matching (shapes / renamed paths).
        return node == root_long or node.startswith(root_long + "|")

    def _iter_marked_preview_constraints(self, descendants: Optional[Set[str]]):
        """Yield marked preview constraints scoped to *descendants* when given."""
        constraints = []
        for constraint_type in ("orientConstraint", "pointConstraint"):
            constraints.extend(cmds.ls(type=constraint_type, long=True) or [])
        for constraint in constraints:
            try:
                if not cmds.attributeQuery(_PREVIEW_CONSTRAINT_ATTR, node=constraint, exists=True):
                    continue
                if not cmds.getAttr(f"{constraint}.{_PREVIEW_CONSTRAINT_ATTR}"):
                    continue
                if descendants is not None and not self._preview_constraint_targets_descendants(
                    constraint, descendants
                ):
                    continue
            except Exception:
                continue
            yield constraint

    def _iter_legacy_bullet_solver_nodes(self, root_long: str, descendants: Set[str]):
        """Yield Bullet rigid-body / joint shapes that live under the model root."""
        # Querying an unloaded Bullet node type emits Maya warnings. A model
        # without the Bullet plug-in cannot have nodes that need suspension.
        if not self.is_bullet_available():
            return
        for node_type in _LEGACY_BULLET_SOLVER_NODE_TYPES:
            for node in cmds.ls(type=node_type, long=True) or []:
                if self._node_is_under_root(node, root_long, descendants):
                    yield node

    def _iter_legacy_bullet_setup_nodes(self, root_long: str, descendants: Set[str]):
        """Yield every node the Root legacy-OFF control may suspend."""
        seen: Set[str] = set()
        for node in self._iter_marked_preview_constraints(descendants):
            if node not in seen:
                seen.add(node)
                yield node
        for node in self._iter_legacy_bullet_solver_nodes(root_long, descendants):
            if node not in seen:
                seen.add(node)
                yield node

    def _set_node_legacy_bullet_suspended(self, node: str, suspend: bool) -> bool:
        """Suspend or restore one node via ``mmd_legacy_bullet_*`` metadata.

        Returns True when this call changed nodeState / metadata.
        """
        if suspend:
            if cmds.attributeQuery(_LEGACY_BULLET_DISABLED_ATTR, node=node, exists=True) and cmds.getAttr(
                f"{node}.{_LEGACY_BULLET_DISABLED_ATTR}"
            ):
                return False
            previous_state = int(cmds.getAttr(f"{node}.nodeState"))
            # Preserve pre-existing user-disabled / non-default evaluation state.
            if previous_state != 0:
                return False
            if not cmds.attributeQuery(_LEGACY_BULLET_PREVIOUS_NODE_STATE_ATTR, node=node, exists=True):
                cmds.addAttr(node, longName=_LEGACY_BULLET_PREVIOUS_NODE_STATE_ATTR, attributeType="long")
            if not cmds.attributeQuery(_LEGACY_BULLET_DISABLED_ATTR, node=node, exists=True):
                cmds.addAttr(node, longName=_LEGACY_BULLET_DISABLED_ATTR, attributeType="bool")
            cmds.setAttr(f"{node}.{_LEGACY_BULLET_PREVIOUS_NODE_STATE_ATTR}", previous_state)
            cmds.setAttr(f"{node}.nodeState", _NODE_STATE_BLOCKING)
            cmds.setAttr(f"{node}.{_LEGACY_BULLET_DISABLED_ATTR}", True)
            return True

        if not cmds.attributeQuery(_LEGACY_BULLET_DISABLED_ATTR, node=node, exists=True):
            return False
        if not cmds.getAttr(f"{node}.{_LEGACY_BULLET_DISABLED_ATTR}"):
            return False
        previous_state = 0
        if cmds.attributeQuery(_LEGACY_BULLET_PREVIOUS_NODE_STATE_ATTR, node=node, exists=True):
            previous_state = int(cmds.getAttr(f"{node}.{_LEGACY_BULLET_PREVIOUS_NODE_STATE_ATTR}"))
        cmds.setAttr(f"{node}.nodeState", previous_state)
        cmds.setAttr(f"{node}.{_LEGACY_BULLET_DISABLED_ATTR}", False)
        return True

    @staticmethod
    def _preview_constraint_targets_descendants(constraint: str, descendants: Set[str]) -> bool:
        """Return whether a preview constraint drives a node below the model root."""
        targets = cmds.listConnections(constraint, source=False, destination=True, shapes=False) or []
        for target in targets:
            long_names = cmds.ls(target, long=True) or [target]
            if any(long_name in descendants for long_name in long_names):
                return True
        return False

    def _create_physics_preview_constraint(
        self,
        transform: str,
        joint: str,
        physics_mode: int,
        rigid_body_index: Optional[int] = None,
    ) -> Optional[str]:
        """Create and mark constraints that feed simulated motion into a joint.

        ``physics_mode=1`` is pure dynamic, so the preview feeds back simulated
        translation and rotation.  ``physics_mode=2`` is dynamic-with-bone
        alignment, so it keeps inheriting parent-joint translation and only
        receives simulated orientation.

        Some mode-1 setups such as breast/chest chains lock translation in
        their Bullet joints.  Feeding solved translation back into the related
        bone in that case stretches the skeleton, so only orientation is driven.
        """
        self._delete_marked_preview_constraints(transform, joint)
        point_constraint = None
        drive_translation = self._should_drive_translation(transform, physics_mode, rigid_body_index)
        self._store_orient_only_flag(transform, not drive_translation and int(physics_mode) != 0)
        if drive_translation:
            point = cmds.pointConstraint(transform, joint, maintainOffset=True)
            if point:
                point_constraint = point[0]
                self._mark_physics_preview_constraint(point_constraint)

        constraint = cmds.orientConstraint(transform, joint, maintainOffset=True)
        if not constraint:
            return point_constraint
        constraint_node = constraint[0]
        self._mark_physics_preview_constraint(constraint_node)
        return constraint_node

    def _should_drive_translation(
        self, transform: str, physics_mode: int, rigid_body_index: Optional[int] = None,
    ) -> bool:
        """Decide whether translation feedback should be applied for this rigid body."""
        if int(physics_mode) != 1:
            return False
        if cmds.attributeQuery("mmd_physics_orient_only", node=transform, exists=True):
            return not cmds.getAttr(f"{transform}.mmd_physics_orient_only")
        return True

    @staticmethod
    def _store_orient_only_flag(transform: str, orient_only: bool) -> None:
        """Persist the orient-only decision so the repair path can recover it."""
        if not cmds.attributeQuery("mmd_physics_orient_only", node=transform, exists=True):
            cmds.addAttr(transform, longName="mmd_physics_orient_only", attributeType="bool")
        cmds.setAttr(f"{transform}.mmd_physics_orient_only", orient_only)

    def _mark_physics_preview_constraint(self, constraint_node: str) -> None:
        """Mark a generated physics preview constraint for future repair."""
        if not cmds.attributeQuery(_PREVIEW_CONSTRAINT_ATTR, node=constraint_node, exists=True):
            cmds.addAttr(constraint_node, longName=_PREVIEW_CONSTRAINT_ATTR, attributeType="bool")
        cmds.setAttr(f"{constraint_node}.{_PREVIEW_CONSTRAINT_ATTR}", True)

    def _has_physics_preview_constraint(
        self,
        transform: str,
        joint: str,
        physics_mode: int,
        rigid_body_index: Optional[int] = None,
    ) -> bool:
        """Return True when *transform* already drives *joint* through the expected preview constraints."""
        has_orient = self._has_marked_target_constraint(transform, joint, "orientConstraint")
        drive_translation = self._should_drive_translation(transform, physics_mode, rigid_body_index)
        if not drive_translation:
            return has_orient
        return has_orient and self._has_marked_target_constraint(transform, joint, "pointConstraint")

    def _has_marked_target_constraint(self, transform: str, joint: str, constraint_type: str) -> bool:
        constraints = cmds.listConnections(joint, source=True, destination=False, type=constraint_type) or []
        for constraint in constraints:
            try:
                if not cmds.attributeQuery("mmd_physics_preview_constraint", node=constraint, exists=True):
                    continue
                if constraint_type == "pointConstraint":
                    targets = cmds.pointConstraint(constraint, query=True, targetList=True) or []
                else:
                    targets = cmds.orientConstraint(constraint, query=True, targetList=True) or []
                if any(self._is_same_maya_node(transform, target) for target in targets):
                    return True
            except Exception:
                continue
        return False

    def _delete_marked_preview_constraints(self, transform: str, joint: str) -> None:
        """Remove all marked preview constraints between *transform* and *joint*."""
        for ctype, query_fn in (
            ("parentConstraint", lambda c: cmds.parentConstraint(c, query=True, targetList=True) or []),
            ("orientConstraint", lambda c: cmds.orientConstraint(c, query=True, targetList=True) or []),
            ("pointConstraint", lambda c: cmds.pointConstraint(c, query=True, targetList=True) or []),
        ):
            constraints = set(cmds.listConnections(joint, source=True, destination=False, type=ctype) or [])
            constraints.update(cmds.ls("*.mmd_physics_preview_constraint", objectsOnly=True, type=ctype) or [])
            for constraint in constraints:
                try:
                    if not cmds.attributeQuery("mmd_physics_preview_constraint", node=constraint, exists=True):
                        continue
                    targets = query_fn(constraint)
                    if any(self._is_same_maya_node(transform, target) for target in targets):
                        cmds.delete(constraint)
                except Exception:
                    continue

    @staticmethod
    def _is_same_maya_node(node_a: str, node_b: str) -> bool:
        """Compare Maya nodes after resolving long DAG paths when possible."""
        paths_a = cmds.ls(node_a, long=True) or []
        paths_b = cmds.ls(node_b, long=True) or []
        if paths_a and paths_b:
            return paths_a[0] == paths_b[0]
        return node_a == node_b or node_a.split("|")[-1] == node_b.split("|")[-1]

    def _create_bullet_visual_locator(self, transform: str, bullet_shape: int, size: List[float]) -> None:
        """Add a draw-only collider locator under a Bullet rigid body.

        The locator is a viewport affordance only.  Bullet simulation and PMX
        export continue to read the Bullet shape plus MMD metadata from the
        parent transform.
        """
        if not cmds.objExists(transform):
            return
        if not self._is_bullet_visual_locator_type_available():
            self._record_bullet_visual_locator_failure(
                transform,
                f"{_RIGID_BODY_LOCATOR_TYPE} node type is unavailable",
            )
            return

        try:
            radius = max(float(size[0]) if size else 0.0, 0.001)
            created = cmds.createNode(
                _RIGID_BODY_LOCATOR_TYPE,
                name=f"{transform.split('|')[-1]}_colliderLocatorShape",
                parent=transform,
            )
            locator = self._resolve_bullet_visual_locator(created, transform)
            if not locator:
                self._record_bullet_visual_locator_failure(
                    transform,
                    f"created locator is not resolvable: {created}",
                    created_locator=created,
                )
                return
            cmds.setAttr(f"{locator}.colliderShapeType", int(bullet_shape))
            cmds.setAttr(f"{locator}.radius", radius)
            length = float(size[1]) + radius * 2.0 if len(size) > 1 else radius * 2.0
            cmds.setAttr(f"{locator}.length", max(length, radius * 2.0))
            if len(size) >= 3:
                # Box rigid bodies encode their full extents in the parent
                # transform scale.  The locator shape must stay unit-sized so
                # VP2 does not apply the same dimensions twice.
                box_size = (1.0, 1.0, 1.0) if bullet_shape == 1 else (
                    max(float(size[0]) * 2.0, 0.001),
                    max(float(size[1]) * 2.0, 0.001),
                    max(float(size[2]) * 2.0, 0.001),
                )
                cmds.setAttr(f"{locator}.boxSizeX", box_size[0])
                cmds.setAttr(f"{locator}.boxSizeY", box_size[1])
                cmds.setAttr(f"{locator}.boxSizeZ", box_size[2])
            self._create_bullet_visual_curves(transform, locator, int(bullet_shape), radius, max(length, radius * 2.0), size)
            root = self._current_model_root
            if root and cmds.objExists(root):
                connect_visibility_attr_to_node(
                    MayaCmdsAdapter(cmds),
                    root,
                    "colliders",
                    locator,
                    target_attr="drawEnabled",
                )
            else:
                cmds.setAttr(f"{locator}.drawEnabled", False)
        except Exception as exc:
            self._record_bullet_visual_locator_failure(transform, str(exc))

    def _create_bullet_visual_curves(
        self,
        transform: str,
        locator: str,
        bullet_shape: int,
        radius: float,
        length: float,
        size: List[float],
    ) -> None:
        """Create always-on-top curve wires as a VP2/DX11-friendly display path."""
        try:
            curves_group = cmds.group(empty=True, name=f"{transform.split('|')[-1]}_colliderCurve", parent=transform)
            cmds.setAttr(f"{curves_group}.inheritsTransform", True)
            curve_paths = []
            if bullet_shape == 1:
                curve_paths.extend(self._create_box_curves(curves_group))
            elif bullet_shape == 3:
                curve_paths.extend(self._create_capsule_curves(curves_group, radius, length))
            else:
                curve_paths.extend(self._create_sphere_curves(curves_group, radius))
            for curve in curve_paths:
                self._style_collider_curve(curve)
            root = self._current_model_root
            if root and cmds.objExists(root):
                connect_visibility_attr_to_node(
                    MayaCmdsAdapter(cmds),
                    root,
                    "colliders",
                    curves_group,
                    target_attr="visibility",
                )
            else:
                cmds.setAttr(f"{curves_group}.visibility", bool(cmds.getAttr(f"{locator}.drawEnabled")))
        except Exception:
            return

    @staticmethod
    def _create_curve(parent: str, name: str, points: List[Tuple[float, float, float]]) -> List[str]:
        curve = cmds.curve(degree=1, point=points, name=name)
        curve = cmds.parent(curve, parent, relative=True)[0]
        return [curve]

    def _create_sphere_curves(self, parent: str, radius: float) -> List[str]:
        return (
            self._create_curve(parent, "colliderSphereXY", self._circle_points("xy", radius))
            + self._create_curve(parent, "colliderSphereXZ", self._circle_points("xz", radius))
            + self._create_curve(parent, "colliderSphereYZ", self._circle_points("yz", radius))
        )

    def _create_capsule_curves(self, parent: str, radius: float, length: float) -> List[str]:
        cylinder_half = max((length - radius * 2.0) * 0.5, 0.0)
        top_y = cylinder_half
        bottom_y = -cylinder_half
        curves = []
        curves += self._create_curve(parent, "colliderCapsuleTop", self._circle_points("xz", radius, top_y))
        curves += self._create_curve(parent, "colliderCapsuleBottom", self._circle_points("xz", radius, bottom_y))
        curves += self._create_curve(parent, "colliderCapsuleXYTop", self._circle_points("xy", radius, top_y))
        curves += self._create_curve(parent, "colliderCapsuleXYBottom", self._circle_points("xy", radius, bottom_y))
        curves += self._create_curve(parent, "colliderCapsuleYZTop", self._circle_points("yz", radius, top_y))
        curves += self._create_curve(parent, "colliderCapsuleYZBottom", self._circle_points("yz", radius, bottom_y))
        for index, (x, z) in enumerate(((radius, 0.0), (-radius, 0.0), (0.0, radius), (0.0, -radius))):
            curves += self._create_curve(parent, f"colliderCapsuleSide{index}", [(x, bottom_y, z), (x, top_y, z)])
        return curves

    def _create_box_curves(self, parent: str) -> List[str]:
        corners = [
            (-0.5, -0.5, -0.5),
            (0.5, -0.5, -0.5),
            (0.5, -0.5, 0.5),
            (-0.5, -0.5, 0.5),
            (-0.5, 0.5, -0.5),
            (0.5, 0.5, -0.5),
            (0.5, 0.5, 0.5),
            (-0.5, 0.5, 0.5),
        ]
        edges = [
            (0, 1), (1, 2), (2, 3), (3, 0),
            (4, 5), (5, 6), (6, 7), (7, 4),
            (0, 4), (1, 5), (2, 6), (3, 7),
        ]
        return [
            curve
            for index, (start, end) in enumerate(edges)
            for curve in self._create_curve(parent, f"colliderBoxEdge{index}", [corners[start], corners[end]])
        ]

    @staticmethod
    def _circle_points(
        axis: str,
        radius: float,
        offset_y: float = 0.0,
        segments: int = 32,
    ) -> List[Tuple[float, float, float]]:
        points = []
        for index in range(segments + 1):
            angle = 2.0 * math.pi * index / segments
            a = math.cos(angle) * radius
            b = math.sin(angle) * radius
            if axis == "xy":
                points.append((a, b + offset_y, 0.0))
            elif axis == "xz":
                points.append((a, offset_y, b))
            else:
                points.append((0.0, a + offset_y, b))
        return points

    @staticmethod
    def _style_collider_curve(curve: str) -> None:
        cmds.setAttr(f"{curve}.overrideEnabled", True)
        cmds.setAttr(f"{curve}.overrideRGBColors", True)
        cmds.setAttr(f"{curve}.overrideColorRGB", 0.1, 0.8, 1.0, type="double3")
        for shape in cmds.listRelatives(curve, shapes=True, fullPath=True) or []:
            cmds.setAttr(f"{shape}.overrideEnabled", True)
            cmds.setAttr(f"{shape}.overrideRGBColors", True)
            cmds.setAttr(f"{shape}.overrideColorRGB", 0.1, 0.8, 1.0, type="double3")
            if cmds.attributeQuery("alwaysDrawOnTop", node=shape, exists=True):
                cmds.setAttr(f"{shape}.alwaysDrawOnTop", True)

    @staticmethod
    def _is_bullet_visual_locator_type_available() -> bool:
        """Return whether Maya has the custom locator type registered."""
        try:
            if _RIGID_BODY_LOCATOR_TYPE in (cmds.allNodeTypes() or []):
                return True
            plugin_path = Path(__file__).resolve().parents[1] / "nodes" / "mmd_rigid_body_locator_plugin.py"
            if plugin_path.exists():
                cmds.loadPlugin(str(plugin_path), quiet=True)
            return _RIGID_BODY_LOCATOR_TYPE in (cmds.allNodeTypes() or [])
        except Exception:
            return False

    @staticmethod
    def _resolve_bullet_visual_locator(locator: str, transform: str) -> Optional[str]:
        """Resolve a just-created locator shape to a setAttr-safe long DAG path."""
        if locator:
            matches = cmds.ls(locator, long=True) or []
            if matches:
                return matches[0]
        child_shapes = cmds.listRelatives(
            transform,
            shapes=True,
            type=_RIGID_BODY_LOCATOR_TYPE,
            fullPath=True,
        ) or []
        return child_shapes[-1] if child_shapes else None

    def _record_bullet_visual_locator_failure(
        self,
        transform: str,
        reason: str,
        *,
        created_locator: Optional[str] = None,
    ) -> None:
        """Record a visual-locator failure for import profile diagnostics."""
        record: Dict[str, object] = {
            "transform": transform,
            "reason": reason,
        }
        if created_locator:
            record["created_locator"] = created_locator
        self.bullet_visual_locator_failures.append(record)
        warning_key = self._bullet_visual_locator_warning_key(reason)
        if warning_key not in self._bullet_visual_locator_warning_keys:
            self._bullet_visual_locator_warning_keys.add(warning_key)
            self.logger.warning(f"Skipped Bullet visual locator(s): {reason}")

    @staticmethod
    def _bullet_visual_locator_warning_key(reason: str) -> str:
        """Group noisy per-node locator failures into stable log categories."""
        if "node type is unavailable" in reason:
            return "node_type_unavailable"
        if "not resolvable" in reason:
            return "not_resolvable"
        if "setAttr" in reason:
            return "set_attr_failed"
        return reason.split(":", 1)[0]

    def _log_bullet_visual_locator_summary(self) -> None:
        """Emit one summary line for visual-locator failures."""
        if not self.bullet_visual_locator_failures:
            return
        counts: Dict[str, int] = {}
        for failure in self.bullet_visual_locator_failures:
            key = self._bullet_visual_locator_warning_key(str(failure.get("reason", "")))
            counts[key] = counts.get(key, 0) + 1
        summary = ", ".join(f"{key}={count}" for key, count in sorted(counts.items()))
        self.logger.warning(
            "Bullet visual locator diagnostics: %d failure(s) (%s)",
            len(self.bullet_visual_locator_failures),
            summary,
        )

    def _get_bullet_solver(self) -> Optional[str]:
        """Maya Bullet solver shape を取得または作成する。"""
        if self.bullet_solver and cmds.objExists(self.bullet_solver):
            self._configure_bullet_solver(self.bullet_solver)
            return self.bullet_solver
        try:
            from maya.app.mayabullet import BulletUtils

            self.bullet_solver = BulletUtils.getSolver()
            self._configure_bullet_solver(self.bullet_solver)
            return self.bullet_solver
        except Exception as exc:
            self.logger.warning(f"Failed to get Bullet solver: {exc}")
            return None

    def _configure_bullet_solver(self, solver: str) -> None:
        """Apply importer physics settings to Maya Bullet solver attributes."""
        if not solver or not cmds.objExists(solver):
            return

        fixed_frame_rate = self._resolve_bullet_fixed_frame_rate()
        settings_by_attr = {
            "maxNumIterations": int(self.settings.get(_OPT_SOLVER_ITERATIONS, 10)),
            "internalFixedFrameRate": fixed_frame_rate,
            "splitImpulse": bool(self.settings.get(_OPT_SPLIT_IMPULSE, True)),
        }
        for attr, value in settings_by_attr.items():
            self._set_attr_if_exists(solver, attr, value)

        gravity = self._resolve_bullet_gravity()
        if gravity is not None:
            self._set_attr_if_exists(solver, "gravity", gravity, attr_type="float3")

    def _resolve_bullet_fixed_frame_rate(self) -> int:
        """Resolve Maya Bullet fixed frame rate from explicit value or quality."""
        explicit = self.settings.get(_OPT_BULLET_FIXED_FRAME_RATE)
        try:
            if explicit is not None:
                value = int(explicit)
                return min((60, 120, 240), key=lambda candidate: abs(candidate - value))
        except (TypeError, ValueError):
            pass

        quality = str(self.settings.get(_OPT_SIMULATION_QUALITY, "medium")).lower()
        return {"low": 60, "medium": 120, "high": 240}.get(quality, 120)

    def _resolve_bullet_gravity(self) -> Optional[tuple]:
        """Resolve gravity as a Maya Bullet vector."""
        gravity = self.settings.get(_OPT_GRAVITY, 98.0)
        if isinstance(gravity, (list, tuple)) and len(gravity) >= 3:
            try:
                return (float(gravity[0]), float(gravity[1]), float(gravity[2]))
            except (TypeError, ValueError):
                return None
        try:
            return (0.0, -abs(float(gravity)), 0.0)
        except (TypeError, ValueError):
            return None

    def _set_attr_if_exists(self, node: str, attr: str, value, attr_type: Optional[str] = None) -> None:
        """Set a Maya attr when it exists, ignoring unsupported Bullet variants."""
        try:
            if not cmds.attributeQuery(attr, node=node, exists=True):
                return
            if attr_type:
                cmds.setAttr(f"{node}.{attr}", *value, type=attr_type)
            else:
                cmds.setAttr(f"{node}.{attr}", value)
        except Exception as exc:
            self.logger.debug(f"Skipping Bullet solver attr {node}.{attr}: {exc}")

    def _connect_attr_if_possible(
        self,
        source: str,
        destination: str,
        next_available: bool = False,
        force: bool = False,
    ) -> None:
        """既存接続を壊さず、存在する plug だけを接続する。"""
        try:
            if not cmds.objExists(source) or not cmds.objExists(destination):
                return
            if not next_available and not force and cmds.listConnections(destination, source=True, destination=False):
                return
            kwargs = {"force": True}
            if next_available:
                kwargs = {"nextAvailable": True}
            cmds.connectAttr(source, destination, **kwargs)
        except Exception as exc:
            self.logger.debug(f"Skipping Bullet attr connection: {source} -> {destination}: {exc}")

    def _set_bullet_collision_filter(self, shape: str, rb, model_type: str) -> None:
        """Apply MMD collision group/mask to Maya Bullet filter attributes."""
        if model_type == "pmd":
            collision_group = getattr(rb, "collision_group", 0)
        else:
            collision_group = getattr(rb, "group", 0)
        collision_mask = getattr(rb, "collision_mask", 0xFFFF)

        values = {
            "collisionFilterGroup": _mmd_collision_group_to_bullet_filter_group(collision_group),
            "collisionFilterMask": _mmd_collision_mask_to_bullet_filter_mask(collision_mask),
        }
        for attr, value in values.items():
            try:
                if cmds.attributeQuery(attr, node=shape, exists=True):
                    cmds.setAttr(f"{shape}.{attr}", value)
            except Exception as exc:
                self.logger.debug(f"Skipping Bullet collision filter attr {shape}.{attr}: {exc}")

    def _connect_bullet_rigid_body(self, transform: str, shape: str) -> None:
        """bulletRigidBodyShape を solver と transform に接続する。"""
        solver = self._get_bullet_solver()
        if not solver:
            return

        self._connect_attr_if_possible(f"{transform}.worldMatrix[0]", f"{shape}.inWorldMatrix")
        self._connect_attr_if_possible(f"{transform}.parentInverseMatrix[0]", f"{shape}.inParentInverseMatrix")
        self._connect_attr_if_possible(f"{solver}.outSolverInitialized", f"{shape}.solverInitialized")
        self._connect_attr_if_possible(f"{solver}.outSolverUpdated", f"{shape}.solverUpdated")
        self._connect_attr_if_possible(f"{shape}.outRigidBodyData", f"{solver}.rigidBodies", next_available=True)
        self._connect_attr_if_possible(f"{solver}.startTime", f"{shape}.startTime")
        self._connect_attr_if_possible(f"{solver}.currentTime", f"{shape}.currentTime")
        self._connect_attr_if_possible(f"{transform}.rotatePivot", f"{shape}.pivotTranslate")

        translate = cmds.xform(transform, query=True, worldSpace=True, translation=True)
        rotate = cmds.xform(transform, query=True, worldSpace=True, rotation=True)
        cmds.setAttr(f"{shape}.initialTranslate", *translate, type="double3")
        cmds.setAttr(f"{shape}.initialRotateX", math.radians(rotate[0]))
        cmds.setAttr(f"{shape}.initialRotateY", math.radians(rotate[1]))
        cmds.setAttr(f"{shape}.initialRotateZ", math.radians(rotate[2]))

    def _connect_bullet_simulation_output(self, transform: str, shape: str, *, drive_translation: bool = True) -> None:
        """Drive a dynamic Bullet rigid body transform from solved output."""
        if not cmds.objExists(transform) or not cmds.objExists(shape):
            return

        translate = cmds.xform(transform, query=True, objectSpace=True, translation=True)
        rotate = cmds.xform(transform, query=True, objectSpace=True, rotation=True)
        pair_blend = cmds.createNode("pairBlend", name=f"{transform.split('|')[-1]}_bulletSolveBlend")
        cmds.setAttr(f"{pair_blend}.inTranslate1", *translate, type="double3")
        cmds.setAttr(f"{pair_blend}.inRotate1", *rotate, type="double3")
        cmds.setAttr(f"{pair_blend}.weight", 1.0)

        if not cmds.attributeQuery("isDrivenBySimulation", node=transform, exists=True):
            cmds.addAttr(transform, longName="isDrivenBySimulation", attributeType="bool", defaultValue=True, keyable=True)
        cmds.setAttr(f"{transform}.isDrivenBySimulation", True)

        self._connect_attr_if_possible(f"{shape}.outSolvedTranslate", f"{pair_blend}.inTranslate2")
        self._connect_attr_if_possible(f"{shape}.outSolvedRotate", f"{pair_blend}.inRotate2")
        self._connect_attr_if_possible(f"{transform}.isDrivenBySimulation", f"{pair_blend}.weight")

        if drive_translation:
            for axis in "XYZ":
                self._connect_attr_if_possible(
                    f"{pair_blend}.outTranslate{axis}",
                    f"{transform}.translate{axis}",
                    force=True,
                )
        for axis in "XYZ":
            self._connect_attr_if_possible(
                f"{pair_blend}.outRotate{axis}",
                f"{transform}.rotate{axis}",
                force=True,
            )

    def _add_rigid_body_custom_attrs(self, transform: str, rb, rigid_body_index: int, physics_mode: int, model_type: str):
        """MMD 剛体メタデータをカスタムアトリビュートとして追加する。"""
        attrs: Dict[str, object] = {
            "mmd_rigid_body_name": str(getattr(rb, "name", "")),
            "mmd_rigid_body_name_english": str(getattr(rb, "name_english", getattr(rb, "name", ""))),
            "mmd_rigid_body_index": rigid_body_index,
            "mmd_physics_mode": physics_mode,
        }
        if model_type == "pmd":
            attrs["mmd_collision_group"] = getattr(rb, "collision_group", 0)
        elif model_type == "pmx":
            attrs["mmd_collision_group"] = getattr(rb, "group", 0)
            attrs["mmd_related_bone_index"] = getattr(rb, "related_bone_index", -1)
        attrs["mmd_collision_mask"] = getattr(rb, "collision_mask", 0xFFFF)
        maya_attribute_utils.set_custom_attributes(transform, attrs)

    def _attach_physics_group_to_model_motion_parent(
        self,
        physics_group: str,
        root_group: str,
        bone_index_map: Dict[int, str],
    ) -> None:
        """Make model-level MMD motion, such as 全ての親, carry physics nodes."""
        motion_parent = self._find_model_motion_parent(root_group, bone_index_map)
        if not motion_parent or not cmds.objExists(motion_parent):
            return
        if motion_parent == physics_group:
            return
        try:
            constraint = cmds.parentConstraint(motion_parent, physics_group, maintainOffset=True)
            if constraint:
                self.logger.debug(
                    "Connected physics group '%s' to model motion parent '%s'",
                    physics_group,
                    motion_parent,
                )
        except Exception as exc:
            self.logger.warning(
                "Skipped physics group model-motion follow connection '%s' -> '%s': %s",
                motion_parent,
                physics_group,
                exc,
            )

    def _find_model_motion_parent(
        self,
        root_group: str,
        bone_index_map: Dict[int, str],
    ) -> Optional[str]:
        """Find the imported all-parent/master transform within one model root."""
        seen = set()
        for node in self._iter_model_motion_parent_candidates(root_group, bone_index_map):
            if not node or node in seen or not cmds.objExists(node):
                continue
            seen.add(node)
            if self._is_model_motion_parent(node):
                return node
        return None

    def _iter_model_motion_parent_candidates(
        self,
        root_group: str,
        bone_index_map: Dict[int, str],
    ) -> List[str]:
        """Return only skeleton-scoped candidates for model-level motion."""
        candidates = list(bone_index_map.values())
        if not root_group or not cmds.objExists(root_group):
            return candidates

        root_children = cmds.listRelatives(root_group, children=True, fullPath=True, type="transform") or []
        skeleton_groups = [
            child for child in root_children
            if self._short_node_name(child) == SKELETON_GROUP
        ]
        for skeleton_group in skeleton_groups:
            candidates.extend(cmds.listRelatives(skeleton_group, allDescendents=True, fullPath=True) or [])
        return candidates

    @staticmethod
    def _is_model_motion_parent(node: str) -> bool:
        short_name = PhysicsConverter._short_node_name(node)
        if short_name in _MODEL_MOTION_PARENT_NAMES:
            return True

        for attr in (ATTR_MMD_BONE_NAME, ATTR_MMD_BONE_NAME_EN):
            try:
                if cmds.attributeQuery(attr, node=node, exists=True):
                    value = cmds.getAttr(f"{node}.{attr}")
                    if value in _MODEL_MOTION_PARENT_NAMES:
                        return True
            except Exception:
                continue
        return False

    @staticmethod
    def _short_node_name(node: str) -> str:
        return node.split("|")[-1].split(":")[-1]

    # ------------------------------------------------------------------
    # Bullet ジョイント作成: PMD (常に sixDOF=4)
    # ------------------------------------------------------------------

    def _create_bullet_constraint_pmd(self, joint, rigid_bodies: list, parent_group: str) -> Optional[str]:
        """PMD ジョイント → bulletRigidBodyConstraintShape (SixDOF)。"""
        try:
            idx_a = getattr(joint, "rigid_body_index_a", -1)
            idx_b = getattr(joint, "rigid_body_index_b", -1)
            if idx_a < 0 or idx_b < 0 or idx_a >= len(rigid_bodies) or idx_b >= len(rigid_bodies):
                self.logger.debug(f"PMD joint '{getattr(joint, 'name', '?')}' has an invalid rigid body index. Skipping")
                return None

            # PMD 回転リミットは degree 単位
            rot_lo = joint.rotation_limit_min if hasattr(joint, "rotation_limit_min") else None
            rot_hi = joint.rotation_limit_max if hasattr(joint, "rotation_limit_max") else None

            return self._create_bullet_constraint_common(
                joint=joint,
                constraint_type=4,  # SixDOF
                parent_group=parent_group,
                rb_shape_a=self._bullet_rigid_body_shapes_by_index.get(idx_a),
                rb_shape_b=self._bullet_rigid_body_shapes_by_index.get(idx_b),
                trans_lo=joint.translation_limit_min if hasattr(joint, "translation_limit_min") else None,
                trans_hi=joint.translation_limit_max if hasattr(joint, "translation_limit_max") else None,
                # PMD の回転リミットは degree (そのまま設定)
                rot_lo_deg=rot_lo,
                rot_hi_deg=rot_hi,
                spring_trans=joint.spring_translation if hasattr(joint, "spring_translation") else None,
                spring_rot=joint.spring_rotation if hasattr(joint, "spring_rotation") else None,
            )
        except Exception as e:
            safe_log_error(self.logger, "PMD joint 作成エラー", joint, e)
            return None

    # ------------------------------------------------------------------
    # Bullet ジョイント作成: PMX
    # ------------------------------------------------------------------

    def _create_bullet_constraint_pmx(self, joint, rigid_bodies: list, parent_group: str) -> Optional[str]:
        """PMX ジョイント → bulletRigidBodyConstraintShape (joint_type に応じて)。"""
        try:
            idx_a = getattr(joint, "rigid_body_a_index", -1)
            idx_b = getattr(joint, "rigid_body_b_index", -1)
            if idx_a < 0 or idx_b < 0 or idx_a >= len(rigid_bodies) or idx_b >= len(rigid_bodies):
                self.logger.debug(f"PMX joint '{getattr(joint, 'name', '?')}' has an invalid rigid body index. Skipping")
                return None

            pmx_joint_type = getattr(joint, "joint_type", 0)
            spring_trans = joint.spring_translation if hasattr(joint, "spring_translation") else None
            spring_rot = joint.spring_rotation if hasattr(joint, "spring_rotation") else None
            bullet_ct = _resolve_pmx_bullet_constraint_type(pmx_joint_type, spring_trans, spring_rot)

            # PMX 回転リミットは radian → degree 変換
            rot_lo = joint.rotation_limit_min if hasattr(joint, "rotation_limit_min") else None
            rot_hi = joint.rotation_limit_max if hasattr(joint, "rotation_limit_max") else None
            rot_lo_deg = tuple(math.degrees(v) for v in rot_lo) if rot_lo else None
            rot_hi_deg = tuple(math.degrees(v) for v in rot_hi) if rot_hi else None

            return self._create_bullet_constraint_common(
                joint=joint,
                constraint_type=bullet_ct,
                pmx_joint_type=pmx_joint_type,
                parent_group=parent_group,
                rb_shape_a=self._bullet_rigid_body_shapes_by_index.get(idx_a),
                rb_shape_b=self._bullet_rigid_body_shapes_by_index.get(idx_b),
                trans_lo=joint.translation_limit_min if hasattr(joint, "translation_limit_min") else None,
                trans_hi=joint.translation_limit_max if hasattr(joint, "translation_limit_max") else None,
                rot_lo_deg=rot_lo_deg,
                rot_hi_deg=rot_hi_deg,
                spring_trans=spring_trans,
                spring_rot=spring_rot,
            )
        except Exception as e:
            safe_log_error(self.logger, "PMX joint 作成エラー", joint, e)
            return None

    # ------------------------------------------------------------------
    # Bullet ジョイント作成共通
    # ------------------------------------------------------------------

    def _create_bullet_constraint_common(
        self,
        joint,
        constraint_type: int,
        parent_group: str,
        rb_shape_a: Optional[str],
        rb_shape_b: Optional[str],
        trans_lo: Optional[Tuple[float, float, float]] = None,
        trans_hi: Optional[Tuple[float, float, float]] = None,
        rot_lo_deg: Optional[Tuple[float, float, float]] = None,
        rot_hi_deg: Optional[Tuple[float, float, float]] = None,
        spring_trans: Optional[Tuple[float, float, float]] = None,
        spring_rot: Optional[Tuple[float, float, float]] = None,
        pmx_joint_type: Optional[int] = None,
    ) -> Optional[str]:
        """
        bulletRigidBodyConstraintShape を作成する共通処理。

        Actual attributes (Maya 2024):
          - constraintType: Point=0, Hinge=1, Slider=2, ConeTwist=3, SixDOF=4, SpringSixDOF=5, SpringHinge=6
          - linearConstraintMin{X,Y,Z}, linearConstraintMax{X,Y,Z}
          - angularConstraintMin{X,Y,Z}, angularConstraintMax{X,Y,Z} (degrees)
          - linearConstraint{X,Y,Z}: Free=0, Locked=1, Limited=2
          - angularConstraint{X,Y,Z}: Free=0, Locked=1, Limited=2
          - linearSpringStiffness{X,Y,Z}, linearSpringDamping{X,Y,Z}
          - angularSpringStiffness{X,Y,Z}, angularSpringDamping{X,Y,Z}
          - linearSpringEnabled{X,Y,Z}, angularSpringEnabled{X,Y,Z}
          - rigidBodyA, rigidBodyB (message connections)
        """
        jname = maya_name_utils.sanitize_text(getattr(joint, "name", "")) or "joint"
        transform = cmds.createNode("transform", name=f"{jname}_bulletConstraint")
        shape = cmds.createNode("bulletRigidBodyConstraintShape", name=f"{jname}_bulletConstraintShape", parent=transform)

        # 位置・回転
        if hasattr(joint, "position") and joint.position:
            if hasattr(joint, "rotation") and joint.rotation:
                cmds.xform(
                    transform,
                    ws=True,
                    matrix=self._mmd_to_maya_transform_matrix(joint.position, joint.rotation, self.physics_scale),
                )
            else:
                maya_pos = self._mmd_to_maya_position(joint.position, self.physics_scale)
                cmds.xform(transform, ws=True, t=maya_pos)

        # constraintType
        cmds.setAttr(f"{shape}.constraintType", constraint_type)

        # Rigid Body A / B 接続
        if rb_shape_a and cmds.objExists(rb_shape_a):
            self._connect_attr_if_possible(f"{rb_shape_a}.outRigidBodyData", f"{shape}.rigidBodyA")
        else:
            self.logger.warning(f"Bullet constraint '{jname}': rigidBodyA connection target not found")
        if rb_shape_b and cmds.objExists(rb_shape_b):
            self._connect_attr_if_possible(f"{rb_shape_b}.outRigidBodyData", f"{shape}.rigidBodyB")
        else:
            self.logger.warning(f"Bullet constraint '{jname}': rigidBodyB connection target not found")

        solver = self._get_bullet_solver()
        if solver:
            self._connect_attr_if_possible(f"{solver}.outSolverInitialized", f"{shape}.solverInitialized")
            self._connect_attr_if_possible(f"{shape}.outConstraintData", f"{solver}.rigidBodyConstraints", next_available=True)
            self._connect_attr_if_possible(f"{solver}.startTime", f"{shape}.startTime")
            self._connect_attr_if_possible(f"{solver}.currentTime", f"{shape}.currentTime")

        # トランスレーションリミット
        if trans_lo and trans_hi:
            axes = ["X", "Y", "Z"]
            for axis, lo, hi in zip(axes, trans_lo, trans_hi):
                if lo == hi == 0.0:
                    # locked: 両端とも 0 なら固定
                    cmds.setAttr(f"{shape}.linearConstraint{axis}", _CONSTRAINT_LOCKED)
                else:
                    cmds.setAttr(f"{shape}.linearConstraintMin{axis}", lo)
                    cmds.setAttr(f"{shape}.linearConstraintMax{axis}", hi)
                    cmds.setAttr(f"{shape}.linearConstraint{axis}", _CONSTRAINT_LIMITED)

        # ローテーションリミット (degrees)
        if rot_lo_deg and rot_hi_deg:
            axes = ["X", "Y", "Z"]
            for axis, lo, hi in zip(axes, rot_lo_deg, rot_hi_deg):
                if lo == hi == 0.0:
                    cmds.setAttr(f"{shape}.angularConstraint{axis}", _CONSTRAINT_LOCKED)
                else:
                    cmds.setAttr(f"{shape}.angularConstraintMin{axis}", lo)
                    cmds.setAttr(f"{shape}.angularConstraintMax{axis}", hi)
                    cmds.setAttr(f"{shape}.angularConstraint{axis}", _CONSTRAINT_LIMITED)

        # スプリング
        if spring_trans:
            axes = ["X", "Y", "Z"]
            for axis, val in zip(axes, spring_trans):
                if val != 0.0:
                    cmds.setAttr(f"{shape}.linearSpringStiffness{axis}", val)
                    cmds.setAttr(f"{shape}.linearSpringEnabled{axis}", True)
        if spring_rot:
            axes = ["X", "Y", "Z"]
            for axis, val in zip(axes, spring_rot):
                if val != 0.0:
                    cmds.setAttr(f"{shape}.angularSpringStiffness{axis}", val)
                    cmds.setAttr(f"{shape}.angularSpringEnabled{axis}", True)

        # カスタムアトリビュート
        export_joint_type = pmx_joint_type if pmx_joint_type is not None else constraint_type
        custom_attrs = {
            "mmd_joint_name": str(getattr(joint, "name", "")),
            "mmd_joint_name_english": str(getattr(joint, "name_english", getattr(joint, "name", ""))),
            "mmd_joint_type": export_joint_type,
        }
        if pmx_joint_type is not None:
            custom_attrs["mmd_joint_is_pmx"] = 1

        maya_attribute_utils.set_custom_attributes(transform, custom_attrs)

        parented = cmds.parent(transform, parent_group)
        if parented:
            transform = parented[0]
        self.created_bullet_constraints.append(transform)
        return transform

    # ------------------------------------------------------------------
    # Nucleus / nCloth fallback (既存実装)
    # ------------------------------------------------------------------

    def _convert_pmd_physics_nucleus(self, pmd_data, bone_joints: Dict[str, str], root_group: str) -> Tuple[List[str], List[str]]:
        return self._nucleus_fallback_impl(pmd_data, bone_joints, root_group)

    def _convert_pmx_physics_nucleus(self, pmx_data, bone_joints: Dict[str, str], root_group: str) -> Tuple[List[str], List[str]]:
        return self._nucleus_fallback_impl(pmx_data, bone_joints, root_group)

    def _nucleus_fallback_impl(self, data, bone_joints: Dict[str, str], root_group: str) -> Tuple[List[str], List[str]]:
        """既存の Nucleus/nCloth/nRigid 経路。"""
        self.created_ncloth_nodes = []
        self.created_nrigid_nodes = []
        self.created_constraint_nodes = []

        physics_group = cmds.group(empty=True, name=PHYSICS_GROUP, parent=root_group)
        rigid_bodies_group = cmds.group(empty=True, name=RIGID_BODIES_GROUP, parent=physics_group)
        constraints_group = cmds.group(empty=True, name=CONSTRAINTS_GROUP, parent=physics_group)

        self._ensure_nucleus_solver()
        bone_index_map = self._create_bone_index_mapping(data.bones, bone_joints)
        rigid_body_groups = self._analyze_rigid_bodies(data.rigid_bodies)

        for group_type, rigid_bodies in rigid_body_groups.items():
            if group_type == PHYSICS_TYPE_HAIR and self.settings.get(_OPT_ENABLE_HAIR_PHYSICS, True):
                self._create_hair_physics(rigid_bodies, bone_joints, bone_index_map, rigid_bodies_group)
            elif group_type == PHYSICS_TYPE_CLOTH and self.settings.get(_OPT_ENABLE_CLOTH_PHYSICS, True):
                self._create_cloth_physics(rigid_bodies, bone_joints, bone_index_map, rigid_bodies_group)
            elif group_type == PHYSICS_TYPE_RIGID:
                self._create_rigid_physics(rigid_bodies, bone_joints, bone_index_map, rigid_bodies_group)
            else:
                self.logger.debug(f"Physics type '{group_type}' was skipped")

        if hasattr(data, "joints") and data.joints:
            self._create_constraints(data.joints, data.rigid_bodies, constraints_group)

        self.logger.info(
            f"Nucleus physics conversion complete: nCloth={len(self.created_ncloth_nodes)}, "
            f"nRigid={len(self.created_nrigid_nodes)}, "
            f"Constraints={len(self.created_constraint_nodes)}"
        )
        return (self.created_ncloth_nodes, self.created_constraint_nodes)

    # ------------------------------------------------------------------
    # Nucleus ソルバー管理
    # ------------------------------------------------------------------

    def _ensure_nucleus_solver(self) -> str:
        if self.nucleus_solver and maya_scene_utils.object_exists(self.nucleus_solver):
            return self.nucleus_solver
        self.nucleus_solver = maya_physics_utils.find_or_create_nucleus_solver("mmd_nucleus")
        self.logger.info(f"Nucleus solver: {self.nucleus_solver}")
        self._configure_nucleus_solver()
        return self.nucleus_solver

    def _configure_nucleus_solver(self):
        if not self.nucleus_solver or not maya_scene_utils.object_exists(self.nucleus_solver):
            return
        quality_settings = {
            "low": {"substeps": 2, "maxCollisionIterations": 4},
            "medium": {"substeps": 3, "maxCollisionIterations": 8},
            "high": {"substeps": 5, "maxCollisionIterations": 12},
        }
        quality = self.settings.get(_OPT_SIMULATION_QUALITY, "medium")
        qs = quality_settings.get(quality, quality_settings["medium"])
        maya_attribute_utils.set_attribute(self.nucleus_solver, "subSteps", qs["substeps"], "long")
        maya_attribute_utils.set_attribute(self.nucleus_solver, "maxCollisionIterations", qs["maxCollisionIterations"], "long")
        maya_attribute_utils.set_attribute(
            self.nucleus_solver,
            "startFrame",
            self.settings.get(_OPT_START_FRAME, 1),
            "double",
        )
        maya_attribute_utils.set_attribute(
            self.nucleus_solver,
            "timeScale",
            self.settings.get(_OPT_TIME_SCALE, 1.0),
            "double",
        )
        maya_attribute_utils.set_attribute(self.nucleus_solver, "gravity", 98.0, "double")
        maya_attribute_utils.set_attribute(self.nucleus_solver, "gravityDirection", [0, -1, 0], "double3")
        self.logger.debug(f"Nucleus solver setup complete: quality={quality}")

    # ------------------------------------------------------------------
    # 剛体分析 / 種別判定 (既存)
    # ------------------------------------------------------------------

    def _analyze_rigid_bodies(self, rigid_bodies: List[Union[PmdRigidBody, PmxRigidBody]]) -> Dict[str, List]:
        groups = {PHYSICS_TYPE_HAIR: [], PHYSICS_TYPE_CLOTH: [], PHYSICS_TYPE_RIGID: [], PHYSICS_TYPE_SOFT: []}
        if not self.settings.get(_OPT_AUTO_DETECT_TYPE, True):
            groups[PHYSICS_TYPE_RIGID] = rigid_bodies
            return groups
        for rb in rigid_bodies:
            groups[self._analyze_physics_type(rb)].append(rb)
        return groups

    def _analyze_physics_type(self, rigid_body: Union[PmdRigidBody, PmxRigidBody]) -> str:
        name_lower = rigid_body.name.lower()
        if any(k in name_lower for k in ["髪", "hair", "毛", "ke", "kami"]):
            return PHYSICS_TYPE_HAIR
        if any(k in name_lower for k in ["スカート", "skirt", "マント", "cape", "cloth", "服", "fuku"]):
            return PHYSICS_TYPE_CLOTH
        if any(k in name_lower for k in ["胸", "chest", "breast", "oppai"]):
            return PHYSICS_TYPE_SOFT
        if rigid_body.shape_type == 1:
            return PHYSICS_TYPE_HAIR
        if rigid_body.shape_type == 0 and (rigid_body.size[0] * rigid_body.size[1] * rigid_body.size[2]) > 10.0:
            return PHYSICS_TYPE_CLOTH
        return PHYSICS_TYPE_RIGID

    # ------------------------------------------------------------------
    # Nucleus 向けヘルパー (既存)
    # ------------------------------------------------------------------

    def _create_hair_physics(self, rigid_bodies, bone_joints, bone_index_map, rigid_bodies_group):
        for rb in rigid_bodies:
            try:
                bone_name = self._get_bone_name_from_rigid_body(rb, bone_index_map)
                if bone_name not in bone_joints:
                    continue
                joint_name = bone_joints[bone_name]
                curve = self._create_dynamic_curve_for_joint(joint_name, rb)
                if curve:
                    hair_system = self._create_nhair_system(curve, rb)
                    if hair_system:
                        self.created_ncloth_nodes.append(hair_system)
                        maya_scene_utils.parent_objects(hair_system, rigid_bodies_group)
            except Exception as e:
                self.logger.error(f"Hair physics creation error: {rb.name} - {e}")

    def _create_cloth_physics(self, rigid_bodies, bone_joints, bone_index_map, rigid_bodies_group):
        for rb in rigid_bodies:
            try:
                proxy_mesh = self._create_cloth_proxy(rb)
                if proxy_mesh:
                    ncloth = self._create_ncloth(proxy_mesh, rb)
                    if ncloth:
                        self.created_ncloth_nodes.append(ncloth)
                        maya_scene_utils.parent_objects(ncloth, rigid_bodies_group)
            except Exception as e:
                self.logger.error(f"Cloth physics creation error: {rb.name} - {e}")

    def _create_rigid_physics(self, rigid_bodies, bone_joints, bone_index_map, rigid_bodies_group):
        for rb in rigid_bodies:
            try:
                collision_obj = self._create_collision_object(rb)
                if collision_obj:
                    nrigid = self._create_nrigid(collision_obj, rb)
                    if nrigid:
                        self.created_nrigid_nodes.append(nrigid)
                        maya_scene_utils.parent_objects(nrigid, rigid_bodies_group)
            except Exception as e:
                self.logger.error(f"Rigid body physics creation error: {rb.name} - {e}")

    def _create_constraints(self, joints, rigid_bodies, constraints_group):
        self.logger.debug(f"Creating constraints: {len(joints)} joint(s)")

    def _map_physics_parameters(self, mmd_params: Dict) -> Dict:
        ncloth_params = {}
        if "mass" in mmd_params:
            ncloth_params["thickness"] = mmd_params["mass"] * 0.1
        if "velocity_attenuation" in mmd_params:
            ncloth_params["damp"] = mmd_params["velocity_attenuation"]
        if "rotation_attenuation" in mmd_params:
            ncloth_params["bendResistance"] = mmd_params["rotation_attenuation"] * 10.0
        if "friction" in mmd_params:
            ncloth_params["friction"] = mmd_params["friction"]
        if "elasticity" in mmd_params:
            ncloth_params["bounce"] = mmd_params["elasticity"]
        return ncloth_params

    # ------------------------------------------------------------------
    # 座標変換ヘルパー
    # ------------------------------------------------------------------

    @staticmethod
    def _mmd_to_maya_position(position: Tuple[float, float, float], scale: float = 1.0) -> List[float]:
        """MMD (X右, Y上, Z手前) → Maya (X右, Y上, Z後ろ)"""
        return list(mmd_point_to_maya(position, scale))

    @staticmethod
    def _mmd_to_maya_rotation(rotation: Tuple[float, float, float]) -> List[float]:
        """MMD rotation (radian) → Maya rotation (degree), with handedness conversion."""
        return list(_mmd_euler_radians_to_maya_degrees(rotation))

    @staticmethod
    def _mmd_to_maya_transform_matrix(
        position: Tuple[float, float, float],
        rotation: Tuple[float, float, float],
        scale: float = 1.0,
    ) -> List[float]:
        """Build a Maya xform matrix for an MMD transform."""
        mmd_matrix = _euler_xyz_radians_to_matrix(rotation)
        maya_matrix = _mat3_mul(_Z_REFLECTION_MATRIX, _mat3_mul(mmd_matrix, _Z_REFLECTION_MATRIX))
        maya_pos = mmd_point_to_maya(position, scale)
        return [
            maya_matrix[0][0],
            maya_matrix[1][0],
            maya_matrix[2][0],
            0.0,
            maya_matrix[0][1],
            maya_matrix[1][1],
            maya_matrix[2][1],
            0.0,
            maya_matrix[0][2],
            maya_matrix[1][2],
            maya_matrix[2][2],
            0.0,
            maya_pos[0],
            maya_pos[1],
            maya_pos[2],
            1.0,
        ]

    @staticmethod
    def _maya_to_mmd_position(position: List[float]) -> List[float]:
        """Maya (X右, Y上, Z後ろ) → MMD (X右, Y上, Z手前)"""
        return list(maya_point_to_mmd(position))

    @staticmethod
    def _maya_to_mmd_rotation(rotation: List[float]) -> List[float]:
        """Maya rotation (degree) → MMD rotation (radian), with handedness conversion."""
        return list(_maya_euler_degrees_to_mmd_radians(rotation))

    def _scaled_rigid_body_size(self, size: Tuple[float, float, float]) -> List[float]:
        """Scale PMX/PMD rigid body dimensions into Maya scene units."""
        return [float(size[i]) * self.physics_scale for i in range(3)]

    # ------------------------------------------------------------------
    # その他ヘルパー (既存)
    # ------------------------------------------------------------------

    def _create_bone_index_mapping(self, bones, bone_joints: Dict[str, str]) -> Dict[int, str]:
        bone_index_map = {}
        for joint in set(bone_joints.values()):
            try:
                if not cmds.objExists(joint):
                    continue
                if cmds.attributeQuery(ATTR_MMD_BONE_INDEX, node=joint, exists=True):
                    bone_index_map[int(cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_INDEX}"))] = joint
            except Exception as exc:
                self.logger.debug(f"Failed to read MMD bone index from '{joint}': {exc}")

        for i, bone in enumerate(bones):
            if i in bone_index_map:
                continue
            bone_name = maya_name_utils.sanitize_text(bone.get_name())
            if bone_name in bone_joints:
                bone_index_map[i] = bone_joints[bone_name]
        return bone_index_map

    def _create_bone_index_mapping_from_scene(self, root_group: Optional[str] = None) -> Dict[int, str]:
        """Create bone-index mapping from joint metadata in the current Maya scene."""
        joints = cmds.ls(type="joint", long=True) or []
        if root_group and cmds.objExists(root_group):
            descendants = set(cmds.listRelatives(root_group, allDescendents=True, fullPath=True) or [])
            joints = [joint for joint in joints if joint in descendants]

        bone_index_map = {}
        for joint in joints:
            try:
                if cmds.attributeQuery(ATTR_MMD_BONE_INDEX, node=joint, exists=True):
                    bone_index_map[int(cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_INDEX}"))] = joint
            except Exception as exc:
                self.logger.debug(f"Failed to map scene joint '{joint}' by MMD bone index: {exc}")
        return bone_index_map

    def _get_bone_name_from_rigid_body(self, rigid_body, bone_index_map: Optional[Dict[int, str]] = None) -> Optional[str]:
        if hasattr(rigid_body, "bone_index"):
            if bone_index_map and rigid_body.bone_index in bone_index_map:
                return maya_name_utils.sanitize_text(bone_index_map[rigid_body.bone_index])
            return f"bone_{rigid_body.bone_index}"
        elif hasattr(rigid_body, "related_bone_index"):
            if bone_index_map and rigid_body.related_bone_index in bone_index_map:
                return maya_name_utils.sanitize_text(bone_index_map[rigid_body.related_bone_index])
            return f"bone_{rigid_body.related_bone_index}"
        return None

    def _create_dynamic_curve_for_joint(self, joint_name: str, rigid_body) -> Optional[str]:
        try:
            pos = cmds.xform(joint_name, q=True, ws=True, t=True)
            end_pos = [pos[0], pos[1] - 5.0, pos[2]]
            return maya_physics_utils.create_dynamic_curve([pos, end_pos], name=f"{rigid_body.name}_curve")
        except Exception as e:
            self.logger.error(f"Curve creation error: {e}")
            return None

    def _create_nhair_system(self, curve: str, rigid_body) -> Optional[str]:
        try:
            hair_system = maya_physics_utils.apply_nhair_to_curve(curve)
            if hair_system:
                params = self._map_physics_parameters({
                    "mass": rigid_body.mass,
                    "velocity_attenuation": rigid_body.velocity_attenuation,
                    "rotation_attenuation": rigid_body.rotation_attenuation,
                    "friction": rigid_body.friction,
                    "elasticity": rigid_body.elasticity,
                })
                for attr, value in params.items():
                    if cmds.attributeQuery(attr, node=hair_system, exists=True):
                        attr_type = cmds.getAttr(f"{hair_system}.{attr}", type=True)
                        maya_attribute_utils.set_attribute(hair_system, attr, value, attr_type)
                return hair_system
        except Exception as e:
            self.logger.error(f"nHair creation error: {e}")
            return None

    def _create_cloth_proxy(self, rigid_body) -> Optional[str]:
        try:
            proxy = cmds.polyPlane(
                name=f"{rigid_body.name}_proxy",
                width=rigid_body.size[0] * 2, height=rigid_body.size[2] * 2,
                subdivisionsX=5, subdivisionsY=5,
            )[0]
            maya_pos = self._mmd_to_maya_position(rigid_body.position, self.physics_scale)
            cmds.xform(proxy, ws=True, t=maya_pos)
            return proxy
        except Exception as e:
            self.logger.error(f"Cloth proxy creation error: {e}")
            return None

    def _create_ncloth(self, mesh: str, rigid_body) -> Optional[str]:
        try:
            ncloth_shape = maya_physics_utils.apply_ncloth_to_mesh(mesh, self.nucleus_solver)
            if ncloth_shape:
                params = self._map_physics_parameters({
                    "mass": rigid_body.mass,
                    "velocity_attenuation": rigid_body.velocity_attenuation,
                    "rotation_attenuation": rigid_body.rotation_attenuation,
                    "friction": rigid_body.friction,
                    "elasticity": rigid_body.elasticity,
                })
                for attr, value in params.items():
                    if cmds.attributeQuery(attr, node=ncloth_shape, exists=True):
                        attr_type = cmds.getAttr(f"{ncloth_shape}.{attr}", type=True)
                        maya_attribute_utils.set_attribute(ncloth_shape, attr, value, attr_type)
                return ncloth_shape
        except Exception as e:
            self.logger.error(f"nCloth creation error: {e}")
            return None

    def _create_collision_object(self, rigid_body) -> Optional[str]:
        try:
            obj = maya_physics_utils.create_collision_primitive(
                rigid_body.shape_type, rigid_body.size, name=f"{rigid_body.name}_collision",
            )
            maya_pos = self._mmd_to_maya_position(rigid_body.position, self.physics_scale)
            cmds.xform(obj, ws=True, t=maya_pos)
            if hasattr(rigid_body, "rotation"):
                maya_rot = self._mmd_to_maya_rotation(rigid_body.rotation)
                cmds.xform(obj, ws=True, ro=maya_rot)
            return obj
        except Exception as e:
            self.logger.error(f"Collision object creation error: {e}")
            return None

    def _create_nrigid(self, obj: str, rigid_body) -> Optional[str]:
        try:
            is_dynamic = True
            if hasattr(rigid_body, "physics_mode"):
                is_dynamic = rigid_body.physics_mode != 0
            nrigid = maya_physics_utils.apply_nrigid_to_mesh(obj, is_dynamic)
            if nrigid:
                maya_attribute_utils.set_attribute(nrigid, "friction", rigid_body.friction, "double")
                maya_attribute_utils.set_attribute(nrigid, "bounce", rigid_body.elasticity, "double")
                return nrigid
        except Exception as e:
            self.logger.error(f"nRigid creation error: {e}")
            return None
