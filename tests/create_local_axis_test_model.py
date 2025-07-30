"""ローカル軸設定テスト用のPMXモデルを作成するスクリプト"""

import sys
import os
import struct
import math
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def write_pmx_header(f):
    """PMXヘッダーを書き込む"""
    f.write(b'PMX ')  # シグネチャ
    f.write(struct.pack('<f', 2.0))  # バージョン
    
    # 追加情報
    f.write(struct.pack('B', 8))  # 追加情報数
    f.write(struct.pack('B', 0))  # エンコーディング: UTF-16LE
    f.write(struct.pack('B', 0))  # 追加UV数
    f.write(struct.pack('B', 1))  # 頂点インデックスサイズ
    f.write(struct.pack('B', 1))  # テクスチャインデックスサイズ
    f.write(struct.pack('B', 1))  # 材質インデックスサイズ
    f.write(struct.pack('B', 1))  # ボーンインデックスサイズ
    f.write(struct.pack('B', 1))  # モーフインデックスサイズ
    f.write(struct.pack('B', 1))  # 剛体インデックスサイズ


def write_text(f, text):
    """UTF-16LEテキストを書き込む"""
    encoded = text.encode('utf-16-le')
    f.write(struct.pack('<I', len(encoded)))
    f.write(encoded)


def write_vertex(f, pos, normal, uv, bone_indices, bone_weights):
    """頂点データを書き込む"""
    # 位置
    f.write(struct.pack('<fff', *pos))
    # 法線
    f.write(struct.pack('<fff', *normal))
    # UV
    f.write(struct.pack('<ff', *uv))
    # ウェイト変形（BDEF1）
    f.write(struct.pack('B', 0))  # BDEF1
    f.write(struct.pack('B', bone_indices[0]))
    # エッジ倍率
    f.write(struct.pack('<f', 1.0))


def write_bone(f, name_jp, name_en, pos, parent_idx, layer, flags, tail_pos=None, 
               fixed_axis=None, local_x=None, local_z=None, key_value=None, ik_data=None):
    """ボーンデータを書き込む"""
    write_text(f, name_jp)
    write_text(f, name_en)
    f.write(struct.pack('<fff', *pos))
    f.write(struct.pack('<b', parent_idx))
    f.write(struct.pack('<I', layer))
    f.write(struct.pack('<H', flags))
    
    if flags & 0x0001:  # 接続先表示
        f.write(struct.pack('<fff', *tail_pos))
    else:
        f.write(struct.pack('<b', -1))  # 接続先ボーンインデックス
    
    # 回転可能
    if flags & 0x0002:
        pass
    
    # 移動可能
    if flags & 0x0004:
        pass
    
    # 表示
    if flags & 0x0008:
        pass
    
    # 操作可
    if flags & 0x0010:
        pass
    
    # IK
    if flags & 0x0020 and ik_data:
        f.write(struct.pack('<b', ik_data['target']))
        f.write(struct.pack('<I', ik_data['loop']))
        f.write(struct.pack('<f', ik_data['limit']))
        f.write(struct.pack('<I', len(ik_data['links'])))
        
        for link in ik_data['links']:
            f.write(struct.pack('<b', link['bone_index']))
            f.write(struct.pack('B', 1 if link['has_limits'] else 0))
            if link['has_limits']:
                f.write(struct.pack('<fff', *link['limit_min']))
                f.write(struct.pack('<fff', *link['limit_max']))
    
    # 回転付与
    if flags & 0x0100:
        f.write(struct.pack('<b', -1))  # 付与親ボーン
        f.write(struct.pack('<f', 1.0))  # 付与率
    
    # 移動付与
    if flags & 0x0200:
        f.write(struct.pack('<b', -1))  # 付与親ボーン
        f.write(struct.pack('<f', 1.0))  # 付与率
    
    # 軸固定
    if flags & 0x0400 and fixed_axis:
        f.write(struct.pack('<fff', *fixed_axis))
    
    # ローカル軸
    if flags & 0x0800 and local_x and local_z:
        f.write(struct.pack('<fff', *local_x))
        f.write(struct.pack('<fff', *local_z))
    
    # 物理後変形
    if flags & 0x1000:
        pass
    
    # 外部親変形
    if flags & 0x2000:
        f.write(struct.pack('<I', key_value if key_value else 0))


