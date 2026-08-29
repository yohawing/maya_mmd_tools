"""End-to-end Physics DAG -> PMX -> fresh-scene Physics DAG round-trip."""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from maya import cmds
import maya.api.OpenMaya as om

from tests.common.maya_test_base import MayaTestBase

from mmd_tools.converters.export_scene_collector import ExportSceneCollector
from mmd_tools.core.constants import CONSTRAINTS_GROUP, PHYSICS_GROUP, RIGID_BODIES_GROUP
from mmd_tools.core.coordinate_transform import mmd_point_to_maya
from tests.common.maya_coordinate_oracle import (
    reflected_mmd_euler_matrix,
    saved_bind_pose_world_matrix,
)
from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.core.pmx_data.morph import PmxMorphType
from mmd_tools.core.physics_form_validation import (
    parse_joint_form,
    parse_rigid_body_form,
)
from mmd_tools.io.mmd_importer import import_mmd_file
from mmd_tools.io.pmx_exporter import PmxExporter
from mmd_tools.ui.presenters.physics_presenter import PhysicsPresenter
from tests.roundtrip.pmx_roundtrip_runner import _build_synthetic_supported_full_dict

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "physics" / "test_hair_physics.pmx"


RIGID_EXACT_FIELDS = ("name", "name_english", "related_bone_index", "group", "collision_mask", "shape_type", "physics_mode")
RIGID_FLOAT_FIELDS = ("mass", "velocity_attenuation", "rotation_attenuation", "elasticity", "friction")
RIGID_VECTOR_FIELDS = ("size", "position", "rotation")
JOINT_EXACT_FIELDS = ("name", "name_english", "joint_type", "rigid_body_a_index", "rigid_body_b_index")
JOINT_VECTOR_FIELDS = ("position", "rotation", "translation_limit_min", "translation_limit_max", "rotation_limit_min", "rotation_limit_max", "spring_translation", "spring_rotation")


def _assert_pmx_fields(test, actual, expected, exact_fields, float_fields, vector_fields, label):
    for field in exact_fields:
        test.assertEqual(getattr(actual, field), _field(expected, field), f"{label}.{field}")
    for field in float_fields:
        test.assertAlmostEqual(
            getattr(actual, field), _field(expected, field), places=5, msg=f"{label}.{field}"
        )
    for field in vector_fields:
        actual_vector = getattr(actual, field)
        expected_vector = _field(expected, field)
        test.assertEqual(len(actual_vector), len(expected_vector), f"{label}.{field}")
        for axis, (actual_value, expected_value) in enumerate(zip(actual_vector, expected_vector)):
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


class _TextControl:
    def __init__(self, value):
        self._value = value

    def text(self):
        return str(self._value)


class _IndexControl:
    def __init__(self, value):
        self._value = int(value)

    def currentIndex(self):
        return self._value


class _ValueControl:
    def __init__(self, value):
        self._value = int(value)

    def value(self):
        return self._value


