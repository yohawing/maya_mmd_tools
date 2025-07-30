#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
包括的な付与ボーン検証用PMXファイルを生成するスクリプト
"""

import os
import sys
import struct

# プロジェクトのルートディレクトリをPythonパスに追加
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)


def write_pmx_string(data: bytearray, text: str, encoding: str = 'utf-16le'):
    """PMX文字列を書き込む"""
    encoded = text.encode(encoding)
    data.extend(struct.pack('<L', len(encoded)))
    data.extend(encoded)


def create_comprehensive_given_bone_pmx():
    """包括的な付与ボーン検証用PMXファイルを生成"""
    data = bytearray()
    
    # ヘッダー
    data.extend(b'PMX ')  # 識別子
    data.extend(struct.pack('<f', 2.0))  # バージョン
    
    # グローバル設定
    data.extend(struct.pack('<B', 8))  # グローバル設定長
    data.extend(struct.pack('<B', 0))  # エンコーディング（UTF-16LE）
    data.extend(struct.pack('<B', 0))  # 追加UV数
    data.extend(struct.pack('<B', 2))  # 頂点インデックスサイズ
    data.extend(struct.pack('<B', 1))  # テクスチャインデックスサイズ
    data.extend(struct.pack('<B', 1))  # 材質インデックスサイズ
    data.extend(struct.pack('<B', 2))  # ボーンインデックスサイズ
    data.extend(struct.pack('<B', 1))  # モーフインデックスサイズ
    data.extend(struct.pack('<B', 1))  # 剛体インデックスサイズ
    
    # モデル情報
    write_pmx_string(data, "付与ボーン検証モデル")
    write_pmx_string(data, "Given Bone Test Model")
    write_pmx_string(data, "付与ボーンの各パターンを検証するためのテストモデル")
    write_pmx_string(data, "Test model for verifying various given bone patterns")
    
    # 頂点データ（なし）
    data.extend(struct.pack('<L', 0))  # 頂点数
    
    # 面データ（なし）
    data.extend(struct.pack('<L', 0))  # 面インデックス数
    
    # テクスチャデータ（なし）
    data.extend(struct.pack('<L', 0))  # テクスチャ数
    
    # 材質データ（なし）
    data.extend(struct.pack('<L', 0))  # 材質数
    
    # ボーンデータ
    bones = [
        # 基本ボーン
        {
            "name": "全ての親", "name_en": "Root",
            "pos": (0.0, 0.0, 0.0), "parent": -1, "layer": 0,
            "flags": 0x001F,  # 接続先ボーン指定、回転、移動、表示、操作
            "connect_bone": 1,
        },
        {
            "name": "センター", "name_en": "Center",
            "pos": (0.0, 0.0, 0.0), "parent": 0, "layer": 0,
            "flags": 0x001E,  # 回転、移動、表示、操作
            "offset": (0.0, 0.0, 0.0),
        },
        
        # パターン1: 通常の親子ボーン（付与なし）
        {
            "name": "親ボーン1", "name_en": "Parent1",
            "pos": (2.0, 0.0, 0.0), "parent": 1, "layer": 0,
            "flags": 0x001F, "connect_bone": 3,
        },
        {
            "name": "子ボーン1", "name_en": "Child1",
            "pos": (2.0, 2.0, 0.0), "parent": 2, "layer": 0,
            "flags": 0x001E, "offset": (0.0, 1.0, 0.0),
        },
        
        # パターン2: グローバル回転付与（付与率1.0）
        {
            "name": "付与元2", "name_en": "Source2",
            "pos": (4.0, 0.0, 0.0), "parent": 1, "layer": 0,
            "flags": 0x001F, "connect_bone": 5,
        },
        {
            "name": "グローバル回転付与2", "name_en": "GlobalRotGiven2",
            "pos": (4.0, 2.0, 0.0), "parent": 4, "layer": 1,
            "flags": 0x011E,  # 回転付与フラグ追加
            "offset": (0.0, 1.0, 0.0),
            "given_parent": 3, "given_rate": 1.0,
        },
        
        # パターン3: グローバル移動付与（付与率0.5）
        {
            "name": "付与元3", "name_en": "Source3",
            "pos": (6.0, 0.0, 0.0), "parent": 1, "layer": 0,
            "flags": 0x001F, "connect_bone": 7,
        },
        {
            "name": "グローバル移動付与3", "name_en": "GlobalMoveGiven3",
            "pos": (6.0, 2.0, 0.0), "parent": 6, "layer": 1,
            "flags": 0x021E,  # 移動付与フラグ追加
            "offset": (0.0, 1.0, 0.0),
            "given_parent": 3, "given_rate": 0.5,
        },
        
        # パターン4: グローバル回転+移動付与（付与率0.8）
        {
            "name": "付与元4", "name_en": "Source4",
            "pos": (8.0, 0.0, 0.0), "parent": 1, "layer": 0,
            "flags": 0x001F, "connect_bone": 9,
        },
        {
            "name": "グローバル回転移動付与4", "name_en": "GlobalRotMoveGiven4",
            "pos": (8.0, 2.0, 0.0), "parent": 8, "layer": 1,
            "flags": 0x031E,  # 回転+移動付与フラグ
            "offset": (0.0, 1.0, 0.0),
            "given_parent": 3, "given_rate": 0.8,
        },
        
        # パターン5: ローカル回転付与（付与率1.0）
        {
            "name": "付与元5", "name_en": "Source5",
            "pos": (10.0, 0.0, 0.0), "parent": 1, "layer": 0,
            "flags": 0x001F, "connect_bone": 11,
        },
        {
            "name": "ローカル回転付与5", "name_en": "LocalRotGiven5",
            "pos": (10.0, 2.0, 0.0), "parent": 10, "layer": 1,
            "flags": 0x019E,  # ローカル+回転付与フラグ
            "offset": (0.0, 1.0, 0.0),
            "given_parent": 10, "given_rate": 1.0,
        },
        
        # パターン6: ローカル移動付与（付与率0.7）
        {
            "name": "付与元6", "name_en": "Source6",
            "pos": (12.0, 0.0, 0.0), "parent": 1, "layer": 0,
            "flags": 0x001F, "connect_bone": 13,
        },
        {
            "name": "ローカル移動付与6", "name_en": "LocalMoveGiven6",
            "pos": (12.0, 2.0, 0.0), "parent": 12, "layer": 1,
            "flags": 0x029E,  # ローカル+移動付与フラグ
            "offset": (0.0, 1.0, 0.0),
            "given_parent": 12, "given_rate": 0.7,
        },
        
        # パターン7: ローカル回転+移動付与（付与率1.0）
        {
            "name": "付与元7", "name_en": "Source7",
            "pos": (14.0, 0.0, 0.0), "parent": 1, "layer": 0,
            "flags": 0x001F, "connect_bone": 15,
        },
        {
            "name": "ローカル回転移動付与7", "name_en": "LocalRotMoveGiven7",
            "pos": (14.0, 2.0, 0.0), "parent": 14, "layer": 1,
            "flags": 0x039E,  # ローカル+回転+移動付与フラグ
            "offset": (0.0, 1.0, 0.0),
            "given_parent": 14, "given_rate": 1.0,
        },
        
        # パターン8: 多重付与（チェーン）
        {
            "name": "付与元8", "name_en": "Source8",
            "pos": (16.0, 0.0, 0.0), "parent": 1, "layer": 0,
            "flags": 0x001F, "connect_bone": 17,
        },
        {
            "name": "付与チェーン8A", "name_en": "GivenChain8A",
            "pos": (16.0, 2.0, 0.0), "parent": 16, "layer": 1,
            "flags": 0x011E,  # 回転付与
            "offset": (0.0, 1.0, 0.0),
            "given_parent": 3, "given_rate": 0.5,
        },
        {
            "name": "付与チェーン8B", "name_en": "GivenChain8B",
            "pos": (16.0, 4.0, 0.0), "parent": 17, "layer": 2,
            "flags": 0x011E,  # 回転付与
            "offset": (0.0, 1.0, 0.0),
            "given_parent": 17, "given_rate": 0.5,
        },
        
        # パターン9: 付与率が負の値
        {
            "name": "付与元9", "name_en": "Source9",
            "pos": (18.0, 0.0, 0.0), "parent": 1, "layer": 0,
            "flags": 0x001F, "connect_bone": 20,
        },
        {
            "name": "負の付与率9", "name_en": "NegativeGiven9",
            "pos": (18.0, 2.0, 0.0), "parent": 19, "layer": 1,
            "flags": 0x011E,  # 回転付与
            "offset": (0.0, 1.0, 0.0),
            "given_parent": 3, "given_rate": -0.5,
        },
        
        # パターン10: 付与率が1を超える
        {
            "name": "付与元10", "name_en": "Source10",
            "pos": (20.0, 0.0, 0.0), "parent": 1, "layer": 0,
            "flags": 0x001F, "connect_bone": 22,
        },
        {
            "name": "付与率1.5_10", "name_en": "Given1.5_10",
            "pos": (20.0, 2.0, 0.0), "parent": 21, "layer": 1,
            "flags": 0x011E,  # 回転付与
            "offset": (0.0, 1.0, 0.0),
            "given_parent": 3, "given_rate": 1.5,
        },
    ]
    
    data.extend(struct.pack('<L', len(bones)))  # ボーン数
    
    for bone in bones:
        # ボーン名
        write_pmx_string(data, bone["name"])
        write_pmx_string(data, bone["name_en"])
        
        # 位置
        data.extend(struct.pack('<fff', *bone["pos"]))
        
        # 親ボーン
        data.extend(struct.pack('<h', bone["parent"]))
        
        # 変形階層
        data.extend(struct.pack('<L', bone["layer"]))
        
        # ボーンフラグ
        data.extend(struct.pack('<H', bone["flags"]))
        
        # フラグに応じた追加データ
        if bone["flags"] & 0x0001:  # 接続先表示方法（ボーン指定）
            data.extend(struct.pack('<h', bone["connect_bone"]))
        else:  # 接続先表示方法（相対座標オフセット）
            data.extend(struct.pack('<fff', *bone["offset"]))
        
        # 付与関連
        if bone["flags"] & 0x0300:  # 回転付与または移動付与
            data.extend(struct.pack('<h', bone["given_parent"]))
            data.extend(struct.pack('<f', bone["given_rate"]))
    
    # モーフデータ（なし）
    data.extend(struct.pack('<L', 0))  # モーフ数
    
    # 表示枠データ
    display_frames = [
        {
            "name": "Root", "name_en": "Root",
            "special": 1,  # 特殊枠
            "elements": []
        },
        {
            "name": "表情", "name_en": "Exp",
            "special": 1,  # 特殊枠
            "elements": []
        },
        {
            "name": "ボーン", "name_en": "bone",
            "special": 0,  # 通常枠
            "elements": [(0, i) for i in range(len(bones))]  # 全ボーンを追加
        }
    ]
    
    data.extend(struct.pack('<L', len(display_frames)))  # 表示枠数
    
    for frame in display_frames:
        write_pmx_string(data, frame["name"])
        write_pmx_string(data, frame["name_en"])
        data.extend(struct.pack('<B', frame["special"]))
        data.extend(struct.pack('<L', len(frame["elements"])))
        for elem_type, elem_index in frame["elements"]:
            data.extend(struct.pack('<B', elem_type))
            data.extend(struct.pack('<h', elem_index))
    
    # 剛体データ（なし）
    data.extend(struct.pack('<L', 0))  # 剛体数
    
    # ジョイントデータ（なし）
    data.extend(struct.pack('<L', 0))  # ジョイント数
    
    return bytes(data)


def main():
    """メイン処理"""
    output_dir = os.path.join(project_root, "tests", "data", "for_unit_test")
    output_path = os.path.join(output_dir, "test_given_bone_comprehensive.pmx")
    
    # PMXデータを生成
    pmx_data = create_comprehensive_given_bone_pmx()
    
    # ファイルに書き込み
    with open(output_path, "wb") as f:
        f.write(pmx_data)
    
    print(f"付与ボーン検証用PMXファイルを作成しました: {output_path}")
    
    # 解析して確認
    from mmd_tools.core import mmd_parser
    from mmd_tools.core.pmx_data.bone import PmxBoneFlag
    
    pmx = mmd_parser.parse_mmd_file(output_path)
    print(f"\nモデル名: {pmx.header.model_name}")
    print(f"ボーン数: {len(pmx.bones)}")
    
    print("\n=== 付与ボーン一覧 ===")
    for i, bone in enumerate(pmx.bones):
        if bone.get_flag(PmxBoneFlag.GIVEN_PARENT_ROTATE) or bone.get_flag(PmxBoneFlag.GIVEN_PARENT_MOVE):
            flags = []
            if bone.get_flag(PmxBoneFlag.LOCAL):
                flags.append("ローカル")
            if bone.get_flag(PmxBoneFlag.GIVEN_PARENT_ROTATE):
                flags.append("回転付与")
            if bone.get_flag(PmxBoneFlag.GIVEN_PARENT_MOVE):
                flags.append("移動付与")
            
            print(f"[{i}] {bone.name} ({bone.name_english})")
            print(f"  付与タイプ: {', '.join(flags)}")
            print(f"  付与親: [{bone.given_parent_bone_index}] {pmx.bones[bone.given_parent_bone_index].name if bone.given_parent_bone_index >= 0 else 'なし'}")
            print(f"  付与率: {bone.given_rate}")
            print(f"  変形階層: {bone.transform_layer}")


if __name__ == "__main__":
    main()