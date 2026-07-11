"""Query MMD Bullet physics metadata from a Maya scene."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from ..adapters.maya_cmds_adapter import MayaCmdsAdapter

_BULLET_SHAPE_TO_MMD = {1: 1, 2: 0, 3: 2}
_BULLET_MODE_TO_MMD = {0: 0, 1: 0, 2: 1}


@dataclass(frozen=True)
class RigidBodySceneRef:
    """A root-scoped Bullet rigid body and its MMD metadata."""

    transform: str
    bullet_shape: str
    index: int
    name: str
    name_english: str
    shape_type: int
    physics_mode: int
    related_bone_index: int
    locator_shape: Optional[str] = None
    collision_group: int = 0
    collision_mask: int = 0xFFFF
    mass: float = 0.0
    linear_damping: float = 0.0
    angular_damping: float = 0.0
    restitution: float = 0.0
    friction: float = 0.0


@dataclass(frozen=True)
class JointSceneRef:
    """A root-scoped Bullet constraint and its MMD metadata."""

    transform: str
    constraint_shape: str
    name: str
    name_english: str
    joint_type: int
    rigid_body_a_index: int
    rigid_body_b_index: int
    is_pmx: bool = False
    linear_constraint_states: tuple[int, int, int] = (0, 0, 0)
    angular_constraint_states: tuple[int, int, int] = (0, 0, 0)
    translation_limit_min: tuple[float, float, float] = (0.0, 0.0, 0.0)
    translation_limit_max: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation_limit_min_degrees: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation_limit_max_degrees: tuple[float, float, float] = (0.0, 0.0, 0.0)
    spring_translation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    spring_rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    spring_translation_enabled: tuple[bool, bool, bool] = (False, False, False)
    spring_rotation_enabled: tuple[bool, bool, bool] = (False, False, False)
    index: int = -1


@dataclass(frozen=True)
class PhysicsSceneRefs:
    """Root-scoped physics metadata references for UI presentation."""

    rigid_bodies: tuple[RigidBodySceneRef, ...]
    joints: tuple[JointSceneRef, ...]


class MayaPhysicsSceneReader:
    """Collect current Bullet-backed MMD physics nodes under one model root."""

    def __init__(self, maya_adapter=None):
        self.maya_adapter = maya_adapter or MayaCmdsAdapter()

    def collect(self, root: str) -> PhysicsSceneRefs:
        """Return root-scoped rigid bodies and joints sorted for display."""
        nodes = self._root_scoped_transforms(root)
        rigid_bodies = []
        shape_to_original_index = {}

        for node in nodes:
            if not self._attribute_exists(node, "mmd_rigid_body_index"):
                continue
            bullet_shape = self._first_child_shape(node, "bulletRigidBodyShape")
            if not bullet_shape:
                continue
            index = _safe_int(self._get_attr(f"{node}.mmd_rigid_body_index"), -1)
            if index < 0:
                continue

            shape_type = _BULLET_SHAPE_TO_MMD.get(
                _safe_int(self._get_attr(f"{bullet_shape}.colliderShapeType"), 1),
                0,
            )
            if self._attribute_exists(node, "mmd_physics_mode"):
                physics_mode = _safe_int(self._get_attr(f"{node}.mmd_physics_mode"), 0)
            else:
                physics_mode = _BULLET_MODE_TO_MMD.get(
                    _safe_int(self._get_attr(f"{bullet_shape}.bodyType"), 0),
                    0,
                )

            name = _safe_str(self._get_attr(f"{node}.mmd_rigid_body_name"), _short_name(node))
            rigid_body = RigidBodySceneRef(
                transform=node,
                bullet_shape=bullet_shape,
                index=index,
                name=name,
                name_english=_safe_str(self._get_attr(f"{node}.mmd_rigid_body_name_english"), name),
                shape_type=shape_type,
                physics_mode=physics_mode,
                related_bone_index=_safe_int(self._get_attr(f"{node}.mmd_related_bone_index"), -1),
                locator_shape=self._first_child_shape(node, "mmdRigidBodyLocator"),
                collision_group=_safe_int(self._get_attr(f"{node}.mmd_collision_group"), 0),
                collision_mask=_safe_int(self._get_attr(f"{node}.mmd_collision_mask"), 0xFFFF),
                mass=_safe_float(self._get_attr(f"{bullet_shape}.mass")),
                linear_damping=_safe_float(self._get_attr(f"{bullet_shape}.linearDamping")),
                angular_damping=_safe_float(self._get_attr(f"{bullet_shape}.angularDamping")),
                restitution=_safe_float(self._get_attr(f"{bullet_shape}.restitution")),
                friction=_safe_float(self._get_attr(f"{bullet_shape}.friction")),
            )
            rigid_bodies.append(rigid_body)
            shape_to_original_index[bullet_shape] = index

        joints = []
        legacy_joint_index = 0
        for node in nodes:
            if not self._attribute_exists(node, "mmd_joint_name"):
                continue
            constraint_shape = self._first_child_shape(node, "bulletRigidBodyConstraintShape")
            if not constraint_shape:
                continue
            name = _safe_str(self._get_attr(f"{node}.mmd_joint_name"), _short_name(node))
            index = _safe_int(
                self._get_attr(f"{node}.mmd_joint_index"),
                legacy_joint_index,
            )
            joint_type = _safe_int(
                self._get_attr(f"{node}.mmd_joint_type"),
                _safe_int(self._get_attr(f"{constraint_shape}.constraintType"), 0),
            )
            joints.append(
                JointSceneRef(
                    transform=node,
                    constraint_shape=constraint_shape,
                    name=name,
                    name_english=_safe_str(self._get_attr(f"{node}.mmd_joint_name_english"), name),
                    joint_type=joint_type,
                    rigid_body_a_index=self._resolve_joint_body_index(
                        constraint_shape,
                        "rigidBodyA",
                        shape_to_original_index,
                    ),
                    rigid_body_b_index=self._resolve_joint_body_index(
                        constraint_shape,
                        "rigidBodyB",
                        shape_to_original_index,
                    ),
                    is_pmx=_safe_bool(self._get_attr(f"{node}.mmd_joint_is_pmx")),
                    linear_constraint_states=tuple(
                        _safe_int(self._get_attr(f"{constraint_shape}.linearConstraint{axis}"))
                        for axis in "XYZ"
                    ),
                    angular_constraint_states=tuple(
                        _safe_int(self._get_attr(f"{constraint_shape}.angularConstraint{axis}"))
                        for axis in "XYZ"
                    ),
                    translation_limit_min=tuple(
                        _safe_float(self._get_attr(f"{constraint_shape}.linearConstraintMin{axis}"))
                        for axis in "XYZ"
                    ),
                    translation_limit_max=tuple(
                        _safe_float(self._get_attr(f"{constraint_shape}.linearConstraintMax{axis}"))
                        for axis in "XYZ"
                    ),
                    rotation_limit_min_degrees=tuple(
                        _safe_float(self._get_attr(f"{constraint_shape}.angularConstraintMin{axis}"))
                        for axis in "XYZ"
                    ),
                    rotation_limit_max_degrees=tuple(
                        _safe_float(self._get_attr(f"{constraint_shape}.angularConstraintMax{axis}"))
                        for axis in "XYZ"
                    ),
                    spring_translation=tuple(
                        _safe_float(self._get_attr(f"{constraint_shape}.linearSpringStiffness{axis}"))
                        for axis in "XYZ"
                    ),
                    spring_rotation=tuple(
                        _safe_float(self._get_attr(f"{constraint_shape}.angularSpringStiffness{axis}"))
                        for axis in "XYZ"
                    ),
                    spring_translation_enabled=tuple(
                        _safe_bool(self._get_attr(f"{constraint_shape}.linearSpringEnabled{axis}"))
                        for axis in "XYZ"
                    ),
                    spring_rotation_enabled=tuple(
                        _safe_bool(self._get_attr(f"{constraint_shape}.angularSpringEnabled{axis}"))
                        for axis in "XYZ"
                    ),
                    index=index,
                )
            )
            legacy_joint_index += 1

        return PhysicsSceneRefs(
            rigid_bodies=tuple(sorted(rigid_bodies, key=lambda item: (item.index, item.name))),
            joints=tuple(sorted(joints, key=lambda item: (item.index, item.name, item.transform))),
        )

    def _root_scoped_transforms(self, root: str) -> list[str]:
        root_path = self._long_path(root) or root
        descendants = self.maya_adapter.list_relatives(
            root_path,
            allDescendents=True,
            type="transform",
            fullPath=True,
        ) or []
        return [root_path, *descendants]

    def _long_path(self, node: str) -> Optional[str]:
        paths = self.maya_adapter.ls(node, long=True) or []
        return paths[0] if paths else None

    def _first_child_shape(self, transform: str, shape_type: str) -> Optional[str]:
        shapes = self.maya_adapter.list_relatives(
            transform,
            shapes=True,
            type=shape_type,
            fullPath=True,
        ) or []
        return shapes[0] if shapes else None

    def _resolve_joint_body_index(
        self,
        constraint_shape: str,
        attr_name: str,
        shape_to_original_index: dict[str, int],
    ) -> int:
        connections = self.maya_adapter.list_connections(
            f"{constraint_shape}.{attr_name}",
            source=True,
            destination=False,
            plugs=True,
        ) or []
        for connection in connections:
            rb_node = connection.split(".", 1)[0]
            if rb_node in shape_to_original_index:
                return shape_to_original_index[rb_node]
            try:
                if self.maya_adapter.node_type(rb_node) != "bulletRigidBodyShape":
                    continue
            except Exception:
                continue
            parents = self.maya_adapter.list_relatives(rb_node, parent=True, fullPath=True) or []
            if not parents:
                continue
            parent = parents[0]
            if not self._attribute_exists(parent, "mmd_rigid_body_index"):
                continue
            return _safe_int(self._get_attr(f"{parent}.mmd_rigid_body_index"), -1)
        return -1

    def _attribute_exists(self, node: str, attr: str) -> bool:
        try:
            return bool(self.maya_adapter.attribute_exists(attr, node))
        except Exception:
            return False

    def _get_attr(self, attr_path: str):
        try:
            return self.maya_adapter.get_attr(attr_path)
        except Exception:
            return None


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _safe_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true"}:
            return True
        if normalized in {"0", "false"}:
            return False
    return default


def _safe_str(value, default: str) -> str:
    return str(value) if value not in (None, "") else default


def _short_name(node: str) -> str:
    return node.rsplit("|", 1)[-1]
