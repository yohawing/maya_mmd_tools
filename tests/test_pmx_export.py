"""
PMXエクスポート機能のテストスクリプト
読み込み → 書き込み → 再読み込みのラウンドトリップテストを実行
"""
import os
import sys
import tempfile

# プロジェクトのルートディレクトリをパスに追加
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

from mmd_tools.core.pmx_parser import PmxParser
from mmd_tools.core.pmx_data.vertex import PmxVertex
from mmd_tools.core.pmx_data.face import PmxFace
from mmd_tools.core.pmx_data.material import PmxMaterial
from mmd_tools.core.pmx_data.bone import PmxBone


def test_pmx_round_trip():
    """PMXファイルの読み込み→書き込み→再読み込みテスト"""
    
    # テスト用PMXファイルのパス
    test_pmx_path = os.path.join(project_root, "tests", "data", "for_unit_test", "pmx_basic_model.pmx")
    
    if not os.path.exists(test_pmx_path):
        print(f"テストファイルが見つかりません: {test_pmx_path}")
        # 別の場所を探す
        test_pmx_path = os.path.join(project_root, "tests", "data", "basic_cube_model.pmx")
        if not os.path.exists(test_pmx_path):
            print(f"代替テストファイルも見つかりません: {test_pmx_path}")
            return False
    
    print(f"テストファイルを読み込み中: {test_pmx_path}")
    
    # 1. オリジナルのPMXファイルを読み込む
    parser1 = PmxParser()
    parser1.parse_file(test_pmx_path)
    
    print(f"読み込み完了:")
    print(f"  - モデル名: {parser1.header.model_name}")
    print(f"  - バージョン: {parser1.header.version}")
    print(f"  - 頂点数: {len(parser1.vertices)}")
    print(f"  - 面数: {len(parser1.faces)}")
    print(f"  - マテリアル数: {len(parser1.materials)}")
    print(f"  - ボーン数: {len(parser1.bones)}")
    
    # 2. 一時ファイルに書き込む
    with tempfile.NamedTemporaryFile(suffix=".pmx", delete=False) as tmp_file:
        tmp_path = tmp_file.name
    
    print(f"\n一時ファイルに書き込み中: {tmp_path}")
    parser1.write_file(tmp_path)
    print("書き込み完了")
    
    # 3. 書き込んだファイルを再度読み込む
    print("\n書き込んだファイルを再読み込み中...")
    parser2 = PmxParser()
    parser2.parse_file(tmp_path)
    
    print(f"再読み込み完了:")
    print(f"  - モデル名: {parser2.header.model_name}")
    print(f"  - バージョン: {parser2.header.version}")
    print(f"  - 頂点数: {len(parser2.vertices)}")
    print(f"  - 面数: {len(parser2.faces)}")
    print(f"  - マテリアル数: {len(parser2.materials)}")
    print(f"  - ボーン数: {len(parser2.bones)}")
    
    # 4. データの一致を確認
    print("\nデータの一致を確認中...")
    
    # ヘッダー情報の比較
    assert parser1.header.model_name == parser2.header.model_name, "モデル名が一致しません"
    assert parser1.header.comment == parser2.header.comment, "コメントが一致しません"
    assert abs(parser1.header.version - parser2.header.version) < 0.001, "バージョンが一致しません"
    
    # 頂点数の比較
    assert len(parser1.vertices) == len(parser2.vertices), "頂点数が一致しません"
    
    # 最初の頂点データの比較（サンプル）
    if parser1.vertices:
        v1 = parser1.vertices[0]
        v2 = parser2.vertices[0]
        assert v1.position == v2.position, "頂点位置が一致しません"
        assert v1.normal == v2.normal, "頂点法線が一致しません"
        assert v1.uv == v2.uv, "頂点UVが一致しません"
    
    # 面数の比較
    assert len(parser1.faces) == len(parser2.faces), "面数が一致しません"
    
    # マテリアル数の比較
    assert len(parser1.materials) == len(parser2.materials), "マテリアル数が一致しません"
    
    # ボーン数の比較
    assert len(parser1.bones) == len(parser2.bones), "ボーン数が一致しません"
    
    # 後片付け
    os.unlink(tmp_path)
    
    print("\n[SUCCESS] ラウンドトリップテスト成功！")
    return True


