# Control Rig から VMD への read-only 直接書き出し計画

## 目的と成功条件

`EDIT / CONTROL_OWNED` の MMD Control Rig を MMD Rig へ一時 Bake せず、
シーンを変更しないまま Bake Timeline VMD を書き出す。

Control は「どのボーントラックを出すか」を決める authoring source として使う。
VMD の姿勢値は Control transform から作らず、Control に一対一で binding された
実在 MMD ボーンのVMD authoring poseを毎フレーム取得する。通常joint channelまたは
ボーンモーフ前のbase channelであり、UUID-backed `authoredPlugs`から解決する。

完了条件:

- fallback ではない専用 Control の所有channelに1個以上の実keyがある実在ボーンだけを出力する。
- Control key はトラック選択と dense frame range の根拠にだけ使用し、値は対応ボーンのボーンモーフ／solver二重適用前のauthoring poseから取得する。
- 準標準ボーン欠損時はトラックを出さず、別 role／bone へ fallback しない。
- 書き出し前後で Control curve payload、接続、metadata、`EDIT / CONTROL_OWNED`、current time、scene revision が不変である。
- small fixture と YYB＋愛言葉IVを Maya 2024 GUIで書き出し、fresh Import後の姿勢・IK state・Morphが規定誤差内で一致する。
- track selection report に selected／omitted reason、Control source key数、出力key数を残す。

## 非目標

- Control local transformを直接VMD値として使うこと。
- MMD Rigへの一時／永続Bake、Control curve clone、snapshot復元、Undoによる復元。
- keyのないControl、表示専用Control、fallback alias、存在しない準標準ボーンの出力。
- final skin jointのworld poseを全ボーンへBakeすること。選択済み対応骨のlocal poseだけを読む。
- Sparse／Preserve Keysの再設計、近似key reduction、solver許容値の変更。
- Control Rig builder、manual Bake、VMD importer、native writer ABIの変更。

## 前提と制約

- 起点は `f8796287`。従来のtemporary Bake実験差分は別worktreeに保全し、このPRへ持ち込まない。
- `bindings[role]` の `jointUuid`、`authoredPlugRefs` と `controls[role]` のUUIDをauthorityとする。名前推測はしない。
- `binding.fallback is not None` は `model_root` を含め常に出力対象外とする。
- 同一Controlを複数jointがclaim、同一jointを複数の非fallback roleがclaim、UUIDが欠落／曖昧、mapped XYZが部分的な場合はfail-closedにする。
- track selectorはmapped Control transform channelの上流animCurve／Animation Layerを調べ、1個以上の実keyがある場合だけeligibleとする。接続済み0-key curveとstatic changed valueは出力しない。キーがexport range外だけにあっても、そのcurveがrange内評価へ影響するためeligibleとする。
- selector対象channelはchannel policyの `keyable_channels + passthrough_channels` とする。fixed-twistの非keyable passthrough X/Y上の既存curveを見落とさない。`ikEnabled`はbone track selectorと分離する。
- Control channelのwriter graphは許可済みdirect animCurve／unitConversion／pairBlend／Animation Layer routeのいずれかで、単一かつ非曖昧でなければならない。未知writerをkeyless omitへ丸めない。
- 各MMD `authoredPlug` の現在writerが担当Control直結、またはmetadata-owned converter chainを経由して担当Controlへ到達することを検証する。unknown／multiple／stale writerはfail-closedにする。ボーンモーフ対象はfinal joint値を使うとMorph sectionとの二重適用になるため、base authored channelを使う。
- binding jointはtarget model配下のjointで、非空のMMD bone name metadataを持つことを必須とする。joint leaf-name fallbackは禁止し、selected VMD bone nameの重複はfail-closedにする。
- bindingが所有しないtranslate／rotate familyは、対応joint側に未知のwriterがなくbind/default値である場合だけ既存joint channelを使用する。それ以外はfinal solver／Append出力を拾わずfail-closedにする。
- 非ASCII実asset pathはUTF-8 JSONからMayaへ渡し、argvへ直接渡さない。
- Maya GUI probeは使い捨て`MAYA_APP_DIR`、専用commandPort、checkout限定plugin allowlistを使用する。未信頼plugin警告には応答せず停止する。

## 検討した選択肢

### A. MMD Rigへ一時Bakeして復元

却下。weighted tangent、quaternion interpolation、solverの1ulp感度まで復元責務が広がり、scene不変契約に対して過剰だった。

### B. Control transformをVMDへ直接変換

却下。ControlのZERO／AIM／authoring basisとMMDボーンのVMD姿勢は同一ではない。Controlはselector、対応ボーンのauthored poseをvalue authorityとする。

### C. 全jointのfinal poseをdense Bake

却下。キーのないIK／Append／D／表示用ボーンまで出力し、fresh Import時の再評価と二重適用する危険がある。

