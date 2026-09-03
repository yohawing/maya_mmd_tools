"""VMD runtime bake execution path tests."""

import ctypes
import struct
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import mmd_tools.converters.vmd_converter as vmd_converter_module
from mmd_tools.converters.vmd_converter import VmdConverter
from tests.common.maya_test_base import MayaTestBase


def _single_bone_vmd_bytes(position=(2.0, 4.0, 6.0)):
    return (
        b"\0" * 50
        + struct.pack("<I", 1)
        + b"\0" * 15
        + struct.pack("<I", 0)
        + struct.pack("<fff", *position)
        + b"\0" * (16 + 64)
        + struct.pack("<I", 0)
    )


class TestVmdRuntimeBakeExecution(MayaTestBase):
    """Runtime bake batch/fallback execution behavior."""

    def setUp(self):
        super().setUp()
        self.converter = VmdConverter()

    def test_runtime_clip_scaler_changes_only_bone_translation(self):
        source = _single_bone_vmd_bytes()
        scaled = vmd_converter_module.scale_vmd_bone_translation_bytes(source, 0.5)

        self.assertEqual(struct.unpack_from("<fff", scaled, 73), (1.0, 2.0, 3.0))
        self.assertEqual(scaled[:73], source[:73])
        self.assertEqual(scaled[85:], source[85:])

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
        logger_mock = MagicMock()
        self.converter.logger = logger_mock
        profile = {}
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
                profile=profile,
            )

        self.assertTrue(result)
        self.assertEqual(FakeInstance.last.batch_calls, [(0.0, 1.0, 3, 0)])
        self.assertEqual(FakeInstance.last.per_frame_calls, [])
        registration = profile["vmd_converter"]["runtime_registration"]
        self.assertEqual(registration["registration_mode"], "model_paired_registered")
        self.assertEqual(registration["status"], "success")
        self.assertEqual(registration["fallback"], "none")
        self.assertEqual(registration["evaluation_mode"], "batch")
        self.assertEqual(registration["frame_count"], 3)

        # Internal runtime bake detail stays on DEBUG; completion summary stays on INFO.
        detail_debug_prefixes = (
            "Runtime evaluation range:",
            "mmd-anim runtime pose evaluation and cache completed",
            "runtime bake cache timings:",
            "Runtime cache key application completed",
            "runtime bake total elapsed=",
            "runtime joint channel pruning:",
        )
        debug_msgs = [call[0][0] for call in logger_mock.debug.call_args_list if call[0]]
        info_msgs = [call[0][0] for call in logger_mock.info.call_args_list if call[0]]
        for prefix in detail_debug_prefixes:
            self.assertTrue(
                any(isinstance(msg, str) and msg.startswith(prefix) for msg in debug_msgs),
                "expected DEBUG log starting with %r, got %r" % (prefix, debug_msgs),
            )
            self.assertFalse(
                any(isinstance(msg, str) and msg.startswith(prefix) for msg in info_msgs),
                "runtime detail %r must not be INFO" % (prefix,),
            )
        self.assertTrue(
            any(
                isinstance(msg, str) and msg.startswith("Applied runtime cache: keyed ")
                for msg in info_msgs
            ),
            "expected INFO Applied runtime cache summary, got %r" % (info_msgs,),
        )

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

        def capture_apply(
            _context,
            _joint_values,
            _joint_static,
            _bake_times,
            baked_frames,
            morph_cache,
            _pmx_morph_names,
        ):
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
            vmd_converter_module,
            "apply_runtime_channel_arrays_to_scene_with_undo_disabled",
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
        # undo is handled inside the apply helper; with the helper mocked it is not invoked here.
        self.assertEqual(undo_calls, [])

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

        def capture_apply(
            _context,
            _joint_values,
            _joint_static,
            _bake_times,
            baked_frames,
            _morph_cache,
            _pmx_morph_names,
        ):
            apply_calls.append(list(baked_frames))

        self.converter.bone_index_to_joint = {}
        self.converter.bone_name_to_index = {}
        with patch.object(vmd_converter_module, "MmdRuntimeModel", FakeModel), patch.object(
            vmd_converter_module,
            "MmdRuntimeClip",
            FakeClip,
        ), patch.object(vmd_converter_module, "MmdRuntimeInstance", FakeInstance), patch.object(
            vmd_converter_module,
            "apply_runtime_channel_arrays_to_scene_with_undo_disabled",
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

        def capture_apply(
            _context,
            _joint_values,
            _joint_static,
            _bake_times,
            baked_frames,
            _morph_cache,
            _pmx_morph_names,
        ):
            apply_calls.append(list(baked_frames))

        self.converter.fps = 60.0
        self.converter.bone_index_to_joint = {}
        self.converter.bone_name_to_index = {}
        with patch.object(vmd_converter_module, "MmdRuntimeModel", FakeModel), patch.object(
            vmd_converter_module,
            "MmdRuntimeClip",
            FakeClip,
        ), patch.object(vmd_converter_module, "MmdRuntimeInstance", FakeInstance), patch.object(
            vmd_converter_module,
            "apply_runtime_channel_arrays_to_scene_with_undo_disabled",
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
                vmd_bytes=b"\0" * 50 + struct.pack("<I", 0),
                pmx_bytes=b"pmx",
                pmx_path="",
            )

        self.assertTrue(result)
        build_bind_maps.assert_called_once_with()
        direct_bake.assert_not_called()
        collect_cache.assert_called_once()
        self.assertEqual(collect_cache.call_args.args[3], [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)])
        self.assertFalse(collect_cache.call_args.kwargs.get("use_native_physics_bake", False))
        apply_cache.assert_called_once()
        self.assertEqual(
            apply_cache.call_args.args[1:],
            (
                runtime_cache.joint_channel_values,
                runtime_cache.joint_channel_static,
                runtime_cache.bake_times,
                runtime_cache.baked_frames,
                runtime_cache.morph_cache,
                [],
            ),
        )

    def test_native_physics_bake_opt_in_dispatches_with_explicit_dt_at_60fps(self):
        """use_native_physics_bake=True は frame_step=0.5 と dt=1/60 で physics bake を呼ぶ。"""

        class Frame:
            frame_number = 2

        class VmdDataLike:
            bone_frames = [Frame()]
            morph_frames = []
            camera_frames = []
            light_frames = []

        class FakeModel:
            registration = None
            evaluation = None

            def __init__(self, source):
                self.source = source
                self.free_calls = 0

            @classmethod
            def from_pmx_bytes(cls, _pmx_bytes):
                cls.registration = cls("pmx")
                return cls.registration

            @classmethod
            def from_descriptors(cls, descriptors):
                cls.evaluation = cls(descriptors)
                return cls.evaluation

            def free(self):
                self.free_calls += 1

        class FakeClip:
            registration_model = None
            runtime_bytes = None
            last = None

            def __init__(self):
                self.free_calls = 0

            @classmethod
            def from_vmd_bytes_for_model(cls, model, vmd_bytes):
                cls.registration_model = model
                cls.runtime_bytes = vmd_bytes
                cls.last = cls()
                return cls.last

            def free(self):
                self.free_calls += 1

        class BatchResult:
            frame_count = 5
            bone_count = 0
            morph_count = 0
            world_matrices = (ctypes.c_float * 0)()
            morph_weights = (ctypes.c_float * 0)()

        class FakeInstance:
            last = None

            def __init__(self, model):
                self.model = model
                self.batch_calls = []
                self.free_calls = 0

            @classmethod
            def for_model(cls, model):
                cls.last = cls(model)
                return cls.last

            def evaluate_clip_frame_batch(self, *_args, **_kwargs):
                self.batch_calls.append((_args, _kwargs))
                raise AssertionError("non-physics batch must not run when physics bake succeeds")

            def free(self):
                self.free_calls += 1

        class FakePhysicsWorld:
            last = None
            free_calls = 0

            def __init__(self):
                self.bake_calls = []

            @classmethod
            def from_pmx_bytes(cls, _pmx_bytes):
                raise AssertionError("scaled Maya targets must not use raw PMX physics")

            @classmethod
            def from_descriptors(cls, rigid_bodies, joints):
                self = cls()
                self.descriptors = (rigid_bodies, joints)
                cls.last = self
                return self

            def bake_clip_frames_with_physics(
                self,
                instance,
                clip,
                start_frame,
                frame_step,
                frame_count,
                dt_seconds,
                *,
                prepare=True,
            ):
                self.bake_calls.append(
                    {
                        "instance": instance,
                        "clip": clip,
                        "start_frame": start_frame,
                        "frame_step": frame_step,
                        "frame_count": frame_count,
                        "dt_seconds": dt_seconds,
                        "prepare": prepare,
                    }
                )
                return BatchResult()

            def free(self):
                type(self).free_calls += 1

        apply_calls = []

        def capture_apply(
            _context,
            _joint_values,
            _joint_static,
            _bake_times,
            baked_frames,
            _morph_cache,
            _pmx_morph_names,
        ):
            apply_calls.append(list(baked_frames))

        self.converter.fps = 60.0
        self.converter.motion_scale = 1.5
        self.converter.bone_index_to_joint = {}
        self.converter.bone_name_to_index = {}
        profile = {}
        model_descriptors = SimpleNamespace(bones=["scaled-bone"])
        physics_descriptors = SimpleNamespace(
            rigid_bodies=["scaled-rigid"],
            joints=["scaled-joint"],
            validation_errors=[],
        )
        vmd_bytes = _single_bone_vmd_bytes()
        with patch.object(vmd_converter_module, "MmdRuntimeModel", FakeModel), patch.object(
            vmd_converter_module,
            "MmdRuntimeClip",
            FakeClip,
        ), patch.object(vmd_converter_module, "MmdRuntimeInstance", FakeInstance), patch.object(
            vmd_converter_module,
            "MmdRuntimePhysicsWorld",
            FakePhysicsWorld,
        ), patch.object(
            vmd_converter_module,
            "is_native_physics_available",
            return_value=True,
        ), patch.object(
            vmd_converter_module,
            "get_runtime_feature_flags",
            return_value=0x3,
        ), patch.object(
            vmd_converter_module,
            "HAS_MMD_RUNTIME",
            True,
        ), patch.object(
            vmd_converter_module,
            "apply_runtime_channel_arrays_to_scene_with_undo_disabled",
            side_effect=capture_apply,
        ), patch(
            "mmd_tools.core.model_dag_descriptor.build_model_descriptors_from_dag",
            return_value=model_descriptors,
        ) as build_model_descriptors, patch(
            "mmd_tools.core.physics_dag_descriptor.build_descriptors_from_dag",
            return_value=physics_descriptors,
        ) as build_physics_descriptors, patch(
            "mmd_tools.core.physics_solver._collect_bone_joints",
            return_value=["|joint0", "|joint1"],
        ), patch.object(
            vmd_converter_module,
            "store_runtime_registration_provenance",
            return_value=True,
        ):
            result = self.converter._convert_using_mmd_runtime(
                VmdDataLike(),
                vmd_bytes=vmd_bytes,
                pmx_bytes=b"pmx-bytes",
                pmx_path="",
                use_native_physics_bake=True,
                profile=profile,
                target_model="|scaledRoot",
            )

        self.assertTrue(result)
        self.assertIs(FakeClip.registration_model, FakeModel.registration)
        self.assertEqual(
            struct.unpack_from("<fff", FakeClip.runtime_bytes, 73),
            (3.0, 6.0, 9.0),
        )
        self.assertIs(FakeInstance.last.model, FakeModel.evaluation)
        self.assertIs(FakeModel.evaluation.source, model_descriptors)
        build_model_descriptors.assert_called_once_with("|scaledRoot")
        build_physics_descriptors.assert_called_once_with(
            "|scaledRoot", bone_joints=["|joint0", "|joint1"], bone_count=2
        )
        self.assertEqual(
            FakePhysicsWorld.last.descriptors,
            (physics_descriptors.rigid_bodies, physics_descriptors.joints),
        )
        self.assertEqual(len(FakePhysicsWorld.last.bake_calls), 1)
        bake_call = FakePhysicsWorld.last.bake_calls[0]
        self.assertEqual(bake_call["start_frame"], 0.0)
        self.assertAlmostEqual(bake_call["frame_step"], 0.5)
        self.assertEqual(bake_call["frame_count"], 5)
        self.assertAlmostEqual(bake_call["dt_seconds"], 1.0 / 60.0)
        self.assertEqual(FakeInstance.last.batch_calls, [])
        self.assertEqual(apply_calls[0], [0.0, 1.0, 2.0, 3.0, 4.0])
        self.assertEqual(FakePhysicsWorld.free_calls, 1)
        self.assertEqual(FakeInstance.last.free_calls, 1)
        self.assertEqual(FakeClip.last.free_calls, 1)
        self.assertEqual(FakeModel.evaluation.free_calls, 1)
        self.assertEqual(FakeModel.registration.free_calls, 1)
        routing = profile["vmd_converter"]["native_physics_bake"]
        self.assertTrue(routing["requested"])
        self.assertTrue(routing["used"])
        self.assertAlmostEqual(routing["dt_seconds"], 1.0 / 60.0)
        self.assertAlmostEqual(routing["frame_step"], 0.5)

    def test_native_physics_bake_default_stays_on_non_physics_batch(self):
        """use_native_physics_bake 未指定時は既存 evaluate_clip_frame_batch を使う。"""

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

            @classmethod
            def for_model(cls, _model):
                cls.last = cls()
                return cls.last

            def evaluate_clip_frame_batch(self, _clip, start_frame, frame_step, frame_count, *, worker_count=0):
                self.batch_calls.append((start_frame, frame_step, frame_count, worker_count))
                return BatchResult

            def free(self):
                pass

        class FakePhysicsWorld:
            created = 0

            @classmethod
            def from_pmx_bytes(cls, _pmx_bytes):
                cls.created += 1
                raise AssertionError("physics world must not be created when opt-in is off")

        self.converter.bone_index_to_joint = {}
        self.converter.bone_name_to_index = {}
        with patch.object(vmd_converter_module, "MmdRuntimeModel", FakeModel), patch.object(
            vmd_converter_module,
            "MmdRuntimeClip",
            FakeClip,
        ), patch.object(vmd_converter_module, "MmdRuntimeInstance", FakeInstance), patch.object(
            vmd_converter_module,
            "MmdRuntimePhysicsWorld",
            FakePhysicsWorld,
        ), patch.object(
            vmd_converter_module,
            "is_native_physics_available",
            return_value=True,
        ):
            result = self.converter._convert_using_mmd_runtime(
                VmdDataLike(),
                vmd_bytes=b"vmd",
                pmx_bytes=b"pmx",
                pmx_path="",
            )

        self.assertTrue(result)
        self.assertEqual(FakeInstance.last.batch_calls, [(0.0, 1.0, 3, 0)])
        self.assertEqual(FakePhysicsWorld.created, 0)

    def test_native_physics_bake_failure_falls_back_to_runtime_batch(self):
        """physics bake が None を返したら既存 runtime batch へ fallback する。"""

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

            @classmethod
            def for_model(cls, _model):
                cls.last = cls()
                return cls.last

            def evaluate_clip_frame_batch(self, _clip, start_frame, frame_step, frame_count, *, worker_count=0):
                self.batch_calls.append((start_frame, frame_step, frame_count, worker_count))
                return BatchResult

            def free(self):
                pass

        class FakePhysicsWorld:
            last = None
            free_calls = 0

            def __init__(self):
                self.bake_calls = []

            @classmethod
            def from_pmx_bytes(cls, _pmx_bytes):
                cls.last = cls()
                return cls.last

            def bake_clip_frames_with_physics(self, *args, **kwargs):
                self.bake_calls.append((args, kwargs))
                return None

            def free(self):
                type(self).free_calls += 1

        self.converter.bone_index_to_joint = {}
        self.converter.bone_name_to_index = {}
        profile = {}
        with patch.object(vmd_converter_module, "MmdRuntimeModel", FakeModel), patch.object(
            vmd_converter_module,
            "MmdRuntimeClip",
            FakeClip,
        ), patch.object(vmd_converter_module, "MmdRuntimeInstance", FakeInstance), patch.object(
            vmd_converter_module,
            "MmdRuntimePhysicsWorld",
            FakePhysicsWorld,
        ), patch.object(
            vmd_converter_module,
            "is_native_physics_available",
            return_value=True,
        ), patch.object(
            vmd_converter_module,
            "get_runtime_feature_flags",
            return_value=0x3,
        ), patch.object(
            vmd_converter_module,
            "HAS_MMD_RUNTIME",
            True,
        ):
            result = self.converter._convert_using_mmd_runtime(
                VmdDataLike(),
                vmd_bytes=b"vmd",
                pmx_bytes=b"pmx",
                pmx_path="",
                use_native_physics_bake=True,
                profile=profile,
            )

        self.assertTrue(result)
        self.assertEqual(len(FakePhysicsWorld.last.bake_calls), 1)
        self.assertEqual(FakeInstance.last.batch_calls, [(0.0, 1.0, 3, 0)])
        self.assertEqual(FakePhysicsWorld.free_calls, 1)
        routing = profile["vmd_converter"]["native_physics_bake"]
        self.assertTrue(routing["requested"])
        self.assertFalse(routing["used"])
        self.assertEqual(routing["reason"], "physics_bake_failed_or_unsupported")

    def test_native_physics_bake_reuses_channel_application_helper(self):
        """physics bake 成功時も apply_runtime_channel_arrays 経路を再利用する。"""

        class Frame:
            frame_number = 1

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

            def free(self):
                pass

        class FakePhysicsWorld:
            @classmethod
            def from_pmx_bytes(cls, _pmx_bytes):
                return cls()

            def free(self):
                pass

        runtime_cache = SimpleNamespace(
            baked_frames=[0.0, 1.0],
            bake_times=object(),
            joint_channel_values={"j": {}},
            joint_channel_static={"j": {}},
            morph_cache=[(0.0, [])],
            batch_mode=True,
            eval_elapsed=0.01,
            eval_copy_elapsed=0.0,
            batch_unpack_elapsed=0.0,
            local_elapsed=0.0,
            append_elapsed=0.0,
            physics_bake={"requested": True, "used": True, "reason": "ok", "dt_seconds": 1.0 / 30.0},
        )

        self.converter.bone_index_to_joint = {0: "j"}
        self.converter.bone_name_to_index = {"センター": 0}
        with patch.object(vmd_converter_module, "MmdRuntimeModel", FakeModel), patch.object(
            vmd_converter_module,
            "MmdRuntimeClip",
            FakeClip,
        ), patch.object(vmd_converter_module, "MmdRuntimeInstance", FakeInstance), patch.object(
            vmd_converter_module,
            "MmdRuntimePhysicsWorld",
            FakePhysicsWorld,
        ), patch.object(
            vmd_converter_module,
            "is_native_physics_available",
            return_value=True,
        ), patch.object(
            vmd_converter_module,
            "HAS_MMD_RUNTIME",
            True,
        ), patch.object(
            self.converter,
            "_disable_mmd_rig_constraints_for_runtime_bake",
        ), patch.object(
            self.converter,
            "_restore_joints_to_bind_pose_for_runtime_bake",
        ), patch.object(
            self.converter,
            "_build_runtime_bind_world_maps",
        ), patch.object(
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
                use_native_physics_bake=True,
            )

        self.assertTrue(result)
        collect_cache.assert_called_once()
        self.assertTrue(collect_cache.call_args.kwargs["use_native_physics_bake"])
        self.assertIsNotNone(collect_cache.call_args.kwargs["physics_world"])
        apply_cache.assert_called_once()
        self.assertEqual(apply_cache.call_args.args[1], runtime_cache.joint_channel_values)
        self.assertEqual(apply_cache.call_args.args[4], runtime_cache.baked_frames)