def create_local_axis_test_model():
    """ローカル軸設定のテストモデルを作成"""
    output_path = os.path.join(os.path.dirname(__file__), "data", "for_unit_test", "test_local_axis.pmx")
    
    with open(output_path, 'wb') as f:
        # ヘッダー
        write_pmx_header(f)
        
        # モデル情報
        write_text(f, "ローカル軸テストモデル")
        write_text(f, "Local Axis Test Model")
        write_text(f, "ローカル軸設定の動作検証用モデル")
        write_text(f, "Model for local axis setting verification")
        
        # 頂点（簡略化）
        vertices = [[0, 0, 0]] * 8
        f.write(struct.pack('<I', len(vertices)))
        for v in vertices:
            write_vertex(f, v, [0, 1, 0], [0, 0], [0], [1.0])
        
        # 面
        faces = [0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7]
        f.write(struct.pack('<I', len(faces)))
        for idx in faces:
            f.write(struct.pack('B', idx))
        
        # テクスチャ（なし）
        f.write(struct.pack('<I', 0))
        
        # 材質
        f.write(struct.pack('<I', 1))
        write_text(f, "マテリアル")
        write_text(f, "Material")
        f.write(struct.pack('<ffff', 0.8, 0.8, 0.8, 1.0))
        f.write(struct.pack('<fff', 0.5, 0.5, 0.5))
        f.write(struct.pack('<f', 5.0))
        f.write(struct.pack('<fff', 0.3, 0.3, 0.3))
        f.write(struct.pack('B', 0x01))
        f.write(struct.pack('<ffff', 0, 0, 0, 1))
        f.write(struct.pack('<f', 1.0))
        f.write(struct.pack('b', -1))
        f.write(struct.pack('b', -1))
        f.write(struct.pack('B', 0))
        f.write(struct.pack('B', 0))
        f.write(struct.pack('b', -1))
        write_text(f, "")
        f.write(struct.pack('<I', len(faces)))
        
        # ボーン
        bones = []
        
        # 0: センター（通常のボーン）
        bones.append({
            'name_jp': 'センター', 'name_en': 'Center',
            'pos': [0, 0, 0], 'parent': -1, 'layer': 0,
            'flags': 0x0001 | 0x0002 | 0x0004 | 0x0008 | 0x0010,  # 接続先表示、回転可能、移動可能、表示、操作可
            'tail_pos': [0, 5, 0]
        })
        
        # 1: ローカル軸設定ボーン（X軸が45度傾いている）
        angle = math.radians(45)
        local_x = [math.cos(angle), math.sin(angle), 0]
        local_z = [0, 0, 1]
        bones.append({
            'name_jp': 'ローカル軸ボーン1', 'name_en': 'Local Axis Bone1',
            'pos': [5, 0, 0], 'parent': 0, 'layer': 0,
            'flags': 0x0001 | 0x0002 | 0x0008 | 0x0010 | 0x0800,  # ローカル軸フラグを含む
            'tail_pos': [7, 2, 0],
            'local_x': local_x,
            'local_z': local_z
        })
        
        # 2: 軸固定ボーン（Y軸固定）
        bones.append({
            'name_jp': '軸固定ボーン', 'name_en': 'Fixed Axis Bone',
            'pos': [-5, 0, 0], 'parent': 0, 'layer': 0,
            'flags': 0x0001 | 0x0002 | 0x0008 | 0x0010 | 0x0400,  # 軸固定フラグを含む
            'tail_pos': [-5, 3, 0],
            'fixed_axis': [0, 1, 0]  # Y軸固定
        })
        
        # 3: ローカル軸設定ボーン2（Z軸が30度傾いている）
        angle_z = math.radians(30)
        local_x2 = [1, 0, 0]
        local_z2 = [0, math.sin(angle_z), math.cos(angle_z)]
        bones.append({
            'name_jp': 'ローカル軸ボーン2', 'name_en': 'Local Axis Bone2',
            'pos': [0, 5, 0], 'parent': 0, 'layer': 0,
            'flags': 0x0001 | 0x0002 | 0x0008 | 0x0010 | 0x0800,
            'tail_pos': [0, 7, 2],
            'local_x': local_x2,
            'local_z': local_z2
        })
        
        # 4: 複雑なローカル軸（X,Z両方とも傾いている）
        angle_x = math.radians(30)
        angle_y = math.radians(45)
        # X軸を回転
        local_x3 = [math.cos(angle_y), math.sin(angle_y), 0]
        # Z軸を回転
        local_z3 = [-math.sin(angle_y) * math.sin(angle_x), 
                    math.cos(angle_y) * math.sin(angle_x), 
                    math.cos(angle_x)]
        bones.append({
            'name_jp': '複雑ローカル軸', 'name_en': 'Complex Local Axis',
            'pos': [0, 0, 5], 'parent': 0, 'layer': 0,
            'flags': 0x0001 | 0x0002 | 0x0008 | 0x0010 | 0x0800,
            'tail_pos': [2, 2, 7],
            'local_x': local_x3,
            'local_z': local_z3
        })
        
        # 5: 子ボーン（ローカル軸ボーン1の子）
        bones.append({
            'name_jp': '子ボーン1', 'name_en': 'Child Bone1',
            'pos': [7, 2, 0], 'parent': 1, 'layer': 0,
            'flags': 0x0001 | 0x0002 | 0x0008 | 0x0010,
            'tail_pos': [9, 4, 0]
        })
        
        # 6: 孫ボーン（子ボーン1の子）
        bones.append({
            'name_jp': '孫ボーン1', 'name_en': 'Grandchild Bone1',
            'pos': [9, 4, 0], 'parent': 5, 'layer': 0,
            'flags': 0x0001 | 0x0002 | 0x0008 | 0x0010,
            'tail_pos': [11, 6, 0]
        })
        
        f.write(struct.pack('<I', len(bones)))
        for bone in bones:
            write_bone(f, bone['name_jp'], bone['name_en'], bone['pos'],
                      bone['parent'], bone['layer'], bone['flags'],
                      bone.get('tail_pos'), bone.get('fixed_axis'),
                      bone.get('local_x'), bone.get('local_z'))
        
        # モーフ（なし）
        f.write(struct.pack('<I', 0))
        
        # 表示枠
        f.write(struct.pack('<I', 3))
        
        # Root
        write_text(f, "Root")
        write_text(f, "Root")
        f.write(struct.pack('B', 1))
        f.write(struct.pack('<I', 0))
        
        # 表情
        write_text(f, "表情")
        write_text(f, "Facial")
        f.write(struct.pack('B', 1))
        f.write(struct.pack('<I', 0))
        
        # ボーン
        write_text(f, "ボーン")
        write_text(f, "Bones")
        f.write(struct.pack('B', 0))
        f.write(struct.pack('<I', len(bones)))
        for i in range(len(bones)):
            f.write(struct.pack('B', 0))
            f.write(struct.pack('B', i))
        
        # 剛体（なし）
        f.write(struct.pack('<I', 0))
        
        # ジョイント（なし）
        f.write(struct.pack('<I', 0))
    
    print(f"Created: {output_path}")


