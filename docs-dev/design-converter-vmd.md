# VMDConverter設計書

## 概要
VMDConverter は、MMD（MikuMikuDance）のモーションデータファイル（VMD）をMayaのアニメーションデータに変換するコンポーネントです。既にMayaシーンに存在するPMD/PMXモデルに対して、ボーンアニメーションとモーフアニメーションを適用します。

## 機能要件

### 1. ボーンアニメーション変換
- VMDのボーンフレームデータをMayaのジョイントアニメーションに変換
- 位置（translate）と回転（rotate）のキーフレームを設定
- MMDとMayaで異なるZ軸の向きを変換（両方右手系だがZ軸が逆向き）
- 補間曲線の標準精度での変換（線形補間またはベジェ近似）

### 2. モーフアニメーション変換
- VMDのモーフフレームデータをMayaのブレンドシェイプアニメーションに変換
- モーフ名のマッピング処理（日本語名からMaya互換名への変換）
- ウェイト値（0.0〜1.0）のキーフレーム設定

### 3. タイムライン設定
- VMDのフレーム番号をMayaのタイムラインに適切にマッピング
- 30fps基準での時間変換
- アニメーション範囲の自動設定

### 4. アニメーションレイヤーサポート
- Mayaのアニメーションレイヤーシステムを活用した複数VMDファイルの管理
- 各VMDファイルを独立したアニメーションレイヤーとしてインポート
- レイヤーのブレンドモードとウェイトの設定
- 既存アニメーションの保護（非破壊的なインポート）

### 5. エラーハンドリング
- 存在しないボーン/モーフ名への対処
- 不正なキーフレームデータの検出と警告
- 部分的な変換成功の許可（一部失敗しても継続）

## 技術仕様

### VMDボーンキーフレームのデータ構造
```python
class VmdBoneKeyframe:
    """VMDボーンキーフレームのバイナリ構造
    全体: 111バイト
    """
    bone_name: str      # 15バイト - null終端文字列
    frame_number: int   # 4バイト - unsigned int
    position: tuple     # 12バイト - (x, y, z) 各4バイトfloat
    rotation: tuple     # 16バイト - (x, y, z, w) クォータニオン、各4バイトfloat
    interpolation: bytes # 64バイト - 補間データ（実際は16バイトが有効）
```

### 補間データ構造の詳細
```python
class InterpolationCurve:
    """VMD補間カーブクラス
    16バイトの補間データを管理
    """
    def __init__(self, data: bytes):
        # 4つの独立した補間カーブ（各4バイト）
        self.x_curve = self._parse_curve(data[0:4])    # X位置補間
        self.y_curve = self._parse_curve(data[4:8])    # Y位置補間
        self.z_curve = self._parse_curve(data[8:12])   # Z位置補間
        self.r_curve = self._parse_curve(data[12:16])  # 回転補間
    
    def _parse_curve(self, curve_data: bytes) -> dict:
        """4バイトのカーブデータを解析
        Returns: {'x1': int, 'y1': int, 'x2': int, 'y2': int}
        """
        return {
            'x1': curve_data[0],  # 0-127
            'x2': curve_data[1],  # 0-127
            'y1': curve_data[2],  # 0-127
            'y2': curve_data[3]   # 0-127
        }
```

### クラス構造
```python
class VmdConverter:
    def __init__(self):
        self.bone_name_mapping = {}  # VMDボーン名 -> Mayaジョイント名
        self.morph_name_mapping = {}  # VMDモーフ名 -> Mayaブレンドシェイプターゲット名
        self.fps = 60.0
        self.logger = get_logger()
    
    def convert(self, vmd_data: VmdParser, target_namespace: str = None) -> bool:
        """メイン変換処理"""
        
    def _build_name_mappings(self, target_namespace: str = None):
        """名前マッピングの構築"""
        
    def _convert_bone_animation(self, bone_frames: list) -> dict:
        """ボーンアニメーション変換"""
        
    def _convert_morph_animation(self, morph_frames: list) -> dict:
        """モーフアニメーション変換"""
        
    def _apply_interpolation(self, frame_data: list) -> list:
        """補間曲線の適用"""
        
    def _set_keyframes(self, node: str, attribute: str, keyframes: list):
        """Mayaへのキーフレーム設定"""
    
    def _quaternion_to_euler(self, quat: tuple) -> tuple:
        """クォータニオンをオイラー角に変換"""
    
    def _calculate_bezier_value(self, t: float, curve: dict) -> float:
        """ベジェ曲線の値を計算"""
```

