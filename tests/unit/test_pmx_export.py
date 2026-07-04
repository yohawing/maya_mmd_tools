"""
PMXエクスポート機能のユニットテスト
モックデータを使用したラウンドトリップテストを実行
"""

import importlib.machinery
import importlib.util
import os

from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.core.pmx_data import PmxData

# Load PmxExporter directly without triggering io/__init__.py's maya imports
_pmx_exporter_path = os.path.join(
    os.path.dirname(__file__), "..", "..", "mmd_tools", "io", "pmx_exporter.py"
)
_loader = importlib.machinery.SourceFileLoader("mmd_tools.io.pmx_exporter", os.path.abspath(_pmx_exporter_path))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
_pmx_exporter_mod = importlib.util.module_from_spec(_spec)
_loader.exec_module(_pmx_exporter_mod)
PmxExporter = _pmx_exporter_mod.PmxExporter
_choose_index_size = _pmx_exporter_mod._choose_index_size
_choose_reference_index_size = _pmx_exporter_mod._choose_reference_index_size
_fan_triangulate = _pmx_exporter_mod._fan_triangulate
from mmd_tools.core.pmx_data.vertex import PmxVertex
from mmd_tools.core.pmx_data.face import PmxFace
from mmd_tools.core.pmx_data.material import PmxMaterial
from mmd_tools.core.pmx_data.bone import PmxBone, PmxBoneFlag
from mmd_tools.core.pmx_data.morph import PmxMorphType
from mmd_tools.core.pmx_data.display_frame import PmxDisplayFrame
from tests.common.test_base import TestBase
from tests.common.pmx_mock import PmxMock


def _parse_pmx(path):
    """Read exporter output with the legacy PMX reader for writer roundtrip checks."""
    return parse_pmx_file(
        path,
        use_native_pmx_parse=False,
        require_native_pmx_parse=False,
    )


class TestPmxExport(TestBase):
    """PMXエクスポート機能のテストクラス"""

    def test_pmx_round_trip_with_minimal_mock(self):
        """モックデータを使用したPMXファイルのラウンドトリップテスト"""

        # モックデータを作成
        mock_data = PmxMock.create_minimal_pmx(version=2.0)

        # 1. モックデータを一時ファイルに書き込む
        tmp_input_path = os.path.join(self.temp_dir, "test_input.pmx")
        with open(tmp_input_path, "wb") as f:
            f.write(mock_data)

        # モックデータからPMXを読み込む
        parser1 = _parse_pmx(tmp_input_path)

        # 2. 一時ファイルに書き込む
        tmp_path = os.path.join(self.temp_dir, "test_export.pmx")
        parser1.write_file(tmp_path)

        # 3. 書き込んだファイルを再度読み込む
        parser2 = _parse_pmx(tmp_path)

        # 4. データの一致を確認
        # ヘッダー情報の比較
        self.assertEqual(
            parser1.header.model_name,
            parser2.header.model_name,
            "モデル名が一致しません",
        )
        self.assertEqual(parser1.header.comment, parser2.header.comment, "コメントが一致しません")
        self.assertAlmostEqual(
            parser1.header.version,
            parser2.header.version,
            places=3,
            msg="バージョンが一致しません",
        )

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

    def test_pmx_round_trip_with_full_mock(self):
        """フル機能モックデータを使用したPMXファイルのラウンドトリップテスト"""

        # モックデータを作成
        mock_data = PmxMock.create_full_pmx(version=2.1)

        # 1. モックデータを一時ファイルに書き込む
        tmp_input_path = os.path.join(self.temp_dir, "test_input.pmx")
        with open(tmp_input_path, "wb") as f:
            f.write(mock_data)

        # モックデータからPMXを読み込む
        parser1 = _parse_pmx(tmp_input_path)

        # 2. 一時ファイルに書き込む
        tmp_path = os.path.join(self.temp_dir, "test_export_full.pmx")
        parser1.write_file(tmp_path)

        # 3. 書き込んだファイルを再度読み込む
        parser2 = _parse_pmx(tmp_path)

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
        self.assertEqual(len(parser1.morphs), len(parser2.morphs), "モーフ数が一致しません")
        self.assertEqual(
            len(parser1.display_frames),
            len(parser2.display_frames),
            "表示枠数が一致しません",
        )

    def test_create_simple_pmx(self):
        """簡単なPMXファイルを作成してエクスポートするテスト"""

        # 新しいPMXパーサーインスタンスを作成
        parser = PmxData()

        # ヘッダー情報を設定
        parser.header.magic = b"PMX "
        parser.header.version = 2.0
        parser.header.model_name = "TestModel"
        parser.header.model_name_english = "TestModel"
        parser.header.comment = "This is a test model created by export test"
        parser.header.comment_english = "This is a test model created by export test"

        # インデックスサイズを設定（小さいモデルなので1バイトで十分）
        parser.header.vertex_index_size = 1
        parser.header.texture_index_size = 1
        parser.header.material_index_size = 1
        parser.header.bone_index_size = 1
        parser.header.morph_index_size = 1
        parser.header.rigid_body_index_size = 1

        # 簡単な三角形の頂点を追加
        # 頂点1
        v1 = PmxVertex(
            bone_index_size=parser.header.bone_index_size,
            additional_uv_count=parser.header.additional_uv,
        )
        v1.position = [0.0, 0.0, 0.0]
        v1.normal = [0.0, 1.0, 0.0]
        v1.uv = [0.0, 0.0]
        v1.weight_transform_type = 0  # BDEF1
        v1.bone_indices = [0]
        v1.bone_weights = []
        v1.edge_magnification = 1.0
        parser.vertices.append(v1)

        # 頂点2
        v2 = PmxVertex(
            bone_index_size=parser.header.bone_index_size,
            additional_uv_count=parser.header.additional_uv,
        )
        v2.position = [1.0, 0.0, 0.0]
        v2.normal = [0.0, 1.0, 0.0]
        v2.uv = [1.0, 0.0]
        v2.weight_transform_type = 0  # BDEF1
        v2.bone_indices = [0]
        v2.bone_weights = []
        v2.edge_magnification = 1.0
        parser.vertices.append(v2)

        # 頂点3
        v3 = PmxVertex(
            bone_index_size=parser.header.bone_index_size,
            additional_uv_count=parser.header.additional_uv,
        )
        v3.position = [0.0, 0.0, 1.0]
        v3.normal = [0.0, 1.0, 0.0]
        v3.uv = [0.0, 1.0]
        v3.weight_transform_type = 0  # BDEF1
        v3.bone_indices = [0]
        v3.bone_weights = []
        v3.edge_magnification = 1.0
        parser.vertices.append(v3)

        # 面（三角形）を追加
        face = PmxFace(parser.header.vertex_index_size)
        face.indices = [0, 1, 2]
        parser.faces.append(face)

        # マテリアルを追加
        material = PmxMaterial(
            parser.header.texture_index_size,
            parser.header.encoding_flag,
            material_index=0,
        )
        material.name = "Material1"
        material.name_english = "Material1"
        material.diffuse = [0.8, 0.8, 0.8, 1.0]
        material.specular = [0.5, 0.5, 0.5]
        material.specular_coefficient = 5.0
        material.ambient = [0.3, 0.3, 0.3]
        material.draw_flag = 0x01  # 両面描画
        material.edge_color = [0.0, 0.0, 0.0, 1.0]
        material.edge_size = 1.0
        material.texture_index = -1
        material.sphere_mode = 0
        material.sphere_texture_index = -1
        material.shared_toon_flag = 0
        material.toon_texture_index = 0
        material.memo = ""
        material.face_count = 1  # 1面（3頂点）
        parser.materials.append(material)

        # 最低限のボーンを追加（ルートボーン）
        bone = PmxBone(parser.header.bone_index_size, parser.header.encoding_flag)
        bone.name = "root"
        bone.name_english = "root"
        bone.position = [0.0, 0.0, 0.0]
        bone.parent_bone_index = -1
        bone.transform_layer = 0
        bone.bone_flag = 0x0001  # 接続先表示
        bone.connect_position_offset = [0.0, 1.0, 0.0]  # 接続先表示の場合は位置を設定
        parser.bones.append(bone)

        # 表示枠を追加（必須：Root, 表情）
        # Root表示枠
        root_frame = PmxDisplayFrame(
            parser.header.bone_index_size,
            parser.header.morph_index_size,
            parser.header.encoding_flag,
        )
        root_frame.name = "Root"
        root_frame.name_english = "Root"
        root_frame.special_flag = 1  # 特殊枠
        parser.display_frames.append(root_frame)

        # 表情枠
        exp_frame = PmxDisplayFrame(
            parser.header.bone_index_size,
            parser.header.morph_index_size,
            parser.header.encoding_flag,
        )
        exp_frame.name = "表情"
        exp_frame.name_english = "Exp"
        exp_frame.special_flag = 1  # 特殊枠
        parser.display_frames.append(exp_frame)

        # 一時ファイルに書き込む
        tmp_path = os.path.join(self.temp_dir, "test_created.pmx")
        parser.write_file(tmp_path)

        # 書き込んだファイルを読み込んで確認
        parser2 = _parse_pmx(tmp_path)

        # データの検証
        self.assertEqual(parser2.header.model_name, "TestModel")
        self.assertEqual(parser2.header.version, 2.0)
        self.assertEqual(len(parser2.vertices), 3)
        self.assertEqual(len(parser2.faces), 1)
        self.assertEqual(len(parser2.materials), 1)
        self.assertEqual(len(parser2.bones), 1)


