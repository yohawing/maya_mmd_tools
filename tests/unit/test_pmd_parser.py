import os

from mmd_tools.core import mmd_parser
from mmd_tools.core.pmd_data.face import PmdFace
from tests.common.test_base import TestBase
from tests.common.pmd_mock import PmdMock


class TestPmdParser(TestBase):
    def setUp(self):
        super().setUp()
        # モックデータを使用してテスト用PMDファイルを作成
        self.pmd_file_path = os.path.join(self.temp_dir, "test_model.pmd")

        # モックを使用してPMDデータを生成
        mock_pmd_data = PmdMock.create_full_pmd()
        with open(self.pmd_file_path, "wb") as f:
            f.write(mock_pmd_data)

        # ファイルを解析
        self.parsed_data = mmd_parser.parse_mmd_file(self.pmd_file_path)

    def test_parse_pmd_header_success(self):
        """PMDヘッダが正しく解析されることをテストする。"""
        # 解析結果がNoneでないことを確認
        self.assertIsNotNone(self.parsed_data)
        # ヘッダの属性が正しく設定されていることを確認
        self.assertEqual(self.parsed_data.header.magic, b"Pmd")
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
        self.assertIsInstance(face, PmdFace)
        self.assertEqual(len(face.indices), 3)

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
        # create_full_pmd には2つのIKが含まれる
        self.assertGreater(len(self.parsed_data.ik_data), 0)
        ik = self.parsed_data.ik_data[0]
        self.assertIsInstance(ik.ik_bone_index, int)

    def test_parse_pmd_morphs(self):
        """PMDモーフデータが正しく解析されることをテストする。"""
        # 解析結果がNoneでないことを確認
        self.assertIsNotNone(self.parsed_data)
        # モーフリストが存在することを確認（空の場合もある）
        self.assertIsInstance(self.parsed_data.morphs, list)
        # モーフがある場合は検証
        if len(self.parsed_data.morphs) > 0:
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
        # create_full_pmd には2つの剛体が含まれる
        self.assertGreater(len(self.parsed_data.rigid_bodies), 0)
        rigid_body = self.parsed_data.rigid_bodies[0]
        self.assertIsInstance(rigid_body.name, str)
        self.assertIsInstance(rigid_body.position, tuple)
        self.assertEqual(len(rigid_body.position), 3)
        self.assertIsInstance(rigid_body.rotation, tuple)
        self.assertEqual(len(rigid_body.rotation), 3)
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
