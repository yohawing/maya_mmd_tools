"""Headless Maya integration for PhysicsTab pose/size authoring fields."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import maya.api.OpenMaya as om
from maya import cmds

from mmd_tools.converters.export_scene_collector import ExportSceneCollector
from mmd_tools.core.coordinate_transform import mmd_point_to_maya
from mmd_tools.core.model_registry import (
    REGISTRY_CATEGORY_PHYSICS,
    list_model_registry_members,
)
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


def _import_fixture(path, namespace, scale=None):
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
        joint_type_combo=_Combo(0),
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
    long_shape = (cmds.ls(shape, long=True) or [shape])[0]
    model_root = "|" + long_shape.strip("|").split("|", 1)[0]
    result.app_state = SimpleNamespace(
        current_model_root=model_root,
        emit_status=lambda _message: None,
    )
    result._current_kind = kind
    result._current_shape = shape
    return result


def _vector(node, attr):
    return tuple(cmds.getAttr(f"{node}.{attr}{axis}") for axis in "XYZ")


def _long(node):
    return (cmds.ls(node, long=True) or [node])[0]


def _source(shape, attr):
    nodes = cmds.listConnections(f"{shape}.{attr}", source=True, destination=False) or []
    return _long(nodes[0]) if nodes else ""


def _model_physics_solver(root):
    """Resolve the selected model's registry-owned solver."""
    members = list_model_registry_members(root, REGISTRY_CATEGORY_PHYSICS)
    if members is None:
        members = cmds.listConnections(
            f"{root}.message", source=False, destination=True, type="mmdPhysicsSolver"
        ) or []
    return next(
        (node for node in members if cmds.objExists(node) and cmds.nodeType(node) == "mmdPhysicsSolver"),
        None,
    )


class _BindingOptionsView:
    def __init__(self):
        self.options = {}

    def set_binding_options(self, key, candidates):
        self.options[key] = list(candidates)


