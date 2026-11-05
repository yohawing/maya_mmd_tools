"""Unit tests for pose manipulation actions."""

from __future__ import annotations

import unittest
from copy import deepcopy
from unittest.mock import patch

from mmd_tools.actions.pose_actions import (
    BakeAnimationAction,
    BakeAnimationRequest,
    CleanCurvesAction,
    CleanCurvesRequest,
    CopyPoseAction,
    CopyPoseRequest,
    MirrorPoseAction,
    MirrorPoseRequest,
    PastePoseAction,
    PastePoseRequest,
    PoseTransform,
    ResetPoseAction,
    ResetPoseRequest,
)


class _FakeAdapter:
    """Minimal adapter stub for pose action tests."""

    def __init__(self):
        self._cmds = self
        self._transforms: dict[str, tuple[list, list]] = {}
        self._undo_chunks: list[str] = []
        self._undo_closed: int = 0
        self.keys = {}
        self.uuids = {}
        self.locked = set()
        self.sources = {}
        self.types = {}
        self.histories = {}
        self.fail_key_at = None
        self.ignore_values = False
        self.key_calls = 0
        self.undo_calls = 0
        self._undo_name = "Previous user edit"
        self._chunk = None
        self._changed = False

    def set_joint(self, name: str, t=(0, 0, 0), r=(0, 0, 0)):
        self._transforms[name] = (list(t), list(r))
        self.uuids[name] = "uuid-" + name

    def xform(self, node, **kwargs):
        if kwargs.get("query"):
            if node not in self._transforms:
                raise RuntimeError(f"Unknown node: {node}")
            t, r = self._transforms[node]
            if kwargs.get("translation"):
                return list(t)
            if kwargs.get("rotation"):
                return list(r)
            return None
        t = kwargs.get("translation")
        if t is not None:
            self._transforms.setdefault(node, ([0, 0, 0], [0, 0, 0]))
            self._transforms[node] = (list(t), self._transforms[node][1])
        r = kwargs.get("rotation")
        if r is not None:
            self._transforms.setdefault(node, ([0, 0, 0], [0, 0, 0]))
            self._transforms[node] = (self._transforms[node][0], list(r))

    def undo_info(self, **kwargs):
        if kwargs.get("openChunk"):
            self._undo_chunks.append(kwargs.get("chunkName", ""))
        if kwargs.get("closeChunk"):
            self._undo_closed += 1

    def ls(self, node=None, **kwargs):
        if kwargs.get("type") == "animLayer" or node not in self._transforms:
            return []
        return [self.uuids[node]] if kwargs.get("uuid") else [node]

    def nodeType(self, node):
        return self.types.get(node, "joint")

    def attributeQuery(self, attr, **kwargs):
        return []

    def getAttr(self, plug, **kwargs):
        if kwargs.get("lock"):
            return plug in self.locked
        node, attr = plug.split(".")
        family = 0 if attr.startswith("translate") else 1
        return self._transforms[node][family]["XYZ".index(attr[-1])]

    def lockNode(self, *_args, **_kwargs):
        return [False]

    def listConnections(self, plug, **_kwargs):
        return self.sources.get(plug, [])

    def listHistory(self, node, **_kwargs):
        return self.histories.get(node, [])

    def animLayer(self, **_kwargs):
        return None

    def currentTime(self, *_args, **_kwargs):
        return 1.0

    def dgdirty(self, *_args, **_kwargs):
        pass

    def setKeyframe(self, plug, **kwargs):
        self.key_calls += 1
        if self.fail_key_at == self.key_calls:
            raise RuntimeError("injected key failure")
        self._changed = True
        self.keys[(plug, kwargs["time"])] = kwargs["value"]
        if not self.ignore_values:
            node, attr = plug.split(".")
            family = 0 if attr.startswith("translate") else 1
            self._transforms[node][family]["XYZ".index(attr[-1])] = kwargs["value"]
        return 1

    def undoInfo(self, **kwargs):
        if kwargs.get("query"):
            return self._undo_name if kwargs.get("undoName") else True
        if kwargs.get("openChunk"):
            self._chunk = kwargs["chunkName"]
            self._saved = deepcopy((self._transforms, self.keys))
            self._changed = False
        self.undo_info(**kwargs)
        if kwargs.get("closeChunk") and self._changed:
            self._undo_name = self._chunk

    def undo(self):
        self.undo_calls += 1
        self._transforms, self.keys = self._saved
        self._undo_name = "Previous user edit"


