"""Headless Maya integration for PhysicsTab pose/size authoring fields."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from maya import cmds

from mmd_tools.converters.export_scene_collector import ExportSceneCollector
from mmd_tools.io.mmd_importer import import_mmd_file
from mmd_tools.io.pmx_exporter import PmxExporter
from mmd_tools.ui.presenters.physics_presenter import PhysicsPresenter
from tests.common.maya_test_base import MayaTestBase


FIXTURE = Path(__file__).resolve().parents[1] / "data" / "physics" / "test_hair_physics.pmx"


class _Line:
    def __init__(self, value):
        self.value = str(value)

    def text(self):
        return self.value


class _Spin:
    def __init__(self, value):
        self._value = value

    def value(self):
        return self._value


class _Combo:
    def __init__(self, value):
        self._value = value

    def currentIndex(self):
        return self._value


def _import_fixture(path, namespace):
    return import_mmd_file(
        str(path),
        options={
            "import_physics": True,
            "create_mmd_shaders": False,
            "setup_rig": False,
            "use_cpp_fast_load": False,
            "use_native_pmx_parse": False,
            "require_native_pmx_parse": False,
            "use_namespace": True,
            "custom_namespace": namespace,
        },
    )


def _rigid_view(size, position, rotation):
    return SimpleNamespace(
        rigid_name_edit=_Line("UI field rigid"),
        rigid_name_english_edit=_Line("UIFieldRigid"),
        rigid_shape_combo=_Combo(1),
        rigid_physics_mode_combo=_Combo(2),
        rigid_shape_size_edit=_Line(", ".join(map(str, size))),
        rigid_position_edit=_Line(", ".join(map(str, position))),
        rigid_rotation_edit=_Line(", ".join(map(str, rotation))),
        rigid_collision_group_spin=_Spin(7),
        rigid_collision_mask_spin=_Line("0x5A5A"),
        rigid_mass_edit=_Line("2.75"),
        rigid_linear_damping_edit=_Line("0.21"),
        rigid_angular_damping_edit=_Line("0.32"),
        rigid_restitution_edit=_Line("0.43"),
        rigid_friction_edit=_Line("0.54"),
    )


def _joint_view(position, rotation):
    return SimpleNamespace(
        joint_name_edit=_Line("UI field joint"),
        joint_name_english_edit=_Line("UIFieldJoint"),
        joint_type_spin=_Line("0"),
        joint_position_edit=_Line(", ".join(map(str, position))),
        joint_rotation_edit=_Line(", ".join(map(str, rotation))),
        joint_translation_min_edit=_Line("-1.1, -1.2, -1.3"),
        joint_translation_max_edit=_Line("1.1, 1.2, 1.3"),
        joint_rotation_min_edit=_Line("-11, -12, -13"),
        joint_rotation_max_edit=_Line("11, 12, 13"),
        joint_spring_translation_edit=_Line("2.1, 2.2, 2.3"),
        joint_spring_rotation_edit=_Line("3.1, 3.2, 3.3"),
    )


def _presenter(view, kind, shape):
    result = object.__new__(PhysicsPresenter)
    result.view = view
    result.app_state = SimpleNamespace(emit_status=lambda _message: None)
    result._current_kind = kind
    result._current_shape = shape
    return result


def _vector(node, attr):
    return tuple(cmds.getAttr(f"{node}.{attr}{axis}") for axis in "XYZ")


class TestPhysicsUIFields(MayaTestBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plugin = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
        if not cmds.pluginInfo(str(plugin), query=True, loaded=True):
            cmds.loadPlugin(str(plugin))

    @unittest.skipUnless(FIXTURE.exists(), "hair physics fixture not found")
    def test_apply_undo_follow_collector_export_and_fresh_reimport(self):
        root = _import_fixture(FIXTURE, "Nested:UIFields")
        rigid_shape = (cmds.listRelatives(root, allDescendents=True, type="mmdRigidBodyShape") or [None])[0]
        joint_shape = (cmds.listRelatives(root, allDescendents=True, type="mmdPhysicsJointShape") or [None])[0]
        self.assertTrue(rigid_shape)
        self.assertTrue(joint_shape)

        rigid_transform = cmds.listRelatives(rigid_shape, parent=True, fullPath=True)[0]
        original_rigid = {
            "size": _vector(rigid_shape, "shapeSize"),
            "position": _vector(rigid_shape, "position"),
            "rotation": _vector(rigid_shape, "rotation"),
        }
        rigid_size = (0.75, 1.25, 0.5)
        rigid_position = (1.5, 2.25, -3.75)
        rigid_rotation = (13.0, -27.0, 41.0)
        rigid = _presenter(_rigid_view(rigid_size, rigid_position, rigid_rotation), "rigid", rigid_shape)
        read_rigid = rigid._read_rigid_body_values(rigid_shape)
        self.assertIn("shape_size", read_rigid)
        self.assertIn("pmx_position", read_rigid)
        self.assertIn("pmx_rotation_degrees", read_rigid)
        rigid_version = cmds.getAttr(f"{rigid_shape}.outDescriptorVersion")
        rigid.apply_changes()
        self.assertEqual(_vector(rigid_shape, "shapeSize"), rigid_size)
        self.assertEqual(_vector(rigid_shape, "position"), rigid_position)
        for actual, expected in zip(_vector(rigid_shape, "rotation"), rigid_rotation):
            self.assertAlmostEqual(actual, expected, places=5)
        self.assertGreater(cmds.getAttr(f"{rigid_shape}.outDescriptorVersion"), rigid_version)
        self.assertTrue(cmds.isConnected(f"{rigid_transform}.worldMatrix[0]", f"{rigid_shape}.authoringMatrix"))

        related_bone = cmds.listConnections(f"{rigid_shape}.relatedBone", source=True, destination=False) or []
        self.assertTrue(related_bone, "fixture must exercise related-bone follow")
        before_follow = tuple(cmds.xform(rigid_transform, query=True, worldSpace=True, matrix=True))
        cmds.move(0.25, 0.5, -0.75, related_bone[0], relative=True, objectSpace=True)
        after_follow = tuple(cmds.xform(rigid_transform, query=True, worldSpace=True, matrix=True))
        self.assertNotEqual(after_follow, before_follow)
        cmds.undo()

        cmds.undo()
        self.assertEqual(_vector(rigid_shape, "shapeSize"), original_rigid["size"])
        self.assertEqual(_vector(rigid_shape, "position"), original_rigid["position"])
        for actual, expected in zip(_vector(rigid_shape, "rotation"), original_rigid["rotation"]):
            self.assertAlmostEqual(actual, expected, places=5)
        rigid.apply_changes()

        joint_transform = cmds.listRelatives(joint_shape, parent=True, fullPath=True)[0]
        original_joint = {
            "position": _vector(joint_shape, "position"),
            "rotation": _vector(joint_shape, "rotation"),
            "translate": tuple(cmds.getAttr(f"{joint_transform}.translate")[0]),
        }
        joint_position = (-4.5, 5.25, 6.75)
        joint_rotation = (-17.0, 29.0, -53.0)
        joint = _presenter(_joint_view(joint_position, joint_rotation), "joint", joint_shape)
        read_joint = joint._read_joint_values(joint_shape)
        self.assertIn("pmx_position", read_joint)
        self.assertIn("pmx_rotation_degrees", read_joint)
        joint_version = cmds.getAttr(f"{joint_shape}.outDescriptorVersion")
        joint.apply_changes()
        self.assertEqual(_vector(joint_shape, "position"), joint_position)
        self.assertEqual(tuple(cmds.getAttr(f"{joint_transform}.translate")[0]), joint_position)
        for actual, expected in zip(_vector(joint_shape, "rotation"), joint_rotation):
            self.assertAlmostEqual(actual, expected, places=5)
        self.assertGreater(cmds.getAttr(f"{joint_shape}.outDescriptorVersion"), joint_version)
        cmds.undo()
        self.assertEqual(_vector(joint_shape, "position"), original_joint["position"])
        self.assertEqual(tuple(cmds.getAttr(f"{joint_transform}.translate")[0]), original_joint["translate"])
        joint.apply_changes()

        collected = ExportSceneCollector().collect_from_model_root(root)
        expected_rigid = next(item for item in collected["rigid_bodies"] if item["name"] == "UI field rigid")
        expected_joint = next(item for item in collected["joints"] if item["name"] == "UI field joint")
        self.assertEqual(tuple(expected_rigid["size"]), rigid_size)
        self.assertEqual(tuple(expected_rigid["position"]), rigid_position)
        self.assertEqual(tuple(expected_joint["position"]), joint_position)

        with tempfile.TemporaryDirectory() as temp_dir:
            exported_path = Path(temp_dir) / "physics_ui_fields.pmx"
            PmxExporter().export_pmx_model(str(exported_path), collected)
            cmds.file(new=True, force=True)
            reopened_root = _import_fixture(exported_path, "Fresh")
            reopened = ExportSceneCollector().collect_from_model_root(reopened_root)
            reopened_rigid = next(item for item in reopened["rigid_bodies"] if item["name"] == "UI field rigid")
            reopened_joint = next(item for item in reopened["joints"] if item["name"] == "UI field joint")
            for field in ("size", "position", "rotation"):
                for actual, expected in zip(reopened_rigid[field], expected_rigid[field]):
                    self.assertAlmostEqual(actual, expected, places=5, msg=f"rigid.{field}")
            for field in ("position", "rotation"):
                for actual, expected in zip(reopened_joint[field], expected_joint[field]):
                    self.assertAlmostEqual(actual, expected, places=5, msg=f"joint.{field}")


if __name__ == "__main__":
    unittest.main()
