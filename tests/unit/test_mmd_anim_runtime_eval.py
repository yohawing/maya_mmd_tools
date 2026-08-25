"""
mmd-anim runtime ラッパーの評価・マーシャリングロジックを純Python で検証するユニットテスト。

既存の ``test_mmd_anim_runtime.py`` はネイティブ DLL の可用性とフォールバック
(None/False を返す経路) を中心に検証していますが、本ファイルは DLL を必要とせず、
ctypes CDLL を模した *フェイクライブラリ* を直接 wrapper クラスへ注入することで、
以下のような「DLL があるときに走る純Python ロジック」を Maya 非依存で検証します:

- MmdRuntimeInstance.get_world_matrices: flat float 配列を 16 要素ごとの行列に分割
- get_morph_weights / get_ik_enabled: 長さ取得 → コピー → Python list 化
- evaluate_clip_frame / *_with_ik_options: ハンドル/引数のガードと bool 変換
- MmdParsedModel のカウント/ポインタアクセサ/byte-buffer デコード
- _set_sig / _find_library / get_mmd_runtime_library のキャッシュ挙動

フェイクライブラリは ``ctypes`` の配列型 (``c_float * n`` など) を実際に受け取り、
本物の DLL と同じ呼び出し規約 (out-buffer へ書き込み、bool を返す) を再現します。
したがって wrapper 側の ctypes バッファ確保・スライス・デコード処理を本物に近い形で
通すことができます。

関連:
- mmd_tools/core/native/mmd_anim_runtime.py
"""

import ctypes
import os
import unittest
from ctypes import c_float, c_uint8
from unittest import mock

import mmd_tools.core.native as native_pkg
import mmd_tools.core.native.mmd_anim_runtime as rt
import mmd_tools.core.native.mmd_anim_runtime_loader as runtime_loader
import mmd_tools.core.native.mmd_anim_runtime_sampling as runtime_sampling
from mmd_tools.core.native.mmd_anim_runtime import (
    MmdParsedModel,
    MmdRuntimeBatchEvaluation,
    MmdRuntimeClip,
    MmdRuntimeInstance,
    MmdRuntimeModel,
    MmdRuntimePhysicsWorld,
    MmdRuntimeFfiByteBuffer,
    compute_maya_local_channels,
    compute_maya_local_channels_batch,
)
from mmd_tools.core.native.mmd_anim_runtime_types import (
    MmdRuntimeFfiGenericCurveDescriptor,
    MmdRuntimeFfiGenericCurveInfo,
)


# ----------------------------------------------------------------------
# フェイクライブラリ (ctypes CDLL の評価系サブセットを模倣)
# ----------------------------------------------------------------------

class _FakeRuntimeLib:
    """評価/コピー系 FFI 関数を模したフェイク CDLL。

    wrapper は ``getattr(lib, name)`` でシンボルを取得し、out-buffer 付きで呼び出す。
    本物の Rust 側と同じく「len を返す関数」と「out-buffer に書き込み bool を返す
    関数」のペアを再現する。指定したシンボルだけを生やすことで、欠損シンボル時の
    フォールバックも検証できる。
    """

    def __init__(
        self,
        *,
        world_matrices=None,
        morph_weights=None,
        ik_enabled=None,
        batch_world_matrices=None,
        batch_morph_weights=None,
        local_channels=None,
        local_channels_batch=None,
        evaluate_result=True,
        with_ik_options=True,
    ):
        self._world = list(world_matrices) if world_matrices is not None else None
        self._morph = list(morph_weights) if morph_weights is not None else None
        self._ik = list(ik_enabled) if ik_enabled is not None else None
        self._batch_world = (
            list(batch_world_matrices) if batch_world_matrices is not None else None
        )
        self._batch_morph = (
            list(batch_morph_weights) if batch_morph_weights is not None else None
        )
        self._local_channels = list(local_channels) if local_channels is not None else None
        self._local_channels_batch = list(local_channels_batch) if local_channels_batch is not None else None
        self._evaluate_result = evaluate_result
        self._provide_ik_options = with_ik_options
        # 呼び出し記録 (引数検証用)
        self.evaluate_calls = []
        self.ik_option_calls = []
        self.batch_calls = []
        self.model_create_calls = []
        self.clip_create_calls = []
        self.instance_create_calls = []

    # --- 評価 ---
    def mmd_runtime_instance_evaluate_clip_frame(self, instance_handle, clip_handle, frame):
        self.evaluate_calls.append((instance_handle, clip_handle, float(frame.value)))
        return self._evaluate_result

    # with_ik_options は __getattr__ ではなく明示プロパティで制御するため
    # provide フラグが False の場合は AttributeError を起こさせる
    def __getattr__(self, name):
        if name == "mmd_runtime_instance_evaluate_clip_frame_with_ik_options":
            if not self._provide_ik_options:
                raise AttributeError(name)

            def _eval_ik(instance_handle, clip_handle, frame, tol, cap):
                self.ik_option_calls.append(
                    (instance_handle, clip_handle, float(frame.value),
                     float(tol.value), int(cap.value))
                )
                return self._evaluate_result

            return _eval_ik
        raise AttributeError(name)

    # --- world matrices ---
    def mmd_runtime_instance_world_matrix_f32_len(self, handle):
        return 0 if self._world is None else len(self._world)

    def mmd_runtime_instance_copy_world_matrices(self, handle, out_buf, n):
        if self._world is None:
            return False
        for i in range(min(n, len(self._world))):
            out_buf[i] = self._world[i]
        return True

    # --- batch evaluation ---
    def mmd_runtime_instance_clip_frame_batch_world_matrix_f32_len(self, handle, frame_count):
        if self._batch_world is None:
            return 0
        return len(self._batch_world)

    def mmd_runtime_instance_clip_frame_batch_morph_weight_f32_len(self, handle, frame_count):
        if self._batch_morph is None:
            return 0
        return len(self._batch_morph)

    def mmd_runtime_instance_evaluate_clip_frame_batch(
        self,
        instance_handle,
        clip_handle,
        start_frame,
        frame_step,
        frame_count,
        worker_count,
        out_world,
        out_world_len,
        out_morph,
        out_morph_len,
    ):
        self.batch_calls.append(
            (
                instance_handle,
                clip_handle,
                float(start_frame.value),
                float(frame_step.value),
                int(frame_count.value),
                int(worker_count.value),
            )
        )
        if not self._evaluate_result or self._batch_world is None:
            return False
        for i in range(min(int(out_world_len.value), len(self._batch_world))):
            out_world[i] = self._batch_world[i]
        if self._batch_morph is not None:
            for i in range(min(int(out_morph_len.value), len(self._batch_morph))):
                out_morph[i] = self._batch_morph[i]
        return True

    def mmd_runtime_compute_maya_local_channels(
        self,
        world_matrices,
        world_matrices_len,
        parent_indices,
        parent_indices_len,
        bind_world_matrices,
        bind_world_matrices_len,
        bind_no_orient_matrices,
        bind_no_orient_matrices_len,
        joint_orient_xyzw,
        joint_orient_xyzw_len,
        rotate_orders,
        rotate_orders_len,
        bone_count,
        out_local_channels,
        out_local_channels_len,
    ):
        if self._local_channels is None:
            return False
        bone_count_value = int(getattr(bone_count, "value", bone_count))
        out_len_value = int(getattr(out_local_channels_len, "value", out_local_channels_len))
        required = bone_count_value * 6
        if out_len_value < required:
            return False
        for i in range(min(required, len(self._local_channels))):
            out_local_channels[i] = self._local_channels[i]
        return True

    def mmd_runtime_compute_maya_local_channels_batch(
        self,
        world_matrices,
        world_matrices_len,
        frame_count,
        parent_indices,
        parent_indices_len,
        bind_world_matrices,
        bind_world_matrices_len,
        bind_no_orient_matrices,
        bind_no_orient_matrices_len,
        joint_orient_xyzw,
        joint_orient_xyzw_len,
        rotate_orders,
        rotate_orders_len,
        bone_count,
        out_local_channels,
        out_local_channels_len,
    ):
        if self._local_channels_batch is None:
            return False
        frame_count_value = int(getattr(frame_count, "value", frame_count))
        bone_count_value = int(getattr(bone_count, "value", bone_count))
        out_len_value = int(getattr(out_local_channels_len, "value", out_local_channels_len))
        required = frame_count_value * bone_count_value * 6
        if out_len_value < required:
            return False
        for i in range(min(required, len(self._local_channels_batch))):
            out_local_channels[i] = self._local_channels_batch[i]
        return True

    # --- morph weights ---
    def mmd_runtime_instance_morph_weight_len(self, handle):
        return 0 if self._morph is None else len(self._morph)

    def mmd_runtime_instance_copy_morph_weights(self, handle, out_buf, n):
        if self._morph is None:
            return False
        for i in range(min(n, len(self._morph))):
            out_buf[i] = self._morph[i]
        return True

    # --- ik enabled ---
    def mmd_runtime_instance_ik_enabled_len(self, handle):
        return 0 if self._ik is None else len(self._ik)

    def mmd_runtime_instance_copy_ik_enabled(self, handle, out_buf, n):
        if self._ik is None:
            return False
        for i in range(min(n, len(self._ik))):
            out_buf[i] = self._ik[i]
        return True

    # --- handle factory ---
    def mmd_runtime_model_create_from_pmx_bytes(self, payload, payload_len):
        self.model_create_calls.append((bytes(payload[:payload_len]), int(payload_len)))
        return 0x1001

    def mmd_runtime_clip_create_from_vmd_bytes_for_model(self, model_handle, payload, payload_len):
        self.clip_create_calls.append((model_handle, bytes(payload[:payload_len]), int(payload_len)))
        return 0x2002

    def mmd_runtime_instance_create_for_model(self, model_handle):
        self.instance_create_calls.append(model_handle)
        return 0x3003

    # --- free 系 (no-op) ---
    def mmd_runtime_instance_free(self, handle):
        pass

    def mmd_runtime_model_free(self, handle):
        pass

    def mmd_runtime_clip_free(self, handle):
        pass


def _make_instance(lib, handle=0xABCD):
    """get_mmd_runtime_library を経由せず wrapper を直接構築する。"""
    inst = object.__new__(MmdRuntimeInstance)
    inst._lib = lib
    inst._handle = handle
    return inst


def _make_clip(handle=0x1234):
    clip = object.__new__(MmdRuntimeClip)
    clip._lib = None
    clip._handle = handle
    return clip