def _vector_text(values):
    return ", ".join(str(float(value)) for value in values)


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

    def _import_fixture(self, path=FIXTURE_PATH, scale=None):
        return import_mmd_file(
            str(path),
            scale=scale,
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

    @staticmethod
    def _rigid_apply_view(values, parsed, targets):
        binding = tuple(values["related_bone"])
        return SimpleNamespace(
            rigid_name_edit=_TextControl(targets["name"]),
            rigid_name_english_edit=_TextControl(targets["name_english"]),
            rigid_shape_combo=_IndexControl(parsed.shape_type),
            rigid_physics_mode_combo=_IndexControl(parsed.physics_mode),
            rigid_shape_size_edit=_TextControl(_vector_text(targets["shape_size"])),
            rigid_position_edit=_TextControl(_vector_text(targets["position"])),
            rigid_rotation_edit=_TextControl(_vector_text(targets["rotation"])),
            rigid_collision_group_spin=_ValueControl(targets["group"]),
            rigid_collision_mask_spin=_TextControl(targets["collision_mask"]),
            rigid_mass_edit=_TextControl(targets["mass"]),
            rigid_linear_damping_edit=_TextControl(targets["linear_damping"]),
            rigid_angular_damping_edit=_TextControl(targets["angular_damping"]),
            rigid_restitution_edit=_TextControl(targets["restitution"]),
            rigid_friction_edit=_TextControl(targets["friction"]),
            binding_selection=lambda key: (
                binding if key == "rigid_related_bone" else ("", -1)
            ),
        )

    @staticmethod
    def _joint_apply_view(values, parsed, targets):
        bindings = {
            "joint_body_a": tuple(values["rigid_body_a"]),
            "joint_body_b": tuple(values["rigid_body_b"]),
        }
        return SimpleNamespace(
            joint_name_edit=_TextControl(targets["name"]),
            joint_name_english_edit=_TextControl(targets["name_english"]),
            joint_type_combo=_IndexControl(parsed.joint_type),
            joint_position_edit=_TextControl(_vector_text(targets["position"])),
            joint_rotation_edit=_TextControl(_vector_text(targets["rotation"])),
            joint_translation_min_edit=_TextControl(
                _vector_text(targets["translation_limit_min"])
            ),
            joint_translation_max_edit=_TextControl(
                _vector_text(targets["translation_limit_max"])
            ),
            joint_rotation_min_edit=_TextControl(
                _vector_text(targets["rotation_limit_min"])
            ),
            joint_rotation_max_edit=_TextControl(
                _vector_text(targets["rotation_limit_max"])
            ),
            joint_spring_translation_edit=_TextControl(
                _vector_text(targets["spring_translation"])
            ),
            joint_spring_rotation_edit=_TextControl(
                _vector_text(targets["spring_rotation"])
            ),
            binding_selection=lambda key: bindings.get(key, ("", -1)),
        )

    def test_production_apply_edits_drive_solver_and_survive_pmx_roundtrip(self):
        """Connect Physics Apply, solver invalidation, Undo/Redo, and PMX export."""

        root = self._import_fixture()
        presenter = self._presenter(root)
        presenter.app_state.emit_status = lambda _message: None
        presenter._bone_candidates = []
        presenter._rigid_body_candidates = []
        presenter._form_dirty = False
        physics_group = presenter._find_child(root, PHYSICS_GROUP)
        rigid_group = presenter._find_child(physics_group, RIGID_BODIES_GROUP)
        joint_group = presenter._find_child(physics_group, CONSTRAINTS_GROUP)
        rigid_pairs = presenter._find_shapes(rigid_group, "mmdRigidBodyShape")
        joint_pairs = presenter._find_shapes(joint_group, "mmdPhysicsJointShape")
        rigid_shape = next(
            shape
            for _transform, shape in rigid_pairs
            if int(cmds.getAttr(f"{shape}.physicsMode")) != 0
        )
        joint_shape = joint_pairs[0][1]

        world = presenter._find_physics_world_shape()
        self.assertIsNotNone(world)
        solvers = presenter._world_solvers(world)
        self.assertTrue(solvers)
        solver = solvers[0]
        presenter.view = SimpleNamespace()
        presenter._on_physics_enable_changed(True)
        self.assertTrue(cmds.getAttr(f"{world}.enable"))

        cmds.currentUnit(time="film")
        cmds.currentTime(0)
        self.assertTrue(cmds.getAttr(f"{solver}.outSolved"))
        cmds.currentTime(30)
        baseline_matrices = tuple(cmds.getAttr(f"{solver}.outBoneMatrices"))
        self.assertTrue(all(math.isfinite(value) for value in baseline_matrices))
        cmds.currentTime(0)

        rigid_values = presenter._read_rigid_body_values(rigid_shape)
        rigid_parsed = parse_rigid_body_form(
            {
                "name": rigid_values["name"],
                "name_english": rigid_values["name_english"],
                "shape": rigid_values["shape"],
                "physics_mode": rigid_values["physics_mode"],
                "related_bone": rigid_values["related_bone"][1],
                "shape_size": rigid_values["shape_size"],
                "pmx_position": rigid_values["pmx_position"],
                "pmx_rotation_degrees": rigid_values["pmx_rotation_degrees"],
                "collision_group": rigid_values["collision_group"],
                "collision_mask": int(str(rigid_values["collision_mask"]), 0),
                "mass": rigid_values["mass"],
                "linear_damping": rigid_values["linear_damping"],
                "angular_damping": rigid_values["angular_damping"],
                "restitution": rigid_values["restitution"],
                "friction": rigid_values["friction"],
            }
        )
        rigid_targets = {
            "name": rigid_parsed.name + "_edited",
            "name_english": rigid_parsed.name_english + "_edited",
            "shape_size": tuple(value + 0.1 for value in rigid_parsed.shape_size),
            "position": tuple(
                value + delta
                for value, delta in zip(rigid_parsed.pmx_position, (0.2, -0.1, 0.15))
            ),
            "rotation": tuple(value + 2.0 for value in rigid_parsed.pmx_rotation_degrees),
            "group": (rigid_parsed.collision_group + 1) % 16,
            "collision_mask": rigid_parsed.collision_mask ^ 1,
            "mass": rigid_parsed.mass + 0.75,
            "linear_damping": rigid_parsed.linear_damping + 0.05,
            "angular_damping": rigid_parsed.angular_damping + 0.05,
            "restitution": rigid_parsed.restitution + 0.05,
            "friction": rigid_parsed.friction + 0.05,
        }
        rigid_version_before = int(cmds.getAttr(f"{rigid_shape}.outDescriptorVersion"))
        presenter._current_kind = "rigid"
        presenter._current_shape = rigid_shape
        presenter.view = self._rigid_apply_view(rigid_values, rigid_parsed, rigid_targets)
        presenter.apply_changes()
        self.assertAlmostEqual(cmds.getAttr(f"{rigid_shape}.mass"), rigid_targets["mass"])
        self.assertGreater(
            int(cmds.getAttr(f"{rigid_shape}.outDescriptorVersion")),
            rigid_version_before,
        )
        cmds.undo()
        self.assertAlmostEqual(cmds.getAttr(f"{rigid_shape}.mass"), rigid_parsed.mass)
        cmds.redo()
        self.assertAlmostEqual(cmds.getAttr(f"{rigid_shape}.mass"), rigid_targets["mass"])

        joint_values = presenter._read_joint_values(joint_shape)
        joint_parsed = parse_joint_form(
            {
                "name": joint_values["name"],
                "name_english": joint_values["name_english"],
                "joint_type": joint_values["joint_type"],
                "rigid_body_a": joint_values["rigid_body_a"][1],
                "rigid_body_b": joint_values["rigid_body_b"][1],
                "pmx_position": joint_values["pmx_position"],
                "pmx_rotation_degrees": joint_values["pmx_rotation_degrees"],
                "linear_constraint_states": "0, 0, 0",
                "angular_constraint_states": "0, 0, 0",
                "translation_limit_min": joint_values["translation_limit_min"],
                "translation_limit_max": joint_values["translation_limit_max"],
                "rotation_limit_min_degrees": joint_values["rotation_limit_min_degrees"],
                "rotation_limit_max_degrees": joint_values["rotation_limit_max_degrees"],
                "spring_translation": joint_values["spring_translation"],
                "spring_rotation": joint_values["spring_rotation"],
                "spring_translation_enabled": "0, 0, 0",
                "spring_rotation_enabled": "0, 0, 0",
            }
        )
        self.assertEqual(joint_parsed.joint_type, 0)
        joint_targets = {
            "name": joint_parsed.name + "_edited",
            "name_english": joint_parsed.name_english + "_edited",
            "position": tuple(value + 0.1 for value in joint_parsed.pmx_position),
            "rotation": tuple(value + 1.0 for value in joint_parsed.pmx_rotation_degrees),
            "translation_limit_min": tuple(
                value - 0.05 for value in joint_parsed.translation_limit_min
            ),
            "translation_limit_max": tuple(
                value + 0.05 for value in joint_parsed.translation_limit_max
            ),
            "rotation_limit_min": tuple(
                value - 1.0 for value in joint_parsed.rotation_limit_min_degrees
            ),
            "rotation_limit_max": tuple(
                value + 1.0 for value in joint_parsed.rotation_limit_max_degrees
            ),
            "spring_translation": tuple(
                value + 0.25 for value in joint_parsed.spring_translation
            ),
            "spring_rotation": tuple(
                value + 0.5 for value in joint_parsed.spring_rotation
            ),
        }
        joint_version_before = int(cmds.getAttr(f"{joint_shape}.outDescriptorVersion"))
        presenter._current_kind = "joint"
        presenter._current_shape = joint_shape
        presenter.view = self._joint_apply_view(joint_values, joint_parsed, joint_targets)
        presenter.apply_changes()
        self.assertAlmostEqual(
            cmds.getAttr(f"{joint_shape}.springRotationX"),
            joint_targets["spring_rotation"][0],
        )
        self.assertGreater(
            int(cmds.getAttr(f"{joint_shape}.outDescriptorVersion")),
            joint_version_before,
        )
        cmds.undo()
        self.assertAlmostEqual(
            cmds.getAttr(f"{joint_shape}.springRotationX"),
            joint_parsed.spring_rotation[0],
        )
        cmds.redo()
        self.assertAlmostEqual(
            cmds.getAttr(f"{joint_shape}.springRotationX"),
            joint_targets["spring_rotation"][0],
        )

        cmds.currentTime(0)
        self.assertTrue(cmds.getAttr(f"{solver}.outSolved"))
        cmds.currentTime(30)
        edited_matrices = tuple(cmds.getAttr(f"{solver}.outBoneMatrices"))
        self.assertTrue(all(math.isfinite(value) for value in edited_matrices))
        self.assertGreater(
            max(abs(before - after) for before, after in zip(baseline_matrices, edited_matrices)),
            1.0e-5,
            "Applied rigid-body and joint edits must change solver output",
        )

        collected = ExportSceneCollector().collect_from_model_root(root)
        with tempfile.TemporaryDirectory() as temp_dir:
            export_path = Path(temp_dir) / "physics_apply_roundtrip.pmx"
            PmxExporter().export_pmx_model(str(export_path), collected)
            exported = parse_pmx_file(str(export_path), use_native_pmx_parse=False)
            rigid_index = int(cmds.getAttr(f"{rigid_shape}.pmxIndex"))
            joint_index = int(cmds.getAttr(f"{joint_shape}.pmxIndex"))
            self.assertAlmostEqual(exported.rigid_bodies[rigid_index].mass, rigid_targets["mass"])
            self.assertAlmostEqual(
                exported.joints[joint_index].spring_rotation[0],
                joint_targets["spring_rotation"][0],
            )
            cmds.file(new=True, force=True)
            fresh_root = self._import_fixture(export_path)
            fresh = ExportSceneCollector().collect_from_model_root(fresh_root)
        self.assertAlmostEqual(fresh["rigid_bodies"][rigid_index]["mass"], rigid_targets["mass"])
        self.assertAlmostEqual(
            fresh["joints"][joint_index]["spring_rotation"][0],
            joint_targets["spring_rotation"][0],
        )

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

    def test_real_pmx_bound_collider_uses_maya_pose_and_follows_related_bone(self):
        source_pmx = parse_pmx_file(str(FIXTURE_PATH), use_native_pmx_parse=False)
        root = self._import_fixture()
        presenter = self._presenter(root)
        physics_group = presenter._find_child(root, PHYSICS_GROUP)
        rb_group = presenter._find_child(physics_group, RIGID_BODIES_GROUP)
        rigid_pairs = presenter._find_shapes(rb_group, "mmdRigidBodyShape")

        bound = None
        for transform, shape in rigid_pairs:
            index = cmds.getAttr(f"{shape}.pmxIndex")
            source = source_pmx.rigid_bodies[index]
            self.assertListAlmostEqual(cmds.getAttr(f"{shape}.position")[0], source.position)
            self.assertListAlmostEqual(
                cmds.getAttr(f"{shape}.rotation")[0],
                [math.degrees(value) for value in source.rotation],
            )

            expected = om.MTransformationMatrix(reflected_mmd_euler_matrix(source.rotation))
            expected.setTranslation(om.MVector(*mmd_point_to_maya(source.position)), om.MSpace.kTransform)
            bones = cmds.listConnections(
                f"{shape}.relatedBone", source=True, destination=False, type="joint"
            ) or []
            if bones:
                parents = cmds.listRelatives(transform, parent=True, fullPath=True) or []
                parent_world = (
                    om.MMatrix(cmds.xform(parents[0], query=True, worldSpace=True, matrix=True))
                    if parents
                    else om.MMatrix()
                )
                bone_rest = saved_bind_pose_world_matrix(bones[0])
                bone_world = om.MMatrix(
                    cmds.xform(bones[0], query=True, worldSpace=True, matrix=True)
                )
                expected = om.MTransformationMatrix(
                    expected.asMatrix() * parent_world * bone_rest.inverse() * bone_world
                )
            actual = om.MMatrix(cmds.xform(transform, query=True, worldSpace=True, matrix=True))
            expected_matrix = expected.asMatrix()
            for matrix_index in range(16):
                self.assertAlmostEqual(actual[matrix_index], expected_matrix[matrix_index], places=5)

            if bones and bound is None:
                bound = (transform, shape, bones[0], source)

        self.assertIsNotNone(bound, "fixture must contain a collider bound to a related bone")
        transform, shape, bone, source = bound

        def relative_matrix():
            collider_world = om.MMatrix(cmds.xform(transform, query=True, worldSpace=True, matrix=True))
            bone_world = om.MMatrix(cmds.xform(bone, query=True, worldSpace=True, matrix=True))
            return collider_world * bone_world.inverse()

        cmds.currentTime(1)
        cmds.setKeyframe(root, attribute="translate")
        cmds.setKeyframe(root, attribute="rotate")
        cmds.setKeyframe(bone, attribute="translate")
        cmds.setKeyframe(bone, attribute="rotate")
        rest_offset = relative_matrix()

        cmds.currentTime(12)
        cmds.move(1.5, -0.75, 2.25, root, relative=True)
        cmds.rotate(0.0, 17.0, 0.0, root, relative=True)
        cmds.move(-0.5, 1.25, 2.0, bone, relative=True, objectSpace=True)
        cmds.rotate(8.0, -13.0, 21.0, bone, relative=True, objectSpace=True)
        cmds.setKeyframe(root, attribute="translate")
        cmds.setKeyframe(root, attribute="rotate")
        cmds.setKeyframe(bone, attribute="translate")
        cmds.setKeyframe(bone, attribute="rotate")
        animated_offset = relative_matrix()
        for matrix_index in range(16):
            self.assertAlmostEqual(animated_offset[matrix_index], rest_offset[matrix_index], places=5)

        bbox = cmds.exactWorldBoundingBox(transform)
        bbox_center = tuple((bbox[axis] + bbox[axis + 3]) * 0.5 for axis in range(3))
        world_position = cmds.xform(transform, query=True, worldSpace=True, translation=True)
        self.assertListAlmostEqual(bbox_center, world_position, places=4)

        collected = ExportSceneCollector().collect_from_model_root(root)
        collected_body = collected["rigid_bodies"][cmds.getAttr(f"{shape}.pmxIndex")]
        self.assertListAlmostEqual(collected_body["position"], source.position)
        self.assertListAlmostEqual(collected_body["rotation"], source.rotation)

    def test_nondefault_import_scale_applies_only_to_collider_display_dag(self):
        display_scale = 2.5
        source_pmx = parse_pmx_file(str(FIXTURE_PATH), use_native_pmx_parse=False)
        root = self._import_fixture(scale=display_scale)
        presenter = self._presenter(root)
        physics_group = presenter._find_child(root, PHYSICS_GROUP)
        rb_group = presenter._find_child(physics_group, RIGID_BODIES_GROUP)
        rigid_pairs = presenter._find_shapes(rb_group, "mmdRigidBodyShape")

        for transform, shape in rigid_pairs:
            source = source_pmx.rigid_bodies[cmds.getAttr(f"{shape}.pmxIndex")]
            expected_position = mmd_point_to_maya(source.position, display_scale)
            bones = cmds.listConnections(
                f"{shape}.relatedBone", source=True, destination=False, type="joint"
            ) or []
            if bones:
                body_rest = om.MTransformationMatrix(
                    reflected_mmd_euler_matrix(source.rotation)
                )
                body_rest.setTranslation(
                    om.MVector(*expected_position), om.MSpace.kTransform
                )
                parents = cmds.listRelatives(transform, parent=True, fullPath=True) or []
                parent_world = (
                    om.MMatrix(cmds.xform(parents[0], query=True, worldSpace=True, matrix=True))
                    if parents
                    else om.MMatrix()
                )
                bone_rest = saved_bind_pose_world_matrix(bones[0])
                bone_world = om.MMatrix(
                    cmds.xform(bones[0], query=True, worldSpace=True, matrix=True)
                )
                expected_world = (
                    body_rest.asMatrix() * parent_world * bone_rest.inverse() * bone_world
                )
                expected_position = tuple(expected_world[index] for index in (12, 13, 14))
            self.assertListAlmostEqual(
                cmds.xform(transform, query=True, worldSpace=True, translation=True),
                expected_position,
                places=4,
            )
            self.assertListAlmostEqual(
                cmds.getAttr(f"{transform}.scale")[0],
                (display_scale, display_scale, display_scale),
            )
            self.assertListAlmostEqual(cmds.getAttr(f"{shape}.position")[0], source.position)

    def test_import_scale_scales_joint_transform_and_preserves_raw_metadata(self):
        """Joint placement scales in Maya space without rewriting PMX metadata."""
        scale = 0.5
        source_data = _build_synthetic_supported_full_dict("physics_import_scale")
        source_joint = source_data["joints"][0]
        source_joint.update(
            {
                "position": [1.25, -2.5, 3.75],
                "translation_limit_min": [-0.4, -0.6, -0.8],
                "translation_limit_max": [0.5, 0.7, 0.9],
                "spring_translation": [11.0, 22.0, 33.0],
            }
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "physics_import_scale.pmx"
            PmxExporter().export_pmx_model(str(source_path), source_data)
            source_pmx = parse_pmx_file(str(source_path), use_native_pmx_parse=False)
            root = self._import_fixture(source_path, scale=scale)

            presenter = self._presenter(root)
            physics_group = presenter._find_child(root, PHYSICS_GROUP)
            constraints_group = presenter._find_child(physics_group, CONSTRAINTS_GROUP)
            joint_transform, joint_shape = next(
                (transform, shape)
                for transform, shape in presenter._find_shapes(
                    constraints_group, "mmdPhysicsJointShape"
                )
                if cmds.getAttr(f"{shape}.pmxIndex") == 0
            )

            source = source_pmx.joints[0]
            for attr, source_values in (
                ("position", source.position),
                ("translationLimitMin", source.translation_limit_min),
                ("translationLimitMax", source.translation_limit_max),
                ("springTranslation", source.spring_translation),
            ):
                self.assertListAlmostEqual(
                    cmds.getAttr(f"{joint_shape}.{attr}")[0],
                    source_values,
                    places=5,
                    msg=f"raw PMX joint metadata {attr}",
                )
            self.assertListAlmostEqual(
                cmds.xform(joint_transform, query=True, worldSpace=True, translation=True),
                mmd_point_to_maya(source.position, scale),
                places=5,
                msg="joint Maya-space transform translation",
            )

    def test_vertex_morph_and_physics_survive_collector_roundtrip(self):
        """A deleted-target PMX morph and every Physics field survive scene collection."""
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "supported_source.pmx"
            export_path = Path(temp_dir) / "supported_export.pmx"
            PmxExporter().export_pmx_model(
                str(source_path),
                _build_synthetic_supported_full_dict("collector_supported_full"),
            )
            source = parse_pmx_file(str(source_path), use_native_pmx_parse=False)
            root = self._import_fixture(source_path)
            shape = next(
                shape
                for shape in (
                    cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True)
                    or []
                )
                if not cmds.getAttr(f"{shape}.intermediateObject")
            )
            blend_shape = next(
                node
                for node in (cmds.listHistory(shape, pruneDagObjects=True) or [])
                if cmds.nodeType(node) == "blendShape"
            )
            self.assertFalse(
                cmds.ls("*vertex_morph*target*", type="transform"),
                "importer must have deleted its temporary morph target mesh",
            )

            controller = cmds.createNode("mmdMorphController", name="roundtripMorphController")
            cmds.setAttr(f"{controller}.groupTopology", "{}", type="string")
            input_weight = f"{controller}.inputWeight[0]"
            output_weight = f"{controller}.outputWeight[0]"
            weight = f"{blend_shape}.weight[0]"
            cmds.setKeyframe(input_weight, time=1, value=0.2)
            cmds.setKeyframe(input_weight, time=12, value=0.6)
            cmds.connectAttr(output_weight, weight, force=True)
            cmds.setAttr(weight, lock=True)
            cmds.setAttr(f"{blend_shape}.envelope", 0.35)
            cmds.currentTime(12)

            def snapshot():
                return {
                    "weight": cmds.getAttr(weight),
                    "locked": cmds.getAttr(weight, lock=True),
                    "incoming": cmds.listConnections(
                        weight, source=True, destination=False, plugs=True
                    )
                    or [],
                    "controller_keys": cmds.keyframe(
                        input_weight, query=True, valueChange=True
                    )
                    or [],
                    "envelope": cmds.getAttr(f"{blend_shape}.envelope"),
                    "time": cmds.currentTime(query=True),
                    "points": cmds.xform(
                        f"{shape}.vtx[*]", query=True, objectSpace=True, translation=True
                    ),
                }

            before = snapshot()
            collected = ExportSceneCollector().collect_from_model_root(root)
            self.assertEqual(snapshot(), before)
            PmxExporter().export_pmx_model(str(export_path), collected)
            exported = parse_pmx_file(str(export_path), use_native_pmx_parse=False)

            source_vertex = next(
                morph for morph in source.morphs
                if morph.morph_type == PmxMorphType.VertexMorph
            )
            exported_vertex = next(
                morph for morph in exported.morphs
                if morph.morph_type == PmxMorphType.VertexMorph
            )
            self.assertEqual(exported_vertex.name, source_vertex.name)
            self.assertEqual(len(exported_vertex.offsets), len(source_vertex.offsets))
            for actual, expected in zip(exported_vertex.offsets, source_vertex.offsets):
                self.assertEqual(actual["vertex_index"], expected["vertex_index"])
                self.assertListAlmostEqual(
                    actual["position_offset"], expected["position_offset"]
                )

            self.assertEqual(len(exported.rigid_bodies), len(source.rigid_bodies))
            self.assertEqual(len(exported.joints), len(source.joints))
            for index, (actual, expected) in enumerate(
                zip(exported.rigid_bodies, source.rigid_bodies)
            ):
                _assert_pmx_fields(
                    self, actual, expected, RIGID_EXACT_FIELDS, RIGID_FLOAT_FIELDS,
                    RIGID_VECTOR_FIELDS, f"supported.rigid_bodies[{index}]",
                )
            for index, (actual, expected) in enumerate(zip(exported.joints, source.joints)):
                _assert_pmx_fields(
                    self, actual, expected, JOINT_EXACT_FIELDS, (),
                    JOINT_VECTOR_FIELDS, f"supported.joints[{index}]",
                )

            cmds.file(new=True, force=True)
            reimported_root = self._import_fixture(export_path)
            reimported = ExportSceneCollector().collect_from_model_root(reimported_root)
            reimported_vertex = next(
                morph for morph in reimported["morphs"] if morph["type"] == "vertex"
            )
            self.assertEqual(reimported_vertex["name"], source_vertex.name)
            self.assertEqual(
                [offset["vertex_index"] for offset in reimported_vertex["offsets"]],
                [offset["vertex_index"] for offset in source_vertex.offsets],
            )


if __name__ == "__main__":
    unittest.main()
