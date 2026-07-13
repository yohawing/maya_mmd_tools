"""
PMDエクスポート機能のユニットテスト
モックデータを使用したラウンドトリップテストを実行
"""

import importlib.machinery
import importlib.util
import os

from mmd_tools.core.exceptions import MMDExportException
from mmd_tools.core.pmd_data import PmdData
from mmd_tools.core.pmd_data.vertex import PmdVertex
from mmd_tools.core.pmd_data.face import PmdFace
from mmd_tools.core.pmd_data.ik import PmdIK
from mmd_tools.core.pmd_data.material import PmdMaterial
from mmd_tools.core.pmd_data.morph import PmdMorph
from mmd_tools.core.pmd_data.bone import PmdBone, PmdBoneType
from mmd_tools.core.pmd_data.display_frame import PmdDisplayFrame
from mmd_tools.core.pmd_data.rigid_body import PmdRigidBody
from mmd_tools.core.pmd_data.joint import PmdJoint

# Load PmdExporter directly without triggering io/__init__.py's maya imports
_pmd_exporter_path = os.path.join(
    os.path.dirname(__file__), "..", "..", "mmd_tools", "io", "pmd_exporter.py"
)
_loader = importlib.machinery.SourceFileLoader("mmd_tools.io.pmd_exporter", os.path.abspath(_pmd_exporter_path))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
_pmd_exporter_mod = importlib.util.module_from_spec(_spec)
_loader.exec_module(_pmd_exporter_mod)
PmdExporter = _pmd_exporter_mod.PmdExporter
_fan_triangulate = _pmd_exporter_mod._fan_triangulate
_normalize_bone_weight = _pmd_exporter_mod._normalize_bone_weight
from tests.common.test_base import TestBase
from tests.common.pmd_mock import PmdMock


