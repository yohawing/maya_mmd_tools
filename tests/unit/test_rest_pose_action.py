"""Rest Pose session topology and rollback tests without Maya runtime."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from mmd_tools.actions.rest_pose_action import RestPoseManager, RestPoseResult


class _FakeCmds:
    def __init__(self):
        self.models = {
            "modelA": ["|modelA|joint"],
            "modelB": ["|modelB|joint"],
        }
        self.values = {
            "|modelA|joint.translate": [1.0, 2.0, 3.0],
            "|modelA|joint.rotate": [10.0, 20.0, 30.0],
            "|modelB|joint.translate": [4.0, 5.0, 6.0],
            "|modelB|joint.rotate": [40.0, 50.0, 60.0],
        }
        self.locks = {"|modelA|joint.rotateX": True}
        self.edges = {
            ("animA.output", "|modelA|joint.rotateX"),
            ("animB.output", "|modelB|joint.rotateX"),
        }
        self.undo_chunks = []
        self.bind_pose = None
        self.fail_bind_restore = False

    def objExists(self, node):
        return node in self.models or any(node == joint for joints in self.models.values() for joint in joints)

    def listRelatives(self, model, **_kwargs):
        return list(self.models.get(model, []))

    def getAttr(self, plug, **kwargs):
        if kwargs.get("lock"):
            return self.locks.get(plug, False)
        value = self.values.get(plug, [0.0, 0.0, 0.0])
        return [tuple(value)] if plug.endswith((".translate", ".rotate")) else value

    def setAttr(self, plug, *values, **kwargs):
        if "lock" in kwargs:
            self.locks[plug] = bool(kwargs["lock"])
            return
        self.values[plug] = list(values)

    def listConnections(self, target, **kwargs):
        connections = kwargs.get("connections", False)
        if connections:
            rows = []
            for source, destination in sorted(self.edges):
                if destination.startswith(f"{target}."):
                    rows.extend([destination, source])
            return rows
        return [source for source, destination in sorted(self.edges) if destination == target]

    def isConnected(self, source, destination):
        return (source, destination) in self.edges

    def disconnectAttr(self, source, destination):
        self.edges.remove((source, destination))

    def connectAttr(self, source, destination, **_kwargs):
        self.edges.add((source, destination))

    def dagPose(self, *_args, **kwargs):
        if kwargs.get("query"):
            return [self.bind_pose] if self.bind_pose else []
        if kwargs.get("restore") and self.fail_bind_restore:
            raise RuntimeError("pose cannot be achieved")
        return None

    def currentTime(self, *args, **kwargs):
        return 12.0 if kwargs.get("query") else args[0]

    def undoInfo(self, **kwargs):
        if kwargs.get("openChunk"):
            self.undo_chunks.append(kwargs.get("chunkName"))


class TestRestPoseManager(unittest.TestCase):
    def setUp(self):
        self.cmds = _FakeCmds()
        self.manager = RestPoseManager(self.cmds)

    def test_enter_and_return_restore_values_locks_and_exact_topology(self):
        with patch(
            "mmd_tools.actions.rest_pose_action.get_stored_bind_translate",
            return_value=(7.0, 8.0, 9.0),
        ):
            entered = self.manager.enter_rest_pose("modelA")

        self.assertTrue(entered.succeeded)
        self.assertTrue(entered.active)
        self.assertNotIn(("animA.output", "|modelA|joint.rotateX"), self.cmds.edges)
        self.assertIn(("animB.output", "|modelB|joint.rotateX"), self.cmds.edges)
        self.assertEqual(self.cmds.values["|modelA|joint.translate"], [7.0, 8.0, 9.0])
        self.assertEqual(self.cmds.values["|modelA|joint.rotate"], [0.0, 0.0, 0.0])

        returned = self.manager.return_to_motion()

        self.assertTrue(returned.succeeded)
        self.assertFalse(returned.active)
        self.assertIn(("animA.output", "|modelA|joint.rotateX"), self.cmds.edges)
        self.assertIn(("animB.output", "|modelB|joint.rotateX"), self.cmds.edges)
        self.assertEqual(self.cmds.values["|modelA|joint.translate"], [1.0, 2.0, 3.0])
        self.assertEqual(self.cmds.values["|modelA|joint.rotate"], [10.0, 20.0, 30.0])
        self.assertTrue(self.cmds.locks["|modelA|joint.rotateX"])

    def test_return_refuses_to_delete_connections_created_during_rest_display(self):
        with patch(
            "mmd_tools.actions.rest_pose_action.get_stored_bind_translate",
            return_value=(1.0, 2.0, 3.0),
        ):
            self.manager.enter_rest_pose("modelA")
        self.cmds.edges.add(("temporary.output", "|modelA|joint.translateY"))

        returned = self.manager.return_to_motion()

        self.assertFalse(returned.succeeded)
        self.assertTrue(returned.active)
        self.assertIn(
            ("temporary.output", "|modelA|joint.translateY"), self.cmds.edges
        )

    def test_failed_dag_pose_restore_falls_back_to_saved_bind_transform(self):
        self.cmds.bind_pose = "modelA_bindPose"
        self.cmds.fail_bind_restore = True
        with patch(
            "mmd_tools.actions.rest_pose_action.get_stored_bind_translate",
            return_value=(7.0, 8.0, 9.0),
        ):
            entered = self.manager.enter_rest_pose("modelA")

        self.assertTrue(entered.succeeded)
        self.assertEqual(self.cmds.values["|modelA|joint.translate"], [7.0, 8.0, 9.0])
        self.assertEqual(self.cmds.values["|modelA|joint.rotate"], [0.0, 0.0, 0.0])

    def test_model_switch_returns_active_motion(self):
        with patch(
            "mmd_tools.actions.rest_pose_action.get_stored_bind_translate",
            return_value=(1.0, 2.0, 3.0),
        ):
            self.manager.enter_rest_pose("modelA")

        result = self.manager.ensure_model("modelB")

        self.assertTrue(result.succeeded)
        self.assertFalse(self.manager.active)
        self.assertIn(("animA.output", "|modelA|joint.rotateX"), self.cmds.edges)

    def test_listeners_receive_enter_and_return_for_cross_ui_sync(self):
        states = []
        self.manager.add_listener(lambda result: states.append((result.active, result.model_root)))
        with patch(
            "mmd_tools.actions.rest_pose_action.get_stored_bind_translate",
            return_value=(1.0, 2.0, 3.0),
        ):
            self.manager.enter_rest_pose("modelA")
        self.manager.return_to_motion()

        self.assertEqual(states, [(True, "modelA"), (False, "modelA")])

    def test_notify_prunes_deleted_qt_listener_and_continues(self):
        received = []

        def deleted_listener(_result):
            raise RuntimeError(
                "Internal C++ object (MaterialSymbolToolButton) already deleted."
            )

        def live_listener(result):
            received.append(result.active)

        self.manager.add_listener(deleted_listener)
        self.manager.add_listener(live_listener)

        self.manager._notify(RestPoseResult(True, True))

        self.assertNotIn(deleted_listener, self.manager._listeners)
        self.assertIn(live_listener, self.manager._listeners)
        self.assertEqual(received, [True])

    def test_before_scene_change_returns_motion_before_save_or_open(self):
        with patch(
            "mmd_tools.actions.rest_pose_action.get_stored_bind_translate",
            return_value=(1.0, 2.0, 3.0),
        ):
            self.manager.enter_rest_pose("modelA")

        self.manager._before_scene_change()

        self.assertFalse(self.manager.active)
        self.assertIn(("animA.output", "|modelA|joint.rotateX"), self.cmds.edges)

    def test_missing_dag_pose_and_bind_metadata_fails_without_partial_rest_pose(self):
        with patch(
            "mmd_tools.actions.rest_pose_action.get_stored_bind_translate",
            return_value=None,
        ):
            entered = self.manager.enter_rest_pose("modelA")

        self.assertFalse(entered.succeeded)
        self.assertFalse(self.manager.active)
        self.assertIn(("animA.output", "|modelA|joint.rotateX"), self.cmds.edges)

    def test_undo_and_redo_synchronize_session_state_in_both_directions(self):
        with patch(
            "mmd_tools.actions.rest_pose_action.get_stored_bind_translate",
            return_value=(7.0, 8.0, 9.0),
        ):
            self.manager.enter_rest_pose("modelA")

        self.cmds.edges.add(("animA.output", "|modelA|joint.rotateX"))
        self.cmds.values["|modelA|joint.translate"] = [1.0, 2.0, 3.0]
        self.cmds.values["|modelA|joint.rotate"] = [10.0, 20.0, 30.0]
        self.manager._after_undo()
        self.assertFalse(self.manager.active)

        self.cmds.edges.remove(("animA.output", "|modelA|joint.rotateX"))
        self.cmds.values["|modelA|joint.translate"] = [7.0, 8.0, 9.0]
        self.cmds.values["|modelA|joint.rotate"] = [0.0, 0.0, 0.0]
        self.manager._after_redo()
        self.assertTrue(self.manager.active)


if __name__ == "__main__":
    unittest.main()
