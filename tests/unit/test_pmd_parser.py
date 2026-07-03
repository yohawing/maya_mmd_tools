import io
import os

from mmd_tools.core import mmd_parser
from mmd_tools.core.pmd_data import PmdData
from mmd_tools.core.pmd_data.display_frame import PmdDisplayFrame
from mmd_tools.core.pmd_data.face import PmdFace
from mmd_tools.core.native.native_pmx_parser import is_native_parser_available
from mmd_tools.core.pmx_data import PmxData
from mmd_tools.core.pmx_data.face import PmxFace
from tests.common.test_base import TestBase
from tests.common.pmd_mock import PmdMock


class TestPmdParser(TestBase):
    def setUp(self):
        super().setUp()
        if not is_native_parser_available():
            self.skipTest("native PMX parser is unavailable in this environment")

        # モックデータを使用してテスト用PMDファイルを作成
        self.pmd_file_path = os.path.join(self.temp_dir, "test_model.pmd")

        # モックを使用してPMDデータを生成
        mock_pmd_data = PmdMock.create_full_pmd()
        with open(self.pmd_file_path, "wb") as f:
            f.write(mock_pmd_data)

        # ファイルを解析
        self.parsed_data = mmd_parser.parse_mmd_file(self.pmd_file_path)
        self.legacy_pmd_data = PmdData().parse_file(self.pmd_file_path)

    def test_parse_pmd_header_success(self):
        """PMDヘッダが正しく解析されることをテストする。"""
        # 解析結果がNoneでないことを確認
        self.assertIsNotNone(self.parsed_data)
        self.assertIsInstance(self.parsed_data, PmxData)
        # ヘッダの属性が正しく設定されていることを確認
        self.assertEqual(self.parsed_data.header.magic, b"PMX ")
        # 汎用 parser は PMD を PMX に変換してから返す
        self.assertAlmostEqual(self.parsed_data.header.version, 2.0)
        # モデル名とコメントが正しく設定されていることを確認
        self.assertIsInstance(self.parsed_data.header.model_name, str)
        # commentが文字列であることを確認
        self.assertIsInstance(self.parsed_data.header.comment, str)

    def test_legacy_pmd_data_parse_file_still_parses_pmd_header(self):
        """PMD writer/export 検証用の legacy PmdData reader は PMD のまま使える。"""
        self.assertEqual(self.legacy_pmd_data.header.magic, b"Pmd")
        self.assertAlmostEqual(self.legacy_pmd_data.header.version, 1.0)

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
        self.assertIsInstance(face, PmxFace)
        self.assertEqual(len(face.indices), 3)

        legacy_face = self.legacy_pmd_data.faces[0]
        self.assertIsInstance(legacy_face, PmdFace)

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
        """PMD IKデータがPMXボーンIKへ変換されることをテストする。"""
        # 解析結果がNoneでないことを確認
        self.assertIsNotNone(self.parsed_data)
        ik_bones = [bone for bone in self.parsed_data.bones if bone.ik_links]
        self.assertGreater(len(ik_bones), 0)
        self.assertIsInstance(ik_bones[0].ik_target_bone_index, int)
        self.assertGreater(len(self.legacy_pmd_data.ik_data), 0)

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
        self.assertGreater(len(self.parsed_data.display_frames), 0)
        self.assertIsNotNone(self.legacy_pmd_data.display_frame)

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
        # create_full_pmd には1つのジョイントが含まれる（IK/剛体テストと同じく
        # 空リストを許容する no-op にせず、必ず存在することをアサートする）
        self.assertIsInstance(self.parsed_data.joints, list)
        self.assertGreater(len(self.parsed_data.joints), 0)
        joint = self.parsed_data.joints[0]
        # ジョイントの属性が正しく設定されていることを確認
        self.assertIsInstance(joint.name, str)
        self.assertIsInstance(joint.position, tuple)
        self.assertEqual(len(joint.position), 3)
        self.assertIsInstance(joint.rotation, tuple)
        self.assertEqual(len(joint.rotation), 3)


class TestPmdDisplayFrameEnglish(TestBase):
    """PmdDisplayFrame の英語表示枠名の write/parse 往復テスト。

    display_frame.parse_english() の回帰防止を目的とする。過去に以下の2バグがあった:
      1. 読み取り件数に len(bone_display_names) を使い、構造が [<jp names>, None] の
         ため常に 2 件しか読まなかった（実際の表示枠数を無視）。
      2. bone_display_names_english[1] への代入が、初期値 [] に対する IndexError に
         なっていた。
    本テストは write_english -> parse_english の往復で英語名が保たれることを検証する。
    """

    def test_english_round_trip_preserves_names(self):
        """3件の英語表示枠名が write/parse 往復で保たれることを確認する。"""
        writer = PmdDisplayFrame()
        writer.bone_display_names_english = [None, ["Frame1", "Frame2", "Frame3"]]

        buf = io.BytesIO()
        writer.write_english(buf)
        buf.seek(0)

        reader = PmdDisplayFrame()
        # parse_english は len(bone_display_names[0]) 件読むため、日本語表示枠名を
        # 3 件セットしておく（バグ時は常に 2 件しか読まず往復が壊れる）。
        reader.bone_display_names = [["a", "b", "c"], None]
        reader.parse_english(buf)

        self.assertEqual(
            reader.bone_display_names_english[1],
            ["Frame1", "Frame2", "Frame3"],
        )

    def test_english_parse_reads_exact_count(self):
        """表示枠が1件でも正しく1件だけ読むことを確認する（件数バグ検出）。"""
        writer = PmdDisplayFrame()
        writer.bone_display_names_english = [None, ["OnlyFrame"]]

        buf = io.BytesIO()
        writer.write_english(buf)
        buf.seek(0)

        reader = PmdDisplayFrame()
        reader.bone_display_names = [["solo"], None]
        reader.parse_english(buf)

        self.assertEqual(reader.bone_display_names_english[1], ["OnlyFrame"])
