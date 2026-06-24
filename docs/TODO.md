# TODO

このファイルはローカル作業用の TODO メモです。終わったタスクはコンパクトにして `- [x]` を付け、
残タスクがレビュワーに一目で分かるよう上に寄せます。コミット対象にしないため `.git/info/exclude` で ignore。

タグ凡例: `[impl]` 実装 / `[verify]` 検証・確認 / `[decide]` 設計判断 / `[investigate]` 調査

---

## 🔲 残タスク（アクティブ）

### TestModel IK VMD: Bake/Rig live IK parity

- [x] `[test]` **focused 赤ゲート追加** — `tests/viewport/ik_motion_bake_rig_probe.py` を追加。`tests/data/mmt_test_model.pmx` + `tests/data/mmt_test_model_ik_test_motion.vmd` frame 0 で Bake/Rig の IK 関連 joint world transform と skinning matrix を比較。初回は `left_knee` position `10.073402`、rotation `179.184980deg`、skinning matrix `23.826671` で FAIL。report: `build/reports/ik_motion_bake_rig_probe_red.md`
- [x] `[impl]` **mmdCcdIk 入力の JO 混入を除去** — mini-chain は PMX/MMD rest 空間で解くため、`inputRotate` に Maya `jointOrient` を pose として混ぜない。`inputTranslate` も PMX rest 直接差分ではなく Maya REST translate からの translate offset として渡す
- [x] `[impl]` **inactive IK link の JO 補償を復元** — live Rig VMD 経路でも runtime 分解を使い、VMD で移動していない IK ノードの link は final rotate を `mmdCcdIk.inputRotate` に入れて pass-through。これで `left_ankle.rotate` は Bake と一致。report: `build/reports/ik_motion_bake_rig_probe_after_inactive_ik_passthrough.md`
- [x] `[impl]` **active mmdCcdIk link の最小 VMD parity 修正** — active link には `mmdCcdIk.outputRotateOffset[]` を追加し、runtime final と live solve の差分を offset key として保持。`mmt_test_model_ik_test_motion.vmd` frame 0 は Bake/Rig とも max position `0.000001`、rotation `0.000007deg`、skinning matrix `0.000001` で PASS。report: `build/reports/ik_motion_bake_rig_probe_final.md`
- [x] `[impl]` **通常 mmt_motion の複数フレーム parity 修正** — `mmt_test_model_test_motion.vmd` は `左足IK親` / `右足IK親` が動き、IK bone 自身の local translate は変わらない。`mmdCcdIk` は `goalWorldMatrix` 接続時でも controller branch の translate delta を見て solver/pass-through を切り替えるよう修正。active IK link は `enabled` と VMD pre-IK `inputRotate` を入れた後に `outputRotateOffset` を key し、inactive toe IK link は runtime final rotate の pass-through を VMD pre-IK 入力で上書きしない。frame 10 probe は max position/rotation/skinning `0.0` で PASS。report: `build/reports/ik_motion_bake_rig_probe_mmt_motion_frame10_active_input_scope.md`
- [x] `[verify]` **完了条件** — `ik_motion_bake_rig_probe.py` は最小 IK VMD / 通常 `mmt_test_model_test_motion.vmd` frame 10 とも PASS。`ik_bend_direction_probe.py` PASS、`test_bake_rig_parity` 9 tests OK、`test_vmd_converter` 55 tests OK、`test_rig_converter` 22 tests OK、`ruff check` OK。Maya2026 mayapy で検証済み

### TestModel + [A]ddiction 実機崩れ再現

