# PhysicsConverter 設計書

## 概要

PhysicsConverterは、MMD（PMD/PMX）の物理演算データをMayaのnClothシステムに変換するクラスです。主に髪の毛やスカートなどの布物理シミュレーションを実現することを目的とします。

## 機能要件

### 1. 基本変換機能
- **剛体データの解析**: PMD/PMXの剛体データを読み込み、物理プロパティを抽出
- **nClothノードの生成**: 適切なメッシュやカーブにnClothを適用
- **コリジョンオブジェクトの設定**: 身体部分などをnRigidとして設定
- **物理パラメータのマッピング**: MMDの物理値をnClothの属性に変換

### 2. 対応する物理タイプ
- **髪の物理**: nHairまたはダイナミックカーブを使用
- **スカート・マント**: nClothを使用した布シミュレーション
- **アクセサリー**: 軽量な剛体はnClothのポイントコンストレインで対応
- **胸部物理**: ソフトボディとしてnClothで実装

### 3. 制御機能
- **物理グループの管理**: コリジョングループとマスクの設定
- **ダンピング制御**: 過度な揺れを防ぐための減衰設定
- **初期状態の保存**: レストポジションの設定

## 技術仕様

### 1. クラス構造
```python
class PhysicsConverter:
    def __init__(self, settings=None):
        """設定を初期化"""
        
    def convert_pmd_physics(self, pmd_data, bone_joints):
        """PMDの物理データを変換"""
        
    def convert_pmx_physics(self, pmx_data, bone_joints):
        """PMXの物理データを変換"""
        
    def _analyze_physics_type(self, rigid_body):
        """剛体のタイプを分析（髪、布、剛体など）"""
        
    def _create_ncloth_for_hair(self, rigid_bodies, bone_joints):
        """髪用のnCloth/nHairを作成"""
        
    def _create_ncloth_for_cloth(self, rigid_bodies, bone_joints):
        """布用のnClothを作成"""
        
    def _create_collision_objects(self, rigid_bodies):
        """コリジョンオブジェクトを作成"""
        
    def _map_physics_parameters(self, mmd_params):
        """MMDパラメータをnClothパラメータに変換"""
```

### 2. パラメータマッピング

MMDの物理パラメータをnClothの属性にマッピング：

| MMD パラメータ | nCloth 属性 | 説明 |
|--------------|------------|------|
| mass | thickness | 質量に相当する厚み |
| velocity_attenuation | damp | 速度減衰 |
| rotation_attenuation | bendResistance | 回転減衰→曲げ抵抗 |
| friction | friction | 摩擦係数 |
| elasticity | bounce | 反発係数 |

### 3. 物理タイプの判定ロジック

剛体の名前、接続ボーン、形状から物理タイプを自動判定：
- 名前に"髪"、"hair"を含む → 髪物理
- 名前に"スカート"、"skirt"を含む → 布物理
- カプセル形状で連続している → 髪物理の可能性
- 大きな箱形状 → 布物理の可能性

## 段階的実装

### Phase 1: 基本実装（必須）
1. 剛体データの読み込みと分析
2. シンプルなnClothノードの作成
3. 基本的なパラメータマッピング
4. テスト用の簡単なシーンでの動作確認

### Phase 2: 髪物理の実装
1. 髪の剛体チェーンの検出
2. ダイナミックカーブの生成
3. nHairシステムの適用
4. フォリクルとの接続

### Phase 3: 布物理の実装
1. スカート・マントの検出
2. 適切なメッシュへのnCloth適用
3. コリジョンオブジェクトの自動設定
4. 頂点の固定設定

### Phase 4: 高度な機能
1. 物理グループによる選択的コリジョン
2. LODシステムの実装
3. キャッシュシステムの統合
4. パフォーマンス最適化

## テスト計画

### 単体テスト
- 剛体データの正しい読み込み
- パラメータマッピングの検証
- nClothノードの作成確認

### 統合テスト [ここまではやらなくて大丈夫です]
- 髪モデルでの物理シミュレーション
- スカートモデルでの布シミュレーション
- 複数の物理オブジェクトの相互作用

### パフォーマンステスト [ここまではやらなくて大丈夫です]
- 大量の剛体を含むモデルでの処理速度
- シミュレーション時のフレームレート
- メモリ使用量の監視

## ドキュメント

### APIドキュメント
- 各メソッドの詳細な説明
- パラメータの型と範囲
- 戻り値の説明
- 使用例

### ユーザーガイド
- 物理設定の調整方法
- トラブルシューティング
- パフォーマンスチューニング
- よくある質問（FAQ）

### 実装メモ
- nClothの制限事項
- MMDとMayaの座標系の違い
- 既知の問題と回避策
- 将来の改善案

## 設定項目

最小限の設定で動作するよう設計：

```json
{
  "physics": {
    "enable_hair_physics": true,
    "enable_cloth_physics": true,
    "simulation_quality": "medium",
    "auto_detect_type": true
  }
}
```

## 注意事項

- nClothはリアルタイムシミュレーションのため、フレームレートに依存
- 大量の物理オブジェクトはパフォーマンスに影響
- MayaのバージョンによってnClothの挙動が異なる場合がある
- 初期状態の設定が重要（レストポジション）