### D. Control keyで選択し、対応MMDボーンのauthoring poseをread-only sampling

採用。既存binding、native sampler、joint→VMD変換、stream writerを再利用でき、scene mutationと復元処理を持たない。

## 採用アプローチ

### Read-only direct export plan

`EDIT / CONTROL_OWNED` を検出したら、collector直前に次を構築する。

- `selected_joints`: fallbackでなく、専用Controlの所有channelに1個以上の実keyがあるjoint。
- `selector_plugs_by_joint`: Control側のUUID検証済み所有plug。collectorが既存key traversalでkey時刻を得る。
- `selector_key_times_by_joint`: collectorがControl側から得たsource key時刻。track selectionとdense planning専用。
- `value_routes`: jointのlogical translate/rotate channelから、UUID解決した同一MMD boneのauthoring channelへのroute。値sampling専用。
- `ik_state_routes`: IK controller bone nameから `control.ikEnabled` へのroute。bone selectorには混ぜない。

coreのread-only resolverは `selector_plugs`、`value_routes`、ownership evidenceまでを返し、key graph traversalはconverter層の既存helperへ残す。coreからconverterをimportしたり、key traversalを複製したりしない。

`collect_bone_frames()` は、現在一つのrouteから兼用している「key時刻」と
「値取得先」を分離する。`selector_key_times_by_joint` がtrack eligibilityを決め、
`value_routes`を既存native samplerへ渡す。

値取得後の処理は既存経路を維持する。

1. `NativeVmdBatchSampler.sample_dense_bone_channels`
2. bind translation差分とjoint rotationのVMD変換
3. exact run reduction
4. `VmdStreamWriter`へのsection streaming

### Lifecycle

`MayaVmdPrepareBackend.can_prepare_for_collection()` は既存preflight capability契約のため維持する。
`prepare_for_collection()` は `EDIT / CONTROL_OWNED` でもBakeせず `None` を返す。
これによりtemporary lifecycleを開始せず、通常のrevision-before／after検査を使う。

## フェーズ分割

### Phase 0 — 計画とread-only probe

対象:

- 本計画書
- 最初は `build/reports/control-rig-direct-vmd/` 配下の一時script

作業:

- binding／Control／joint／key source／writer ownership censusを取得する。
- fallback、missing、display-only、keylessを理由付きで除外する。
- selected jointのauthoring channelを0・中間・終端frameで読み、VMD frameを一時生成する。
- production collectorはまだ変更しない。

完了条件:

- small fixtureで期待するbone nameだけが選ばれる。
- Control値ではなく対応MMD boneのauthoring poseを読んでいることをsentinelで証明する。ボーンモーフfixtureではfinal joint値の二重適用も検出する。
- probe前後のcurve、接続、metadata、owner/state、current timeが完全一致する。
- probeで再利用価値が確認できたrunnerだけをPhase 3で `tools/control_rig_direct_vmd_export_probe.py` として恒久化する。

### Phase 1 — Selector／route resolver

対象候補:

- `mmd_tools/core/mmd_control_rig_motion.py`
- `tests/unit/test_control_rig_direct_vmd_export.py`

作業:

- UUID／writer ownership検証済みのplain mapping resolverを1個追加する。
- fallbackなし・keyあり・一対一bindingだけを返す。
- Control selector key timesとMMD value routesを別フィールドにする。
- resolverはselector plugsまでを返し、direct animCurve、unitConversion、pairBlend、Animation Layerのkey censusはcollectorの既存helperで扱う。

完了条件:

- keyed dedicated bindingを選択する。
- keyless、fallback、missingをomitする。
- duplicate claim、stale UUID、partial XYZ、unknown writerをfail-closedにする。
- fixed-twist passthrough curveを見落とさない。
- keyed Controlが0本でもerrorにせず、Morph-only／IK-property-only／空motionの他sectionを継続する。unsupported／ambiguous routeだけはerrorにする。

検証:

```powershell
rtk pytest -q tests/unit/test_control_rig_direct_vmd_export.py
```

### Phase 2 — Collector統合

対象候補:

- `mmd_tools/converters/vmd_scene_collector.py`
- `mmd_tools/adapters/maya_vmd_prepare_backend.py`
- 既存の対応unit tests

作業:

- `collect_to_sink()`でCONTROL_OWNED direct planを選択する。
- jointsを`selected_joints`へ限定する。
- `_scene_authored_input_routes()`の通常Append／IK／physics mergeをdirect planへ重ねない。
- `collect_bone_frames()`へselector key times overrideを追加する。
- IK property sectionはControlの`ikEnabled` routeをread-only samplingする。
- IK solver UUID、担当Control、`control.ikEnabled`、VMD IK bone nameの一対一所有権を検証する。同名／重複claim／欠落はfail-closedにする。
- IK propertyは既存semanticsどおりsource key／transition frames＋baselineを使用し、dense化しない。
- temporary Bake／restore lifecycleを通らない。
- `can_prepare_for_collection`／`prepare_for_collection` のtemporary bake前提docstringとunit testをread-only capabilityの意味へ更新する。