- [x] `[investigate]` **指定再現ケース確認** — `tests/data/mmt_test_model.pmx` + `F:\MMD\vmd\124_[A]ddiction_モーション\[A]ddiction_モーション\[A]ddiction_Tda式.vmd`。mayapy argv では日本語パスが文字化けしたため `build/local_assets/addiction_tda.vmd` に同一内容コピーして検証
- [x] `[test]` **Rig REST raw mesh gate 追加** — `pmx_rest_mesh_compare.py --setup-rig --setup-bone-orientation` で skinning 前 PMX 生頂点 `(x,y,-z)` を正解に比較。修正前 Rig REST は max `2.595401`, mean `0.368238` で FAIL
- [x] `[impl]` **Rig REST 崩壊修正** — `mmdCcdIk.enabled` を PMX import 直後は default `False` に変更。脚 IK が REST からひざ制限を満たすために曲げていたのが原因。VMD import 時は `ik_show_hide_frames` またはモデルモーション default に従って enabled を keyframe
- [x] `[verify]` **Rig REST raw mesh PASS** — `mmt_test_model.pmx` Rig+JO REST が PMX 生頂点と max/mean/p95 `0.0` で一致。capture: `build/captures/mmt_rest_rig_ik_keyed_fix_bright.0000.png`
- [x] `[verify]` **PMX raw bind oracle 追加** — `mmd_anim_mesh_oracle_compare.py --bind-source pmx` で Maya `skinCluster.bindPreMatrix` に依存しない比較を追加。`mmt_test_model + [A]ddiction` frames 0/60/120/300/600 は Bake/Rig とも max `0.00098` で PASS
- [x] `[verify]` **[A]ddiction viewport capture** — frame 600 を明るい lambert/fill で再取得。capture: `build/captures/mmt_addiction_frame600_ik_keyed_fix_bright.0000.png`
- [x] `[verify]` **Codex review と oracle 再点検** — `codex review --uncommitted` で P2 2件を検出し修正。local append translation は source delta を使う回帰テスト追加、IK property frame は default ON key 後に property key で上書きする回帰テスト追加。P3 の文字化け test log は削除
- [x] `[verify]` **review 修正後ゲート再実行** — `test_vmd_converter` 45 tests OK、`test_bone_converter` 20 tests OK、`test_bake_rig_parity` 9 tests OK、`ruff check` OK、Rig REST raw mesh PASS、`mmt_test_model + [A]ddiction` PMX raw bind oracle は Bake/Rig とも PASS
- [x] `[impl]` **2回目 Codex review P2 対応** — IK state key を `target_namespace` で対象リグに限定。`mmdCcdIk.enabled=False` 時は `inputRotate` を `outputRotate` に pass-through して IK OFF 区間の FK/VMD 回転を保持。回帰テスト追加済み
- [x] `[impl]` **3回目 Codex review P2 対応** — `controllerBoneSlot` ありの新リグでは内部 `goalDecomp -> mmdCcdIk.goal` 接続を作らず pre-IK controller goal を使う。外部から `goal` が接続された場合は公開入力を優先。外部 goal 接続の回帰テスト追加済み
- [x] `[impl]` **4回目 Codex review P2 対応** — translation-only 付与移動で `sourceRotate` 接続が無い場合も `sourceTranslate` / `plusMinusAverage.input3D[0]` から source joint を収集。移動付与のみの `mmdAppend` 回帰テスト追加済み
- [x] `[verify]` **review4 修正後ゲート再実行** — `test_vmd_converter` 47 tests OK、`test_bake_rig_parity` 9 tests OK、`native_smoke` OK、`ruff check` OK、Rig REST raw mesh PASS、`mmt_test_model + [A]ddiction` PMX raw bind oracle は Bake/Rig とも PASS
- [ ] `[verify]` **GoldenOracle numeric manifest 化検討** — 現行の `mmd_anim_mesh_oracle_compare.py --bind-source pmx` は Maya bind に依存しない。追加の独立 gate として `mmd-anim verify <manifest.json> --mode numeric` 用に `TestModel + [A]ddiction` manifest/JSONL を作れるか確認する
- [x] `[verify]` **公開版 importer mesh oracle 初回確認** — `v0.2.0` (`82c084f`) を `build/worktrees/public-v0.2.0` に別 worktree 展開し、公開版コードで `tests/data/mmt_test_model.pmx` を import。REST mesh は PMX raw vertex `(x,y,-z)` と max/mean/p95 `0.0` で一致。report: `build/reports/public_v020_rest_mesh_compare.md`
- [x] `[verify]` **公開版 importer mesh oracle データ出力** — 公開版 REST mesh 頂点を `build/oracles/public_v020_mmt_test_model_rest_mesh.json` に保存し、現行 `ce96481` Rig+JO REST mesh と直接比較。max/mean/p95 `0.0` で PASS。report: `build/reports/current_rig_rest_vs_public_v020_mesh_oracle.md`
- [x] `[verify]` **Maya2027 公開版 FBX animated mesh oracle 赤ゲート追加** — ユーザー提供 `build/mmt_test_model_motion.fbx` を正解メッシュ FBX として import し、現行 `mmt_test_model.pmx + [A]ddiction_Tda式.vmd` Bake/Rig と world mesh vertices を比較する `tests/viewport/fbx_mesh_oracle_compare.py` を追加。frames 0/30/60/120/300/600 は Bake/Rig とも FAIL、overall max `16.934331`, mean `4.753479`。report: `build/reports/current_vs_maya2027_public_fbx_mesh_oracle.md`
- [x] `[investigate]` **mmd-anim oracle の限界を明文化** — `mmd-anim` は JO を考慮しない骨データ基準なので、JO 付き Maya skin の実メッシュ崩壊検出では十分な正解にならない。`mmd_anim_mesh_oracle_compare.py --bind-source pmx` が PASS しても、Maya2027 公開版 FBX mesh oracle が FAIL なら現行アニメーション変形は未修正として扱う
- [x] `[investigate]` **FBX oracle のフレーム保持確認** — `build/mmt_test_model_motion.fbx` を Maya2024 FBX import すると animCurve は 45 本 / key 270 個、key time は `0..5`、playback range も `0..5`。dense probe で frame 10 以降が同一 bbox だったのは最終キー保持。現 FBX は 0-5 の短い正解 mesh oracle として扱う。report: `build/reports/current_vs_maya2027_public_fbx_mesh_oracle_dense_probe.md`
- [x] `[verify]` **FBX oracle 有効範囲 0-5 赤確認** — `build/mmt_test_model_motion.fbx` の key 範囲に合わせて frames 0..5 で再比較。現行 Bake/Rig は同一結果で FAIL、overall max `13.071468`, mean `4.366996`。report: `build/reports/current_vs_maya2027_public_fbx_mesh_oracle_frames_0_5.md`
- [x] `[verify]` **Maya2027 で FBX oracle 再検証** — `C:\Program Files\Autodesk\Maya2027\bin\mayapy.exe` で `build/mmt_test_model_motion.fbx` を読み戻しても key range は `0..5`。同じ Maya2027 上で現行 Bake/Rig と比較しても FAIL、overall max `13.071468`, mean `4.366996`。Maya2024 FBX import 由来ではなく、現行変形が Maya2027 公開版 oracle と不一致。report: `build/reports/maya2027_current_vs_maya2027_public_fbx_mesh_oracle_frames_0_5.md`
- [x] `[investigate]` **legacy VMD 経路との差分確認** — `fbx_mesh_oracle_compare.py --disable-runtime-bake` で現行 Bake を legacy VMD converter に強制したが、FBX oracle との差は悪化（overall max `22.405742`, mean `10.832903`）。runtime bake 単独の問題ではない。report: `build/reports/current_legacy_bake_vs_maya2027_public_fbx_mesh_oracle.md`
- [x] `[investigate]` **Bake の JO 無効化仮説確認** — Bake import では `setup_bone_orientation=False` 時に JO を付けない挙動へ戻すと、Maya2027 FBX oracle との差は Bake overall mean `4.366996 -> 3.460418` に改善。ただし max `12.728723` でまだ FAIL。Rig は JO ありのため未改善。report: `build/reports/maya2027_after_bake_no_jo_vs_public_fbx_mesh_oracle_frames_0_5.md`
- [x] `[investigate]` **フレーム番号ズレ仮説確認** — FBX oracle frames `0..5` に対して現行 Bake frames `0..180` を全探索しても best overall mean `2.821788`。単純な 30fps/1fps 変換、offset、hold の問題ではない。report: `build/reports/maya2027_bake_no_jo_fbx_oracle_frame_search_0_180.md`
- [x] `[verify]` **FBX vertex order 依存を切り分ける** — `fbx_mesh_oracle_compare.py` に per-index 比較に加えて nearest-neighbor / symmetric point-cloud / bbox delta を追加。Maya2027 で再実行し、Bake frame 5 は symmetric mean `2.244560` / bbox center distance `3.131592`、Rig frame 5 は symmetric mean `2.683694` / bbox center distance `5.173895`。頂点順だけでなく形状自体がズレている。report: `build/reports/maya2027_after_bake_no_jo_vs_public_fbx_mesh_oracle_frames_0_5_nn.md`
- [x] `[investigate]` **JO 補正の責務再確認** — VMD/mmd-anim の回転は MMD ボーンローカル姿勢で、Maya の JO 付き joint に入れる前に `joint.rotate` 空間へ変換する必要がある。Maya2027 実測では joint の合成は `rotate * jointOrient` なので、直入力の基本式は `rotate = desired * jointOrient^-1`。既存 `_compute_all_bone_locals()` / `_convert_vmd_quat_to_joint_rotate()` の式向きは合っている
- [x] `[investigate]` **ユーザー提供 FBX oracle の限界確認** — `build/mmt_test_model_motion.fbx` は animCurve 45 本 / key range `0..5` で、腕・指など [A]ddiction VMD の多くのキーを含まない。フルモーション正解ではなく部分確認用として扱う
- [x] `[verify]` **ユーザー提供 FBX oracle 再確認: 正解優先度を下げる** — 本線復帰後に `Maya2027 mayapy` で `fbx_mesh_oracle_compare.py --mode both --frame 0..5` を再実行し、Bake/Rig とも FAIL（overall max `12.728722`, mean `3.460402`）。ただし frame 0 から FBX bbox が public direct oracle / 現行 PMX import と大きく異なるため、同一 `mmt_test_model.pmx + [A]ddiction_Tda式.vmd` のフル正解とは扱わない。report: `build/reports/recheck_user_maya2027_fbx_partial_oracle_frames_0_5.md`
- [x] `[verify]` **公開版 direct mesh oracle を作成** — `build/worktrees/public-v0.2.0` の公開版 importer で `mmt_test_model + [A]ddiction` frames `0..5` の world-space mesh vertices を `build/oracles/public_v020_mmt_test_model_addiction_mesh_frames_0_5.json` に出力。公開版は Bake/no-JO 基準の正解メッシュとして扱う
- [x] `[impl]` **JO skeleton runtime bake の bind-space 補正** — raw bone world ではなく skinning matrix が no-JO/public と一致するよう、runtime world を `B_maya * inverse(B_noJO) * W_mmd` に変換してから local 分解する
- [x] `[impl]` **Rig runtime bake の live rig 二重評価を遮断** — runtime bake は IK/付与解決済み final pose なので、VMD 適用時は `mmdAppend` / `mmdCcdIk` 出力接続を切り、joint を bind pose へ戻してから final pose を直接焼く。残り値を bind と誤認して D ボーン/目/足が崩れる問題を修正
- [x] `[verify]` **公開版 direct mesh oracle PASS** — `Maya2027 mayapy` で `public_mesh_oracle_compare.py` を実行。`mmt_test_model + [A]ddiction` frames `0..5` は Bake/Rig とも public v0.2.0 mesh oracle に PASS、overall max `0.001287`, mean `0.000040`。reports: `build/reports/after_bind_restore_bake_vs_public_v020_addiction_mesh_frames_0_5.md`, `build/reports/after_bind_restore_rig_vs_public_v020_addiction_mesh_frames_0_5.md`
- [x] `[verify]` **本線に戻して公開版 direct oracle 再確認** — CodexReview 文脈を止め、`Maya2027 mayapy` で `public_mesh_oracle_compare.py` を再実行。`mmt_test_model + [A]ddiction` frames `0..5` は Bake/Rig とも PASS、overall max `0.001287`, mean `0.000040`。reports: `build/reports/recheck_public_v020_addiction_bake_frames_0_5.md`, `build/reports/recheck_public_v020_addiction_rig_frames_0_5.md`
- [x] `[test]` **Rig mode が runtime bake 経路に逃げる赤テスト確認** — target joint に live `mmdCcdIk.outputRotate` が接続されていても `_should_use_mmd_runtime_bake()` が True を返す赤を追加し、修正後 PASS。Rig VMD import は live IK/付与を壊す直焼き経路を使わない
- [x] `[investigate]` **Rig REST 崩れ再現と修正** — `pmx_rest_mesh_compare.py --setup-rig --setup-bone-orientation` で `mmt_test_model` Rig REST が max `3.009356` FAIL。原因は `mmdAppend` が JO を REST grant として扱っていたこと。`mmdAppend` / append 逆分解で JO を grant 入力から外し、Rig REST は max/mean/p95 `0.0` PASS。reports: `build/reports/recheck_rig_rest_mesh_mmt_test_model.md`, `build/reports/after_vmd_no_jo_inverse_rig_rest_mesh_mmt_test_model.md`
- [x] `[impl]` **移動付与なし mmdAppend の translate 接続を停止** — TestModel の `mmdAppend` は全て `affectTranslation=False`。rotation-only 付与で `outputTranslate -> joint.translate` を接続しないようにし、不要な `(0,0,0)` 戻り経路を閉じた。Rig REST は引き続き PASS。report: `build/reports/after_append_translate_gate_rig_rest_mesh_mmt_test_model.md`
- [x] `[impl]` **mmdCcdIk goal 未接続を修正** — Rig import 直後の `mmdCcdIk.goalX/Y/Z` が未接続かつ 0 のままで、IK controller を動かしても solver goal に入らない状態を確認。compound 接続では UI/plug 確認で見えにくいため、`controller_joint.translateX/Y/Z -> mmdCcdIk.goalX/Y/Z` を個別接続するよう修正。`mmt_test_model` の `left/right_leg_ik`, `left/right_toe_ik` 全てで `goalX/Y/Z` source が入ることを確認。Rig REST は max/mean/p95 `0.0` PASS。report: `build/reports/after_ik_goal_axis_connect_rig_rest_mesh_mmt_test_model.md`
- [x] `[verify]` **mmdCcdIk controller move 実測 PASS** — `mmt_test_model` Rig import 後に `left_leg_ik_mmdCcdIk.enabled=True`、`left_leg_ik.translate` を `(0, 1.554188, 0.238175)` から `(1.0, 2.054188, -0.511825)` へ移動。`goalX/Y/Z` は `left_leg_ik.translateX/Y/Z` 接続、`outputRotate` / `left_knee.rotate` / `left_leg.rotate` が変化し、`left_ankle` world position delta `1.294069`。`IK_MOVE_PASS`
- [x] `[verify]` **Maya2026 でも mmdCcdIk controller move PASS** — `C:\Program Files\Autodesk\Maya2026\bin\mayapy.exe` で同じ probe を実行。`goalX/Y/Z` は `left_leg_ik.translateX/Y/Z` 接続、`left_leg_ik.translate` 移動で `outputRotate` / `left_knee.rotate` / `left_leg.rotate` が変化し、`left_ankle` world position delta `1.294069`、`IK_MOVE_PASS`
- [x] `[verify]` **Maya2026 GUI commandPort clean import でも IK move PASS** — 再起動後の `:7726` commandPort で `tests/data/mmt_test_model.pmx` を Rig+JO import。`left_leg_ik_mmdCcdIk.enabled=True` にして `left_leg_ik.translate` を `(0, 1.554188, 0.238175)` から `(1.0, 2.054188, -0.511825)` へ移動し、`outputRotate[0]` は `(2.727955, -0.845003, -1.925107) -> (26.056328, -0.845003, -1.925107)`、`outputRotate[1]` は `(0.30048, 5.375533, -173.522629) -> (-15.032779, 0.939538, -174.00016)`。`left_knee/left_leg.rotate` も同値で追従。log: `build/e2e/maya2026_gui_ik_probe_fresh_import.log`
- [x] `[test]` **IK move チェックの誤判定を修正** — 旧 probe は `enabled=True` を強制していたため「solver 単体は動く」だけを PASS としていた。`tests/viewport/gui_ik_probe.py` を修正し、通常確認では import 直後の `enabled` をそのまま見る。これにより現状の `enabled=False` は `RESULT=IK_DISABLED` として赤確認済み
- [x] `[impl]` **Rig IK を import 直後から操作可能に修正** — `mmdCcdIk` は generated rig では public `goalX/Y/Z` を接続せず、`controllerBoneSlot + inputTranslate` から pre-IK controller goal を復元する。`enabled=True` でも controller が REST 位置なら pass-through し、REST mesh を崩さない。配線完了前の stale solve を避けるため、`enabled` は全 input/output 接続後に ON
- [x] `[verify]` **Maya2026 fresh mayapy で IK interactive PASS** — `mmt_test_model` Rig+JO import 直後に `left_leg_ik_mmdCcdIk.enabled=True` / goal 未接続 / REST `outputRotate[0/1] == (0,0,0)`。`left_leg_ik.translate` を動かすと `left_knee.rotate` が `(23.325725, -0.845003, -1.925107)` へ変化。Rig REST mesh gate も PASS。report: `build/reports/after_ik_interactive_rest_mesh_mmt_test_model.md`
- [x] `[impl]` **live Rig IK の膝方向を修正** — `mmt_test_model` の PMX 膝 link 制限は MMD 空間で `X: -170deg..-0.5deg`。raw `MmdIkChain.solve()` も膝を MMD `X=-45.497deg` に解くが、現 node の MMD→Maya 変換では `left_knee.rotateX=+42.713deg` になり、実機目視で膝が逆方向に曲がっていた。足IKの手動操作は Maya `ikRPsolver` + Z+ pole target を live 操作用に使い、REST では `ikBlend=0`、controller 移動時だけ `ikBlend=1` になるよう修正。Maya2026 GUI commandPort `:7726` の fresh import probe で `left_leg_ik_ikHandle.ikBlend=1.0`、ankle/controller distance `0.000013`、knee delta Z `+2.297959`、`RESULT=IK_MOVED` を確認。report: `build/e2e/maya2026_gui_ik_probe_native_handle.log`
- [x] `[impl]` **mmdCcdIk に world goal 入力を追加** — `goalWorldMatrix` matrix attr を追加し、generated rig では controller が IK link の子孫でない場合だけ `controller.worldMatrix[0] -> goalWorldMatrix` を接続する。controller が REST 位置なら world goal 接続があっても pass-through し、REST は崩さない。TestModel left/right leg IK は `goalWorldMatrix` 接続あり、REST `outputRotate[0] == (0,0,0)`、Rig REST mesh PASS
- [x] `[verify]` **goalWorldMatrix 追加後ゲート** — `test_vmd_converter` 53 tests OK、`pmx_rest_mesh_compare.py --setup-rig --setup-bone-orientation` PASS。report: `build/reports/after_goal_world_matrix_rig_rest_mesh_mmt_test_model.md`
- [x] `[test]` **live Rig IK 膝方向 gate 追加** — `tests/viewport/ik_bend_direction_probe.py` を追加。`mmt_test_model` Rig+JO import 後に `left_leg_ik` を +Y へ動かし、足首が controller に到達し、かつ膝 world Z が正方向へ曲がることを検証。修正後 PASS。report: `build/reports/ik_bend_direction_probe_after_axis_revert.md`
- [x] `[verify]` **native live IK 後の REST gate** — `pmx_rest_mesh_compare.py --setup-rig --setup-bone-orientation` は max/mean/p95 `0.0` で PASS。native `ikHandle` は REST で `ikBlend=0` のため import 直後の mesh を崩さない。report: `build/reports/after_live_ik_axis_remap_rig_rest_mesh_mmt_test_model.md`
- [x] `[test]` **IK move gate に膝方向を追加** — `tests/viewport/ik_bend_direction_probe.py` で `left_leg_ik` を代表方向へ動かした時、足首距離と膝 world Z の bend direction を検証。Maya2026 mayapy / GUI commandPort `:7726` で PASS。reports: `build/reports/final_ik_bend_direction_probe.md`, `build/e2e/maya2026_gui_ik_probe_7726_after_runtime_fix.log`
- [x] `[impl]` **Rig VMD live 入力分解を修正** — Rig mode でも mmd-anim runtime final pose を使い、`mmdAppend.base*` へ逆分解、`mmdCcdIk.outputRotate -> joint.rotate` は `chainJson.links[].bone_slot` の `inputRotate` へ final rotation を焼いて `enabled=0` にする。Rig import 直後の手動 IK は Maya native `ikHandle` を作らず `mmdCcdIk` だけで操作する
- [x] `[impl]` **mmdCcdIk live IK 膝方向を修正** — live Maya 操作用の `mmdCcdIk` chainJson では MMD 膝 X angle limit を `[-max, -min]` に変換。TestModel left leg IK は Maya2026 GUI commandPort `:7726` で native handles `[]`、`left_knee/left_leg.rotate` が `mmdCcdIk.outputRotate` 駆動、ankle/controller distance `0.006320`、knee delta Z `+2.291328`、`RESULT=IK_MOVED`
- [x] `[verify]` **Rig VMD live 入力分解後ゲート** — `test_vmd_converter` 56 tests OK、`test_rig_converter` 22 tests OK、`test_bake_rig_parity` 9 tests OK。TestModel + `[A]ddiction_Tda式.vmd` frames `0..5` は公開版 direct mesh oracle / mmd-anim PMX no-JO bind oracle とも Rig PASS。reports: `build/reports/final_mmdccd_only_public_v020_addiction_rig_frames_0_5.md`, `build/reports/final_mmdccd_only_mmd_anim_addiction_rig_frames_0_5.md`, `build/e2e/maya2026_gui_ik_probe_7726_mmdccd_only.log`
- [x] `[impl]` **mmd-anim mesh oracle の PMX bind を no-JO 基準へ修正** — `mmd-anim` は JO なし骨評価なので、`mmd_anim_mesh_oracle_compare.py --bind-source pmx` は LOCAL_AXIS 回転を bind に入れず、PMX bone position のみで no-JO bind を作る
- [x] `[verify]` **mmd-anim mesh oracle PASS** — `mmt_test_model + [A]ddiction` frames `0..5` (+ default 30/60) は Bake/Rig とも `--bind-source pmx` で PASS、overall max `0.001277`, mean `0.000128`。reports: `build/reports/after_nojo_pmx_bind_mmd_anim_mesh_oracle_bake_addiction_frames_0_5.md`, `build/reports/after_nojo_pmx_bind_mmd_anim_mesh_oracle_rig_addiction_frames_0_5.md`
- [x] `[verify]` **mmd-anim no-JO bind oracle 再確認** — `Maya2027 mayapy` で `mmd_anim_mesh_oracle_compare.py --bind-source pmx` を再実行。`mmt_test_model + [A]ddiction` は Bake/Rig とも PASS、overall max `0.001277`, mean `0.000128`。`--frame 0..5` 指定に加えてスクリプト既定の 30/60 も含まれる。reports: `build/reports/recheck_mmd_anim_nojo_bind_addiction_bake_frames_0_5.md`, `build/reports/recheck_mmd_anim_nojo_bind_addiction_rig_frames_0_5.md`
- [x] `[verify]` **ローカルアセット + FBX roundtrip PASS** — `local_asset_motion_compare.py` を Maya2027 で実行。`Alicia_solid + wg_motion` と `aria + wg_motion` は Bake vs Rig mesh max `0.0`、Rig→FBX→import mesh max は Alicia `0.000914` / aria `0.000179` で PASS。report: `build/reports/after_bind_restore_local_asset_motion_compare.md`
- [ ] `[test]` **JO あり mmdAppend / mmdCcdIk 入力空間を赤テスト化** — 直入力だけでなく、`mmdAppend.baseRotate/sourceRotate/outputRotate` と `mmdCcdIk.inputRotate/outputRotate` が JO 補正済みの `joint.rotate` 空間で閉じているか、非可換 JO + 回転で検証する。疑うべき点は append の final 分解式と node 合成順
- [x] `[test]` **live Rig IK 膝方向 gate の不足修正** — `ik_bend_direction_probe.py` は足首到達と knee world Z だけを見ていたため、TestModel 左膝が逆ヒンジ方向へ折れるケースを見逃した。world Z は診断値に降格し、`left_knee.rotateX > 0` + 足首到達を合格条件に変更。現行の X 制限反転は赤、MMD 元制限では PASS。Maya2026 GUI `:7726` fresh import でも native handles `[]`、`left_knee.rotateX=42.712714`、ankle/controller distance `0.010553`、`RESULT=IK_MOVED`。reports: `build/reports/ik_bend_direction_probe_knee_first_final.md`, `build/e2e/maya2026_gui_ik_probe_7726_hinge_final_reload.log`
- [ ] `[impl]` **Rig VMD import を live rig 経路へ戻しつつ parity を戻す** — live rig 接続時に runtime bake を選ばない赤テストは確認・修正済み。ただし legacy VMD 経路だけでは `test_bake_rig_parity` が FAIL する。以前の runtime final pose + `mmdCcdIk.enabled=0` は parity を作るが操作上 Bake 化するため不可。次は runtime final を通常 joint / mmdAppend へ使いつつ、IK は VMD pre-IK 入力と `enabled` を維持する live 分解経路が必要
- [x] `[impl]` **公開版 Bake/no-JO mesh oracle に現行 Bake/Rig を合わせる** — FBX oracle ではなく公開版 direct mesh oracle を正解に採用。frame 0..5 の world-space mesh vertices で Bake/Rig とも一致済み