class TestPmxExporterFromDict(TestBase):
    """PmxExporter.export_pmx_model() をdict入力から呼ぶテスト"""

    def setUp(self):
        super().setUp()
        self.exporter = PmxExporter()

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
        out_path = os.path.join(self.temp_dir, "triangle.pmx")
        self.exporter.export_pmx_model(out_path, data)

        pmx = _parse_pmx(out_path)

        self.assertEqual(pmx.header.model_name, "TriangleTest")
        self.assertEqual(pmx.header.version, 2.0)
        self.assertEqual(len(pmx.vertices), 3)
        self.assertEqual(len(pmx.faces), 1)
        self.assertEqual(len(pmx.materials), 1)
        self.assertEqual(pmx.materials[0].face_count, 3)
        self.assertEqual(len(pmx.bones), 1)
        self.assertEqual(len(pmx.display_frames), 2)

        # Verify vertex data
        v = pmx.vertices[0]
        self.assertEqual(v.position, (0.0, 0.0, 0.0))
        self.assertEqual(v.normal, (0.0, 0.0, 1.0))
        self.assertEqual(v.uv, (0.0, 0.0))
        self.assertEqual(v.weight_transform_type, 0)  # BDEF1
        self.assertEqual(v.bone_indices, [0])
        self.assertAlmostEqual(v.edge_magnification, 1.0)

        # Verify face
        self.assertEqual(pmx.faces[0].indices, (0, 1, 2))

    def test_exporter_uses_native_parts_writer_for_basic_model(self):
        """basic PMX は native parts writer に flat buffers と descriptor を渡す。"""
        calls = []

        def native_parts_exporter(metadata, positions, normals, uvs, **kwargs):
            calls.append(
                {
                    "metadata": metadata,
                    "positions": positions,
                    "normals": normals,
                    "uvs": uvs,
                    **kwargs,
                }
            )
            return b"NATIVE-PMX"

        exporter = PmxExporter(native_parts_exporter=native_parts_exporter)
        out_path = os.path.join(self.temp_dir, "native_parts.pmx")
        exporter.export_pmx_model(
            out_path,
            {
                "model_name": "NativePmx",
                "vertices": [
                    {"position": [0, 0, 0], "normal": [0, 0, 1], "uv": [0, 0], "bone_indices": [0]},
                    {
                        "position": [1, 0, 0],
                        "normal": [0, 0, 1],
                        "uv": [1, 0],
                        "bone_indices": [0, 0],
                        "bone_weights": [0.25],
                    },
                    {
                        "position": [0, 1, 0],
                        "normal": [0, 0, 1],
                        "uv": [0, 1],
                        "bone_indices": [0, 0, 0, 0],
                        "bone_weights": [0.25, 0.25, 0.25, 0.25],
                    },
                ],
                "faces": [[0, 1, 2]],
                "materials": [{"name": "mat", "face_count": 3}],
            },
        )

        with open(out_path, "rb") as handle:
            self.assertEqual(handle.read(), b"NATIVE-PMX")
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual(call["metadata"]["name"], "NativePmx")
        self.assertEqual(call["metadata"]["materials"][0]["faceCount"], 1)
        self.assertEqual(call["metadata"]["bones"][0]["name"], "root")
        self.assertEqual(call["positions"], [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0])
        self.assertEqual(call["indices"], [0, 1, 2])
        self.assertEqual(call["skin_indices"], [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
        self.assertEqual(call["skin_weights"], [1.0, 0.0, 0.0, 0.0, 0.25, 0.75, 0.0, 0.0, 0.25, 0.25, 0.25, 0.25])

    def test_exporter_includes_supported_rich_parts_in_native_descriptor(self):
        """vertex morph / rigid body / joint は native parts descriptor に含める。"""
        calls = []
        exporter = PmxExporter(
            native_parts_exporter=lambda metadata, positions, normals, uvs, **kwargs: calls.append(metadata) or b"NATIVE-PMX"
        )
        out_path = os.path.join(self.temp_dir, "native_rich_parts.pmx")
        exporter.export_pmx_model(
            out_path,
            {
                "model_name": "NativeRichPmx",
                "vertices": [
                    {"position": [0, 0, 0], "normal": [0, 0, 1], "uv": [0, 0]},
                    {"position": [1, 0, 0], "normal": [0, 0, 1], "uv": [1, 0]},
                    {"position": [0, 1, 0], "normal": [0, 0, 1], "uv": [0, 1]},
                ],
                "faces": [[0, 1, 2]],
                "morphs": [
                    {
                        "type": "vertex",
                        "name": "smile",
                        "offsets": [{"vertex_index": 0, "position_offset": [0.0, 0.1, 0.0]}],
                    }
                ],
                "rigid_bodies": [{"name": "body", "shape_type": 1, "physics_mode": 2}],
                "joints": [{"name": "joint", "rigid_body_a_index": 0, "rigid_body_b_index": -1}],
            },
        )

        self.assertEqual(len(calls), 1)
        metadata = calls[0]
        self.assertEqual(metadata["morphs"][0]["kind"], "vertex")
        self.assertEqual(metadata["morphs"][0]["vertexOffsets"][0]["position"], [0.0, 0.1, 0.0])
        self.assertEqual(metadata["rigidBodies"][0]["shape"], "box")
        self.assertEqual(metadata["rigidBodies"][0]["mode"], "dynamicBone")
        self.assertEqual(metadata["joints"][0]["type"], "generic6dofSpring")

    def test_exporter_falls_back_for_sections_not_supported_by_parts_path(self):
        """material morph 等の parts ABI 未対応 PMX は従来 writer に戻る。"""
        calls = []
        exporter = PmxExporter(native_parts_exporter=lambda *args, **kwargs: calls.append((args, kwargs)) or b"NATIVE-PMX")
        out_path = os.path.join(self.temp_dir, "native_parts_fallback.pmx")
        exporter.export_pmx_model(
            out_path,
            {
                "model_name": "FallbackPmx",
                "vertices": [
                    {"position": [0, 0, 0], "normal": [0, 0, 1], "uv": [0, 0]},
                    {"position": [1, 0, 0], "normal": [0, 0, 1], "uv": [1, 0]},
                    {"position": [0, 1, 0], "normal": [0, 0, 1], "uv": [0, 1]},
                ],
                "faces": [[0, 1, 2]],
                "morphs": [
                    {
                        "type": "material",
                        "name": "hide_mat",
                        "offsets": [{"material_index": 0}],
                    }
                ],
            },
        )

        self.assertEqual(calls, [])
        pmx = _parse_pmx(out_path)
        self.assertEqual(pmx.header.model_name, "FallbackPmx")
        self.assertEqual(len(pmx.morphs), 1)
        self.assertEqual(int(pmx.morphs[0].morph_type), 8)

    def test_export_display_frame_name_roundtrip_matches_header_encoding(self):
        """dict export でヘッダと同じ encoding_flag で表示枠名を保存できる"""
        data = {
            "model_name": "DisplayFrameEncodingTest",
            "vertices": [
                {"position": [0.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [0.0, 0.0]},
                {"position": [1.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [1.0, 0.0]},
                {"position": [0.0, 1.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [0.0, 1.0]},
            ],
            "faces": [[0, 1, 2]],
        }
        out_path = os.path.join(self.temp_dir, "display_encoding.pmx")
        self.exporter.export_pmx_model(out_path, data)

        pmx = _parse_pmx(out_path)

        self.assertEqual(pmx.header.encoding_flag, 0)
        self.assertEqual(len(pmx.display_frames), 2)
        self.assertEqual(pmx.display_frames[1].name, "表情")

    def test_export_custom_display_frames_roundtrip(self):
        """dict export は任意表示枠と bone / morph 要素を保持する"""
        data = {
            "model_name": "DisplayFrameRoundtripTest",
            "vertices": [
                {"position": [0.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [0.0, 0.0]},
                {"position": [1.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [1.0, 0.0]},
                {"position": [0.0, 1.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [0.0, 1.0]},
            ],
            "faces": [[0, 1, 2]],
            "bones": [
                {"name": "センター", "name_english": "Center", "position": [0.0, 0.0, 0.0]},
                {"name": "上半身", "name_english": "UpperBody", "position": [0.0, 1.0, 0.0]},
            ],
            "morphs": [
                {
                    "type": "vertex",
                    "name": "笑い",
                    "name_english": "Smile",
                    "panel": 3,
                    "offsets": [{"vertex_index": 1, "position_offset": [0.1, 0.0, 0.0]}],
                }
            ],
            "display_frames": [
                {
                    "name": "Root",
                    "name_english": "Root",
                    "special_flag": 1,
                    "elements": [{"type": 0, "index": 0}],
                },
                {
                    "name": "表情",
                    "name_english": "Exp",
                    "special_flag": 1,
                    "elements": [{"type": 1, "index": 0}],
                },
                {
                    "name": "操作",
                    "name_english": "Controls",
                    "special_flag": 0,
                    "elements": [
                        {"type": 0, "index": 1},
                        {"type": 1, "index": 0},
                    ],
                },
            ],
        }
        out_path = os.path.join(self.temp_dir, "custom_display_frames.pmx")
        self.exporter.export_pmx_model(out_path, data)

        pmx = _parse_pmx(out_path)

        self.assertEqual([frame.name for frame in pmx.display_frames], ["Root", "表情", "操作"])
        self.assertEqual(pmx.display_frames[2].name_english, "Controls")
        self.assertEqual(pmx.display_frames[2].special_flag, 0)
        self.assertEqual(
            pmx.display_frames[2].elements,
            [{"type": 0, "index": 1}, {"type": 1, "index": 0}],
        )

    def test_export_display_frame_bone_index_out_of_range_raises(self):
        """表示枠の bone index が範囲外なら ValueError"""
        data = {
            "model_name": "BadDisplayFrameBone",
            "vertices": [
                {"position": [0.0, 0.0, 0.0]},
                {"position": [1.0, 0.0, 0.0]},
                {"position": [0.0, 1.0, 0.0]},
            ],
            "faces": [[0, 1, 2]],
            "bones": [{"name": "root"}],
            "display_frames": [
                {"name": "Bad", "elements": [{"type": 0, "index": 1}]},
            ],
        }
        with self.assertRaises(ValueError):
            self.exporter.export_pmx_model(
                os.path.join(self.temp_dir, "bad_display_frame_bone.pmx"),
                data,
            )

    def test_export_roundtrip_keeps_supported_field_values(self):
        """ヘッダ英名 / 材質英名・flags / 接続位置オフセットを保持する"""
        data = {
            "model_name": "TestModel",
            "model_name_english": "TestModelEnglish",
            "comment": "JP comment",
            "comment_english": "EN comment",
            "vertices": [
                {
                    "position": [0.0, 0.0, 0.0],
                    "normal": [0.0, 0.0, 1.0],
                    "uv": [0.0, 0.0],
                    "bone_indices": [0, 0],
                    "bone_weights": [1.0, 0.0],
                },
                {
                    "position": [1.0, 0.0, 0.0],
                    "normal": [0.0, 0.0, 1.0],
                    "uv": [1.0, 0.0],
                },
                {
                    "position": [0.0, 1.0, 0.0],
                    "normal": [0.0, 0.0, 1.0],
                    "uv": [0.0, 1.0],
                },
            ],
            "faces": [[0, 1, 2]],
            "textures": ["material.png"],
            "materials": [
                {
                    "name": "MatA",
                    "name_english": "MaterialEN",
                    "diffuse": [1.0, 0.0, 0.0, 1.0],
                    "draw_flag": 0x12,
                    "edge_color": [0.1, 0.2, 0.3, 0.4],
                    "edge_size": 2.5,
                    "texture_index": 0,
                    "sphere_mode": 1,
                    "shared_toon_flag": 1,
                    "toon_texture_index": 0,
                    "memo": "unit test",
                }
            ],
            "bones": [
                {
                    "name": "root",
                    "position": [0.0, 0.0, 0.0],
                    "connect_position_offset": [0.0, 4.999999523162842, 0.0],
                    "bone_flag": int(PmxBoneFlag.DISPLAY | PmxBoneFlag.OPERATABLE),
                }
            ],
        }
        out_path = os.path.join(self.temp_dir, "supported_fields.pmx")
        self.exporter.export_pmx_model(out_path, data)

        pmx = _parse_pmx(out_path)

        self.assertEqual(pmx.header.model_name_english, "TestModelEnglish")
        self.assertEqual(pmx.header.comment, "JP comment")
        self.assertEqual(pmx.header.comment_english, "EN comment")
        self.assertEqual(pmx.materials[0].name_english, "MaterialEN")
        self.assertEqual(pmx.materials[0].draw_flag, 0x12)
        self.assertEqual(pmx.materials[0].edge_size, 2.5)
        self.assertAlmostEqual(pmx.materials[0].edge_color[0], 0.1)
        self.assertAlmostEqual(pmx.materials[0].edge_color[1], 0.2)
        self.assertAlmostEqual(pmx.materials[0].edge_color[2], 0.3)
        self.assertAlmostEqual(pmx.materials[0].edge_color[3], 0.4)
        self.assertEqual(pmx.materials[0].texture_index, 0)
        self.assertEqual(pmx.materials[0].sphere_mode, 1)
        self.assertEqual(pmx.materials[0].shared_toon_flag, 1)
        self.assertEqual(pmx.materials[0].toon_texture_index, 0)
        self.assertEqual(pmx.materials[0].memo, "unit test")
        self.assertEqual(
            pmx.bones[0].connect_position_offset,
            (0.0, 4.999999523162842, 0.0),
        )

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
        out_path = os.path.join(self.temp_dir, "quad.pmx")
        self.exporter.export_pmx_model(out_path, data)

        pmx = _parse_pmx(out_path)

        self.assertEqual(len(pmx.vertices), 4)
        self.assertEqual(len(pmx.faces), 2)  # quad -> 2 triangles
        self.assertEqual(pmx.materials[0].face_count, 6)
        self.assertEqual(pmx.faces[0].indices, (0, 1, 2))
        self.assertEqual(pmx.faces[1].indices, (0, 2, 3))

    def test_export_empty_vertices_raises(self):
        """vertices空でValueError"""
        with self.assertRaises(ValueError):
            self.exporter.export_pmx_model(
                os.path.join(self.temp_dir, "empty.pmx"),
                {"vertices": [], "faces": [[0, 1, 2]]},
            )

    def test_export_empty_faces_raises(self):
        """faces空でValueError"""
        with self.assertRaises(ValueError):
            self.exporter.export_pmx_model(
                os.path.join(self.temp_dir, "empty.pmx"),
                {"vertices": [{"position": [0.0, 0.0, 0.0]}], "faces": []},
            )

    def test_export_multi_material_face_count(self):
        """複数マテリアルでface_countが指定されない場合の挙動"""
        data = {
            "model_name": "MultiMat",
            "vertices": [
                {"position": [0.0, 0.0, 0.0]},
                {"position": [1.0, 0.0, 0.0]},
                {"position": [0.0, 1.0, 0.0]},
                {"position": [1.0, 1.0, 0.0]},
            ],
            "faces": [[0, 1, 2], [1, 3, 2]],
            "materials": [
                {"name": "MatA", "diffuse": [1.0, 0.0, 0.0, 1.0]},
                {"name": "MatB", "diffuse": [0.0, 1.0, 0.0, 1.0]},
            ],
        }
        out_path = os.path.join(self.temp_dir, "multimat.pmx")
        self.exporter.export_pmx_model(out_path, data)

        pmx = _parse_pmx(out_path)

        self.assertEqual(len(pmx.materials), 2)
        self.assertEqual(pmx.materials[0].face_count, 6)  # first gets all index count
        self.assertEqual(pmx.materials[1].face_count, 0)

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
        out_path = os.path.join(self.temp_dir, "mat_none.pmx")
        self.exporter.export_pmx_model(out_path, data)

        pmx = _parse_pmx(out_path)

        self.assertEqual(len(pmx.materials), 2)
        self.assertEqual(pmx.materials[0].face_count, 3)
        self.assertEqual(pmx.materials[1].face_count, 0)

    def test_export_with_textures(self):
        """テクスチャ指定あり"""
        data = {
            "model_name": "TexTest",
            "vertices": [
                {"position": [0.0, 0.0, 0.0]},
                {"position": [1.0, 0.0, 0.0]},
                {"position": [0.0, 1.0, 0.0]},
            ],
            "faces": [[0, 1, 2]],
            "textures": ["tex_a.png", "tex_b.png"],
            "materials": [
                {"name": "WithTex", "diffuse": [1.0, 1.0, 1.0, 1.0]},
            ],
        }
        out_path = os.path.join(self.temp_dir, "tex.pmx")
        self.exporter.export_pmx_model(out_path, data)

        pmx = _parse_pmx(out_path)

        self.assertEqual(len(pmx.textures), 2)
        self.assertEqual(pmx.textures[0], "tex_a.png")
        self.assertEqual(pmx.textures[1], "tex_b.png")

    def test_choose_index_size(self):
        """_choose_index_size helper"""
        self.assertEqual(_choose_index_size(0), 1)
        self.assertEqual(_choose_index_size(0xFF), 1)
        self.assertEqual(_choose_index_size(0x100), 2)
        self.assertEqual(_choose_index_size(0xFFFF), 2)
        self.assertEqual(_choose_index_size(0x10000), 4)

    def test_choose_reference_index_size(self):
        """_choose_reference_index_size helper"""
        self.assertEqual(_choose_reference_index_size(0), 1)
        self.assertEqual(_choose_reference_index_size(0x7F), 1)
        self.assertEqual(_choose_reference_index_size(0x80), 2)
        self.assertEqual(_choose_reference_index_size(0x7FFF), 2)
        self.assertEqual(_choose_reference_index_size(0x8000), 4)

    def test_export_vertex_index_size_uses_unsigned_cutoff(self):
        """vertex index size は unsigned cutoff を使う"""
        vertex_count = 0x100
        data = {
            "model_name": "VertexIndexUnsignedCutoff",
            "vertices": [{"position": [0.0, 0.0, 0.0]} for _ in range(vertex_count)],
            "faces": [[0, 1, 2]],
        }
        out_path = os.path.join(self.temp_dir, "vertex_cutoff.pmx")
        self.exporter.export_pmx_model(out_path, data)

        pmx = _parse_pmx(out_path)

        self.assertEqual(pmx.header.vertex_index_size, 2)
        self.assertEqual(pmx.header.bone_index_size, 1)

    def test_export_reference_index_sizes_use_signed_cutoff(self):
        """参照 index size は signed cutoff を使う"""
        count = 0x80
        data = {
            "model_name": "ReferenceIndexSignedCutoff",
            "vertices": [
                {"position": [0.0, 0.0, 0.0]},
                {"position": [1.0, 0.0, 0.0]},
                {"position": [0.0, 1.0, 0.0]},
            ],
            "faces": [[0, 1, 2]],
            "bones": [{"name": f"Bone{i}"} for i in range(count)],
            "textures": [f"tex_{i}.png" for i in range(count)],
            "materials": [{"name": f"Mat{i}"} for i in range(count)],
            "morphs": [{"type": "vertex", "name": f"Morph{i}"} for i in range(count)],
            "rigid_bodies": [{"name": f"RB{i}"} for i in range(count)],
        }
        out_path = os.path.join(self.temp_dir, "reference_cutoff.pmx")
        self.exporter.export_pmx_model(out_path, data)

        pmx = _parse_pmx(out_path)

        self.assertEqual(pmx.header.bone_index_size, 2)
        self.assertEqual(pmx.header.texture_index_size, 2)
        self.assertEqual(pmx.header.material_index_size, 2)
        self.assertEqual(pmx.header.morph_index_size, 2)
        self.assertEqual(pmx.header.rigid_body_index_size, 2)

    def test_fan_triangulate(self):
        """_fan_triangulate helper"""
        self.assertEqual(_fan_triangulate([0, 1, 2, 3]), [[0, 1, 2], [0, 2, 3]])
        self.assertEqual(
            _fan_triangulate([0, 1, 2, 3, 4]),
            [[0, 1, 2], [0, 2, 3], [0, 3, 4]],
        )
        self.assertEqual(_fan_triangulate([0, 1, 2]), [[0, 1, 2]])

    def test_export_with_edge_magnification(self):
        """edge_magnification が正しく保存される"""
        data = {
            "model_name": "EdgeTest",
            "vertices": [
                {"position": [0.0, 0.0, 0.0], "edge_magnification": 0.5},
                {"position": [1.0, 0.0, 0.0]},
                {"position": [0.0, 1.0, 0.0]},
            ],
            "faces": [[0, 1, 2]],
        }
        out_path = os.path.join(self.temp_dir, "edge.pmx")
        self.exporter.export_pmx_model(out_path, data)

        pmx = _parse_pmx(out_path)

        self.assertAlmostEqual(pmx.vertices[0].edge_magnification, 0.5)
        self.assertAlmostEqual(pmx.vertices[1].edge_magnification, 1.0)  # default

    # --- Phase 2: bone + skin weight tests ---

    def test_export_two_bones_bdef2_roundtrip(self):
        """bones 2本 + BDEF2 vertex の export -> parse_file 検証"""
        data = {
            "model_name": "BoneBDEF2Test",
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
                    "bone_weights": [0.5],
                },
                {
                    "position": [0.0, 1.0, 0.0],
                    "normal": [0.0, 0.0, 1.0],
                    "uv": [0.0, 1.0],
                    "bone_indices": [0, 1],
                },
            ],
            "faces": [[0, 1, 2]],
            "bones": [
                {"name": "root", "position": [0.0, 0.0, 0.0]},
                {"name": "bone1", "position": [5.0, 0.0, 0.0], "parent_index": 0},
            ],
        }
        out_path = os.path.join(self.temp_dir, "bone_bdef2.pmx")
        self.exporter.export_pmx_model(out_path, data)

        pmx = _parse_pmx(out_path)

        self.assertEqual(pmx.header.model_name, "BoneBDEF2Test")
        self.assertEqual(len(pmx.bones), 2)
        self.assertEqual(pmx.header.bone_index_size, 1)  # 2 bones <= 0xFF
        self.assertEqual(len(pmx.display_frames), 2)

        # Verify bones
        self.assertEqual(pmx.bones[0].name, "root")
        self.assertEqual(pmx.bones[0].position, (0.0, 0.0, 0.0))
        self.assertEqual(pmx.bones[0].parent_bone_index, -1)

        self.assertEqual(pmx.bones[1].name, "bone1")
        self.assertEqual(pmx.bones[1].position, (5.0, 0.0, 0.0))
        self.assertEqual(pmx.bones[1].parent_bone_index, 0)

        # Verify BDEF2 vertex
        v0 = pmx.vertices[0]
        self.assertEqual(v0.weight_transform_type, 1)  # BDEF2
        self.assertEqual(v0.bone_indices, [0, 1])
        self.assertEqual(len(v0.bone_weights), 1)
        self.assertAlmostEqual(v0.bone_weights[0], 0.75)

        v1 = pmx.vertices[1]
        self.assertEqual(v1.weight_transform_type, 1)  # BDEF2
        self.assertEqual(v1.bone_indices, [0, 1])
        self.assertAlmostEqual(v1.bone_weights[0], 0.5)

        # Vertex without bone_weights defaults to 0.5
        v2 = pmx.vertices[2]
        self.assertEqual(v2.weight_transform_type, 1)  # BDEF2
        self.assertAlmostEqual(v2.bone_weights[0], 0.5)

    def test_export_bdef4_roundtrip(self):
        """BDEF4 vertex の export -> parse_file 検証"""
        data = {
            "model_name": "BDEF4Test",
            "vertices": [
                {
                    "position": [0.0, 0.0, 0.0],
                    "normal": [0.0, 0.0, 1.0],
                    "bone_indices": [0, 1, 2, 3],
                    "bone_weights": [0.4, 0.3, 0.2, 0.1],
                },
                {
                    "position": [1.0, 0.0, 0.0],
                    "normal": [0.0, 0.0, 1.0],
                    "bone_indices": [0, 1, 2, 3],
                    "bone_weights": [0.5],  # padded with 0
                },
            ],
            "faces": [[0, 1, 0]],  # degenerate face for parsing
            "bones": [
                {"name": "b0"},
                {"name": "b1"},
                {"name": "b2"},
                {"name": "b3"},
            ],
        }
        out_path = os.path.join(self.temp_dir, "bdef4.pmx")
        self.exporter.export_pmx_model(out_path, data)

        pmx = _parse_pmx(out_path)

        self.assertEqual(len(pmx.bones), 4)
        self.assertEqual(pmx.header.bone_index_size, 1)

        # Verify BDEF4 vertex with full weights
        v0 = pmx.vertices[0]
        self.assertEqual(v0.weight_transform_type, 2)  # BDEF4
        self.assertEqual(v0.bone_indices, [0, 1, 2, 3])
        self.assertEqual(len(v0.bone_weights), 4)
        self.assertAlmostEqual(v0.bone_weights[0], 0.4)
        self.assertAlmostEqual(v0.bone_weights[1], 0.3)
        self.assertAlmostEqual(v0.bone_weights[2], 0.2)
        self.assertAlmostEqual(v0.bone_weights[3], 0.1)

        # Verify BDEF4 vertex with padded weights
        v1 = pmx.vertices[1]
        self.assertEqual(v1.weight_transform_type, 2)  # BDEF4
        self.assertAlmostEqual(v1.bone_weights[0], 0.5)
        self.assertAlmostEqual(v1.bone_weights[1], 0.0)
        self.assertAlmostEqual(v1.bone_weights[2], 0.0)
        self.assertAlmostEqual(v1.bone_weights[3], 0.0)

    def test_vertex_unsupported_bone_indices_len_raises(self):
        """bone_indices 長さが 1/2/4 以外で ValueError"""
        for bad_len in (0, 3, 5):
            data = {
                "model_name": "Bad",
                "vertices": [
                    {
                        "position": [0.0, 0.0, 0.0],
                        "bone_indices": list(range(bad_len)),
                    },
                    {"position": [1.0, 0.0, 0.0]},
                    {"position": [0.0, 1.0, 0.0]},
                ],
                "faces": [[0, 1, 2]],
            }
            with self.assertRaises(ValueError):
                self.exporter.export_pmx_model(
                    os.path.join(self.temp_dir, f"bad_len_{bad_len}.pmx"),
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
        with self.assertRaises(ValueError):
            self.exporter.export_pmx_model(
                os.path.join(self.temp_dir, "empty_bones.pmx"),
                data,
            )

    def test_export_bone_index_out_of_range_raises(self):
        """vertex の bone index が bone 数を超える場合は ValueError"""
        data = {
            "model_name": "BadBoneIndex",
            "vertices": [
                {"position": [0.0, 0.0, 0.0], "bone_indices": [2]},
                {"position": [1.0, 0.0, 0.0]},
                {"position": [0.0, 1.0, 0.0]},
            ],
            "faces": [[0, 1, 2]],
            "bones": [{"name": "root"}],
        }
        with self.assertRaises(ValueError):
            self.exporter.export_pmx_model(
                os.path.join(self.temp_dir, "bad_bone_index.pmx"),
                data,
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
        with self.assertRaises(ValueError):
            self.exporter.export_pmx_model(
                os.path.join(self.temp_dir, "bad_face_index.pmx"),
                data,
            )

    def test_export_bones_not_specified_auto_root(self):
        """bones 未指定なら既存通り root 1本を自動作成 (Phase1互換)"""
        data = {
            "model_name": "AutoRoot",
            "vertices": [
                {"position": [0.0, 0.0, 0.0]},
                {"position": [1.0, 0.0, 0.0]},
                {"position": [0.0, 1.0, 0.0]},
            ],
            "faces": [[0, 1, 2]],
        }
        out_path = os.path.join(self.temp_dir, "autoroot.pmx")
        self.exporter.export_pmx_model(out_path, data)

        pmx = _parse_pmx(out_path)

        self.assertEqual(len(pmx.bones), 1)
        self.assertEqual(pmx.bones[0].name, "root")
        self.assertEqual(pmx.header.bone_index_size, 1)

    def test_export_vertex_morph_roundtrip(self):
        """VertexMorph dict の export -> parse_file 検証"""
        data = {
            "model_name": "VertexMorphTest",
            "vertices": [
                {"position": [0.0, 0.0, 0.0]},
                {"position": [1.0, 0.0, 0.0]},
                {"position": [0.0, 1.0, 0.0]},
            ],
            "faces": [[0, 1, 2]],
            "morphs": [
                {
                    "type": "vertex",
                    "name": "smile",
                    "name_english": "smile_en",
                    "panel": 3,
                    "offsets": [
                        {"vertex_index": 1, "position_offset": [0.1, 0.2, 0.3]},
                        {"vertex_index": 2, "position_offset": [-0.1, 0.0, 0.0]},
                    ],
                }
            ],
        }
        out_path = os.path.join(self.temp_dir, "vertex_morph.pmx")
        self.exporter.export_pmx_model(out_path, data)

        pmx = _parse_pmx(out_path)

        self.assertEqual(len(pmx.morphs), 1)
        morph = pmx.morphs[0]
        self.assertEqual(morph.name, "smile")
        self.assertEqual(morph.name_english, "smile_en")
        self.assertEqual(morph.panel, 3)
        self.assertEqual(int(morph.morph_type), 1)
        self.assertEqual(len(morph.offsets), 2)
        self.assertEqual(morph.offsets[0]["vertex_index"], 1)
        self.assertAlmostEqual(morph.offsets[0]["position_offset"][0], 0.1)
        self.assertAlmostEqual(morph.offsets[0]["position_offset"][1], 0.2)
        self.assertAlmostEqual(morph.offsets[0]["position_offset"][2], 0.3)
        self.assertEqual(morph.offsets[1]["vertex_index"], 2)

    def test_export_bone_morph_roundtrip(self):
        """BoneMorph dict の export -> parse_file 検証"""
        data = {
            "model_name": "BoneMorphTest",
            "vertices": [
                {"position": [0.0, 0.0, 0.0]},
                {"position": [1.0, 0.0, 0.0]},
                {"position": [0.0, 1.0, 0.0]},
            ],
            "faces": [[0, 1, 2]],
            "bones": [
                {"name": "root", "position": [0.0, 0.0, 0.0]},
                {"name": "bone1", "position": [5.0, 0.0, 0.0], "parent_index": 0},
            ],
            "morphs": [
                {
                    "type": "bone",
                    "name": "brow_up",
                    "name_english": "brow_up",
                    "panel": 1,
                    "offsets": [
                        {
                            "bone_index": 0,
                            "translation": [0.0, 0.5, 0.0],
                            "rotation": [0.0, 0.0, 0.0, 1.0],
                        },
                        {
                            "bone_index": 1,
                            "translation": [0.0, -0.2, 0.0],
                        },
                    ],
                }
            ],
        }
        out_path = os.path.join(self.temp_dir, "bone_morph.pmx")
        self.exporter.export_pmx_model(out_path, data)

        pmx = _parse_pmx(out_path)

        self.assertEqual(len(pmx.morphs), 1)
        morph = pmx.morphs[0]
        self.assertEqual(morph.name, "brow_up")
        self.assertEqual(morph.name_english, "brow_up")
        self.assertEqual(morph.panel, 1)
        self.assertEqual(int(morph.morph_type), 2)  # BoneMorph
        self.assertEqual(len(morph.offsets), 2)

        # First offset: explicit bone_index, translation, rotation
        off0 = morph.offsets[0]
        self.assertEqual(off0["bone_index"], 0)
        self.assertAlmostEqual(off0["translation"][0], 0.0)
        self.assertAlmostEqual(off0["translation"][1], 0.5)
        self.assertAlmostEqual(off0["translation"][2], 0.0)
        self.assertAlmostEqual(off0["rotation"][3], 1.0)

        # Second offset: defaults for rotation
        off1 = morph.offsets[1]
        self.assertEqual(off1["bone_index"], 1)
        self.assertAlmostEqual(off1["translation"][1], -0.2)
        self.assertAlmostEqual(off1["rotation"][0], 0.0)
        self.assertAlmostEqual(off1["rotation"][1], 0.0)
        self.assertAlmostEqual(off1["rotation"][2], 0.0)
        self.assertAlmostEqual(off1["rotation"][3], 1.0)

    def test_export_bone_morph_index_out_of_range_raises(self):
        """BoneMorph offset の bone_index が範囲外なら ValueError"""
        data = {
            "model_name": "BadBoneMorph",
            "vertices": [
                {"position": [0.0, 0.0, 0.0]},
                {"position": [1.0, 0.0, 0.0]},
                {"position": [0.0, 1.0, 0.0]},
            ],
            "faces": [[0, 1, 2]],
            "bones": [{"name": "root"}],
            "morphs": [
                {
                    "type": "bone",
                    "name": "bad",
                    "offsets": [{"bone_index": 2}],
                }
            ],
        }
        with self.assertRaises(ValueError):
            self.exporter.export_pmx_model(
                os.path.join(self.temp_dir, "bad_bone_morph.pmx"),
                data,
            )

    def test_export_material_morph_roundtrip(self):
        """MaterialMorph dict の export -> parse_file 検証"""
        data = {
            "model_name": "MaterialMorphTest",
            "vertices": [
                {"position": [0.0, 0.0, 0.0]},
                {"position": [1.0, 0.0, 0.0]},
                {"position": [0.0, 1.0, 0.0]},
            ],
            "faces": [[0, 1, 2]],
            "materials": [
                {"name": "MatA"},
                {"name": "MatB"},
            ],
            "morphs": [
                {
                    "type": "material",
                    "name": "hide_mat",
                    "name_english": "hide_mat",
                    "panel": 4,
                    "offsets": [
                        {
                            "material_index": 0,
                            "operation_type": 0,
                            "diffuse": [0.0, 0.0, 0.0, 0.0],
                            "specular": [0.1, 0.2, 0.3],
                            "specular_coefficient": 0.5,
                            "ambient": [0.4, 0.5, 0.6],
                            "edge_color": [1.0, 0.0, 0.0, 1.0],
                            "edge_size": 2.0,
                            "texture_factor": [0.5, 0.5, 0.5, 1.0],
                            "sphere_texture_factor": [0.0, 0.0, 0.0, 0.0],
                            "toon_texture_factor": [0.2, 0.2, 0.2, 1.0],
                        },
                        {
                            "material_index": -1,
                            # all other fields use defaults
                        },
                    ],
                }
            ],
        }
        out_path = os.path.join(self.temp_dir, "material_morph.pmx")
        self.exporter.export_pmx_model(out_path, data)

        pmx = _parse_pmx(out_path)

        self.assertEqual(len(pmx.morphs), 1)
        morph = pmx.morphs[0]
        self.assertEqual(morph.name, "hide_mat")
        self.assertEqual(morph.name_english, "hide_mat")
        self.assertEqual(morph.panel, 4)
        self.assertEqual(int(morph.morph_type), 8)  # MaterialMorph
        self.assertEqual(len(morph.offsets), 2)

        # First offset: explicit values
        off0 = morph.offsets[0]
        self.assertEqual(off0["material_index"], 0)
        self.assertEqual(off0["operation_type"], 0)
        self.assertAlmostEqual(off0["diffuse"][0], 0.0)
        self.assertAlmostEqual(off0["diffuse"][3], 0.0)
        self.assertAlmostEqual(off0["specular"][1], 0.2)
        self.assertAlmostEqual(off0["specular_coefficient"], 0.5)
        self.assertAlmostEqual(off0["ambient"][0], 0.4)
        self.assertAlmostEqual(off0["edge_color"][0], 1.0)
        self.assertAlmostEqual(off0["edge_color"][3], 1.0)
        self.assertAlmostEqual(off0["edge_size"], 2.0)
        self.assertAlmostEqual(off0["texture_factor"][0], 0.5)
        self.assertAlmostEqual(off0["sphere_texture_factor"][2], 0.0)
        self.assertAlmostEqual(off0["toon_texture_factor"][3], 1.0)

        # Second offset: material_index = -1, all defaults
        off1 = morph.offsets[1]
        self.assertEqual(off1["material_index"], -1)
        self.assertEqual(off1["operation_type"], 1)  # default
        self.assertAlmostEqual(off1["diffuse"][0], 0.0)
        self.assertAlmostEqual(off1["specular"][0], 0.0)
        self.assertAlmostEqual(off1["specular_coefficient"], 0.0)
        self.assertAlmostEqual(off1["ambient"][0], 0.0)
        self.assertAlmostEqual(off1["edge_color"][0], 0.0)
        self.assertAlmostEqual(off1["edge_size"], 0.0)
        self.assertAlmostEqual(off1["texture_factor"][1], 0.0)
        self.assertAlmostEqual(off1["sphere_texture_factor"][1], 0.0)
        self.assertAlmostEqual(off1["toon_texture_factor"][1], 0.0)

    def test_export_material_morph_index_out_of_range_raises(self):
        """MaterialMorph offset の material_index が範囲外なら ValueError"""
        data = {
            "model_name": "BadMatMorph",
            "vertices": [
                {"position": [0.0, 0.0, 0.0]},
                {"position": [1.0, 0.0, 0.0]},
                {"position": [0.0, 1.0, 0.0]},
            ],
            "faces": [[0, 1, 2]],
            "materials": [{"name": "MatA"}],
            "morphs": [
                {
                    "type": "material",
                    "name": "bad",
                    "offsets": [{"material_index": 3}],
                }
            ],
        }
        with self.assertRaises(ValueError):
            self.exporter.export_pmx_model(
                os.path.join(self.temp_dir, "bad_mat_morph.pmx"),
                data,
            )

    def test_export_unsupported_morph_types_raise(self):
        """UV / Flip / Impulse モーフは文字列・enum・数値いずれの指定でも ValueError"""
        _base_data = {
            "model_name": "UnsupportedMorphTypes",
            "vertices": [
                {"position": [0.0, 0.0, 0.0]},
                {"position": [1.0, 0.0, 0.0]},
                {"position": [0.0, 1.0, 0.0]},
            ],
            "faces": [[0, 1, 2]],
        }

        unsupported_types = [
            # string aliases
            "uv",
            "flip",
            "impulse",
            # PmxMorphType enum values
            PmxMorphType.UVMorph,
            PmxMorphType.FlipMorph,
            PmxMorphType.ImpulseMorph,
            # numeric equivalents
            int(PmxMorphType.UVMorph),    # 3
            int(PmxMorphType.FlipMorph),   # 9
            int(PmxMorphType.ImpulseMorph),  # 10
        ]

        for morph_type in unsupported_types:
            with self.subTest(morph_type=morph_type):
                data = dict(_base_data)
                data["morphs"] = [{"type": morph_type, "name": "bad", "offsets": []}]
                with self.assertRaises(ValueError):
                    self.exporter.export_pmx_model(
                        os.path.join(self.temp_dir, f"unsupported_{str(morph_type).replace(' ', '_')}.pmx"),
                        data,
                    )

    def test_export_morph_vertex_index_out_of_range_raises(self):
        """VertexMorph offset の vertex index が範囲外なら ValueError"""
        data = {
            "model_name": "BadMorphIndex",
            "vertices": [
                {"position": [0.0, 0.0, 0.0]},
                {"position": [1.0, 0.0, 0.0]},
                {"position": [0.0, 1.0, 0.0]},
            ],
            "faces": [[0, 1, 2]],
            "morphs": [
                {
                    "type": "vertex",
                    "name": "bad",
                    "offsets": [{"vertex_index": 3, "position_offset": [0.0, 0.0, 0.0]}],
                }
            ],
        }
        with self.assertRaises(ValueError):
            self.exporter.export_pmx_model(
                os.path.join(self.temp_dir, "bad_morph_index.pmx"),
                data,
            )

    def test_export_physics_roundtrip(self):
        """RigidBody / Joint dict の export -> parse_file 検証"""
        data = {
            "model_name": "PhysicsExportTest",
            "vertices": [
                {"position": [0.0, 0.0, 0.0]},
                {"position": [1.0, 0.0, 0.0]},
                {"position": [0.0, 1.0, 0.0]},
            ],
            "faces": [[0, 1, 2]],
            "bones": [{"name": "center", "position": [0.0, 0.0, 0.0]}],
            "rigid_bodies": [
                {
                    "name": "rb",
                    "name_english": "rb_en",
                    "related_bone_index": 0,
                    "group": 2,
                    "collision_mask": 0xFFFE,
                    "shape_type": 1,
                    "size": [1.0, 2.0, 3.0],
                    "position": [0.5, 1.5, 2.5],
                    "rotation": [0.1, 0.2, 0.3],
                    "mass": 4.0,
                    "velocity_attenuation": 0.4,
                    "rotation_attenuation": 0.5,
                    "elasticity": 0.6,
                    "friction": 0.7,
                    "physics_mode": 2,
                }
            ],
            "joints": [
                {
                    "name": "joint",
                    "name_english": "joint_en",
                    "joint_type": 0,
                    "rigid_body_a_index": 0,
                    "rigid_body_b_index": -1,
                    "position": [1.0, 2.0, 3.0],
                    "rotation": [0.1, 0.2, 0.3],
                    "translation_limit_min": [-1.0, -2.0, -3.0],
                    "translation_limit_max": [1.0, 2.0, 3.0],
                    "rotation_limit_min": [-0.1, -0.2, -0.3],
                    "rotation_limit_max": [0.1, 0.2, 0.3],
                    "spring_translation": [0.01, 0.02, 0.03],
                    "spring_rotation": [0.04, 0.05, 0.06],
                }
            ],
        }
        out_path = os.path.join(self.temp_dir, "physics.pmx")
        self.exporter.export_pmx_model(out_path, data)

        pmx = _parse_pmx(out_path)

        self.assertEqual(len(pmx.rigid_bodies), 1)
        rigid_body = pmx.rigid_bodies[0]
        self.assertEqual(rigid_body.name, "rb")
        self.assertEqual(rigid_body.name_english, "rb_en")
        self.assertEqual(rigid_body.related_bone_index, 0)
        self.assertEqual(rigid_body.group, 2)
        self.assertEqual(rigid_body.collision_mask, 0xFFFE)
        self.assertEqual(rigid_body.shape_type, 1)
        self.assertEqual(rigid_body.size, (1.0, 2.0, 3.0))
        self.assertEqual(rigid_body.position, (0.5, 1.5, 2.5))
        self.assertAlmostEqual(rigid_body.rotation[0], 0.1)
        self.assertAlmostEqual(rigid_body.mass, 4.0)
        self.assertAlmostEqual(rigid_body.velocity_attenuation, 0.4)
        self.assertAlmostEqual(rigid_body.rotation_attenuation, 0.5)
        self.assertAlmostEqual(rigid_body.elasticity, 0.6)
        self.assertAlmostEqual(rigid_body.friction, 0.7)
        self.assertEqual(rigid_body.physics_mode, 2)

        self.assertEqual(len(pmx.joints), 1)
        joint = pmx.joints[0]
        self.assertEqual(joint.name, "joint")
        self.assertEqual(joint.name_english, "joint_en")
        self.assertEqual(joint.joint_type, 0)
        self.assertEqual(joint.rigid_body_a_index, 0)
        self.assertEqual(joint.rigid_body_b_index, -1)
        self.assertEqual(joint.position, (1.0, 2.0, 3.0))
        self.assertAlmostEqual(joint.rotation[0], 0.1)
        self.assertEqual(joint.translation_limit_min, (-1.0, -2.0, -3.0))
        self.assertEqual(joint.translation_limit_max, (1.0, 2.0, 3.0))
        self.assertAlmostEqual(joint.rotation_limit_min[0], -0.1)
        self.assertAlmostEqual(joint.rotation_limit_max[2], 0.3)
        self.assertAlmostEqual(joint.spring_translation[1], 0.02)
        self.assertAlmostEqual(joint.spring_rotation[2], 0.06)

    def test_export_rigid_body_related_bone_index_out_of_range_raises(self):
        """剛体の related_bone_index が範囲外なら ValueError"""
        data = {
            "model_name": "BadRigidBodyBone",
            "vertices": [
                {"position": [0.0, 0.0, 0.0]},
                {"position": [1.0, 0.0, 0.0]},
                {"position": [0.0, 1.0, 0.0]},
            ],
            "faces": [[0, 1, 2]],
            "bones": [{"name": "center"}],
            "rigid_bodies": [{"name": "bad", "related_bone_index": 1}],
        }
        with self.assertRaises(ValueError):
            self.exporter.export_pmx_model(
                os.path.join(self.temp_dir, "bad_rigid_body_bone.pmx"),
                data,
            )

    def test_export_joint_rigid_body_index_out_of_range_raises(self):
        """Joint の剛体 index が範囲外なら ValueError"""
        data = {
            "model_name": "BadJointRigidBody",
            "vertices": [
                {"position": [0.0, 0.0, 0.0]},
                {"position": [1.0, 0.0, 0.0]},
                {"position": [0.0, 1.0, 0.0]},
            ],
            "faces": [[0, 1, 2]],
            "rigid_bodies": [{"name": "rb"}],
            "joints": [{"name": "bad_joint", "rigid_body_a_index": 1}],
        }
        with self.assertRaises(ValueError):
            self.exporter.export_pmx_model(
                os.path.join(self.temp_dir, "bad_joint_rb.pmx"),
                data,
            )

    def test_export_joint_without_rigid_body_raises(self):
        """Joint だけ指定された場合は ValueError"""
        data = {
            "model_name": "JointWithoutRigidBody",
            "vertices": [
                {"position": [0.0, 0.0, 0.0]},
                {"position": [1.0, 0.0, 0.0]},
                {"position": [0.0, 1.0, 0.0]},
            ],
            "faces": [[0, 1, 2]],
            "joints": [{"name": "bad_joint"}],
        }
        with self.assertRaises(ValueError):
            self.exporter.export_pmx_model(
                os.path.join(self.temp_dir, "joint_without_rb.pmx"),
                data,
            )
