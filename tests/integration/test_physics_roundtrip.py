"""End-to-end Physics DAG -> PMX -> fresh-scene Physics DAG round-trip."""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from maya import cmds

from tests.common.maya_test_base import MayaTestBase

from mmd_tools.converters.export_scene_collector import ExportSceneCollector
from mmd_tools.core.constants import CONSTRAINTS_GROUP, PHYSICS_GROUP, RIGID_BODIES_GROUP
from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.io.mmd_importer import import_mmd_file
from mmd_tools.io.pmx_exporter import PmxExporter
from mmd_tools.ui.presenters.physics_presenter import PhysicsPresenter

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "physics" / "test_hair_physics.pmx"


RIGID_EXACT_FIELDS = ("name", "name_english", "related_bone_index", "group", "collision_mask", "shape_type", "physics_mode")
RIGID_FLOAT_FIELDS = ("mass", "velocity_attenuation", "rotation_attenuation", "elasticity", "friction")
RIGID_VECTOR_FIELDS = ("size", "position", "rotation")
JOINT_EXACT_FIELDS = ("name", "name_english", "joint_type", "rigid_body_a_index", "rigid_body_b_index")
JOINT_VECTOR_FIELDS = ("position", "rotation", "translation_limit_min", "translation_limit_max", "rotation_limit_min", "rotation_limit_max", "spring_translation", "spring_rotation")


def _assert_pmx_fields(test, actual, expected, exact_fields, float_fields, vector_fields, label):
    for field in exact_fields:
        test.assertEqual(getattr(actual, field), expected[field], f"{label}.{field}")
    for field in float_fields:
        test.assertAlmostEqual(getattr(actual, field), expected[field], places=5, msg=f"{label}.{field}")
    for field in vector_fields:
        actual_vector = getattr(actual, field)
        test.assertEqual(len(actual_vector), len(expected[field]), f"{label}.{field}")
        for axis, (actual_value, expected_value) in enumerate(zip(actual_vector, expected[field])):
            test.assertAlmostEqual(actual_value, expected_value, places=5, msg=f"{label}.{field}[{axis}]")


def _field(item, name):
    return item[name] if isinstance(item, dict) else getattr(item, name)


def _binding_names(model):
    bones = model["bones"] if isinstance(model, dict) else model.bones
    rigid_bodies = model["rigid_bodies"] if isinstance(model, dict) else model.rigid_bodies
    joints = model["joints"] if isinstance(model, dict) else model.joints
    bone_names = [_field(bone, "name") for bone in bones]
    rigid_names = [_field(body, "name") for body in rigid_bodies]
    related_bones = [bone_names[index] if index >= 0 else None for index in (
        _field(body, "related_bone_index") for body in rigid_bodies
    )]
    joint_bodies = [
        tuple(rigid_names[index] if index >= 0 else None for index in
              (_field(joint, "rigid_body_a_index"), _field(joint, "rigid_body_b_index")))
        for joint in joints
    ]
    return related_bones, joint_bodies