### ランタイムノード本番化: Bake/Rig パリティ修正

- [x] `[investigate]` Parity E2E テスト作成・初回実行 → Bake 150 rot / 390 pos outliers 検出
- [x] `[investigate]` Codex コード分析 → 3 つの ROOT CAUSE 特定
- [x] `[impl]` **Bake: runtime bake 再有効化** — `_should_use_mmd_runtime_bake()` の early return False 削除 (3行)
- [x] `[impl]` **Bake: vmd_importer target_model 解決修正** — selection から target_model を設定し mmd_source_file ルックアップが動くように
- [x] `[verify]` **Bake: E2E パリティ検証** — runtime bake 有効化確認 (150→20→0 outliers)
- [x] `[impl]` **Rig IK: JO 除去スキップ** — mmdCcdIk 出力で JO^-1 補正を除去し Bake と同じ表現に統一
- [x] `[verify]` **Rig 付与: mmdAppend 確認** — JO 処理なし、パリティ差なし (修正不要)
- [x] `[verify]` **最終パリティ検証** — Bake vs Rig: 回転 0 / 位置 0 → PARITY PASS
- [x] `[verify]` **IK リーチテスト** — aria.pmx 28/28 PASS (max_dist=0.0086)
- [x] `[verify]` **ユニットテスト** — 764 tests, 0 failures (22 errors = 既存 shader_override)
- [x] `[investigate]` **JO 二重適用バグ発見** — Bake/Rig は joint.rotate 値で合意していたが setup_bone_orientation=True では両方が JO を二重適用していた
- [x] `[impl]` **JO 分離修正** — `_compute_all_bone_locals` で q_total*q_jo.inverse()、mmdCcdIk の JO^-1 復元。E2E をワールド空間 quat 比較に変更
- [x] `[verify]` **修正後パリティ** — 本番設定 (Bake:JO=0, Rig:JO≠0) でワールド回転 0 / 位置 0 → PARITY PASS (089114f)