def create_hierarchy_local_axis_model():
    """階層構造でのローカル軸テストモデル"""
    output_path = os.path.join(os.path.dirname(__file__), "data", "for_unit_test", "test_hierarchy_local_axis.pmx")
    
    with open(output_path, 'wb') as f:
        # ヘッダー
        write_pmx_header(f)
        
        # モデル情報
        write_text(f, "階層ローカル軸テストモデル")
        write_text(f, "Hierarchy Local Axis Test Model")
        write_text(f, "階層構造でのローカル軸設定検証用")
        write_text(f, "For testing local axis in hierarchy")
        
        # 頂点（簡略化）
        vertices = [[0, 0, 0]] * 8
        f.write(struct.pack('<I', len(vertices)))
        for v in vertices:
            write_vertex(f, v, [0, 1, 0], [0, 0], [0], [1.0])
        
        # 面
        faces = [0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7]
        f.write(struct.pack('<I', len(faces)))
        for idx in faces:
            f.write(struct.pack('B', idx))
        
        # テクスチャ（なし）
        f.write(struct.pack('<I', 0))
        
        # 材質
        f.write(struct.pack('<I', 1))
        write_text(f, "マテリアル")
        write_text(f, "Material")
        f.write(struct.pack('<ffff', 0.8, 0.8, 0.8, 1.0))
        f.write(struct.pack('<fff', 0.5, 0.5, 0.5))
        f.write(struct.pack('<f', 5.0))
        f.write(struct.pack('<fff', 0.3, 0.3, 0.3))
        f.write(struct.pack('B', 0x01))
        f.write(struct.pack('<ffff', 0, 0, 0, 1))
        f.write(struct.pack('<f', 1.0))
        f.write(struct.pack('b', -1))
        f.write(struct.pack('b', -1))
        f.write(struct.pack('B', 0))
        f.write(struct.pack('B', 0))
        f.write(struct.pack('b', -1))
        write_text(f, "")
        f.write(struct.pack('<I', len(faces)))
        
        # ボーン（階層構造でローカル軸を持つ）
        bones = []
        
        # 0: ルート
        bones.append({
            'name_jp': 'ルート', 'name_en': 'Root',
            'pos': [0, 0, 0], 'parent': -1, 'layer': 0,
            'flags': 0x0001 | 0x0002 | 0x0004 | 0x0008 | 0x0010,
            'tail_pos': [0, 2, 0]
        })
        
        # 1: 親ボーン（45度回転したローカル軸）
        angle = math.radians(45)
        parent_local_x = [math.cos(angle), 0, math.sin(angle)]
        parent_local_z = [-math.sin(angle), 0, math.cos(angle)]
        bones.append({
            'name_jp': '親ボーン', 'name_en': 'Parent Bone',
            'pos': [0, 2, 0], 'parent': 0, 'layer': 0,
            'flags': 0x0001 | 0x0002 | 0x0008 | 0x0010 | 0x0800,
            'tail_pos': [0, 4, 0],
            'local_x': parent_local_x,
            'local_z': parent_local_z
        })
        
        # 2: 子ボーン（さらに30度回転したローカル軸）
        angle2 = math.radians(30)
        child_local_x = [1, 0, 0]
        child_local_z = [0, math.sin(angle2), math.cos(angle2)]
        bones.append({
            'name_jp': '子ボーン', 'name_en': 'Child Bone',
            'pos': [0, 4, 0], 'parent': 1, 'layer': 0,
            'flags': 0x0001 | 0x0002 | 0x0008 | 0x0010 | 0x0800,
            'tail_pos': [0, 6, 0],
            'local_x': child_local_x,
            'local_z': child_local_z
        })
        
        # 3: 孫ボーン（通常のボーン）
        bones.append({
            'name_jp': '孫ボーン', 'name_en': 'Grandchild Bone',
            'pos': [0, 6, 0], 'parent': 2, 'layer': 0,
            'flags': 0x0001 | 0x0002 | 0x0008 | 0x0010,
            'tail_pos': [0, 8, 0]
        })
        
        # 4-6: 別の階層（すべてローカル軸を持つ）
        bones.append({
            'name_jp': '腕根元', 'name_en': 'Arm Root',
            'pos': [3, 2, 0], 'parent': 0, 'layer': 0,
            'flags': 0x0001 | 0x0002 | 0x0008 | 0x0010 | 0x0800,
            'tail_pos': [5, 2, 1],
            'local_x': [0.9701, 0, 0.2425],  # 約14度
            'local_z': [-0.2425, 0, 0.9701]
        })
        
        bones.append({
            'name_jp': '腕中間', 'name_en': 'Arm Middle',
            'pos': [5, 2, 1], 'parent': 4, 'layer': 0,
            'flags': 0x0001 | 0x0002 | 0x0008 | 0x0010 | 0x0800,
            'tail_pos': [7, 2, 2],
            'local_x': [0.9848, 0.1736, 0],  # X軸周りに10度
            'local_z': [0, 0, 1]
        })
        
        bones.append({
            'name_jp': '腕先端', 'name_en': 'Arm End',
            'pos': [7, 2, 2], 'parent': 5, 'layer': 0,
            'flags': 0x0001 | 0x0002 | 0x0008 | 0x0010,
            'tail_pos': [9, 2, 3]
        })
        
        f.write(struct.pack('<I', len(bones)))
        for bone in bones:
            write_bone(f, bone['name_jp'], bone['name_en'], bone['pos'],
                      bone['parent'], bone['layer'], bone['flags'],
                      bone.get('tail_pos'), bone.get('fixed_axis'),
                      bone.get('local_x'), bone.get('local_z'))
        
        # モーフ（なし）
        f.write(struct.pack('<I', 0))
        
        # 表示枠
        f.write(struct.pack('<I', 3))
        
        # Root
        write_text(f, "Root")
        write_text(f, "Root")
        f.write(struct.pack('B', 1))
        f.write(struct.pack('<I', 0))
        
        # 表情
        write_text(f, "表情")
        write_text(f, "Facial")
        f.write(struct.pack('B', 1))
        f.write(struct.pack('<I', 0))
        
        # ボーン
        write_text(f, "ボーン")
        write_text(f, "Bones")
        f.write(struct.pack('B', 0))
        f.write(struct.pack('<I', len(bones)))
        for i in range(len(bones)):
            f.write(struct.pack('B', 0))
            f.write(struct.pack('B', i))
        
        # 剛体（なし）
        f.write(struct.pack('<I', 0))
        
        # ジョイント（なし）
        f.write(struct.pack('<I', 0))
    
    print(f"Created: {output_path}")


if __name__ == "__main__":
    create_local_axis_test_model()
    create_hierarchy_local_axis_model()