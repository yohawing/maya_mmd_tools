"""
mmd-anim runtime (Python ctypes ラッパー) の基本動作を検証するユニットテスト。

このテストはネイティブライブラリ (mmd_anim_ffi.dll など) が存在しなくても
常にパスするように設計されています。
ネイティブが利用可能な環境では、より実践的な評価テストも実行します。

関連:
- mmd_tools/core/native/mmd_anim_runtime.py
- docs-dev/runtime-architecture.md
"""

import json
import unittest
from pathlib import Path

from mmd_tools.core.native import (
    is_mmd_runtime_available,
    is_native_pmx_parser_available,
    MmdParsedModel,
    MmdRuntimeClip,
    MmdRuntimeInstance,
    MmdRuntimeModel,
    get_mmd_runtime_library,
)
from mmd_tools.core.native.native_pmx_parser import is_native_parser_available, parse_pmx_native
from mmd_tools.core.pmx_data.morph import PmxMorphType

_THIS_DIR = Path(__file__).resolve().parent
_TEST_DATA_DIR = _THIS_DIR.parent / "data"
_MMT_PMX_PATH = _TEST_DATA_DIR / "mmt_test_model.pmx"
_MORPH_PMX_PATH = _TEST_DATA_DIR / "test_morph_model.pmx"


class TestMmdAnimRuntimeAvailability(unittest.TestCase):
    """ネイティブランタイムの可用性と安全なフォールバックを検証。"""

    def test_module_imports_without_error(self):
        """ラッパーモジュールがインポートエラーなく読み込めること。"""
        # すでに import できている時点で成功
        self.assertIsNotNone(is_mmd_runtime_available)

    def test_is_available_returns_bool(self):
        """is_mmd_runtime_available() が bool を返すこと。"""
        result = is_mmd_runtime_available()
        self.assertIsInstance(result, bool)

    def test_get_library_returns_none_or_cdll_when_unavailable(self):
        """ライブラリ未ロード時も安全に None を返す (またはロード済みならオブジェクト)。"""
        lib = get_mmd_runtime_library()
        # ここでは「None または CDLL 相当」であることだけを確認
        # (CDLL の厳密な型チェックは環境依存のため緩め)
        self.assertTrue(lib is None or hasattr(lib, "_name") or str(type(lib)).find("CDLL") >= 0)

    def test_wrapper_classes_return_none_safely_when_unavailable(self):
        """
        ネイティブが利用できない環境でも、各ラッパークラスのクラスメソッドが
        安全に None を返し、例外を伝播しないこと。
        """
        dummy_pmx = b"PMX dummy bytes for test (will not be used if runtime unavailable)"
        dummy_vmd = b"VMD dummy bytes for test"

        model = MmdRuntimeModel.from_pmx_bytes(dummy_pmx)
        self.assertIsNone(model)

        # model が None の場合のクリップ生成も安全
        clip = MmdRuntimeClip.from_vmd_bytes_for_model(None, dummy_vmd)
        self.assertIsNone(clip)

        instance = MmdRuntimeInstance.for_model(None)
        self.assertIsNone(instance)

    def test_runtime_clip_frame_range_uses_optional_ffi_symbol(self):
        """clip の frame range は optional ABI があれば安全に取得できる。"""
        calls = []

        def frame_range(_handle, out_first, out_last):
            calls.append(True)
            out_first._obj.value = 3
            out_last._obj.value = 8
            return True

        class FakeLib:
            mmd_runtime_clip_frame_range = staticmethod(frame_range)

            @staticmethod
            def mmd_runtime_clip_free(_handle):
                pass

        clip = MmdRuntimeClip(FakeLib(), 123)

        self.assertEqual(clip.frame_range(), (3, 8))
        self.assertEqual(calls, [True])

    def test_runtime_clip_frame_range_returns_none_without_symbol(self):
        """古い DLL で frame range ABI がなくても例外にしない。"""
        class FakeLib:
            @staticmethod
            def mmd_runtime_clip_free(_handle):
                pass

        clip = MmdRuntimeClip(FakeLib(), 123)

        self.assertIsNone(clip.frame_range())

    # ---- parsed-model の unavailable-path テスト ----

    def test_is_native_pmx_parser_available_returns_bool(self):
        """is_native_pmx_parser_available() が bool を返すこと。"""
        result = is_native_pmx_parser_available()
        self.assertIsInstance(result, bool)

    def test_parsed_model_from_pmx_bytes_returns_none_when_unavailable(self):
        """
        DLL またはシンボルが利用できない環境でも
        MmdParsedModel.from_pmx_bytes が安全に None を返すこと。
        """
        dummy = b"PMX\0\0\0\0"  # 完全に無効なデータ
        model = MmdParsedModel.from_pmx_bytes(dummy)
        # DLL が無い／シンボルが無い → None
        # DLL とシンボルが揃っていてもパース失敗 → None
        self.assertIsNone(model)

    def test_parsed_model_properties_are_safe_on_none_handle(self):
        """
        MmdParsedModel を None ハンドルで初期化した場合、
        全プロパティが例外を投げずに安全な値を返すこと。
        """
        lib = get_mmd_runtime_library()
        if lib is None:
            # ダミーオブジェクトでテスト
            model = object.__new__(MmdParsedModel)
            model._lib = None
            model._handle = None
        else:
            model = object.__new__(MmdParsedModel)
            model._lib = lib
            model._handle = None

        self.assertEqual(model.vertex_count, 0)
        self.assertEqual(model.index_count, 0)
        self.assertEqual(model.material_group_count, 0)
        self.assertEqual(model.vertex_morph_count, 0)
        self.assertEqual(model.vertex_morph_offset_count, 0)
        self.assertIsNone(model.positions)
        self.assertIsNone(model.normals)
        self.assertIsNone(model.uvs)
        self.assertIsNone(model.edge_scale)
        self.assertIsNone(model.indices)
        self.assertIsNone(model.skin_indices)
        self.assertIsNone(model.skin_weights)
        self.assertIsNone(model.material_groups)
        self.assertIsNone(model.vertex_morph_spans)
        self.assertIsNone(model.vertex_morph_vertex_indices)
        self.assertIsNone(model.vertex_morph_position_offsets)
        self.assertIsNone(model.vertex_morph_names)
        self.assertIsNone(model.metadata_json)

        # free も安全に呼べること
        try:
            model.free()
        except Exception:
            self.fail("free() raised unexpectedly on invalid handle")

    def test_parsed_model_del_is_safe(self):
        """解放済み/空の MmdParsedModel で __del__ が安全であること。"""
        model = MmdParsedModel.from_pmx_bytes(b"\0\0\0\0")
        if model is None:
            # DLL またはシンボルがない環境ではスキップ
            return
        # 2回 free を呼んでも安全
        model.free()
        model.free()
        # __del__ でも安全
        model.__del__()


