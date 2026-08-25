"""Focused tests for the Animator keyless Reset Pose transaction."""

from __future__ import annotations

from copy import deepcopy
import unittest

from mmd_tools.ui.rest_pose_transaction import (
    ResetPoseTransaction,
    ResetPoseTransactionError,
)


_CHANNELS = (
    "translateX",
    "translateY",
    "translateZ",
    "rotateX",
    "rotateY",
    "rotateZ",
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
            for channel, value in zip(_CHANNELS, values):
                self.values[f"{node}.{channel}"] = value
                self.locks[f"{node}.{channel}"] = False
        self.incoming = {"|model|joint.rotateX": ["animCurve.output"]}
        self.node_types = {"animCurve": "animCurveTA"}
        self.history = {}
        self.keyframes = {"animCurve": {12.0: 11.0, 24.0: 20.0, 36.0: 33.0}}
        self.value_writes = []
        self.fail_plug = None
        self.mutate_topology_plug = None
        self.dirty_all_calls = 0
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

    def getAttr(self, plug, lock=False, **_kwargs):
        if lock:
            return self.locks.get(plug, False)
        return self.values[plug]

    def setAttr(self, plug, value=None, lock=None, **_kwargs):
        if lock is not None:
            self.locks[plug] = bool(lock)
            return
        if plug == self.fail_plug:
            self.fail_plug = None
            raise RuntimeError("setAttr failure")
        self.values[plug] = float(value)
        self.value_writes.append((plug, float(value)))
        if plug == self.mutate_topology_plug:
            self.incoming[plug] = ["replacement.output"]

    def listConnections(self, plug, source=False, destination=False, plugs=False):
        if source and not destination and plugs:
            return list(self.incoming.get(plug, ()))
        return []

    def nodeType(self, node):
        return self.node_types[node]

    def listHistory(self, node, **_kwargs):
        return list(self.history.get(node, ()))

    def dgdirty(self, **kwargs):
        if kwargs.get("allPlugs"):
            self.dirty_all_calls += 1

    def begin_undo(self):
        self._undo_snapshot = (
            dict(self.values),
            dict(self.locks),
            deepcopy(self.incoming),
            deepcopy(self.keyframes),
        )

    def undo(self):
        self.values, self.locks, self.incoming, self.keyframes = self._undo_snapshot


class _FakeAdapter:
    def __init__(
        self,
        cmds,
        *,
        undo_available=True,
        close_fails=False,
        fail_state_restore_once=False,
    ):
        self._cmds = cmds
        self.undo_available = undo_available
        self.close_fails = close_fails
        self.close_calls = 0
        self.undo_state = True
        self.undo_state_calls = []
        self.fail_state_restore_once = fail_state_restore_once

    def undo_info(self, **kwargs):
        if kwargs.get("query") and kwargs.get("state"):
            return self.undo_state
        if "stateWithoutFlush" in kwargs:
            if kwargs["stateWithoutFlush"] and self.fail_state_restore_once:
                self.fail_state_restore_once = False
                raise RuntimeError("state restoration failure")
            self.undo_state = bool(kwargs["stateWithoutFlush"])
            self.undo_state_calls.append(self.undo_state)
            return None
        if kwargs.get("openChunk"):
            if not self.undo_available:
                raise RuntimeError("undo unavailable")
            self._cmds.begin_undo()
        if kwargs.get("closeChunk"):
            self.close_calls += 1
            if self.close_fails:
                raise RuntimeError("close failure")


class TestResetPoseTransaction(unittest.TestCase):
    def _make(self, *, undo_available=True, close_fails=False):
        cmds = _FakeCmds()
        adapter = _FakeAdapter(
            cmds,
            undo_available=undo_available,
            close_fails=close_fails,
        )
        transaction = ResetPoseTransaction(
            adapter,
            model_root="|model",
            model_uuid="model-uuid",
            targets=["|model|joint"],
            bind_translations={"|model|joint": (1.0, 2.0, 3.0)},
        )
        return cmds, adapter, transaction

    def test_direct_anim_curve_is_overridden_without_editing_keys(self):
        cmds, _adapter, transaction = self._make()
        keys_before = deepcopy(cmds.keyframes)
        cmds.locks["|model|joint.rotateZ"] = True

        self.assertEqual(transaction.apply(), 1)

        self.assertEqual(
            tuple(cmds.values[f"|model|joint.{channel}"] for channel in _CHANNELS),
            (1.0, 2.0, 3.0, 0.0, 0.0, 0.0),
        )
        self.assertEqual(cmds.keyframes, keys_before)
        self.assertEqual(cmds.incoming["|model|joint.rotateX"], ["animCurve.output"])
        self.assertTrue(cmds.locks["|model|joint.rotateZ"])
        self.assertTrue(_adapter.undo_state)
        self.assertEqual(_adapter.undo_state_calls, [False, True])

    def test_animation_layer_with_time_curve_is_a_known_transient_writer(self):
        cmds, _adapter, transaction = self._make()
        cmds.incoming["|model|joint.rotateX"] = ["layerBlend.output"]
        cmds.node_types["layerBlend"] = "animBlendNodeAdditiveRotation"
        cmds.node_types["layerCurve"] = "animCurveTA"
        cmds.history["layerBlend"] = ["layerCurve"]
        keys_before = deepcopy(cmds.keyframes)

        self.assertEqual(transaction.apply(), 1)

        self.assertEqual(cmds.values["|model|joint.rotateX"], 0.0)
        self.assertEqual(cmds.keyframes, keys_before)

    def test_unitless_curve_pair_blend_and_static_blend_fail_before_mutation(self):
        for writer, writer_type, history in (
            ("driven.output", "animCurveUA", ()),
            ("pair.outRotateX", "pairBlend", ()),
            ("layerBlend.output", "animBlendNodeAdditiveRotation", ()),
        ):
            with self.subTest(writer_type=writer_type):
                cmds, _adapter, transaction = self._make()
                node = writer.rsplit(".", 1)[0]
                cmds.incoming["|model|joint.rotateX"] = [writer]
                cmds.node_types[node] = writer_type
                cmds.history[node] = list(history)
                before = dict(cmds.values)

                with self.assertRaises(ResetPoseTransactionError):
                    transaction.apply()

                self.assertEqual(cmds.values, before)
                self.assertEqual(cmds.value_writes, [])

    def test_unknown_procedural_writer_fails_before_mutation(self):
        cmds, _adapter, transaction = self._make()
        cmds.incoming["|model|joint.rotateX"] = ["solver.outputRotate[0]"]
        cmds.node_types["solver"] = "mmdCcdIk"
        before = dict(cmds.values)

        with self.assertRaisesRegex(ResetPoseTransactionError, "mmdCcdIk"):
            transaction.apply()

        self.assertEqual(cmds.values, before)
        self.assertEqual(cmds.value_writes, [])

    def test_already_rest_target_returns_zero_without_writes(self):
        cmds, _adapter, transaction = self._make()
        for channel, value in zip(_CHANNELS, (1.0, 2.0, 3.0, 0.0, 0.0, 0.0)):
            cmds.values[f"|model|joint.{channel}"] = value
        keys_before = deepcopy(cmds.keyframes)

        self.assertEqual(transaction.apply(), 0)

        self.assertEqual(cmds.value_writes, [])
        self.assertEqual(cmds.keyframes, keys_before)

    def test_unchanged_static_layer_member_does_not_block_other_channels(self):
        cmds, _adapter, transaction = self._make()
        cmds.values["|model|joint.rotateX"] = 0.0
        cmds.incoming["|model|joint.rotateX"] = ["staticBlend.output"]
        cmds.node_types["staticBlend"] = "animBlendNodeAdditiveRotation"
        cmds.history["staticBlend"] = []

        self.assertEqual(transaction.apply(), 1)

        self.assertEqual(cmds.values["|model|joint.rotateX"], 0.0)

    def test_changed_target_is_counted_once(self):
        cmds, _adapter, transaction = self._make()
        for channel, value in zip(_CHANNELS, (1.0, 2.0, 3.0, 0.0, 5.0, 0.0)):
            cmds.values[f"|model|joint.{channel}"] = value

        self.assertEqual(transaction.apply(), 1)

    def test_setattr_failure_rolls_back_values_topology_and_keys(self):
        cmds, _adapter, transaction = self._make()
        before = (dict(cmds.values), deepcopy(cmds.incoming), deepcopy(cmds.keyframes))
        cmds.fail_plug = "|model|joint.rotateY"

        with self.assertRaises(ResetPoseTransactionError):
            transaction.apply()

        self.assertEqual((cmds.values, cmds.incoming, cmds.keyframes), before)
        self.assertEqual(cmds.dirty_all_calls, 1)

    def test_topology_change_rolls_back(self):
        cmds, _adapter, transaction = self._make()
        before = (dict(cmds.values), deepcopy(cmds.incoming), deepcopy(cmds.keyframes))
        cmds.mutate_topology_plug = "|model|joint.rotateX"

        with self.assertRaisesRegex(ResetPoseTransactionError, "rollback was incomplete"):
            transaction.apply()

        self.assertEqual(cmds.values, before[0])
        self.assertNotEqual(cmds.incoming, before[1])
        self.assertEqual(cmds.keyframes, before[2])

    def test_manual_rollback_when_undo_is_unavailable(self):
        cmds, _adapter, transaction = self._make(undo_available=False)
        before = dict(cmds.values)
        cmds.fail_plug = "|model|joint.rotateY"

        with self.assertRaises(ResetPoseTransactionError):
            transaction.apply()

        self.assertEqual(cmds.values, before)
        self.assertEqual(cmds.dirty_all_calls, 1)

    def test_undo_close_failure_never_returns_success(self):
        cmds, adapter, transaction = self._make(close_fails=True)
        before = dict(cmds.values)

        with self.assertRaisesRegex(ResetPoseTransactionError, "Undo close failed"):
            transaction.apply()

        self.assertEqual(adapter.close_calls, 1)
        self.assertEqual(cmds.values, before)

    def test_undo_state_restore_failure_is_surfaced_after_value_rollback(self):
        cmds = _FakeCmds()
        adapter = _FakeAdapter(cmds, fail_state_restore_once=True)
        before = dict(cmds.values)
        transaction = ResetPoseTransaction(
            adapter,
            model_root="|model",
            model_uuid="model-uuid",
            targets=["|model|joint"],
            bind_translations={"|model|joint": (1.0, 2.0, 3.0)},
        )

        with self.assertRaisesRegex(
            ResetPoseTransactionError,
            "state restoration failure",
        ):
            transaction.apply()

        self.assertEqual(cmds.values, before)
        self.assertTrue(adapter.undo_state)

    def test_semantic_authored_inputs_use_joint_bind_basis_without_keying(self):
        for node in ("appendNode", "boneMorphAccum"):
            with self.subTest(node=node):
                cmds, _adapter, _transaction = self._make()
                for attribute, values in (
                    ("baseTranslate", (7.0, 8.0, 9.0)),
                    ("baseRotate", (15.0, 25.0, 35.0)),
                ):
                    for axis, value in zip("XYZ", values):
                        cmds.values[f"{node}.{attribute}{axis}"] = value
                cmds.incoming = {f"{node}.baseRotateX": ["animCurve.output"]}
                keys_before = deepcopy(cmds.keyframes)
                joint_before = {
                    plug: value
                    for plug, value in cmds.values.items()
                    if plug.startswith("|model|joint.")
                }
                transaction = ResetPoseTransaction(
                    _FakeAdapter(cmds),
                    model_root="|model",
                    model_uuid="model-uuid",
                    targets=["|model|joint"],
                    bind_translations={"|model|joint": (1.0, 2.0, 3.0)},
                    authored_plugs_by_target={
                        "|model|joint": (
                            f"{node}.baseTranslate",
                            f"{node}.baseRotate",
                        )
                    },
                )

                self.assertEqual(transaction.apply(), 1)
                self.assertEqual(
                    tuple(cmds.values[f"{node}.baseTranslate{axis}"] for axis in "XYZ"),
                    (1.0, 2.0, 3.0),
                )
                self.assertEqual(
                    tuple(cmds.values[f"{node}.baseRotate{axis}"] for axis in "XYZ"),
                    (0.0, 0.0, 0.0),
                )
                self.assertEqual(cmds.keyframes, keys_before)
                self.assertEqual(
                    {
                        plug: value
                        for plug, value in cmds.values.items()
                        if plug.startswith("|model|joint.")
                    },
                    joint_before,
                )

    def test_solver_authored_input_compound_expands_to_zero(self):
        cmds, _adapter, _transaction = self._make()
        plugs = tuple(
            f"solver.inputRotate[8].inputRotateElement{axis}" for axis in "XYZ"
        )
        for plug, value in zip(plugs, (10.0, 20.0, 30.0)):
            cmds.values[plug] = value
        cmds.incoming.clear()
        transaction = ResetPoseTransaction(
            _FakeAdapter(cmds),
            model_root="|model",
            model_uuid="model-uuid",
            targets=["|model|joint"],
            authored_plugs_by_target={"|model|joint": ("solver.inputRotate[8]",)},
        )

        self.assertEqual(transaction.apply(), 1)
        self.assertEqual(tuple(cmds.values[plug] for plug in plugs), (0.0, 0.0, 0.0))

    def test_missing_bind_authority_fails_before_mutation(self):
        cmds, _adapter, _transaction = self._make()
        for axis, value in zip("XYZ", (7.0, 8.0, 9.0)):
            cmds.values[f"appendNode.baseTranslate{axis}"] = value
        cmds.incoming.clear()
        transaction = ResetPoseTransaction(
            _FakeAdapter(cmds),
            model_root="|model",
            model_uuid="model-uuid",
            targets=["|model|joint"],
            authored_plugs_by_target={"|model|joint": ("appendNode.baseTranslate",)},
        )

        with self.assertRaisesRegex(ResetPoseTransactionError, "bind translation"):
            transaction.apply()

        self.assertEqual(cmds.value_writes, [])

    def test_target_outside_uuid_scope_fails(self):
        cmds, _adapter, _transaction = self._make()
        transaction = ResetPoseTransaction(
            _FakeAdapter(cmds),
            model_root="|model",
            model_uuid="model-uuid",
            targets=["|other|joint"],
        )

        with self.assertRaisesRegex(ResetPoseTransactionError, "outside model UUID"):
            transaction.apply()


if __name__ == "__main__":
    unittest.main()
