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

        self.assertIsNotNone(parsed_data)
        self.assertEqual(parsed_data.header.magic, b'Pmd')
        self.assertAlmostEqual(parsed_data.header.version, 1.0)
        self.assertEqual(parsed_data.header.model_name, '初音ミク')
        self.assertTrue(parsed_data.header.comment.startswith('PolyMo用モデルデータ：初音ミク ver.2.3'))

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

    def test_parse_pmd_vertices(self):
        """PMD頂点データが正しく解析されることをテストする。"""
        parsed_data = mmd_parser.parse_mmd_file(self.pmd_file_path)
        self.assertGreater(len(parsed_data.vertices), 0)
        vertex = parsed_data.vertices[0]
        self.assertIsInstance(vertex.position, tuple)
        self.assertEqual(len(vertex.position), 3)

    def test_parse_pmd_faces(self):
        """PMD面データが正しく解析されることをテストする。"""
        parsed_data = mmd_parser.parse_mmd_file(self.pmd_file_path)
        self.assertGreater(len(parsed_data.faces), 0)
        face = parsed_data.faces[0]
        self.assertIsInstance(face, tuple)
        self.assertEqual(len(face), 3)

    def test_parse_pmd_materials(self):
        """PMD材質データが正しく解析されることをテストする。"""
        parsed_data = mmd_parser.parse_mmd_file(self.pmd_file_path)
        self.assertGreater(len(parsed_data.materials), 0)
        material = parsed_data.materials[0]
        self.assertIsInstance(material.diffuse, tuple)
        self.assertEqual(len(material.diffuse), 4)

    def test_parse_pmd_bones(self):
        """PMDボーンデータが正しく解析されることをテストする。"""
        parsed_data = mmd_parser.parse_mmd_file(self.pmd_file_path)
        self.assertGreater(len(parsed_data.bones), 0)
        bone = parsed_data.bones[0]
        self.assertIsInstance(bone.name, str)

    def test_parse_pmd_ik_data(self):
        """PMD IKデータが正しく解析されることをテストする。"""
        parsed_data = mmd_parser.parse_mmd_file(self.pmd_file_path)
        self.assertGreater(len(parsed_data.ik_data), 0)
        ik = parsed_data.ik_data[0]
        self.assertIsInstance(ik.ik_bone_index, int)

    def test_parse_pmd_morphs(self):
        """PMDモーフデータが正しく解析されることをテストする。"""
        parsed_data = mmd_parser.parse_mmd_file(self.pmd_file_path)
        self.assertGreater(len(parsed_data.morphs), 0)
        morph = parsed_data.morphs[0]
        self.assertIsInstance(morph.name, str)

    def test_parse_pmd_display_frames(self):
        """PMD表示枠データが正しく解析されることをテストする。"""
        parsed_data = mmd_parser.parse_mmd_file(self.pmd_file_path)
        self.assertIsNotNone(parsed_data.display_frame)

    def test_parse_pmd_rigid_bodies(self):
        """PMD剛体データが正しく解析されることをテストする。"""
        parsed_data = mmd_parser.parse_mmd_file(self.pmd_file_path)
        self.assertIsInstance(parsed_data.rigid_bodies, list)

    def test_parse_pmd_joints(self):
        """PMDジョイントデータが正しく解析されることをテストする。"""
        parsed_data = mmd_parser.parse_mmd_file(self.pmd_file_path)
        self.assertIsInstance(parsed_data.joints, list)