### メッシュ変形バグ: Rig モード JO 起因

- [x] `[investigate]` **3-way 頂点比較** — Bake vs Rig(JO=off) vs Rig(JO=on)。JO=off ならリグ含め max 0.03 units 一致。JO が全頂点ズレ (mean 2.2, max 8.1) の 99.9% を占める
- [x] `[investigate]` **bind pose デルタ比較** — 77/81 outlier ボーンが JO≠0。mmdAppend/mmdCcdIk は無実
- [x] `[investigate]` **`_set_bone_local_axis()` バグ特定** — PMX ワールド空間軸をそのまま jointOrient に設定。parenting 後に `parent_rot * pmx_rot` (二重回転) になる。ボーン世界座標はアニメ補正で正しいが bind pose orientation がずれて skinCluster デルタが狂う
- [x] `[impl]` **JO parent-local 修正** — 方針(B)採用。parenting 後に Python で parent_rot⁻¹ × pmx_rot を計算し JO 設定。DG キャッシュ不使用。setup_bone_orientation フラグ無視で両モード統一。skinCluster をリグ接続前に作成して bind pose R=0 保証。6テスト全 PASS (bone rot/pos + vertex REST/anim × 2)

### mmd-anim oracle / Bake / Rig 三者一致

- [x] `[verify]` **REST raw mesh oracle 追加** — `pmx_rest_mesh_compare.py` で `mmt_test_model.pmx` の Maya REST mesh と PMX 生頂点 `(x, y, -z)` を source vertex index で比較。max/mean/p95 `0.0`、REST 生メッシュは崩れていない
- [x] `[verify]` **REST 目視参照を正面カメラで再取得** — PMX raw front: `build/captures/mmt_test_model_pmx_raw_front.png`、Maya import front: `build/captures/mmt_test_model_rest_front.0000.png`。斜めカメラでは腰/脚の重なりが強く、正面では raw PMX と import が同じ人型シルエット
- [x] `[verify]` **REST LOCAL_AXIS/joint 監査** — 腰/足/ひざ/足首/D 系の joint world position は PMX と誤差 `0.0`。LOCAL_AXIS ボーンの world X/Y/Z dot は全て `1.0`
- [x] `[impl]` **absolute mesh oracle の JO 漏れ修正** — mmd-anim worldMatrix から頂点 oracle を作る際、`vertex - bone.position` の逆移動だけではなく `vertex * inverse(bindWorldMatrix)` を使うよう修正。JO を無視した偽陽性 max `8.146714` を解消
- [x] `[verify]` **mmd-anim / Bake / Rig absolute mesh oracle** — `mmt_test_model.pmx + mmt_test_model_test_motion.vmd` frames 0/30/60。Bake/Rig とも JO-aware bind inverse 後に max `0.000002`、mean `0.000001` で PASS
- [x] `[verify]` **mmd-anim CLI oracle smoke** — `mmd-anim inspect/import/rig/verify --help` を確認し、PMX/VMD 検証・正解データ生成の入口を `AGENTS.md` に追記。Maya 比較は rotate 単体ではなく world matrix / world translate / JO-aware bind inverse mesh oracle を使う
- [x] `[verify]` **default Bake vs mmd-anim oracle** — `test_runtime_bake_matches_mmd_anim_world_transforms` PASS。bone world rotate / world translate を PMX bone index で比較
- [x] `[verify]` **Rig vs mmd-anim oracle** — `test_rig_mode_matches_mmd_anim_world_transforms` PASS。Rig mode も同じ oracle と一致
- [x] `[verify]` **Bake vs Rig parity file** — `test_bake_rig_parity` 9 tests OK。convert 直呼び経路も保存済み source から runtime bake に戻り oracle PASS
- [x] `[impl]` **unit test cleanup** — `test_vmd_converter` legacy route tests の mock を `_collect_ik_link_joints()` の dict contract に更新。41 tests OK (skipped=1)
- [x] `[impl]` **複数 fixture oracle gate の Rig 赤修正** — Bake/Rig とも 5 ケースで oracle PASS。原因は runtime final translate を `mmdAppend.baseTranslate` に流して付与移動を二重適用していたこと。`affectTranslation=True` の付与だけ final translate から grant 寄与を除去して base にキーするよう修正
- [x] `[verify]` **mesh vertex gate 拡張** — mesh 付き fixture は `mmt_test_model` と `test_1bone_cube`。root 以下の全 mesh 頂点を連結して Bake/Rig parity を検証するよう拡張し、`TestBakeRigVertexParity` PASS
- [x] `[impl]` **convert 直呼び Bake+JO 不一致修正** — `VmdData.source_file` と model root の `mmd_source_file` から runtime bake 入力を復元。`VmdConverter.convert(vmd_bytes=None, pmx_path=None)` でも通常インポート済みシーンなら mmd-anim runtime に戻り、world position oracle PASS
- [x] `[impl]` **実アセット optional gate 追加** — `MAYA_MMD_TOOLS_REAL_PMX` / `MAYA_MMD_TOOLS_REAL_VMD` 指定時に実 PMX/VMD で Bake/Rig/mmd-anim oracle と mesh vertex parity を検証
- [x] `[verify]` **実モデル+実モーション検証** — `F:\MMD\pmx\【女主角_荧】_by_原神\Lumine.pmx` + `F:\MMD\ref\ラビットホール.vmd`、frames 0/30/60/90。実アセット gate 3 tests OK、mmd-anim frame 60 checksum `62dfc770`
- [x] `[verify]` **実モデル animated viewport capture** — `Alicia_solid.pmx` + `wg_motion.vmd` frame 60 を `static_render_capture.py --motion` で capture。PNG nonblank (`max=178`, `800x800`) かつ目視で mesh 爆発なし。出力: `build/captures/alicia_weekender_frame60.0000.png`
- [x] `[impl]` **IK controller pre-solve goal 修正** — Alicia の `三つ編みIK` で controller bone が IK link の子にいるため、`controller.worldMatrix` 接続だと solver 後に goal 自体が動いていた。`controllerBoneSlot` を `mmdCcdIk` に渡し、input pose から pre-IK controller world 位置をノード内で再構成して native solver goal に使うよう修正
- [x] `[verify]` **ローカル実アセット absolute mesh oracle** — `mmd_anim_mesh_oracle_compare.py` で mmd-anim world matrix + Maya skinCluster bindPreMatrix を使った JO-aware mesh oracle と比較。`Alicia_solid + wg_motion` frames 0/30/60/120: Bake/Rig とも max `0.000018`, mean `0.000001`。`aria + wg_motion`: Bake/Rig とも max `0.000004`, mean `0.000001`
- [x] `[verify]` **ローカル実アセット相対 mesh/FBX 比較** — `local_asset_motion_compare.py` は skinCluster 付き可視 mesh だけを比較対象に限定。`Alicia_solid + wg_motion` frames 0/60/120: Bake/Rig mesh max `0.000002`; Rig→FBX→import mesh max `0.003017`。`aria + wg_motion`: Bake/Rig max `0.000001`; FBX max `0.000179`。report: `build/reports/local_asset_motion_compare_filtered.md`
- [x] `[verify]` **広い回帰テスト** — unit 788 tests OK (skipped=18)、integration 109 tests OK (skipped=24)。PMD/Lumine fixture 不在・VP2 shader override・pytest 依存・UNC resolve の既存赤を整理済み

