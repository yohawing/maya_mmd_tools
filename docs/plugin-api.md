# 外部プラグインから既存サービスを使う

既存の`mmd_tools.services.scene_model_service.SceneModelService`を外部プラグインから利用します。モデルの内部属性名やadapterの構築を知らずに、モデル一覧と骨の基準姿勢を取得できます。

これらのサービスはMaya内のメインスレッドで使います。MMD Toolsでモデルをインポートしてから呼び出します。

## モデルを選んで骨を取得する

次の例はモデル一覧を表示し、モデルが1つだけの場合に骨情報を取得します。複数モデルがある場合は、呼び出し側のUIで選んだrootを渡します。

```python
from mmd_tools.services.scene_model_service import SceneModelService

scene = SceneModelService()
models = scene.list_mmd_models()
print(models)

if len(models) == 1:
    skeleton = scene.get_model_skeleton(models[0])
    for bone in skeleton["bones"]:
        print(bone["index"], bone["name"], bone["joint"])
```

モデル・骨情報の読み取りはシーンの選択、現在時刻、キー、接続を変更しません。返される値は呼び出し時点のスナップショットで、JSONに変換できます。モデルや骨をリネーム・削除した場合は取得し直します。

## `list_mmd_models()`

MMDモデルrootのDAG full pathを名前順の`list[str]`で返します。モデルがなければ空のlistです。名前空間はpathに含まれます。

## 選択中のモデルを使う

候補一覧を用意せず、選択されたモデルやその子nodeからrootを取得できます。

```python
root = scene.resolve_model_from_selection()
if root is not None:
    skeleton = scene.get_model_skeleton(root)
```

引数を省略するか`None`を渡すと、シーン内のMMDモデルを候補にします。未選択、候補に含まれない選択、複数モデルにまたがる選択は`None`を返します。

`resolve_model_from_selection(available_models)`に一覧を渡すと、その候補だけから解決します。空の一覧は「候補なし」で、自動列挙しません。UIの候補や取得済み一覧を維持したい場合に使います。

## `get_model_skeleton(model_root)`

`model_root`には`list_mmd_models()`で取得したrootを渡します。一意に解決できる名前も受け付けますが、存在しない名前、曖昧な短い名前、MMDモデルroot以外は拒否します。

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

## シーンのモーションをVMDへ出力する

`ExportWorkflowService`は、内部のactionやadapterを用意せずに使えます。標準の出力処理が既定で設定されます。次の例では、出力先を実在するフォルダー内のパスに置き換えます。

```python
from mmd_tools.services import ExportWorkflowRequest, ExportWorkflowService
from mmd_tools.services.scene_model_service import SceneModelService

root = SceneModelService().resolve_model_from_selection()
if root is None:
    raise ValueError("出力するMMDモデルを1つ選択してください")

request = ExportWorkflowRequest(
    file_path=r"C:\work\motion.vmd",
    options={"current_model_root": root, "start_frame": 0, "end_frame": 120},
)
result = ExportWorkflowService().execute(request)
print(result.succeeded, result.state, result.report, result.error)
```

拡張子からVMD出力を判定し、指定範囲をBake Timelineで評価します。成功は`result.succeeded`で確認します。検証エラーや未承認の警告がある場合は出力を止め、`result.report`に理由を返します。独自のVMD actionが必要な呼び出し元は、従来どおりコンストラクターの`vmd_action`で指定できます。

このページの呼び出し方はv0.9.0向けに整備中です。0.xではAPIが変更される場合があります。
