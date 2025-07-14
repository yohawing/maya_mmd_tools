# MMDボーン構造とIKシステム実装仕様

## 概要

本ドキュメントは、MikuMikuDance (MMD) のボーン構造とIK（Inverse Kinematics）システムの実装仕様をまとめたものです。

## ボーン構造

### 標準ボーン（必須ボーン）

MMDモデルが配布モーションデータで正しく動作するために必要な最小限のボーンセットです。

#### コアボーン
- センター (Center)
- 下半身 (Lower Body)
- 上半身 (Upper Body)
- 首 (Neck)
- 頭 (Head)

#### 体幹ボーン
- 上半身2 (Upper Body 2) - 準標準だが多くのモーションで必須
- 左目 (Left Eye)
- 右目 (Right Eye)

#### 腕ボーン
- 左肩 (Left Shoulder)
- 左腕 (Left Arm)
- 左ひじ (Left Elbow)
- 左手首 (Left Wrist)
- 右肩 (Right Shoulder)
- 右腕 (Right Arm)
- 右ひじ (Right Elbow)
- 右手首 (Right Wrist)

#### 脚・足IKボーン
- 左足 (Left Leg)
- 左ひざ (Left Knee)
- 左足首 (Left Ankle)
- 左足ＩＫ (Left Foot IK)
- 左つま先 (Left Toe)
- 左つま先ＩＫ (Left Toe IK)
- 右足 (Right Leg)
- 右ひざ (Right Knee)
- 右足首 (Right Ankle)
- 右足ＩＫ (Right Foot IK)
- 右つま先 (Right Toe)
- 右つま先ＩＫ (Right Toe IK)

#### 指ボーン
- 左親指０～２ (Left Thumb 0-2)
- 左人指１～３ (Left Index Finger 1-3)
- 左中指１～３ (Left Middle Finger 1-3)
- 左薬指１～３ (Left Ring Finger 1-3)
- 左小指１～３ (Left Pinky Finger 1-3)
- 右手も同様のパターン

### 準標準ボーン

モーションの表現力を高め、操作性を向上させるための追加ボーンです。

- グルーブ (Groove) - センターボーンの後に配置
- 腰 (Waist) - 下半身と足の間に作成
- 足IK親 (Foot IK Parent) - 足IKボーンの親
- 足先EX (Foot Tip EX)
- 全ての親 (Parent of All) - 全ボーンを一括制御
- 操作中心 (Operation Center) - カメラビューの中心用
- 手捻 (Hand Twist)
- 腕捻 (Arm Twist)

### ボーン実装時の注意点

1. **命名規則**
   - ボーン名の数字は全角・半角どちらも使用される
   - IKは全角「ＩＫ」と半角「IK」の違いに注意

2. **先端ボーン**
   - ☆マークの付いたボーンはMMD/nanoemでは表示されないが、正常動作に必要

3. **階層構造**
   - 腕の典型的な階層: 肩 > 腕 > ひじ > 手首
   - 適切な親子関係の設定が重要

## IKシステム

### IKの基本概念

IK（逆運動学）は、末端の位置から各関節の角度を逆算する手法です。直線的な動き（ストレートパンチなど）や面に沿った動き（拭き取り動作など）を実現する際に使用されます。

### MMDで使用されるIKアルゴリズム

#### CCD-IK (Cyclic Coordinate Descent)

MMDで主に使用されるアルゴリズムです。

**動作原理:**
1. 先端に最も近いジョイントから順に角度を計算
2. 各ジョイントで以下を実行:
   - 現在のジョイントから先端への方向ベクトルを計算
   - 現在のジョイントから目標位置への方向ベクトルを計算
   - 2つのベクトル間の回転を計算
   - ジョイントを回転

**実装パラメータ:**
- **Loop**: 1フレームあたりのIK計算回数
- **単位角**: 計算1回ごとの最大回転量（計算ごとに減衰）

**アルゴリズムの手順:**
```
1. 単位角の範囲でターゲットボーンがIKボーンに近づくよう影響下ボーンを回転
2. Loop回数分繰り返す
3. 各反復で単位角を減衰させて微調整
```

#### 実装に必要な情報

1. **ターゲットボーン**: IKが到達すべき目標位置
2. **リンクボーン配列**: IK影響下のボーンリスト
3. **変換情報**: ワールド座標系からローカル座標系への変換クォータニオン

### IK実装の制約と注意点

