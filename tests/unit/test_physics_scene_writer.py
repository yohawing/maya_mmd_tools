"""Atomic PhysicsTab scene writer tests with an integration-like fake adapter."""

import unittest

from mmd_tools.core.physics_form_validation import JointFormValues, RigidBodyFormValues
from mmd_tools.core.physics_scene_query import JointSceneRef, RigidBodySceneRef
from mmd_tools.core.physics_scene_writer import MayaPhysicsSceneWriter, PhysicsSceneWriteError


class _FakeAdapter:
    def __init__(self, attrs, ranges=None, fail_set_number=None, undo_enabled=True, unsettable=None):
        self.attrs = dict(attrs)
        self.ranges = dict(ranges or {})
        self.fail_set_number = fail_set_number
        self.undo_enabled = undo_enabled
        self.unsettable = set(unsettable or ())
        self.set_count = 0
        self.events = []
        self._snapshot = None

    def object_exists(self, node):
        return any(existing_node == node for existing_node, _attr in self.attrs)

    def attribute_exists(self, attr, node):
        return (node, attr) in self.attrs

    def attribute_range(self, attr, node):
        return self.ranges.get((node, attr), (None, None))

    def is_attr_settable(self, plug):
        return plug not in self.unsettable

    def undo_info(self, **kwargs):
        if kwargs.get("query") and kwargs.get("state"):
            return self.undo_enabled
        if kwargs.get("openChunk"):
            self.events.append(("open", kwargs.get("chunkName")))
            self._snapshot = dict(self.attrs)
        elif kwargs.get("closeChunk"):
            self.events.append(("close",))

    def set_attr(self, plug, value, **kwargs):
        self.set_count += 1
        self.events.append(("set", plug, value, kwargs))
        if self.fail_set_number == self.set_count:
            raise RuntimeError("simulated setAttr failure")
        node, attr = plug.rsplit(".", 1)
        self.attrs[(node, attr)] = value

    def undo(self):
        self.events.append(("undo",))
        self.attrs = dict(self._snapshot)


def _rigid_ref():
    return RigidBodySceneRef(
        transform="|root|rb",
        bullet_shape="|root|rb|shape",
        index=0,
        name="rb",
        name_english="RB",
        shape_type=0,
        physics_mode=0,
        related_bone_index=0,
    )


def _rigid_values(**changes):
    values = {
        "name": "new rb",
        "name_english": "New RB",
        "shape_type": 2,
        "physics_mode": 2,
        "related_bone_index": 4,
        "collision_group": 7,
        "collision_mask": 0xFF7F,
        "mass": 2.5,
        "linear_damping": 0.15,
        "angular_damping": 0.25,
        "restitution": 0.35,
        "friction": 0.45,
    }
    values.update(changes)
    return RigidBodyFormValues(**values)


def _rigid_attrs():
    transform = "|root|rb"
    shape = "|root|rb|shape"
    return {
        (transform, "mmd_rigid_body_name"): "rb",
        (transform, "mmd_rigid_body_name_english"): "RB",
        (transform, "mmd_physics_mode"): 0,
        (transform, "mmd_related_bone_index"): 0,
        (transform, "mmd_collision_group"): 0,
        (transform, "mmd_collision_mask"): 0xFFFF,
        (shape, "colliderShapeType"): 2,
        (shape, "bodyType"): 1,
        (shape, "mass"): 1.0,
        (shape, "linearDamping"): 0.0,
        (shape, "angularDamping"): 0.0,
        (shape, "restitution"): 0.0,
        (shape, "friction"): 0.0,
    }


def _joint_ref():
    return JointSceneRef(
        transform="|root|joint",
        constraint_shape="|root|joint|shape",
        name="joint",
        name_english="Joint",
        joint_type=0,
        rigid_body_a_index=2,
        rigid_body_b_index=5,
    )


def _joint_values():
    return JointFormValues(
        name="new joint",
        name_english="New Joint",
        joint_type=4,
        rigid_body_a_index=2,
        rigid_body_b_index=5,
        linear_constraint_states=(0, 1, 2),
        angular_constraint_states=(2, 1, 0),
        translation_limit_min=(-1.0, -2.0, -3.0),
        translation_limit_max=(1.0, 2.0, 3.0),
        rotation_limit_min_degrees=(-10.0, -20.0, -30.0),
        rotation_limit_max_degrees=(10.0, 20.0, 30.0),
        spring_translation=(0.1, 0.2, 0.3),
        spring_rotation=(0.4, 0.5, 0.6),
        spring_translation_enabled=(True, False, True),
        spring_rotation_enabled=(False, True, False),
    )


def _joint_attrs():
    transform = "|root|joint"
    shape = "|root|joint|shape"
    attrs = {
        (transform, "mmd_joint_name"): "joint",
        (transform, "mmd_joint_name_english"): "Joint",
        (transform, "mmd_joint_type"): 0,
    }
    for axis in "XYZ":
        for prefix in (
            "linearConstraint",
            "angularConstraint",
            "linearConstraintMin",
            "linearConstraintMax",
            "angularConstraintMin",
            "angularConstraintMax",
            "linearSpringStiffness",
            "angularSpringStiffness",
            "linearSpringEnabled",
            "angularSpringEnabled",
        ):
            attrs[(shape, f"{prefix}{axis}")] = 0
    return attrs


