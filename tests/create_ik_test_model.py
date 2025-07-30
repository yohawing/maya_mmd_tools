"""IKテスト用のPMXモデルを作成するスクリプト"""

import sys
import os
import struct
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


def write_bone(f, name_jp, name_en, pos, parent_idx, layer, flags, tail_pos=None, ik_data=None):
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
    
    # IKの場合
    if flags & 0x0020 and ik_data:
        f.write(struct.pack('<b', ik_data['target']))  # ターゲットボーン
        f.write(struct.pack('<I', ik_data['loop']))    # ループ回数
        f.write(struct.pack('<f', ik_data['limit']))   # 制限角度
        f.write(struct.pack('<I', len(ik_data['links'])))  # リンク数
        
        for link in ik_data['links']:
            f.write(struct.pack('<b', link['bone_index']))
            f.write(struct.pack('B', 1 if link['has_limits'] else 0))
            if link['has_limits']:
                f.write(struct.pack('<fff', *link['limit_min']))
                f.write(struct.pack('<fff', *link['limit_max']))


def create_simple_ik_model():
    """シンプルなIKチェーンモデルを作成"""
    output_path = os.path.join(os.path.dirname(__file__), "data", "for_unit_test", "test_ik_simple.pmx")
    
    with open(output_path, 'wb') as f:
        # ヘッダー
        write_pmx_header(f)
        
        # モデル情報
        write_text(f, "IKテストモデル")
        write_text(f, "IK Test Model")
        write_text(f, "IKチェーンの動作検証用モデル")
        write_text(f, "Model for IK chain verification")
        
        # 頂点（簡単な立方体の8頂点）
        vertices = [
            [-1, -1,  1], [ 1, -1,  1], [ 1,  1,  1], [-1,  1,  1],
            [-1, -1, -1], [ 1, -1, -1], [ 1,  1, -1], [-1,  1, -1]
        ]
        
        f.write(struct.pack('<I', len(vertices)))
        for v in vertices:
            write_vertex(f, v, [0, 1, 0], [0, 0], [0], [1.0])
        
        # 面（12面 = 36頂点インデックス）
        faces = [
            0, 1, 2, 0, 2, 3,  # 前面
            4, 6, 5, 4, 7, 6,  # 背面
            0, 4, 5, 0, 5, 1,  # 下面
            2, 6, 7, 2, 7, 3,  # 上面
            1, 5, 6, 1, 6, 2,  # 右面
            0, 7, 4, 0, 3, 7   # 左面
        ]
        
        f.write(struct.pack('<I', len(faces)))
        for idx in faces:
            f.write(struct.pack('B', idx))
        
        # テクスチャ（なし）
        f.write(struct.pack('<I', 0))
        
        # 材質
        f.write(struct.pack('<I', 1))
        write_text(f, "マテリアル")
        write_text(f, "Material")
        # 拡散色
        f.write(struct.pack('<ffff', 0.8, 0.8, 0.8, 1.0))
        # 反射色
        f.write(struct.pack('<fff', 0.5, 0.5, 0.5))
        # 反射強度
        f.write(struct.pack('<f', 5.0))
        # 環境色
        f.write(struct.pack('<fff', 0.3, 0.3, 0.3))
        # フラグ
        f.write(struct.pack('B', 0x01))  # 両面描画
        # エッジ色
        f.write(struct.pack('<ffff', 0, 0, 0, 1))
        # エッジサイズ
        f.write(struct.pack('<f', 1.0))
        # テクスチャインデックス
        f.write(struct.pack('b', -1))
        # スフィアテクスチャインデックス
        f.write(struct.pack('b', -1))
        # スフィアモード
        f.write(struct.pack('B', 0))
        # Toonフラグ
        f.write(struct.pack('B', 0))
        # Toonテクスチャインデックス
        f.write(struct.pack('b', -1))
        # メモ
        write_text(f, "")
        # 面数
        f.write(struct.pack('<I', len(faces)))
        
        # ボーン
        bones = []
        # 0: センター
        bones.append({
            'name_jp': 'センター', 'name_en': 'Center',
            'pos': [0, 0, 0], 'parent': -1, 'layer': 0,
            'flags': 0x0001, 'tail_pos': [0, 2, 0]
        })
        # 1-4: ボーンチェーン
        bone_positions = [[0, 2, 0], [0, 4, 0], [0, 6, 0], [0, 8, 0]]
        for i, pos in enumerate(bone_positions):
            bones.append({
                'name_jp': f'ボーン{i+1}', 'name_en': f'Bone{i+1}',
                'pos': pos, 'parent': i, 'layer': 0,
                'flags': 0x0001,
                'tail_pos': bone_positions[i+1] if i < 3 else [0, 10, 0]
            })
        # 5: IKボーン
        bones.append({
            'name_jp': '足IK', 'name_en': 'Leg IK',
            'pos': [0, 8, 0], 'parent': 0, 'layer': 0,
            'flags': 0x0020,  # IK
            'ik_data': {
                'target': 4,  # Bone4
                'loop': 10,
                'limit': 3.14159,
                'links': [
                    {'bone_index': 3, 'has_limits': True, 'limit_min': [-3.14159, 0, 0], 'limit_max': [0, 0, 0]},
                    {'bone_index': 2, 'has_limits': True, 'limit_min': [-3.14159, 0, 0], 'limit_max': [0, 0, 0]},
                    {'bone_index': 1, 'has_limits': True, 'limit_min': [-3.14159, 0, 0], 'limit_max': [0, 0, 0]}
                ]
            }
        })
        
        f.write(struct.pack('<I', len(bones)))
        for bone in bones:
            write_bone(f, bone['name_jp'], bone['name_en'], bone['pos'],
                      bone['parent'], bone['layer'], bone['flags'],
                      bone.get('tail_pos'), bone.get('ik_data'))
        
        # モーフ（なし）
        f.write(struct.pack('<I', 0))
        
        # 表示枠
        f.write(struct.pack('<I', 4))
        
        # Root
        write_text(f, "Root")
        write_text(f, "Root")
        f.write(struct.pack('B', 1))  # 特殊枠
        f.write(struct.pack('<I', 0))  # 要素数
        
        # 表情
        write_text(f, "表情")
        write_text(f, "Facial")
        f.write(struct.pack('B', 1))  # 特殊枠
        f.write(struct.pack('<I', 0))  # 要素数
        
        # IK
        write_text(f, "IK")
        write_text(f, "IK")
        f.write(struct.pack('B', 0))  # 通常枠
        f.write(struct.pack('<I', 1))  # 要素数
        f.write(struct.pack('B', 0))   # ボーン
        f.write(struct.pack('B', 5))   # IKボーン
        
        # ボーン
        write_text(f, "ボーン")
        write_text(f, "Bones")
        f.write(struct.pack('B', 0))  # 通常枠
        f.write(struct.pack('<I', 5))  # 要素数
        for i in range(5):
            f.write(struct.pack('B', 0))  # ボーン
            f.write(struct.pack('B', i))  # インデックス
        
        # 剛体（なし）
        f.write(struct.pack('<I', 0))
        
        # ジョイント（なし）
        f.write(struct.pack('<I', 0))
    
    print(f"Created: {output_path}")


