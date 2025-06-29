import os
import struct

from tests.common.test_base import TestBase
from mmd_tools.core import mmd_parser

class TestPmdParser(TestBase):

    def setUp(self):
        super().setUp()
        self.pmd_file_path = os.path.join(os.path.dirname(__file__), "..", "data", "miku_v2.pmd")
        self.parsed_data = mmd_parser.parse_mmd_file(self.pmd_file_path)

    def test_parse_pmd_header_success(self):
        """PMDヘッダが正しく解析されることをテストする。"""
        # 解析結果がNoneでないことを確認
        self.assertIsNotNone(self.parsed_data)
        # ヘッダの属性が正しく設定されていることを確認
        self.assertEqual(self.parsed_data.header.magic, b'Pmd')
        # ヘッダのバージョンが1.0であることを確認
        self.assertAlmostEqual(self.parsed_data.header.version, 1.0)
        # モデル名とコメントが正しく設定されていることを確認
        self.assertIsInstance(self.parsed_data.header.model_name, str)
        # commentが文字列であることを確認
        self.assertIsInstance(self.parsed_data.header.comment, str)


    def test_parse_pmd_vertices(self):
        """PMD頂点データが正しく解析されることをテストする。"""
        # 解析結果がNoneでないことを確認
        self.assertIsNotNone(self.parsed_data)
        # 頂点リストが空でないことを確認
        self.assertGreater(len(self.parsed_data.vertices), 0)
        vertex = self.parsed_data.vertices[0]
        self.assertIsInstance(vertex.position, tuple)
        self.assertEqual(len(vertex.position), 3)

    def test_parse_pmd_faces(self):
        """PMD面データが正しく解析されることをテストする。"""
        # 解析結果がNoneでないことを確認
        self.assertIsNotNone(self.parsed_data)
        # 面リストが空でないことを確認
        self.assertGreater(len(self.parsed_data.faces), 0)
        face = self.parsed_data.faces[0]
        self.assertIsInstance(face, tuple)
        self.assertEqual(len(face), 3)

    def test_parse_pmd_materials(self):
        """PMD材質データが正しく解析されることをテストする。"""
        # 解析結果がNoneでないことを確認
        self.assertIsNotNone(self.parsed_data)
        # 材質リストが空でないことを確認
        self.assertGreater(len(self.parsed_data.materials), 0)
        material = self.parsed_data.materials[0]
        self.assertIsInstance(material.diffuse, tuple)
        self.assertEqual(len(material.diffuse), 4)

    def test_parse_pmd_bones(self):
        """PMDボーンデータが正しく解析されることをテストする。"""
        # 解析結果がNoneでないことを確認
        self.assertIsNotNone(self.parsed_data)
        # ボーンリストが空でないことを確認
        self.assertGreater(len(self.parsed_data.bones), 0)
        bone = self.parsed_data.bones[0]
        self.assertIsInstance(bone.name, str)

    def test_parse_pmd_ik_data(self):
        """PMD IKデータが正しく解析されることをテストする。"""
        # 解析結果がNoneでないことを確認
        self.assertIsNotNone(self.parsed_data)
        # IKデータリストが空でないことを確認
        # PMDファイルにはIKデータが存在しない場合もあるため、必ずしも空でないことを確認する。
        # ただし、存在する場合は正しく解析されていることを確認
        if not self.parsed_data.ik_data:
            return
        # IKデータが存在する場合、最初のIKデータを取得して確認
        self.assertIsNotNone(self.parsed_data.ik_data)
        # IKデータが存在する場合、最初のIKデータを取得して確認
        self.assertGreater(len(self.parsed_data.ik_data), 0)
        ik = self.parsed_data.ik_data[0]
        self.assertIsInstance(ik.ik_bone_index, int)

    def test_parse_pmd_morphs(self):
        """PMDモーフデータが正しく解析されることをテストする。"""
        # 解析結果がNoneでないことを確認
        self.assertIsNotNone(self.parsed_data)
        # モーフリストが空でないことを確認
        self.assertGreater(len(self.parsed_data.morphs), 0)
        morph = self.parsed_data.morphs[0]
        self.assertIsInstance(morph.name, str)

    def test_parse_pmd_display_frames(self):
        """PMD表示枠データが正しく解析されることをテストする。"""
        # 解析結果がNoneでないことを確認
        self.assertIsNotNone(self.parsed_data)
        # 表示枠リストが空でないことを確認
        self.assertIsNotNone(self.parsed_data.display_frame)

    def test_parse_pmd_rigid_bodies(self):
        """PMD剛体データが正しく解析されることをテストする。"""
        # 解析結果がNoneでないことを確認
        self.assertIsNotNone(self.parsed_data)
        # 剛体リストが空でないことを確認
        # 剛体リストがからの場合もあるため、必ずしも空でないことを確認する。
        # ただし、存在する場合は正しく解析されていることを確認
        if not self.parsed_data.rigid_bodies:
            return
        # 剛体データが存在する場合、最初の剛体データを取得して確認
        self.assertIsNotNone(self.parsed_data.rigid_bodies)
        # 剛体データが存在する場合、最初の剛体データを取得して確認
        rigid_body = self.parsed_data.rigid_bodies[0]
        self.assertIsInstance(rigid_body.name, str)
        self.assertIsInstance(rigid_body.position, tuple)
        self.assertEqual(len(rigid_body.position), 3)
        self.assertIsInstance(rigid_body.rotation, tuple)
        self.assertEqual(len(rigid_body.rotation), 3)
        self.assertIsInstance(rigid_body.scale, tuple)
        self.assertEqual(len(rigid_body.scale), 3)
        self.assertIsInstance(rigid_body.mass, float)

    def test_parse_pmd_joints(self):
        """PMDジョイントデータが正しく解析されることをテストする。"""
        # 解析結果がNoneでないことを確認
        self.assertIsNotNone(self.parsed_data)
        # ジョイントリストが空でないことを確認
        self.assertIsInstance(self.parsed_data.joints, list)
        if len(self.parsed_data.joints) == 0:
            # ジョイントが存在しない場合はテストをスキップ
            return
        joint = self.parsed_data.joints[0]
        # ジョイントの属性が正しく設定されていることを確認
        self.assertIsInstance(joint.name, str)
        self.assertIsInstance(joint.position, tuple)
        self.assertEqual(len(joint.position), 3)
        self.assertIsInstance(joint.rotation, tuple)
        self.assertEqual(len(joint.rotation), 3)
        self.assertIsInstance(joint.scale, tuple)
        self.assertEqual(len(joint.scale), 3)