### 座標系変換

#### MMDとMayaの座標系
- **MMD（右手系）**: X右、Y上、Z手前（画面から手前に向かって正）
- **Maya（右手系）**: X右、Y上、Z奥（画面の奥に向かって正）
- 両方とも右手座標系だが、Z軸の向きのみが異なる

#### 位置の変換規則
- `maya_x = mmd_x`（変換なし）
- `maya_y = mmd_y`（変換なし）
- `maya_z = -mmd_z`（Z軸反転）

#### 回転の変換規則
Z軸の向きが逆になることで、回転方向にも影響が生じます：

1. **クォータニオンのZ成分を反転**
   - `maya_quat = (mmd_x, mmd_y, -mmd_z, mmd_w)`
   - これによりZ軸周りの回転が正しく変換される

2. **オイラー角への変換後、全軸の回転を反転**
   - Z軸の向きが逆のため、X軸とY軸の回転方向も反転する
   - `maya_rx = -euler_x`（X軸回転を反転）
   - `maya_ry = -euler_y`（Y軸回転を反転）
   - `maya_rz = -euler_z`（Z軸回転も反転）

#### 変換の理由
- Z軸が反転すると、右手の法則により他の軸の回転方向も影響を受ける
- 例：MMDでX軸周りに正の回転は、Mayaでは負の回転として表現される

### 回転データの処理

#### クォータニオンによる回転表現
VMDでは回転データをクォータニオン（四元数）として保存します：
- 形式: (X, Y, Z, W) - 4つの浮動小数点数
- 利点:
  - ジンバルロックの回避
  - スムーズな回転補間
  - 任意の軸周りの回転を効率的に表現
  - 回転の合成が簡単

#### クォータニオンの特性
- 単位クォータニオン: |q| = √(x² + y² + z² + w²) = 1
- 恒等回転: (0, 0, 0, 1)
- 逆回転: q⁻¹ = (-x, -y, -z, w) / |q|²

#### MayaへのクォータニオンMayaでは内部的にオイラー角で回転を管理するため、変換が必要：
1. VMDクォータニオンをMayaのMQuaternionオブジェクトに変換
2. MQuaternion.asEulerRotation()でオイラー角に変換
3. 必要に応じて回転順序を指定（XYZ, ZYX等）

### 補間曲線の処理

#### VMD補間データ構造
VMDの補間データは64バイトで構成されており、ボーンアニメーションの場合、実際には16バイトが有効に使用されます：
- 4つの独立した補間カーブ（X位置、Y位置、Z位置、回転）
- 各カーブは2つの制御点で定義（ベジェ曲線）
- 制御点は128×128グリッド上に配置（値の範囲: 0-127）
- 各制御点はX座標とY座標を持つ（各1バイト）

#### 補間カーブの詳細仕様
```
補間データ配列（16バイト）:
- X軸補間: [X1, X2, Y1, Y2] (4バイト)
- Y軸補間: [X1, X2, Y1, Y2] (4バイト)
- Z軸補間: [X1, X2, Y1, Y2] (4バイト)
- 回転補間: [X1, X2, Y1, Y2] (4バイト)

※ 各軸で独立した補間カーブを持つことで、
  例えば上下動は線形、前後動はスムーズなど、
  軸ごとに異なる動きを表現可能
```

#### 変換処理フロー
1. VMDの補間パラメータ（16バイト）を解析
2. 各軸の制御点から3次ベジェ曲線を構築
   - P0 = (0, 0) - 開始点（前のキーフレーム）
   - P1 = (X1/127, Y1/127) - 第1制御点
   - P2 = (X2/127, Y2/127) - 第2制御点
   - P3 = (1, 1) - 終了点（次のキーフレーム）
3. Mayaのアニメーションカーブへマッピング
   - 線形補間: タンジェントタイプ "linear"
   - ベジェ補間: タンジェントタイプ "spline" または "auto"

### 名前マッピング戦略
1. カスタム属性から元の名前を取得（pmx_bone_name, pmd_bone_name）
2. Unicode正規化とサニタイズ処理
3. 完全一致 → 部分一致 → 類似度マッチングの順で探索

## 実装上の注意点

### キーフレーム間の補間処理
1. VMDファイルではキーフレームが飛び飛びに存在
   - 例: フレーム0, 10, 30にキーがある場合、間のフレームは補間で生成
