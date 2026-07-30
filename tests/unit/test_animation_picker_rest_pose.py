"""Focused tests for the Animator model-scoped Rest Pose transaction."""

from __future__ import annotations

import unittest

from mmd_tools.ui.rest_pose_transaction import (
    RestPoseTransaction,
    RestPoseTransactionError,
)


class _FakeCmds:
    def __init__(self):
        self.uuid = "model-uuid"
        self.paths = {
            "|model": "|model",
            "model": "|model",
            "|model|joint": "|model|joint",
            "joint": "|model|joint",
            "|other|joint": "|other|joint",
            "animCurve.output": "animCurve.output",
        }
        self.values = {
            "|model|joint.translateX": 4.0,
            "|model|joint.translateY": 5.0,
            "|model|joint.translateZ": 6.0,
            "|model|joint.rotateX": 20.0,
            "|model|joint.rotateY": 30.0,
            "|model|joint.rotateZ": 40.0,
            "animCurve.output": 20.0,
        }
        self.locks = {plug: False for plug in self.values}
        self.incoming = {
            "|model|joint.rotateX": ["animCurve.output"],
        }
        self.set_calls = []
        self.connections = []
        self.frame = 24.0
        self.fail_next_value = None

    def ls(self, node=None, long=False, uuid=False, **_kwargs):
        if uuid:
            return [self.uuid] if node in ("model", "|model") else []
        if node is None:
            return []
        nodes = node if isinstance(node, (list, tuple)) else [node]
        if long:
            return [self.paths[value] for value in nodes if value in self.paths]
        return list(nodes)

    def getAttr(self, plug, lock=False):
        if lock:
            return self.locks.get(plug, False)
        return self.values[plug]

    def setAttr(self, plug, value=None, lock=None, **_kwargs):
        if lock is not None:
            self.locks[plug] = bool(lock)
            return
        self.set_calls.append((plug, value))
        if self.fail_next_value == plug:
            self.fail_next_value = None
            raise RuntimeError("setAttr failure")
        self.values[plug] = value

    def listConnections(self, plug, source=False, destination=False, plugs=False):
        if source and not destination and plugs:
            return list(self.incoming.get(plug, ()))
        return []

    def disconnectAttr(self, source, destination):
        self.incoming[destination].remove(source)

    def connectAttr(self, source, destination, force=False):
        del force
        self.incoming.setdefault(destination, []).append(source)
        self.connections.append((source, destination))
        if source in self.values:
            self.values[destination] = self.values[source]

    def nodeType(self, source):
        return "animCurveTA" if str(source).startswith("animCurve") else "transform"

    def keyframe(self, _source, query=False, timeChange=False, valueChange=False):
        del query
        if timeChange:
            return (1.0, 24.0)
        if valueChange:
            return (0.0, 20.0)
        return ()

    def currentTime(self, value=None, query=False, edit=False):
        del edit
        if query:
            return self.frame
        self.frame = value


class _FakeAdapter:
    def __init__(self, cmds):
        self._cmds = cmds
        self.selected = ["|model|joint"]
        self.undo_chunks = []

    def ls(self, *args, **kwargs):
        if kwargs.get("selection"):
            return list(self.selected)
        return self._cmds.ls(*args, **kwargs)

    def current_time(self):
        return self._cmds.currentTime(query=True)

    def select(self, nodes, replace=True):
        if replace:
            self.selected = list(nodes)

    def undo_info(self, **kwargs):
        if kwargs.get("openChunk"):
            self.undo_chunks.append(kwargs.get("chunkName"))