### UI 整理: アニメーションタブ昇格・Bake モード公開

- [ ] `[impl]` **Bake モードチェックボックスを通常表示に昇格** — 現在 Developer mode 限定の Bake/Rig 切替を、通常モードでもアクセスできるようにする。Bake をデフォルト ON の optional チェックボックスとして公開
- [ ] `[impl]` **アニメーションタブの子タブ廃止・フラット化** — モデル＞アニメーションタブの中身を子タブで隠さず全て表に出す。Bake チェックボックス、既存アニメーションクリア、VMD インポート設定などを一画面にフラットに配置

### ログ・メッセージ品質改善

- [ ] `[impl]` **ログメッセージの英語統一** — logger 出力に日本語が混在している。ユーザー向け UI テキスト以外のログ（debug/info/warning/error）はすべて英語にする。国際環境でのログ解析・issue 報告を容易にするため
- [ ] `[impl]` **エラーメッセージ・デバッグログの見直し** — 不要な冗長ログの削除、エラー時の情報不足（変数値・コンテキスト未出力）の補完、ログレベルの適正化（info で出すべきでないものを debug へ降格など）

### 残判断

- [ ] `[decide]` **develop ブランチを push してよいか**（前回の CI fix コミット + 今回のハードニングコミット）
- [x] `[verify]` **FBX ベイク時の IK 評価ズレ再確認** — skinCluster mesh 限定の filtered 比較では Alicia / aria とも Rig→FBX→import mesh が threshold 1.0 内で PASS。以前の大きなズレは morph target mesh 混入と IK controller post-solve goal が主因

