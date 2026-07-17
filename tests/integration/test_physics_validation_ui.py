"""Focused user-visible validation reporting for the Physics presenter."""

import unittest
import math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from maya import cmds
import maya.api.OpenMaya as om

from mmd_tools.core.collider_authoring import (
    connect_collider_authoring_follow,
    connect_collider_authoring_transform,
)
from mmd_tools.core.physics_form_validation import PhysicsFormValidationError
from mmd_tools.core.constants import CONSTRAINTS_GROUP, PHYSICS_GROUP, RIGID_BODIES_GROUP
from mmd_tools.converters.physics_export_collector import collect_physics_from_scene
from mmd_tools.ui.presenters.physics_presenter import PhysicsPresenter, UITranslator
from tests.common.maya_test_base import MayaTestBase
from tests.common.maya_coordinate_oracle import reflected_mmd_euler_matrix


class _Translator:
    def translate(self, key, category):
        return {
            ("mass", "fields"): "Mass:",
            ("physics_validation_minimum", "messages"): "must be at least {minimum}",
            ("physics_validation_error", "messages"): "{field}: {reason}",
        }[(key, category)]


class TestPhysicsValidationUI(unittest.TestCase):
    def test_localized_error_is_emitted_to_status_bar_and_script_editor(self):
        status_messages = []
        presenter = object.__new__(PhysicsPresenter)
        presenter.app_state = SimpleNamespace(emit_status=status_messages.append)
        error = PhysicsFormValidationError("mass", "physics_validation_minimum", minimum=0.0)

        with patch.object(UITranslator, "instance", return_value=_Translator()), patch.object(cmds, "warning") as warning:
            message = presenter._report_validation_error(error)

        self.assertEqual(message, "Mass: must be at least 0.0")
        self.assertEqual(status_messages, [message])
        warning.assert_called_once_with(message)


class _FakeButton:
    def __init__(self):
        self.enabled = False

    def setEnabled(self, enabled):
        self.enabled = bool(enabled)


class _FakeList:
    def __init__(self):
        self.items = []

    def clear(self):
        self.items.clear()

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def item(self, index):
        return self.items[index]


class _FakePhysicsView:
    def __init__(self):
        self.rigid_body_list = _FakeList()
        self.joint_list = _FakeList()
        self.create_btn = _FakeButton()
        self.apply_btn = _FakeButton()
        self.reset_btn = _FakeButton()
        self.delete_btn = _FakeButton()
        self.duplicate_btn = _FakeButton()
        self.last_form = None

    def set_physics_details_enabled(self, enabled):
        self.details_enabled = bool(enabled)

    def set_physics_form(self, kind, values):
        self.last_form = (kind, values)


