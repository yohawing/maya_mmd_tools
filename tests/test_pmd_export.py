"""
PMDエクスポート機能のテストスクリプト
読み込み → 書き込み → 再読み込みのラウンドトリップテストを実行
"""
import os
import sys
import tempfile

# プロジェクトのルートディレクトリをパスに追加
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

from mmd_tools.core.pmd_parser import PmdParser


def test_pmd_round_trip():
    """PMDファイルの読み込み→書き込み→再読み込みテスト"""
    
    # テスト用PMDファイルのパス
    test_pmd_path = os.path.join(project_root, "tests", "data", "basic_cube_model.pmd")
    
    if not os.path.exists(test_pmd_path):
        print(f"テストファイルが見つかりません: {test_pmd_path}")
        return False
    
    print(f"テストファイルを読み込み中: {test_pmd_path}")
    
    # 1. オリジナルのPMDファイルを読み込む
    parser1 = PmdParser()
    parser1.parse_file(test_pmd_path)
    
    print(f"読み込み完了:")
    print(f"  - モデル名: {parser1.header.model_name}")
    print(f"  - 頂点数: {len(parser1.vertices)}")
    print(f"  - 面数: {len(parser1.faces)}")
    print(f"  - マテリアル数: {len(parser1.materials)}")
    print(f"  - ボーン数: {len(parser1.bones)}")
    
    # 2. 一時ファイルに書き込む
    with tempfile.NamedTemporaryFile(suffix=".pmd", delete=False) as tmp_file:
        tmp_path = tmp_file.name
    
    print(f"\n一時ファイルに書き込み中: {tmp_path}")
    parser1.write_file(tmp_path)
    print("書き込み完了")
    
    # 3. 書き込んだファイルを再度読み込む
    print("\n書き込んだファイルを再読み込み中...")
    parser2 = PmdParser()
    parser2.parse_file(tmp_path)
    
    print(f"再読み込み完了:")
    print(f"  - モデル名: {parser2.header.model_name}")
    print(f"  - 頂点数: {len(parser2.vertices)}")
    print(f"  - 面数: {len(parser2.faces)}")
    print(f"  - マテリアル数: {len(parser2.materials)}")
    print(f"  - ボーン数: {len(parser2.bones)}")
    
    # 4. データの一致を確認
    print("\nデータの一致を確認中...")
    
    # ヘッダー情報の比較
    assert parser1.header.model_name == parser2.header.model_name, "モデル名が一致しません"
    assert parser1.header.comment == parser2.header.comment, "コメントが一致しません"
    
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


def test_create_simple_pmd():
    """簡単なPMDファイルを作成してエクスポートするテスト"""
    
    print("\n簡単なPMDファイルを作成中...")
    
    # 新しいPMDパーサーインスタンスを作成
    parser = PmdParser()
    
    # ヘッダー情報を設定
    parser.header.magic = b"Pmd"
    parser.header.version = 1.0
    parser.header.model_name = "TestModel"
    parser.header.comment = "This is a test model created by export test"
    
    # 簡単な三角形の頂点を追加
    from mmd_tools.core.pmd_data.vertex import PmdVertex
    
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
    from mmd_tools.core.pmd_data.face import PmdFace
    
    face = PmdFace()
    face.indices = (0, 1, 2)
    parser.faces.append(face)
    
    # マテリアルを追加
    from mmd_tools.core.pmd_data.material import PmdMaterial
    
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
    from mmd_tools.core.pmd_data.bone import PmdBone, PmdBoneType
    
    bone = PmdBone()
    bone.name = "root"
    bone.parent_bone_index = -1
    bone.tail_pos_bone_index = 0xFFFF
    bone.bone_type = PmdBoneType.ROTATE_AND_MOVE
    bone.ik_parent_bone_index = 0
    bone.position = (0.0, 0.0, 0.0)
    parser.bones.append(bone)
    
    # 一時ファイルに書き込む
    with tempfile.NamedTemporaryFile(suffix=".pmd", delete=False) as tmp_file:
        tmp_path = tmp_file.name
    
    print(f"作成したモデルをファイルに書き込み中: {tmp_path}")
    parser.write_file(tmp_path)
    print("書き込み完了")
    
    # 書き込んだファイルを読み込んで確認
    print("\n書き込んだファイルを読み込み中...")
    parser2 = PmdParser()
    parser2.parse_file(tmp_path)
    
    print(f"読み込み完了:")
    print(f"  - モデル名: {parser2.header.model_name}")
    print(f"  - 頂点数: {len(parser2.vertices)}")
    print(f"  - 面数: {len(parser2.faces)}")
    print(f"  - マテリアル数: {len(parser2.materials)}")
    print(f"  - ボーン数: {len(parser2.bones)}")
    
    # 後片付け
    os.unlink(tmp_path)
    
    print("\n[SUCCESS] 簡単なPMDファイル作成テスト成功！")
    return True


if __name__ == "__main__":
    print("PMDエクスポート機能のテストを開始します...\n")
    
    # ラウンドトリップテスト
    try:
        test_pmd_round_trip()
    except Exception as e:
        print(f"\n[FAILED] ラウンドトリップテスト失敗: {e}")
    
    # 簡単なPMD作成テスト
    try:
        test_create_simple_pmd()
    except Exception as e:
        print(f"\n[FAILED] PMD作成テスト失敗: {e}")
    
    print("\nテスト完了")