---

## 🌙 外部環境ブロッカー（解消待ち）

- [ ] `[verify]` Track 1: Maya GUI / DX11 Viewport 2.0 で Outline の before/after PNG + diag 取得
- [ ] `[verify]` Maya GUI / DX11 で実モデル+実モーションの手動再生または commandPort capture（mayapy offscreen animated capture は済み）
- [ ] `[investigate]` FBX import 時に MMD custom string attr の文字化け `setAttr ... Unterminated string` 警告が出る。mesh 比較は PASS だが、FBX metadata roundtrip を扱うなら別途修正する
- [ ] `[investigate]` Track 2 glslShader: Mac で調査
- [ ] `[verify]` macOS 実機 Toon capture
- [ ] `[verify]` `view/shader_override.py` の compute/draw/initialize/updateDG/terminate 等は live Maya VP2.0 必須（Maya 非依存部は unit 済）

---

## 📦 Deferred（リリース後 or 別途判断 — 機能拡張系）

- [x] `[impl]` ランタイムノード本番化（mmd-anim live runtime をデフォルト経路化、付与/IK の Python constraint 置換）
  - 2026-06-22: mmdAppend/mmdCcdIk MPxNode プロトタイプ完了。パイプライン統合完了（22 append + 4 IK ノード、Lumine.pmx）。E2E 全 PASS。残: VMD モーション接続、ベイクモード等価性検証、submodule pointer 更新