1. **MMDの制限事項**
   - IKはモデルに事前に組み込む必要がある
   - IKによる回転角度は記録されない
   - IK移動したボーンの子ボーンは追従しない

2. **収束性**
   - 目標が到達可能範囲外の場合、指定反復回数後も収束しない可能性
   - 適切なLoop数と単位角の設定が重要

3. **モデル互換性**
   - 特殊な補助ボーンや装飾がある腕では腕切りIKが失敗することがある
   - PMXEditorで動作してもMMDで動作しない場合は変形階層設定を確認

### 腕IKの実装

腕IKは標準では含まれていないため、追加実装が必要です。

**実装要件:**
- 左右の手先ボーンが必要
- IK Makerプラグインを使用して実装可能
- 腕切りIKにより表現の幅が大幅に拡張

**腕IKの階層構造:**
```
肩ボーン
└── 腕ボーン
    └── ひじボーン
        └── 手首ボーン
            └── 手先ボーン（IKターゲット）
```

## PMX形式でのボーン表現

### ボーンフラグ

PMX形式では以下のフラグでボーンの特性を定義:

- 0x0020: IK
- 0x0080: ローカル付与
- 0x0100: 回転付与
- 0x0200: 移動付与
- 0x1000: 物理後変形
- 0x2000: 外部親変形

### エディタでの表示

- IKボーン: 四角形で表示
- 通常ボーン: 青い円で表示

## 実装推奨事項

1. **標準ボーンチェック機能（バリデーション）**
   - 必須ボーンの存在確認
   - 必須ボーンに必要なパラメーターの検証

2. **IK設定の検証**
   - Loop数とUnit角度の妥当性チェック
   - リンクボーン配列の整合性確認

3. **エラーハンドリング**
   - 到達不可能な目標への対処
   - 無効なボーン参照の検出

## BoneConverterへの実装計画

現状のBoneConverterクラスの実装状況と、必要な追加実装について整理します。

### 現在の実装状況

1. **実装済み機能**
   - PMD/PMXボーンのMayaジョイントへの変換
   - ボーン階層構造の再現
   - スキンクラスターの作成とウェイト設定
   - カスタムアトリビュートでの元データ保持
   - PMXのIKフラグとIK関連データの保存（アトリビュートのみ）

2. **TODO項目として記載されている未実装機能**
   - ボーンのローカル軸、変形階層の正確な再現
   - IKボーンのMaya ikHandleへの変換

### 実装計画

#### フェーズ1: 標準ボーンのバリデーション機能

- validationディレクトリを作成して、そこにファイルを配置する。
- 

```python
class BoneValidator:
    """標準ボーンの存在と命名規則をチェックするクラス"""
    
    STANDARD_BONES = {
        "センター": ["center"],
        "上半身": ["upper_body"],
        "下半身": ["lower_body"],
        "左足ＩＫ": ["left_leg_ik", "左足IK"],
        # ... 他の標準ボーン
    }
    
    def validate_bones(self, bones):
        """標準ボーンの存在確認と命名規則の検証"""
        missing_bones = []
        naming_issues = []
        return missing_bones, naming_issues
```

#### フェーズ2: IKチェーンの作成

1. **IK情報の抽出とマッピング**
   ```python
   def _extract_ik_chains(self, bones, bone_map):
       """PMX/PMDボーンからIKチェーン情報を抽出"""
       ik_chains = []
       for i, bone in enumerate(bones):
           if hasattr(bone, 'bone_flag') and bone.get_flag(PmxBoneFlag.IK):
               ik_chain = {
                   'ik_bone': bone_map[i],
                   'target_bone': bone_map[bone.ik_target_bone_index],
                   'loop_count': bone.ik_loop_count,
                   'unit_angle': bone.ik_limit_angle,
                   'ik_links': []
               }
               # IKリンクの処理
               for link in bone.ik_links:
                   link_info = {
                       'bone': bone_map[link.ik_bone_index],
                       'angle_limit': link.angle_limit,
                       'limit_min': link.limit_min,
                       'limit_max': link.limit_max
                   }
                   ik_chain['ik_links'].append(link_info)
               ik_chains.append(ik_chain)
       return ik_chains
   ```

2. **Maya IKハンドルの作成**