完了条件:

- writer、prepared action、native ABIを変更せずstreaming VMDを生成する。
- track namesがselected bindingの実在bone nameと完全一致する。
- scene revisionがcollection前後で一致する。
- unsupported／ambiguous routeを部分成功にしない。

検証:

```powershell
rtk pytest -q tests/unit/test_control_rig_direct_vmd_export.py tests/unit/test_vmd_scene_collector.py tests/unit/test_maya_vmd_prepare_backend.py tests/unit/test_vmd_stream_writer.py tests/unit/test_native_vmd_batch_sampler.py
rtk ruff check <changed files only>
```

### Phase 3 — small Maya gate

fixture:

- `tests/data/yw_test_model_control_rig_bone_morph.pmx`
- `tests/data/yw_test_model_control_rig_bone_morph.vmd`

検証内容:

- frames 0..20をdirect exportしてfresh sceneへImportする。
- selected authored binding pose、全indexed joint world、IK state、Morph witnessを比較する。
- fallback／missing trackが出力されないことを確認する。
- before／afterのControl curve key・tangent、metadata JSON、connection census、owner/state、current time、node censusを比較する。

完了条件:

- authored binding local/VMD入力parityはstrict gateを満たす。
- solver-owned／non-solver world oracleを別集計する。direct exportのinput差とsolver数値感度を混同せず、既存gate値を変更しない。
- scene mutationと一時node残留がゼロ。

### Phase 4 — 実アセットgate

fixture:

- `F:/MMD/pmx/YYB Hatsune Miku_10th/YYB Hatsune Miku_10th_v1.02.pmx`
- 愛言葉IV YYBモーション（UTF-8 JSON config経由）

作業:

- まず短range、次に受入rangeで同じoracleを実行する。
- track数、dense key数、VMD size、sampling時間をreportする。
- 未信頼plugin警告がないことを確認する。

完了条件:

- fresh Import parityとscene不変性の両方がgreen。
- small専用の名前／roleハードコードがない。

### Phase 5 — Review／PR

- focused tests、relevant full gate、Maya 2024 evidenceを揃える。
- 不要な抽象化、fallback、重複test、既存Bake lifecycle残骸を独立reviewする。
- 目的単位でcommitし、PR本文に非目標と実機証跡を記載する。
- push／PR作成は実装とgate完了後に行う。mergeは行わない。

最終gate:

```powershell
uvx nox -s tests
uvx nox -s tests -- --type integration
python tools/control_rig_direct_vmd_export_probe.py <small UTF-8 config>
python tools/control_rig_direct_vmd_export_probe.py <YYB UTF-8 config>
```

## リスクと対策

- **Control key censusとvalue routeの混同**: APIとデータ名を分け、unitでControl値とauthored poseを意図的に異ならせる。
- **fallback aliasの混入**: `binding.fallback is not None`を最初に除外し、duplicate claimはdedupeせずerrorにする。
- **IK／Append final outputの混入**: Control keyで選んだ一対一bindingのauthoring channelだけを読む。通常のscene route mergeやkeyless related boneを追加しない。
- **Animation Layer keyの見落とし**: Control channelのupstream animCurve censusを既存helperで行う。
- **unknown writerのsilent omit**: selector/valueの両graphでownershipを先に検証し、未知routeをkeyless扱いしない。
- **MMD name fallback**: direct routeではMMD bone name metadataを必須とし、joint leaf名をVMD nameへ使わない。
- **keyless boneの混入**: collectorへ全jointを渡さず`selected_joints`だけを渡す。
- **currentTime／revision drift**: native samplerの復帰契約とprepare actionの通常revision gateを実機で検証する。
- **巨大差分への再膨張**: writer、native ABI、importer、manual Bakeを変更しない。各phaseでdiff sizeと削除可能コードをreviewする。

## 前提が崩れたことを検知する方法

以下のいずれかが出た時点でproduction実装を止め、planを見直す。

- dedicated keyed Controlとbinding jointが一対一でない。
- 対応boneのauthoring poseをsamplingしてもfresh Importの対応bone poseを再構成できない。
- Control keyなしboneを出力しなければworld parityを満たせない。
- value取得のためscene connection／metadata mutationが必要になる。
- direct routeのためnative sampler／writer ABI変更が必要になる。

この場合もtemporary MMD Bake方式へ自動的に戻さず、失敗routeをreportしてfail-closedにする。

## 未解決の問い

- 実YYBでAnimation Layerを使用しているControlがあるか。Phase 0 censusで確定する。
- bindingが所有しないlogical familyをbind/defaultとして扱える範囲。small probeでunknown writerを列挙し、許可表を最小化する。
