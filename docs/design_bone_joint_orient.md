# BoneConverter JointOrient機能設計

## 概要

現在のBoneConverterでは、MMDボーンをMayaのジョイントに変換する際、ボーンの位置情報のみが設定されており、ボーンの向き（JointOrient）が正しく設定されていません。本設計では、MMDボーンの方向情報を正しくMayaのJointOrientに変換する機能を追加します。

## 背景

### 現状の課題
- MMDボーンは独自の座標系とローカル軸を持つ
- 現在の実装では`_apply_bone_orientation`メソッドが呼び出されているが、実装されていない
- ボーンの向きが正しく設定されていないため、アニメーションが正しく再生されない可能性がある

### MMDボーンの方向情報
PMXボーンには以下の方向情報が含まれる：
- **軸固定（AXIS_FIXED）**: `axis_direction` - ボーンの主軸方向
- **ローカル軸（LOCAL_AXIS）**: `x_axis_direction`、`z_axis_direction` - ローカル座標系

PMDボーンには：
- `tail_pos_bone_index` - 子ボーンへの参照（方向の計算に使用可能）

## 設計方針

### 座標系の変換
- MMD：右手座標系（Y軸が上）
- Maya：左手座標系（Y軸が上）
- Z軸の反転が必要

### JointOrientの計算方法

#### PMXボーンの場合
1. **ローカル軸フラグがある場合**
   - `x_axis_direction`と`z_axis_direction`からY軸を計算
   - 3x3の回転行列を構築
   - MayaのJointOrientに変換

2. **軸固定フラグがある場合**
   - `axis_direction`を主軸として使用
   - 適切な補助軸を選択して直交座標系を構築

3. **子ボーンが存在する場合**
   - 子ボーンへの方向をX軸とする
   - Y軸を上方向（ワールド）に近づける
   - Z軸は外積で計算

4. **それ以外の場合**
   - 親ボーンと同じ向きを継承
   - ルートボーンの場合はワールド座標系を使用

#### PMDボーンの場合
1. **tail_pos_bone_indexが有効な場合**
   - 指定されたボーンへの方向をX軸とする
   - Y軸を上方向（ワールド）に近づける

2. **子ボーンが存在する場合**
   - PMXと同様の処理

3. **それ以外の場合**
   - PMXと同様の処理

## 実装詳細

### メソッド構成

```python
def _apply_bone_orientation(self, bones, maya_joints, format_type):
    """
    ボーンの向きを設定する。
    
    Args:
        bones: ボーンデータのリスト
        maya_joints: Mayaジョイントノードの名前のリスト
        format_type: 'pmx' または 'pmd'
    """
    if format_type == 'pmx':
        self._apply_pmx_bone_orientation(bones, maya_joints)
    else:
        self._apply_pmd_bone_orientation(bones, maya_joints)

def _apply_pmx_bone_orientation(self, bones, maya_joints):
    """PMXボーンの向きを設定"""
    # 子ボーンのマッピングを作成
    children_map = self._create_children_map(bones)
    
    for i, (bone, joint) in enumerate(zip(bones, maya_joints)):
        orient = self._calculate_pmx_joint_orient(
            bone, i, bones, children_map
        )
        self._set_joint_orient(joint, orient)

def _calculate_pmx_joint_orient(self, bone, bone_index, bones, children_map):
    """PMXボーンのJointOrientを計算"""
    # 実装詳細は後述

def _apply_pmd_bone_orientation(self, bones, maya_joints):
    """PMDボーンの向きを設定"""
    # 実装詳細は後述

def _create_children_map(self, bones):
    """親子関係のマッピングを作成"""
    children = {}
    for i, bone in enumerate(bones):
        if bone.parent_bone_index != -1:
            if bone.parent_bone_index not in children:
                children[bone.parent_bone_index] = []
            children[bone.parent_bone_index].append(i)
    return children

def _set_joint_orient(self, joint, orient):
    """ジョイントの向きを設定"""
    cmds.setAttr(f"{joint}.jointOrientX", orient[0])
    cmds.setAttr(f"{joint}.jointOrientY", orient[1])
    cmds.setAttr(f"{joint}.jointOrientZ", orient[2])
```

### ユーティリティメソッド

```python
def _matrix_to_euler(self, matrix):
    """3x3回転行列をオイラー角（度）に変換"""
    # Maya Python API2.0を使用した実装

def _create_rotation_matrix(self, x_axis, y_axis, z_axis):
    """3つの軸ベクトルから回転行列を作成"""
    # 正規化と直交化を含む

def _calculate_aim_matrix(self, aim_vector, up_vector):
    """エイムベクトルとアップベクトルから回転行列を計算"""
    # クロス積を使用した直交座標系の構築
```

## テスト計画

### ユニットテスト
- 各計算メソッドの個別テスト
- 座標系変換のテスト
- エッジケースのテスト（子ボーンなし、ルートボーンなど）

### 統合テスト
- PMXファイルのインポートテスト
  - ローカル軸を持つボーン
  - 軸固定ボーン
  - 通常のボーン階層
- PMDファイルのインポートテスト
- アニメーション再生の確認

### 検証項目
- ボーンの向きが正しく設定されているか
- アニメーションが正しく再生されるか
- IKが正しく動作するか

## 実装順序

1. ユーティリティメソッドの実装
   - 行列計算関連
   - 座標系変換

2. 基本的なJointOrient設定
   - 子ボーンへの向き
   - デフォルトの向き

3. PMX特有の機能
   - ローカル軸
   - 軸固定

4. PMD対応
   - tail_pos_bone_indexの処理

5. テストの実装と検証

## 考慮事項

### パフォーマンス
- 大量のボーンを処理する際の最適化
- Maya Python API2.0の活用

### 互換性
- 既存のMMDツールとの互換性
- Mayaの異なるバージョンでの動作

### エラーハンドリング
- 不正なボーンデータへの対処
- 循環参照の検出