- IKを作成する機能は`maya_utils.py`に実装します。
- 単体テストを`tests/test_maya_utils.py`に追加します。


   ```python
   def _create_maya_ik_handles(self, ik_chains):
       """IKチェーン情報からMayaのikHandleを作成"""
       ik_handles = []
       for chain in ik_chains:
           # IKチェーンの最初と最後のジョイントを特定
           start_joint = chain['ik_links'][-1]['bone'] if chain['ik_links'] else chain['target_bone']
           end_joint = chain['target_bone']
           
           # ikHandleを作成
           ik_handle = cmds.ikHandle(
               startJoint=start_joint,
               endEffector=end_joint,
               solver='ikRPsolver',  # または 'ikSCsolver'
               name=f"{chain['ik_bone']}_ikHandle"
           )[0]
           
           # IKハンドルをIKボーンにペアレント
           cmds.parent(ik_handle, chain['ik_bone'])
           
           # 角度制限の設定
           self._set_joint_limits(chain['ik_links'])
           
           ik_handles.append(ik_handle)
       return ik_handles
   ```

3. **ジョイント角度制限の設定**
   ```python
   def _set_joint_limits(self, ik_links):
       """IKリンクのジョイントに角度制限を設定"""
       for link in ik_links:
           if link['angle_limit']:
               joint = link['bone']
               # Mayaのラジアン変換（MMDは度数法）
               limit_min_rad = [math.radians(deg) for deg in link['limit_min']]
               limit_max_rad = [math.radians(deg) for deg in link['limit_max']]
               
               # ジョイントの回転制限を設定
               cmds.setAttr(f"{joint}.rotateMinX", limit_min_rad[0])
               cmds.setAttr(f"{joint}.rotateMaxX", limit_max_rad[0])
               cmds.setAttr(f"{joint}.rotateMinY", limit_min_rad[1])
               cmds.setAttr(f"{joint}.rotateMaxY", limit_max_rad[1])
               cmds.setAttr(f"{joint}.rotateMinZ", limit_min_rad[2])
               cmds.setAttr(f"{joint}.rotateMaxZ", limit_max_rad[2])
               
               # 制限を有効化
               cmds.setAttr(f"{joint}.minRotXLimitEnable", True)
               cmds.setAttr(f"{joint}.maxRotXLimitEnable", True)
               # Y, Zも同様
   ```

#### フェーズ3: ボーンのローカル軸設定

```python
def _set_bone_local_axis(self, joint, bone):
    """PMXボーンのローカル軸情報をMayaジョイントに適用"""
    if hasattr(bone, 'get_flag') and bone.get_flag(PmxBoneFlag.LOCAL_AXIS):
        x_axis = bone.x_axis_direction
        z_axis = bone.z_axis_direction
        
        # Y軸を外積で計算
        y_axis = maya_utils.cross_product(z_axis, x_axis)
        
        # ジョイントオリエンテーションの設定
        matrix = maya_utils.create_matrix_from_axes(x_axis, y_axis, z_axis)
        rotation = maya_utils.matrix_to_euler(matrix)
        
        cmds.setAttr(f"{joint}.jointOrientX", rotation[0])
        cmds.setAttr(f"{joint}.jointOrientY", rotation[1])
        cmds.setAttr(f"{joint}.jointOrientZ", rotation[2])
```

#### フェーズ4: 準標準ボーンの追加

```python
def _add_semi_standard_bones(self, maya_joints, skeleton_group):
    """準標準ボーンを追加"""
    # 全ての親
    parent_of_all = cmds.group(empty=True, name="全ての親", parent=skeleton_group)
    
    # グルーブ
    center_joint = self._find_joint_by_name(maya_joints, "センター")
    if center_joint:
        groove = cmds.group(empty=True, name="グルーブ", parent=parent_of_all)
        cmds.parent(center_joint, groove)
    
    # 腰ボーンの追加（下半身と足の間）
    # ... 実装
```

### 実装優先順位

1. **高優先度**
   - IKチェーンの抽出とMaya ikHandleの作成
   - 標準ボーンのバリデーション機能

2. **中優先度**
   - ジョイント角度制限の設定
   - 準標準ボーンの自動追加機能

3. **低優先度**
   - ローカル軸の詳細設定
   - 付与ボーンの実装
   - 外部親変形の実装

### テスト計画

1. **単体テスト**
   - IKチェーン抽出のテスト
   - 角度制限変換のテスト（度数法→ラジアン）

2. **統合テスト**
   - 標準的なMMDモデルでのIK動作確認
   - モーションデータ適用時のIK挙動確認