class _FakeReducedPoseLib:
    """Fake generic reduction ABI covering ownership and validation paths."""

    def __init__(self, *, feature_flags=1 << 4, invalid_descriptor=False, start_frame=10.0, frame_step=1.0):
        self.feature_flags = feature_flags
        self.invalid_descriptor = invalid_descriptor
        self.start_frame = start_frame
        self.frame_step = frame_step
        self.create_calls = []
        self.free_calls = []

    def mmd_runtime_feature_flags(self):
        return self.feature_flags

    def mmd_runtime_reduced_pose_create_from_dense(self, model, *args):
        self.create_calls.append(model)
        args[-1]._obj.value = 0xCAFE
        return rt.MMD_RUNTIME_STATUS_OK

    def mmd_runtime_reduced_pose_free(self, handle):
        self.free_calls.append(handle.value if hasattr(handle, "value") else handle)

    def mmd_runtime_reduced_pose_generic_curve_info(self, _pose, out_info):
        info = out_info._obj
        info.struct_size = ctypes.sizeof(MmdRuntimeFfiGenericCurveInfo)
        info.abi_version = 1
        info.reduction_target = 2
        info.model_identity = 7
        info.start_frame = self.start_frame
        info.frame_step = self.frame_step
        info.frame_count = 2
        info.bone_count = 1
        info.morph_count = 1
        return rt.MMD_RUNTIME_STATUS_OK

    def mmd_runtime_reduced_pose_generic_curve_count(self, _pose, out_count):
        out_count._obj.value = 2
        return rt.MMD_RUNTIME_STATUS_OK

    def mmd_runtime_reduced_pose_generic_curve_descriptor(self, _pose, index, out_descriptor):
        descriptor = out_descriptor._obj
        descriptor.struct_size = ctypes.sizeof(MmdRuntimeFfiGenericCurveDescriptor)
        descriptor.abi_version = 1
        descriptor.interpolation = 999 if self.invalid_descriptor else 2
        descriptor.key_count = 2
        if index == 0:
            descriptor.kind = 0
            descriptor.target_index = 0
            descriptor.parent_index = -1
            descriptor.value_flags = 3
            descriptor.rotation_basis = 1
        else:
            descriptor.kind = 1
            descriptor.target_index = 0
            descriptor.parent_index = -1
            descriptor.value_flags = 4
            descriptor.rotation_basis = 0
        return rt.MMD_RUNTIME_STATUS_OK

    def mmd_runtime_reduced_pose_generic_curve_keys(
        self, _pose, _index, out_keys, capacity, _stride, out_required
    ):
        out_required._obj.value = 2
        if out_keys is None or capacity == 0:
            return rt.MMD_RUNTIME_STATUS_BUFFER_TOO_SMALL
        for sample_index in range(2):
            key = out_keys[sample_index]
            key.sample_index = sample_index
            key.frame = self.start_frame + self.frame_step * sample_index
            key.rotation_xyzw[3] = 1.0
        return rt.MMD_RUNTIME_STATUS_OK

    def mmd_runtime_reduced_pose_report(self, _pose, out_report):
        report = out_report._obj
        report.source_bone_key_count = 2
        report.reduced_bone_key_count = 2
        report.source_morph_key_count = 2
        report.reduced_morph_key_count = 2
        return rt.MMD_RUNTIME_STATUS_OK


class _FakeReductionModel:
    def __init__(self, lib):
        self._lib = lib
        self._handle = ctypes.c_void_p(0x100)

    @property
    def handle(self):
        return self._handle


class _FakeReductionLibMissingSymbol(_FakeReducedPoseLib):
    def __getattribute__(self, name):
        if name == "mmd_runtime_reduced_pose_create_from_dense":
            raise AttributeError(name)
        return super().__getattribute__(name)


def _make_reduction_batch():
    return MmdRuntimeBatchEvaluation(
        frame_count=2,
        bone_count=1,
        morph_count=1,
        world_matrices=[0.0] * 32,
        morph_weights=[0.0, 0.0],
    )


# ----------------------------------------------------------------------
# MmdRuntimeInstance: 評価
# ----------------------------------------------------------------------

class TestEvaluateClipFrame(unittest.TestCase):
    def test_evaluate_passes_frame_and_returns_true(self):
        lib = _FakeRuntimeLib(evaluate_result=True)
        inst = _make_instance(lib)
        clip = _make_clip()
        self.assertTrue(inst.evaluate_clip_frame(clip, 42.5))
        self.assertEqual(len(lib.evaluate_calls), 1)
        _, _, frame = lib.evaluate_calls[0]
        # c_float の丸めを考慮し近似一致で確認
        self.assertAlmostEqual(frame, 42.5, places=4)

    def test_evaluate_returns_false_when_native_reports_failure(self):
        lib = _FakeRuntimeLib(evaluate_result=False)
        inst = _make_instance(lib)
        self.assertFalse(inst.evaluate_clip_frame(_make_clip(), 0.0))

    def test_evaluate_guards_on_null_instance_handle(self):
        lib = _FakeRuntimeLib()
        inst = _make_instance(lib, handle=0)  # null handle
        self.assertFalse(inst.evaluate_clip_frame(_make_clip(), 1.0))
        self.assertEqual(lib.evaluate_calls, [])

    def test_evaluate_guards_on_null_clip_handle(self):
        lib = _FakeRuntimeLib()
        inst = _make_instance(lib)
        self.assertFalse(inst.evaluate_clip_frame(_make_clip(handle=0), 1.0))
        self.assertEqual(lib.evaluate_calls, [])

    def test_evaluate_guards_when_clip_is_none(self):
        lib = _FakeRuntimeLib()
        inst = _make_instance(lib)
        self.assertFalse(inst.evaluate_clip_frame(None, 1.0))

    def test_evaluate_returns_false_on_native_exception(self):
        lib = _FakeRuntimeLib()

        def _boom(*_args, **_kwargs):
            raise RuntimeError("native crash")

        lib.mmd_runtime_instance_evaluate_clip_frame = _boom
        inst = _make_instance(lib)
        # 例外は捕捉され False になる (伝播しない)
        self.assertFalse(inst.evaluate_clip_frame(_make_clip(), 1.0))


class TestEvaluateClipFrameWithIkOptions(unittest.TestCase):
    def test_passes_ik_options(self):
        lib = _FakeRuntimeLib(with_ik_options=True, evaluate_result=True)
        inst = _make_instance(lib)
        ok = inst.evaluate_clip_frame_with_ik_options(
            _make_clip(), 3.0, ik_tolerance=0.05, ik_max_iterations_cap=8
        )
        self.assertTrue(ok)
        self.assertEqual(len(lib.ik_option_calls), 1)
        _, _, frame, tol, cap = lib.ik_option_calls[0]
        self.assertAlmostEqual(frame, 3.0, places=4)
        self.assertAlmostEqual(tol, 0.05, places=4)
        self.assertEqual(cap, 8)

    def test_negative_cap_is_clamped_to_zero(self):
        lib = _FakeRuntimeLib(with_ik_options=True)
        inst = _make_instance(lib)
        inst.evaluate_clip_frame_with_ik_options(
            _make_clip(), 0.0, ik_max_iterations_cap=-5
        )
        _, _, _, _, cap = lib.ik_option_calls[0]
        self.assertEqual(cap, 0)

    def test_returns_false_when_symbol_missing(self):
        lib = _FakeRuntimeLib(with_ik_options=False)
        inst = _make_instance(lib)
        self.assertFalse(
            inst.evaluate_clip_frame_with_ik_options(_make_clip(), 0.0)
        )

    def test_guards_on_null_handle(self):
        lib = _FakeRuntimeLib(with_ik_options=True)
        inst = _make_instance(lib, handle=0)
        self.assertFalse(
            inst.evaluate_clip_frame_with_ik_options(_make_clip(), 0.0)
        )


class TestEvaluateClipFrameBatch(unittest.TestCase):
    def test_returns_flat_buffers_and_counts(self):
        world = [float(i) for i in range(2 * 2 * 16)]
        morph = [0.0, 0.5, 1.0, 0.25]
        lib = _FakeRuntimeLib(
            batch_world_matrices=world,
            batch_morph_weights=morph,
        )
        inst = _make_instance(lib)
        result = inst.evaluate_clip_frame_batch(
            _make_clip(),
            0.0,
            30.0,
            2,
            worker_count=3,
        )

        self.assertIsInstance(result, MmdRuntimeBatchEvaluation)
        self.assertEqual(result.frame_count, 2)
        self.assertEqual(result.bone_count, 2)
        self.assertEqual(result.morph_count, 2)
        self.assertEqual(list(result.world_matrices), world)
        self.assertEqual(list(result.morph_weights), morph)
        self.assertEqual(len(lib.batch_calls), 1)
        _, _, start, step, count, workers = lib.batch_calls[0]
        self.assertAlmostEqual(start, 0.0, places=4)
        self.assertAlmostEqual(step, 30.0, places=4)
        self.assertEqual(count, 2)
        self.assertEqual(workers, 3)

    def test_worker_count_is_clamped_to_non_negative(self):
        lib = _FakeRuntimeLib(
            batch_world_matrices=[0.0] * 16,
            batch_morph_weights=[],
        )
        inst = _make_instance(lib)
        self.assertIsNotNone(
            inst.evaluate_clip_frame_batch(_make_clip(), 0.0, 1.0, 1, worker_count=-10)
        )
        self.assertEqual(lib.batch_calls[0][-1], 0)

    def test_returns_empty_result_for_zero_frames(self):
        lib = _FakeRuntimeLib(batch_world_matrices=[], batch_morph_weights=[])
        inst = _make_instance(lib)
        result = inst.evaluate_clip_frame_batch(_make_clip(), 0.0, 1.0, 0)
        self.assertEqual(result.frame_count, 0)
        self.assertEqual(len(result.world_matrices), 0)
        self.assertEqual(len(result.morph_weights), 0)

    def test_returns_none_when_batch_symbol_missing(self):
        lib = _FakeRuntimeLib(batch_world_matrices=[0.0] * 16, batch_morph_weights=[])
        lib.mmd_runtime_instance_evaluate_clip_frame_batch = None
        inst = _make_instance(lib)
        self.assertIsNone(inst.evaluate_clip_frame_batch(_make_clip(), 0.0, 1.0, 1))

    def test_returns_none_when_native_reports_failure(self):
        lib = _FakeRuntimeLib(
            batch_world_matrices=[0.0] * 16,
            batch_morph_weights=[],
            evaluate_result=False,
        )
        inst = _make_instance(lib)
        self.assertIsNone(inst.evaluate_clip_frame_batch(_make_clip(), 0.0, 1.0, 1))

    def test_returns_none_for_negative_frame_count(self):
        lib = _FakeRuntimeLib(batch_world_matrices=[0.0] * 16, batch_morph_weights=[])
        inst = _make_instance(lib)
        self.assertIsNone(inst.evaluate_clip_frame_batch(_make_clip(), 0.0, 1.0, -1))