class TestCopyPoseAction(unittest.TestCase):
    def test_copy_empty(self):
        adapter = _FakeAdapter()
        result = CopyPoseAction(adapter).execute(CopyPoseRequest(joints=[]))
        self.assertTrue(result.succeeded)
        self.assertEqual(result.pose, {})

    def test_copy_snapshots_transforms(self):
        adapter = _FakeAdapter()
        adapter.set_joint("joint1", t=(1, 2, 3), r=(10, 20, 30))
        adapter.set_joint("joint2", t=(4, 5, 6), r=(40, 50, 60))
        result = CopyPoseAction(adapter).execute(
            CopyPoseRequest(joints=["joint1", "joint2"])
        )
        self.assertTrue(result.succeeded)
        self.assertEqual(len(result.pose), 2)
        self.assertEqual(result.pose["joint1"].translation, (1, 2, 3))
        self.assertEqual(result.pose["joint1"].rotation, (10, 20, 30))
        self.assertEqual(result.pose["joint2"].translation, (4, 5, 6))

    def test_copy_error_on_missing_joint(self):
        adapter = _FakeAdapter()
        result = CopyPoseAction(adapter).execute(
            CopyPoseRequest(joints=["nonexistent"])
        )
        self.assertFalse(result.succeeded)
        self.assertIsNotNone(result.error)