class TestRestPoseTransaction(unittest.TestCase):
    def _make(self):
        cmds = _FakeCmds()
        adapter = _FakeAdapter(cmds)
        transaction = RestPoseTransaction(
            adapter,
            model_root="|model",
            model_uuid="model-uuid",
            targets=["|model|joint"],
            bind_translations={"|model|joint": (1.0, 2.0, 3.0)},
        )
        return cmds, adapter, transaction

    def test_apply_restore_preserves_values_writer_curve_frame_and_locks(self):
        cmds, adapter, transaction = self._make()
        cmds.locks["|model|joint.rotateZ"] = True

        self.assertEqual(transaction.apply(), 1)
        self.assertEqual(cmds.values["|model|joint.translateX"], 1.0)
        self.assertEqual(cmds.values["|model|joint.translateY"], 2.0)
        self.assertEqual(cmds.values["|model|joint.translateZ"], 3.0)
        self.assertEqual(cmds.values["|model|joint.rotateX"], 0.0)
        self.assertEqual(cmds.incoming["|model|joint.rotateX"], [])
        self.assertTrue(cmds.locks["|model|joint.rotateZ"])

        cmds.frame = 48.0
        adapter.selected = []
        transaction.restore()

        self.assertEqual(cmds.values["|model|joint.translateX"], 4.0)
        self.assertEqual(cmds.values["|model|joint.rotateX"], 20.0)
        self.assertEqual(cmds.incoming["|model|joint.rotateX"], ["animCurve.output"])
        self.assertEqual(cmds.frame, 24.0)
        self.assertEqual(adapter.selected, ["|model|joint"])
        self.assertTrue(cmds.locks["|model|joint.rotateZ"])
        self.assertEqual(cmds.keyframe("animCurve.output", query=True, valueChange=True), (0.0, 20.0))

    def test_uuid_mismatch_is_fail_closed_before_mutation(self):
        cmds, _adapter, transaction = self._make()
        cmds.uuid = "different-model"

        with self.assertRaises(RestPoseTransactionError):
            transaction.apply()
        self.assertEqual(cmds.set_calls, [])

    def test_apply_failure_rolls_back_prior_channels_and_connection(self):
        cmds, _adapter, transaction = self._make()
        cmds.fail_next_value = "|model|joint.rotateX"

        with self.assertRaises(RestPoseTransactionError):
            transaction.apply()
        self.assertEqual(cmds.values["|model|joint.translateX"], 4.0)
        self.assertEqual(cmds.values["|model|joint.rotateX"], 20.0)
        self.assertEqual(cmds.incoming["|model|joint.rotateX"], ["animCurve.output"])

    def test_multiple_writers_and_out_of_scope_target_are_rejected(self):
        cmds, adapter, transaction = self._make()
        cmds.incoming["|model|joint.rotateX"] = ["a.output", "b.output"]
        with self.assertRaises(RestPoseTransactionError):
            transaction.apply()

        outside = RestPoseTransaction(
            adapter,
            model_root="|model",
            model_uuid="model-uuid",
            targets=["|other|joint"],
        )
        with self.assertRaises(RestPoseTransactionError):
            outside.apply()

    def test_procedural_append_output_stays_connected_while_source_rests(self):
        cmds, adapter, _transaction = self._make()
        append = "|model|append"
        cmds.paths[append] = append
        for channel, value in zip(
            ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"),
            (7.0, 8.0, 9.0, 11.0, 12.0, 13.0),
        ):
            cmds.values[f"{append}.{channel}"] = value
            cmds.locks[f"{append}.{channel}"] = False
        procedural_plug = f"{append}.rotateX"
        cmds.values["pairBlend.output"] = 11.0
        cmds.incoming[procedural_plug] = ["pairBlend.output"]
        cmds.locks[procedural_plug] = True
        transaction = RestPoseTransaction(
            adapter,
            model_root="|model",
            model_uuid="model-uuid",
            targets=["|model|joint", append],
            bind_translations={"|model|joint": (1.0, 2.0, 3.0)},
        )

        self.assertEqual(transaction.apply(), 2)
        self.assertEqual(cmds.values["|model|joint.translateX"], 1.0)
        self.assertEqual(cmds.values[procedural_plug], 11.0)
        self.assertEqual(cmds.incoming[procedural_plug], ["pairBlend.output"])
        self.assertTrue(cmds.locks[procedural_plug])

        transaction.restore()
        self.assertEqual(cmds.incoming[procedural_plug], ["pairBlend.output"])
        self.assertTrue(cmds.locks[procedural_plug])

    def test_restore_refuses_foreign_writer_without_disconnect(self):
        """A writer added after apply is never removed by rollback."""
        cmds, _adapter, transaction = self._make()
        transaction.apply()

        foreign_source = "foreign.output"
        cmds.values[foreign_source] = 99.0
        cmds.incoming["|model|joint.rotateX"] = [foreign_source]

        with self.assertRaisesRegex(RestPoseTransactionError, "topology drift"):
            transaction.restore()

        self.assertEqual(
            cmds.incoming["|model|joint.rotateX"],
            [foreign_source],
            "foreign writer must remain connected after fail-closed rollback",
        )


if __name__ == "__main__":
    unittest.main()
