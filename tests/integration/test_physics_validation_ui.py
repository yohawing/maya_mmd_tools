"""Focused user-visible validation reporting for the Physics presenter."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from maya import cmds

from mmd_tools.core.physics_form_validation import PhysicsFormValidationError
from mmd_tools.core.constants import CONSTRAINTS_GROUP, PHYSICS_GROUP, RIGID_BODIES_GROUP
from mmd_tools.converters.physics_export_collector import collect_physics_from_scene
from mmd_tools.ui.presenters.physics_presenter import PhysicsPresenter, UITranslator
from tests.common.maya_test_base import MayaTestBase


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


if __name__ == "__main__":
    unittest.main()
