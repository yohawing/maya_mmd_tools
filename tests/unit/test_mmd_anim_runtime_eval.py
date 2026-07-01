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
import unittest
from ctypes import c_float, c_uint8
from unittest import mock

import mmd_tools.core.native.mmd_anim_runtime as rt
import mmd_tools.core.native.mmd_anim_runtime_loader as runtime_loader
from mmd_tools.core.native.mmd_anim_runtime import (
    MmdParsedModel,
    MmdRuntimeBatchEvaluation,
    MmdRuntimeClip,
    MmdRuntimeInstance,
    MmdRuntimeModel,
    MmdRuntimeFfiByteBuffer,
    compute_maya_local_channels,
    compute_maya_local_channels_batch,
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
        self.vertex_count = 2
        self.index_count = 3
        self.material_group_count = 1
        self.vertex_morph_count = 1
        self.vertex_morph_offset_count = 2

        self._positions = [(0.0, 0.0, 0.0), (1.0, 2.0, 3.0)]
        self._material_groups = [(0, 3, 5)]
        self._morph_names = ["まばたき"]

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

    def test_parsed_model_from_empty_bytes_returns_none(self):
        runtime_loader._runtime_lib = _FakeParsedLib()
        self.assertIsNone(MmdParsedModel.from_pmx_bytes(b""))


if __name__ == "__main__":
    unittest.main()
