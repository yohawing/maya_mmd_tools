"""Focused tests for the Animator one-shot Reset Pose transaction."""

from __future__ import annotations

import unittest

from mmd_tools.ui.rest_pose_transaction import (
    ResetPoseTransaction,
    ResetPoseTransactionError,
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
        }
        self.values = {}
        self.locks = {}
        for node, values in (
            ("|model|joint", (4.0, 5.0, 6.0, 20.0, 30.0, 40.0)),
            ("|other|joint", (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)),
        ):
            for channel, value in zip(
                (
                    "translateX",
                    "translateY",
                    "translateZ",
                    "rotateX",
                    "rotateY",
                    "rotateZ",
                ),
                values,
            ):
                self.values[f"{node}.{channel}"] = value
                self.locks[f"{node}.{channel}"] = False
        self.incoming = {"|model|joint.rotateX": ["animCurve.output"]}
        self.curve_type = "animCurveTA"
        self.keyframes = []
        self.frame = 24.0
        self.fail_plug = None
        self._undo_snapshot = None

    def ls(self, node=None, long=False, uuid=False, **_kwargs):
        if uuid:
            return [self.uuid] if node in ("model", "|model") else []
        if node is None:
            return []
        nodes = node if isinstance(node, (list, tuple)) else [node]
        if long:
            return [self.paths[value] for value in nodes if value in self.paths]
        return list(nodes)

    def getAttr(self, plug, lock=False, settable=False):
        if lock:
            return self.locks.get(plug, False)
        if settable:
            return not self.incoming.get(plug)
        return self.values[plug]

    def setAttr(self, plug, value=None, lock=None, **_kwargs):
        if lock is not None:
            self.locks[plug] = bool(lock)
            return
        if plug == self.fail_plug:
            self.fail_plug = None
            raise RuntimeError("setAttr failure")
        self.values[plug] = float(value)

    def listConnections(self, plug, source=False, destination=False, plugs=False):
        if source and not destination and plugs:
            return list(self.incoming.get(plug, ()))
        return []

    def nodeType(self, node):
        return self.curve_type if node == "animCurve" else "pairBlend"

    def currentTime(self, query=False):
        return self.frame if query else None

    def setKeyframe(self, curve, time, value):
        self.keyframes.append((curve, tuple(time), float(value)))
        for plug, incoming in self.incoming.items():
            if incoming == [f"{curve}.output"]:
                self.values[plug] = float(value)

    def begin_undo(self):
        self._undo_snapshot = (
            dict(self.values),
            dict(self.locks),
            list(self.keyframes),
        )

    def undo(self):
        self.values, self.locks, self.keyframes = self._undo_snapshot


class _FakeAdapter:
    def __init__(self, cmds, *, undo_available=True):
        self._cmds = cmds
        self.undo_available = undo_available

    def undo_info(self, **kwargs):
        if kwargs.get("openChunk"):
            if not self.undo_available:
                raise RuntimeError("undo unavailable")
            self._cmds.begin_undo()


class TestResetPoseTransaction(unittest.TestCase):
    def _make(self, *, undo_available=True):
        cmds = _FakeCmds()
        adapter = _FakeAdapter(cmds, undo_available=undo_available)
        transaction = ResetPoseTransaction(
            adapter,
            model_root="|model",
            model_uuid="model-uuid",
            targets=["|model|joint"],
            bind_translations={"|model|joint": (1.0, 2.0, 3.0)},
        )
        return cmds, transaction

    def test_apply_writes_rest_values_and_current_frame_curve_key(self):
        cmds, transaction = self._make()
        cmds.locks["|model|joint.rotateZ"] = True

        self.assertEqual(transaction.apply(), 1)

        self.assertEqual(cmds.values["|model|joint.translateX"], 1.0)
        self.assertEqual(cmds.values["|model|joint.translateY"], 2.0)
        self.assertEqual(cmds.values["|model|joint.translateZ"], 3.0)
        self.assertEqual(cmds.values["|model|joint.rotateX"], 0.0)
        self.assertEqual(cmds.values["|model|joint.rotateY"], 0.0)
        self.assertEqual(cmds.values["|model|joint.rotateZ"], 0.0)
        self.assertEqual(cmds.incoming["|model|joint.rotateX"], ["animCurve.output"])
        self.assertEqual(cmds.keyframes, [("animCurve", (24.0, 24.0), 0.0)])
        self.assertEqual(cmds.frame, 24.0)
        self.assertTrue(cmds.locks["|model|joint.rotateZ"])
        self.assertFalse(hasattr(transaction, "restore"))

    def test_failure_uses_single_undo_to_restore_completed_writes(self):
        cmds, transaction = self._make()
        original = dict(cmds.values)
        cmds.fail_plug = "|model|joint.rotateY"

        with self.assertRaises(ResetPoseTransactionError):
            transaction.apply()

        self.assertEqual(cmds.values, original)
        self.assertEqual(cmds.keyframes, [])

    def test_uuid_mismatch_and_out_of_scope_target_fail_closed(self):
        cmds, transaction = self._make()
        cmds.uuid = "different-model"
        with self.assertRaises(ResetPoseTransactionError):
            transaction.apply()

        cmds.uuid = "model-uuid"
        outside = ResetPoseTransaction(
            _FakeAdapter(cmds),
            model_root="|model",
            model_uuid="model-uuid",
            targets=["|other|joint"],
        )
        with self.assertRaises(ResetPoseTransactionError):
            outside.apply()

    def test_procedural_writer_is_left_connected_and_unchanged(self):
        cmds, transaction = self._make()
        plug = "|model|joint.rotateY"
        cmds.incoming[plug] = ["pairBlend.output"]
        before = cmds.values[plug]

        self.assertEqual(transaction.apply(), 1)

        self.assertEqual(cmds.incoming[plug], ["pairBlend.output"])
        self.assertEqual(cmds.values[plug], before)

    def test_driven_key_curve_types_are_not_written(self):
        cmds, transaction = self._make()
        plug = "|model|joint.rotateX"
        cmds.curve_type = "animCurveUA"
        before = cmds.values[plug]

        self.assertEqual(transaction.apply(), 1)

        self.assertEqual(cmds.values[plug], before)
        self.assertEqual(cmds.incoming[plug], ["animCurve.output"])
        self.assertEqual(cmds.keyframes, [])

    def test_animated_channels_require_undo_support(self):
        cmds, transaction = self._make(undo_available=False)
        original = dict(cmds.values)

        with self.assertRaisesRegex(ResetPoseTransactionError, "requires Maya Undo"):
            transaction.apply()

        self.assertEqual(cmds.values, original)
        self.assertEqual(cmds.keyframes, [])


if __name__ == "__main__":
    unittest.main()
