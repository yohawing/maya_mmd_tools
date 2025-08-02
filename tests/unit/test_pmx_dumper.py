"""
PMXダンパーのユニットテスト
"""

import os
import sys
import tempfile
import unittest
from io import StringIO

sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))

from mmd_tools.core.pmx_parser import PmxParser
from mmd_tools.tools.pmx_dumper import PmxDumper
from tests.common.pmx_mock import PmxMock


class TestPmxDumper(unittest.TestCase):
    """PMXダンパーのテストクラス"""

    def setUp(self):
        """テストの前処理"""
        # モックPMXファイルを作成
        pmx_data = PmxMock.create_minimal_pmx()
        
        # 一時ファイルに書き込み
        with tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix='.pmx') as f:
            f.write(pmx_data)
            self.test_pmx_path = f.name
        
        # PMXファイルを読み込む
        self.pmx_parser = PmxParser()
        self.pmx_parser.parse_file(self.test_pmx_path)
        self.dumper = PmxDumper(self.pmx_parser)
    
    def tearDown(self):
        """テストの後処理"""
        # 一時ファイルを削除
        if hasattr(self, 'test_pmx_path') and os.path.exists(self.test_pmx_path):
            os.unlink(self.test_pmx_path)

    def test_dump_header(self):
        """ヘッダー情報のダンプテスト"""
        result = self.dumper.dump(sections=["header"])
        
        # 基本的な内容を確認
        self.assertIn("=== PMX MODEL DEBUG DUMP ===", result)
        self.assertIn("Version:", result)
        self.assertIn("Encoding:", result)
        self.assertIn("Model:", result)
        self.assertIn("Comment:", result)

    def test_dump_statistics(self):
        """統計情報のダンプテスト"""
        result = self.dumper.dump(sections=["statistics"])
        
        # 統計情報の確認
        self.assertIn("=== STATISTICS ===", result)
        self.assertIn("Vertices:", result)
        self.assertIn("Faces:", result)
        self.assertIn("Materials:", result)
        self.assertIn("Bones:", result)
        self.assertIn("Morphs:", result)

    def test_dump_bones(self):
        """ボーン情報のダンプテスト"""
        result = self.dumper.dump(sections=["bones"])
        
        # ボーン階層の確認
        self.assertIn("=== BONE HIERARCHY", result)
        if self.pmx_parser.bones:
            # ボーンが存在する場合の確認
            self.assertIn("[0]", result)  # インデックス表示

    def test_dump_morphs(self):
        """モーフ情報のダンプテスト"""
        result = self.dumper.dump(sections=["morphs"])
        
        # モーフ情報の確認
        self.assertIn("=== MORPHS", result)
        if self.pmx_parser.morphs:
            self.assertIn("Morph Types:", result)

    def test_dump_materials(self):
        """材質情報のダンプテスト"""
        result = self.dumper.dump(sections=["materials"])
        
        # 材質情報の確認
        self.assertIn("=== MATERIALS", result)
        if self.pmx_parser.materials:
            self.assertIn("Diffuse:", result)
            self.assertIn("Specular:", result)

    def test_dump_physics(self):
        """物理演算情報のダンプテスト"""
        result = self.dumper.dump(sections=["physics"])
        
        # 物理演算情報の確認
        self.assertIn("=== PHYSICS ===", result)
        self.assertIn("Rigid Bodies:", result)
        self.assertIn("Joints:", result)

    def test_dump_vertices(self):
        """頂点情報のダンプテスト"""
        result = self.dumper.dump(sections=["vertices"])
        
        if self.pmx_parser.vertices:
            # 頂点情報の確認
            self.assertIn("=== VERTICES", result)
            self.assertIn("Position Range:", result)
            self.assertIn("Vertex Samples:", result)

    def test_dump_all_sections(self):
        """全セクションのダンプテスト"""
        result = self.dumper.dump()
        
        # デフォルトセクションが含まれているか確認
        self.assertIn("=== PMX MODEL DEBUG DUMP ===", result)
        self.assertIn("=== STATISTICS ===", result)
        self.assertIn("=== BONE HIERARCHY", result)
        self.assertIn("=== MORPHS", result)
        self.assertIn("=== MATERIALS", result)
        self.assertIn("=== PHYSICS ===", result)

    def test_dump_to_file(self):
        """ファイルへの出力テスト"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8') as f:
            temp_path = f.name
            self.dumper.dump(output=f)
        
        # ファイルが作成されたか確認
        self.assertTrue(os.path.exists(temp_path))
        
        # 内容を確認
        with open(temp_path, 'r', encoding='utf-8') as f:
            content = f.read()
            self.assertIn("=== PMX MODEL DEBUG DUMP ===", content)
        
        # クリーンアップ
        os.unlink(temp_path)

    def test_dump_with_error_handling(self):
        """エラーハンドリングのテスト"""
        # 不正なデータでダンパーを作成（属性を削除）
        dumper = PmxDumper(self.pmx_parser)
        
        # ヘッダーに存在しない属性を参照するようにして、エラーを発生させる
        del dumper.pmx.header.model_name_english
        
        result = dumper.dump(sections=["header"])
        
        # エラーが適切に処理されているか確認
        self.assertIn("[ERROR]", result)

    def test_header_contains_index_sizes(self):
        """ヘッダー情報にインデックスサイズが含まれているかテスト"""
        result = self.dumper.dump(sections=["header"])
        
        # インデックスサイズ情報が含まれているか確認
        self.assertIn("Index Sizes:", result)
        self.assertIn("Vertex:", result)
        self.assertIn("Texture:", result)
        self.assertIn("Material:", result)
        self.assertIn("Bone:", result)
        self.assertIn("Morph:", result)
        self.assertIn("Rigid Body:", result)


if __name__ == "__main__":
    unittest.main()