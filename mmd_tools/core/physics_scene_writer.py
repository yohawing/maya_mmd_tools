"""Atomically apply validated PhysicsTab values through a Maya adapter."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .physics_form_validation import JointFormValues, RigidBodyFormValues
from .physics_scene_query import JointSceneRef, RigidBodySceneRef

class PhysicsSceneWriteError(RuntimeError):
    """Structured preflight, write, or rollback failure."""

    def __init__(self, field_key, message_key, **params):
        super().__init__(f"{field_key}: {message_key}")
        self.field_key = field_key
        self.message_key = message_key
        self.params = params


@dataclass(frozen=True)
class _WriteOp:
    node: str
    attr: str
    value: object
    field_key: str
    string_value: bool = False

    @property
    def plug(self):
        return f"{self.node}.{self.attr}"


class MayaPhysicsSceneWriter:
    """Preflight all plugs/ranges, then update them in one undoable chunk."""

    def __init__(self, maya_adapter):
        self.maya_adapter = maya_adapter

    def apply_rigid_body(self, ref: RigidBodySceneRef, values: RigidBodyFormValues):
        transform = ref.transform
        shape = ref.bullet_shape
        operations = [
            _WriteOp(transform, "mmd_rigid_body_name", values.name, "name", True),
            _WriteOp(transform, "mmd_rigid_body_name_english", values.name_english, "name_english", True),
            _WriteOp(shape, "mass", values.mass, "mass"),
            _WriteOp(shape, "linearDamping", values.linear_damping, "linear_damping"),
            _WriteOp(shape, "angularDamping", values.angular_damping, "angular_damping"),
            _WriteOp(shape, "restitution", values.restitution, "restitution"),
            _WriteOp(shape, "friction", values.friction, "friction"),
        ]
        self._apply_operations(operations)

    def apply_joint(self, ref: JointSceneRef, values: JointFormValues):
        transform = ref.transform
        shape = ref.constraint_shape
        operations = [
            _WriteOp(transform, "mmd_joint_name", values.name, "name", True),
            _WriteOp(transform, "mmd_joint_name_english", values.name_english, "name_english", True),
        ]
        for index, axis in enumerate("XYZ"):
            operations.extend(
                (
                    _WriteOp(
                        shape,
                        f"linearConstraint{axis}",
                        values.linear_constraint_states[index],
                        "linear_constraint_states",
                    ),
                    _WriteOp(
                        shape,
                        f"angularConstraint{axis}",
                        values.angular_constraint_states[index],
                        "angular_constraint_states",
                    ),
                    _WriteOp(
                        shape,
                        f"linearConstraintMin{axis}",
                        values.translation_limit_min[index],
                        "translation_limit_min",
                    ),
                    _WriteOp(
                        shape,
                        f"linearConstraintMax{axis}",
                        values.translation_limit_max[index],
                        "translation_limit_max",
                    ),
                    _WriteOp(
                        shape,
                        f"angularConstraintMin{axis}",
                        values.rotation_limit_min_degrees[index],
                        "rotation_limit_min_degrees",
                    ),
                    _WriteOp(
                        shape,
                        f"angularConstraintMax{axis}",
                        values.rotation_limit_max_degrees[index],
                        "rotation_limit_max_degrees",
                    ),
                    _WriteOp(
                        shape,
                        f"linearSpringStiffness{axis}",
                        values.spring_translation[index],
                        "spring_translation",
                    ),
                    _WriteOp(
                        shape,
                        f"angularSpringStiffness{axis}",
                        values.spring_rotation[index],
                        "spring_rotation",
                    ),
                    _WriteOp(
                        shape,
                        f"linearSpringEnabled{axis}",
                        values.spring_translation_enabled[index],
                        "spring_translation_enabled",
                    ),
                    _WriteOp(
                        shape,
                        f"angularSpringEnabled{axis}",
                        values.spring_rotation_enabled[index],
                        "spring_rotation_enabled",
                    ),
                )
            )
        self._apply_operations(operations)

    def _apply_operations(self, operations):
        self._preflight(operations)
        opened = False
        wrote_any = False
        current = operations[0] if operations else None
        failure = None
        try:
            self.maya_adapter.undo_info(openChunk=True, chunkName="Apply Physics Values")
            opened = True
            for current in operations:
                if current.string_value:
                    self.maya_adapter.set_attr(current.plug, current.value, type="string")
                else:
                    self.maya_adapter.set_attr(current.plug, current.value)
                wrote_any = True
        except Exception as exc:
            failure = exc
        finally:
            if opened:
                try:
                    self.maya_adapter.undo_info(closeChunk=True)
                except Exception as exc:
                    if failure is None:
                        failure = exc

        if failure is None:
            return

        if wrote_any:
            try:
                self.maya_adapter.undo()
            except Exception as rollback_error:
                raise PhysicsSceneWriteError(
                    current.field_key if current else "node",
                    "physics_write_rollback_failed",
                    error=str(failure),
                    rollback_error=str(rollback_error),
                )
        raise PhysicsSceneWriteError(
            current.field_key if current else "node",
            "physics_write_failed",
            error=str(failure),
        )

    def _preflight(self, operations):
        try:
            undo_enabled = bool(self.maya_adapter.undo_info(query=True, state=True))
        except Exception as exc:
            raise PhysicsSceneWriteError(
                "node",
                "physics_write_preflight_failed",
                error=str(exc),
            )
        if not undo_enabled:
            raise PhysicsSceneWriteError("node", "physics_write_undo_disabled")

        for operation in operations:
            try:
                if not self.maya_adapter.object_exists(operation.node):
                    raise PhysicsSceneWriteError(
                        operation.field_key,
                        "physics_write_node_missing",
                        node=operation.node,
                    )
                if not self.maya_adapter.attribute_exists(operation.attr, operation.node):
                    raise PhysicsSceneWriteError(
                        operation.field_key,
                        "physics_write_attribute_missing",
                        plug=operation.plug,
                    )
                if not self.maya_adapter.is_attr_settable(operation.plug):
                    raise PhysicsSceneWriteError(
                        operation.field_key,
                        "physics_write_attribute_not_settable",
                        plug=operation.plug,
                    )
                if operation.string_value or not isinstance(operation.value, (int, float)):
                    continue
                value = float(operation.value)
                if not math.isfinite(value):
                    raise PhysicsSceneWriteError(
                        operation.field_key,
                        "physics_validation_finite",
                    )
                minimum, maximum = self.maya_adapter.attribute_range(operation.attr, operation.node)
                if minimum is not None and value < minimum:
                    raise PhysicsSceneWriteError(
                        operation.field_key,
                        "physics_validation_minimum",
                        minimum=minimum,
                    )
                if maximum is not None and value > maximum:
                    raise PhysicsSceneWriteError(
                        operation.field_key,
                        "physics_validation_maximum",
                        maximum=maximum,
                    )
            except PhysicsSceneWriteError:
                raise
            except Exception as exc:
                raise PhysicsSceneWriteError(
                    operation.field_key,
                    "physics_write_preflight_failed",
                    error=str(exc),
                )
