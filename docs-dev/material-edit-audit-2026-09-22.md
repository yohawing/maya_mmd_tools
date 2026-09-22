# 材質編集の追加実機監査（2026-09-22）

対象コードは `077717e2`。YYB Hatsune Miku 10th v1.02を統合メッシュで読み込み、q202を主に確認した。Maya 2024 Release／2026 Debugの隔離した検証プロセスを使用し、ユーザーの作業中シーンは変更していない。以下は修正前の監査記録。修正結果は末尾の「修正後の確認」を参照。

## 確認した不具合

### 1. P1: 未編集の輪郭サイズ・不透明度が書き換わる

検証用に `mmd_edge_size=3.25`、`mmd_diffuse_alpha=0.1234567` を設定してから、GUIを読み直して英語名だけ変更・適用した。

- 輪郭サイズ: 3.25 → 2.0
- 不透明度: 0.12345670163631439 → 0.11999999731779099
- 英語名以外の2属性も変わる。両版で再現。

元のq202はこの値ではない。PMX/Mayaの属性が保持できる値を使った範囲・精度テストである。

`material_tab.py` の輪郭サイズ上限2.0、透明度の小数2桁と、`MaterialPresenter._render_material_detail()`／`_material_from_authoring_controls()` が表示値を再保存する経路が原因。係数で実施した未編集値の保持が、これらには適用されていない。

証跡: `build/reports/material-audit/2026/24-range-fixture.json` と `25-name-only-range.json`、2024は `verified/08-range-fixture.json` と `09-name-only-range.json`。

### 2. P1: 失敗した変更がRedoで復活する

主テクスチャをq2.pngからq3.pngへ変更してApplyする際、`commit_material_binding_patch()` に書込み後の例外を注入した。

失敗直後には全材質属性、fileノードとパス、シェーダー接続が元に戻る。しかしRedo履歴に `MMD Material Binding Patch` が残り、GUIでCtrl+Yを押すと失敗したq3.pngへの変更が再適用される。両版で再現。

`MayaSceneMetadataBackend.rollback_write()` はchunkを閉じてundoするが、失敗した操作をRedoから再実行できる状態が残る。既存の正常なUndo履歴を維持しながら、失敗した操作を再公開しない処理が必要。

証跡: 2026の `28-before-fault-network.json`／`29-after-fault-network.json`（直後の復元）、`34-failed-before-redo.json`／`35-redo-failed-transaction.json`（Redoで復活）。2024は `verified/10-before-fault.json`〜`12-redo-failed-transaction.json`。

### 3. P2: 構造変更をUndoしても材質一覧が更新されない

- 追加→Undo: シーン31材質に対し一覧32行。両版で再現。
- 複製した材質を削除→Undo: シーン33材質に対し一覧32行（2026）。
- 並べ替え→Undo: q202のindexは27へ戻るが一覧が古い順序のままになり、選択が解除される（2026）。

`MaterialPresenter._sync_history()` は選択材質の詳細だけ読み直し、一覧のprojectionを更新しない。材質追加・削除・index変更を伴う履歴操作には一覧更新も必要。

証跡: 2026の `09-create`／`10-undo-create`、`13-delete-duplicate`／`14-undo-delete`、`16-move-up`／`17-undo-move`。2024は `verified/04-create`／`05-undo-create`。

### 4. P2: シェーダーアウトラインの変更が無視される

standardSurface材質で「シェーダーアウトライン」をONにしApplyすると、成功メッセージとON表示が出る。しかし `mmd_shader_outline_enabled` はfalseのままで、ResetするとOFFへ戻る。両版で再現。

`MaterialPresenter._viewport_outline_intent()` は `dx11Shader` 以外で `None` を返すが、UIは編集可能なまま。現行standardSurface経路へ編集意図を渡すか、非対応なら操作不可と理由を表示する必要がある。

証跡: 2026の `26-outline-applied`／`27-outline-reset`、2024の `verified/06-outline-applied`／`07-outline-reset`。

### 5. P2: 保存再オープン後の並べ替えが失敗する

保存再オープン後にq202を選択して上へ移動すると、両版で次のエラーになりindexは変わらない。Refresh後の再試行でも再現。

`move_material_fast ... [mmdRenderQueueReindex] Material queue reindex was rejected.`

2026では保存前の並べ替えは成功していた。2024では追加・削除操作より前に保存したシーンを再オープンしても確認した。再オープン時のrender queue再構築と隣接reindexの前提の調査が必要。拒否の内部原因は未確定。

証跡: 2026の `36-before-reopened-move`／`37-reopened-move`、2024の `verified/17-before-move-retry`／`18-move-retry`、`19-clean-reopen`／`20-clean-reopen-move`。

## 確認できた操作と限界

| 操作 | 結果 |
| --- | --- |
| 共有toon選択、個別toonのパス・index変更 | 両版で適用・属性読戻し成功 |
| sphere画像と乗算モードへの変更 | 両版で適用成功 |
| Diffuse／Specular／Ambient／Edge色、透明度0.25、輪郭サイズ1.25 | 両版で入力値を反映。アウトラインONは上記の不具合 |
| 描画フラグ各種 | 2026でdraw_flagsの変更を確認。各フラグのレンダー画像比較は未実施 |
| 材質追加・複製・空の複製材質の削除 | 両版で操作成功。Undo後の一覧に不具合 |
| 面割当済みq202の削除 | 2026で成功。全faceの再割当の個別照合は未実施 |
| 保存再オープン | 両版で全材質のユーザー定義属性が保存前と一致。以降の並べ替えは失敗 |
| Apply書込み後の強制失敗 | 両版で属性・fileノード・接続を直後に復元。ただしRedoで失敗変更が復活 |

この監査はUI入力と保存属性の検証であり、全シェーダーの見た目、PMX再出力、分割メッシュ、別モデルの網羅検証ではない。

## 検証方法

`tools/render_override/material_edit_audit.py` を隔離Maya内で読み込み、`install(window, output)` でヘルパーを作成した。編集はQtのクリック／キー入力で行い、ApplyやUndoのpresenterメソッドを直接呼んでいない。保存再オープンにはMayaのfileコマンドを使用した。範囲テストの初期値設定とcommit失敗注入は上記のとおり明示的な検証用操作。

画像とJSONは `build/reports/material-audit/` に保存。Qt5の色ダイアログではHTML欄が無名だったため、最初の検証コードがnull対象へ入力してMaya 2024を2回終了させた。ヘルパーの対象特定を修正後、色編集は通過している。この終了は製品不具合に含めない。

## 修正後の確認

5件を修正し、Maya 2024／2026の通常Release DLLで実GUIを再確認した。未編集の輪郭サイズ・不透明度の保持、構造変更の一覧／詳細同期、保存再オープン後の並べ替え、書込み後失敗のrollbackとRedo抑止、直前の正常なUndo履歴の保持を確認。standardSurfaceのShader Outlineは非対応を明示して操作不可にした。

追加で、Maya 2024のscriptJobによるUndo/Redo通知が届かないケースを確認し、MEventMessageと遅延UI更新へ置き換えた。ウィンドウ破棄時のcallback解除も両版で確認した。書込み前の失敗はUndoを消費せず、訂正用の未適用入力を残す。

関連unit tests 336 pass、Ruff pass、独立レビュー指摘を解消。コミット: d615e7a8、a8ff07b2、f1fc51f1、94083992、de9976d0。詳細と検証条件は `build/reports/material-repair-final/report.md`、両版の集計は `summary.json`。稼働Mayaへの最終Pythonメソッド再ロードによる検証であり、全release gate完了ではない。
