"""Geometry, authoring policy and ownership guards for secondary controls."""

import math
import unittest
from unittest.mock import Mock, patch

from mmd_tools.core import secondary_bone_controls as controls


class SecondaryBoneControlsTests(unittest.TestCase):
    def test_build_without_control_rig_rejects_before_scene_edits(self):
        cmds = Mock()
        cmds.ls.return_value = ["|model"]
        with patch.object(controls, "_read", return_value=None), patch(
            "mmd_tools.core.mmd_control_rig_builder.inspect_mmd_control_rig", return_value=None
        ), patch.object(controls, "_plan") as plan:
            with self.assertRaisesRegex(ValueError, "Set up the Control Rig"):
                controls.build("|model", cmds)
        plan.assert_not_called()
        cmds.createNode.assert_not_called()
        cmds.undoInfo.assert_not_called()

    def test_physics_toggle_updates_existing_world(self):
        cmds = Mock()
        with patch.object(controls, "inspect", return_value={"world": "world"}):
            controls.set_physics_enabled("root", False, cmds)
        cmds.setAttr.assert_called_once_with("world.enable", False)
        cmds.undoInfo.assert_any_call(closeChunk=True)

    def test_physics_toggle_restores_previous_state_when_solver_fails(self):
        cmds = Mock()
        cmds.getAttr.side_effect = lambda plug: plug == "solver.enable"
        with patch.object(controls, "inspect", return_value={"world": "world"}), patch(
            "mmd_tools.core.physics_preroll._world_solvers", return_value=["solver"]
        ):
            with self.assertRaisesRegex(ValueError, "Physics did not solve"):
                controls.set_physics_enabled("root", True, cmds)
        self.assertEqual(cmds.setAttr.call_args_list, [unittest.mock.call("world.enable", True),
                                                     unittest.mock.call("world.enable", False)])
        cmds.undoInfo.assert_any_call(closeChunk=True)

    def test_without_undo_restores_state_even_after_failure(self):
        for enabled in (True, False):
            for fail in (True, False):
                with self.subTest(enabled=enabled, fail=fail):
                    cmds = Mock()
                    cmds.undoInfo.return_value = enabled
                    raised = False
                    try:
                        with controls._without_undo(cmds):
                            cmds.undoInfo.assert_called_with(stateWithoutFlush=False)
                            if fail:
                                raise ValueError("restore failed")
                    except ValueError:
                        raised = True
                    self.assertEqual(raised, fail)
                    cmds.undoInfo.assert_called_with(stateWithoutFlush=enabled)
                    self.assertEqual(cmds.undoInfo.call_count, 3)

    def test_circle_uses_radius_not_capsule_length(self):
        sphere = controls.curve_points(0, (2, 8, 9))
        capsule = controls.curve_points(2, (2, 80, 9))
        self.assertEqual(sphere, capsule)
        for x, y, z in sphere:
            self.assertAlmostEqual(math.hypot(x, z), 2)
            self.assertEqual(y, 0)
        for left, right in zip(sphere[0], sphere[-1]):
            self.assertAlmostEqual(left, right)

    def test_box_traces_every_edge_with_half_extents(self):
        points = controls.curve_points(1, (2, 3, 4))
        edges = set()
        for left, right in zip(points, points[1:]):
            self.assertEqual(sum(a != b for a, b in zip(left, right)), 1)
            edges.add(tuple(sorted((left, right))))
        self.assertEqual(len(edges), 12)
        self.assertEqual({abs(point[0]) for point in points}, {2})
        self.assertEqual({abs(point[1]) for point in points}, {3})
        self.assertEqual({abs(point[2]) for point in points}, {4})

    def test_permissions_follow_bone_flags(self):
        self.assertEqual(controls.allowed_channels(0), ())
        self.assertEqual(controls.allowed_channels(2), ("rotateX", "rotateY", "rotateZ"))
        self.assertEqual(controls.allowed_channels(4), ("translateX", "translateY", "translateZ"))
        self.assertEqual(len(controls.allowed_channels(6)), 6)

    def _route_cmds(self, linked="joint"):
        cmds = Mock()
        cmds.listConnections.side_effect = lambda plug, **kw: ["ctrl.rotateX"] if plug == "driver.inPreRotateX" else [linked]
        cmds.attributeQuery.side_effect = lambda attr, **kw: attr == "mmdSecondaryControl"
        cmds.getAttr.return_value = True
        cmds.ls.side_effect = lambda node, **kw: ["|model|" + (node[0] if isinstance(node, list) else node)]
        return cmds

    def test_import_route_requires_matching_target_bone(self):
        self.assertEqual(controls.secondary_control_input_route("driver", "joint", "rotateX", self._route_cmds()), ("ctrl", "rotateX"))
        self.assertIsNone(controls.secondary_control_input_route("driver", "joint", "rotateX", self._route_cmds("other")))

    def test_import_route_rejects_unmarked_sources(self):
        cmds = self._route_cmds()
        cmds.attributeQuery.side_effect = None
        cmds.attributeQuery.return_value = False
        self.assertIsNone(controls.secondary_control_input_route("driver", "joint", "rotateX", cmds))

    def test_import_route_rejects_cross_channel_connections(self):
        cmds = self._route_cmds()
        cmds.listConnections.return_value = ["ctrl.rotateY"]
        cmds.listConnections.side_effect = None
        self.assertIsNone(controls.secondary_control_input_route("driver", "joint", "rotateX", cmds))

    def test_remove_transfers_animation_layer_inputs_without_deleting_them(self):
        cmds = Mock()
        cmds.isConnected.return_value = True
        cmds.getAttr.return_value = 3.5
        cmds.objExists.return_value = True
        cmds.ls.return_value = ["root_uuid"]
        cmds.listConnections.side_effect = lambda plug, **kw: ["blend.outputX"] if plug == "ctrl.rotateX" else []
        data = {"controls": [{"control": "ctrl", "driver": "driver"}], "nodes": ["ctrl", "proxy"]}
        with patch.object(controls, "_read", return_value=data):
            controls.remove("root", cmds)
        cmds.disconnectAttr.assert_any_call("blend.outputX", "ctrl.rotateX")
        cmds.connectAttr.assert_any_call("blend.outputX", "driver.inPreRotateX")
        cmds.setAttr.assert_any_call("driver.inPreTranslateX", 3.5)
        cmds.delete.assert_called_once_with(["ctrl", "proxy"])

    def test_remove_rejects_changed_connection_before_mutation(self):
        cmds = Mock()
        cmds.isConnected.return_value = False
        data = {"controls": [{"control": "ctrl", "driver": "driver"}], "nodes": ["ctrl"]}
        with patch.object(controls, "_read", return_value=data):
            with self.assertRaises(ValueError):
                controls.remove("root", cmds)
        cmds.disconnectAttr.assert_not_called()
        cmds.delete.assert_not_called()

    def test_mode_refresh_locks_channels_without_adding_undo_entries(self):
        cmds = Mock()
        cmds.ls.return_value = ["|root"]
        cmds.undoInfo.return_value = True
        cmds.getAttr.side_effect = lambda plug, **kw: plug == "world.enable"
        data = {"world": "world", "controls": [{"control": "ctrl", "channels": controls.allowed_channels(6)}]}
        with patch.object(controls, "_read", return_value=data):
            controls.refresh_editability("root", cmds)
        cmds.undoInfo.assert_any_call(stateWithoutFlush=False)
        self.assertEqual(cmds.undoInfo.call_args.kwargs, {"stateWithoutFlush": True})
        for channel in controls.allowed_channels(6):
            cmds.setAttr.assert_any_call(f"ctrl.{channel}", lock=True, keyable=False, channelBox=False)

    def test_layer_membership_moves_without_rebuilding_animation_graph(self):
        cmds = Mock()
        cmds.listConnections.return_value = ["motion.dagSetMembers[2]", "motion.weight"]
        controls._move_layer_memberships(cmds, "proxy.rotateX", "ctrl.rotateX")
        cmds.disconnectAttr.assert_called_once_with("proxy.rotateX", "motion.dagSetMembers[2]")
        cmds.connectAttr.assert_called_once_with("ctrl.rotateX", "motion.dagSetMembers[2]")
        cmds.animLayer.assert_not_called()
        cmds.delete.assert_not_called()

    def test_build_mode_records_lock_changes_for_redo(self):
        cmds = Mock()
        cmds.ls.return_value = ["|root"]
        cmds.getAttr.side_effect = lambda plug, **kw: plug == "world.enable"
        data = {"world": "world", "controls": [{"control": "ctrl", "channels": controls.allowed_channels(6)}]}
        with patch.object(controls, "_read", return_value=data):
            controls.refresh_editability("root", cmds, record_undo=True)
        self.assertTrue(cmds.setAttr.called)
        self.assertFalse(any("stateWithoutFlush" in call.kwargs for call in cmds.undoInfo.call_args_list))

    def test_off_channels_are_keyable_not_only_channel_box_visible(self):
        cmds = Mock()
        cmds.ls.return_value = ["|root"]
        cmds.getAttr.side_effect = lambda plug, **kw: bool(kw.get("lock"))
        data = {"world": "world", "controls": [{"control": "ctrl", "channels": controls.allowed_channels(2)}]}
        with patch.object(controls, "_read", return_value=data):
            controls.refresh_editability("root", cmds)
        for channel in controls.allowed_channels(2):
            cmds.setAttr.assert_any_call(f"ctrl.{channel}", lock=False, keyable=True, channelBox=False)


if __name__ == "__main__":
    unittest.main()
