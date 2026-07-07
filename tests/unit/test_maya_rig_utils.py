"""Unit tests for Maya rig utility helpers with fake cmds objects."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from tests.common.maya_stub import install_maya_stub

install_maya_stub(profile="headless")

from mmd_tools.core import maya_rig_utils


class TestMayaRigUtils(unittest.TestCase):
    def setUp(self) -> None:
        self.cmds_patcher = patch.object(maya_rig_utils, "cmds", autospec=False)
        self.cmds = self.cmds_patcher.start()

    def tearDown(self) -> None:
        self.cmds_patcher.stop()

    def test_create_ik_handle_calls_maya_with_explicit_name(self):
        self.cmds.objExists.return_value = True
        self.cmds.ikHandle.return_value = ["ankle_ikHandle", "ankle_effector"]

        ik_handle, effector = maya_rig_utils.create_ik_handle(
            start_joint="knee_jnt",
            end_joint="ankle_jnt",
            solver="ikRPsolver",
            name="ankle_ikHandle",
        )

        self.assertEqual(ik_handle, "ankle_ikHandle")
        self.assertEqual(effector, "ankle_effector")
        self.cmds.ikHandle.assert_called_once_with(
            startJoint="knee_jnt",
            endEffector="ankle_jnt",
            solver="ikRPsolver",
            name="ankle_ikHandle",
        )

    def test_create_ik_handle_uses_default_name(self):
        self.cmds.objExists.return_value = True
        self.cmds.ikHandle.return_value = ["end_jnt_ikHandle", "effector"]

        maya_rig_utils.create_ik_handle("start_jnt", "end_jnt")

        self.cmds.ikHandle.assert_called_once_with(
            startJoint="start_jnt",
            endEffector="end_jnt",
            solver="ikRPsolver",
            name="end_jnt_ikHandle",
        )

    def test_create_ik_handle_rejects_missing_joint(self):
        self.cmds.objExists.side_effect = lambda node: node != "missing_jnt"

        with self.assertRaisesRegex(ValueError, "Start joint 'missing_jnt' does not exist"):
            maya_rig_utils.create_ik_handle("missing_jnt", "end_jnt")

        self.cmds.ikHandle.assert_not_called()

    def test_create_ik_handle_rejects_invalid_solver(self):
        self.cmds.objExists.return_value = True

        with self.assertRaisesRegex(ValueError, "Invalid solver"):
            maya_rig_utils.create_ik_handle("start_jnt", "end_jnt", solver="badSolver")

        self.cmds.ikHandle.assert_not_called()

    def test_set_joint_limits_sets_limit_attrs_and_switches(self):
        set_double3 = MagicMock()

        with patch.object(maya_rig_utils, "_set_double3_attribute", set_double3):
            maya_rig_utils.set_joint_limits(
                joint="knee_jnt",
                limit_min=[-1.0, -0.5, -0.25],
                limit_max=[1.0, 0.5, 0.25],
                enable_limits=False,
            )

        set_double3.assert_any_call("knee_jnt", "minRotLimit", [-1.0, -0.5, -0.25])
        set_double3.assert_any_call("knee_jnt", "maxRotLimit", [1.0, 0.5, 0.25])
        self.cmds.setAttr.assert_any_call("knee_jnt.minRotXLimitEnable", False)
        self.cmds.setAttr.assert_any_call("knee_jnt.minRotYLimitEnable", False)
        self.cmds.setAttr.assert_any_call("knee_jnt.minRotZLimitEnable", False)
        self.cmds.setAttr.assert_any_call("knee_jnt.maxRotXLimitEnable", False)
        self.cmds.setAttr.assert_any_call("knee_jnt.maxRotYLimitEnable", False)
        self.cmds.setAttr.assert_any_call("knee_jnt.maxRotZLimitEnable", False)
        self.assertEqual(self.cmds.setAttr.call_count, 6)

    def test_set_double3_attribute_sets_child_plugs_with_raw_values(self):
        class _FakeSelectionList:
            def __init__(self):
                self.added = []

            def add(self, node):
                self.added.append(node)

            def getDependNode(self, index):
                return f"node_obj_{index}"

        class _FakeChildPlug:
            def __init__(self):
                self.values = []

            def setDouble(self, value):
                self.values.append(value)

        class _FakePlug:
            def __init__(self):
                self.children = [_FakeChildPlug(), _FakeChildPlug(), _FakeChildPlug()]

            def child(self, index):
                return self.children[index]

        class _FakeDependFn:
            def __init__(self, node_obj):
                self.node_obj = node_obj
                self.plug = _FakePlug()
                self.find_calls = []

            def findPlug(self, attr_name, want_networked):
                self.find_calls.append((attr_name, want_networked))
                return self.plug

        selection_list = _FakeSelectionList()
        depend_fns = []

        def _make_depend_fn(node_obj):
            depend_fn = _FakeDependFn(node_obj)
            depend_fns.append(depend_fn)
            return depend_fn

        fake_om = MagicMock()
        fake_om.MSelectionList.return_value = selection_list
        fake_om.MFnDependencyNode.side_effect = _make_depend_fn

        with patch.object(maya_rig_utils, "om", fake_om):
            maya_rig_utils._set_double3_attribute("knee_jnt", "minRotLimit", [-1.0, -0.5, -0.25])

        self.assertEqual(selection_list.added, ["knee_jnt"])
        self.assertEqual(depend_fns[0].node_obj, "node_obj_0")
        self.assertEqual(depend_fns[0].find_calls, [("minRotLimit", False)])
        self.assertEqual([child.values for child in depend_fns[0].plug.children], [[-1.0], [-0.5], [-0.25]])

    def test_create_pole_vector_constraint_returns_first_constraint(self):
        self.cmds.objExists.return_value = True
        self.cmds.poleVectorConstraint.return_value = ["poleVectorConstraint1"]

        constraint = maya_rig_utils.create_pole_vector_constraint(
            "leg_ikHandle",
            "leg_pole_ctrl",
            maintain_offset=False,
        )

        self.assertEqual(constraint, "poleVectorConstraint1")
        self.cmds.poleVectorConstraint.assert_called_once_with(
            "leg_pole_ctrl",
            "leg_ikHandle",
            maintainOffset=False,
        )

    def test_create_pole_vector_constraint_rejects_missing_object(self):
        self.cmds.objExists.side_effect = lambda node: node != "missing_ctrl"

        with self.assertRaisesRegex(ValueError, "Pole vector object 'missing_ctrl' does not exist"):
            maya_rig_utils.create_pole_vector_constraint("leg_ikHandle", "missing_ctrl")

        self.cmds.poleVectorConstraint.assert_not_called()


if __name__ == "__main__":
    unittest.main()