def test_create_simple_pmx():
    """簡単なPMXファイルを作成してエクスポートするテスト"""
    
    print("\n簡単なPMXファイルを作成中...")
    
    # 新しいPMXパーサーインスタンスを作成
    parser = PmxParser()
    
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
    v1 = PmxVertex(parser.header)
    v1.position = [0.0, 0.0, 0.0]
    v1.normal = [0.0, 1.0, 0.0]
    v1.uv = [0.0, 0.0]
    v1.weight_type = 0  # BDEF1
    v1.bone_indices = [0]
    v1.bone_weights = []
    v1.edge_scale = 1.0
    parser.vertices.append(v1)
    
    # 頂点2
    v2 = PmxVertex(parser.header)
    v2.position = [1.0, 0.0, 0.0]
    v2.normal = [0.0, 1.0, 0.0]
    v2.uv = [1.0, 0.0]
    v2.weight_type = 0  # BDEF1
    v2.bone_indices = [0]
    v2.bone_weights = []
    v2.edge_scale = 1.0
    parser.vertices.append(v2)
    
    # 頂点3
    v3 = PmxVertex(parser.header)
    v3.position = [0.0, 0.0, 1.0]
    v3.normal = [0.0, 1.0, 0.0]
    v3.uv = [0.0, 1.0]
    v3.weight_type = 0  # BDEF1
    v3.bone_indices = [0]
    v3.bone_weights = []
    v3.edge_scale = 1.0
    parser.vertices.append(v3)
    
    # 面（三角形）を追加
    face = PmxFace(parser.header.vertex_index_size)
    face.indices = [0, 1, 2]
    parser.faces.append(face)
    
    # マテリアルを追加
    material = PmxMaterial(parser.header.texture_index_size, parser.header.encoding, material_index=0)
    material.name = "Material1"
    material.name_english = "Material1"
    material.diffuse = [0.8, 0.8, 0.8, 1.0]
    material.specular = [0.5, 0.5, 0.5]
    material.specular_power = 5.0
    material.ambient = [0.3, 0.3, 0.3]
    material.draw_flags = 0x01  # 両面描画
    material.edge_color = [0.0, 0.0, 0.0, 1.0]
    material.edge_size = 1.0
    material.texture_index = -1
    material.sphere_mode = 0
    material.sphere_texture_index = -1
    material.shared_toon_flag = 0
    material.toon_texture_index = 0
    material.comment = ""
    material.face_count = 1  # 1面（3頂点）
    parser.materials.append(material)
    
    # 最低限のボーンを追加（ルートボーン）
    bone = PmxBone(parser.header.bone_index_size, parser.header.encoding)
    bone.name = "root"
    bone.name_english = "root"
    bone.position = [0.0, 0.0, 0.0]
    bone.parent_bone_index = -1
    bone.transform_layer = 0
    bone.bone_flag = 0x0001  # 接続先表示
    bone.connect_position_offset = [0.0, 1.0, 0.0]  # 接続先表示の場合は位置を設定
    parser.bones.append(bone)
    
    # 表示枠を追加（必須：Root, 表情）
    from mmd_tools.core.pmx_data.display_frame import PmxDisplayFrame
    
    # Root表示枠
    root_frame = PmxDisplayFrame(parser.header.bone_index_size, parser.header.morph_index_size, parser.header.encoding)
    root_frame.name = "Root"
    root_frame.name_english = "Root"
    root_frame.special_flag = 1  # 特殊枠
    parser.display_frames.append(root_frame)
    
    # 表情枠
    exp_frame = PmxDisplayFrame(parser.header.bone_index_size, parser.header.morph_index_size, parser.header.encoding)
    exp_frame.name = "表情"
    exp_frame.name_english = "Exp"
    exp_frame.special_flag = 1  # 特殊枠
    parser.display_frames.append(exp_frame)
    
    # 一時ファイルに書き込む
    with tempfile.NamedTemporaryFile(suffix=".pmx", delete=False) as tmp_file:
        tmp_path = tmp_file.name
    
    print(f"作成したモデルをファイルに書き込み中: {tmp_path}")
    parser.write_file(tmp_path)
    print("書き込み完了")
    
    # 書き込んだファイルを読み込んで確認
    print("\n書き込んだファイルを読み込み中...")
    parser2 = PmxParser()
    parser2.parse_file(tmp_path)
    
    print(f"読み込み完了:")
    print(f"  - モデル名: {parser2.header.model_name}")
    print(f"  - バージョン: {parser2.header.version}")
    print(f"  - 頂点数: {len(parser2.vertices)}")
    print(f"  - 面数: {len(parser2.faces)}")
    print(f"  - マテリアル数: {len(parser2.materials)}")
    print(f"  - ボーン数: {len(parser2.bones)}")
    
    # 後片付け
    os.unlink(tmp_path)
    
    print("\n[SUCCESS] 簡単なPMXファイル作成テスト成功！")
    return True


if __name__ == "__main__":
    print("PMXエクスポート機能のテストを開始します...\n")
    
    # ラウンドトリップテスト
    try:
        test_pmx_round_trip()
    except Exception as e:
        print(f"\n[FAILED] ラウンドトリップテスト失敗: {e}")
        import traceback
        traceback.print_exc()
    
    # 簡単なPMX作成テスト
    try:
        test_create_simple_pmx()
    except Exception as e:
        print(f"\n[FAILED] PMX作成テスト失敗: {e}")
        import traceback
        traceback.print_exc()
    
    print("\nテスト完了")