class TestComputeMayaLocalChannels(unittest.TestCase):
    def test_returns_bone_channel_tuples_from_native_buffer(self):
        lib = _FakeRuntimeLib(
            local_channels=[
                1.0, 2.0, 3.0, 10.0, 20.0, 30.0,
                4.0, 5.0, 6.0, 40.0, 50.0, 60.0,
            ],
        )
        with mock.patch.object(rt, "get_mmd_runtime_library", return_value=lib):
            result = compute_maya_local_channels(
                world_matrices=[0.0] * 32,
                parent_indices=[-1, 0],
                bind_world_matrices=[0.0] * 32,
                bind_no_orient_matrices=[0.0] * 32,
                joint_orient_quats=[0.0, 0.0, 0.0, 1.0] * 2,
                rotate_orders=[0, 2],
            )

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], (1.0, 2.0, 3.0, 10.0, 20.0, 30.0))
        self.assertEqual(result[1], (4.0, 5.0, 6.0, 40.0, 50.0, 60.0))

    def test_returns_none_when_symbol_missing_or_lengths_invalid(self):
        lib = _FakeRuntimeLib(local_channels=[0.0] * 6)
        lib.mmd_runtime_compute_maya_local_channels = None
        with mock.patch.object(rt, "get_mmd_runtime_library", return_value=lib):
            self.assertIsNone(
                compute_maya_local_channels(
                    world_matrices=[0.0] * 16,
                    parent_indices=[-1],
                    bind_world_matrices=[0.0] * 16,
                    bind_no_orient_matrices=[0.0] * 16,
                    joint_orient_quats=[0.0, 0.0, 0.0, 1.0],
                    rotate_orders=[0],
                )
            )

        with mock.patch.object(rt, "get_mmd_runtime_library", return_value=_FakeRuntimeLib()):
            self.assertIsNone(
                compute_maya_local_channels(
                    world_matrices=[0.0] * 15,
                    parent_indices=[-1],
                    bind_world_matrices=[0.0] * 16,
                    bind_no_orient_matrices=[0.0] * 16,
                    joint_orient_quats=[0.0, 0.0, 0.0, 1.0],
                    rotate_orders=[0],
                )
            )


class TestComputeMayaLocalChannelsBatch(unittest.TestCase):
    def test_returns_ctypes_batch_buffer(self):
        values = [
            1.0, 2.0, 3.0, 10.0, 20.0, 30.0,
            4.0, 5.0, 6.0, 40.0, 50.0, 60.0,
            7.0, 8.0, 9.0, 70.0, 80.0, 90.0,
            10.0, 11.0, 12.0, 100.0, 110.0, 120.0,
        ]
        lib = _FakeRuntimeLib(local_channels_batch=values)
        world = (c_float * (2 * 2 * 16))(*([0.0] * (2 * 2 * 16)))
        with mock.patch.object(rt, "get_mmd_runtime_library", return_value=lib):
            result = compute_maya_local_channels_batch(
                world_matrices=world,
                frame_count=2,
                bone_count=2,
                parent_indices=[-1, 0],
                bind_world_matrices=[0.0] * 32,
                bind_no_orient_matrices=[0.0] * 32,
                joint_orient_quats=[0.0, 0.0, 0.0, 1.0] * 2,
                rotate_orders=[0, 0],
            )

        self.assertEqual(result.frame_count, 2)
        self.assertEqual(result.bone_count, 2)
        self.assertEqual(list(result.local_channels), values)

    def test_returns_none_when_batch_symbol_fails(self):
        lib = _FakeRuntimeLib(local_channels_batch=None)
        world = (c_float * 16)(*([0.0] * 16))
        with mock.patch.object(rt, "get_mmd_runtime_library", return_value=lib):
            self.assertIsNone(
                compute_maya_local_channels_batch(
                    world_matrices=world,
                    frame_count=1,
                    bone_count=1,
                    parent_indices=[-1],
                    bind_world_matrices=[0.0] * 16,
                    bind_no_orient_matrices=[0.0] * 16,
                    joint_orient_quats=[0.0, 0.0, 0.0, 1.0],
                    rotate_orders=[0],
                )
            )


class _FakeVmdSamplerLib:
    def __init__(self):
        self.free_calls = []
        self.camera_payload_len = None
        self.light_payload_len = None

    def mmd_runtime_vmd_camera_track_create_from_vmd_bytes(self, _payload, payload_len):
        self.camera_payload_len = int(payload_len)
        return 101

    def mmd_runtime_vmd_camera_track_sample(self, track, frame, out, out_len):
        if track != 101 or int(out_len.value) != 9:
            return False
        base = float(frame.value)
        values = [30.0 + base, 1.0, 2.0, 3.0, 0.1, 0.2, 0.3, 45.0, 1.0]
        for index, value in enumerate(values):
            out[index] = value
        return True

    def mmd_runtime_vmd_camera_track_free(self, track):
        self.free_calls.append(("camera", track))

    def mmd_runtime_vmd_light_track_create_from_vmd_bytes(self, _payload, payload_len):
        self.light_payload_len = int(payload_len)
        return 202

    def mmd_runtime_vmd_light_track_sample(self, track, frame, out, out_len):
        if track != 202 or int(out_len.value) != 6:
            return False
        base = float(frame.value)
        values = [1.0, 0.5, 0.25, base, -1.0, 2.0]
        for index, value in enumerate(values):
            out[index] = value
        return True

    def mmd_runtime_vmd_light_track_free(self, track):
        self.free_calls.append(("light", track))


class TestVmdRuntimeSampling(unittest.TestCase):
    def test_camera_sampler_returns_samples_and_frees_track(self):
        lib = _FakeVmdSamplerLib()

        samples = runtime_sampling.sample_vmd_camera_frames(
            b"camera-vmd",
            start_frame=10.0,
            frame_step=0.5,
            frame_count=2,
            get_library=lambda: lib,
        )

        self.assertEqual(lib.camera_payload_len, len(b"camera-vmd"))
        self.assertEqual(lib.free_calls, [("camera", 101)])
        self.assertEqual(samples[0]["frame"], 10.0)
        self.assertEqual(samples[1]["frame"], 10.5)
        self.assertEqual(samples[0]["distance"], 40.0)
        self.assertEqual(samples[0]["position"], (1.0, 2.0, 3.0))
        self.assertTrue(samples[0]["perspective"])

    def test_light_sampler_returns_samples_and_frees_track(self):
        lib = _FakeVmdSamplerLib()

        samples = runtime_sampling.sample_vmd_light_frames(
            b"light-vmd",
            start_frame=3.0,
            frame_step=1.0,
            frame_count=1,
            get_library=lambda: lib,
        )

        self.assertEqual(lib.light_payload_len, len(b"light-vmd"))
        self.assertEqual(lib.free_calls, [("light", 202)])
        self.assertEqual(samples, [{"frame": 3.0, "color": (1.0, 0.5, 0.25), "position": (3.0, -1.0, 2.0)}])

    def test_legacy_runtime_module_proxy_uses_patchable_library_getter(self):
        lib = _FakeVmdSamplerLib()

        with mock.patch.object(rt, "get_mmd_runtime_library", return_value=lib):
            samples = rt.sample_vmd_camera_frames(b"camera-vmd", 1.0, 1.0, 1)

        self.assertEqual(samples[0]["frame"], 1.0)
        self.assertEqual(lib.free_calls, [("camera", 101)])

    def test_sampler_returns_none_without_library_or_payload(self):
        self.assertIsNone(runtime_sampling.sample_vmd_camera_frames(b"", 0.0, 1.0, 1, get_library=lambda: None))
        self.assertIsNone(runtime_sampling.sample_vmd_light_frames(b"vmd", 0.0, 1.0, 0, get_library=lambda: None))


# ----------------------------------------------------------------------
# MmdRuntimeInstance: world matrices / morph / ik マーシャリング
# ----------------------------------------------------------------------

class TestWorldMatrices(unittest.TestCase):
    def test_splits_flat_floats_into_16_element_matrices(self):
        # 2 ボーン分 = 32 float。1 つ目は identity 風、2 つ目は連番。
        flat = [0.0] * 32
        flat[0] = flat[5] = flat[10] = flat[15] = 1.0  # bone0 identity
        for i in range(16):
            flat[16 + i] = float(i)  # bone1 = 0..15
        lib = _FakeRuntimeLib(world_matrices=flat)
        inst = _make_instance(lib)

        mats = inst.get_world_matrices()
        self.assertIsNotNone(mats)
        self.assertEqual(len(mats), 2)
        self.assertEqual(len(mats[0]), 16)
        self.assertEqual(mats[0][0], 1.0)
        self.assertEqual(mats[0][5], 1.0)
        self.assertEqual(mats[1], [float(i) for i in range(16)])

    def test_returns_empty_list_when_len_is_zero(self):
        lib = _FakeRuntimeLib(world_matrices=[])
        inst = _make_instance(lib)
        self.assertEqual(inst.get_world_matrices(), [])

    def test_returns_none_when_copy_fails(self):
        # len > 0 だがコピーが False を返す → None
        lib = _FakeRuntimeLib(world_matrices=[1.0] * 16)
        lib.mmd_runtime_instance_copy_world_matrices = (
            lambda handle, out, n: False
        )
        inst = _make_instance(lib)
        self.assertIsNone(inst.get_world_matrices())

    def test_returns_none_on_null_handle(self):
        lib = _FakeRuntimeLib(world_matrices=[1.0] * 16)
        inst = _make_instance(lib, handle=0)
        self.assertIsNone(inst.get_world_matrices())

    def test_out_buffer_is_ctypes_float_array(self):
        captured = {}

        def _copy(handle, out_buf, n):
            captured["type"] = type(out_buf)
            captured["len"] = n
            for i in range(n):
                out_buf[i] = float(i)
            return True

        lib = _FakeRuntimeLib(world_matrices=[0.0] * 16)
        lib.mmd_runtime_instance_copy_world_matrices = _copy
        inst = _make_instance(lib)
        inst.get_world_matrices()
        # wrapper は (c_float * n)() を渡しているはず
        self.assertEqual(captured["len"], 16)
        self.assertTrue(issubclass(captured["type"], ctypes.Array))
        self.assertIs(captured["type"]._type_, c_float)


class TestMorphWeights(unittest.TestCase):
    def test_returns_python_float_list(self):
        lib = _FakeRuntimeLib(morph_weights=[0.0, 0.25, 1.0])
        inst = _make_instance(lib)
        weights = inst.get_morph_weights()
        self.assertEqual(len(weights), 3)
        self.assertAlmostEqual(weights[1], 0.25, places=5)
        self.assertTrue(all(isinstance(w, float) for w in weights))

    def test_returns_empty_list_when_zero(self):
        lib = _FakeRuntimeLib(morph_weights=[])
        inst = _make_instance(lib)
        self.assertEqual(inst.get_morph_weights(), [])

    def test_returns_none_when_copy_fails(self):
        lib = _FakeRuntimeLib(morph_weights=[0.5])
        lib.mmd_runtime_instance_copy_morph_weights = lambda h, o, n: False
        inst = _make_instance(lib)
        self.assertIsNone(inst.get_morph_weights())

    def test_returns_none_on_null_handle(self):
        lib = _FakeRuntimeLib(morph_weights=[0.5])
        inst = _make_instance(lib, handle=0)
        self.assertIsNone(inst.get_morph_weights())


class TestIkEnabled(unittest.TestCase):
    def test_returns_int_list(self):
        lib = _FakeRuntimeLib(ik_enabled=[1, 0, 1])
        inst = _make_instance(lib)
        flags = inst.get_ik_enabled()
        self.assertEqual(flags, [1, 0, 1])
        self.assertTrue(all(isinstance(x, int) for x in flags))

    def test_returns_empty_list_when_zero(self):
        lib = _FakeRuntimeLib(ik_enabled=[])
        inst = _make_instance(lib)
        self.assertEqual(inst.get_ik_enabled(), [])

    def test_returns_none_when_copy_fails(self):
        lib = _FakeRuntimeLib(ik_enabled=[1])
        lib.mmd_runtime_instance_copy_ik_enabled = lambda h, o, n: False
        inst = _make_instance(lib)
        self.assertIsNone(inst.get_ik_enabled())

    def test_returns_none_on_null_handle(self):
        lib = _FakeRuntimeLib(ik_enabled=[1])
        inst = _make_instance(lib, handle=0)
        self.assertIsNone(inst.get_ik_enabled())