class TestPhysicsPresenterNamespace(MayaTestBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plugin = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
        if not cmds.pluginInfo(str(plugin), query=True, loaded=True):
            cmds.loadPlugin(str(plugin))

    @staticmethod
    def _create_namespaced_model(namespace, rigid_count, joint_count):
        parts = namespace.split(":")
        current = ""
        for part in parts:
            candidate = f"{current}:{part}" if current else part
            if not cmds.namespace(exists=candidate):
                cmds.namespace(add=candidate)
            current = candidate
        root = cmds.createNode("transform", name=f"{namespace}:Base_root")
        physics = cmds.createNode("transform", name=f"{namespace}:{PHYSICS_GROUP}", parent=root)
        rigid_group = cmds.createNode(
            "transform", name=f"{namespace}:{RIGID_BODIES_GROUP}", parent=physics
        )
        joint_group = cmds.createNode(
            "transform", name=f"{namespace}:{CONSTRAINTS_GROUP}", parent=physics
        )
        for index in range(rigid_count):
            transform = cmds.createNode(
                "transform", name=f"{namespace}:rb_{index}", parent=rigid_group
            )
            shape = cmds.createNode(
                "mmdRigidBodyShape", name=f"{namespace}:rb_{index}Shape", parent=transform
            )
            cmds.setAttr(f"{shape}.pmxIndex", index)
            cmds.setAttr(f"{shape}.nameJp", f"剛体{index}", type="string")
        for index in range(joint_count):
            transform = cmds.createNode(
                "transform", name=f"{namespace}:joint_{index}", parent=joint_group
            )
            shape = cmds.createNode(
                "mmdPhysicsJointShape",
                name=f"{namespace}:joint_{index}Shape",
                parent=transform,
            )
            cmds.setAttr(f"{shape}.pmxIndex", index)
            cmds.setAttr(f"{shape}.nameJp", f"ジョイント{index}", type="string")
        return root

    def test_namespaced_refresh_and_model_switch_remain_root_scoped(self):
        root_a = self._create_namespaced_model("Base", rigid_count=1, joint_count=2)
        root_b = self._create_namespaced_model("Nested:Other", rigid_count=3, joint_count=1)
        view = _FakePhysicsView()
        app_state = SimpleNamespace(current_model_root=root_a)
        presenter = object.__new__(PhysicsPresenter)
        presenter.view = view
        presenter.app_state = app_state
        presenter.maya_adapter = SimpleNamespace(object_exists=cmds.objExists)
        presenter._current_kind = None
        presenter._current_shape = None

        presenter.refresh_physics(force=True)
        self.assertEqual(view.rigid_body_list.count(), 1)
        self.assertEqual(view.joint_list.count(), 2)
        rigid_bodies, joints = collect_physics_from_scene(root_a, {})
        self.assertEqual(len(rigid_bodies), 1)
        self.assertEqual(len(joints), 2)

        presenter._on_rigid_body_selected(view.rigid_body_list.item(0), None)
        self.assertEqual(view.last_form[0], "rigid")
        self.assertEqual(view.last_form[1]["name"], "剛体0")
        self.assertTrue(view.apply_btn.enabled)
        self.assertTrue(view.reset_btn.enabled)

        app_state.current_model_root = root_b
        presenter.on_current_model_changed(root_b)
        self.assertEqual(view.rigid_body_list.count(), 3)
        self.assertEqual(view.joint_list.count(), 1)
        presenter.refresh_physics(force=True)
        self.assertEqual(view.rigid_body_list.count(), 3)
        self.assertEqual(view.joint_list.count(), 1)
        rigid_bodies, joints = collect_physics_from_scene(root_b, {})
        self.assertEqual(len(rigid_bodies), 3)
        self.assertEqual(len(joints), 1)

    def test_refresh_migrates_legacy_collider_pose_without_losing_current_bone_offset(self):
        root = self._create_namespaced_model("Legacy", rigid_count=1, joint_count=0)
        bone = cmds.createNode("joint", name="Legacy:hairBone", parent=root)
        physics = cmds.listRelatives(root, children=True, fullPath=True, type="transform")[0]
        rigid_group = next(
            child
            for child in cmds.listRelatives(
                physics, children=True, fullPath=True, type="transform"
            )
            if child.rsplit(":", 1)[-1] == RIGID_BODIES_GROUP
        )
        transform = cmds.listRelatives(
            rigid_group, children=True, fullPath=True, type="transform"
        )[0]
        shape = cmds.listRelatives(transform, shapes=True, fullPath=True)[0]
        position = (1.25, 2.5, 3.75)
        rotation = (0.37, -0.61, 0.83)
        cmds.setAttr(f"{shape}.position", *position, type="double3")
        cmds.setAttr(
            f"{shape}.rotation",
            *(math.degrees(value) for value in rotation),
            type="double3",
        )

        legacy_rest = om.MTransformationMatrix()
        legacy_rest.setTranslation(
            om.MVector(position[0], position[1], -position[2]), om.MSpace.kTransform
        )
        legacy_rest.setRotation(om.MEulerRotation(rotation[0], rotation[1], -rotation[2]))
        legacy_rest_matrix = legacy_rest.asMatrix()
        canonical_rest = om.MTransformationMatrix(reflected_mmd_euler_matrix(rotation))
        canonical_rest.setTranslation(
            om.MVector(position[0], position[1], -position[2]), om.MSpace.kTransform
        )

        cmds.xform(transform, objectSpace=True, matrix=list(legacy_rest_matrix))
        connect_collider_authoring_transform(transform, shape)
        cmds.connectAttr(f"{bone}.message", f"{shape}.relatedBone")
        connect_collider_authoring_follow(transform, shape)
        cmds.move(2.0, -1.0, 0.5, root, relative=True)
        cmds.rotate(4.0, -7.0, 11.0, bone, relative=True, objectSpace=True)

        old_collider_world = om.MMatrix(
            cmds.xform(transform, query=True, worldSpace=True, matrix=True)
        )
        bone_world = om.MMatrix(cmds.xform(bone, query=True, worldSpace=True, matrix=True))
        old_offset = old_collider_world * bone_world.inverse()
        expected_offset = canonical_rest.asMatrix() * legacy_rest_matrix.inverse() * old_offset
        expected_world = expected_offset * bone_world

        view = _FakePhysicsView()
        presenter = object.__new__(PhysicsPresenter)
        presenter.view = view
        presenter.app_state = SimpleNamespace(current_model_root=root)
        presenter.maya_adapter = SimpleNamespace(object_exists=cmds.objExists)
        presenter._current_kind = None
        presenter._current_shape = None
        presenter.refresh_physics(force=True)

        actual_world = om.MMatrix(cmds.xform(transform, query=True, worldSpace=True, matrix=True))
        max_error = max(abs(actual_world[index] - expected_world[index]) for index in range(16))
        self.assertLessEqual(max_error, 1.0e-9)
        self.assertEqual(
            list(cmds.getAttr(f"{shape}.rotation")[0]),
            [math.degrees(value) for value in rotation],
        )
        presenter.refresh_physics(force=True)
        second_world = om.MMatrix(cmds.xform(transform, query=True, worldSpace=True, matrix=True))
        for index in range(16):
            self.assertAlmostEqual(second_world[index], actual_world[index], places=10)


if __name__ == "__main__":
    unittest.main()
