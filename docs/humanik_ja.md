# HumanIK (Experimental)

> **試験的機能です。** HumanIK対応は現在Experimental（実験的）な機能として提供しています。
> UIやAPIは予告なく変更される可能性があります。問題が発生した場合は [GitHub Issues](https://github.com/yohawing/maya_mmd_tools/issues) で報告してください。

## この機能について

HumanIK (Experimental) は、Maya標準のHumanIK機能を使って、**MMDモデル同士**でモーションをリターゲット（再ターゲット）するための機能です。
片方のMMDモデルにインポート済みのVMDモーションを、別のMMDモデルへHumanIK経由で転写できます。

- **対応**: MMDモデル → MMDモデル のリターゲットのみ。
- **未対応**: モーションキャプチャデータなど、MMDモデルではない外部キャラクターとのリターゲットは未対応です。Source（リターゲット元）に指定できるのはシーン内の他のMMDモデルのみです。

## 開き方

`MMD` メニュー > `HumanIK (Experimental)` サブメニュー > `HumanIK Editor...` を選択します。

HumanIK Editorは、MMD Editor（`MMD > MMD Editor`）とは別の**独立したdockableウィンドウ**として開きます。MMD Editorのタブではないため、MMD Editorを閉じてもHumanIK Editorはそのまま操作できます。ウィンドウは既に開いていれば再度メニューを選ぶだけで前面に表示（フォーカス）されます。

`HumanIK (Experimental)` サブメニューには、HumanIK Editorを開く項目のほかに、Editor内の各アクション（Setup / Characterize、Enter Source Mode、Enter Target Mode、Create Control Rig、Bake to MMD Rig、Restore MMD Rig、Diagnostics）に対応するメニュー項目もあります。通常はHumanIK Editorのボタン操作で十分ですが、Mayaの選択（アクティブなMMDモデルのルート、またはその配下のジョイント）に対して直接コマンドを実行したい場合はメニューから呼び出すこともできます。

## 画面構成

HumanIK Editorの上部には、太字で以下の注意書きが常時表示されます。

> HumanIK対応は試験的機能です。予告なく変更される場合があります。

### Character（キャラクター）コンボ

このウィンドウが現在操作対象としているMMDモデルです。以下の優先順位で自動的に選択されます。

1. **Mayaの選択に追従**: MMDモデルのルート、またはその配下のジョイントを選択すると、そのモデルが自動的にCharacterとして選ばれます。
2. **手動ピック**: Characterコンボを直接操作してモデルを選ぶと、その選択がMayaのシーン選択が変わるまで優先されます。
3. **1体自動採用**: シーン内にMMDモデルが1体しかない場合は、選択が何もなくてもそのモデルが自動的にCharacterになります。
4. 上記のいずれにも該当しない場合は、直前に表示していたモデル（sticky）が維持されます。

### Source（ソース）コンボ

リターゲット元を指定するコンボです。項目は「None」と、シーン内の他のMMDモデル（Character以外）です。

- **モデルを選択する = リターゲット接続のトリガー**です。選択した瞬間に、Source用モデルとCharacter（Target）用モデルの接続処理が自動的に始まります。
- **「None」を選択する = 切断（Restore）**です。アクティブなリターゲットとControl Rigを終了し、MMDリグの状態を復元します。
- Sourceコンボには次のツールチップが表示されます: 「リターゲットのSourceに指定できるのはMMDモデルのみです。モーキャプ等の外部キャラクターは未対応です。」

Sourceコンボの表示は、ユーザーが最後にクリックした値ではなく、常にバックエンドの実際の状態（`describe_frontend_state()` が返すSOURCEバインディング）に同期されます。接続や切断が失敗・キャンセルされた場合、コンボは実際の状態に戻ります。

### ステータス（Status）セクション

現在の状態を表示するグループです。

| 項目 | 内容 |
|---|---|
| Mode | `neutral` / `source` / `target_preview` / `control_rig` のいずれか |
| Source | 現在SOURCEとして接続されているモデル（未接続時は「None」） |
| Target | 現在TARGETプレビュー中のモデル（未接続時は「None」） |
| Control Rigs | このセッションが把握しているControl Rigの一覧（無ければ「None」） |

### アクションセクション

以下の3つの折りたたみ可能なセクションがあります（見出しをクリックすると内容を隠す／表示できます）。

1. **Control Rig** — `Create Control Rig` ボタン。
2. **Bake** — 開始フレーム／終了フレームのSpinBoxと `Bake to MMD Rig` ボタン。
3. **Restore / Diagnostics** — `Restore MMD Rig` ボタンと `Diagnostics` ボタン。

各ボタンが無効な場合は、その理由がボタンの隣にオレンジ色のテキストで（ツールチップにも同じ文言で）表示されます。

## 接続時に自動で行われること

Sourceコンボでモデルを選ぶと、以下が自動的に順番に実行されます。

1. **auto-characterize**（自動キャラクタライズ）: Source側・Target（Character）側のいずれかがまだキャラクタライズされていない場合、既定の **Body onlyプロファイル**（指の割り当てを含まない）で自動的にキャラクタライズされます。手動で `Setup / Characterize` メニューを実行した場合は、確認ダイアログで「Body only」と「Body + fingers」を選択できますが、Sourceコンボからの自動接続では常にBody onlyが使われます。
2. **SOURCE設定**（Enter Source Mode）: Source側モデルをHumanIKのSOURCEとして設定します。
3. **TARGET preview**（Enter Target Mode）: Character側モデルをTARGETプレビュー状態にします。この直前に確認ダイアログが表示され、対象モデルのownership（どの制約ノードがミュートされ、どれが保持されるか）の概要と「Continue」の確認を求められます。確認をキャンセルすると接続は中断されます。

途中のいずれかのステップが失敗した場合（例: SOURCE/TARGETのプロファイル不一致、blockerの存在など）、その時点でエラーが表示され、Sourceコンボは実際の状態（未接続のまま等）に戻ります。

## 各アクションの詳細

### Bake to MMD Rig

TARGETプレビュー中のHumanIKリターゲット結果を、指定したフレーム範囲でMMDリグ（ジョイントのanimCurve）へベイクします。

- フレーム範囲はEditor上のSpinBox（開始／終了）で指定します。
- **タイムラインの再生範囲は変更されません** — SpinBoxの値はベイク処理にのみ渡され、MayaのplaybackOptionsを書き換えることはありません。
- 実行前に確認ダイアログが表示され、対象フレーム範囲と使用中のプロファイル（Body onlyの場合は指の割り当てが除外／保留される旨）が案内されます。

### Create Control Rig

キャラクタライズ済みのモデルに対してHumanIK Control Rigを作成します。

- **サポートされる経路は、このプラグインのメニュー／ボタン経由のみです。** Maya標準のCharacter Controls UIから直接Control Rigを作成した場合、`mmd_tools`はその変化を検知して警告バナーを表示します（詳細は後述のトラブルシューティングを参照）。
- 実行前の確認ダイアログには「An active preview must be restored first.」（アクティブなプレビューは先にRestoreする必要があります）という案内が含まれます。

### Restore MMD Rig

現在のHumanIK状態を元のMMDリグ状態へ復元します。意味論は次の通りです。

- **Control Rigを削除し、journal（このセッションが記録した変更履歴）を復元します。**
- **キャラクタライズ済みのHIKノード自体は削除されません** — つまり、Restore後はキャラクタライズされていない状態ではなく、**SOURCE状態（キャラクタライズ済みだがTARGET/Control Rigではない状態）へ戻ります**。
- Editor下部には次の説明文が常時表示されます: 「Restore = Control Rigを削除しjournalを復元します。characterize済みのHIKノードは残ります（SOURCE状態へ戻ります。未characterize状態には戻りません）。」
- **孤立したControl Rig**（このセッションが作成・追跡していないControl Rig）も、MMDモデルによって駆動されているものであれば、Restore MMD Rigの実行時に回収（削除）されます。ただしjournalが無いため、writerの接続やキャラクタライズ前のポーズは復元されません（後述のトラブルシューティングを参照）。

Source コンボで「None」を選ぶと、確認ダイアログ（「Disconnect the HumanIK retarget and restore the MMD rig? Any active Control Rig will also be closed.」）の後にこのRestoreが実行されます。

## VMD importの制限

**TARGETプレビュー中、またはControl Rigがアクティブな間は、そのモデルへのVMD importが拒否されます。**

- TARGETプレビュー中に拒否された場合の理由文言: 「このモデルは現在HumanIK Targetプレビュー中です。」
- Control Rigがアクティブな間に拒否された場合の理由文言: 「このモデルには現在有効なHumanIK Control Rigがあります。」
- Editor上部にも赤字の警告として「VMD importは現在拒否されます: (理由) — Restore MMD Rigで解除してください。」が表示されます。

この制限は **Restore MMD Rig** を実行してTARGET preview / Control Rig状態を終了すれば解除されます。NEUTRAL状態やSOURCE状態のモデル、またそもそもHumanIKに関与していないモデルへのVMD importは通常どおり可能です。

## トラブルシューティング

### 「Restoreが効かないように見える」

Restore MMD Rig実行後もモデルの姿勢や制約ノードの接続が完全には元に戻っていないように見える場合、そのControl Rigが**このセッションのjournal無しで回収された「孤立したControl Rig」**である可能性があります。

- Editor上部にオレンジ色で「このセッションが追跡していないControl Rigが見つかりました。Restore MMD Rigで回収できますが、journalが無いためwriter接続とcharacterize前のポーズは復元されません。」という警告バナーが表示されていた場合、その回収では **writerの接続（mmdCcdIkなどの制約ノードの再接続）とキャラクタライズ前のポーズは復元対象になりません**。これはjournalが存在しない場合の既知の制約です。
- 正常なjournal付きのRestore（このプラグインのメニュー／Editorから開始したリターゲット・Control Rigに対するRestore）であれば、writer接続とポーズは正しく復元されます。「効かない」と感じたら、まずその孤立警告が出ていたかどうかを確認してください。

### 孤立したControl Rig警告バナーの意味

Editor上に「Control RigがMaya標準UIで作成されました。サポート経路はMMDメニューのHumanIK (Experimental) > Create Control Rigです。Restore MMD Rigで回収できます。」という警告バナーが表示された場合、これは**Maya標準のCharacter Controls UIから直接Control Rigを作成した**ことを検知して表示されるものです。

- この警告表示自体はシーンを変更しません（監視のみで、自動的な回収や削除は行いません）。
- 表示された場合、そのControl Rigを正しく片付けるには **Restore MMD Rig** を実行してください（孤立したControl Rigの回収についての制約は前項を参照）。
- サポートされている作成経路は、常に `MMD > HumanIK (Experimental) > Create Control Rig`（またはEditorの `Create Control Rig` ボタン）です。Maya標準UIでのControl Rig作成は避けてください。

## 制限事項まとめ

- MMDモデル → MMDモデルのリターゲットのみ対応（モーションキャプチャ等の外部キャラクターは未対応）。
- TARGETプレビュー中／Control Rigアクティブ中は対象モデルへのVMD importが拒否される。
- Restoreで戻るのはSOURCE状態まで（未characterize状態へは戻らない）。
- 孤立したControl Rig（journal無し）の回収では、writer接続とキャラクタライズ前ポーズは復元されない。
- 試験的機能のため、UI・挙動は予告なく変更される可能性がある。