class TestPastePoseAction(unittest.TestCase):
    def test_paste_empty(self):
        adapter = _FakeAdapter()
        result = PastePoseAction(adapter).execute(PastePoseRequest(pose={}))
        self.assertTrue(result.succeeded)

    def test_paste_applies_transforms(self):
        adapter = _FakeAdapter()
        adapter.set_joint("joint1")
        pose = {"joint1": PoseTransform(translation=(1, 2, 3), rotation=(10, 20, 30))}
        result = PastePoseAction(adapter).execute(PastePoseRequest(pose=pose))
        self.assertTrue(result.succeeded)
        self.assertEqual(result.applied_count, 1)
        t, r = adapter._transforms["joint1"]
        self.assertEqual(t, [1, 2, 3])
        self.assertEqual(r, [10, 20, 30])

    def test_paste_opens_undo_chunk(self):
        adapter = _FakeAdapter()
        adapter.set_joint("j1")
        pose = {"j1": PoseTransform(translation=(0, 0, 0), rotation=(0, 0, 0))}
        PastePoseAction(adapter).execute(PastePoseRequest(pose=pose))
        self.assertEqual(len(adapter._undo_chunks), 1)
        self.assertTrue(adapter._undo_chunks[0].startswith("Paste Pose "))
        self.assertEqual(adapter._undo_closed, 1)

    def test_paste_multiple_joints(self):
        adapter = _FakeAdapter()
        adapter.set_joint("j1")
        adapter.set_joint("j2")
        pose = {
            "j1": PoseTransform(translation=(1, 0, 0), rotation=(0, 0, 0)),
            "j2": PoseTransform(translation=(2, 0, 0), rotation=(0, 0, 0)),
        }
        result = PastePoseAction(adapter).execute(PastePoseRequest(pose=pose))
        self.assertTrue(result.succeeded)
        self.assertEqual(result.applied_count, 2)
        self.assertEqual(adapter._transforms["j1"][0], [1, 0, 0])
        self.assertEqual(adapter._transforms["j2"][0], [2, 0, 0])

    def test_paste_keys_without_creating_an_animation_layer(self):
        adapter = _FakeAdapter()
        adapter.set_joint("joint1", t=(1, 2, 3), r=(10, 20, 30))
        copied = CopyPoseAction(adapter).execute(CopyPoseRequest(["joint1"]))
        adapter.set_joint("joint1")
        result = PastePoseAction(adapter).execute(PastePoseRequest(copied.pose))
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual(adapter.keys[("joint1.rotateZ", 1.0)], 30)
        self.assertEqual(len(adapter.keys), 6)

    def test_replaced_node_is_rejected_before_any_write(self):
        adapter = _FakeAdapter()
        adapter.set_joint("j1")
        copied = CopyPoseAction(adapter).execute(CopyPoseRequest(["j1"]))
        adapter.uuids["j1"] = "replacement"
        result = PastePoseAction(adapter).execute(PastePoseRequest(copied.pose))
        self.assertFalse(result.succeeded)
        self.assertIn("changed since Copy", str(result.error))
        self.assertEqual(adapter._undo_chunks, [])

    def test_all_targets_are_preflighted_before_writing(self):
        adapter = _FakeAdapter()
        adapter.set_joint("j1")
        adapter.set_joint("j2")
        copied = CopyPoseAction(adapter).execute(CopyPoseRequest(["j1", "j2"]))
        adapter.locked.add("j2.rotateX")
        result = PastePoseAction(adapter).execute(PastePoseRequest(copied.pose))
        self.assertFalse(result.succeeded)
        self.assertIn("locked", str(result.error))
        self.assertEqual(adapter.key_calls, 0)

    def test_partial_write_rolls_back_its_own_chunk(self):
        adapter = _FakeAdapter()
        adapter.set_joint("j1")
        adapter.fail_key_at = 2
        pose = {"j1": PoseTransform((1, 2, 3), (10, 20, 30))}
        result = PastePoseAction(adapter).execute(PastePoseRequest(pose))
        self.assertFalse(result.succeeded)
        self.assertEqual(adapter._transforms["j1"], ([0, 0, 0], [0, 0, 0]))
        self.assertEqual(adapter.keys, {})
        self.assertEqual(adapter.undo_calls, 1)

    def test_first_write_failure_does_not_undo_previous_user_edit(self):
        adapter = _FakeAdapter()
        adapter.set_joint("j1")
        adapter.fail_key_at = 1
        result = PastePoseAction(adapter).execute(PastePoseRequest({"j1": PoseTransform((1, 2, 3), (10, 20, 30))}))
        self.assertFalse(result.succeeded)
        self.assertEqual(adapter.undo_calls, 0)
        self.assertEqual(adapter._undo_name, "Previous user edit")

    def test_zero_keys_is_failure_even_when_pose_values_already_match(self):
        adapter = _FakeAdapter()
        adapter.set_joint("j1")
        adapter.setKeyframe = lambda *_args, **_kwargs: 0
        result = PastePoseAction(adapter).execute(PastePoseRequest({"j1": PoseTransform((0, 0, 0), (0, 0, 0))}))
        self.assertFalse(result.succeeded)
        self.assertIn("did not create a key", str(result.error))
        self.assertEqual(adapter.undo_calls, 0)

    def test_ineffective_keying_is_failure_and_rolls_back(self):
        adapter = _FakeAdapter()
        adapter.set_joint("j1")
        adapter.ignore_values = True
        result = PastePoseAction(adapter).execute(PastePoseRequest({"j1": PoseTransform((1, 2, 3), (10, 20, 30))}))
        self.assertFalse(result.succeeded)
        self.assertEqual(adapter.keys, {})
        self.assertEqual(adapter.undo_calls, 1)

    def test_conversion_or_blend_cannot_hide_a_solver_writer(self):
        for wrapper in ("unitConversion", "animBlendNodeAdditiveRotation"):
            with self.subTest(wrapper=wrapper):
                adapter = _FakeAdapter()
                adapter.set_joint("j1")
                adapter.sources["j1.rotateX"] = ["wrapper.output"]
                adapter.types.update(wrapper=wrapper, solver="mmdCcdIk")
                adapter.histories["wrapper"] = ["solver"]
                result = PastePoseAction(adapter).execute(PastePoseRequest({"j1": PoseTransform((0, 0, 0), (10, 0, 0))}))
                self.assertFalse(result.succeeded)
                self.assertIn("unsupported driver", str(result.error))
                self.assertEqual(adapter._undo_chunks, [])

    def test_control_owned_rotation_policy_keeps_locked_translation_out_of_pose(self):
        adapter = _FakeAdapter()
        adapter.set_joint("model")
        adapter.set_joint("control", r=(10, 20, 30))
        adapter.locked.update("control.translate" + axis for axis in "XYZ")
        metadata = {
            "owner": "CONTROL_OWNED", "controls": {"upper_body": "uuid-control"},
            "bindings": {"upper_body": {"inputKind": "direct_channel", "authoredPlugs": ["joint.rotate"]}},
        }
        with patch("mmd_tools.core.mmd_control_rig_builder.read_mmd_control_rig_metadata", return_value=metadata):
            with patch("mmd_tools.core.pose_authoring.resolve_model_pose_plugs", return_value=None):
                copied = CopyPoseAction(adapter).execute(CopyPoseRequest(["control"], model_root="model"))
                self.assertTrue(copied.succeeded, copied.error)
                self.assertEqual(copied.pose["control"].authoring.route.plugs, tuple("control.rotate" + axis for axis in "XYZ"))
                adapter.set_joint("control")
                result = PastePoseAction(adapter).execute(PastePoseRequest(copied.pose, model_root="model"))
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual(len(adapter.keys), 3)
        self.assertEqual(adapter._transforms["control"][1], [10, 20, 30])


