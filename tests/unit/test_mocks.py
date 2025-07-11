"""
テストモック機能のテストコード
"""
import unittest
import os
import tempfile
from tests.common.pmd_mock import PmdMock
from tests.common.pmx_mock import PmxMock
from tests.common.vmd_mock import VmdMock
from tests.common.test_fixture_provider import TestFixtureProvider


class TestPmdMock(unittest.TestCase):
    """PmdMockクラスのテスト"""
    
    def test_create_minimal_pmd(self):
        """最小限のPMDファイルバイナリデータ生成テスト"""
        data = PmdMock.create_minimal_pmd()
        self.assertIsInstance(data, bytes)
        self.assertGreater(len(data), 0)
        # PMDファイルの識別子をチェック
        self.assertEqual(data[:4], b'Pmd\x00')
    
    def test_create_full_pmd(self):
        """全機能を含むPMDファイルバイナリデータ生成テスト"""
        data = PmdMock.create_full_pmd()
        self.assertIsInstance(data, bytes)
        self.assertGreater(len(data), 0)
        # PMDファイルの識別子をチェック
        self.assertEqual(data[:4], b'Pmd\x00')
    
    def test_create_invalid_pmd(self):
        """不正なPMDファイルバイナリデータ生成テスト"""
        data = PmdMock.create_invalid_pmd()
        self.assertIsInstance(data, bytes)
        self.assertGreater(len(data), 0)
        # 不正なデータであることを確認
        self.assertNotEqual(data[:4], b'Pmd\x00')
    
    def test_create_custom_pmd(self):
        """カスタムPMDファイルバイナリデータ生成テスト"""
        data = PmdMock.create_custom_pmd(
            vertex_count=16,
            face_count=24,
            bone_count=5
        )
        self.assertIsInstance(data, bytes)
        self.assertGreater(len(data), 0)
        # PMDファイルの識別子をチェック
        self.assertEqual(data[:4], b'Pmd\x00')


class TestPmxMock(unittest.TestCase):
    """PmxMockクラスのテスト"""
    
    def test_create_minimal_pmx(self):
        """最小限のPMXファイルバイナリデータ生成テスト"""
        data = PmxMock.create_minimal_pmx()
        self.assertIsInstance(data, bytes)
        self.assertGreater(len(data), 0)
        # PMXファイルの識別子をチェック
        self.assertEqual(data[:4], b'PMX ')
    
    def test_create_minimal_pmx_with_version(self):
        """バージョン指定でのPMXファイルバイナリデータ生成テスト"""
        data = PmxMock.create_minimal_pmx(version=2.1)
        self.assertIsInstance(data, bytes)
        self.assertGreater(len(data), 0)
        # PMXファイルの識別子をチェック
        self.assertEqual(data[:4], b'PMX ')
    
    def test_create_full_pmx(self):
        """全機能を含むPMXファイルバイナリデータ生成テスト"""
        data = PmxMock.create_full_pmx()
        self.assertIsInstance(data, bytes)
        self.assertGreater(len(data), 0)
        # PMXファイルの識別子をチェック
        self.assertEqual(data[:4], b'PMX ')
    
    def test_create_invalid_pmx(self):
        """不正なPMXファイルバイナリデータ生成テスト"""
        data = PmxMock.create_invalid_pmx()
        self.assertIsInstance(data, bytes)
        self.assertGreater(len(data), 0)
        # 不正なデータであることを確認
        self.assertNotEqual(data[:4], b'PMX ')
    
    def test_create_custom_pmx(self):
        """カスタムPMXファイルバイナリデータ生成テスト"""
        data = PmxMock.create_custom_pmx(
            version=2.1,
            encoding=1,
            vertex_count=16,
            bone_count=5
        )
        self.assertIsInstance(data, bytes)
        self.assertGreater(len(data), 0)
        # PMXファイルの識別子をチェック
        self.assertEqual(data[:4], b'PMX ')