2. 補間計算の実装
   - 時間tを0〜1に正規化（現在フレーム位置の割合）
   - ベジェ曲線のパラメータuを二分探索で求める
   - uからベジェ曲線のy値を計算して補間値を取得

### 物理演算との干渉
- 一部のバイトが物理演算インジケータで上書きされる場合がある
- 物理演算対象のボーンは通常のキーフレームアニメーションと競合する可能性
- 実装時は物理演算フラグをチェックして適切に処理

### 座標系の考慮事項
- VMDの位置データはバインドポーズからの相対座標
- PMD/PMXモデルのボーン位置は世界座標系で定義
- 変換時は親子関係を考慮した相対変換が必要

### エラー処理戦略
- ボーン名が見つからない場合: 警告を出して該当キーフレームをスキップ
- 不正なクォータニオン（非正規化）: 正規化して処理を継続
- 補間データの異常値: デフォルト値（線形補間）にフォールバック

## パフォーマンス最適化

### メモリ効率の考慮
1. 大量のキーフレーム処理
   - ストリーミング処理でメモリ使用量を抑制
   - 必要に応じてキーフレームをバッチ処理
2. 補間計算のキャッシュ
   - 同一補間カーブの再計算を避ける
   - よく使われる補間パターンを事前計算

### 処理速度の最適化
1. ベジェ曲線の近似
   - 完全な精度より実用的な速度を優先
   - 固定ステップでの近似テーブル使用を検討
2. Maya APIの効率的な使用
   - キーフレーム設定をバッチ処理
   - アンドゥスタックの一時的な無効化

### スケーラビリティ
- 10,000フレーム以上のアニメーションでも実用的な速度
- プログレスバーやキャンセル機能の実装
- 非同期処理の検討（Maya 2020以降）

## 段階的実装

### フェーズ1: 基本機能（初回実装）
- ボーンの位置・回転アニメーション変換
- 線形補間のみサポート
- 基本的なエラーハンドリング

### フェーズ2: モーフサポート
- ブレンドシェイプアニメーション変換
- モーフ名マッピング機能

### フェーズ3: 補間曲線改善
- ベジェ補間の近似実装
- アニメーションカーブの最適化

## テスト計画

### 単体テスト
1. 座標系変換の正確性
2. フレーム番号からタイムへの変換
3. 名前マッピングのロジック

### 統合テスト
1. 単純なボーンアニメーション（1ボーン、数フレーム）
2. 階層構造を持つボーンアニメーション
3. モーフアニメーションの適用
4. 存在しないボーン/モーフへの対処

### パフォーマンステスト
- 大量のキーフレーム（10,000フレーム以上）での処理速度
- メモリ使用量の監視

## ドキュメント

### 使用例
```python
# VMDファイルの読み込み
vmd_parser = VmdParser()
vmd_parser.parse_file("dance.vmd")

# 変換実行
converter = VmdConverter()
success = converter.convert(vmd_parser, target_namespace="character1")

# アニメーションレイヤーを使用した複数VMDのインポート
# 1つ目のVMD（ベースモーション）
vmd_parser1 = VmdParser()
vmd_parser1.parse_file("base_motion.vmd")
converter.convert(vmd_parser1, target_namespace="character1", layer_name="BaseMotion")

# 2つ目のVMD（追加モーション）
vmd_parser2 = VmdParser()
vmd_parser2.parse_file("additional_motion.vmd") 
converter.convert(vmd_parser2, target_namespace="character1", layer_name="AdditionalMotion")

# レイヤーの重みを調整
cmds.animLayer("AdditionalMotion", edit=True, weight=0.5)
```

### 制限事項
- IKボーンのアニメーションは未サポート
- カメラ・ライトアニメーションは未サポート
- 物理演算の影響は考慮されない
- 補間曲線は完全な再現ではなく近似値

### Quaternion補間サポート（2025/07/19追加）
VMDファイルのQuaternion回転データをより正確に再現するため、MayaのQuaternion補間モードをサポートしました。

#### 機能概要
- キーフレーム設定後、`rotationInterpolation`コマンドでQuaternion補間に変換
- ジンバルロック問題を回避し、より自然な回転を実現

#### 使用方法
```python
# Quaternion補間を有効にして変換（デフォルト）
converter = VmdConverter(use_quaternion_interpolation=True)

# Quaternion補間を無効にして変換（従来のEuler補間）
converter = VmdConverter(use_quaternion_interpolation=False)
```