class TestPmdExport(TestBase):
    """PMDエクスポート機能のテストクラス"""

    def test_pmd_round_trip_with_minimal_mock(self):
        """モックデータを使用したPMDファイルのラウンドトリップテスト"""

        # モックデータを作成
        mock_data = PmdMock.create_minimal_pmd()

        # 1. モックデータを一時ファイルに書き込む
        tmp_input_path = os.path.join(self.temp_dir, "test_input.pmd")
        with open(tmp_input_path, "wb") as f:
            f.write(mock_data)

        # モックデータからPMDを読み込む
        parser1 = PmdData()
        parser1.parse_file(tmp_input_path)

        # 2. 一時ファイルに書き込む
        tmp_path = os.path.join(self.temp_dir, "test_export.pmd")
        parser1.write_file(tmp_path)

        # 3. 書き込んだファイルを再度読み込む
        parser2 = PmdData()
        parser2.parse_file(tmp_path)

        # 4. データの一致を確認
        # ヘッダー情報の比較
        self.assertEqual(
            parser1.header.model_name,
            parser2.header.model_name,
            "モデル名が一致しません",
        )
        self.assertEqual(parser1.header.comment, parser2.header.comment, "コメントが一致しません")

        # 頂点数の比較
        self.assertEqual(len(parser1.vertices), len(parser2.vertices), "頂点数が一致しません")

        # 最初の頂点データの比較（サンプル）
        if parser1.vertices:
            v1 = parser1.vertices[0]
            v2 = parser2.vertices[0]
            self.assertEqual(v1.position, v2.position, "頂点位置が一致しません")
            self.assertEqual(v1.normal, v2.normal, "頂点法線が一致しません")
            self.assertEqual(v1.uv, v2.uv, "頂点UVが一致しません")

        # 面数の比較
        self.assertEqual(len(parser1.faces), len(parser2.faces), "面数が一致しません")

        # マテリアル数の比較
        self.assertEqual(len(parser1.materials), len(parser2.materials), "マテリアル数が一致しません")

        # ボーン数の比較
        self.assertEqual(len(parser1.bones), len(parser2.bones), "ボーン数が一致しません")

    def test_pmd_round_trip_with_full_mock(self):
        """フル機能モックデータを使用したPMDファイルのラウンドトリップテスト"""

        # モックデータを作成
        mock_data = PmdMock.create_full_pmd()

        # 1. モックデータを一時ファイルに書き込む
        tmp_input_path = os.path.join(self.temp_dir, "test_input.pmd")
        with open(tmp_input_path, "wb") as f:
            f.write(mock_data)

        # モックデータからPMDを読み込む
        parser1 = PmdData()
        parser1.parse_file(tmp_input_path)

        # 2. 一時ファイルに書き込む
        tmp_path = os.path.join(self.temp_dir, "test_export_full.pmd")
        parser1.write_file(tmp_path)

        # 3. 書き込んだファイルを再度読み込む
        parser2 = PmdData()
        parser2.parse_file(tmp_path)

        # 4. データの一致を確認
        # ヘッダー情報の比較
        self.assertEqual(
            parser1.header.model_name,
            parser2.header.model_name,
            "モデル名が一致しません",
        )
        self.assertEqual(parser1.header.comment, parser2.header.comment, "コメントが一致しません")

        # 詳細なデータ数の比較
        self.assertEqual(len(parser1.vertices), len(parser2.vertices), "頂点数が一致しません")
        self.assertEqual(len(parser1.faces), len(parser2.faces), "面数が一致しません")
        self.assertEqual(len(parser1.materials), len(parser2.materials), "マテリアル数が一致しません")
        self.assertEqual(len(parser1.bones), len(parser2.bones), "ボーン数が一致しません")
        self.assertEqual(len(parser1.ik_data), len(parser2.ik_data), "IK数が一致しません")
        self.assertEqual(len(parser1.morphs), len(parser2.morphs), "モーフ数が一致しません")

    def test_create_simple_pmd(self):
        """簡単なPMDファイルを作成してエクスポートするテスト"""

        # 新しいPMDパーサーインスタンスを作成
        parser = PmdData()

        # ヘッダー情報を設定
        parser.header.magic = b"Pmd"
        parser.header.version = 1.0
        parser.header.model_name = "TestModel"
        parser.header.comment = "This is a test model created by export test"

        # 簡単な三角形の頂点を追加
        # 頂点1
        v1 = PmdVertex()
        v1.position = (0.0, 0.0, 0.0)
        v1.normal = (0.0, 1.0, 0.0)
        v1.uv = (0.0, 0.0)
        v1.bone_indices = (0, 0)
        v1.bone_weight = 100
        v1.edge_flag = 1
        parser.vertices.append(v1)

        # 頂点2
        v2 = PmdVertex()
        v2.position = (1.0, 0.0, 0.0)
        v2.normal = (0.0, 1.0, 0.0)
        v2.uv = (1.0, 0.0)
        v2.bone_indices = (0, 0)
        v2.bone_weight = 100
        v2.edge_flag = 1
        parser.vertices.append(v2)

        # 頂点3
        v3 = PmdVertex()
        v3.position = (0.0, 0.0, 1.0)
        v3.normal = (0.0, 1.0, 0.0)
        v3.uv = (0.0, 1.0)
        v3.bone_indices = (0, 0)
        v3.bone_weight = 100
        v3.edge_flag = 1
        parser.vertices.append(v3)

        # 面（三角形）を追加
        face = PmdFace()
        face.indices = (0, 1, 2)
        parser.faces.append(face)

        # マテリアルを追加
        material = PmdMaterial(0)
        material.diffuse = (0.8, 0.8, 0.8, 1.0)
        material.specular_power = 5.0
        material.specular = (0.5, 0.5, 0.5)
        material.ambient = (0.3, 0.3, 0.3)
        material.toon_texture_index = 0
        material.edge_flag = 1
        material.face_count = 1  # 1面（3頂点）
        material.texture_file_name = ""
        parser.materials.append(material)

        # 最低限のボーンを追加（ルートボーン）
        bone = PmdBone()
        bone.name = "root"
        bone.parent_bone_index = -1
        bone.tail_pos_bone_index = 0xFFFF
        bone.bone_type = PmdBoneType.ROTATE_AND_MOVE
        bone.ik_parent_bone_index = 0
        bone.position = (0.0, 0.0, 0.0)
        parser.bones.append(bone)

        # 一時ファイルに書き込む
        tmp_path = os.path.join(self.temp_dir, "test_created.pmd")
        parser.write_file(tmp_path)

        # 書き込んだファイルを読み込んで確認
        parser2 = PmdData()
        parser2.parse_file(tmp_path)

        # データの検証
        self.assertEqual(parser2.header.model_name, "TestModel")
        self.assertEqual(len(parser2.vertices), 3)
        self.assertEqual(len(parser2.faces), 1)
        self.assertEqual(len(parser2.materials), 1)
        self.assertEqual(len(parser2.bones), 1)