def create_complex_ik_model():
    """複雑なIKチェーンモデルを作成（両脚）"""
    output_path = os.path.join(os.path.dirname(__file__), "data", "for_unit_test", "test_ik_complex.pmx")
    
    with open(output_path, 'wb') as f:
        # ヘッダー
        write_pmx_header(f)
        
        # モデル情報
        write_text(f, "複数IKチェーンテストモデル")
        write_text(f, "Multi IK Chain Test Model")
        write_text(f, "複数のIKチェーンと制限の検証用")
        write_text(f, "For testing multiple IK chains and constraints")
        
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
        
        # 0: センター
        bones.append({
            'name_jp': 'センター', 'name_en': 'Center',
            'pos': [0, 5, 0], 'parent': -1, 'layer': 0,
            'flags': 0x0001, 'tail_pos': [0, 10, 0]
        })
        
        # 左脚チェーン (1-3)
        left_positions = [[2, 5, 0], [2, 2.5, 0], [2, 0, 0]]
        for i, pos in enumerate(left_positions):
            bones.append({
                'name_jp': f'左脚{i+1}', 'name_en': f'LeftLeg{i+1}',
                'pos': pos, 'parent': 0 if i == 0 else i, 'layer': 0,
                'flags': 0x0001,
                'tail_pos': left_positions[i+1] if i < 2 else [2, -1, 0]
            })
        
        # 4: 左足IK
        bones.append({
            'name_jp': '左足IK', 'name_en': 'Left Leg IK',
            'pos': [2, 0, 0], 'parent': 0, 'layer': 0,
            'flags': 0x0020,
            'ik_data': {
                'target': 3,
                'loop': 20,
                'limit': 2.0,
                'links': [
                    {'bone_index': 2, 'has_limits': True, 'limit_min': [-3.14159, 0, 0], 'limit_max': [0, 0, 0]},
                    {'bone_index': 1, 'has_limits': True, 'limit_min': [-1.5708, -0.5236, -0.5236], 'limit_max': [1.5708, 0.5236, 0.5236]}
                ]
            }
        })
        
        # 右脚チェーン (5-7)
        right_positions = [[-2, 5, 0], [-2, 2.5, 0], [-2, 0, 0]]
        for i, pos in enumerate(right_positions):
            bones.append({
                'name_jp': f'右脚{i+1}', 'name_en': f'RightLeg{i+1}',
                'pos': pos, 'parent': 0 if i == 0 else i + 4, 'layer': 0,
                'flags': 0x0001,
                'tail_pos': right_positions[i+1] if i < 2 else [-2, -1, 0]
            })
        
        # 8: 右足IK
        bones.append({
            'name_jp': '右足IK', 'name_en': 'Right Leg IK',
            'pos': [-2, 0, 0], 'parent': 0, 'layer': 0,
            'flags': 0x0020,
            'ik_data': {
                'target': 7,
                'loop': 20,
                'limit': 2.0,
                'links': [
                    {'bone_index': 6, 'has_limits': True, 'limit_min': [-3.14159, 0, 0], 'limit_max': [0, 0, 0]},
                    {'bone_index': 5, 'has_limits': True, 'limit_min': [-1.5708, -0.5236, -0.5236], 'limit_max': [1.5708, 0.5236, 0.5236]}
                ]
            }
        })
        
        f.write(struct.pack('<I', len(bones)))
        for bone in bones:
            write_bone(f, bone['name_jp'], bone['name_en'], bone['pos'],
                      bone['parent'], bone['layer'], bone['flags'],
                      bone.get('tail_pos'), bone.get('ik_data'))
        
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
        
        # IK
        write_text(f, "IK")
        write_text(f, "IK")
        f.write(struct.pack('B', 0))
        f.write(struct.pack('<I', 2))
        f.write(struct.pack('B', 0))
        f.write(struct.pack('B', 4))
        f.write(struct.pack('B', 0))
        f.write(struct.pack('B', 8))
        
        # 剛体（なし）
        f.write(struct.pack('<I', 0))
        
        # ジョイント（なし）
        f.write(struct.pack('<I', 0))
    
    print(f"Created: {output_path}")


if __name__ == "__main__":
    create_simple_ik_model()
    create_complex_ik_model()