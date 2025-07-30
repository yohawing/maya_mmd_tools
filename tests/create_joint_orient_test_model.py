"""
joint orient検証用のシンプルなPMXモデルとVMDファイルを作成するスクリプト
親子関係のあるボーンチェーンを持つモデルを作成し、回転アニメーションを適用
"""
import os
import sys
import math

# プロジェクトのルートディレクトリをパスに追加
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

from mmd_tools.core.pmx_parser import PmxParser
from mmd_tools.core.pmx_data.header import PmxHeader
from mmd_tools.core.pmx_data.vertex import PmxVertex
from mmd_tools.core.pmx_data.face import PmxFace
from mmd_tools.core.pmx_data.material import PmxMaterial
from mmd_tools.core.pmx_data.bone import PmxBone, PmxBoneFlag

from mmd_tools.core.vmd_parser import VmdParser
from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame


def create_joint_orient_test_pmx():
    """
    joint orient検証用のPMXモデルを作成
    3つのボーンからなるチェーンと、それに沿った簡単なメッシュを作成
    """
    print("joint orient検証用PMXモデルを作成中...")
    
    parser = PmxParser()
    
    # ヘッダー設定
    parser.header = PmxHeader()
    parser.header.magic = b"PMX "
    parser.header.version = 2.0
    parser.header.encoding = 1  # UTF-16LE
    parser.header.additional_vec4_count = 0
    parser.header.vertex_index_size = 1
    parser.header.texture_index_size = 1
    parser.header.material_index_size = 1
    parser.header.bone_index_size = 1
    parser.header.morph_index_size = 1
    parser.header.rigid_body_index_size = 1
    
    parser.header.model_name = "JointOrientTest"
    parser.header.model_name_english = "Joint Orient Test Model"
    parser.header.comment = "joint orient検証用のテストモデル"
    parser.header.comment_english = "Test model for joint orient verification"
    
    # 頂点を作成（各ボーンの位置に立方体）
    # Root (0,0,0)
    add_cube_vertices(parser, [0, 0, 0], 0.5, 0)  # Root用の立方体
    # Bone1 (2,0,0)
    add_cube_vertices(parser, [2, 0, 0], 0.5, 1)  # Bone1用の立方体
    # Bone2 (4,0,0)
    add_cube_vertices(parser, [4, 0, 0], 0.5, 2)  # Bone2用の立方体
    
    # 面を作成（各立方体に12個の三角形）
    for cube_idx in range(3):
        add_cube_faces(parser, cube_idx * 8)
    
    # マテリアルを作成
    for i, color in enumerate([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]):
        material = PmxMaterial()
        material.name = f"Material{i}"
        material.name_english = f"Material{i}"
        material.diffuse = color + [1.0]  # RGB + Alpha
        material.specular = [0.5, 0.5, 0.5]
        material.specular_coefficient = 10.0
        material.ambient = [0.3, 0.3, 0.3]
        material.draw_flag = 0x01 | 0x02 | 0x04 | 0x08 | 0x10  # 両面描画など
        material.edge_color = [0.0, 0.0, 0.0, 1.0]
        material.edge_size = 1.0
        material.texture_index = -1
        material.sphere_texture_index = -1
        material.sphere_mode = 0
        material.shared_toon_flag = 1
        material.toon_texture_index = 0
        material.memo = ""
        material.face_count = 36  # 1立方体につき12面×3頂点
        
        parser.materials.append(material)
    
    # ボーンを作成
    # Root
    root_bone = PmxBone()
    root_bone.name = "Root"
    root_bone.name_english = "Root"
    root_bone.position = [0.0, 0.0, 0.0]
    root_bone.parent_bone_index = -1
    root_bone.transform_layer = 0
    root_bone.bone_flag = PmxBoneFlag.ROTATABLE | PmxBoneFlag.MOVABLE | PmxBoneFlag.DISPLAY
    root_bone.tail_position = [2.0, 0.0, 0.0]  # 次のボーンへの方向
    
    # ボーン固有の属性設定
    root_bone.bone_index_size = parser.header.bone_index_size
    root_bone.encoding = parser.header.encoding
    
    parser.bones.append(root_bone)
    
    # Bone1 (親: Root)
    bone1 = PmxBone()
    bone1.name = "Bone1"
    bone1.name_english = "Bone1"
    bone1.position = [2.0, 0.0, 0.0]
    bone1.parent_bone_index = 0  # Root
    bone1.transform_layer = 0
    bone1.bone_flag = PmxBoneFlag.ROTATABLE | PmxBoneFlag.DISPLAY
    bone1.tail_position = [2.0, 0.0, 0.0]  # 相対位置
    
    bone1.bone_index_size = parser.header.bone_index_size
    bone1.encoding = parser.header.encoding
    
    parser.bones.append(bone1)
    
    # Bone2 (親: Bone1)
    bone2 = PmxBone()
    bone2.name = "Bone2"
    bone2.name_english = "Bone2"
    bone2.position = [4.0, 0.0, 0.0]
    bone2.parent_bone_index = 1  # Bone1
    bone2.transform_layer = 0
    bone2.bone_flag = PmxBoneFlag.ROTATABLE | PmxBoneFlag.DISPLAY
    bone2.tail_position = [1.0, 0.0, 0.0]  # 相対位置
    
    bone2.bone_index_size = parser.header.bone_index_size
    bone2.encoding = parser.header.encoding
    
    parser.bones.append(bone2)
    
    # 出力ファイルパス
    output_path = os.path.join(project_root, "tests", "data", "joint_orient_test.pmx")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    parser.write_file(output_path)
    print(f"PMXモデルを作成しました: {output_path}")
    
    return output_path