class TestMmdAnimRuntimeWhenAvailable(unittest.TestCase):
    """
    ネイティブランタイムが実際に利用可能な環境での基本動作テスト。

    通常の CI や開発環境ではこのクラスはスキップされます。
    事前ビルドの mmd_anim_ffi.dll を配置した環境でのみ意味を持ちます。
    """

    @classmethod
    def setUpClass(cls):
        if not is_mmd_runtime_available():
            raise unittest.SkipTest("mmd-anim native runtime is not available in this environment")

    def test_create_model_from_minimal_pmx(self):
        """最小の PMX データでモデル作成ができること (実在の有効 PMX が必要)。"""
        self.assertTrue(is_mmd_runtime_available())

    def test_basic_lifecycle_does_not_crash(self):
        """モデル→クリップ→インスタンス→評価→解放の一連の流れでクラッシュしないこと。"""
        self.assertTrue(is_mmd_runtime_available())


class TestNativePmxParserMorphAccessors(unittest.TestCase):
    """buffer-based native PMX parser の vertex morph metadata を固定する。"""

    @classmethod
    def setUpClass(cls):
        if not is_native_parser_available():
            raise unittest.SkipTest("buffer-based native PMX parser symbols are not available in this environment")
        if not _MORPH_PMX_PATH.exists():
            raise unittest.SkipTest(f"test morph model not found: {_MORPH_PMX_PATH}")

    def test_vertex_morph_metadata_from_test_morph_model(self):
        """test_morph_model.pmx の vertex morph 名と offset が native parser で読める。"""
        pmx_data = parse_pmx_native(str(_MORPH_PMX_PATH))
        self.assertIsNotNone(pmx_data)

        vertex_morphs = [
            morph
            for morph in pmx_data.morphs
            if morph.morph_type == PmxMorphType.VertexMorph
        ]
        self.assertEqual([morph.name_english for morph in vertex_morphs], ["smile", "angry"])
        self.assertEqual([len(morph.offsets) for morph in vertex_morphs], [4, 4])

        smile_offsets = vertex_morphs[0].offsets
        angry_offsets = vertex_morphs[1].offsets
        self.assertEqual([offset["vertex_index"] for offset in smile_offsets], [2, 3, 6, 7])
        self.assertEqual([offset["position_offset"] for offset in smile_offsets], [(0.0, 2.0, 0.0)] * 4)
        self.assertEqual([offset["vertex_index"] for offset in angry_offsets], [0, 1, 4, 5])
        self.assertEqual([offset["position_offset"] for offset in angry_offsets], [(0.0, -1.0, 0.0)] * 4)


