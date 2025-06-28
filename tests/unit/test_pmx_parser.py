import os

from tests.common.test_base import TestBase
from mmd_tools.core import mmd_parser

class TestPmxParser(TestBase):

    def setUp(self):
        super().setUp()
        self.dummy_pmx_path = os.path.join(self.temp_dir, "test_model.pmx")
        self.pmx_file_path = os.path.join(os.path.dirname(__file__), "..", "data", "Lumine.pmx")

    def tearDown(self):
        super().tearDown()
        if os.path.exists(self.dummy_pmx_path):
            os.remove(self.dummy_pmx_path)

    def _create_dummy_pmx_file(self, magic=b'PMX ', version=2.0, global_flags=0, model_name_jp='TestModelJP', model_name_en='TestModelEN', comment_jp='TestCommentJP', comment_en='TestCommentEN', vertices=None):
        """
        テスト用にダミーのPMXファイルを生成するヘルパー関数。
        実際のPMXファイル構造に合わせてバイナリデータを書き込む。
        """
        # TODO: 実際のPMXファイル構造に基づいてバイナリデータを書き込むロジックを実装する。
        # magic, version, global_flags, text_encoding, num_uv_sets, etc.
        # model_name_jp, model_name_en, comment_jp, comment_en
        # vertices (数、各頂点データ)
        pass
    

    def test_parse_pmx_header_success(self):
        """PMXヘッダが正しく解析されることをテストする。"""

        # TODO: mmd_parser.parse_mmd_fileを呼び出し、解析結果を取得する。
        parsed_data = mmd_parser.parse_mmd_file(self.pmx_file_path)

        # TODO: 解析結果が期待通りであることをアサートする。
        self.assertIsNotNone(parsed_data)
        self.assertEqual(parsed_data.header.magic, b'PMX ')
        self.assertAlmostEqual(parsed_data.header.version, 2.0)
        self.assertEqual(parsed_data.header.model_name, 'PlayerGirl')
        self.assertEqual(parsed_data.header.model_name_english, '')
        # self.assertEqual(parsed_data.header.comment, 'テストコメント')
        # self.assertEqual(parsed_data.header.comment_english, '')
        self.assertEqual(parsed_data.header.text_encoding, 'utf-16-le')
        self.assertEqual(parsed_data.header.additional_uv, 0)
        self.assertEqual(parsed_data.header.vertex_index_size, 2)
        self.assertEqual(parsed_data.header.texture_index_size, 1)
        self.assertEqual(parsed_data.header.material_index_size, 1)
        self.assertEqual(parsed_data.header.bone_index_size, 2)
        self.assertEqual(parsed_data.header.morph_index_size, 1)

        pass