- [ ] `[impl]` エクスポーター拡充（PMX/PMD export の本実装）
  - 2026-06-18: PMD dict exporter 最小スライス、ExportSceneCollector 追加済み。skinCluster/morph/IK/physics 未実装
- [ ] `[impl]` Python PMX パーサーを mmd-anim ベースに統一（純 Python バイナリパーサー廃止）
- [ ] `[impl]` 分割インポートの本実装（morph group split の製品化）
  - 2026-06-18: 逆引きマップ、dev mode UI 接続、static material 保持、実アセット fixture 追加済み

---

## 💡 バックログ（要件未確定メモ）

- [ ] `[impl]` **VMD インポート時の既存アニメーションクリア** — モデルインポートの「新規ファイル」チェックボックスと同様に、VMD インポート UI にも既存アニメーションをクリアしてからインポートするオプションを追加。animCurve / animLayer / runtime bake キーを対象モデル配下で削除してからモーション適用する
- [ ] `[impl]` **D&D インストーラ** (`drag_drop_install.py`): `.py` を Maya へ D&D → .mod 自動生成。リリース前昇格検討の価値あり
- [ ] `[investigate]` **D&D インポート**: PMX/VMD を VP へドロップ → import 起動
- [ ] `[impl]` **Rig モード簡易コントローラ** — Rig インポート後に IK/FK 操作用の簡易コントローラ（NURBSカーブ等）を自動生成。足IK・手IK・センターなど主要ボーンにコントローラを配置し、アニメーターが直感的にポーズ編集できるようにする
- [ ] `[impl]` **HumanIK 自動セットアップ**（dev mode 限定）: MMD ボーン → HumanIK スロット自動マッピング。Bake モード前提で mmdAnim ノード除去後に Definition を張る方針
- [ ] `[impl]` **File > Import 統合**: `MPxFileTranslator` で Maya 標準 import ダイアログに PMX/PMD/VMD を追加