class TestMayaPhysicsSceneWriter(unittest.TestCase):
    def test_rigid_apply_preflights_then_writes_one_chunk(self):
        adapter = _FakeAdapter(
            _rigid_attrs(),
            ranges={("|root|rb|shape", "mass"): (0.0, 10.0)},
        )

        MayaPhysicsSceneWriter(adapter).apply_rigid_body(_rigid_ref(), _rigid_values())

        self.assertEqual(adapter.events[0], ("open", "Apply Physics Values"))
        self.assertEqual(adapter.events[-1], ("close",))
        self.assertEqual(adapter.attrs[("|root|rb|shape", "colliderShapeType")], 2)
        self.assertEqual(adapter.attrs[("|root|rb|shape", "bodyType")], 1)
        self.assertEqual(adapter.attrs[("|root|rb", "mmd_physics_mode")], 0)
        name_write = next(event for event in adapter.events if event[:2] == ("set", "|root|rb.mmd_rigid_body_name"))
        self.assertEqual(name_write[3], {"type": "string"})

    def test_preflight_rejects_node_range_without_opening_chunk(self):
        adapter = _FakeAdapter(
            _rigid_attrs(),
            ranges={("|root|rb|shape", "friction"): (0.0, 1.0)},
        )

        with self.assertRaises(PhysicsSceneWriteError) as caught:
            MayaPhysicsSceneWriter(adapter).apply_rigid_body(
                _rigid_ref(),
                _rigid_values(friction=2.0),
            )

        self.assertEqual(caught.exception.field_key, "friction")
        self.assertEqual(caught.exception.message_key, "physics_validation_maximum")
        self.assertEqual(adapter.events, [])

    def test_mid_write_failure_closes_chunk_and_undoes_partial_changes(self):
        original = _rigid_attrs()
        adapter = _FakeAdapter(original, fail_set_number=5)

        with self.assertRaises(PhysicsSceneWriteError) as caught:
            MayaPhysicsSceneWriter(adapter).apply_rigid_body(_rigid_ref(), _rigid_values())

        self.assertEqual(caught.exception.message_key, "physics_write_failed")
        self.assertEqual(adapter.attrs, original)
        self.assertEqual(adapter.events[-2:], [("close",), ("undo",)])

    def test_missing_attribute_fails_before_write(self):
        attrs = _rigid_attrs()
        del attrs[("|root|rb|shape", "mass")]
        adapter = _FakeAdapter(attrs)

        with self.assertRaises(PhysicsSceneWriteError) as caught:
            MayaPhysicsSceneWriter(adapter).apply_rigid_body(_rigid_ref(), _rigid_values())

        self.assertEqual(caught.exception.message_key, "physics_write_attribute_missing")
        self.assertEqual(adapter.events, [])

    def test_undo_disabled_fails_before_write(self):
        adapter = _FakeAdapter(_rigid_attrs(), undo_enabled=False)

        with self.assertRaises(PhysicsSceneWriteError) as caught:
            MayaPhysicsSceneWriter(adapter).apply_rigid_body(_rigid_ref(), _rigid_values())

        self.assertEqual(caught.exception.message_key, "physics_write_undo_disabled")
        self.assertEqual(adapter.events, [])

    def test_non_settable_attribute_fails_before_write(self):
        adapter = _FakeAdapter(
            _rigid_attrs(),
            unsettable={"|root|rb|shape.mass"},
        )

        with self.assertRaises(PhysicsSceneWriteError) as caught:
            MayaPhysicsSceneWriter(adapter).apply_rigid_body(_rigid_ref(), _rigid_values())

        self.assertEqual(caught.exception.message_key, "physics_write_attribute_not_settable")
        self.assertEqual(caught.exception.field_key, "mass")
        self.assertEqual(adapter.events, [])

    def test_graph_dependent_rigid_metadata_is_not_required_or_written(self):
        attrs = _rigid_attrs()
        for attr in (
            "mmd_physics_mode",
            "mmd_related_bone_index",
            "mmd_collision_group",
            "mmd_collision_mask",
        ):
            del attrs[("|root|rb", attr)]
        adapter = _FakeAdapter(attrs)

        MayaPhysicsSceneWriter(adapter).apply_rigid_body(_rigid_ref(), _rigid_values())

        written = {event[1] for event in adapter.events if event[0] == "set"}
        self.assertNotIn("|root|rb|shape.colliderShapeType", written)
        self.assertNotIn("|root|rb|shape.bodyType", written)
        self.assertNotIn("|root|rb.mmd_related_bone_index", written)

    def test_joint_apply_writes_values_without_reconnecting_bodies(self):
        adapter = _FakeAdapter(_joint_attrs())

        MayaPhysicsSceneWriter(adapter).apply_joint(_joint_ref(), _joint_values())

        written_plugs = [event[1] for event in adapter.events if event[0] == "set"]
        self.assertNotIn("|root|joint|shape.rigidBodyA", written_plugs)
        self.assertNotIn("|root|joint|shape.rigidBodyB", written_plugs)
        self.assertNotIn("|root|joint.mmd_joint_type", written_plugs)
        self.assertEqual(adapter.attrs[("|root|joint|shape", "linearConstraintZ")], 2)
        self.assertEqual(adapter.attrs[("|root|joint|shape", "angularConstraintX")], 2)
        self.assertEqual(adapter.attrs[("|root|joint|shape", "angularSpringEnabledY")], True)


if __name__ == "__main__":
    unittest.main()
