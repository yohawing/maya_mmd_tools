"""Focused tests for VMD runtime bake cache and apply helpers.

Scene-heavy runtime bake tests remain in test_vmd_converter.py. This module
keeps the cache/apply infrastructure tests small and isolated.
"""

import ctypes
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import maya.api.OpenMaya as om

from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.converters.vmd_runtime_cache_collect import collect_runtime_bake_cache
from mmd_tools.converters.vmd_runtime_scene_apply import apply_runtime_channel_arrays_to_scene_with_undo_disabled


class TestVmdRuntimeBakeInfrastructure(unittest.TestCase):
    """Runtime bake infrastructure tests that do not edit a Maya scene."""

    def setUp(self):
        self.converter = VmdConverter()

    def test_iter_runtime_bake_frames_returns_every_frame(self):
        """_iter_runtime_bake_frames returns all output-frame samples."""
        self.assertEqual(self.converter._iter_runtime_bake_frames(0, 5), [0, 1, 2, 3, 4, 5])
        self.assertEqual(self.converter._iter_runtime_bake_frames(10, 10), [10])
        self.assertEqual(self.converter._iter_runtime_bake_frames(5, 3), [])
        self.converter.fps = 60.0
        self.assertEqual(self.converter._iter_runtime_bake_frames(0, 2), [0.0, 0.5, 1.0, 1.5, 2.0])
        self.assertEqual(
            self.converter._iter_runtime_bake_frame_samples(0, 2),
            [(0.0, 0.0), (1.0, 0.5), (2.0, 1.0), (3.0, 1.5), (4.0, 2.0)],
        )

    def test_runtime_batch_buffer_helpers_unpack_flat_frame_data(self):
        """Batch ABI flat buffers can be sliced for a requested frame."""
        class BatchResult:
            frame_count = 2
            bone_count = 2
            morph_count = 3
            world_matrices = (ctypes.c_float * 64)(*range(64))
            morph_weights = (ctypes.c_float * 6)(0.0, 0.1, 0.2, 0.3, 0.4, 0.5)

        matrices = self.converter._runtime_batch_world_matrices_for_frame(BatchResult, 1)
        morphs = self.converter._runtime_batch_morph_weights_for_frame(BatchResult, 1)

        self.assertEqual(len(matrices), 2)
        self.assertEqual(matrices[0], [float(value) for value in range(32, 48)])
        self.assertEqual(matrices[1], [float(value) for value in range(48, 64)])
        for actual, expected in zip(morphs, [0.3, 0.4, 0.5], strict=True):
            self.assertAlmostEqual(actual, expected, places=6)

    def test_collect_runtime_bake_cache_uses_batch_eval_and_restores_state(self):
        """The extracted collector stores batch results and restores state."""
        appended = []
        refresh_calls = []

        class BatchResult:
            frame_count = 2
            bone_count = 0
            morph_count = 2

        converter = SimpleNamespace(
            anim_layer="runtime_layer",
            bone_index_to_joint={},
            logger=SimpleNamespace(info=lambda *_args, **_kwargs: None),
            _create_runtime_joint_channel_arrays=lambda: {},
            _create_runtime_joint_channel_static_state=lambda: {},
            _compute_native_local_channel_batch=lambda _batch_result: None,
            _runtime_batch_morph_weights_for_frame=lambda _batch_result, frame_index: [frame_index, frame_index + 0.5],
            _append_bone_locals_to_channel_arrays=lambda bone_locals, values, static: appended.append(
                (bone_locals, values, static)
            ),
        )
        instance = SimpleNamespace(
            evaluate_clip_frame_batch=lambda clip, start, step, count, worker_count=0: BatchResult()
        )

        def fake_refresh(*_args, **kwargs):
            refresh_calls.append(kwargs.get("suspend"))

        with patch("mmd_tools.converters.vmd_runtime_cache_collect.cmds.refresh", side_effect=fake_refresh):
            cache = collect_runtime_bake_cache(converter, instance, clip=object(), bake_samples=[(1.0, 0.0), (2.0, 1.0)])

        self.assertTrue(cache.batch_mode)
        self.assertEqual(cache.baked_frames, [1.0, 2.0])
        self.assertEqual(cache.morph_cache, [(1.0, [0, 0.5]), (2.0, [1, 1.5])])
        self.assertEqual(len(cache.bake_times), 2)
        self.assertEqual(appended, [({}, {}, {}), ({}, {}, {})])
        self.assertEqual(refresh_calls, [True, False])
        self.assertEqual(converter.anim_layer, "runtime_layer")

    def test_collect_runtime_bake_cache_keeps_outer_refresh_suspend_active(self):
        """Collector does not resume refresh when convert already suspended it."""
        converter = SimpleNamespace(
            anim_layer="runtime_layer",
            _vmd_import_refresh_suspended=True,
            bone_index_to_joint={},
            logger=SimpleNamespace(info=lambda *_args, **_kwargs: None),
            _create_runtime_joint_channel_arrays=lambda: {},
            _create_runtime_joint_channel_static_state=lambda: {},
        )
        instance = SimpleNamespace(evaluate_clip_frame_batch=lambda *_args, **_kwargs: None)

        with patch("mmd_tools.converters.vmd_runtime_cache_collect.cmds.refresh") as refresh:
            cache = collect_runtime_bake_cache(converter, instance, clip=object(), bake_samples=[])

        refresh.assert_not_called()
        self.assertEqual(cache.baked_frames, [])
        self.assertEqual(converter.anim_layer, "runtime_layer")

    def test_runtime_scene_apply_suspends_refresh_when_called_standalone(self):
        """Standalone runtime apply suppresses undo and refresh, then restores them."""
        applied = []
        undo_calls = []
        refresh_calls = []
        converter = SimpleNamespace(
            _vmd_import_refresh_suspended=False,
            _apply_runtime_channel_arrays_to_scene=lambda *_args: applied.append(True),
        )

        def fake_undo_info(*_args, **kwargs):
            undo_calls.append(kwargs)
            if kwargs.get("q") and kwargs.get("state"):
                return True
            return None

        def fake_refresh(*_args, **kwargs):
            refresh_calls.append(kwargs.get("suspend"))

        with patch("mmd_tools.converters.vmd_runtime_scene_apply.cmds.undoInfo", side_effect=fake_undo_info), patch(
            "mmd_tools.converters.vmd_runtime_scene_apply.cmds.refresh",
            side_effect=fake_refresh,
        ):
            apply_runtime_channel_arrays_to_scene_with_undo_disabled(
                converter,
                {},
                {},
                om.MTimeArray(),
                [],
                [],
                [],
            )

        self.assertEqual(applied, [True])
        self.assertEqual(refresh_calls, [True, False])
        self.assertIn({"stateWithoutFlush": False}, undo_calls)
        self.assertIn({"stateWithoutFlush": True}, undo_calls)

    def test_runtime_scene_apply_keeps_outer_refresh_suspend_active(self):
        """Runtime apply does not resume refresh when convert already suspended it."""
        converter = SimpleNamespace(
            _vmd_import_refresh_suspended=True,
            _apply_runtime_channel_arrays_to_scene=lambda *_args: None,
        )

        with patch("mmd_tools.converters.vmd_runtime_scene_apply.cmds.undoInfo", return_value=False), patch(
            "mmd_tools.converters.vmd_runtime_scene_apply.cmds.refresh"
        ) as refresh:
            apply_runtime_channel_arrays_to_scene_with_undo_disabled(
                converter,
                {},
                {},
                om.MTimeArray(),
                [],
                [],
                [],
            )

        refresh.assert_not_called()

    def test_collect_runtime_bake_cache_falls_back_to_per_frame_eval(self):
        """When batch is unavailable, per-frame ABI stores successful samples."""
        appended = []
        evaluated = []

        converter = SimpleNamespace(
            anim_layer="runtime_layer",
            bone_index_to_joint={},
            logger=SimpleNamespace(info=lambda *_args, **_kwargs: None),
            _create_runtime_joint_channel_arrays=lambda: {},
            _create_runtime_joint_channel_static_state=lambda: {},
            _append_bone_locals_to_channel_arrays=lambda bone_locals, values, static: appended.append(
                (bone_locals, values, static)
            ),
        )

        def evaluate_clip_frame(_clip, frame):
            evaluated.append(frame)
            return frame != 1.0

        instance = SimpleNamespace(
            evaluate_clip_frame_batch=lambda *_args, **_kwargs: None,
            evaluate_clip_frame=evaluate_clip_frame,
            get_world_matrices=lambda: [],
            get_morph_weights=lambda: [0.25, 0.75],
        )

        with patch("mmd_tools.converters.vmd_runtime_cache_collect.cmds.refresh"):
            cache = collect_runtime_bake_cache(
                converter,
                instance,
                clip=object(),
                bake_samples=[(10.0, 0.0), (11.0, 1.0), (12.0, 2.0)],
            )

        self.assertFalse(cache.batch_mode)
        self.assertEqual(evaluated, [0.0, 1.0, 2.0])
        self.assertEqual(cache.baked_frames, [10.0, 12.0])
        self.assertEqual(cache.morph_cache, [(10.0, [0.25, 0.75]), (12.0, [0.25, 0.75])])
        self.assertEqual(appended, [({}, {}, {}), ({}, {}, {})])
        self.assertEqual(converter.anim_layer, "runtime_layer")


if __name__ == "__main__":
    unittest.main()
