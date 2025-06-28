import os

from tests.common.test_base import TestBase
from mmd_tools.core import mmd_parser

class TestMmdParser(TestBase):

    def setUp(self):
        super().setUp()
        self.dummy_pmd_path = os.path.join(self.temp_dir, "test_model.pmd")
        self.dummy_pmx_path = os.path.join(self.temp_dir, "test_model.pmx")
        self.dummy_vmd_path = os.path.join(self.temp_dir, "test_motion.vmd")

    def tearDown(self):
        super().tearDown()
        if os.path.exists(self.dummy_pmd_path):
            os.remove(self.dummy_pmd_path)
        if os.path.exists(self.dummy_pmx_path):
            os.remove(self.dummy_pmx_path)
        if os.path.exists(self.dummy_vmd_path):
            os.remove(self.dummy_vmd_path)

    def _create_dummy_pmd_file(self, magic=b'Pmd', version=1.0, model_name='TestModel', comment='TestComment'):
        import struct
        with open(self.dummy_pmd_path, 'wb') as f:
            f.write(magic)
            f.write(struct.pack('<f', version))
            f.write(model_name.encode('shift_jis').ljust(20, b'\x00'))
            f.write(comment.encode('shift_jis').ljust(256, b'\x00'))
            f.write(struct.pack('<I', 0)) # vertices
            f.write(struct.pack('<I', 0)) # faces
            f.write(struct.pack('<I', 0)) # materials
            f.write(struct.pack('<H', 0)) # bones
            f.write(struct.pack('<H', 0)) # ik_data
            f.write(struct.pack('<H', 0)) # morphs
            f.write(struct.pack('<B', 0)) # display_frames
            f.write(struct.pack('<B', 0)) # has_english_header
            for _ in range(10):
                f.write(b''.ljust(100, b'\x00')) # toon_textures
            f.write(struct.pack('<I', 0)) # rigid_bodies
            f.write(struct.pack('<I', 0)) # joints

    def _create_dummy_pmx_file(self, magic=b'PMX ', version=2.0, global_flags=0, model_name_jp='TestModelJP', model_name_en='TestModelEN', comment_jp='TestCommentJP', comment_en='TestCommentEN'):
        import struct
        with open(self.dummy_pmx_path, 'wb') as f:
            f.write(magic)
            f.write(struct.pack('<f', version))
            f.write(struct.pack('<B', global_flags)) # global_flags
            f.write(struct.pack('<B', 0)) # text_encoding
            f.write(struct.pack('<B', 0)) # num_uv_sets
            f.write(struct.pack('<B', 0)) # vertex_index_size
            f.write(struct.pack('<B', 0)) # texture_index_size
            f.write(struct.pack('<B', 0)) # material_index_size
            f.write(struct.pack('<B', 0)) # bone_index_size
            f.write(struct.pack('<B', 0)) # morph_index_size
            f.write(struct.pack('<B', 0)) # rigid_body_index_size
            f.write(struct.pack('<I', len(model_name_jp.encode('utf-16-le')))) # model_name_jp length
            f.write(model_name_jp.encode('utf-16-le'))
            f.write(struct.pack('<I', len(model_name_en.encode('utf-16-le')))) # model_name_en length
            f.write(model_name_en.encode('utf-16-le'))
            f.write(struct.pack('<I', len(comment_jp.encode('utf-16-le')))) # comment_jp length
            f.write(comment_jp.encode('utf-16-le'))
            f.write(struct.pack('<I', len(comment_en.encode('utf-16-le')))) # comment_en length
            f.write(comment_en.encode('utf-16-le'))
            f.write(struct.pack('<I', 0)) # vertices

    def _create_dummy_vmd_file(self, magic=b'Vocaloid Motion Data file', version=2.0, model_name='TestModel'):
        import struct
        with open(self.dummy_vmd_path, 'wb') as f:
            f.write(magic.ljust(30, b'\x00'))
            f.write(struct.pack('<f', version))
            f.write(model_name.encode('shift_jis').ljust(20, b'\x00'))
            f.write(struct.pack('<I', 0)) # bone_frames
            f.write(struct.pack('<I', 0)) # morph_frames
            f.write(struct.pack('<I', 0)) # camera_frames
            f.write(struct.pack('<I', 0)) # light_frames
            if version >= 2.0:
                f.write(struct.pack('<I', 0)) # shadow_frames
                f.write(struct.pack('<I', 0)) # ik_show_hide_frames

    def test_parse_mmd_file_pmd_type(self):
        """mmd_parser.parse_mmd_fileがPMDファイルを正しく識別することをテストする。"""
        self._create_dummy_pmd_file()
        parsed_data = mmd_parser.parse_mmd_file(self.dummy_pmd_path)
        self.assertIsInstance(parsed_data, mmd_parser.PmdParser)

    def test_parse_mmd_file_pmx_type(self):
        """mmd_parser.parse_mmd_fileがPMXファイルを正しく識別することをテストする。"""
        self._create_dummy_pmx_file()
        parsed_data = mmd_parser.parse_mmd_file(self.dummy_pmx_path)
        self.assertIsInstance(parsed_data, mmd_parser.PmxParser)

    def test_parse_mmd_file_vmd_type(self):
        """mmd_parser.parse_mmd_fileがVMDファイルを正しく識別することをテストする。"""
        self._create_dummy_vmd_file()
        parsed_data = mmd_parser.parse_mmd_file(self.dummy_vmd_path)
        self.assertIsInstance(parsed_data, mmd_parser.VmdParser)

    def test_parse_mmd_file_not_found(self):
        """存在しないファイルを解析しようとしたときにFileNotFoundErrorが発生することをテストする。"""
        with self.assertRaises(FileNotFoundError):
            mmd_parser.parse_mmd_file("non_existent_file.unknown")

    def test_parse_mmd_file_unsupported_format(self):
        """サポートされていないファイル形式の場合にMMDParseExceptionが発生することをテストする。"""
        dummy_file_path = os.path.join(self.temp_dir, "test.txt")
        with open(dummy_file_path, 'wb') as f:
            f.write(b'Random data')
        with self.assertRaisesRegex(mmd_parser.MMDParseException, "Unsupported MMD file format"):
            mmd_parser.parse_mmd_file(dummy_file_path)