class TestResetPoseAction(unittest.TestCase):
    def test_reset_empty(self):
        adapter = _FakeAdapter()
        result = ResetPoseAction(adapter).execute(ResetPoseRequest(joints=[]))
        self.assertTrue(result.succeeded)
        self.assertEqual(result.reset_count, 0)

    def test_reset_restores_bind_translation_and_zeroes_rotation(self):
        adapter = _FakeAdapter()
        adapter.set_joint("j1", t=(5, 5, 5), r=(45, 45, 45))
        result = ResetPoseAction(adapter).execute(
            ResetPoseRequest(
                joints=["j1"],
                bind_translations={"j1": (1, 2, 3)},
            )
        )
        self.assertTrue(result.succeeded)
        self.assertEqual(result.reset_count, 1)
        t, r = adapter._transforms["j1"]
        self.assertEqual(t, [1, 2, 3])
        self.assertEqual(r, [0, 0, 0])

    def test_reset_without_bind_translation_does_not_collapse_joint(self):
        adapter = _FakeAdapter()
        adapter.set_joint("j1", t=(5, 5, 5), r=(45, 45, 45))

        ResetPoseAction(adapter).execute(ResetPoseRequest(joints=["j1"]))

        self.assertEqual(adapter._transforms["j1"], ([5, 5, 5], [0, 0, 0]))

    def test_reset_opens_undo_chunk(self):
        adapter = _FakeAdapter()
        adapter.set_joint("j1")
        ResetPoseAction(adapter).execute(ResetPoseRequest(joints=["j1"]))
        self.assertEqual(adapter._undo_chunks[0], "Reset Pose")
        self.assertEqual(adapter._undo_closed, 1)


class TestStubActions(unittest.TestCase):
    def test_mirror_returns_not_implemented(self):
        adapter = _FakeAdapter()
        result = MirrorPoseAction(adapter).execute(
            MirrorPoseRequest(joints=["j1"])
        )
        self.assertFalse(result.succeeded)
        self.assertIsInstance(result.error, NotImplementedError)

    def test_bake_returns_not_implemented(self):
        adapter = _FakeAdapter()
        result = BakeAnimationAction(adapter).execute(
            BakeAnimationRequest(joints=["j1"])
        )
        self.assertFalse(result.succeeded)
        self.assertIsInstance(result.error, NotImplementedError)

    def test_clean_returns_not_implemented(self):
        adapter = _FakeAdapter()
        result = CleanCurvesAction(adapter).execute(
            CleanCurvesRequest(joints=["j1"])
        )
        self.assertFalse(result.succeeded)
        self.assertIsInstance(result.error, NotImplementedError)


class TestPoseTransformFrozen(unittest.TestCase):
    def test_immutable(self):
        pt = PoseTransform(translation=(1, 2, 3), rotation=(4, 5, 6))
        with self.assertRaises(AttributeError):
            pt.translation = (0, 0, 0)


if __name__ == "__main__":
    unittest.main()
