# 付与ボーンとアニメーション実装の課題と解決策

## 概要

本ドキュメントは、MMDの付与ボーン（Grant Parent Bone）をMayaで実装する際に発生する、VMDアニメーションインポート時のコンストレイント削除問題について、その原因と解決策をまとめたものです。

## 問題の背景

### 付与ボーンとは

付与ボーンは、他のボーンの動きを一定の割合で受け継ぐMMD特有の機能です。

- **回転付与**: 親ボーンの回転を指定した割合で継承
- **移動付与**: 親ボーンの移動を指定した割合で継承
- **ローカル付与**: 親ボーンのローカル空間での変化を継承

### Mayaでの実装

現在の実装では、付与ボーンの動作をMayaのコンストレイント（`orientConstraint`、`pointConstraint`）で再現しています：

```python
# rig_converter.py での実装例
constraint = cmds.orientConstraint(
    parent_joint, joint, maintainOffset=True, weight=given_rate
)[0]
```

## 技術的な課題

### 問題の発生箇所

`maya_utils.create_animation_curves`関数で、アニメーションカーブを作成する前に既存の接続をすべて削除しています：

```python
# 既存のアニメーションカーブをクリア
for attr in attributes:
    connections = cmds.listConnections(
        f"{node_name}.{attr}", source=True, destination=False
    )
    if connections:
        cmds.delete(connections)  # コンストレイントも削除されてしまう
```

### 影響

1. 付与ボーンに設定されたコンストレイントが削除される
2. VMDアニメーション適用後、付与ボーンが正しく動作しない
3. `test_given_bones_with_pmx`テストが失敗する

## 解決策の検討

### 1. アニメーションレイヤーアプローチ（推奨）

Mayaのアニメーションレイヤー機能を使用して、コンストレイントとアニメーションを共存させる方法。

**メリット:**
- コンストレイントを保持したままアニメーションを追加可能
- 非破壊的な編集が可能
- レイヤーウェイトでブレンドが可能
- Mayaの標準機能を活用

**実装概要:**
```python
# アニメーションレイヤーの作成
animation_layer = cmds.animLayer("VMD_Animation")

# レイヤーにアニメーションを追加
cmds.animLayer(animation_layer, edit=True, addSelectedObjects=True)

# アニメーションカーブの作成（レイヤー上）
# BaseAnimationのコンストレイントは保持される
```

### 2. 選択的削除アプローチ

接続されているノードのタイプを判別し、アニメーションカーブのみを削除する方法。

**実装例:**
```python
for attr in attributes:
    connections = cmds.listConnections(
        f"{node_name}.{attr}", source=True, destination=False
    )
    if connections:
        for connection in connections:
            node_type = cmds.nodeType(connection)
            # アニメーションカーブのみを削除
            if node_type.startswith('animCurve'):
                cmds.delete(connection)
            # コンストレイントは保持
```

**考慮点:**
- すべてのコンストレイントタイプを正しく識別する必要がある
- カスタムノードへの対応も必要

### 3. 付与ボーン専用処理

付与ボーンを識別し、特別な処理を行う方法。

**実装案:**
```python
# 付与ボーンかどうかを判定
if cmds.attributeQuery(ATTR_MMD_GRANT_PARENT_INDEX, node=joint, exists=True):
    # 付与ボーンの場合は特別な処理
    use_animation_layer = True
else:
    # 通常のボーンは既存の処理
    use_animation_layer = False
```

## 推奨される実装方針

### アニメーションレイヤーを使用した実装

最も柔軟で拡張性の高いアプローチとして、アニメーションレイヤーの使用を推奨します。

#### 実装の詳細

1. **VmdConverterの拡張**
   ```python
   class VmdConverter:
       def __init__(self):
           self.use_animation_layers = True  # 設定可能にする
           self.animation_layer = None
       
       def _create_animation_layer(self, layer_name="VMD_Animation"):
           """VMDアニメーション用のレイヤーを作成"""
           if cmds.animLayer(layer_name, query=True, exists=True):
               cmds.delete(layer_name)
           
           self.animation_layer = cmds.animLayer(layer_name)
           return self.animation_layer
   ```

2. **maya_utilsの修正**
   ```python
   def create_animation_curves(
       node_name, attributes, tangent_type=oma.MFnAnimCurve.kTangentLinear,
       preserve_constraints=False, animation_layer=None
   ):
       """
       Args:
           preserve_constraints (bool): コンストレイントを保持するか
           animation_layer (str): 使用するアニメーションレイヤー名
       """
       if animation_layer:
           # レイヤーモードでの実装
           cmds.animLayer(animation_layer, edit=True, addSelectedObjects=True)
           # レイヤー上でアニメーションカーブを作成
       else:
           # 既存の実装（必要に応じて選択的削除を使用）
   ```

3. **設定の追加**
   ```python
   # settings.jsonに追加
   {
       "import": {
           "vmd": {
               "use_animation_layers": true,
               "preserve_constraints": true
           }
       }
   }
   ```

#### 考慮点

1. **パフォーマンス**
   - アニメーションレイヤーの使用はわずかにオーバーヘッドがある
   - 大規模なアニメーションでは評価時間に注意

2. **互換性**
   - Maya 2020以降で推奨（古いバージョンでも動作するが制限あり）
   - エクスポート時のレイヤーの扱いに注意

3. **ユーザビリティ**
   - レイヤーの存在をユーザーに明示する
   - レイヤーウェイトの調整方法をドキュメント化

## 実装手順

1. **フェーズ1: 基本実装**
   - `VmdConverter`にアニメーションレイヤー作成機能を追加
   - `maya_utils.create_animation_curves`に`animation_layer`パラメータを追加

2. **フェーズ2: 付与ボーン対応**
   - 付与ボーンの判定ロジックを実装
   - 付与ボーンがある場合は自動的にレイヤーを使用

3. **フェーズ3: UI統合**
   - インポートオプションにレイヤー使用の設定を追加
   - レイヤー管理のUIを提供

## テスト計画

1. **単体テスト**
   - アニメーションレイヤー作成のテスト
   - コンストレイント保持の確認テスト

2. **統合テスト**
   - `test_given_bones_with_pmx`の修正と確認
   - 各種付与ボーンパターンでのテスト

3. **パフォーマンステスト**
   - 大規模なVMDファイルでの動作確認
   - レイヤー評価時間の測定

## 今後の課題

1. **物理演算との統合**
   - 物理演算と付与ボーンの組み合わせ
   - 評価順序の最適化

2. **エクスポート対応**
   - アニメーションレイヤーからのVMDエクスポート
   - コンストレイントのベイク処理

3. **ユーザー教育**
   - アニメーションレイヤーの使い方ドキュメント
   - トラブルシューティングガイド

## 参考資料

- [Maya Animation Layers Documentation](https://help.autodesk.com/view/MAYAUL/2024/ENU/?guid=GUID-B865F8D9-AE8E-47F7-AFB4-DFF9D6A17DB4)
- MMD付与ボーン仕様書（PMXフォーマット仕様）
- 関連コード: `mmd_tools/converters/rig_converter.py`, `mmd_tools/core/maya_utils.py`