class TestPhysicsUIFields(MayaTestBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plugin = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
        if not cmds.pluginInfo(str(plugin), query=True, loaded=True):
            cmds.loadPlugin(str(plugin))

    @unittest.skipUnless(FIXTURE.exists(), "hair physics fixture not found")
    def test_apply_recompiles_live_solver_world(self):
        root = _import_fixture(FIXTURE, "LiveApply")
        solver = _model_physics_solver(root)
        self.assertTrue(solver)
        world = (cmds.listConnections(
            f"{solver}.inWorldSettings", source=True, destination=False,
            type="mmdPhysicsWorldShape",
        ) or [None])[0]
        self.assertTrue(world)
        cmds.setAttr(f"{world}.enable", True)
        cmds.currentTime(0)
        self.assertTrue(cmds.getAttr(f"{solver}.outSolved"))

        selection = om.MSelectionList()
        selection.add(solver)
        solver_node = om.MFnDependencyNode(selection.getDependNode(0)).userNode()
        original_world = solver_node._world
        self.assertIsNotNone(original_world)

        rigid_shape = (cmds.listRelatives(
            root, allDescendents=True, type="mmdRigidBodyShape", fullPath=True,
        ) or [None])[0]
        self.assertTrue(rigid_shape)
        original_size = _vector(rigid_shape, "shapeSize")
        edited_size = (original_size[0] * 10.0, original_size[1], original_size[2])
        view = _rigid_view(
            edited_size,
            _vector(rigid_shape, "position"),
            _vector(rigid_shape, "rotation"),
        )
        view.rigid_name_edit = _Line(cmds.getAttr(f"{rigid_shape}.nameJp"))
        view.rigid_name_english_edit = _Line(cmds.getAttr(f"{rigid_shape}.nameEn"))
        view.rigid_shape_combo = _Combo(cmds.getAttr(f"{rigid_shape}.shapeType"))
        view.rigid_physics_mode_combo = _Combo(cmds.getAttr(f"{rigid_shape}.physicsMode"))
        view.rigid_collision_group_spin = _Spin(cmds.getAttr(f"{rigid_shape}.collisionGroup"))
        view.rigid_collision_mask_spin = _Line(cmds.getAttr(f"{rigid_shape}.collisionMask"))
        view.rigid_mass_edit = _Line(cmds.getAttr(f"{rigid_shape}.mass"))
        view.rigid_linear_damping_edit = _Line(cmds.getAttr(f"{rigid_shape}.linearDamping"))
        view.rigid_angular_damping_edit = _Line(cmds.getAttr(f"{rigid_shape}.angularDamping"))
        view.rigid_restitution_edit = _Line(cmds.getAttr(f"{rigid_shape}.restitution"))
        view.rigid_friction_edit = _Line(cmds.getAttr(f"{rigid_shape}.friction"))
        presenter = _presenter(view, "rigid", rigid_shape)
        presenter.apply_changes()
        self.assertEqual(_vector(rigid_shape, "shapeSize"), edited_size)

        _ = cmds.getAttr(f"{solver}.outSolved")
        self.assertIsNot(
            solver_node._world,
            original_world,
            "Apply must replace the native world compiled from stale descriptors",
        )

    @unittest.skipUnless(FIXTURE.exists(), "hair physics fixture not found")
    def test_unsupported_joint_type_apply_keeps_live_world(self):
        root = _import_fixture(FIXTURE, "UnsupportedJoint")
        solver = _model_physics_solver(root)
        world = (cmds.listConnections(
            f"{solver}.inWorldSettings", source=True, destination=False,
            type="mmdPhysicsWorldShape",
        ) or [None])[0]
        cmds.setAttr(f"{world}.enable", True)
        cmds.currentTime(0)
        self.assertTrue(cmds.getAttr(f"{solver}.outSolved"))

        selection = om.MSelectionList()
        selection.add(solver)
        solver_node = om.MFnDependencyNode(selection.getDependNode(0)).userNode()
        original_world = solver_node._world
        joint_shape = (cmds.listRelatives(
            root, allDescendents=True, type="mmdPhysicsJointShape", fullPath=True,
        ) or [None])[0]
        original_type = cmds.getAttr(f"{joint_shape}.jointType")
        view = _joint_view(
            _vector(joint_shape, "position"),
            _vector(joint_shape, "rotation"),
        )
        view.joint_type_combo = _Combo(2)
        presenter = _presenter(view, "joint", joint_shape)
        presenter.apply_changes()

        self.assertEqual(cmds.getAttr(f"{joint_shape}.jointType"), original_type)
        self.assertIs(solver_node._world, original_world)
        self.assertTrue(cmds.getAttr(f"{solver}.outSolved"))

    @unittest.skipUnless(FIXTURE.exists(), "hair physics fixture not found")
    def test_apply_joint_uses_effective_position_for_display_translation(self):
        root = _import_fixture(FIXTURE, "JointScale", scale=0.5)
        joint_shape = (cmds.listRelatives(
            root, allDescendents=True, type="mmdPhysicsJointShape", fullPath=True,
        ) or [None])[0]
        self.assertTrue(joint_shape)
        joint_transform = cmds.listRelatives(joint_shape, parent=True, fullPath=True)[0]
        position = _vector(joint_shape, "position")
        view = _joint_view(position, _vector(joint_shape, "rotation"))
        presenter = _presenter(view, "joint", joint_shape)

        presenter.apply_changes()

        self.assertEqual(_vector(joint_shape, "position"), position)
        expected_translation = mmd_point_to_maya(position)
        self.assertEqual(
            tuple(cmds.getAttr(f"{joint_transform}.translate")[0]),
            expected_translation,
        )

    @unittest.skipUnless(FIXTURE.exists(), "hair physics fixture not found")
    def test_create_and_duplicate_physics_items_preserve_effective_values(self):
        root = _import_fixture(FIXTURE, "AuthoringScale", scale=1.5)
        presenter = object.__new__(PhysicsPresenter)
        presenter.app_state = SimpleNamespace(current_model_root=root)

        presenter._create_rigid_body(root)
        rigid_shapes = cmds.listRelatives(
            root, allDescendents=True, type="mmdRigidBodyShape", fullPath=True
        ) or []
        created_rigid = max(rigid_shapes, key=lambda node: cmds.getAttr(f"{node}.pmxIndex"))
        created_transform = cmds.listRelatives(
            created_rigid, parent=True, fullPath=True
        )[0]
        self.assertEqual(_vector(created_rigid, "shapeSize"), (0.5, 0.5, 0.5))
        self.assertEqual(tuple(cmds.getAttr(f"{created_transform}.scale")[0]), (1.0,) * 3)

        source_rigid = min(rigid_shapes, key=lambda node: cmds.getAttr(f"{node}.pmxIndex"))
        presenter._duplicate_rigid_body(root, source_rigid)
        rigid_shapes = cmds.listRelatives(
            root, allDescendents=True, type="mmdRigidBodyShape", fullPath=True
        ) or []
        duplicated_rigid = max(
            rigid_shapes, key=lambda node: cmds.getAttr(f"{node}.pmxIndex")
        )
        duplicated_transform = cmds.listRelatives(
            duplicated_rigid, parent=True, fullPath=True
        )[0]
        source_position = _vector(source_rigid, "position")
        self.assertEqual(_vector(duplicated_rigid, "position"), source_position)
        self.assertEqual(
            tuple(cmds.getAttr(f"{duplicated_transform}.scale")[0]), (1.0,) * 3
        )
        self.assertEqual(
            tuple(cmds.getAttr(f"{duplicated_transform}.translate")[0]),
            mmd_point_to_maya(source_position),
        )

        joint_shapes = cmds.listRelatives(
            root, allDescendents=True, type="mmdPhysicsJointShape", fullPath=True
        ) or []
        source_joint = min(joint_shapes, key=lambda node: cmds.getAttr(f"{node}.pmxIndex"))
        presenter._duplicate_joint(root, source_joint)
        joint_shapes = cmds.listRelatives(
            root, allDescendents=True, type="mmdPhysicsJointShape", fullPath=True
        ) or []
        duplicated_joint = max(
            joint_shapes, key=lambda node: cmds.getAttr(f"{node}.pmxIndex")
        )
        duplicated_joint_transform = cmds.listRelatives(
            duplicated_joint, parent=True, fullPath=True
        )[0]
        source_joint_position = _vector(source_joint, "position")
        self.assertEqual(_vector(duplicated_joint, "position"), source_joint_position)
        self.assertEqual(
            tuple(cmds.getAttr(f"{duplicated_joint_transform}.translate")[0]),
            mmd_point_to_maya(source_joint_position),
        )

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
        self.assertEqual(
            tuple(cmds.getAttr(f"{joint_transform}.translate")[0]),
            mmd_point_to_maya(joint_position),
        )
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

    @unittest.skipUnless(FIXTURE.exists(), "hair physics fixture not found")
    def test_root_scoped_binding_apply_undo_follow_refresh_and_roundtrip(self):
        root_a = _import_fixture(FIXTURE, "Nested:BindingsA")
        root_b = _import_fixture(FIXTURE, "Nested:BindingsB")
        rigid_shape = _long(
            (cmds.listRelatives(root_a, allDescendents=True, type="mmdRigidBodyShape") or [None])[0]
        )
        joint_shape = _long(
            (cmds.listRelatives(root_a, allDescendents=True, type="mmdPhysicsJointShape") or [None])[0]
        )

        options_view = _BindingOptionsView()
        candidate_presenter = object.__new__(PhysicsPresenter)
        candidate_presenter.view = options_view
        candidate_presenter.app_state = SimpleNamespace(current_model_root=root_a)
        physics_group = candidate_presenter._find_child(root_a, "Physics")
        rigid_group = candidate_presenter._find_child(physics_group, "RigidBodies")
        candidate_presenter._refresh_binding_candidates(root_a, rigid_group)
        bones_a = options_view.options["rigid_related_bone"]
        rigids_a = options_view.options["joint_body_a"]
        self.assertGreater(len(bones_a), 1)
        self.assertGreater(len(rigids_a), 1)
        self.assertEqual([item[2] for item in bones_a], sorted(item[2] for item in bones_a))
        self.assertEqual([item[2] for item in rigids_a], sorted(item[2] for item in rigids_a))
        self.assertTrue(all(item[1].startswith(_long(root_a) + "|") for item in bones_a + rigids_a))
        self.assertTrue(all("BindingsB" not in item[1] for item in bones_a + rigids_a))
        self.assertTrue(all(item[0].startswith(f"{item[2]}: ") for item in bones_a + rigids_a))

        original_bone = _source(rigid_shape, "relatedBone")
        original_bone_index = cmds.getAttr(f"{rigid_shape}.relatedBoneIndex")
        new_bone = next(item for item in bones_a if item[1] != original_bone)
        rigid_position = _vector(rigid_shape, "position")
        rigid_rotation = _vector(rigid_shape, "rotation")
        rigid_view = _rigid_view(
            _vector(rigid_shape, "shapeSize"), rigid_position, rigid_rotation
        )
        rigid_view.binding_selection = lambda key: (new_bone[1], new_bone[2])
        rigid = _presenter(rigid_view, "rigid", rigid_shape)
        rigid._bone_candidates = bones_a
        rigid._rigid_body_candidates = rigids_a
        rigid_version = cmds.getAttr(f"{rigid_shape}.outDescriptorVersion")
        rigid.apply_changes()
        self.assertEqual(_source(rigid_shape, "relatedBone"), new_bone[1])
        self.assertEqual(cmds.getAttr(f"{rigid_shape}.relatedBoneIndex"), new_bone[2])
        self.assertEqual(_vector(rigid_shape, "position"), rigid_position)
        self.assertEqual(_vector(rigid_shape, "rotation"), rigid_rotation)
        self.assertGreater(cmds.getAttr(f"{rigid_shape}.outDescriptorVersion"), rigid_version)

        rigid_transform = _long(cmds.listRelatives(rigid_shape, parent=True, fullPath=True)[0])
        new_tx = cmds.getAttr(f"{new_bone[1]}.translateX")
        old_ty = cmds.getAttr(f"{original_bone}.translateY")
        cmds.undoInfo(openChunk=True, chunkName="Binding Follow Frame Oracle")
        try:
            for frame, value in ((0, new_tx), (5, new_tx + 0.5)):
                cmds.setKeyframe(new_bone[1], attribute="translateX", time=frame, value=value)
            for frame in (0, 5):
                cmds.setKeyframe(original_bone, attribute="translateY", time=frame, value=old_ty)
            cmds.currentTime(0)
            new_follow_frame_0 = tuple(
                cmds.xform(rigid_transform, query=True, worldSpace=True, matrix=True)
            )
            cmds.currentTime(5)
            new_follow_frame_5 = tuple(
                cmds.xform(rigid_transform, query=True, worldSpace=True, matrix=True)
            )
            self.assertGreater(
                max(abs(left - right) for left, right in zip(new_follow_frame_0, new_follow_frame_5)),
                1.0e-6,
            )

            cmds.setKeyframe(new_bone[1], attribute="translateX", time=5, value=new_tx)
            cmds.setKeyframe(original_bone, attribute="translateY", time=5, value=old_ty + 0.5)
            cmds.currentTime(0)
            old_ignored_frame_0 = tuple(
                cmds.xform(rigid_transform, query=True, worldSpace=True, matrix=True)
            )
            cmds.currentTime(5)
            old_ignored_frame_5 = tuple(
                cmds.xform(rigid_transform, query=True, worldSpace=True, matrix=True)
            )
            self.assertLessEqual(
                max(abs(left - right) for left, right in zip(old_ignored_frame_0, old_ignored_frame_5)),
                1.0e-9,
            )
        finally:
            cmds.currentTime(0)
            cmds.undoInfo(closeChunk=True)
        cmds.undo()

        cmds.undo()
        self.assertEqual(_source(rigid_shape, "relatedBone"), original_bone)
        self.assertEqual(cmds.getAttr(f"{rigid_shape}.relatedBoneIndex"), original_bone_index)
        rigid.apply_changes()

        original_a = (_source(joint_shape, "rigidBodyA"), cmds.getAttr(f"{joint_shape}.rigidBodyAIndex"))
        original_b = (_source(joint_shape, "rigidBodyB"), cmds.getAttr(f"{joint_shape}.rigidBodyBIndex"))
        new_a = next(item for item in rigids_a if item[1] != original_a[0])
        new_b = next(item for item in reversed(rigids_a) if item[1] not in {original_b[0], new_a[1]})
        joint_view = _joint_view(_vector(joint_shape, "position"), _vector(joint_shape, "rotation"))
        joint_view.binding_selection = lambda key: (
            (new_a[1], new_a[2]) if key == "joint_body_a" else (new_b[1], new_b[2])
        )
        joint = _presenter(joint_view, "joint", joint_shape)
        joint._bone_candidates = bones_a
        joint._rigid_body_candidates = rigids_a
        joint_version = cmds.getAttr(f"{joint_shape}.outDescriptorVersion")
        joint.apply_changes()
        self.assertEqual((_source(joint_shape, "rigidBodyA"), cmds.getAttr(f"{joint_shape}.rigidBodyAIndex")), (new_a[1], new_a[2]))
        self.assertEqual((_source(joint_shape, "rigidBodyB"), cmds.getAttr(f"{joint_shape}.rigidBodyBIndex")), (new_b[1], new_b[2]))
        self.assertGreater(cmds.getAttr(f"{joint_shape}.outDescriptorVersion"), joint_version)
        cmds.undo()
        self.assertEqual((_source(joint_shape, "rigidBodyA"), cmds.getAttr(f"{joint_shape}.rigidBodyAIndex")), original_a)
        self.assertEqual((_source(joint_shape, "rigidBodyB"), cmds.getAttr(f"{joint_shape}.rigidBodyBIndex")), original_b)
        joint.apply_changes()

        before_refresh = (
            _source(rigid_shape, "relatedBone"),
            _source(joint_shape, "rigidBodyA"),
            _source(joint_shape, "rigidBodyB"),
        )
        candidate_presenter._refresh_binding_candidates(root_a, rigid_group)
        self.assertEqual(before_refresh, (
            _source(rigid_shape, "relatedBone"),
            _source(joint_shape, "rigidBodyA"),
            _source(joint_shape, "rigidBodyB"),
        ))

        candidate_presenter.app_state.current_model_root = root_b
        physics_b = candidate_presenter._find_child(root_b, "Physics")
        rigid_group_b = candidate_presenter._find_child(physics_b, "RigidBodies")
        candidate_presenter._refresh_binding_candidates(root_b, rigid_group_b)
        self.assertTrue(all("BindingsB" in item[1] for item in options_view.options["rigid_related_bone"]))
        stale_bone = options_view.options["rigid_related_bone"][0]
        statuses = []
        rigid.app_state.emit_status = statuses.append
        rigid_view.binding_selection = lambda key: (stale_bone[1], stale_bone[2])
        rigid.apply_changes()
        self.assertTrue(statuses)
        self.assertEqual(_source(rigid_shape, "relatedBone"), new_bone[1])

        rigid.app_state.current_model_root = root_b
        rigid_view.binding_selection = lambda key: ("", -1)
        rigid.apply_changes()
        self.assertEqual(_source(rigid_shape, "relatedBone"), new_bone[1])
        rigid.app_state.current_model_root = root_a

        rigid_view.binding_selection = lambda key: ("", -1)
        rigid.apply_changes()
        self.assertEqual(_source(rigid_shape, "relatedBone"), "")
        self.assertEqual(cmds.getAttr(f"{rigid_shape}.relatedBoneIndex"), -1)
        cmds.undo()
        self.assertEqual(_source(rigid_shape, "relatedBone"), new_bone[1])

        joint_view.binding_selection = lambda key: ("", -1)
        joint.apply_changes()
        self.assertEqual(_source(joint_shape, "rigidBodyA"), "")
        self.assertEqual(_source(joint_shape, "rigidBodyB"), "")
        self.assertEqual(cmds.getAttr(f"{joint_shape}.rigidBodyAIndex"), -1)
        self.assertEqual(cmds.getAttr(f"{joint_shape}.rigidBodyBIndex"), -1)
        cmds.undo()
        self.assertEqual(_source(joint_shape, "rigidBodyA"), new_a[1])
        self.assertEqual(_source(joint_shape, "rigidBodyB"), new_b[1])

        collected = ExportSceneCollector().collect_from_model_root(root_a)
        collected_rigid = next(item for item in collected["rigid_bodies"] if item["name"] == "UI field rigid")
        collected_joint = next(item for item in collected["joints"] if item["name"] == "UI field joint")
        self.assertEqual(collected_rigid["related_bone_index"], new_bone[2])
        self.assertEqual(collected_joint["rigid_body_a_index"], new_a[2])
        self.assertEqual(collected_joint["rigid_body_b_index"], new_b[2])

        with tempfile.TemporaryDirectory() as temp_dir:
            exported_path = Path(temp_dir) / "physics_ui_bindings.pmx"
            PmxExporter().export_pmx_model(str(exported_path), collected)
            cmds.file(new=True, force=True)
            reopened_root = _import_fixture(exported_path, "Fresh:Bindings")
            reopened = ExportSceneCollector().collect_from_model_root(reopened_root)
            reopened_rigid = next(item for item in reopened["rigid_bodies"] if item["name"] == "UI field rigid")
            reopened_joint = next(item for item in reopened["joints"] if item["name"] == "UI field joint")
            self.assertEqual(reopened_rigid["related_bone_index"], new_bone[2])
            self.assertEqual(reopened_joint["rigid_body_a_index"], new_a[2])
            self.assertEqual(reopened_joint["rigid_body_b_index"], new_b[2])


if __name__ == "__main__":
    unittest.main()
