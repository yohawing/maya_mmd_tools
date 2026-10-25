# 外部プラグインからモデルと骨を取得する

`mmd_tools.api`は、外部プラグインからMMDモデルを扱うPythonの入口です。モデルの内部属性名やadapterの構築を知らずに、モデル一覧と骨の基準姿勢を取得できます。

このAPIはMaya内のメインスレッドで使います。MMD Toolsでモデルをインポートしてから呼び出します。現在の公開範囲は読み取り操作です。

## モデルを選んで骨を取得する

次の例はモデル一覧を表示し、モデルが1つだけの場合に骨情報を取得します。複数モデルがある場合は、呼び出し側のUIで選んだrootを渡します。

```python
from mmd_tools import api

models = api.list_models()
print(models)

if len(models) == 1:
    skeleton = api.get_model_skeleton(models[0])
    for bone in skeleton["bones"]:
        print(bone["index"], bone["name"], bone["joint"])
```

APIはシーンの選択、現在時刻、キー、接続を変更しません。返される値は呼び出し時点のスナップショットで、JSONに変換できます。モデルや骨をリネーム・削除した場合は取得し直します。

## `list_models()`

MMDモデルrootのDAG full pathを名前順の`tuple[str, ...]`で返します。モデルがなければ空のtupleです。名前空間はpathに含まれます。

## `get_model_skeleton(model_root)`

`model_root`には`list_models()`で取得したrootを渡します。一意に解決できる名前も受け付けますが、存在しない名前、曖昧な短い名前、MMDモデルroot以外は拒否します。

戻り値は次の辞書です。

| キー | 値 |
|---|---|
| `model_root` | 正規化されたモデルrootのDAG full path |
| `bones` | PMXの骨index順に並んだ骨の辞書のlist |

各骨の辞書には次の値が入ります。

| キー | 値 |
|---|---|
| `index` | モデル内のPMX骨index |
| `name` / `name_english` | PMXの日英名。Mayaのnode名とは別 |
| `parent_index` | PMXの親骨index。親がなければ`-1` |
| `joint` | Maya jointのDAG full path |
| `pmx_rest_position` | PMX空間の絶対基準位置、3要素 |
| `bind_world_matrix` | Mayaの保存済みbind world行列、行優先の16要素。並進成分はcm |

`parent_index`はPMXの親関係で、補助nodeを含むMaya DAGの親とは異なります。名前は重複し得るため、モデル内では`index`、Mayaでの操作対象には`joint`を使います。

`pmx_rest_position`と`bind_world_matrix`は異なる座標空間です。前者はPMX空間の位置、後者はMayaの行列規約によるworld行列です。アニメーション中の姿勢を基準姿勢として返すことはありません。保存bindがない補助骨では、インポート時のrest metadataから復元します。

## 取得できない場合

無効なモデル指定や不正な骨metadataは`ValueError`系、bind姿勢の欠落・不一致は`RuntimeError`系の例外になります。壊れた骨を黙って省略したり、現在姿勢で代用したりしません。呼び出し側は例外メッセージを表示し、モデルの再読込やmetadataの修正を案内できます。

骨の取得は、返されたjointへ直接接続してよいという保証ではありません。既存のIK・付与・モーフ・物理との駆動切替、ベイク、解除は別の操作契約として整備します。

この入口はv0.9.0向けの開発中APIです。0.xではAPIが変更される場合があります。
