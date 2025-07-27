# 設定管理

このドキュメントは、プラグインの設定を管理するための `Settings` クラスの使用方法について説明します。

## 概要

`mmd_tools/core/settings.py` にある `Settings` クラスは、MMD Tools プラグインのユーザー設定可能なすべての項目を管理する責任を負います。これにより、Mayaのセッションをまたいで設定を一元的かつ永続的に保存・アクセスする方法を提供します。

- **永続性:** 設定はMayaの `optionVar` コマンドを使用して保存されるため、Mayaを閉じて再度開いた後も保持されます。
- **シングルトンパターン:** このモジュールは `settings` という名前の `Settings` クラスの単一のグローバルアクセス可能なインスタンスを提供します。これにより、プラグインのすべての部分が同じ設定で動作することが保証されます。
- **デフォルト値:** デフォルト設定は `mmd_tools/core/default_settings.json` ファイルから読み込まれます。ユーザーによって設定が変更されていない場合は、このファイルの値が使用されます。

## 使用方法

プラグインのどこからでも設定にアクセスまたは変更するには、まずグローバルな `settings` オブジェクトをインポートします。

```python
from mmd_tools.core.settings import settings
```

### 設定の取得

設定の値は、カテゴリと設定名を指定して取得します。`Settings` クラスは辞書ライクなアクセスを提供します。

```python
# 現在のスケールファクターを取得
scale = settings.get('import.general.scale_factor')
print(f"現在のスケールファクター: {scale}")

# 物理をインポートするかどうかを確認
if settings.get('import.physics.import_physics'):
    print("物理をインポートします。")
```

### 値の設定

設定を変更するには、新しい値を代入するだけです。変更は自動的にMayaの `optionVar` に保存されます。

```python
# スケールファクターを変更（従来の方法も引き続き使用可能）
settings['import']["general"]["scale_factor"] = 2.0

# 物理のインポートを無効にする（新しい方法）
settings.get('import.physics.import_physics', False)
```

### 利用可能な設定

以下は、`default_settings.json` で定義されている設定項目です。

#### インポート設定 (`import`)

| カテゴリ      | 設定名                       | 型      | デフォルト値 | 説明                                                                 |
|---------------|------------------------------|---------|--------------|----------------------------------------------------------------------|
| **general**   | `scale_factor`               | `float` | `1.0`        | インポート時に適用する全体的なスケール。                             |
|               | `use_namespace`              | `bool`  | `false`      | インポート時にMayaの名前空間を使用するか。                           |
|               | `root_bone_name`             | `str`   | `"master"`   | 生成されるルートボーンの名前。                                       |
| **model**     | `import_models`              | `bool`  | `true`       | モデル（メッシュ、マテリアル等）をインポートするか。                 |
|               | `merge_meshes_by_material`   | `bool`  | `false`      | 同じマテリアルを持つメッシュを統合するか。                           |
|               | `create_mmd_shaders`         | `bool`  | `true`       | MMDライクなシェーダーを自動で作成・割り当てするか。                  |
|               | `texture_search_path`        | `str`   | `""`         | テクスチャファイルを追加で検索するパス。                             |
|               | `hide_hidden_geometry`       | `bool`  | `true`       | 非表示設定のジオメトリをインポート時に隠すか。                       |
| **physics**   | `import_physics`             | `bool`  | `true`       | 物理関連の要素（剛体、ジョイント）をインポートするかのマスター設定。 |
|               | `create_rigid_bodies`        | `bool`  | `true`       | 剛体を作成するか。                                                   |
|               | `create_physics_joints`      | `bool`  | `true`       | 物理ジョイント（コンストレイント）を作成するか。                     |
|               | `group_physics_objects`      | `bool`  | `true`       | 物理関連オブジェクトをグループ化するか。                             |
| **morphs**    | `import_morphs`              | `bool`  | `true`       | モーフ（ブレンドシェイプ）をインポートするかのマスター設定。         |
|               | `create_blendshape_node`     | `bool`  | `true`       | ブレンドシェイプノードを作成するか。                                 |
|               | `group_morphs_by_panel`      | `bool`  | `true`       | MMDの表示枠パネルに基づいてモーフをグループ化するか。                |
| **animation** | `import_animations`          | `bool`  | `true`       | VMDアニメーションをインポートするかのマスター設定。                  |
|               | `animation_start_frame`      | `int`   | `1`          | アニメーションの開始フレーム。                                       |
|               | `resample_curves`            | `bool`  | `false`      | アニメーションカーブをリサンプリングするか。                         |
|               | `import_camera_animation`    | `bool`  | `true`       | カメラアニメーションをインポートするか。                             |
|               | `import_light_animation`     | `bool`  | `true`       | 照明アニメーションをインポートするか。                               |
| **naming**    | `translate_names`            | `bool`  | `true`       | ノード名を日本語から英語に翻訳するか。                               |
|               | `translation_dictionary`     | `str`   | `""`         | 翻訳に使用するカスタム辞書ファイルのパス。                           |

#### エクスポート設定 (`export`)

| カテゴリ    | 設定名          | 型     | デフォルト値 | 説明                                                       |
|-------------|-----------------|--------|--------------|------------------------------------------------------------|
| **general** | `export_format` | `str`  | `"pmx"`      | デフォルトのエクスポート形式（`pmx`または`pmd`）。          |
|             | `apply_scale`   | `bool` | `true`       | エクスポート時にシーンのスケールをモデルに適用するか。       |

#### UI設定 (`ui`)

| カテゴリ    | 設定名                  | 型     | デフォルト値 | 説明                                                       |
|-------------|-------------------------|--------|--------------|------------------------------------------------------------|
| **general** | `show_advanced_options` | `bool` | `false`      | UIで詳細設定オプションを表示するか。                       |
|             | `log_level`             | `str`  | `"INFO"`     | プラグインが出力するログの詳細レベル（`DEBUG`, `INFO`, `WARNING`, `ERROR`）。 |

### デフォルトへのリセット

すべての設定を `default_settings.json` の内容に戻すには、`reset()` メソッドを使用します。

```python
# すべての設定をリセット
settings.reset()
print(f"スケールファクターは現在: {settings.import['general']['scale_factor']}")
```

これは、プラグインのUIに「デフォルトにリセット」ボタンを提供する場合に便利です。