#### 技術的詳細
- MayaはEuler角でキーフレームを保存しますが、補間計算時にQuaternionを使用
- 球面線形補間（SLERP）により、最短経路での回転を実現
- 180度を超える回転でも自然な動きを維持

### IKのPoleVector自動生成（2025/07/19追加）

VMDファイルには足IKのPoleVectorデータが含まれていないため、太ももの回転データから膝の向きを計算し、動的にPoleVectorの位置を生成する機能を実装しました。

#### 機能概要
- 足IKハンドル作成時にPoleTargetロケータを自動生成
- VMDの太もも回転データから膝の向きを推定
- 各キーフレームでPoleTargetの位置を更新

#### 実装詳細

##### 1. PoleTargetの作成
```python
# IKハンドル作成時にPoleTargetも作成
pole_target = cmds.spaceLocator(name=f"{ik_bone}_poleTarget")[0]
# 足IKの親（通常は足IKコントローラーの親）の子として配置
cmds.parent(pole_target, leg_ik_parent)
# PoleVectorConstraintでIKハンドルに接続
cmds.poleVectorConstraint(pole_target, ik_handle)
```

##### 2. PoleVector位置の計算
```python
def calculate_pole_vector_position(hip_pos, ankle_pos, thigh_rotation, offset_distance=10.0):
    """
    太ももの回転から膝の向きを計算し、PoleVectorの位置を決定
    
    Args:
        hip_pos: 股関節の位置
        ankle_pos: 足首の位置
        thigh_rotation: 太ももの回転（VMDデータ）
        offset_distance: PoleVectorのオフセット距離
    
    Returns:
        PoleVectorの位置
    """
    # IKチェーンの中点を計算
    mid_point = [(hip_pos[i] + ankle_pos[i]) / 2 for i in range(3)]
    
    # 太ももの回転からY軸回転を抽出（膝の向き）
    knee_direction = extract_knee_direction_from_rotation(thigh_rotation)
    
    # IKチェーンの平面に対して垂直方向にオフセット
    pole_position = calculate_offset_position(mid_point, knee_direction, offset_distance)
    
    return pole_position
```

##### 3. キーフレーム処理
```python
# VMDの太ももキーフレームと同期してPoleTargetにもキーフレームを設定
for frame in thigh_frames:
    # 太ももの回転からPoleVector位置を計算
    pole_pos = calculate_pole_vector_position(
        hip_pos=hip_position,
        ankle_pos=ankle_position,
        thigh_rotation=frame.rotation,
        offset_distance=pole_distance
    )
    
    # PoleTargetの位置にキーフレームを設定
    cmds.setKeyframe(pole_target, attribute='translateX', time=frame.frame_number, value=pole_pos[0])
    cmds.setKeyframe(pole_target, attribute='translateY', time=frame.frame_number, value=pole_pos[1])
    cmds.setKeyframe(pole_target, attribute='translateZ', time=frame.frame_number, value=pole_pos[2])
```

#### 技術的な考慮事項

##### 座標系変換
- VMDの太もも回転データをMayaの座標系に変換
- Z軸の反転を考慮（位置: maya_z = -mmd_z）
- 回転はクォータニオンZ成分反転後、オイラー角で全軸反転

##### 初期ポーズの重要性
- MMDモデルの初期ポーズでの膝の向きを基準として使用
- バインドポーズでのPoleVectorのデフォルト位置を適切に設定

##### スムージング処理
- フレーム間でPoleVector位置が急激に変化しないよう補間
- ベジェ補間やスプライン補間を適用可能

##### IKチェーンの識別
- 足IKチェーンを自動識別（「左足IK」「右足IK」などの名前パターン）
- 対応する太ももボーン（「左足」「右足」）とのマッピング

#### 使用方法
```python
# PoleVector自動生成を有効にして変換（デフォルト）
converter = VmdConverter(generate_pole_vectors=True)

# PoleVector自動生成を無効にして変換
converter = VmdConverter(generate_pole_vectors=False)
```

### アニメーションレイヤー機能（2025/08/01追加）

複数のVMDファイルを非破壊的に重ね合わせるため、Mayaのアニメーションレイヤーシステムを活用する機能を実装しました。

#### 機能概要
- 各VMDファイルを独立したアニメーションレイヤーとしてインポート
- 既存のアニメーションを保持したまま新しいモーションを追加
- レイヤーごとにウェイトやブレンドモードを調整可能
- モーションの合成や部分的な適用が可能