# ----------------------------------------------------------------------
# free / __del__ の冪等性
# ----------------------------------------------------------------------

class TestInstanceLifecycle(unittest.TestCase):
    def test_free_is_idempotent(self):
        freed = []
        lib = _FakeRuntimeLib()
        lib.mmd_runtime_instance_free = lambda h: freed.append(h)
        inst = _make_instance(lib, handle=0x55)
        inst.free()
        inst.free()
        # 1 回だけ free が呼ばれ、2 回目は handle が None なので呼ばれない
        self.assertEqual(freed, [0x55])
        self.assertIsNone(inst._handle)

    def test_free_swallows_native_exception(self):
        lib = _FakeRuntimeLib()

        def _boom(_h):
            raise RuntimeError("free crash")

        lib.mmd_runtime_instance_free = _boom
        inst = _make_instance(lib, handle=0x55)
        # 例外を伝播しない
        inst.free()
        self.assertIsNone(inst._handle)


# ----------------------------------------------------------------------
# MmdParsedModel: フェイク lib によるカウント/ポインタ/バッファ
# ----------------------------------------------------------------------

class _FakeParsedLib:
    """parsed-model アクセサ群を模したフェイク CDLL。

    ポインタアクセサは「実メモリ上の ctypes 配列のアドレス」を int で返すことで、
    wrapper 側の ``from_address`` 読み出しを本物に近い形で通す。
    生成した配列は GC されないよう保持する。
    """

    def __init__(self):
        self._keepalive = []  # ctypes 配列が GC されないよう保持
        self.create_calls = []
        self.vertex_count = 2
        self.index_count = 3
        self.material_group_count = 1
        self.vertex_morph_count = 1
        self.vertex_morph_offset_count = 2

        self._positions = [(0.0, 0.0, 0.0), (1.0, 2.0, 3.0)]
        self._material_groups = [(0, 3, 5)]
        self._morph_names = ["まばたき"]

    def mmd_runtime_parsed_model_create_from_pmx_bytes(self, payload, payload_len):
        self.create_calls.append((bytes(payload[:payload_len]), int(payload_len)))
        return 0x9901

    # --- counts ---
    def mmd_runtime_parsed_model_vertex_count(self, h):
        return self.vertex_count

    def mmd_runtime_parsed_model_index_count(self, h):
        return self.index_count

    def mmd_runtime_parsed_model_material_group_count(self, h):
        return self.material_group_count

    def mmd_runtime_parsed_model_vertex_morph_count(self, h):
        return self.vertex_morph_count

    def mmd_runtime_parsed_model_vertex_morph_offset_count(self, h):
        return self.vertex_morph_offset_count

    # --- pointer accessors (return int address of a ctypes array) ---
    def _addr_of_float_array(self, values):
        arr = (c_float * len(values))(*values)
        self._keepalive.append(arr)
        return ctypes.addressof(arr)

    def _addr_of_u32_array(self, values):
        arr = (ctypes.c_uint32 * len(values))(*values)
        self._keepalive.append(arr)
        return ctypes.addressof(arr)

    def mmd_runtime_parsed_model_positions(self, h):
        flat = [c for xyz in self._positions for c in xyz]
        return self._addr_of_float_array(flat)

    def mmd_runtime_parsed_model_material_groups(self, h):
        flat = [c for g in self._material_groups for c in g]
        return self._addr_of_u32_array(flat)

    # --- byte buffers (return MmdRuntimeFfiByteBuffer by value) ---
    def mmd_runtime_parsed_model_vertex_morph_name(self, h, index):
        name = self._morph_names[index]
        encoded = name.encode("utf-8")
        arr = (c_uint8 * len(encoded)).from_buffer_copy(encoded)
        self._keepalive.append(arr)
        buf = MmdRuntimeFfiByteBuffer()
        buf.data = ctypes.cast(arr, ctypes.POINTER(c_uint8))
        buf.len = len(encoded)
        return buf

    def mmd_runtime_parsed_model_metadata_json(self, h):
        payload = b'{"format":"PMX","version":2.0,"name":"x","counts":{}}'
        arr = (c_uint8 * len(payload)).from_buffer_copy(payload)
        self._keepalive.append(arr)
        buf = MmdRuntimeFfiByteBuffer()
        buf.data = ctypes.cast(arr, ctypes.POINTER(c_uint8))
        buf.len = len(payload)
        return buf

    def mmd_runtime_byte_buffer_free(self, buf):
        # no-op (keepalive が解放を担保)
        pass

    def mmd_runtime_parsed_model_free(self, h):
        pass


def _make_parsed(lib, handle=0x99):
    model = object.__new__(MmdParsedModel)
    model._lib = lib
    model._handle = handle
    return model


class TestParsedModelCounts(unittest.TestCase):
    def test_counts_proxy_to_native(self):
        model = _make_parsed(_FakeParsedLib())
        self.assertEqual(model.vertex_count, 2)
        self.assertEqual(model.index_count, 3)
        self.assertEqual(model.material_group_count, 1)
        self.assertEqual(model.vertex_morph_count, 1)
        self.assertEqual(model.vertex_morph_offset_count, 2)

    def test_count_returns_zero_when_symbol_missing(self):
        # vertex_count シンボルだけ削除したフェイク
        class _NoVertexCount(_FakeParsedLib):
            mmd_runtime_parsed_model_vertex_count = None

        model = _make_parsed(_NoVertexCount())
        self.assertEqual(model.vertex_count, 0)

    def test_count_returns_zero_on_native_exception(self):
        lib = _FakeParsedLib()
        lib.mmd_runtime_parsed_model_vertex_count = lambda h: (_ for _ in ()).throw(RuntimeError())
        model = _make_parsed(lib)
        self.assertEqual(model.vertex_count, 0)


class TestParsedModelPointerAccessors(unittest.TestCase):
    def test_positions_read_from_address(self):
        model = _make_parsed(_FakeParsedLib())
        positions = model.positions
        self.assertIsNotNone(positions)
        self.assertEqual(len(positions), 2)
        self.assertEqual(positions[0], (0.0, 0.0, 0.0))
        self.assertEqual(positions[1], (1.0, 2.0, 3.0))

    def test_material_groups_triples(self):
        model = _make_parsed(_FakeParsedLib())
        groups = model.material_groups
        self.assertEqual(groups, [(0, 3, 5)])

    def test_positions_none_when_pointer_null(self):
        lib = _FakeParsedLib()
        lib.mmd_runtime_parsed_model_positions = lambda h: 0  # NULL
        model = _make_parsed(lib)
        self.assertIsNone(model.positions)

    def test_positions_none_when_symbol_missing(self):
        lib = _FakeParsedLib()
        lib.mmd_runtime_parsed_model_positions = None
        model = _make_parsed(lib)
        self.assertIsNone(model.positions)


class TestParsedModelByteBuffers(unittest.TestCase):
    def test_vertex_morph_names_utf8_decoded(self):
        model = _make_parsed(_FakeParsedLib())
        names = model.vertex_morph_names
        self.assertEqual(names, ["まばたき"])

    def test_metadata_json_decoded(self):
        model = _make_parsed(_FakeParsedLib())
        raw = model.metadata_json
        self.assertIn('"format":"PMX"', raw)

    def test_metadata_json_none_when_buffer_empty(self):
        lib = _FakeParsedLib()

        def _empty(h):
            buf = MmdRuntimeFfiByteBuffer()
            buf.data = None
            buf.len = 0
            return buf

        lib.mmd_runtime_parsed_model_metadata_json = _empty
        model = _make_parsed(lib)
        self.assertIsNone(model.metadata_json)

    def test_vertex_morph_names_none_when_symbol_missing(self):
        lib = _FakeParsedLib()
        lib.mmd_runtime_parsed_model_vertex_morph_name = None
        model = _make_parsed(lib)
        self.assertIsNone(model.vertex_morph_names)


class _FakePmxExportLib:
    def __init__(self, payload=b"PMX exported bytes", *, empty_result=False):
        self.payload = payload
        self.empty_result = empty_result
        self.calls = []
        self.free_calls = 0
        self._keepalive = []

    def mmd_runtime_export_pmx_from_parts(
        self,
        metadata_json,
        metadata_json_len,
        positions_xyz,
        vertex_count,
        normals_xyz,
        uvs_xy,
        indices,
        index_count,
        skin_indices,
        skin_weights,
        edge_scale,
    ):
        vertex_count = int(vertex_count)
        index_count = int(index_count)
        metadata_json_len = int(metadata_json_len)
        self.calls.append(
            {
                "metadata": bytes(metadata_json[i] for i in range(metadata_json_len)),
                "positions": [float(positions_xyz[i]) for i in range(vertex_count * 3)],
                "normals": [float(normals_xyz[i]) for i in range(vertex_count * 3)],
                "uvs": [float(uvs_xy[i]) for i in range(vertex_count * 2)],
                "indices": [int(indices[i]) for i in range(index_count)] if indices else None,
                "skin_indices": [int(skin_indices[i]) for i in range(vertex_count * 4)] if skin_indices else None,
                "skin_weights": [float(skin_weights[i]) for i in range(vertex_count * 4)] if skin_weights else None,
                "edge_scale": [float(edge_scale[i]) for i in range(vertex_count)] if edge_scale else None,
            }
        )
        buf = MmdRuntimeFfiByteBuffer()
        if self.empty_result:
            buf.data = None
            buf.len = 0
            return buf
        arr = (c_uint8 * len(self.payload)).from_buffer_copy(self.payload)
        self._keepalive.append(arr)
        buf.data = ctypes.cast(arr, ctypes.POINTER(c_uint8))
        buf.len = len(self.payload)
        return buf

    def mmd_runtime_byte_buffer_free(self, buf):
        self.free_calls += 1


