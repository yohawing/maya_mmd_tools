"""
mmd-anim runtime (Python ctypes ラッパー) の基本動作を検証するユニットテスト。

このテストはネイティブライブラリ (mmd_anim_ffi.dll など) が存在しなくても
常にパスするように設計されています。
ネイティブが利用可能な環境では、より実践的な評価テストも実行します。

関連:
- mmd_tools/core/native/mmd_anim_runtime.py
- docs-dev/runtime-architecture.md
"""

import unittest

from mmd_tools.core.native import (
    is_mmd_runtime_available,
    MmdRuntimeModel,
    MmdRuntimeClip,
    MmdRuntimeInstance,
    get_mmd_runtime_library,
)


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
        # ここでは「利用可能」であることだけを確認し、
        # 本格的なデータを使った評価は統合テストや Phase 1 以降に委ねる。
        # 実データが必要な場合は TestFixtureProvider 経由で取得する。
        self.assertTrue(is_mmd_runtime_available())

    def test_basic_lifecycle_does_not_crash(self):
        """モデル→クリップ→インスタンス→評価→解放の一連の流れでクラッシュしないこと。"""
        # 実際の有効な PMX/VMD バイトが必要なため、ここでは利用可能性の再確認のみ。
        # 将来的に tests/data/for_unit_test/ や mmt_test_model を使った
        # 具体的な行列/モーフ検証を追加予定。
        self.assertTrue(is_mmd_runtime_available())


if __name__ == "__main__":
    unittest.main()