class TestPmdExporterFromDict(TestBase):
    """PmdExporter.export_pmd_model() をdict入力から呼ぶテスト"""

    def setUp(self):
        super().setUp()
        self.exporter = PmdExporter()

    def test_export_triangle_dict_roundtrip(self):
        """三角形dictをexport -> parse_file で検証"""
        data = {
            "model_name": "TriangleTest",
            "vertices": [
                {"position": [0.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [0.0, 0.0]},
                {"position": [1.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [1.0, 0.0]},
                {"position": [0.0, 1.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [0.0, 1.0]},
            ],
            "faces": [[0, 1, 2]],
        }
        out_path = os.path.join(self.temp_dir, "triangle.pmd")
        self.exporter.export_pmd_model(out_path, data)

        pmd = PmdData()
        pmd.parse_file(out_path)

        self.assertEqual(pmd.header.model_name, "TriangleTest")
        self.assertEqual(len(pmd.vertices), 3)
        self.assertEqual(len(pmd.faces), 1)
        self.assertEqual(len(pmd.materials), 1)
        # PMD の face_count はインデックス数（面数 * 3）。
        self.assertEqual(pmd.materials[0].face_count, 3)
        self.assertEqual(len(pmd.bones), 1)

        # 頂点データの検証
        v = pmd.vertices[0]
        self.assertEqual(v.position, (0.0, 0.0, 0.0))
        self.assertEqual(v.normal, (0.0, 0.0, 1.0))
        self.assertEqual(v.uv, (0.0, 0.0))
        self.assertEqual(v.bone_indices, (0, 0))
        self.assertEqual(v.bone_weight, 100)  # default: 全乗せ
        self.assertEqual(v.edge_flag, 0)

        # 面の検証
        self.assertEqual(pmd.faces[0].indices, (0, 1, 2))

        # 自動生成された root ボーンの検証
        self.assertEqual(pmd.bones[0].name, "root")
        self.assertEqual(pmd.bones[0].parent_bone_index, -1)
        self.assertEqual(pmd.bones[0].bone_type, PmdBoneType.ROTATE_AND_MOVE)

    def test_exporter_uses_native_writer_when_available(self):
        """native PMD writer が bytes を返す場合はその bytes を書く。"""
        calls = []

        def native_exporter(payload):
            calls.append(payload)
            return b"NATIVE-PMD"

        exporter = PmdExporter(native_exporter=native_exporter)
        out_path = os.path.join(self.temp_dir, "native_dict.pmd")
        exporter.export_pmd_model(
            out_path,
            {
                "model_name": "NativePmd",
                "vertices": [
                    {"position": [0, 0, 0], "normal": [0, 1, 0], "uv": [0, 0], "bone_indices": [0, 0]},
                    {"position": [1, 0, 0], "normal": [0, 1, 0], "uv": [1, 0], "bone_indices": [0, 0]},
                    {"position": [0, 1, 0], "normal": [0, 1, 0], "uv": [0, 1], "bone_indices": [0, 0]},
                ],
                "faces": [[0, 1, 2]],
                "materials": [{"name": "mat", "face_count": 3, "edge_flag": 1}],
            },
        )

        with open(out_path, "rb") as handle:
            self.assertEqual(handle.read(), b"NATIVE-PMD")
        self.assertEqual(len(calls), 1)
        payload = calls[0]
        self.assertEqual(payload["metadata"]["name"], "NativePmd")
        self.assertEqual(payload["metadata"]["counts"]["vertices"], 3)
        self.assertEqual(payload["metadata"]["counts"]["faces"], 1)
        self.assertEqual(payload["materials"][0]["faceCount"], 1)
        self.assertEqual(payload["geometry"]["vertices"][0]["edgeEnabled"], True)
        self.assertEqual(payload["skeleton"]["bones"][0]["name"], "root")

    def test_exporter_falls_back_when_native_writer_returns_none(self):
        """native PMD writer が使えない環境では従来 writer へ戻る。"""
        exporter = PmdExporter(native_exporter=lambda payload: None)
        out_path = os.path.join(self.temp_dir, "fallback_dict.pmd")
        exporter.export_pmd_model(
            out_path,
            {
                "model_name": "FallbackPmd",
                "vertices": [
                    {"position": [0, 0, 0], "normal": [0, 1, 0], "uv": [0, 0], "bone_indices": [0, 0]},
                    {"position": [1, 0, 0], "normal": [0, 1, 0], "uv": [1, 0], "bone_indices": [0, 0]},
                    {"position": [0, 1, 0], "normal": [0, 1, 0], "uv": [0, 1], "bone_indices": [0, 0]},
                ],
                "faces": [[0, 1, 2]],
            },
        )

        parsed = PmdData().parse_file(out_path)
        self.assertEqual(parsed.header.model_name, "FallbackPmd")
        self.assertEqual(len(parsed.vertices), 3)
        self.assertEqual(len(parsed.faces), 1)

    def test_native_export_blocker_documents_unsupported_pmd_sections(self):
        """native JSON がまだ表現しない PMD セクションは理由付きで Python writer に戻す。"""
        exporter = PmdExporter(native_exporter=lambda payload: b"NATIVE-PMD")
        pmd = PmdData()
        self.assertIsNone(exporter.native_export_blocker(pmd))

        section_cases = [
            ("ik_data", PmdIK(), "ik"),
            ("morphs", PmdMorph(), "morphs"),
            ("display_frames", PmdDisplayFrame(), "display_frames"),
            ("rigid_bodies", PmdRigidBody(), "rigid_bodies"),
            ("joints", PmdJoint(), "joints"),
        ]
        for attr, item, reason in section_cases:
            with self.subTest(section=attr):
                pmd = PmdData()
                getattr(pmd, attr).append(item)
                self.assertEqual(exporter.native_export_blocker(pmd), reason)

    def test_native_export_falls_back_for_unsupported_pmd_sections(self):
        """blocker 対象セクションがある場合は native writer を呼ばず Python writer を使う。"""
        calls = []
        exporter = PmdExporter(native_exporter=lambda payload: calls.append(payload) or b"NATIVE-PMD")
        pmd = PmdData()
        pmd.header.magic = b"Pmd"
        pmd.header.version = 1.0
        pmd.header.model_name = "UnsupportedNativePmd"

        for position in ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)):
            vertex = PmdVertex()
            vertex.position = position
            vertex.normal = (0.0, 0.0, 1.0)
            vertex.uv = (0.0, 0.0)
            vertex.bone_indices = (0, 0)
            vertex.bone_weight = 100
            pmd.vertices.append(vertex)

        face = PmdFace()
        face.indices = (0, 1, 2)
        pmd.faces.append(face)

        material = PmdMaterial(0)
        material.face_count = 3
        pmd.materials.append(material)

        bone = PmdBone()
        bone.name = "root"
        bone.parent_bone_index = -1
        bone.tail_pos_bone_index = 0xFFFF
        bone.bone_type = PmdBoneType.ROTATE_AND_MOVE
        bone.ik_parent_bone_index = 0
        pmd.bones.append(bone)

        display_frame = PmdDisplayFrame()
        pmd.display_frames.append(display_frame)
        native_bytes = exporter._try_native_export(pmd)
        self.assertIsNone(native_bytes)
        self.assertEqual(calls, [])

    def test_export_quad_triangulation(self):
        """quad dictをfan triangulate -> export -> parse_file で検証"""
        data = {
            "model_name": "QuadTest",
            "vertices": [
                {"position": [0.0, 0.0, 0.0]},
                {"position": [1.0, 0.0, 0.0]},
                {"position": [1.0, 1.0, 0.0]},
                {"position": [0.0, 1.0, 0.0]},
            ],
            "faces": [[0, 1, 2, 3]],  # quad
        }
        out_path = os.path.join(self.temp_dir, "quad.pmd")
        self.exporter.export_pmd_model(out_path, data)

        pmd = PmdData()
        pmd.parse_file(out_path)

        self.assertEqual(len(pmd.vertices), 4)
        self.assertEqual(len(pmd.faces), 2)  # quad -> 2 triangles
        self.assertEqual(pmd.materials[0].face_count, 6)  # 2 面 * 3
        self.assertEqual(pmd.faces[0].indices, (0, 1, 2))
        self.assertEqual(pmd.faces[1].indices, (0, 2, 3))

    def test_export_allows_highest_valid_vertex_index(self):
        """PMD の unsigned 16bit index 上限 65535 を参照できる。"""
        vertices = [{"position": [0.0, 0.0, 0.0]} for _ in range(0x10000)]
        data = {
            "model_name": "MaxIndex",
            "vertices": vertices,
            "faces": [[0xFFFD, 0xFFFE, 0xFFFF]],
        }
        out_path = os.path.join(self.temp_dir, "max_index.pmd")
        self.exporter.export_pmd_model(out_path, data)

        pmd = PmdData()
        pmd.parse_file(out_path)

        self.assertEqual(len(pmd.vertices), 0x10000)
        self.assertEqual(pmd.faces[0].indices, (0xFFFD, 0xFFFE, 0xFFFF))

    def test_export_rejects_vertex_count_above_pmd_index_range(self):
        """65537 頂点以上は PMD の face index で表現できないため拒否する。"""
        vertices = [{"position": [0.0, 0.0, 0.0]} for _ in range(0x10001)]
        with self.assertRaises(MMDExportException):
            self.exporter.export_pmd_model(
                os.path.join(self.temp_dir, "too_many_vertices.pmd"),
                {"vertices": vertices, "faces": [[0, 1, 2]]},
            )

    def test_export_material_face_count_none_gets_remaining_indices(self):
        """face_count=None は未指定扱いで残りインデックス数を割り当てる。"""
        data = {
            "model_name": "MatNone",
            "vertices": [
                {"position": [0.0, 0.0, 0.0]},
                {"position": [1.0, 0.0, 0.0]},
                {"position": [0.0, 1.0, 0.0]},
            ],
            "faces": [[0, 1, 2]],
            "materials": [
                {"name": "MatA", "face_count": None},
                {"name": "MatB", "face_count": 0},
            ],
        }
        out_path = os.path.join(self.temp_dir, "mat_none.pmd")
        self.exporter.export_pmd_model(out_path, data)

        pmd = PmdData()
        pmd.parse_file(out_path)

        self.assertEqual(len(pmd.materials), 2)
        self.assertEqual(pmd.materials[0].face_count, 3)
        self.assertEqual(pmd.materials[1].face_count, 0)

    def test_export_pmx_style_material_toon_minus_one_is_clamped(self):
        """PMX互換 material の toon_texture_index=-1 は PMD の 0 に正規化する。"""
        data = {
            "model_name": "PmxStyleMaterial",
            "vertices": [
                {"position": [0.0, 0.0, 0.0]},
                {"position": [1.0, 0.0, 0.0]},
                {"position": [0.0, 1.0, 0.0]},
            ],
            "faces": [[0, 1, 2]],
            "materials": [
                {
                    "name": "MatA",
                    "toon_texture_index": -1,
                    "face_count": 3,
                }
            ],
        }
        out_path = os.path.join(self.temp_dir, "toon_minus_one.pmd")
        self.exporter.export_pmd_model(out_path, data)

        pmd = PmdData()
        pmd.parse_file(out_path)

        self.assertEqual(pmd.materials[0].toon_texture_index, 0)

    def test_export_custom_material_bone_header_roundtrip(self):
        """ヘッダ英名・材質・複数ボーンを指定した dict の roundtrip"""
        data = {
            "model_name": "CustomModel",
            "model_name_english": "ModelEN",
            "comment": "JP comment",
            "comment_english": "EN comment",
            "vertices": [
                {
                    "position": [0.0, 0.0, 0.0],
                    "normal": [0.0, 0.0, 1.0],
                    "uv": [0.0, 0.0],
                    "bone_indices": [0, 1],
                    "bone_weights": [0.75, 0.25],
                },
                {
                    "position": [1.0, 0.0, 0.0],
                    "normal": [0.0, 0.0, 1.0],
                    "uv": [1.0, 0.0],
                    "bone_indices": [0, 1],
                    "bone_weight": 40,
                },
                {
                    "position": [0.0, 1.0, 0.0],
                    "normal": [0.0, 0.0, 1.0],
                    "uv": [0.0, 1.0],
                    "edge_flag": 1,
                },
            ],
            "faces": [[0, 1, 2]],
            "materials": [
                {
                    "name": "MatA",
                    "diffuse": [1.0, 0.0, 0.0, 1.0],
                    "specular_power": 12.0,
                    "specular": [0.1, 0.2, 0.3],
                    "ambient": [0.4, 0.5, 0.6],
                    "toon_texture_index": 1,
                    "edge_flag": 1,
                    "texture_file_name": "tex.bmp",
                }
            ],
            "bones": [
                {"name": "root", "name_english": "Root", "position": [0.0, 0.0, 0.0]},
                {
                    "name": "arm",
                    "name_english": "Arm",
                    "parent_index": 0,
                    "tail_pos_bone_index": 0,
                    "bone_type": PmdBoneType.ROTATE_AND_MOVE,
                    "ik_parent_bone_index": 0,
                    "position": [1.0, 2.0, 3.0],
                },
            ],
        }
        out_path = os.path.join(self.temp_dir, "custom.pmd")
        self.exporter.export_pmd_model(out_path, data)

        pmd = PmdData()
        pmd.parse_file(out_path)

        # ヘッダ
        self.assertEqual(pmd.header.model_name, "CustomModel")
        self.assertEqual(pmd.header.model_name_english, "ModelEN")
        self.assertEqual(pmd.header.comment, "JP comment")
        self.assertEqual(pmd.header.comment_english, "EN comment")

        # 材質
        self.assertEqual(len(pmd.materials), 1)
        mat = pmd.materials[0]
        self.assertAlmostEqual(mat.diffuse[0], 1.0)
        self.assertAlmostEqual(mat.diffuse[1], 0.0)
        self.assertAlmostEqual(mat.specular_power, 12.0)
        self.assertAlmostEqual(mat.specular[0], 0.1, places=5)
        self.assertAlmostEqual(mat.ambient[2], 0.6, places=5)
        self.assertEqual(mat.toon_texture_index, 1)
        self.assertEqual(mat.edge_flag, 1)
        self.assertEqual(mat.texture_file_name, "tex.bmp")
        self.assertEqual(mat.face_count, 3)

        # 頂点の重み
        self.assertEqual(pmd.vertices[0].bone_indices, (0, 1))
        self.assertEqual(pmd.vertices[0].bone_weight, 75)  # 0.75 -> 75
        self.assertEqual(pmd.vertices[1].bone_weight, 40)  # int そのまま
        self.assertEqual(pmd.vertices[2].edge_flag, 1)

        # ボーン
        self.assertEqual(len(pmd.bones), 2)
        self.assertEqual(pmd.bones[0].name, "root")
        self.assertEqual(pmd.bones[0].name_english, "Root")
        self.assertEqual(pmd.bones[0].parent_bone_index, -1)
        self.assertEqual(pmd.bones[1].name, "arm")
        self.assertEqual(pmd.bones[1].name_english, "Arm")
        self.assertEqual(pmd.bones[1].parent_bone_index, 0)
        self.assertEqual(pmd.bones[1].bone_type, PmdBoneType.ROTATE_AND_MOVE)
        self.assertAlmostEqual(pmd.bones[1].position[0], 1.0)
        self.assertAlmostEqual(pmd.bones[1].position[1], 2.0)
        self.assertAlmostEqual(pmd.bones[1].position[2], 3.0)

    def test_export_empty_vertices_raises(self):
        """vertices空でValueError"""
        with self.assertRaises(MMDExportException):
            self.exporter.export_pmd_model(
                os.path.join(self.temp_dir, "empty.pmd"),
                {"vertices": [], "faces": [[0, 1, 2]]},
            )

    def test_export_empty_faces_raises(self):
        """faces空でValueError"""
        with self.assertRaises(MMDExportException):
            self.exporter.export_pmd_model(
                os.path.join(self.temp_dir, "empty.pmd"),
                {"vertices": [{"position": [0.0, 0.0, 0.0]}], "faces": []},
            )

    def test_export_face_vertex_index_out_of_range_raises(self):
        """face の vertex index が頂点数を超える場合は ValueError"""
        data = {
            "model_name": "BadFaceIndex",
            "vertices": [
                {"position": [0.0, 0.0, 0.0]},
                {"position": [1.0, 0.0, 0.0]},
                {"position": [0.0, 1.0, 0.0]},
            ],
            "faces": [[0, 1, 3]],
        }
        with self.assertRaises(MMDExportException):
            self.exporter.export_pmd_model(
                os.path.join(self.temp_dir, "bad_face_index.pmd"),
                data,
            )

    def test_export_bone_index_out_of_range_raises(self):
        """vertex の bone index が bone 数を超える場合は ValueError"""
        data = {
            "model_name": "BadBoneIndex",
            "vertices": [
                {"position": [0.0, 0.0, 0.0], "bone_indices": [2, 0]},
                {"position": [1.0, 0.0, 0.0]},
                {"position": [0.0, 1.0, 0.0]},
            ],
            "faces": [[0, 1, 2]],
            "bones": [{"name": "root"}],
        }
        with self.assertRaises(MMDExportException):
            self.exporter.export_pmd_model(
                os.path.join(self.temp_dir, "bad_bone_index.pmd"),
                data,
            )

    def test_export_empty_bones_raises(self):
        """bones が指定されているが空の場合は ValueError"""
        data = {
            "model_name": "EmptyBones",
            "vertices": [
                {"position": [0.0, 0.0, 0.0]},
                {"position": [1.0, 0.0, 0.0]},
                {"position": [0.0, 1.0, 0.0]},
            ],
            "faces": [[0, 1, 2]],
            "bones": [],
        }
        with self.assertRaises(MMDExportException):
            self.exporter.export_pmd_model(
                os.path.join(self.temp_dir, "empty_bones.pmd"),
                data,
            )

    def test_fan_triangulate(self):
        """_fan_triangulate helper"""
        self.assertEqual(_fan_triangulate([0, 1, 2, 3]), [[0, 1, 2], [0, 2, 3]])
        self.assertEqual(
            _fan_triangulate([0, 1, 2, 3, 4]),
            [[0, 1, 2], [0, 2, 3], [0, 3, 4]],
        )
        self.assertEqual(_fan_triangulate([0, 1, 2]), [[0, 1, 2]])

    def test_normalize_bone_weight(self):
        """_normalize_bone_weight helper"""
        self.assertEqual(_normalize_bone_weight({"bone_weight": 60}), 60)
        self.assertEqual(_normalize_bone_weight({"bone_weights": [0.75, 0.25]}), 75)
        self.assertEqual(_normalize_bone_weight({}), 100)  # default
        self.assertEqual(_normalize_bone_weight({"bone_weight": 200}), 100)  # clamp
        self.assertEqual(_normalize_bone_weight({"bone_weight": -10}), 0)  # clamp