def add_cube_vertices(parser, center, size, bone_index):
    """立方体の頂点を追加"""
    half_size = size / 2
    
    # 8つの頂点
    vertices = [
        [-half_size, -half_size, -half_size],
        [half_size, -half_size, -half_size],
        [half_size, half_size, -half_size],
        [-half_size, half_size, -half_size],
        [-half_size, -half_size, half_size],
        [half_size, -half_size, half_size],
        [half_size, half_size, half_size],
        [-half_size, half_size, half_size],
    ]
    
    for v in vertices:
        vertex = PmxVertex()
        vertex.position = [center[0] + v[0], center[1] + v[1], center[2] + v[2]]
        vertex.normal = [0.0, 1.0, 0.0]  # 仮の法線
        vertex.uv = [0.0, 0.0]
        vertex.weight_transform_type = 0  # BDEF1
        vertex.bone_indices = [bone_index]
        vertex.bone_weights = [1.0]
        vertex.edge_magnification = 1.0
        
        parser.vertices.append(vertex)


def add_cube_faces(parser, base_vertex_index):
    """立方体の面を追加"""
    # 立方体の面インデックス
    face_indices = [
        # 前面
        [0, 1, 2], [0, 2, 3],
        # 背面
        [4, 6, 5], [4, 7, 6],
        # 上面
        [3, 2, 6], [3, 6, 7],
        # 下面
        [0, 5, 1], [0, 4, 5],
        # 左面
        [0, 3, 7], [0, 7, 4],
        # 右面
        [1, 5, 6], [1, 6, 2],
    ]
    
    for indices in face_indices:
        face = PmxFace(parser.header.vertex_index_size)
        face.indices = [base_vertex_index + i for i in indices]
        parser.faces.append(face)


def create_joint_orient_test_vmd(pmx_path):
    """
    joint orient検証用のVMDモーションを作成
    各ボーンに異なる軸周りの回転を適用
    """
    print("\njoint orient検証用VMDモーションを作成中...")
    
    parser = VmdParser()
    
    # ヘッダー設定
    parser.header.magic = b"Vocaloid Motion Data\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    parser.header.model_name = "JointOrientTest"
    
    # 30フレームのアニメーションを作成
    total_frames = 30
    
    # Root: Y軸周りに回転
    for frame in range(0, total_frames + 1, 10):
        bone_frame = VmdBoneFrame()
        bone_frame.bone_name = "Root"
        bone_frame.frame_number = frame
        bone_frame.position = [0.0, 0.0, 0.0]
        
        # Y軸周りの回転（度からラジアンに変換）
        angle = (frame / total_frames) * 90.0  # 0度から90度
        rad = math.radians(angle)
        # クォータニオン (Y軸回転: [0, sin(θ/2), 0, cos(θ/2)])
        bone_frame.rotation = [0.0, math.sin(rad/2), 0.0, math.cos(rad/2)]
        
        # デフォルト補間データ
        bone_frame.interpolation = b'\x14\x14\x14\x14' * 16
        
        parser.bone_frames.append(bone_frame)
    
    # Bone1: Z軸周りに回転
    for frame in range(0, total_frames + 1, 10):
        bone_frame = VmdBoneFrame()
        bone_frame.bone_name = "Bone1"
        bone_frame.frame_number = frame
        bone_frame.position = [0.0, 0.0, 0.0]
        
        # Z軸周りの回転
        angle = (frame / total_frames) * -60.0  # 0度から-60度
        rad = math.radians(angle)
        # クォータニオン (Z軸回転: [0, 0, sin(θ/2), cos(θ/2)])
        bone_frame.rotation = [0.0, 0.0, math.sin(rad/2), math.cos(rad/2)]
        
        bone_frame.interpolation = b'\x14\x14\x14\x14' * 16
        
        parser.bone_frames.append(bone_frame)
    
    # Bone2: X軸周りに回転
    for frame in range(0, total_frames + 1, 10):
        bone_frame = VmdBoneFrame()
        bone_frame.bone_name = "Bone2"
        bone_frame.frame_number = frame
        bone_frame.position = [0.0, 0.0, 0.0]
        
        # X軸周りの回転
        angle = (frame / total_frames) * 45.0  # 0度から45度
        rad = math.radians(angle)
        # クォータニオン (X軸回転: [sin(θ/2), 0, 0, cos(θ/2)])
        bone_frame.rotation = [math.sin(rad/2), 0.0, 0.0, math.cos(rad/2)]
        
        bone_frame.interpolation = b'\x14\x14\x14\x14' * 16
        
        parser.bone_frames.append(bone_frame)
    
    # 出力ファイルパス
    output_path = os.path.join(project_root, "tests", "data", "joint_orient_test.vmd")
    
    parser.write_file(output_path)
    print(f"VMDモーションを作成しました: {output_path}")
    
    return output_path


if __name__ == "__main__":
    print("joint orient検証用テストファイルの作成を開始します...\n")
    
    # PMXモデルを作成
    pmx_path = create_joint_orient_test_pmx()
    
    # VMDモーションを作成
    vmd_path = create_joint_orient_test_vmd(pmx_path)
    
    print("\n作成完了!")
    print("作成されたファイル:")
    print(f"  - PMXモデル: {pmx_path}")
    print(f"  - VMDモーション: {vmd_path}")
    print("\nこれらのファイルをMMDやMayaにインポートして、")
    print("joint orientの動作を確認してください。")