#### 実装詳細

##### 1. アニメーションレイヤーの作成
```python
def create_animation_layer(layer_name: str, parent_layer: str = None) -> str:
    """
    アニメーションレイヤーを作成
    
    Args:
        layer_name: レイヤー名
        parent_layer: 親レイヤー名（階層構造を作る場合）
    
    Returns:
        作成されたレイヤー名
    """
    # 既存のレイヤーをチェック
    if cmds.animLayer(layer_name, query=True, exists=True):
        # 既存レイヤーに追加するか、新規作成するかの選択
        return layer_name
    
    # 新規レイヤー作成
    layer = cmds.animLayer(layer_name)
    
    # 親レイヤーがある場合は階層を設定
    if parent_layer:
        cmds.animLayer(layer, edit=True, parent=parent_layer)
    
    return layer
```

##### 2. レイヤーへのキーフレーム設定
```python
def set_keyframe_on_layer(node: str, attribute: str, time: float, value: float, layer: str):
    """
    特定のアニメーションレイヤーにキーフレームを設定
    
    Args:
        node: ノード名
        attribute: アトリビュート名
        time: 時間
        value: 値
        layer: レイヤー名
    """
    # レイヤーをアクティブにする
    cmds.animLayer(layer, edit=True, selected=True)
    
    # レイヤーにアトリビュートを追加
    cmds.animLayer(layer, edit=True, addAttribute=f"{node}.{attribute}")
    
    # キーフレームを設定
    cmds.setKeyframe(node, attribute=attribute, time=time, value=value, animLayer=layer)
```

##### 3. レイヤーのブレンドモード設定
```python
# Override: 完全に上書き（デフォルト）
cmds.animLayer(layer_name, edit=True, override=True)

# Additive: 加算合成
cmds.animLayer(layer_name, edit=True, override=False)

# ウェイト設定（0.0〜1.0）
cmds.animLayer(layer_name, edit=True, weight=0.7)
```

#### 使用例
```python
# 基本的な使用方法
converter = VmdConverter()

# ベースアニメーション
converter.convert(vmd_base, layer_name="BaseAnimation")

# 上半身のみのアニメーション（Additiveモード）
converter.convert(vmd_upper, layer_name="UpperBodyOverride", blend_mode="additive")

# 表情アニメーション（別レイヤー）
converter.convert(vmd_facial, layer_name="FacialAnimation")

# レイヤーの調整
cmds.animLayer("UpperBodyOverride", edit=True, weight=0.8)
cmds.animLayer("FacialAnimation", edit=True, mute=False)  # ミュート解除
```

#### 高度な使用例
```python
# 複数キャラクターへの同じモーション適用
characters = ["character1", "character2", "character3"]
for char in characters:
    converter.convert(vmd_data, 
                     target_namespace=char,
                     layer_name=f"{char}_Dancing")

# 時間オフセット付きインポート
converter.convert(vmd_data,
                 layer_name="DelayedMotion",
                 time_offset=30)  # 30フレーム遅延

# 部分的なボーンのみアニメーション
converter.convert(vmd_data,
                 layer_name="ArmsOnly",
                 bone_filter=["左腕", "右腕", "左ひじ", "右ひじ"])
```

#### 技術的な考慮事項

##### レイヤーの優先順位
- 上位レイヤーが下位レイヤーを上書き
- Overrideモードでは完全に置き換え
- Additiveモードでは値を加算

##### パフォーマンス
- 多数のレイヤーは評価速度に影響
- 不要なレイヤーはミュートまたは削除を推奨
- ベイク機能で最終結果を単一レイヤーに統合可能

##### 互換性
- Maya 2016以降で使用可能
- FBXエクスポート時はベイクが必要な場合あり

#### 制限事項
- 腕のIKには対応していない（通常MMDでは腕IKを使用しないため）
- 物理演算の影響は考慮されない
- 極端なポーズでは手動調整が必要な場合がある

### トラブルシューティング
- ボーンが見つからない場合: ボーン名マッピングの確認
- アニメーションがずれる場合: FPS設定の確認
- 回転が反転する場合: 座標系変換の確認
- PoleVectorが不自然な場合: 初期ポーズの膝の向きを確認
- レイヤーが適用されない場合: レイヤーのミュート状態とウェイトを確認
- アニメーションが重複する場合: レイヤーのブレンドモード（Override/Additive）を確認