---

## ✅ 完了（コンパクト記録）

- [x] 2026-06-22: UI ハードニング — i18n 修正（4言語）、VPD UI 除去、モーフボタン除去、bone_tab 修正、logger 修正
- [x] 2026-06-22: テスト基盤改善 — conftest.py（Maya 依存テスト自動スキップ）、Python 3.14 互換修正
- [x] 2026-06-22: VMD/VPD データフレーム型テスト 29 件追加
- [x] 2026-06-22: 言語切替クラッシュ修正 — export_settings_tab の GC で format_label 等の C++ オブジェクト破棄
- [x] 2026-06-22: i18n 追加修正 — retranslateUi 漏れ 4 件追加、「元PMXパス」→「元テクスチャパス」リネーム
- [x] 2026-06-22: Display Pane — 現状維持と決定（デッドコードだが削除せず保持）
- [x] 2026-06-22: rig primitive FFI — ctypes バインディング (rig_spec/ik_chain/append_solver) + native_rig_builder + パイプライン接続、19 テスト追加

---

## メモ
- 経緯は archive（`docs/TODO.archive-2026-06-16.md`）の対応 Track を参照。
- 調査の根拠（モジュールマップ・テスト構成）は 2026-06-16 の Explore 調査による。
- ユニットテスト現状: 660 passed, 136 skipped (Maya依存), 0 failed
