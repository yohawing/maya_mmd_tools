# VMDConverter設計書

## 概要
VMDConverter は、MMD（MikuMikuDance）のモーションデータファイル（VMD）をMayaのアニメーションデータに変換するコンポーネントです。既にMayaシーンに存在するPMD/PMXモデルに対して、ボーンアニメーションとモーフアニメーションを適用します。

## 機能要件

### 1. ボーンアニメーション変換
- VMDのボーンフレームデータをMayaのジョイントアニメーションに変換
- 位置（translate）と回転（rotate）のキーフレームを設定
- MMDとMayaで異なるZ軸の向きを変換（両方左手系だがZ軸が逆向き）
- 補間曲線の標準精度での変換（線形補間またはベジェ近似）

### 2. モーフアニメーション変換
- VMDのモーフフレームデータをMayaのブレンドシェイプアニメーションに変換
- モーフ名のマッピング処理（日本語名からMaya互換名への変換）
- ウェイト値（0.0〜1.0）のキーフレーム設定

### 3. タイムライン設定
- VMDのフレーム番号をMayaのタイムラインに適切にマッピング
- 30fps基準での時間変換
- アニメーション範囲の自動設定

### 4. エラーハンドリング
- 存在しないボーン/モーフ名への対処
- 不正なキーフレームデータの検出と警告
- 部分的な変換成功の許可（一部失敗しても継続）

## 技術仕様

### クラス構造
```python
class VmdConverter:
    def __init__(self):
        self.bone_name_mapping = {}  # VMDボーン名 -> Mayaジョイント名
        self.morph_name_mapping = {}  # VMDモーフ名 -> Mayaブレンドシェイプターゲット名
        self.fps = 30.0
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
```

### 座標系変換
- MMD（左手系）: X右、Y上、Z手前（画面から手前に向かって正）
- Maya（左手系）: X右、Y上、Z奥（画面の奥に向かって正）
- 変換規則: `maya_z = -mmd_z`（Z軸の向きが逆のため反転）

### 補間曲線の処理
1. VMDの補間パラメータ（4点ベジェ）を取得
2. 標準精度での近似カーブを生成
3. Mayaのアニメーションカーブタイプにマッピング
   - 線形補間: linear
   - スムーズ補間: spline

### 名前マッピング戦略
1. カスタム属性から元の名前を取得（pmx_bone_name, pmd_bone_name）
2. Unicode正規化とサニタイズ処理
3. 完全一致 → 部分一致 → 類似度マッチングの順で探索

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
```

### 制限事項
- IKボーンのアニメーションは未サポート
- カメラ・ライトアニメーションは未サポート
- 物理演算の影響は考慮されない
- 補間曲線は完全な再現ではなく近似値

### トラブルシューティング
- ボーンが見つからない場合: ボーン名マッピングの確認
- アニメーションがずれる場合: FPS設定の確認
- 回転が反転する場合: 座標系変換の確認
