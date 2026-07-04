"""VMD runtime bake execution path tests."""

import ctypes
from types import SimpleNamespace
from unittest.mock import patch

import mmd_tools.converters.vmd_converter as vmd_converter_module
from mmd_tools.converters.vmd_converter import VmdConverter
from tests.common.maya_test_base import MayaTestBase


class TestVmdRuntimeBakeExecution(MayaTestBase):
    """Runtime bake batch/fallback execution behavior."""

    def setUp(self):
        super().setUp()
        self.converter = VmdConverter()

    def test_runtime_bake_uses_batch_evaluation_when_available(self):
        """batch ABI がある runtime では per-frame 評価へ落ちずに cache を構築する。"""
        class Frame:
            frame_number = 2

        class VmdDataLike:
            bone_frames = [Frame()]
            morph_frames = []
            camera_frames = []
            light_frames = []

        class FakeModel:
            @classmethod
            def from_pmx_bytes(cls, _pmx_bytes):
                return cls()

            def free(self):
                pass

        class FakeClip:
            @classmethod
            def from_vmd_bytes_for_model(cls, _model, _vmd_bytes):
                return cls()

            def free(self):
                pass

        class BatchResult:
            frame_count = 3
            bone_count = 0
            morph_count = 0
            world_matrices = (ctypes.c_float * 0)()
            morph_weights = (ctypes.c_float * 0)()

        class FakeInstance:
            last = None

            def __init__(self):
                self.batch_calls = []
                self.per_frame_calls = []

            @classmethod
            def for_model(cls, _model):
                cls.last = cls()
                return cls.last

            def evaluate_clip_frame_batch(self, _clip, start_frame, frame_step, frame_count, *, worker_count=0):
                self.batch_calls.append((start_frame, frame_step, frame_count, worker_count))
                return BatchResult

            def evaluate_clip_frame(self, _clip, frame):
                self.per_frame_calls.append(frame)
                return False

            def free(self):
                pass

        self.converter.bone_index_to_joint = {}
        self.converter.bone_name_to_index = {}
        with patch.object(vmd_converter_module, "MmdRuntimeModel", FakeModel), patch.object(
            vmd_converter_module,
            "MmdRuntimeClip",
            FakeClip,
        ), patch.object(vmd_converter_module, "MmdRuntimeInstance", FakeInstance):
            result = self.converter._convert_using_mmd_runtime(
                VmdDataLike(),
                vmd_bytes=b"vmd",
                pmx_bytes=b"pmx",
                pmx_path="",
            )

        self.assertTrue(result)
        self.assertEqual(FakeInstance.last.batch_calls, [(0.0, 1.0, 3, 0)])
        self.assertEqual(FakeInstance.last.per_frame_calls, [])

    def test_runtime_bake_fps_60_batch_samples_target_maya_frames(self):
        """60fps runtime bake は Maya output frame ごとに 0.5 VMD frame step で評価する。"""
        class Frame:
            frame_number = 100

        class VmdDataLike:
            bone_frames = [Frame()]
            morph_frames = []
            camera_frames = []
            light_frames = []

        class FakeModel:
            @classmethod
            def from_pmx_bytes(cls, _pmx_bytes):
                return cls()

            def free(self):
                pass

        class FakeClip:
            @classmethod
            def from_vmd_bytes_for_model(cls, _model, _vmd_bytes):
                return cls()

            def free(self):
                pass

        class BatchResult:
            bone_count = 0
            morph_count = 0
            world_matrices = (ctypes.c_float * 0)()
            morph_weights = (ctypes.c_float * 0)()

            def __init__(self, frame_count):
                self.frame_count = frame_count

        class FakeInstance:
            last = None

            def __init__(self):
                self.batch_calls = []

            @classmethod
            def for_model(cls, _model):
                cls.last = cls()
                return cls.last

            def evaluate_clip_frame_batch(self, _clip, start_frame, frame_step, frame_count, *, worker_count=0):
                self.batch_calls.append((start_frame, frame_step, frame_count, worker_count))
                return BatchResult(frame_count)

            def free(self):
                pass

        apply_calls = []

        def capture_apply(_joint_values, _joint_static, _bake_times, baked_frames, morph_cache, _pmx_morph_names):
            apply_calls.append((list(baked_frames), list(morph_cache)))

        undo_calls = []

        def fake_undo_info(**kwargs):
            undo_calls.append(kwargs)
            if kwargs == {"q": True, "state": True}:
                return True
            return None

        self.converter.fps = 60.0
        self.converter.bone_index_to_joint = {}
        self.converter.bone_name_to_index = {}
        with patch.object(vmd_converter_module, "MmdRuntimeModel", FakeModel), patch.object(
            vmd_converter_module,
            "MmdRuntimeClip",
            FakeClip,
        ), patch.object(vmd_converter_module, "MmdRuntimeInstance", FakeInstance), patch.object(
            self.converter,
            "_apply_runtime_channel_arrays_to_scene",
            side_effect=capture_apply,
        ), patch.object(
            vmd_converter_module.cmds,
            "undoInfo",
            side_effect=fake_undo_info,
        ):
            result = self.converter._convert_using_mmd_runtime(
                VmdDataLike(),
                vmd_bytes=b"vmd",
                pmx_bytes=b"pmx",
                pmx_path="",
            )

        self.assertTrue(result)
        self.assertEqual(FakeInstance.last.batch_calls, [(0.0, 0.5, 201, 0)])
        self.assertEqual(apply_calls[0][0][0], 0.0)
        self.assertEqual(apply_calls[0][0][-1], 200.0)
        self.assertEqual(len(apply_calls[0][0]), 201)
        self.assertEqual(
            undo_calls,
            [
                {"q": True, "state": True},
                {"stateWithoutFlush": False},
                {"stateWithoutFlush": True},
            ],
        )

    def test_runtime_bake_uses_clip_frame_range_when_python_vmd_is_empty(self):
        """Python VMD parser が空でも runtime clip の frame range で bake 範囲を決める。"""
        class VmdDataLike:
            bone_frames = []
            morph_frames = []
            camera_frames = []
            light_frames = []

        class FakeModel:
            @classmethod
            def from_pmx_bytes(cls, _pmx_bytes):
                return cls()

            def free(self):
                pass

        class FakeClip:
            @classmethod
            def from_vmd_bytes_for_model(cls, _model, _vmd_bytes):
                return cls()

            def frame_range(self):
                return (2, 4)

            def free(self):
                pass

        class BatchResult:
            bone_count = 0
            morph_count = 0
            world_matrices = (ctypes.c_float * 0)()
            morph_weights = (ctypes.c_float * 0)()

            def __init__(self, frame_count):
                self.frame_count = frame_count

        class FakeInstance:
            last = None

            def __init__(self):
                self.batch_calls = []

            @classmethod
            def for_model(cls, _model):
                cls.last = cls()
                return cls.last

            def evaluate_clip_frame_batch(self, _clip, start_frame, frame_step, frame_count, *, worker_count=0):
                self.batch_calls.append((start_frame, frame_step, frame_count, worker_count))
                return BatchResult(frame_count)

            def free(self):
                pass

        apply_calls = []

        def capture_apply(_joint_values, _joint_static, _bake_times, baked_frames, _morph_cache, _pmx_morph_names):
            apply_calls.append(list(baked_frames))

        self.converter.bone_index_to_joint = {}
        self.converter.bone_name_to_index = {}
        with patch.object(vmd_converter_module, "MmdRuntimeModel", FakeModel), patch.object(
            vmd_converter_module,
            "MmdRuntimeClip",
            FakeClip,
        ), patch.object(vmd_converter_module, "MmdRuntimeInstance", FakeInstance), patch.object(
            self.converter,
            "_apply_runtime_channel_arrays_to_scene",
            side_effect=capture_apply,
        ):
            result = self.converter._convert_using_mmd_runtime(
                VmdDataLike(),
                vmd_bytes=b"vmd",
                pmx_bytes=b"pmx",
                pmx_path="",
            )

        self.assertTrue(result)
        self.assertEqual(FakeInstance.last.batch_calls, [(2.0, 1.0, 3, 0)])
        self.assertEqual(apply_calls[0], [2.0, 3.0, 4.0])

    def test_runtime_bake_fps_60_fallback_evaluates_fractional_vmd_frames(self):
        """per-frame ABI でも Maya output frame から逆算した fractional VMD frame を評価する。"""
        class Frame:
            frame_number = 2

        class VmdDataLike:
            bone_frames = [Frame()]
            morph_frames = []
            camera_frames = []
            light_frames = []

        class FakeModel:
            @classmethod
            def from_pmx_bytes(cls, _pmx_bytes):
                return cls()

            def free(self):
                pass

        class FakeClip:
            @classmethod
            def from_vmd_bytes_for_model(cls, _model, _vmd_bytes):
                return cls()

            def free(self):
                pass

        class FakeInstance:
            last = None

            def __init__(self):
                self.per_frame_calls = []

            @classmethod
            def for_model(cls, _model):
                cls.last = cls()
                return cls.last

            def evaluate_clip_frame_batch(self, *_args, **_kwargs):
                return None

            def evaluate_clip_frame(self, _clip, frame):
                self.per_frame_calls.append(frame)
                return True

            def get_world_matrices(self):
                return []

            def get_morph_weights(self):
                return []

            def free(self):
                pass

        apply_calls = []

        def capture_apply(_joint_values, _joint_static, _bake_times, baked_frames, _morph_cache, _pmx_morph_names):
            apply_calls.append(list(baked_frames))

        self.converter.fps = 60.0
        self.converter.bone_index_to_joint = {}
        self.converter.bone_name_to_index = {}
        with patch.object(vmd_converter_module, "MmdRuntimeModel", FakeModel), patch.object(
            vmd_converter_module,
            "MmdRuntimeClip",
            FakeClip,
        ), patch.object(vmd_converter_module, "MmdRuntimeInstance", FakeInstance), patch.object(
            self.converter,
            "_apply_runtime_channel_arrays_to_scene",
            side_effect=capture_apply,
        ):
            result = self.converter._convert_using_mmd_runtime(
                VmdDataLike(),
                vmd_bytes=b"vmd",
                pmx_bytes=b"pmx",
                pmx_path="",
            )

        self.assertTrue(result)
        self.assertEqual(FakeInstance.last.per_frame_calls, [0.0, 0.5, 1.0, 1.5, 2.0])
        self.assertEqual(apply_calls[0], [0.0, 1.0, 2.0, 3.0, 4.0])

    def test_runtime_bake_with_joint_map_uses_channel_cache_pipeline(self):
        """通常 PMX runtime bake は joint map があっても direct world bake に入らない。"""
        class Frame:
            frame_number = 2

        class VmdDataLike:
            bone_frames = [Frame()]
            morph_frames = []
            camera_frames = []
            light_frames = []

        class FakeModel:
            @classmethod
            def from_pmx_bytes(cls, _pmx_bytes):
                return cls()

            def free(self):
                pass

        class FakeClip:
            @classmethod
            def from_vmd_bytes_for_model(cls, _model, _vmd_bytes):
                return cls()

            def free(self):
                pass

        class FakeInstance:
            @classmethod
            def for_model(cls, _model):
                return cls()

            def evaluate_clip_frame(self, *_args, **_kwargs):
                raise AssertionError("direct world bake should not evaluate frames here")

            def get_world_matrices(self):
                raise AssertionError("direct world bake should not read world matrices")

            def free(self):
                pass

        runtime_cache = SimpleNamespace(
            baked_frames=[0.0, 1.0, 2.0, 3.0, 4.0],
            bake_times=object(),
            joint_channel_values={"runtime_cache_joint": {}},
            joint_channel_static={"runtime_cache_joint": {}},
            morph_cache=[(0.0, [])],
            batch_mode=True,
            eval_elapsed=0.01,
            eval_copy_elapsed=0.0,
            batch_unpack_elapsed=0.0,
            local_elapsed=0.0,
            append_elapsed=0.0,
        )

        self.converter.motion_scale = 2.0
        self.converter.bone_index_to_joint = {0: "runtime_cache_joint"}
        self.converter.bone_name_to_index = {"センター": 0}
        with patch.object(vmd_converter_module, "MmdRuntimeModel", FakeModel), patch.object(
            vmd_converter_module,
            "MmdRuntimeClip",
            FakeClip,
        ), patch.object(vmd_converter_module, "MmdRuntimeInstance", FakeInstance), patch.object(
            self.converter,
            "_disable_mmd_rig_constraints_for_runtime_bake",
        ), patch.object(
            self.converter,
            "_restore_joints_to_bind_pose_for_runtime_bake",
        ), patch.object(
            self.converter,
            "_build_runtime_bind_world_maps",
        ) as build_bind_maps, patch.object(
            self.converter,
            "_bake_bone_poses_from_world_matrices",
            side_effect=AssertionError("direct world bake helper should not be called"),
        ) as direct_bake, patch.object(
            vmd_converter_module,
            "collect_runtime_bake_cache",
            return_value=runtime_cache,
        ) as collect_cache, patch.object(
            vmd_converter_module,
            "apply_runtime_channel_arrays_to_scene_with_undo_disabled",
        ) as apply_cache:
            result = self.converter._convert_using_mmd_runtime(
                VmdDataLike(),
                vmd_bytes=b"vmd",
                pmx_bytes=b"pmx",
                pmx_path="",
            )

        self.assertTrue(result)
        build_bind_maps.assert_called_once_with()
        direct_bake.assert_not_called()
        collect_cache.assert_called_once()
        self.assertEqual(collect_cache.call_args.args[0], self.converter)
        self.assertEqual(collect_cache.call_args.args[3], [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)])
        apply_cache.assert_called_once_with(
            self.converter,
            runtime_cache.joint_channel_values,
            runtime_cache.joint_channel_static,
            runtime_cache.bake_times,
            runtime_cache.baked_frames,
            runtime_cache.morph_cache,
            [],
        )
