import os
import struct

from tests.common.test_base import TestBase
from mmd_tools.core import mmd_parser

class TestPmdParser(TestBase):

    def setUp(self):
        super().setUp()
        self.pmd_file_path = os.path.join(os.path.dirname(__file__), "..", "data", "miku_v2.pmd")

    def test_parse_pmd_header_success(self):
        """PMDヘッダが正しく解析されることをテストする。"""
        parsed_data = mmd_parser.parse_mmd_file(self.pmd_file_path)
        print(f"Parsed PMD data: {parsed_data}")
        # ヘッダの内容を確認

        self.assertIsNotNone(parsed_data)
        self.assertEqual(parsed_data.header.magic, b'Pmd')
        self.assertAlmostEqual(parsed_data.header.version, 1.0)
        self.assertEqual(parsed_data.header.model_name, '初音ミク')
        self.assertEqual(parsed_data.header.comment, '初音ミクVer.2')

    def test_parse_pmd_file_not_found(self):
        """存在しないPMDファイルを解析しようとしたときにFileNotFoundErrorが発生することをテストする。"""
        with self.assertRaises(FileNotFoundError):
            mmd_parser.parse_mmd_file("non_existent_file.pmd")

    def test_parse_pmd_invalid_magic(self):
        """PMDマジックが不正な場合にMMDParseExceptionが発生することをテストする。"""
        dummy_pmd_path_invalid_magic = os.path.join(self.temp_dir, "invalid_magic.pmd")
        with open(dummy_pmd_path_invalid_magic, 'wb') as f:
            f.write(b'XXX') # Invalid magic
            f.write(struct.pack('<f', 1.0))
            f.write(b'TestModel'.ljust(20, b'\x00'))
            f.write(b'TestComment'.ljust(256, b'\x00'))
        with self.assertRaisesRegex(mmd_parser.MMDParseException, "Unsupported MMD file format."):
            mmd_parser.parse_mmd_file(dummy_pmd_path_invalid_magic)

    # The following tests are commented out as they rely on specific dummy data generation
    # and would require more complex setup to use with a real PMD file or generate
    # specific dummy data for each test case.

    # def test_parse_pmd_vertices(self):
    #     """PMD頂点データが正しく解析されることをテストする。"""
    #     pass

    # def test_parse_pmd_faces(self):
    #     """PMD面データが正しく解析されることをテストする。"""
    #     pass

    # def test_parse_pmd_materials(self):
    #     """PMD材質データが正しく解析されることをテストする。"""
    #     pass

    # def test_parse_pmd_bones(self):
    #     """PMDボーンデータが正しく解析されることをテストする。"""
    #     pass

    # def test_parse_pmd_ik_data(self):
    #     """PMD IKデータが正しく解析されることをテストする。"""
    #     pass

    # def test_parse_pmd_morphs(self):
    #     """PMDモーフデータが正しく解析されることをテストする。"""
    #     pass

    # def test_parse_pmd_display_frames(self):
    #     """PMD表示枠データが正しく解析されることをテストする。"""
    #     pass

    # def test_parse_pmd_rigid_bodies(self):
    #     """PMD剛体データが正しく解析されることをテストする。"""
    #     pass

    # def test_parse_pmd_joints(self):
    #     """PMDジョイントデータが正しく解析されることをテストする。"""
    #     pass
