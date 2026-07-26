"""Unit tests for pose manipulation actions."""

from __future__ import annotations

import unittest

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
        self._transforms: dict[str, tuple[list, list]] = {}
        self._undo_chunks: list[str] = []
        self._undo_closed: int = 0

    def set_joint(self, name: str, t=(0, 0, 0), r=(0, 0, 0)):
        self._transforms[name] = (list(t), list(r))

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

    def ls(self, **kwargs):
        return []


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
        pose = {"j1": PoseTransform(translation=(0, 0, 0), rotation=(0, 0, 0))}
        PastePoseAction(adapter).execute(PastePoseRequest(pose=pose))
        self.assertEqual(len(adapter._undo_chunks), 1)
        self.assertEqual(adapter._undo_chunks[0], "Paste Pose")
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