class TestPmxPartsExportWrapper(unittest.TestCase):
    def test_availability_false_without_library(self):
        with mock.patch.object(rt, "get_mmd_runtime_library", return_value=None):
            self.assertFalse(rt.is_native_pmx_parts_export_available())
            self.assertIsNone(
                rt.export_pmx_from_parts(
                    {"format": "PMX"},
                    [0.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0],
                    [0.0, 0.0],
                )
            )

    def test_export_passes_flat_buffers_and_frees_result(self):
        lib = _FakePmxExportLib(payload=b"PMX-data")
        with mock.patch.object(rt, "get_mmd_runtime_library", return_value=lib):
            self.assertTrue(rt.is_native_pmx_parts_export_available())
            result = rt.export_pmx_from_parts(
                {"format": "PMX", "version": 2.0},
                [0, 0, 0, 1, 2, 3],
                [0, 1, 0, 0, 1, 0],
                [0, 0, 1, 1],
                indices=[0, 1, 0],
                skin_indices=[0, 1, 2, 3, 4, 5, 6, 7],
                skin_weights=[1, 0, 0, 0, 0.25, 0.25, 0.25, 0.25],
                edge_scale=[1, 0.5],
            )

        self.assertEqual(result, b"PMX-data")
        self.assertEqual(lib.free_calls, 1)
        self.assertEqual(len(lib.calls), 1)
        call = lib.calls[0]
        self.assertEqual(call["metadata"], b'{"format":"PMX","version":2.0}')
        self.assertEqual(call["positions"], [0.0, 0.0, 0.0, 1.0, 2.0, 3.0])
        self.assertEqual(call["uvs"], [0.0, 0.0, 1.0, 1.0])
        self.assertEqual(call["indices"], [0, 1, 0])
        self.assertEqual(call["skin_indices"], [0, 1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(call["edge_scale"], [1.0, 0.5])

    def test_export_allows_optional_buffers_to_be_null(self):
        lib = _FakePmxExportLib()
        with mock.patch.object(rt, "get_mmd_runtime_library", return_value=lib):
            result = rt.export_pmx_from_parts(
                b'{"format":"PMX"}',
                [0, 0, 0],
                [0, 1, 0],
                [0, 0],
            )

        self.assertEqual(result, b"PMX exported bytes")
        call = lib.calls[0]
        self.assertIsNone(call["indices"])
        self.assertIsNone(call["skin_indices"])
        self.assertIsNone(call["skin_weights"])
        self.assertIsNone(call["edge_scale"])

    def test_export_empty_native_buffer_returns_none_and_frees(self):
        lib = _FakePmxExportLib(empty_result=True)
        with mock.patch.object(rt, "get_mmd_runtime_library", return_value=lib):
            result = rt.export_pmx_from_parts(
                {"format": "PMX"},
                [0, 0, 0],
                [0, 1, 0],
                [0, 0],
            )

        self.assertIsNone(result)
        self.assertEqual(lib.free_calls, 1)

    def test_export_rejects_mismatched_skin_buffers_before_native_call(self):
        lib = _FakePmxExportLib()
        with mock.patch.object(rt, "get_mmd_runtime_library", return_value=lib):
            result = rt.export_pmx_from_parts(
                {"format": "PMX"},
                [0, 0, 0],
                [0, 1, 0],
                [0, 0],
                skin_indices=[0, 1, 2, 3],
            )

        self.assertIsNone(result)
        self.assertEqual(lib.calls, [])
        self.assertEqual(lib.free_calls, 0)


class _FakeJsonExportLib:
    def __init__(self, *, payload=b"mmd bytes", empty_result=False, missing_symbols=()):
        self.payload = payload
        self.empty_result = empty_result
        self.calls = []
        self.free_calls = 0
        self._missing_symbols = set(missing_symbols)
        self._keepalive = []

    def __getattribute__(self, name):
        if name.startswith("mmd_runtime_export_"):
            missing = object.__getattribute__(self, "_missing_symbols")
            if name in missing:
                raise AttributeError(name)
        return object.__getattribute__(self, name)

    def _export(self, symbol, json_payload, json_len):
        json_len = int(json_len)
        self.calls.append(
            {
                "symbol": symbol,
                "payload": bytes(json_payload[i] for i in range(json_len)),
            }
        )
        buf = MmdRuntimeFfiByteBuffer()
        if self.empty_result:
            buf.data = None
            buf.len = 0
            return buf
        arr = (c_uint8 * len(self.payload)).from_buffer_copy(self.payload)
        self._keepalive.append(arr)
        buf.data = ctypes.cast(arr, ctypes.POINTER(c_uint8))
        buf.len = len(self.payload)
        return buf

    def mmd_runtime_export_pmx_model_json(self, json_payload, json_len):
        return self._export("pmx", json_payload, json_len)

    def mmd_runtime_byte_buffer_free(self, buf):
        self.free_calls += 1


class TestJsonExportWrapper(unittest.TestCase):
    def test_availability_uses_format_symbol(self):
        lib = _FakeJsonExportLib()
        with mock.patch.object(rt, "get_mmd_runtime_library", return_value=lib):
            self.assertFalse(rt.is_native_json_export_available("vmd"))
            self.assertTrue(rt.is_native_json_export_available("pmx"))
            self.assertFalse(rt.is_native_json_export_available("pmd"))
            self.assertFalse(rt.is_native_json_export_available("unknown"))

    def test_export_pmx_json_selects_pmx_symbol(self):
        lib = _FakeJsonExportLib(payload=b"PMX")
        with mock.patch.object(rt, "get_mmd_runtime_library", return_value=lib):
            self.assertEqual(rt.export_pmx_model_json(b'{"kind":"pmx"}'), b"PMX")

        self.assertEqual([call["symbol"] for call in lib.calls], ["pmx"])
        self.assertEqual(lib.free_calls, 1)

    def test_export_json_returns_none_for_empty_pmx_result(self):
        empty = _FakeJsonExportLib(empty_result=True)
        with mock.patch.object(rt, "get_mmd_runtime_library", return_value=empty):
            self.assertIsNone(rt.export_pmx_model_json({"kind": "pmx"}))
        self.assertEqual(empty.free_calls, 1)


# ----------------------------------------------------------------------
# _set_sig / loader.find_library / get_mmd_runtime_library のキャッシュ
# ----------------------------------------------------------------------

class _SigStub:
    """argtypes/restype を属性として受け取れるだけの呼び出し可能スタブ。"""

    def __init__(self):
        self.restype = None
        self.argtypes = None


class _FakeLibForSig:
    def __init__(self, present_names):
        self._present = {name: _SigStub() for name in present_names}

    def __getattr__(self, name):
        # getattr(lib, name, None) で None を返したいので存在しなければ AttributeError
        present = self.__dict__.get("_present", {})
        if name in present:
            return present[name]
        raise AttributeError(name)


class TestSetSig(unittest.TestCase):
    def test_sets_restype_and_argtypes_when_present(self):
        lib = _FakeLibForSig(["sym_a"])
        rt._set_sig(lib, "sym_a", ctypes.c_size_t, [ctypes.c_void_p])
        self.assertIs(lib._present["sym_a"].restype, ctypes.c_size_t)
        self.assertEqual(lib._present["sym_a"].argtypes, [ctypes.c_void_p])

    def test_noop_when_symbol_missing(self):
        lib = _FakeLibForSig([])
        # 例外なく何もしない
        rt._set_sig(lib, "missing_sym", ctypes.c_size_t, [])


class TestFindLibrary(unittest.TestCase):
    def setUp(self):
        self._environment = mock.patch.dict(os.environ, {"MMD_ANIM_FFI_PATH": ""}, clear=False)
        self._environment.start()

    def tearDown(self):
        self._environment.stop()

    def test_default_candidates_only_include_absolute_bundled_directory(self):
        self.assertEqual(runtime_loader._CANDIDATE_PATHS, [runtime_loader._BUNDLED_LIBRARY_DIR])
        self.assertTrue(runtime_loader._BUNDLED_LIBRARY_DIR.is_absolute())

    def test_returns_none_when_no_candidate_exists(self):
        # すべての候補パスを存在しないものに差し替える
        from pathlib import Path
        with mock.patch.object(runtime_loader, "_CANDIDATE_PATHS",
                               [Path("F:/__definitely_missing_dir__/x")]):
            self.assertIsNone(runtime_loader.find_library())

    def test_finds_explicit_file_candidate(self):
        # 実在する一時ファイルを「ライブラリ名そのもの」で配置し検出させる
        import tempfile
        from pathlib import Path

        lib_name = runtime_loader._LIB_NAMES[0]
        tmpdir = tempfile.mkdtemp()
        try:
            lib_path = Path(tmpdir) / lib_name
            lib_path.write_bytes(b"\x00")
            with mock.patch.object(runtime_loader, "_CANDIDATE_PATHS", [lib_path]):
                found = runtime_loader.find_library()
            self.assertIsNotNone(found)
            self.assertEqual(Path(found).name, lib_name)
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_finds_library_in_directory_candidate(self):
        import tempfile
        from pathlib import Path

        lib_name = runtime_loader._LIB_NAMES[0]
        tmpdir = tempfile.mkdtemp()
        try:
            (Path(tmpdir) / lib_name).write_bytes(b"\x00")
            with mock.patch.object(runtime_loader, "_CANDIDATE_PATHS", [Path(tmpdir)]):
                found = runtime_loader.find_library()
            self.assertIsNotNone(found)
            self.assertEqual(Path(found).name, lib_name)
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_finds_absolute_environment_override(self):
        import tempfile
        from pathlib import Path

        lib_name = runtime_loader._LIB_NAMES[0]
        with tempfile.TemporaryDirectory() as temporary:
            lib_path = Path(temporary) / lib_name
            lib_path.write_bytes(b"\x00")
            with mock.patch.dict("os.environ", {"MMD_ANIM_FFI_PATH": str(lib_path)}, clear=False):
                with mock.patch.object(runtime_loader, "_CANDIDATE_PATHS", []):
                    self.assertEqual(runtime_loader.find_library(), lib_path.resolve())

    def test_ignores_cwd_and_relative_plugin_runtime_candidates(self):
        import tempfile
        from pathlib import Path

        lib_name = runtime_loader._LIB_NAMES[0]
        with tempfile.TemporaryDirectory() as temporary:
            model_dir = Path(temporary)
            (model_dir / lib_name).write_bytes(b"cwd")
            plugin_dir = model_dir / "plug-ins"
            plugin_dir.mkdir()
            (plugin_dir / lib_name).write_bytes(b"plugin")

            with mock.patch.object(runtime_loader, "_CANDIDATE_PATHS", []), mock.patch(
                "os.getcwd", return_value=str(model_dir)
            ):
                self.assertIsNone(runtime_loader.find_library())

    def test_rejects_relative_environment_override(self):
        import tempfile
        from pathlib import Path

        lib_name = runtime_loader._LIB_NAMES[0]
        with tempfile.TemporaryDirectory() as temporary:
            model_dir = Path(temporary)
            (model_dir / lib_name).write_bytes(b"relative")
            with mock.patch.dict("os.environ", {"MMD_ANIM_FFI_PATH": lib_name}, clear=False):
                with mock.patch.object(runtime_loader, "_CANDIDATE_PATHS", []), mock.patch(
                    "os.getcwd", return_value=str(model_dir)
                ):
                    self.assertIsNone(runtime_loader.find_library())


class TestGetRuntimeLibraryCache(unittest.TestCase):
    def setUp(self):
        # グローバルキャッシュを退避し、各テストで初期化
        self._saved_lib = runtime_loader._runtime_lib
        self._saved_path = runtime_loader._runtime_lib_path

    def tearDown(self):
        runtime_loader._runtime_lib = self._saved_lib
        runtime_loader._runtime_lib_path = self._saved_path

    def test_caches_false_when_library_not_found(self):
        runtime_loader._runtime_lib = None
        runtime_loader._runtime_lib_path = None
        with mock.patch.object(runtime_loader, "find_library", return_value=None) as finder:
            self.assertIsNone(rt.get_mmd_runtime_library())
            # 2 回目はキャッシュにより find_library を再呼び出ししない
            self.assertIsNone(rt.get_mmd_runtime_library())
        self.assertEqual(finder.call_count, 1)
        self.assertIs(runtime_loader._runtime_lib, False)

    def test_is_available_reflects_cached_false(self):
        runtime_loader._runtime_lib = False
        self.assertFalse(rt.is_mmd_runtime_available())

    def test_is_native_pmx_parser_available_false_when_lib_none(self):
        runtime_loader._runtime_lib = False  # キャッシュ済み「ロード失敗」
        self.assertFalse(rt.is_native_pmx_parser_available())

    def test_rejects_runtime_library_when_abi_mismatches(self):
        from pathlib import Path

        path = Path("F:/runtime/mmd_runtime_ffi.dll")

        class FakeLib:
            @staticmethod
            def mmd_runtime_abi_version():
                return runtime_loader.MMD_RUNTIME_ABI_VERSION + 1

        runtime_loader._runtime_lib = None
        runtime_loader._runtime_lib_path = None
        with mock.patch.object(runtime_loader, "find_library", return_value=path), mock.patch.object(
            runtime_loader.ctypes, "CDLL", return_value=FakeLib()
        ), mock.patch.object(runtime_loader, "setup_function_signatures"):
            self.assertIsNone(rt.get_mmd_runtime_library())

        self.assertIs(runtime_loader._runtime_lib, False)
        self.assertIsNone(runtime_loader._runtime_lib_path)

    def test_accepts_current_and_compatible_runtime_abi_versions(self):
        from pathlib import Path

        path = Path("F:/runtime/mmd_runtime_ffi.dll")
        for abi_version in runtime_loader.MMD_RUNTIME_ABI_VERSIONS_SUPPORTED:
            class FakeLib:
                @staticmethod
                def mmd_runtime_abi_version():
                    return abi_version

            runtime_loader._runtime_lib = None
            runtime_loader._runtime_lib_path = None
            with self.subTest(abi_version=abi_version), mock.patch.object(
                runtime_loader, "find_library", return_value=path
            ), mock.patch.object(runtime_loader.ctypes, "CDLL", return_value=FakeLib()), mock.patch.object(
                runtime_loader, "setup_function_signatures"
            ):
                self.assertIsNotNone(rt.get_mmd_runtime_library())

            self.assertIsNot(runtime_loader._runtime_lib, False)
            self.assertEqual(runtime_loader._runtime_lib_path, path)

    def test_distribution_abi_escape_hatch_is_disabled(self):
        from pathlib import Path

        path = Path("F:/runtime/mmd_runtime_ffi.dll")

        class FakeLib:
            @staticmethod
            def mmd_runtime_abi_version():
                return runtime_loader.MMD_RUNTIME_ABI_VERSION + 1

        runtime_loader._runtime_lib = None
        runtime_loader._runtime_lib_path = None
        with mock.patch.dict("os.environ", {"MMD_ANIM_FFI_ALLOW_ABI_MISMATCH": "1"}, clear=False):
            with mock.patch.object(runtime_loader, "find_library", return_value=path), mock.patch.object(
                runtime_loader.ctypes, "CDLL", return_value=FakeLib()
            ), mock.patch.object(runtime_loader, "setup_function_signatures"):
                self.assertIsNone(rt.get_mmd_runtime_library())

        self.assertIs(runtime_loader._runtime_lib, False)
        self.assertIsNone(runtime_loader._runtime_lib_path)

    def test_logs_loaded_runtime_library_path_at_info_level(self):
        from pathlib import Path

        path = Path("F:/runtime/mmd_runtime_ffi.dll")

        class FakeLib:
            @staticmethod
            def mmd_runtime_abi_version():
                return runtime_loader.MMD_RUNTIME_ABI_VERSION

        runtime_loader._runtime_lib = None
        runtime_loader._runtime_lib_path = None
        with mock.patch.object(runtime_loader, "find_library", return_value=path), mock.patch.object(
            runtime_loader.ctypes, "CDLL", return_value=FakeLib()
        ), mock.patch.object(runtime_loader, "setup_function_signatures"):
            with self.assertLogs(runtime_loader.logger.name, level="INFO") as logs:
                self.assertIsNotNone(rt.get_mmd_runtime_library())

        self.assertIn(str(path), "\n".join(logs.output))
        self.assertIn(f"ABI {runtime_loader.MMD_RUNTIME_ABI_VERSION}", "\n".join(logs.output))


# ----------------------------------------------------------------------
# from_pmx_bytes / from_vmd_bytes のガード (lib が None のとき)
# ----------------------------------------------------------------------

class TestFactoryGuardsWithoutLibrary(unittest.TestCase):
    def setUp(self):
        self._saved = runtime_loader._runtime_lib

    def tearDown(self):
        runtime_loader._runtime_lib = self._saved

    def test_model_from_empty_bytes_returns_none_even_if_lib_present(self):
        # lib があっても空 bytes は弾く
        runtime_loader._runtime_lib = _FakeRuntimeLib()
        self.assertIsNone(MmdRuntimeModel.from_pmx_bytes(b""))

    def test_clip_from_none_model_returns_none(self):
        runtime_loader._runtime_lib = _FakeRuntimeLib()
        self.assertIsNone(MmdRuntimeClip.from_vmd_bytes_for_model(None, b"vmd"))

    def test_instance_for_none_model_returns_none(self):
        runtime_loader._runtime_lib = _FakeRuntimeLib()
        self.assertIsNone(MmdRuntimeInstance.for_model(None))

    def test_handle_factories_use_legacy_runtime_module_getter_patch(self):
        lib = _FakeRuntimeLib()
        with mock.patch.object(rt, "get_mmd_runtime_library", return_value=lib):
            model = MmdRuntimeModel.from_pmx_bytes(b"pmx")
            clip = MmdRuntimeClip.from_vmd_bytes_for_model(model, b"vmd")
            instance = MmdRuntimeInstance.for_model(model)

        self.assertEqual(model.handle, 0x1001)
        self.assertEqual(clip.handle, 0x2002)
        self.assertEqual(instance.handle, 0x3003)
        self.assertEqual(lib.model_create_calls, [(b"pmx", 3)])
        self.assertEqual(lib.clip_create_calls, [(0x1001, b"vmd", 3)])
        self.assertEqual(lib.instance_create_calls, [0x1001])

    def test_parsed_model_from_empty_bytes_returns_none(self):
        runtime_loader._runtime_lib = _FakeParsedLib()
        self.assertIsNone(MmdParsedModel.from_pmx_bytes(b""))

    def test_parsed_model_factory_uses_legacy_runtime_module_getter_patch(self):
        lib = _FakeParsedLib()
        with mock.patch.object(rt, "get_mmd_runtime_library", return_value=lib):
            parsed = MmdParsedModel.from_pmx_bytes(b"pmx")

        self.assertEqual(parsed.handle, 0x9901)
        self.assertEqual(lib.create_calls, [(b"pmx", 3)])

    def test_package_parser_availability_uses_legacy_runtime_module_getter_patch(self):
        lib = _FakeParsedLib()
        with mock.patch.object(rt, "get_mmd_runtime_library", return_value=lib):
            self.assertTrue(rt.is_native_pmx_parser_available())
            self.assertTrue(native_pkg.is_native_pmx_parser_available())


class TestNativeReducedPoseWrapper(unittest.TestCase):
    """Generic reduced-pose DTO and native-handle ownership contracts."""

    def test_reduce_dense_pose_snapshots_generic_curves_and_frees_once(self):
        lib = _FakeReducedPoseLib()
        model = _FakeReductionModel(lib)
        result = rt.reduce_dense_pose(model, _make_reduction_batch(), model_identity=7, start_frame=10.0)

        self.assertIsNotNone(result)
        self.assertEqual(len(result.curves), 2)
        self.assertEqual(result.curves[0].descriptor.target_index, 0)
        self.assertEqual(result.curves[1].descriptor.kind, 1)
        self.assertEqual(lib.create_calls, [model.handle])
        self.assertEqual(lib.free_calls, [0xCAFE])

    def test_reduce_dense_pose_accepts_large_f32_frame_origin_and_step(self):
        start_frame = 100000.1
        frame_step = 1000000.1
        lib = _FakeReducedPoseLib(start_frame=start_frame, frame_step=frame_step)
        model = _FakeReductionModel(lib)

        result = rt.reduce_dense_pose(
            model,
            _make_reduction_batch(),
            model_identity=7,
            start_frame=start_frame,
            frame_step=frame_step,
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.info.start_frame, c_float(start_frame).value)
        self.assertEqual(result.info.frame_step, c_float(frame_step).value)
        self.assertEqual([key.frame for key in result.curves[0].keys], [
            c_float(start_frame).value,
            c_float(start_frame + frame_step).value,
        ])
        self.assertEqual(lib.free_calls, [0xCAFE])

    def test_validation_failure_still_frees_once(self):
        lib = _FakeReducedPoseLib(invalid_descriptor=True)
        model = _FakeReductionModel(lib)

        result = rt.reduce_dense_pose(model, _make_reduction_batch(), model_identity=7, start_frame=10.0)

        self.assertIsNone(result)
        self.assertEqual(lib.create_calls, [model.handle])
        self.assertEqual(lib.free_calls, [0xCAFE])

    def test_missing_feature_or_symbol_returns_none_without_create_or_free(self):
        for lib in (_FakeReducedPoseLib(feature_flags=0), _FakeReductionLibMissingSymbol()):
            model = _FakeReductionModel(lib)
            result = rt.reduce_dense_pose(model, _make_reduction_batch(), model_identity=7, start_frame=10.0)
            self.assertIsNone(result)
            self.assertEqual(lib.create_calls, [])
            self.assertEqual(lib.free_calls, [])


class _FakePhysicsLib:
    """Fake CDLL covering physics world create/reset/step/bake ABI."""

    def __init__(
        self,
        *,
        feature_flags=0,
        create_status=0,
        world_handle=0x4404,
        bake_status=0,
        step_status=0,
        set_mode_status=0,
        rest_pose_ok=True,
        bone_count=2,
        morph_count=1,
        batch_world_matrices=None,
        batch_morph_weights=None,
        missing_symbols=None,
    ):
        self.feature_flags = feature_flags
        self.create_status = create_status
        self.world_handle = world_handle
        self.bake_status = bake_status
        self.step_status = step_status
        self.set_mode_status = set_mode_status
        self.rest_pose_ok = rest_pose_ok
        self.bone_count = bone_count
        self.morph_count = morph_count
        self._batch_world = (
            list(batch_world_matrices) if batch_world_matrices is not None else None
        )
        self._batch_morph = (
            list(batch_morph_weights) if batch_morph_weights is not None else None
        )
        self._missing_symbols = set(missing_symbols or ())
        self.create_calls = []
        self.reset_calls = []
        self.count_calls = []
        self.free_calls = []
        self.set_mode_calls = []
        self.rest_pose_calls = []
        self.step_calls = []
        self.bake_calls = []
        self.physics_mode = 0

    def __getattribute__(self, name):
        if name.startswith("mmd_runtime_"):
            missing = object.__getattribute__(self, "_missing_symbols")
            if name in missing:
                raise AttributeError(name)
        return object.__getattribute__(self, name)

    def mmd_runtime_feature_flags(self):
        return self.feature_flags

    def mmd_runtime_physics_world_create_from_pmx_bytes(self, pmx_data, pmx_len, out_world):
        self.create_calls.append(bytes(pmx_data[:pmx_len]))
        if self.create_status == rt.MMD_RUNTIME_STATUS_OK and self.world_handle:
            out_world._obj.value = self.world_handle
        return self.create_status

    def mmd_runtime_physics_world_reset(self, world, instance, out_seeded):
        self.reset_calls.append((world.value, instance.value))
        out_seeded._obj.value = 7
        return rt.MMD_RUNTIME_STATUS_OK

    def mmd_runtime_physics_world_rigidbody_count(self, world, out_count):
        self.count_calls.append(world.value)
        out_count._obj.value = 9
        return rt.MMD_RUNTIME_STATUS_OK

    def mmd_runtime_physics_world_free(self, world):
        self.free_calls.append(world.value)

    def mmd_runtime_instance_set_physics_mode(self, instance, mode):
        mode_value = int(getattr(mode, "value", mode))
        self.set_mode_calls.append((instance.value, mode_value))
        if self.set_mode_status == rt.MMD_RUNTIME_STATUS_OK:
            self.physics_mode = mode_value
        return self.set_mode_status

    def mmd_runtime_instance_get_physics_mode(self, instance, out_mode):
        out_mode._obj.value = self.physics_mode
        return rt.MMD_RUNTIME_STATUS_OK

    def mmd_runtime_instance_evaluate_rest_pose(self, instance):
        self.rest_pose_calls.append(instance.value)
        return self.rest_pose_ok

    def mmd_runtime_physics_world_step_runtime(self, world, instance, dt_seconds, out_report):
        self.step_calls.append(
            (world.value, instance.value, float(getattr(dt_seconds, "value", dt_seconds)))
        )
        if self.step_status == rt.MMD_RUNTIME_STATUS_OK and out_report:
            report = out_report._obj
            report.tick.input_dt_seconds = float(getattr(dt_seconds, "value", dt_seconds))
            report.tick.clamped_dt_seconds = float(getattr(dt_seconds, "value", dt_seconds))
            report.tick.substeps = 2
            report.tick.accumulator_seconds = 0.0
            report.kinematic_rigidbodies_fed = 1
            report.bones_written_back = self.bone_count
        return self.step_status

    def mmd_runtime_instance_clip_frame_batch_world_matrix_f32_len(self, handle, frame_count):
        count = int(getattr(frame_count, "value", frame_count))
        if self._batch_world is not None:
            return len(self._batch_world)
        return self.bone_count * 16 * count

    def mmd_runtime_instance_clip_frame_batch_morph_weight_f32_len(self, handle, frame_count):
        count = int(getattr(frame_count, "value", frame_count))
        if self._batch_morph is not None:
            return len(self._batch_morph)
        return self.morph_count * count

    def mmd_runtime_physics_world_bake_clip_frames(
        self,
        world,
        instance,
        clip,
        start_frame,
        frame_step,
        dt_seconds,
        frame_count,
        out_world,
        out_world_len,
        out_morph,
        out_morph_len,
        out_last_report,
    ):
        call = (
            world.value,
            instance.value,
            clip.value,
            float(getattr(start_frame, "value", start_frame)),
            float(getattr(frame_step, "value", frame_step)),
            float(getattr(dt_seconds, "value", dt_seconds)),
            int(getattr(frame_count, "value", frame_count)),
            int(getattr(out_world_len, "value", out_world_len)),
            int(getattr(out_morph_len, "value", out_morph_len)),
        )
        self.bake_calls.append(call)
        if self.bake_status != rt.MMD_RUNTIME_STATUS_OK:
            return self.bake_status
        count = int(getattr(frame_count, "value", frame_count))
        world_len = int(getattr(out_world_len, "value", out_world_len))
        morph_len = int(getattr(out_morph_len, "value", out_morph_len))
        if self._batch_world is not None:
            values = self._batch_world
        else:
            values = [float(i + 1) for i in range(world_len)]
        for i in range(min(world_len, len(values))):
            out_world[i] = values[i]
        if morph_len:
            if self._batch_morph is not None:
                morph_values = self._batch_morph
            else:
                morph_values = [0.1 * (i + 1) for i in range(morph_len)]
            for i in range(min(morph_len, len(morph_values))):
                out_morph[i] = morph_values[i]
        if out_last_report:
            report = out_last_report._obj
            report.tick.input_dt_seconds = float(getattr(dt_seconds, "value", dt_seconds))
            report.tick.clamped_dt_seconds = float(getattr(dt_seconds, "value", dt_seconds))
            report.tick.substeps = count
            report.tick.accumulator_seconds = 0.0
            report.kinematic_rigidbodies_fed = 1
            report.bones_written_back = self.bone_count
        return self.bake_status


class TestNativePhysicsFoundation(unittest.TestCase):
    def test_feature_flags_are_zero_without_library(self):
        with mock.patch.object(rt, "get_mmd_runtime_library", return_value=None):
            self.assertEqual(rt.get_runtime_feature_flags(), 0)
            self.assertFalse(rt.is_native_physics_available())
            self.assertFalse(native_pkg.is_native_physics_available())

    def test_feature_flags_source_of_truth(self):
        split_only = _FakePhysicsLib(feature_flags=rt.MMD_RUNTIME_FEATURE_SPLIT_PHYSICS_EVALUATION)
        with mock.patch.object(rt, "get_mmd_runtime_library", return_value=split_only):
            self.assertEqual(rt.get_runtime_feature_flags(), rt.MMD_RUNTIME_FEATURE_SPLIT_PHYSICS_EVALUATION)
            self.assertFalse(rt.is_native_physics_available())

        full = _FakePhysicsLib(feature_flags=rt.MMD_RUNTIME_REQUIRED_PHYSICS_FEATURE_FLAGS)
        with mock.patch.object(rt, "get_mmd_runtime_library", return_value=full):
            self.assertEqual(rt.get_runtime_feature_flags(), rt.MMD_RUNTIME_REQUIRED_PHYSICS_FEATURE_FLAGS)
            self.assertTrue(rt.is_native_physics_available())

    def test_physics_world_requires_feature_flags(self):
        lib = _FakePhysicsLib(feature_flags=rt.MMD_RUNTIME_FEATURE_SPLIT_PHYSICS_EVALUATION)
        with mock.patch.object(rt, "get_mmd_runtime_library", return_value=lib):
            self.assertIsNone(MmdRuntimePhysicsWorld.from_pmx_bytes(b"pmx"))
        self.assertEqual(lib.create_calls, [])

    def test_physics_world_create_reset_count_and_free(self):
        lib = _FakePhysicsLib(feature_flags=rt.MMD_RUNTIME_REQUIRED_PHYSICS_FEATURE_FLAGS)
        instance = MmdRuntimeInstance(lib, ctypes.c_void_p(0x3003))
        with mock.patch.object(rt, "get_mmd_runtime_library", return_value=lib):
            world = MmdRuntimePhysicsWorld.from_pmx_bytes(b"pmx")

        self.assertIsNotNone(world)
        self.assertEqual(world.handle.value, 0x4404)
        self.assertEqual(lib.create_calls, [b"pmx"])
        self.assertEqual(world.reset(instance), 7)
        self.assertEqual(world.rigidbody_count(), 9)
        world.free()
        self.assertEqual(lib.reset_calls, [(0x4404, 0x3003)])
        self.assertEqual(lib.count_calls, [0x4404])
        self.assertEqual(lib.free_calls, [0x4404])
        self.assertIsNone(world.handle)


class TestNativePhysicsBake(unittest.TestCase):
    """Focused fake-CDLL coverage for sequential physics bake wrapper."""

    def _full_lib(self, **kwargs):
        kwargs.setdefault("feature_flags", rt.MMD_RUNTIME_REQUIRED_PHYSICS_FEATURE_FLAGS)
        return _FakePhysicsLib(**kwargs)

    def _world_and_handles(self, lib):
        world = MmdRuntimePhysicsWorld(lib, ctypes.c_void_p(0x4404))
        instance = MmdRuntimeInstance(lib, ctypes.c_void_p(0x3003))
        clip = MmdRuntimeClip(lib, ctypes.c_void_p(0x2002))
        return world, instance, clip

    def test_prepare_for_sequential_bake_mode_rest_reset_order(self):
        lib = self._full_lib()
        world, instance, _clip = self._world_and_handles(lib)

        self.assertTrue(world.prepare_for_sequential_bake(instance))
        self.assertEqual(lib.set_mode_calls, [(0x3003, rt.MMD_RUNTIME_PHYSICS_MODE_LIVE)])
        self.assertEqual(lib.rest_pose_calls, [0x3003])
        self.assertEqual(lib.reset_calls, [(0x4404, 0x3003)])
        self.assertEqual(instance.get_physics_mode(), rt.MMD_RUNTIME_PHYSICS_MODE_LIVE)

    def test_prepare_fails_without_feature_flags(self):
        lib = _FakePhysicsLib(feature_flags=rt.MMD_RUNTIME_FEATURE_SPLIT_PHYSICS_EVALUATION)
        world, instance, _clip = self._world_and_handles(lib)

        self.assertFalse(world.prepare_for_sequential_bake(instance))
        self.assertEqual(lib.set_mode_calls, [])
        self.assertEqual(lib.rest_pose_calls, [])
        self.assertEqual(lib.reset_calls, [])

    def test_bake_clip_frames_with_physics_contract_and_layout(self):
        frame_count = 3
        bone_count = 2
        morph_count = 1
        world_values = [float(i) for i in range(frame_count * bone_count * 16)]
        morph_values = [0.25 * (i + 1) for i in range(frame_count * morph_count)]
        lib = self._full_lib(
            bone_count=bone_count,
            morph_count=morph_count,
            batch_world_matrices=world_values,
            batch_morph_weights=morph_values,
        )
        world, instance, clip = self._world_and_handles(lib)

        result = world.bake_clip_frames_with_physics(
            instance,
            clip,
            start_frame=0.0,
            frame_step=1.0,
            frame_count=frame_count,
            dt_seconds=1.0 / 30.0,
        )

        self.assertIsInstance(result, MmdRuntimeBatchEvaluation)
        self.assertEqual(result.frame_count, frame_count)
        self.assertEqual(result.bone_count, bone_count)
        self.assertEqual(result.morph_count, morph_count)
        self.assertEqual(len(result.world_matrices), frame_count * bone_count * 16)
        self.assertEqual(len(result.morph_weights), frame_count * morph_count)
        self.assertEqual(list(result.world_matrices), world_values)
        self.assertEqual(list(result.morph_weights), morph_values)

        # LIVE → rest → reset before bake
        self.assertEqual(lib.set_mode_calls, [(0x3003, rt.MMD_RUNTIME_PHYSICS_MODE_LIVE)])
        self.assertEqual(lib.rest_pose_calls, [0x3003])
        self.assertEqual(lib.reset_calls, [(0x4404, 0x3003)])
        self.assertEqual(len(lib.bake_calls), 1)
        call = lib.bake_calls[0]
        self.assertEqual(call[0], 0x4404)  # world
        self.assertEqual(call[1], 0x3003)  # instance
        self.assertEqual(call[2], 0x2002)  # clip
        self.assertEqual(call[3], 0.0)  # start_frame
        self.assertEqual(call[4], 1.0)  # frame_step (VMD units)
        self.assertAlmostEqual(call[5], 1.0 / 30.0)  # explicit dt_seconds
        self.assertEqual(call[6], frame_count)
        self.assertEqual(call[7], frame_count * bone_count * 16)
        self.assertEqual(call[8], frame_count * morph_count)

    def test_bake_accepts_vmd_half_step_with_explicit_maya_60fps_dt(self):
        """VMD frame_step 0.5 at Maya 60fps must pass dt=1/60, not 0.5/60=1/120.

        frame_step is VMD timeline units (fixed 30fps). Maya sampling at 60fps
        advances 0.5 VMD frames per output sample, but real elapsed time is
        1/60 s. The old inferred expression ``frame_step / scene_fps`` wrongly
        yielded 1/120. Callers must pass the actual sample dt in seconds.
        """
        lib = self._full_lib()
        world, instance, clip = self._world_and_handles(lib)
        result = world.bake_clip_frames_with_physics(
            instance,
            clip,
            start_frame=0.0,
            frame_step=0.5,
            frame_count=2,
            dt_seconds=1.0 / 60.0,
            prepare=False,
        )
        self.assertIsNotNone(result)
        self.assertEqual(len(lib.bake_calls), 1)
        call = lib.bake_calls[0]
        self.assertAlmostEqual(call[4], 0.5)  # VMD sample step unchanged
        self.assertAlmostEqual(call[5], 1.0 / 60.0)  # correct wall dt
        # Prove the forbidden inference is NOT what was passed.
        self.assertNotAlmostEqual(call[5], 0.5 / 60.0)

    def test_bake_rejects_non_positive_dt_step_count(self):
        lib = self._full_lib()
        world, instance, clip = self._world_and_handles(lib)

        self.assertIsNone(
            world.bake_clip_frames_with_physics(instance, clip, 0.0, 1.0, 3, dt_seconds=0.0)
        )
        self.assertIsNone(
            world.bake_clip_frames_with_physics(instance, clip, 0.0, 1.0, 3, dt_seconds=-1.0 / 30.0)
        )
        self.assertIsNone(
            world.bake_clip_frames_with_physics(instance, clip, 0.0, 0.0, 3, dt_seconds=1.0 / 30.0)
        )
        self.assertIsNone(
            world.bake_clip_frames_with_physics(instance, clip, 0.0, -1.0, 3, dt_seconds=1.0 / 30.0)
        )
        self.assertIsNone(
            world.bake_clip_frames_with_physics(instance, clip, 0.0, 1.0, 0, dt_seconds=1.0 / 30.0)
        )
        self.assertIsNone(
            world.bake_clip_frames_with_physics(instance, clip, 0.0, 1.0, -1, dt_seconds=1.0 / 30.0)
        )
        self.assertEqual(lib.bake_calls, [])
        # Invalid inputs must not reach prepare either (LIVE / rest / reset).
        self.assertEqual(lib.set_mode_calls, [])
        self.assertEqual(lib.rest_pose_calls, [])
        self.assertEqual(lib.reset_calls, [])

    def test_bake_rejects_non_finite_time_and_dt_inputs(self):
        """NaN / ±inf for dt_seconds, frame_step, start_frame must not call native bake."""
        lib = self._full_lib()
        world, instance, clip = self._world_and_handles(lib)
        cases = [
            # dt_seconds
            {"start_frame": 0.0, "frame_step": 1.0, "frame_count": 2, "dt_seconds": float("inf")},
            {"start_frame": 0.0, "frame_step": 1.0, "frame_count": 2, "dt_seconds": float("-inf")},
            {"start_frame": 0.0, "frame_step": 1.0, "frame_count": 2, "dt_seconds": float("nan")},
            # frame_step
            {"start_frame": 0.0, "frame_step": float("inf"), "frame_count": 2, "dt_seconds": 1.0 / 30.0},
            {"start_frame": 0.0, "frame_step": float("-inf"), "frame_count": 2, "dt_seconds": 1.0 / 30.0},
            {"start_frame": 0.0, "frame_step": float("nan"), "frame_count": 2, "dt_seconds": 1.0 / 30.0},
            # start_frame
            {"start_frame": float("inf"), "frame_step": 1.0, "frame_count": 2, "dt_seconds": 1.0 / 30.0},
            {"start_frame": float("-inf"), "frame_step": 1.0, "frame_count": 2, "dt_seconds": 1.0 / 30.0},
            {"start_frame": float("nan"), "frame_step": 1.0, "frame_count": 2, "dt_seconds": 1.0 / 30.0},
        ]
        for kwargs in cases:
            with self.subTest(**kwargs):
                self.assertIsNone(
                    world.bake_clip_frames_with_physics(instance, clip, **kwargs)
                )
        self.assertEqual(lib.bake_calls, [])
        self.assertEqual(lib.step_calls, [])
        self.assertEqual(lib.set_mode_calls, [])
        self.assertEqual(lib.rest_pose_calls, [])
        self.assertEqual(lib.reset_calls, [])

    def test_bake_rejects_lossy_frame_count_coercion(self):
        """Fractional floats and bools must not be silently truncated into frame_count."""
        lib = self._full_lib()
        world, instance, clip = self._world_and_handles(lib)
        for frame_count in (1.5, 2.9, True, False, 0.0, -3.0, float("inf"), float("nan")):
            with self.subTest(frame_count=frame_count):
                self.assertIsNone(
                    world.bake_clip_frames_with_physics(
                        instance, clip, 0.0, 1.0, frame_count, dt_seconds=1.0 / 30.0
                    )
                )
        # Exact integer-like floats remain acceptable.
        result = world.bake_clip_frames_with_physics(
            instance, clip, 0.0, 1.0, 2.0, dt_seconds=1.0 / 30.0, prepare=False
        )
        self.assertIsNotNone(result)
        self.assertEqual(len(lib.bake_calls), 1)
        self.assertEqual(lib.bake_calls[0][6], 2)

    def test_bake_fail_closed_without_feature_flags(self):
        lib = _FakePhysicsLib(
            feature_flags=rt.MMD_RUNTIME_FEATURE_SPLIT_PHYSICS_EVALUATION,
        )
        world, instance, clip = self._world_and_handles(lib)
        self.assertIsNone(
            world.bake_clip_frames_with_physics(
                instance, clip, 0.0, 1.0, 2, dt_seconds=1.0 / 30.0
            )
        )
        self.assertEqual(lib.bake_calls, [])

    def test_bake_fail_closed_when_bake_symbol_missing(self):
        lib = self._full_lib(missing_symbols={"mmd_runtime_physics_world_bake_clip_frames"})
        world, instance, clip = self._world_and_handles(lib)
        # prepare still runs when bake symbol is missing only after prepare path...
        # bake checks bake_func before prepare when feature flags pass.
        result = world.bake_clip_frames_with_physics(
            instance, clip, 0.0, 1.0, 2, dt_seconds=1.0 / 30.0, prepare=False
        )
        self.assertIsNone(result)
        self.assertEqual(lib.bake_calls, [])

    def test_bake_returns_none_on_unsupported_status(self):
        lib = self._full_lib(bake_status=rt.MMD_RUNTIME_STATUS_UNSUPPORTED)
        world, instance, clip = self._world_and_handles(lib)
        result = world.bake_clip_frames_with_physics(
            instance, clip, 0.0, 1.0, 2, dt_seconds=1.0 / 30.0
        )
        self.assertIsNone(result)
        self.assertEqual(len(lib.bake_calls), 1)

    def test_bake_returns_none_on_error_status(self):
        lib = self._full_lib(bake_status=rt.MMD_RUNTIME_STATUS_ERROR)
        world, instance, clip = self._world_and_handles(lib)
        result = world.bake_clip_frames_with_physics(
            instance, clip, 0.0, 1.0, 2, dt_seconds=1.0 / 30.0
        )
        self.assertIsNone(result)
        self.assertEqual(len(lib.bake_calls), 1)

    def test_bake_skips_prepare_when_requested(self):
        lib = self._full_lib()
        world, instance, clip = self._world_and_handles(lib)
        result = world.bake_clip_frames_with_physics(
            instance,
            clip,
            start_frame=10.0,
            frame_step=2.0,
            frame_count=2,
            dt_seconds=2.0 / 30.0,
            prepare=False,
        )
        self.assertIsNotNone(result)
        self.assertEqual(lib.set_mode_calls, [])
        self.assertEqual(lib.rest_pose_calls, [])
        self.assertEqual(lib.reset_calls, [])
        self.assertAlmostEqual(lib.bake_calls[0][4], 2.0)
        self.assertAlmostEqual(lib.bake_calls[0][5], 2.0 / 30.0)

    def test_step_runtime_contract(self):
        lib = self._full_lib()
        world, instance, _clip = self._world_and_handles(lib)
        report = world.step_runtime(instance, 1.0 / 30.0)
        self.assertIsNotNone(report)
        self.assertAlmostEqual(report.tick.input_dt_seconds, 1.0 / 30.0, places=6)
        self.assertEqual(report.tick.substeps, 2)
        self.assertEqual(report.bones_written_back, 2)
        self.assertEqual(len(lib.step_calls), 1)
        self.assertEqual(lib.step_calls[0][:2], (0x4404, 0x3003))
        self.assertAlmostEqual(lib.step_calls[0][2], 1.0 / 30.0, places=6)

    def test_step_runtime_rejects_non_finite_or_negative_dt(self):
        """NaN / ±inf / negative dt_seconds must not issue native step_runtime."""
        lib = self._full_lib()
        world, instance, _clip = self._world_and_handles(lib)
        for dt in (float("inf"), float("-inf"), float("nan"), -0.001, "not-a-number"):
            with self.subTest(dt=dt):
                self.assertIsNone(world.step_runtime(instance, dt))
        self.assertEqual(lib.step_calls, [])

    def test_step_runtime_fail_closed_without_features(self):
        lib = _FakePhysicsLib(feature_flags=0)
        world, instance, _clip = self._world_and_handles(lib)
        self.assertIsNone(world.step_runtime(instance, 1.0 / 30.0))
        self.assertEqual(lib.step_calls, [])

    def test_explicit_free_after_bake(self):
        lib = self._full_lib()
        world, instance, clip = self._world_and_handles(lib)
        result = world.bake_clip_frames_with_physics(
            instance, clip, 0.0, 1.0, 2, dt_seconds=1.0 / 30.0
        )
        self.assertIsNotNone(result)
        world.free()
        self.assertEqual(lib.free_calls, [0x4404])
        self.assertIsNone(world.handle)
        # Buffers remain usable after world free (caller-owned)
        self.assertEqual(len(result.world_matrices), 2 * 2 * 16)

    def test_set_physics_mode_fail_closed_without_symbol(self):
        lib = self._full_lib(missing_symbols={"mmd_runtime_instance_set_physics_mode"})
        instance = MmdRuntimeInstance(lib, ctypes.c_void_p(0x3003))
        self.assertFalse(instance.set_physics_mode(rt.MMD_RUNTIME_PHYSICS_MODE_LIVE))


if __name__ == "__main__":
    unittest.main()