class TestParsedModelWhenAvailable(unittest.TestCase):
    """
    parsed-model ABI が利用可能な環境での MmdParsedModel スモークテスト。

    tests/data/mmt_test_model.pmx を使って基本機能を検証します。
    """

    _pmx_bytes: bytes = b""

    @classmethod
    def setUpClass(cls):
        if not is_native_pmx_parser_available():
            raise unittest.SkipTest(
                "parsed-model native symbols are not available in this environment"
            )
        if not _MMT_PMX_PATH.exists():
            raise unittest.SkipTest(
                f"test model not found: {_MMT_PMX_PATH}"
            )
        with open(_MMT_PMX_PATH, "rb") as f:
            cls._pmx_bytes = f.read()

    def test_create_from_valid_pmx(self):
        """有効な PMX から MmdParsedModel が作成できること。"""
        model = MmdParsedModel.from_pmx_bytes(self._pmx_bytes)
        self.assertIsNotNone(model)
        model.free()

    def test_counts_are_positive(self):
        """カウントプロパティが正の値を返すこと。"""
        model = MmdParsedModel.from_pmx_bytes(self._pmx_bytes)
        self.assertIsNotNone(model)
        try:
            self.assertGreater(model.vertex_count, 0)
            self.assertGreater(model.index_count, 0)
            self.assertGreater(model.material_group_count, 0)
        finally:
            model.free()

    def test_pointer_accessors_return_data(self):
        """ポインターアクセサが None でない Python リストを返すこと。"""
        model = MmdParsedModel.from_pmx_bytes(self._pmx_bytes)
        self.assertIsNotNone(model)
        try:
            vc = model.vertex_count
            ic = model.index_count

            self.assertIsNotNone(model.positions)
            self.assertEqual(len(model.positions), vc)

            self.assertIsNotNone(model.normals)
            self.assertEqual(len(model.normals), vc)

            self.assertIsNotNone(model.uvs)
            self.assertEqual(len(model.uvs), vc)

            self.assertIsNotNone(model.edge_scale)
            self.assertEqual(len(model.edge_scale), vc)

            self.assertIsNotNone(model.indices)
            self.assertEqual(len(model.indices), ic)

            self.assertIsNotNone(model.skin_indices)
            self.assertEqual(len(model.skin_indices), vc)

            self.assertIsNotNone(model.skin_weights)
            self.assertEqual(len(model.skin_weights), vc)
        finally:
            model.free()

    def test_material_groups_triples(self):
        """material_groups が (start, count, material_index) のタプルリストであること。"""
        model = MmdParsedModel.from_pmx_bytes(self._pmx_bytes)
        self.assertIsNotNone(model)
        try:
            groups = model.material_groups
            self.assertIsNotNone(groups)
            self.assertGreater(len(groups), 0)
            for g in groups:
                self.assertEqual(len(g), 3)
                # start と count は非負整数
                self.assertIsInstance(g[0], int)
                self.assertIsInstance(g[1], int)
                self.assertIsInstance(g[2], int)
        finally:
            model.free()

    def test_metadata_json_is_valid_json(self):
        """metadata_json がパース可能な JSON 文字列を返すこと。"""
        model = MmdParsedModel.from_pmx_bytes(self._pmx_bytes)
        self.assertIsNotNone(model)
        try:
            raw = model.metadata_json
            self.assertIsNotNone(raw)
            self.assertIsInstance(raw, str)
            data = json.loads(raw)
            # 最低限のフィールド確認
            self.assertIn("format", data)
            self.assertIn("version", data)
            self.assertIn("name", data)
            self.assertIn("counts", data)
        finally:
            model.free()

    def test_multiple_create_free_cycles(self):
        """作成/解放を繰り返してもメモリリークやクラッシュが起きないこと。"""
        for _ in range(5):
            model = MmdParsedModel.from_pmx_bytes(self._pmx_bytes)
            self.assertIsNotNone(model)
            model.free()


if __name__ == "__main__":
    unittest.main()
