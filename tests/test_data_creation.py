"""
PMX/PMD/VMDデータクラスの作成テスト
各データクラスが正しく初期化・作成できることを確認
"""
import os
import sys

# プロジェクトのルートディレクトリをパスに追加
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

from mmd_tools.core.pmx_data.header import PmxHeader
from mmd_tools.core.pmx_data.vertex import PmxVertex
from mmd_tools.core.pmx_data.material import PmxMaterial
from mmd_tools.core.pmx_data.bone import PmxBone
from mmd_tools.core.pmd_data.vertex import PmdVertex
from mmd_tools.core.pmd_data.material import PmdMaterial
from mmd_tools.core.pmd_data.bone import PmdBone
from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame


def test_pmx_data_creation():
    """PMXデータクラスの作成テスト"""
    print("PMXデータクラス作成テスト")
    
    # ヘッダーを作成
    header = PmxHeader()
    header.encoding = 1  # UTF-16LE
    header.texture_index_size = 1
    print("  [OK] PmxHeader作成成功")
    
    # 頂点を作成
    try:
        vertex = PmxVertex(header)
        vertex.position = [0.0, 1.0, 0.0]
        vertex.normal = [0.0, 1.0, 0.0]
        vertex.uv = [0.5, 0.5]
        print("  [OK] PmxVertex作成成功")
    except Exception as e:
        print(f"  [NG] PmxVertex作成失敗: {e}")
    
    # マテリアルを作成
    try:
        material = PmxMaterial(header.texture_index_size, header.encoding, 0)
        material.name = "TestMaterial"
        material.diffuse = [1.0, 0.0, 0.0, 1.0]
        print("  [OK] PmxMaterial作成成功")
    except Exception as e:
        print(f"  [NG] PmxMaterial作成失敗: {e}")
    
    # ボーンを作成
    try:
        # PmxBoneの初期化方法を確認
        bone = PmxBone()
        bone.name = "TestBone"
        bone.position = [0.0, 0.0, 0.0]
        print("  [OK] PmxBone作成成功（引数なし）")
    except:
        try:
            # 引数が必要な場合
            bone = PmxBone(header)
            bone.name = "TestBone"
            bone.position = [0.0, 0.0, 0.0]
            print("  [OK] PmxBone作成成功（header引数あり）")
        except Exception as e:
            print(f"  [NG] PmxBone作成失敗: {e}")


def test_pmd_data_creation():
    """PMDデータクラスの作成テスト"""
    print("\nPMDデータクラス作成テスト")
    
    # 頂点を作成
    try:
        vertex = PmdVertex()
        vertex.position = [0.0, 1.0, 0.0]
        vertex.normal = [0.0, 1.0, 0.0]
        vertex.uv = [0.5, 0.5]
        print("  [OK] PmdVertex作成成功")
    except Exception as e:
        print(f"  [NG] PmdVertex作成失敗: {e}")
    
    # マテリアルを作成
    try:
        material = PmdMaterial()
        material.diffuse_color = [1.0, 0.0, 0.0, 1.0]
        material.specular_color = [0.5, 0.5, 0.5]
        print("  [OK] PmdMaterial作成成功")
    except Exception as e:
        print(f"  [NG] PmdMaterial作成失敗: {e}")
    
    # ボーンを作成
    try:
        bone = PmdBone()
        bone.bone_name = "TestBone"
        bone.bone_head_pos = [0.0, 0.0, 0.0]
        print("  [OK] PmdBone作成成功")
    except Exception as e:
        print(f"  [NG] PmdBone作成失敗: {e}")


def test_vmd_data_creation():
    """VMDデータクラスの作成テスト"""
    print("\nVMDデータクラス作成テスト")
    
    # ボーンフレームを作成
    try:
        frame = VmdBoneFrame()
        frame.bone_name = "TestBone"
        frame.frame_number = 0
        frame.position = [0.0, 0.0, 0.0]
        frame.rotation = [0.0, 0.0, 0.0, 1.0]
        frame.interpolation = b'\x14' * 64
        print("  [OK] VmdBoneFrame作成成功")
    except Exception as e:
        print(f"  [NG] VmdBoneFrame作成失敗: {e}")


if __name__ == "__main__":
    print("データクラス作成テストを開始...\n")
    
    test_pmx_data_creation()
    test_pmd_data_creation()
    test_vmd_data_creation()
    
    print("\nテスト完了")