@unittest.skipUnless(FIXTURE_PATH.exists(), "hair physics fixture not found")
class TestPhysicsRoundTrip(MayaTestBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plugin_path = str(Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py")
        try:
            cmds.loadPlugin(plugin_path)
        except Exception:
            pass

    def _import_fixture(self, path=FIXTURE_PATH):
        return import_mmd_file(
            str(path),
            options={
                "import_physics": True,
                "create_mmd_shaders": False,
                "setup_rig": False,
                "use_cpp_fast_load": False,
                "use_native_pmx_parse": False,
                "require_native_pmx_parse": False,
            },
        )

    @staticmethod
    def _presenter(root):
        presenter = object.__new__(PhysicsPresenter)
        presenter.app_state = SimpleNamespace(current_model_root=root)
        presenter.refresh_physics = lambda force=False: None
        presenter._current_kind = None
        presenter._current_shape = None
        return presenter

    @staticmethod
    def _shape_by_index(pairs, pmx_index):
        return next(shape for _transform, shape in pairs if cmds.getAttr(f"{shape}.pmxIndex") == pmx_index)

    @staticmethod
    def _set_vector(shape, attr, values):
        for axis, value in zip("XYZ", values):
            cmds.setAttr(f"{shape}.{attr}{axis}", value)

    def test_physics_edits_survive_real_pmx_export_and_fresh_scene_reimport(self):
        source_pmx = parse_pmx_file(str(FIXTURE_PATH), use_native_pmx_parse=False)
        root = self._import_fixture()
        presenter = self._presenter(root)
        collector = ExportSceneCollector()
        initial_collect = collector.collect_from_model_root(root)
        self.assertEqual(len(initial_collect["rigid_bodies"]), len(source_pmx.rigid_bodies))
        self.assertEqual(len(initial_collect["joints"]), len(source_pmx.joints))
        self.assertEqual(_binding_names(initial_collect), _binding_names(source_pmx))
        for index, (source, collected) in enumerate(zip(source_pmx.rigid_bodies, initial_collect["rigid_bodies"])):
            _assert_pmx_fields(self, source, collected, RIGID_EXACT_FIELDS, RIGID_FLOAT_FIELDS,
                               RIGID_VECTOR_FIELDS, f"initial.rigid_bodies[{index}]")
        for index, (source, collected) in enumerate(zip(source_pmx.joints, initial_collect["joints"])):
            _assert_pmx_fields(self, source, collected, JOINT_EXACT_FIELDS, (),
                               JOINT_VECTOR_FIELDS, f"initial.joints[{index}]")

        physics_group = presenter._find_child(root, PHYSICS_GROUP)
        rb_group = presenter._find_child(physics_group, RIGID_BODIES_GROUP)
        jt_group = presenter._find_child(physics_group, CONSTRAINTS_GROUP)
        rigid_pairs = presenter._find_shapes(rb_group, "mmdRigidBodyShape")
        joint_pairs = presenter._find_shapes(jt_group, "mmdPhysicsJointShape")
        original_rigid_max = max(cmds.getAttr(f"{shape}.pmxIndex") for _transform, shape in rigid_pairs)
        original_joint_max = max(cmds.getAttr(f"{shape}.pmxIndex") for _transform, shape in joint_pairs)
        candidate_indices = [
            index
            for joint in source_pmx.joints
            for index in (joint.rigid_body_a_index, joint.rigid_body_b_index)
            if 0 < index < len(source_pmx.rigid_bodies) - 1
        ]
        deleted_rigid_index = candidate_indices[0]
        deleted_rigid_shape = self._shape_by_index(rigid_pairs, deleted_rigid_index)
        deleted_rigid_transform = cmds.listRelatives(deleted_rigid_shape, parent=True, fullPath=True)[0]
        affected_slots = []
        for _transform, joint_shape in joint_pairs:
            for message_attr, fallback_attr in (
                ("rigidBodyA", "rigidBodyAIndex"),
                ("rigidBodyB", "rigidBodyBIndex"),
            ):
                if cmds.isConnected(f"{deleted_rigid_transform}.message", f"{joint_shape}.{message_attr}"):
                    affected_slots.append((joint_shape, message_attr, fallback_attr))
        self.assertTrue(affected_slots, "fixture must contain a joint that references the deleted body")
        presenter._current_kind = "rigid"
        presenter._current_shape = deleted_rigid_shape
        presenter.delete_item()
        for joint_shape, message_attr, fallback_attr in affected_slots:
            self.assertTrue(cmds.objExists(joint_shape), "rigid-body deletion must not cascade-delete joints")
            self.assertEqual(cmds.getAttr(f"{joint_shape}.{fallback_attr}"), -1)
            self.assertFalse(cmds.listConnections(f"{joint_shape}.{message_attr}", source=True, destination=False))
        presenter._create_rigid_body(root)
        rigid_pairs = presenter._find_shapes(rb_group, "mmdRigidBodyShape")
        created_rigid_shape = self._shape_by_index(rigid_pairs, original_rigid_max + 1)
        duplicate_source_shape = rigid_pairs[0][1]
        presenter._duplicate_rigid_body(root, duplicate_source_shape)
        rigid_pairs = presenter._find_shapes(rb_group, "mmdRigidBodyShape")
        duplicated_rigid_shape = self._shape_by_index(rigid_pairs, original_rigid_max + 2)
        self.assertEqual(len({cmds.getAttr(f"{shape}.pmxIndex") for _transform, shape in rigid_pairs}), len(rigid_pairs))
        for attr, value in (
            ("nameJp", "往復剛体"),
            ("nameEn", "roundtrip_rigid"),
        ):
            cmds.setAttr(f"{duplicated_rigid_shape}.{attr}", value, type="string")
        for attr, value in (
            ("collisionGroup", 7),
            ("collisionMask", 0x5A5A),
            ("shapeType", 2),
            ("mass", 3.25),
            ("linearDamping", 0.17),
            ("angularDamping", 0.29),
            ("restitution", 0.41),
            ("friction", 0.53),
            ("physicsMode", 1),
        ):
            cmds.setAttr(f"{duplicated_rigid_shape}.{attr}", value)
        self._set_vector(duplicated_rigid_shape, "shapeSize", (0.75, 1.25, 0.5))
        self._set_vector(duplicated_rigid_shape, "position", (1.5, 2.5, -3.5))
        self._set_vector(duplicated_rigid_shape, "rotation", (11.0, -22.0, 33.0))
        source_bone = cmds.listConnections(f"{duplicate_source_shape}.relatedBone", source=True, destination=False)[0]
        if not cmds.isConnected(f"{source_bone}.message", f"{duplicated_rigid_shape}.relatedBone"):
            cmds.connectAttr(f"{source_bone}.message", f"{duplicated_rigid_shape}.relatedBone", force=True)
        cmds.setAttr(f"{created_rigid_shape}.nameJp", "新規剛体", type="string")
        affected_joint_shapes = {slot[0] for slot in affected_slots}
        joint_pairs = presenter._find_shapes(jt_group, "mmdPhysicsJointShape")
        deleted_joint_shape = next(shape for _transform, shape in joint_pairs[1:-1] if shape not in affected_joint_shapes)
        presenter._current_kind = "joint"
        presenter._current_shape = deleted_joint_shape
        presenter.delete_item()
        presenter._create_joint(root)
        joint_pairs = presenter._find_shapes(jt_group, "mmdPhysicsJointShape")
        created_joint_shape = self._shape_by_index(joint_pairs, original_joint_max + 1)
        duplicate_joint_source = joint_pairs[0][1]
        presenter._duplicate_joint(root, duplicate_joint_source)
        joint_pairs = presenter._find_shapes(jt_group, "mmdPhysicsJointShape")
        duplicated_joint_shape = self._shape_by_index(joint_pairs, original_joint_max + 2)
        self.assertEqual(len({cmds.getAttr(f"{shape}.pmxIndex") for _transform, shape in joint_pairs}), len(joint_pairs))
        for attr, value in (("nameJp", "往復ジョイント"), ("nameEn", "roundtrip_joint")):
            cmds.setAttr(f"{duplicated_joint_shape}.{attr}", value, type="string")
        cmds.setAttr(f"{duplicated_joint_shape}.jointType", 0)
        for attr, values in (
            ("position", (4.0, -5.0, 6.0)),
            ("rotation", (14.0, -25.0, 36.0)),
            ("translationLimitMin", (-0.4, -0.5, -0.6)),
            ("translationLimitMax", (0.7, 0.8, 0.9)),
            ("rotationLimitMin", (-17.0, -28.0, -39.0)),
            ("rotationLimitMax", (41.0, 52.0, 63.0)),
            ("springTranslation", (1.1, 2.2, 3.3)),
            ("springRotation", (4.4, 5.5, 6.6)),
        ):
            self._set_vector(duplicated_joint_shape, attr, values)
        surviving_rigid_transforms = [transform for transform, _shape in rigid_pairs]
        for message_attr, fallback_attr, rigid_transform in (
            ("rigidBodyA", "rigidBodyAIndex", surviving_rigid_transforms[0]),
            ("rigidBodyB", "rigidBodyBIndex", surviving_rigid_transforms[-1]),
        ):
            cmds.connectAttr(f"{rigid_transform}.message", f"{duplicated_joint_shape}.{message_attr}", force=True)
            cmds.setAttr(f"{duplicated_joint_shape}.{fallback_attr}", deleted_rigid_index)
        cmds.setAttr(f"{created_joint_shape}.nameJp", "新規ジョイント", type="string")
        before_export = collector.collect_from_model_root(root)
        rigid_source_indices = [cmds.getAttr(f"{shape}.pmxIndex") for _transform, shape in rigid_pairs]
        joint_source_indices = [cmds.getAttr(f"{shape}.pmxIndex") for _transform, shape in joint_pairs]
        self.assertEqual(rigid_source_indices, sorted(rigid_source_indices))
        self.assertEqual(joint_source_indices, sorted(joint_source_indices))
        expected_rigid_order = [body["name"] for body in before_export["rigid_bodies"]]
        expected_joint_order = [joint["name"] for joint in before_export["joints"]]
        expected_bindings = _binding_names(before_export)
        self.assertEqual(len(before_export["rigid_bodies"]), len(source_pmx.rigid_bodies) + 1)
        self.assertEqual(len(before_export["joints"]), len(source_pmx.joints) + 1)
        self.assertIn(None, [name for pair in expected_bindings[1] for name in pair])
        with tempfile.TemporaryDirectory() as temp_dir:
            export_path = Path(temp_dir) / "physics_roundtrip.pmx"
            PmxExporter().export_pmx_model(str(export_path), before_export)
            exported = parse_pmx_file(str(export_path), use_native_pmx_parse=False)
            self.assertEqual([body.name for body in exported.rigid_bodies], expected_rigid_order)
            self.assertEqual([joint.name for joint in exported.joints], expected_joint_order)
            self.assertEqual(_binding_names(exported), expected_bindings)
            edited_rigid = next(body for body in exported.rigid_bodies if body.name == "往復剛体")
            explicit_rigid = {
                "name": "往復剛体", "name_english": "roundtrip_rigid",
                "related_bone_index": edited_rigid.related_bone_index,
                "group": 7, "collision_mask": 0x5A5A, "shape_type": 2,
                "physics_mode": 1, "mass": 3.25, "velocity_attenuation": 0.17,
                "rotation_attenuation": 0.29, "elasticity": 0.41, "friction": 0.53,
                "size": (0.75, 1.25, 0.5), "position": (1.5, 2.5, -3.5),
                "rotation": tuple(math.radians(value) for value in (11.0, -22.0, 33.0)),
            }
            _assert_pmx_fields(self, edited_rigid, explicit_rigid, RIGID_EXACT_FIELDS, RIGID_FLOAT_FIELDS,
                               RIGID_VECTOR_FIELDS, "edited.rigid_body")
            source_bone_index = source_pmx.rigid_bodies[0].related_bone_index
            self.assertEqual(exported.bones[edited_rigid.related_bone_index].name,
                             source_pmx.bones[source_bone_index].name)

            edited_joint = next(joint for joint in exported.joints if joint.name == "往復ジョイント")
            explicit_joint = {
                "name": "往復ジョイント", "name_english": "roundtrip_joint", "joint_type": 0,
                "rigid_body_a_index": edited_joint.rigid_body_a_index,
                "rigid_body_b_index": edited_joint.rigid_body_b_index,
                "position": (4.0, -5.0, 6.0),
                "rotation": tuple(math.radians(value) for value in (14.0, -25.0, 36.0)),
                "translation_limit_min": (-0.4, -0.5, -0.6),
                "translation_limit_max": (0.7, 0.8, 0.9),
                "rotation_limit_min": tuple(math.radians(value) for value in (-17.0, -28.0, -39.0)),
                "rotation_limit_max": tuple(math.radians(value) for value in (41.0, 52.0, 63.0)),
                "spring_translation": (1.1, 2.2, 3.3), "spring_rotation": (4.4, 5.5, 6.6),
            }
            _assert_pmx_fields(self, edited_joint, explicit_joint, JOINT_EXACT_FIELDS, (),
                               JOINT_VECTOR_FIELDS, "edited.joint")
            self.assertEqual(exported.rigid_bodies[edited_joint.rigid_body_a_index].name,
                             source_pmx.rigid_bodies[0].name)
            self.assertEqual(exported.rigid_bodies[edited_joint.rigid_body_b_index].name, "往復剛体")
            for index, (actual, expected) in enumerate(zip(exported.rigid_bodies, before_export["rigid_bodies"])):
                _assert_pmx_fields(self, actual, expected, RIGID_EXACT_FIELDS, RIGID_FLOAT_FIELDS,
                                   RIGID_VECTOR_FIELDS, f"exported.rigid_bodies[{index}]")
            for index, (actual, expected) in enumerate(zip(exported.joints, before_export["joints"])):
                _assert_pmx_fields(self, actual, expected, JOINT_EXACT_FIELDS, (),
                                   JOINT_VECTOR_FIELDS, f"exported.joints[{index}]")
            cmds.file(new=True, force=True)
            reimported_root = self._import_fixture(export_path)
            after_reimport = collector.collect_from_model_root(reimported_root)

        self.assertEqual([body["name"] for body in after_reimport["rigid_bodies"]], expected_rigid_order)
        self.assertEqual([joint["name"] for joint in after_reimport["joints"]], expected_joint_order)
        self.assertEqual(_binding_names(after_reimport), expected_bindings)
        for index, (actual, expected) in enumerate(zip(exported.rigid_bodies, after_reimport["rigid_bodies"])):
            _assert_pmx_fields(self, actual, expected, RIGID_EXACT_FIELDS, RIGID_FLOAT_FIELDS,
                               RIGID_VECTOR_FIELDS, f"reimported.rigid_bodies[{index}]")
        for index, (actual, expected) in enumerate(zip(exported.joints, after_reimport["joints"])):
            _assert_pmx_fields(self, actual, expected, JOINT_EXACT_FIELDS, (),
                               JOINT_VECTOR_FIELDS, f"reimported.joints[{index}]")


if __name__ == "__main__":
    unittest.main()