class TestVmdMock(unittest.TestCase):
    """VmdMockクラスのテスト"""
    
    def test_create_minimal_vmd(self):
        """最小限のVMDファイルバイナリデータ生成テスト"""
        data = VmdMock.create_minimal_vmd()
        self.assertIsInstance(data, bytes)
        self.assertGreater(len(data), 0)
        # VMDファイルの識別子をチェック
        self.assertTrue(data.startswith(b'Vocaloid Motion Data'))
    
    def test_create_full_vmd(self):
        """全機能を含むVMDファイルバイナリデータ生成テスト"""
        data = VmdMock.create_full_vmd()
        self.assertIsInstance(data, bytes)
        self.assertGreater(len(data), 0)
        # VMDファイルの識別子をチェック
        self.assertTrue(data.startswith(b'Vocaloid Motion Data'))
    
    def test_create_camera_vmd(self):
        """カメラVMDファイルバイナリデータ生成テスト"""
        data = VmdMock.create_camera_vmd()
        self.assertIsInstance(data, bytes)
        self.assertGreater(len(data), 0)
        # VMDファイルの識別子をチェック
        self.assertTrue(data.startswith(b'Vocaloid Motion Data'))
    
    def test_create_invalid_vmd(self):
        """不正なVMDファイルバイナリデータ生成テスト"""
        data = VmdMock.create_invalid_vmd()
        self.assertIsInstance(data, bytes)
        self.assertGreater(len(data), 0)
        # 不正なデータであることを確認
        self.assertFalse(data.startswith(b'Vocaloid Motion Data'))
    
    def test_create_custom_vmd(self):
        """カスタムVMDファイルバイナリデータ生成テスト"""
        data = VmdMock.create_custom_vmd(
            model_name="CustomModel",
            bone_frame_count=20,
            morph_frame_count=10
        )
        self.assertIsInstance(data, bytes)
        self.assertGreater(len(data), 0)
        # VMDファイルの識別子をチェック
        self.assertTrue(data.startswith(b'Vocaloid Motion Data'))


class TestTestFixtureProvider(unittest.TestCase):
    """TestFixtureProviderクラスのテスト"""
    
    def setUp(self):
        """テストセットアップ"""
        # テスト用の一時ディレクトリを作成
        self.temp_dir = tempfile.mkdtemp()
        self.provider = TestFixtureProvider(self.temp_dir)
    
    def tearDown(self):
        """テストクリーンアップ"""
        self.provider.cleanup_temp_files()
        # 一時ディレクトリを削除
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_init_with_default_data_dir(self):
        """デフォルトデータディレクトリでの初期化テスト"""
        provider = TestFixtureProvider()
        self.assertIsNotNone(provider._data_dir)
        self.assertTrue(provider._data_dir.endswith('data'))
    
    def test_init_with_custom_data_dir(self):
        """カスタムデータディレクトリでの初期化テスト"""
        provider = TestFixtureProvider(self.temp_dir)
        self.assertEqual(provider._data_dir, self.temp_dir)
    
    def test_get_available_files_empty(self):
        """利用可能ファイルリスト取得テスト（空の場合）"""
        pmd_files = self.provider.get_available_pmd_files()
        pmx_files = self.provider.get_available_pmx_files()
        vmd_files = self.provider.get_available_vmd_files()
        
        self.assertEqual(len(pmd_files), 0)
        self.assertEqual(len(pmx_files), 0)
        self.assertEqual(len(vmd_files), 0)
    
    def test_get_file_not_found(self):
        """ファイルが見つからない場合のテスト"""
        with self.assertRaises(FileNotFoundError):
            self.provider.get_pmd_file()
        
        with self.assertRaises(FileNotFoundError):
            self.provider.get_pmx_file()
        
        with self.assertRaises(FileNotFoundError):
            self.provider.get_vmd_file()
    
    def test_create_temp_file(self):
        """一時ファイル作成テスト"""
        content = b'test content'
        temp_path = self.provider.create_temp_file(content, '.test')
        
        self.assertTrue(os.path.exists(temp_path))
        self.assertTrue(temp_path.endswith('.test'))
        
        # ファイル内容を確認
        with open(temp_path, 'rb') as f:
            self.assertEqual(f.read(), content)
    
    def test_cleanup_temp_files(self):
        """一時ファイルクリーンアップテスト"""
        content = b'test content'
        temp_path = self.provider.create_temp_file(content, '.test')
        
        # ファイルが存在することを確認
        self.assertTrue(os.path.exists(temp_path))
        
        # クリーンアップ実行
        self.provider.cleanup_temp_files()
        
        # ファイルが削除されたことを確認
        self.assertFalse(os.path.exists(temp_path))
    
    def test_file_cache_with_real_data(self):
        """実際のデータディレクトリでのファイルキャッシュテスト"""
        # 実際のデータディレクトリでテスト
        real_provider = TestFixtureProvider()
        
        # 利用可能なファイルを取得
        pmd_files = real_provider.get_available_pmd_files()
        pmx_files = real_provider.get_available_pmx_files()
        vmd_files = real_provider.get_available_vmd_files()
        
        # ファイルがある場合のテスト
        if pmd_files:
            file_path = real_provider.get_pmd_file()
            self.assertTrue(os.path.exists(file_path))
            self.assertTrue(file_path.endswith('.pmd'))
        
        if pmx_files:
            file_path = real_provider.get_pmx_file()
            self.assertTrue(os.path.exists(file_path))
            self.assertTrue(file_path.endswith('.pmx'))
        
        if vmd_files:
            file_path = real_provider.get_vmd_file()
            self.assertTrue(os.path.exists(file_path))
            self.assertTrue(file_path.endswith('.vmd'))


if __name__ == '__main__':
    unittest.main()