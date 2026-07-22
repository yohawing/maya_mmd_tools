# HumanIK (Experimental)

> **試験的機能です。** HumanIK対応は現在Experimental（実験的）な機能として提供しています。
> UIやAPIは予告なく変更される可能性があります。問題が発生した場合は [GitHub Issues](https://github.com/yohawing/maya_mmd_tools/issues) で報告してください。

## この機能について

HumanIK (Experimental) は、Maya標準のHumanIK機能を使って、モーションをリターゲット（再ターゲット）するための機能です。
片方のMMDモデルにインポート済みのVMDモーションを、別のMMDモデルへHumanIK経由で転写できます。また、モーションキャプチャなど**MMDモデルではない外部のHumanIKキャラクター**をSource（リターゲット元）に指定することもできます。

- **対応**: MMDモデル → MMDモデル のリターゲット、および 外部HIKキャラクター（モーキャプ等） → MMDモデル のリターゲット。
- **Sourceに指定できるもの**: シーン内の他のMMDモデル、または既にcharacterize（Characterize/Lock）済みのHumanIKキャラクター（mmd_toolsの外で作成されたもの。例: モーションキャプチャのパフォーマー）。
- **外部HIKキャラクターは不可侵**: 外部キャラクターのジョイント・アニメーション・characterization自体はmmd_toolsから一切変更されません（auto-characterizeも実行されません）。TARGET側（Character）は従来通りauto-characterizeされます。
- **未対応**: 外部HIKキャラクターをTARGET（Character）に指定すること。TARGETは常にMMDモデルです。

## 開き方

`MMD` メニュー > `HumanIK (Experimental)` サブメニュー > `HumanIK Editor...` を選択します。

HumanIK Editorは、MMD Editor（`MMD > MMD Editor`）とは別の**独立したdockableウィンドウ**として開きます。MMD Editorのタブではないため、MMD Editorを閉じてもHumanIK Editorはそのまま操作できます。ウィンドウは既に開いていれば再度メニューを選ぶだけで前面に表示（フォーカス）されます。

`HumanIK (Experimental)` サブメニューには、HumanIK Editorを開く項目のほかに、Editor内の各アクション（Setup / Characterize、Enter Source Mode、Enter Target Mode、Create Control Rig、Bake to MMD Rig、Restore MMD Rig、Diagnostics）に対応するメニュー項目もあります。通常はHumanIK Editorのボタン操作で十分ですが、Mayaの選択（アクティブなMMDモデルのルート、またはその配下のジョイント）に対して直接コマンドを実行したい場合はメニューから呼び出すこともできます。

## 画面構成

HumanIK Editorの最上部右側には `Refresh` ボタンだけを表示します。Experimental表記はウィンドウタイトルに集約し、画面内には重複表示しません。`Refresh` を押すと、シーンを再スキャンしてCharacter/Sourceコンボとステータス行を最新化します。

### Character（キャラクター）コンボ

このウィンドウが現在操作対象としているMMDモデルです。以下の優先順位で自動的に選択されます。

1. **Mayaの選択に追従**: MMDモデルのルート、またはその配下のジョイントを選択すると、そのモデルが自動的にCharacterとして選ばれます。
2. **手動ピック**: Characterコンボを直接操作してモデルを選ぶと、その選択がMayaのシーン選択が変わるまで優先されます。
3. **1体自動採用**: シーン内にMMDモデルが1体しかない場合は、選択が何もなくてもそのモデルが自動的にCharacterになります。
4. 上記のいずれにも該当しない場合は、直前に表示していたモデル（sticky）が維持されます。

### Source（ソース）コンボ

リターゲット元を指定するコンボです。項目は次の3種類です。

1. 「None」
2. シーン内の他のMMDモデル（Character以外）
3. シーン内の**非MMDのHumanIKキャラクター**（`(HIK)` サフィックス付きで表示、例: 「MocapChar (HIK)」。モーキャプ等、mmd_toolsの外でcharacterizeされたキャラクター）

- **項目を選択する = リターゲット接続のトリガー**です。選択した瞬間に、Source（MMDモデルまたは外部HIKキャラクター）とCharacter（Target）用モデルの接続処理が自動的に始まります。
- **「None」を選択する = 切断（Restore）**です。アクティブなリターゲットとControl Rigを終了し、MMDリグの状態を復元します。
- Sourceコンボには次のツールチップが表示されます: 「SourceにはMMDモデル、またはシーン内でcharacterize/lock済みのHumanIKキャラクター（モーキャプ等）を指定できます。」
- 非MMDのHumanIKキャラクターは、Characterize/Lockされていなくても一覧には表示されます。未lockのキャラクターを選択すると接続はエラーになり（通常の接続失敗と同じ経路でエラー表示され）、コンボは未接続状態に戻ります。

外部HIKキャラクターを選択した場合のみ、追加のチェックが1つ入ります。**TARGET（Character）側のHumanIK割り当てジョイントに既存のanimCurve（VMDインポート済みモーション等）があるかを軽くスキャン**し、見つかった場合は次の3択ダイアログが表示されます。

- **Clear and connect**: 既存のanimCurveを削除してから接続します（削除は1つのundoチャンクにまとめられ、`Ctrl+Z` 一回で元に戻せます）。
- **Connect anyway**: 既存のアニメーションを残したまま接続します。ただしこの場合、**Bake to MMD Rigは書き込み衝突で失敗します**（既にキー付けされたチャンネルへ外部HumanIKの結果を書き込めないため）。
- **Cancel**: 接続を中止します。

MMDモデルをSourceに選ぶ場合はこのチェックは行われず、従来通りの挙動です。

Sourceコンボの表示は、ユーザーが最後にクリックした値ではなく、常にバックエンドの実際の状態（`describe_frontend_state()` が返すSOURCEバインディング）に同期されます。接続や切断が失敗・キャンセルされた場合、コンボは実際の状態に戻ります。

シーンを開き直した場合やプラグインを再読み込みした場合も、Maya標準HumanIKに残っているDirect Character Input（SOURCE/TARGET関係）を読み戻してSourceコンボへ表示します。プラグイン経由で作成したControl Rigでは、Bake/Restoreに必要なプレビュー復元情報もControl Rig transactionと一緒にシーンへ保存されるため、再読み込み後も同じBake経路を継続できます。Mayaの標準UIから作られたControl Rigや、復元情報を持たない旧シーンは安全に再構築できないため、推測でBakeを有効化しません。

### ステータス行

以前はMode/Source/Target/Control Rigsの4行テーブル（Statusグループ）でしたが、ユーザーfeedbackにより**1行のステータスラベル**に簡素化されました。Source/Targetの情報は上部のCharacter/Sourceコンボと重複するため撤去され、ステータス行には次の内容のみが表示されます。

- 現在のMode（`neutral` / `source` / `target_preview` / `control_rig` のいずれかに対応する文言）
- Control Rigが1つ以上存在する場合のみ、`/ Control Rig: N`（Nは件数）という接尾辞

例: `Control Rig / Control Rig: 1`

詳細な状態・警告文はEditor内へ展開せず、Maya Script Editorへ記録されます。

### アクションボタン

Character/Sourceは、ラベルを左、残り幅いっぱいのComboを右に置く1行構成です。操作は次の順に並び、設定項目の多いBake欄だけを見出しの矢印で折り畳めます。

1. `Create Control Rig` ボタン（全幅）
2. `▼ ベイク` 折り畳み欄
   - 開始フレーム／終了フレームのSpinBox（1行）
   - `Control Rigへベイク`／`MMD Rigへベイク`（横並び）
   - `ベイクを実行` ボタン
3. `MMDリグを復元` ボタン

詳細な診断はEditor内へ常時表示せず、MMDメニューのHumanIK診断とMaya Script Editorのログから確認します。

ボタンは常に操作可能です。実行できない状態ではバックエンドが処理を拒否し、操作名・理由・tracebackをMaya Script Editorへ出力します。Editor内に長い理由文やエラーダイアログは表示しません。

## 接続時に自動で行われること

Sourceコンボで項目を選ぶと、以下が自動的に順番に実行されます。**確認ダイアログは基本的に表示されません**（下記「ポップアップの削減」を参照）。

1. **auto-characterize**（自動キャラクタライズ）: MMDモデルがまだキャラクタライズされていない場合、既定の **Full（Body + fingers）プロファイル**で自動的にキャラクタライズされます。SourceがMMDモデルならSource側にも適用されます。**Sourceが外部HIKキャラクターの場合、このステップは実行されません**（外部キャラクターは不可侵。既にcharacterize/lock済みであることが前提です）。
   - 既に別のプロファイル（例: body-only）でcharacterize済みのモデルは再characterizeされません（既存のbindingが優先されます）。
2. **既存アニメーションのチェック**（外部Sourceの場合のみ）: 前述のSourceコンボの説明を参照。
3. **SOURCE設定**（Enter Source Mode / Enter External Source Mode）: Source側をHumanIKのSOURCEとして設定します。
4. **TARGET preview**（Enter Target Mode）: Character側モデルをTARGETプレビュー状態にします。確認ダイアログは表示されず、ownership（どの制約ノードがミュートされ、どれが保持されるか）のpreflightチェックを通過すれば即座に実行されます。結果の概要はダイアログではなく完了後の情報メッセージとして表示されます。

途中のいずれかのステップが失敗した場合（例: SOURCE/TARGETのプロファイル不一致、blockerの存在など）、詳細はMaya Script Editorへ記録され、Sourceコンボは実際の状態（未接続のまま等）に戻ります。

## ポップアップの削減（Phase B6）

以前は複数の操作で確認ダイアログが表示されていましたが、設定項目のないものは即実行に変更されました。

- **Setup / Characterize**: 「Body only / Body + fingers / Cancel」の選択ダイアログは廃止されました。常にFull（Body + fingers）プロファイルで即実行されます（既存のbindingがある場合はそのプロファイルを維持）。preflight情報はダイアログではなく実行後の情報メッセージとして表示されます。
- **Enter Target Mode**: 「Continue/Cancel」の確認ダイアログは廃止されました。ownership/blockerチェックを通過すれば即実行されます。
- **Bake to MMD Rig**: 確認ダイアログは廃止されました（設定項目がフレーム範囲のSpinBoxのみのため）。即実行されます。
- **Create Control Rig**: 確認ダイアログは廃止されました。
- **Restore MMD Rig / 切断（Sourceコンボで「None」を選択）**: **アクティブなControl Rigがある場合のみ**、確認ダイアログ（「Control Rigが削除されます。続行しますか？」）が表示されます。Control Rigが無い場合は即実行されます。
- **既存アニメーションのクリア確認**（外部Source接続時）: これは設定項目（Clear and connect / Connect anyway / Cancel）のある確認なので、Phase B6でも残されています。

## HumanIKとキー済みチャンネルの注意

HumanIKのリターゲットやベイクが対象とするジョイントのチャンネルが既にキー付け（animCurveが存在する状態）されている場合、次の点に注意してください。

- **キー済みチャンネルへの直接`setAttr`は無効です。** HumanIK/HIKの内部評価やリターゲットのプレビューはanimCurveの評価結果を上書きできません。
- **外部HIKソースからのBake to MMD Rigは、TARGET側のチャンネルが既にキー付けされていると書き込み衝突で失敗します。** これがSourceコンボで外部HIKキャラクターを選ぶ際に既存アニメーションのクリア確認が表示される理由です。接続前に「Clear and connect」で既存のanimCurveを削除するか、「Connect anyway」で残したままにする場合はBake前に手動でクリアしてください。
- MMDモデル同士のリターゲット（Source=MMDモデル）ではこのチェックは行われません（従来通りの挙動）。

## 各アクションの詳細

### Bake to MMD Rig

TARGETプレビュー中のHumanIKリターゲット結果を、指定したフレーム範囲でMMDリグへベイクします。ジョイントのanimCurveに加えて、標準の左右足IKがある場合は足IKコントローラのTranslate XYZにもキーを作成し、足IK solverを有効なまま保持します。

- フレーム範囲はEditor上のSpinBox（開始／終了）で指定します。
- **タイムラインの再生範囲は変更されません** — SpinBoxの値はベイク処理にのみ渡され、MayaのplaybackOptionsを書き換えることはありません。
- 確認ダイアログはありません（Phase B6でポップアップを削減）。ボタンを押すと即座にベイクが実行され、完了後に結果（開始／終了フレーム、書き込んだキー数など）が情報メッセージとして表示されます。
- 足IK込みのBake後も、全ジョイントのBake前後差をフレームごとに検証します。標準足IKを安全に特定できないモデルでは推測で接続を書き換えず、従来のfail-safeなジョイントBakeへ戻します。
- つま先IKは足IKとは別solverです。現時点では従来のfail-safe経路（pre-solver回転Bake）を維持し、足IKコントローラBakeの対象には含めません。

### Create Control Rig

キャラクタライズ済みのモデルに対してHumanIK Control Rigを作成します。

- **サポートされる経路は、このプラグインのメニュー／ボタン経由のみです。** Maya標準のCharacter Controls UIから直接Control Rigを作成した場合、`mmd_tools`はその変化を検知してMaya Script Editorへ警告します（詳細は後述のトラブルシューティングを参照）。
- 確認ダイアログはありません（Phase B6でポップアップを削減）。アクティブなプレビューが残っている場合は、これまで通りエラーとして拒否されます。

### MMDリグを復元

現在のHumanIK状態を元のMMDリグ状態へ復元します。意味論は次の通りです。

- **Control Rigを削除し、復元状態（このセッションが記録した変更履歴）を復元します。**
- **HumanIKのため一時的に外したMMDの足IK／つま先IK、付与、制約の接続と有効状態も復元します。**
- **キャラクタライズ済みのHIKノード自体は削除されません** — つまり、Restore後はキャラクタライズされていない状態ではなく、**SOURCE状態（キャラクタライズ済みだがTARGET/Control Rigではない状態）へ戻ります**。
- **孤立したControl Rig**（このセッションが作成・追跡していないControl Rig）も、MMDモデルによって駆動されているものであれば、Restore MMD Rigの実行時に回収（削除）されます。ただし復元状態が無いため、writerの接続やキャラクタライズ前のポーズは復元されません（後述のトラブルシューティングを参照）。

Source コンボで「None」を選ぶとこのRestoreが実行されます。確認ダイアログが表示されるのは**アクティブなControl Rigがある場合のみ**（「Disconnect the HumanIK retarget and restore the MMD rig? The active Control Rig will also be deleted.」）です。Control Rigが無い状態（TARGETプレビューのみ、または何も無い状態）からの切断は即実行されます。

## VMD importの制限

**TARGETプレビュー中、またはControl Rigがアクティブな間は、そのモデルへのVMD importが拒否されます。**

- TARGETプレビュー中に拒否された場合の理由文言: 「このモデルは現在HumanIK Targetプレビュー中です。」
- Control Rigがアクティブな間に拒否された場合の理由文言: 「このモデルには現在有効なHumanIK Control Rigがあります。」
- 拒否理由と詳細はMaya Script Editorへ出力されます。

この制限は **Restore MMD Rig** を実行してTARGET preview / Control Rig状態を終了すれば解除されます。NEUTRAL状態やSOURCE状態のモデル、またそもそもHumanIKに関与していないモデルへのVMD importは通常どおり可能です。

## トラブルシューティング

### 「Restoreが効かないように見える」

Restore MMD Rig実行後もモデルの姿勢や制約ノードの接続が完全には元に戻っていないように見える場合、そのControl Rigが**このセッションの復元状態無しで回収された「孤立したControl Rig」**である可能性があります。

- Maya Script Editorに孤立Control Rigの警告が出ていた場合、その回収では **writerの接続（mmdCcdIkなどの制約ノードの再接続）とキャラクタライズ前のポーズは復元対象になりません**。これは復元状態が存在しない場合の既知の制約です。
- 正常な復元状態付きのRestore（このプラグインのメニュー／Editorから開始したリターゲット・Control Rigに対するRestore）であれば、writer接続とポーズは正しく復元されます。

### 孤立したControl Rig警告の意味

Maya Script EditorにControl RigがMaya標準UIで作成されたという警告が出た場合、これは**Maya標準のCharacter Controls UIから直接Control Rigを作成した**ことを検知したものです。

- この警告表示自体はシーンを変更しません（監視のみで、自動的な回収や削除は行いません）。
- 表示された場合、そのControl Rigを正しく片付けるには **Restore MMD Rig** を実行してください（孤立したControl Rigの回収についての制約は前項を参照）。
- サポートされている作成経路は、常に `MMD > HumanIK (Experimental) > Create Control Rig`（またはEditorの `Create Control Rig` ボタン）です。Maya標準UIでのControl Rig作成は避けてください。

## 制限事項まとめ

- Sourceに指定できるのはMMDモデル、またはcharacterize/lock済みの外部HumanIKキャラクターのみ。TARGET（Character）は常にMMDモデル。
- 外部HIKキャラクターをSourceに使う場合、キー済みチャンネルへのBakeは書き込み衝突で失敗する（接続時の既存アニメーションクリア確認を参照）。
- キー済みチャンネルへの直接`setAttr`は無効。
- TARGETプレビュー中／Control Rigアクティブ中は対象モデルへのVMD importが拒否される。
- Restoreで戻るのはSOURCE状態まで（未characterize状態へは戻らない）。
- 孤立したControl Rig（復元状態無し）の回収では、writer接続とキャラクタライズ前ポーズは復元されない。
- Setup / Characterizeの既定プロファイルはFull（Body + fingers）。既に別プロファイルでcharacterize済みのモデルは再characterizeされない。
- 確認ダイアログはRestore/切断（アクティブなControl Rigがある場合のみ）と、外部Source接続時の既存アニメーションクリア確認のみに限定されている（Phase B6）。
- 試験的機能のため、UI・挙動は予告なく変更される可能性がある。
