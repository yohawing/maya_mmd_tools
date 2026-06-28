import os
from unittest.mock import patch

from mmd_tools.core import mmd_parser
from mmd_tools.core.pmd_data.material import PmdMaterial
from mmd_tools.core.pmx_data.header import PmxEncoding
from mmd_tools.core.pmx_data import PmxData
from tests.common.test_base import TestBase
from tests.common.pmx_mock import PmxMock


class TestPmxParser(TestBase):
    def setUp(self):
        super().setUp()
        # モックデータを使用してテスト用PMXファイルを作成
        self.pmx_file_path = os.path.join(self.temp_dir, "test_model.pmx")

        # モックを使用してPMXデータを生成
        mock_pmx_data = PmxMock.create_full_pmx()
        with open(self.pmx_file_path, "wb") as f:
            f.write(mock_pmx_data)

        # ファイルを解析
        self.parsed_data = mmd_parser.parse_mmd_file(self.pmx_file_path)

    def tearDown(self):
        super().tearDown()
        # temp_dirはベースクラスで自動的に削除される

    def test_parse_pmx_file_success(self):
        """PMXファイルが正しく解析されることをテストする。"""

        # 解析結果がNoneでないことを確認
        self.assertIsNotNone(self.parsed_data, msg="パース結果がNoneです")
        # 型のチェック
        self.assertIsInstance(self.parsed_data, PmxData, msg="パース結果の型が不正です")

    def test_native_pmx_parse_can_be_disabled_by_call_site(self):
        """警告回避が必要な呼び出し元では native PMX parse を明示的に無効化できる。"""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MMD_TOOLS_ENABLE_NATIVE_PMX_PARSE", None)
            self.assertIsNone(mmd_parser._try_native_pmx_parse(self.pmx_file_path, use_native_pmx_parse=False))

    def test_required_native_pmx_parse_rejects_disabled_parser(self):
        """native PMX parse 必須モードでは無効化を Python parser fallback で隠さない。"""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MMD_TOOLS_ENABLE_NATIVE_PMX_PARSE", None)
            with self.assertRaises(mmd_parser.MMDParseException):
                mmd_parser._try_native_pmx_parse(
                    self.pmx_file_path,
                    use_native_pmx_parse=False,
                    require_native_pmx_parse=True,
                )

    def test_required_native_pmx_parse_rejects_unavailable_parser(self):
        """native PMX parse 必須モードでは None 戻り値を parse 失敗として扱う。"""
        with patch("mmd_tools.core.native.native_pmx_parser.parse_pmx_native", return_value=None):
            with self.assertRaises(mmd_parser.MMDParseException):
                mmd_parser.parse_mmd_file(
                    self.pmx_file_path,
                    require_native_pmx_parse=True,
                )

    def test_pmx_parse_requires_native_parser_by_default(self):
        """PMX parse は既定で Python parser fallback を許可しない。"""
        with patch("mmd_tools.core.native.native_pmx_parser.parse_pmx_native", return_value=None):
            with self.assertRaises(mmd_parser.MMDParseException):
                mmd_parser.parse_mmd_file(self.pmx_file_path)

    def test_legacy_python_pmx_parser_requires_explicit_opt_out(self):
        """移行用の legacy Python PMX parser は明示 opt-out 時だけ使える。"""
        with patch("mmd_tools.core.native.native_pmx_parser.parse_pmx_native", return_value=None):
            parsed_data = mmd_parser.parse_mmd_file(
                self.pmx_file_path,
                require_native_pmx_parse=False,
            )
        self.assertIsInstance(parsed_data, PmxData)

    def test_parse_pmx_file_uses_pmx_specific_entry_point(self):
        """PMX専用入口は import parser dispatch なしで構造化PMXを返す。"""
        with patch("mmd_tools.core.native.native_pmx_parser.parse_pmx_native", return_value=None):
            parsed_data = mmd_parser.parse_pmx_file(
                self.pmx_file_path,
                require_native_pmx_parse=False,
            )
        self.assertIsInstance(parsed_data, PmxData)

    def test_parse_pmx_file_fallback_uses_legacy_helper_not_pmx_data_method(self):
        """PMX専用入口の legacy fallback は PmxData.parse_file へ戻さない。"""
        with patch("mmd_tools.core.native.native_pmx_parser.parse_pmx_native", return_value=None):
            with patch.object(PmxData, "parse_file", side_effect=AssertionError("legacy method should not be used")):
                parsed_data = mmd_parser.parse_pmx_file(
                    self.pmx_file_path,
                    require_native_pmx_parse=False,
                )
        self.assertIsInstance(parsed_data, PmxData)

    def test_parse_pmx_file_rejects_non_pmx_magic(self):
        """PMX専用入口は PMX 以外の magic を受け付けない。"""
        invalid_path = os.path.join(self.temp_dir, "not_pmx.vmd")
        with open(invalid_path, "wb") as f:
            f.write(b"Vocaloid Motion Data file")

        with self.assertRaises(mmd_parser.MMDParseException):
            mmd_parser.parse_pmx_file(invalid_path)

    def test_native_pmx_parse_env_flag(self):
        """native PMX parse は通常有効で、環境変数で明示的に切り替えられる。"""
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(mmd_parser._native_pmx_parse_enabled())
        with patch.dict(os.environ, {"MMD_TOOLS_ENABLE_NATIVE_PMX_PARSE": "1"}, clear=True):
            self.assertTrue(mmd_parser._native_pmx_parse_enabled())
        with patch.dict(os.environ, {"MMD_TOOLS_ENABLE_NATIVE_PMX_PARSE": "0"}, clear=True):
            self.assertFalse(mmd_parser._native_pmx_parse_enabled())

    def test_parse_pmx_header_success(self):
        """PMXヘッダが正しく解析されることをテストする。"""

        # 解析結果がNoneでないことを確認
        self.assertIsNotNone(self.parsed_data, msg="パース結果がNoneです")
        # ヘッダ情報がNoneでないことを確認
        header = self.parsed_data.header
        self.assertIsNotNone(header, msg="ヘッダがNoneです")

        # マジックナンバーが'PMX 'であることを確認
        self.assertEqual(header.magic, b"PMX ", msg=f"マジックナンバーが不正です: {header.magic}")
        # バージョンが2.0または2.1であることを確認（浮動小数点数の誤差を考慮）
        self.assertTrue(
            abs(header.version - 2.0) < 0.01 or abs(header.version - 2.1) < 0.01,
            msg=f"サポート外のバージョンです: {header.version}",
        )
        # ヘッダサイズが8バイトであることを確認
        self.assertEqual(header.header_size, 8, msg=f"ヘッダサイズが不正です: {header.header_size}")
        # エンコーディングがPMXEncodingのいずれかであることを確認
        self.assertIsInstance(header.encoding, PmxEncoding)
        # 追加UV数が0から4の範囲内であることを確認
        self.assertIn(
            header.additional_uv,
            [0, 1, 2, 3, 4],
            msg=f"追加UV数が不正です: {header.additional_uv}",
        )
        # 各インデックスサイズが1, 2, 4のいずれかであることを確認
        self.assertIn(
            header.vertex_index_size,
            [1, 2, 4],
            msg=f"頂点インデックスサイズが不正です: {header.vertex_index_size}",
        )
        self.assertIn(
            header.texture_index_size,
            [1, 2, 4],
            msg=f"テクスチャインデックスサイズが不正です: {header.texture_index_size}",
        )
        self.assertIn(
            header.material_index_size,
            [1, 2, 4],
            msg=f"材質インデックスサイズが不正です: {header.material_index_size}",
        )
        self.assertIn(
            header.bone_index_size,
            [1, 2, 4],
            msg=f"ボーンインデックスサイズが不正です: {header.bone_index_size}",
        )
        self.assertIn(
            header.morph_index_size,
            [1, 2, 4],
            msg=f"モーフインデックスサイズが不正です: {header.morph_index_size}",
        )
        self.assertIn(
            header.rigid_body_index_size,
            [1, 2, 4],
            msg=f"剛体インデックスサイズが不正です: {header.rigid_body_index_size}",
        )

    def test_parse_pmx_vertex_success(self):
        """PMX頂点データが正しく解析されることをテストする。"""

        # 解析結果がNoneでないことを確認
        self.assertIsNotNone(self.parsed_data, msg="パース結果がNoneです")
        # 頂点リストがNoneでなく、空でないことを確認
        self.assertIsNotNone(self.parsed_data.vertices, msg="頂点リストがNoneです")
        self.assertGreater(len(self.parsed_data.vertices), 0, msg="頂点リストが空です")

        # 最初の頂点を取得して、各属性の型と構造を確認
        vertex = self.parsed_data.vertices[0]
        self.assertIsNotNone(vertex, msg="最初の頂点がNoneです")
        # 位置、法線、UV座標がfloat3またはfloat2形式（3つまたは2つの浮動小数点数）であることを確認
        self.assertEqual(
            len(vertex.position),
            3,
            msg=f"頂点位置の要素数が不正です: {len(vertex.position)}",
        )
        self.assertEqual(
            len(vertex.normal),
            3,
            msg=f"頂点法線の要素数が不正です: {len(vertex.normal)}",
        )
        self.assertEqual(len(vertex.uv), 2, msg=f"UV座標の要素数が不正です: {len(vertex.uv)}")
        # ウェイト変形方式が0から4のいずれかであることを確認
        self.assertIn(
            vertex.weight_transform_type,
            [0, 1, 2, 3, 4],
            msg=f"不正なウェイト変形方式です: {vertex.weight_transform_type}",
        )
        # エッジ倍率が浮動小数点数であることを確認
        self.assertIsInstance(
            vertex.edge_magnification,
            float,
            msg=f"エッジ倍率がfloatではありません: {type(vertex.edge_magnification)}",
        )

    def test_parse_pmx_face_success(self):
        """PMX面データが正しく解析されることをテストする。"""

        # 解析結果がNoneでないことを確認
        self.assertIsNotNone(self.parsed_data, msg="パース結果がNoneです")
        # 面リストがNoneでなく、空でないことを確認
        self.assertIsNotNone(self.parsed_data.faces, msg="面リストがNoneです")
        self.assertGreater(len(self.parsed_data.faces), 0, msg="面リストが空です")

        # 最初の面を取得して、各頂点インデックスが整数であることを確認
        face = self.parsed_data.faces[0]
        self.assertIsNotNone(face, msg="最初の面がNoneです")
        self.assertIsInstance(face.indices[0], int, msg="面を構成する頂点インデックスが整数ではありません")
        self.assertIsInstance(face.indices[1], int, msg="面を構成する頂点インデックスが整数ではありません")
        self.assertIsInstance(face.indices[2], int, msg="面を構成する頂点インデックスが整数ではありません")

    def test_parse_pmx_texture_success(self):
        """PMXテクスチャリストが正しく解析されることをテストする。"""

        # 解析結果がNoneでないことを確認
        self.assertIsNotNone(self.parsed_data, msg="パース結果がNoneです")
        # テクスチャリストがNoneでなく、空でないことを確認
        self.assertIsNotNone(self.parsed_data.textures, msg="テクスチャリストがNoneです")
        self.assertGreater(len(self.parsed_data.textures), 0, msg="テクスチャリストが空です")

        # 最初のテクスチャパスが文字列であることを確認
        self.assertIsInstance(
            self.parsed_data.textures[0],
            str,
            msg="テクスチャパスが文字列ではありません",
        )

    def test_parse_pmx_material_success(self):
        """PMX材質データが正しく解析されることをテストする。"""

        # 解析結果がNoneでないことを確認
        self.assertIsNotNone(self.parsed_data, msg="パース結果がNoneです")
        # 材質リストがNoneでなく、空でないことを確認
        self.assertIsNotNone(self.parsed_data.materials, msg="材質リストがNoneです")
        self.assertGreater(len(self.parsed_data.materials), 0, msg="材質リストが空です")

        # 最初の材質を取得して、各属性の型と構造を確認
        material: PmdMaterial = self.parsed_data.materials[0]
        self.assertIsNotNone(material, msg="最初の材質がNoneです")
        self.assertIsInstance(material.name, str, msg="材質名が文字列ではありません")

        # Mockデータの名前チェック
        self.assertEqual(material.name, "テスト材質", msg="材質名が不正です")
        self.assertIsInstance(material.name_english, str, msg="英語材質名が文字列ではありません")
        self.assertEqual(material.name_english, "TestMaterial", msg="英語材質名が不正です")
        self.assertEqual(len(material.diffuse), 4, msg="Diffuseの要素数が不正です")
        self.assertEqual(len(material.specular), 3, msg="Specularの要素数が不正です")
        self.assertIsInstance(
            material.specular_coefficient,
            float,
            msg="Specular係数がfloatではありません",
        )
        self.assertEqual(len(material.ambient), 3, msg="Ambientの要素数が不正です")
        self.assertIsInstance(material.draw_flag, int, msg="描画フラグがintではありません")
        self.assertEqual(len(material.edge_color), 4, msg="エッジ色の要素数が不正です")
        self.assertIsInstance(material.edge_size, float, msg="エッジサイズがfloatではありません")
        self.assertIsInstance(material.texture_index, int, msg="テクスチャインデックスがintではありません")
        self.assertIsInstance(
            material.sphere_texture_index,
            int,
            msg="スフィアテクスチャインデックスがintではありません",
        )
        self.assertIn(material.sphere_mode, [0, 1, 2, 3], msg="スフィアモードの値が不正です")
        self.assertIn(material.shared_toon_flag, [0, 1], msg="共有Toonフラグの値が不正です")
        self.assertIsInstance(
            material.toon_texture_index,
            int,
            msg="Toonテクスチャインデックスがintではありません",
        )
        self.assertIsInstance(material.memo, str, msg="メモが文字列ではありません")
        self.assertIsInstance(material.face_count, int, msg="材質に対応する面数がintではありません")

    def test_parse_pmx_bone_success(self):
        """PMXボーンデータが正しく解析されることをテストする。"""

        # 解析結果がNoneでないことを確認
        self.assertIsNotNone(self.parsed_data, msg="パース結果がNoneです")
        # ボーンリストがNoneでなく、空でないことを確認
        self.assertIsNotNone(self.parsed_data.bones, msg="ボーンリストがNoneです")
        self.assertGreater(len(self.parsed_data.bones), 0, msg="ボーンリストが空です")

        # 最初のボーンを取得して、各属性の型と構造を確認
        bone = self.parsed_data.bones[0]
        self.assertIsNotNone(bone, msg="最初のボーンがNoneです")
        self.assertIsInstance(bone.name, str, msg="ボーン名が文字列ではありません")
        self.assertEqual(bone.name, "センター", msg="ボーン名が不正です")
        self.assertIsInstance(bone.name_english, str, msg="英語ボーン名が文字列ではありません")
        self.assertEqual(bone.name_english, "center", msg="英語ボーン名が不正です")
        self.assertEqual(len(bone.position), 3, msg="ボーン位置の要素数が不正です")
        self.assertIsInstance(bone.parent_bone_index, int, msg="親ボーンインデックスがintではありません")
        self.assertIsInstance(bone.transform_layer, int, msg="変形階層がintではありません")
        self.assertIsInstance(bone.bone_flag, int, msg="ボーンフラグがintではありません")

    def test_parse_pmx_morph_success(self):
        """PMXモーフデータが正しく解析されることをテストする。"""

        # 解析結果がNoneでないことを確認
        self.assertIsNotNone(self.parsed_data, msg="パース結果がNoneです")
        # モーフリストがNoneでなく、空でないことを確認
        self.assertIsNotNone(self.parsed_data.morphs, msg="モーフリストがNoneです")
        self.assertGreater(len(self.parsed_data.morphs), 0, msg="モーフリストが空です")

        # 最初のモーフを取得して、各属性の型と構造を確認
        morph = self.parsed_data.morphs[0]
        self.assertIsNotNone(morph, msg="最初のモーフがNoneです")
        self.assertIsInstance(morph.name, str, msg="モーフ名が文字列ではありません")
        self.assertIsInstance(morph.name_english, str, msg="英語モーフ名が文字列ではありません")
        self.assertIn(morph.panel, [0, 1, 2, 3, 4], msg="操作パネルの値が不正です")
        self.assertIsInstance(morph.morph_type, int, msg="モーフ種類がintではありません")
        self.assertGreaterEqual(morph.offset_count, 0, msg="オフセット数が負の値です")
        self.assertEqual(
            len(morph.offsets),
            morph.offset_count,
            msg="オフセット数とオフセットのリストの長さが一致しません",
        )

    def test_parse_pmx_display_frame_success(self):
        """PMX表示枠データが正しく解析されることをテストする。"""

        # 解析結果がNoneでないことを確認
        self.assertIsNotNone(self.parsed_data, msg="パース結果がNoneです")
        # 表示枠リストがNoneでなく、空でないことを確認
        self.assertIsNotNone(self.parsed_data.display_frames, msg="表示枠リストがNoneです")
        self.assertGreater(len(self.parsed_data.display_frames), 0, msg="表示枠リストが空です")

        # 最初の表示枠を取得して、各属性の型と構造を確認
        display_frame = self.parsed_data.display_frames[0]
        self.assertIsNotNone(display_frame, msg="最初の表示枠がNoneです")
        self.assertIsInstance(display_frame.name, str, msg="表示枠名が文字列ではありません")
        self.assertIsInstance(display_frame.name_english, str, msg="英語表示枠名が文字列ではありません")
        self.assertIn(display_frame.special_flag, [0, 1], msg="特殊枠フラグの値が不正です")
        self.assertGreaterEqual(len(display_frame.elements), 0, msg="枠内要素数が負の値です")

    def test_parse_pmx_rigid_body_success(self):
        """PMX剛体データが正しく解析されることをテストする。"""

        # 解析結果がNoneでないことを確認
        self.assertIsNotNone(self.parsed_data, msg="パース結果がNoneです")
        # 剛体リストがNoneでなく、空でないことを確認
        self.assertIsNotNone(self.parsed_data.rigid_bodies, msg="剛体リストがNoneです")
        self.assertGreater(len(self.parsed_data.rigid_bodies), 0, msg="剛体リストが空です")

        # 最初の剛体を取得して、各属性の型と構造を確認
        rigid_body = self.parsed_data.rigid_bodies[0]
        self.assertIsNotNone(rigid_body, msg="最初の剛体がNoneです")
        self.assertIsInstance(rigid_body.name, str, msg="剛体名が文字列ではありません")
        self.assertIsInstance(rigid_body.name_english, str, msg="英語剛体名が文字列ではありません")
        self.assertIsInstance(
            rigid_body.related_bone_index,
            int,
            msg="関連ボーンインデックスがintではありません",
        )
        self.assertIsInstance(rigid_body.group, int, msg="グループがintではありません")
        self.assertIsInstance(
            rigid_body.collision_mask,
            int,
            msg="非衝突グループフラグがintではありません",
        )
        self.assertIn(rigid_body.shape_type, [0, 1, 2], msg="形状タイプが不正です")
        self.assertEqual(len(rigid_body.size), 3, msg="サイズの要素数が不正です")
        self.assertEqual(len(rigid_body.position), 3, msg="位置の要素数が不正です")
        self.assertEqual(len(rigid_body.rotation), 3, msg="回転の要素数が不正です")
        self.assertIsInstance(rigid_body.mass, float, msg="質量がfloatではありません")
        self.assertIsInstance(rigid_body.velocity_attenuation, float, msg="移動減衰がfloatではありません")
        self.assertIsInstance(rigid_body.rotation_attenuation, float, msg="回転減衰がfloatではありません")
        self.assertIsInstance(rigid_body.elasticity, float, msg="反発力がfloatではありません")
        self.assertIsInstance(rigid_body.friction, float, msg="摩擦力がfloatではありません")
        self.assertIn(rigid_body.physics_mode, [0, 1, 2], msg="物理演算タイプが不正です")

    def test_parse_pmx_joint_success(self):
        """PMXジョイントデータが正しく解析されることをテストする。"""

        # 解析結果がNoneでないことを確認
        self.assertIsNotNone(self.parsed_data, msg="パース結果がNoneです")
        # ジョイントリストがNoneでなく、空でないことを確認
        self.assertIsNotNone(self.parsed_data.joints, msg="ジョイントリストがNoneです")
        self.assertGreater(len(self.parsed_data.joints), 0, msg="ジョイントリストが空です")

        # 最初のジョイントを取得して、各属性の型と構造を確認
        joint = self.parsed_data.joints[0]
        self.assertIsNotNone(joint, msg="最初のジョイントがNoneです")
        self.assertIsInstance(joint.name, str, msg="ジョイント名が文字列ではありません")
        self.assertIsInstance(joint.name_english, str, msg="英語ジョイント名が文字列ではありません")
        self.assertIsInstance(joint.joint_type, int, msg="Joint種類がintではありません")
        self.assertIsInstance(
            joint.rigid_body_a_index,
            int,
            msg="関連剛体Aのインデックスがintではありません",
        )
        self.assertIsInstance(
            joint.rigid_body_b_index,
            int,
            msg="関連剛体Bのインデックスがintではありません",
        )
        self.assertEqual(len(joint.position), 3, msg="位置の要素数が不正です")
        self.assertEqual(len(joint.rotation), 3, msg="回転の要素数が不正です")
        self.assertEqual(len(joint.translation_limit_min), 3, msg="移動制限下限の要素数が不正です")
        self.assertEqual(len(joint.translation_limit_max), 3, msg="移動制限上限の要素数が不正です")
        self.assertEqual(len(joint.rotation_limit_min), 3, msg="回転制限下限の要素数が不正です")
        self.assertEqual(len(joint.rotation_limit_max), 3, msg="回転制限上限の要素数が不正です")
        self.assertEqual(len(joint.spring_translation), 3, msg="バネ定数（移動）の要素数が不正です")
        self.assertEqual(len(joint.spring_rotation), 3, msg="バネ定数（回転）の要素数が不正です")

    def test_parse_pmx_soft_body_success(self):
        """PMXソフトボディデータが正しく解析されることをテストする。"""

        # 解析結果がNoneでないことを確認
        self.assertIsNotNone(self.parsed_data, msg="パース結果がNoneです")
        # ソフトボディリストがNoneでないことを確認
        # 注: 現在の実装ではSoftBodyのパーサーはプレースホルダーであり、詳細な解析は行われない。
        # そのため、リストがNoneでないことのみを確認する。
        self.assertIsNotNone(self.parsed_data.soft_bodies, msg="ソフトボディリストがNoneです")
