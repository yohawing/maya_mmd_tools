# mmd-anim ランタイム導入の進捗まとめ

このドキュメントは、maya_mmd_tools プロジェクトに mmd-anim (https://github.com/yohawing/mmd-anim) をランタイムとして導入する作業の現状をまとめたものです。

## 背景と目的

maya_mmd_tools では現在、VMD モーションのインポート時に独自の変換処理を行っていますが、以下の課題があります。

- MMD 特有のベジェ補間が正しく再現されない
- 付与変形 (append transform) と IK の組み合わせ評価が不正確
- 複雑なモーションで本家 MMD との挙動差が発生しやすい

mmd-anim は Rust で実装された高精度な MMD アニメーション評価エンジンで、C ABI と Python バインディングを提供しています。これを導入することで、以下の 2 つの利用形態を目指します。

- 高精度ベイク: インポート時に runtime で全フレームを評価し、正確なポーズを Maya キーフレームとして焼き込む
- ライブ評価: Maya シーン内で runtime を直接呼び出し、時間変化に追従してリアルタイムに姿勢を更新する

## 全体計画の概要

作業は以下のフェーズで進めています（各フェーズは明示的な承認を得て進行）。

### 基盤整備
- mmd-anim のサブモジュール追加
- Python 側の FFI ラッパー作成（ctypes によるモデル・クリップ・インスタンス管理）
- native バイナリ配置レイアウトの整備
- C++ プラグインの最小スケルトン作成
- 開発者向けドキュメントと基本テストの追加

### 高精度ベイクパス
- 設定 `import.animation.use_mmd_runtime_bake` の追加
- VmdConverter への runtime 評価分岐の実装
- ボーンインデックスマッピングの強化と world matrix の正確な適用
- PMX/PMD インポート時にソースファイルパスをモデルルートに保存
- VMD インポート時の生バイト渡しと自動 PMX ソース解決
- テストの拡充

### ライブ評価ノード基盤
- C++ 側の FFI ブリッジ実装（RuntimeBridge）
- Maya カスタムノード (mmdRuntimeInstance) の作成
- Python 側からのノード作成・操作ヘルパーの追加
- VMD インポート時のオプションによるライブノード自動アタッチ

## 実施内容の詳細

### 基盤整備で追加・変更した主なファイル
- `.gitmodules` と `external/mmd-anim/`（サブモジュール）
- `mmd_tools/core/native/mmd_anim_runtime.py`（Python FFI ラッパー）
- `mmd_tools/core/native/__init__.py`
- `mmd_tools/native/README.md` および win64 配置用ファイル
- `docs-dev/runtime-architecture.md`（設計ドキュメント）
- `tests/unit/test_mmd_anim_runtime.py`
- `cpp/src/` 以下の CMakeLists.txt、pluginMain.cpp、README.md（C++ スケルトン）

### 高精度ベイクパスで追加・変更した主なファイル
- `mmd_tools/config/default_settings.json`（use_mmd_runtime_bake 設定追加）
- `mmd_tools/converters/vmd_converter.py`（runtime ベイクロジック、フレーム範囲修正、行列適用、ボーンインデックス対応）
- `mmd_tools/io/vmd_importer.py`（生バイト読み込み、PMX ソース解決、ライブオプション対応）
- `mmd_tools/io/pmx_importer.py` / `pmd_importer.py`（ソースファイルパスの保存）
- `tests/unit/test_vmd_converter.py`（runtime インフラテスト追加）

主な機能ポイント:
- runtime が利用可能な場合、VMD バイトと PMX データから MmdRuntimeModel / Clip / Instance を作成
- 全フレームで evaluate し、world_matrices を Maya ジョイントに worldSpace で適用
- Z 軸反転などの座標系変換を考慮
- native がない環境では従来のレガシーパスに自動フォールバック

### ライブ評価ノード基盤で追加・変更した主なファイル
- `cpp/src/CMakeLists.txt`（更新）
- `cpp/src/mmdRuntimeBridge.h` / `.cpp`（C++ FFI ブリッジ、ファイルロード対応）
- `cpp/src/mmdRuntimeNode.h` / `.cpp`（Maya カスタムノード、compute での評価と出力）
- `cpp/src/pluginMain.cpp`（ノード登録）
- `mmd_tools/core/native/mmd_anim_runtime.py`（create_runtime_node_for_model、get_runtime_matrices_from_node 追加）
- `mmd_tools/io/vmd_importer.py`（use_live_runtime オプションによる自動ノード作成）

主な機能ポイント:
- C++ ノードが time 入力を受け、内部で mmd-anim を評価
- worldMatrices / morphWeights / ikEnabled などの出力属性を提供
- Python から簡単にノードを作成・操作可能
- モデルルートと message で関連付け

### GoldenOracle runtime 数値比較

`F:\Develop\MMDDev\GoldenOracle\manifests\motion-numeric.json` を使い、MMD 実機ダンプと mmd-anim runtime の数値比較を行っています。MMD 実機ダンプは `frame + 1` の表示更新後に取得されるため、runtime 側の比較には `--sample-frame-offset 1` を使います。`+1` から外すとモーフ差が再発するため、現在の比較基準は `offset=1` です。

比較用 CLI として `mmd_tools/tools/runtime_oracle_dumper.py` を追加しました。主な用途は以下です。

- GoldenOracle manifest から PMX/VMD/frames を読み、runtime 評価結果を JSONL に出力
- `--compare-oracle --focus-only` で focus ボーン/モーフだけを比較
- `--sample-frame-offset` で MMD ダンプの観測タイミングに合わせる
- `--ik-tolerance` / `--ik-max-iterations-cap` で IK 診断用の評価条件を切り替える
- offset や IK option を出力ファイル名に含め、比較結果の上書きを避ける

PMM 生成側では、`F:\Develop\MMDDev\MMDDumper\src\pmm-document-vmd-patch.mjs` で VMD の 64 byte ボーン補間を PMM の 16 byte 補間へ反映する修正を行いました。この修正後に PMX ケースの Oracle を再記録し、古い PMM 補間由来の大きな差分を減らしています。

最新の `offset=1` focus 比較結果は以下です。

| case | max bone delta | max morph delta | worst |
| --- | ---: | ---: | --- |
| `tda-miku-melancholy-night` | `0.049162259` | `4.9e-10` | `左足首@1200` |
| `sour-miku-rabbithole` | `0.164652821` | `2.98e-7` | `右ひざ@4800` |
| `yyb-miku-10th-hibana` | `0.159853459` | `3.33e-10` | `右手捩@3600` |
| `rem-proseka-miku-vs-marine-mirage` | `0.000019551` | `4.73e-10` | `左親指０@1200` |
| `rem-proseka-miku-vs-weekender-girl` | `0.243796568` | `0` | `左袖@6420` |

確認済みの切り分け:

- `offset=1` が最小差分になる。時刻ズレではない。
- 残差ボーンは local translation ではなく local rotation が主因。
- `--ik-tolerance 0` でも `sour` / `weekender` の主残差はほぼ変わらない。
- 固定軸を全面無効化すると `yyb` は改善するが `weekender` が悪化するため、恒久化しない。
- 単軸 IK 専用経路の無効化、IK link 逆順、IK step limit 無効化、Euler 分解順変更はいずれも決定的な改善にならない。

このため、現時点で残っている差分は MMD 実機の IK / 固定軸 / 角度制限の細部再現に寄っていると見ています。小さな数値差を隠すために tolerance を広げるのではなく、次に進める場合は `sour` の膝 IK、`yyb` の固定軸付き手捩、`weekender` の角度制限付き袖 IK を個別に再現する必要があります。

## 現在の状態

- Phase 0（基盤）と Phase 1（高精度ベイク）の主要機能は動作する状態
- Phase 2（ライブノード）の C++ スケルトンと Python 連携は実装済みで、基本的な評価フローが通る
- Codex review で指摘された主要バグ（行列扱い、フレーム範囲計算、CMake パス、テスト依存）はすべて修正済み
- ユニットテストは概ねパス（Maya 環境依存の部分を除く）
- 事前ビルドの mmd_anim_ffi.dll が必要（開発時は `external/mmd-anim/target/release/`、配布時は `native/win64` などに配置）
- `external/mmd-anim` は submodule なので、DLL を得るには `cargo build -p mmd-anim-ffi --release` が必要

## 残課題と次のステップ

- C++ ノードのデータ受け渡し強化（バイト列直接対応）
- 出力行列を実際のジョイント駆動に接続するロジック
- UI への設定露出（インポートオプションや専用タブ）
- より実践的な統合テストとサンプルデータでの検証
- 物理非対応などの mmd-anim 制限事項のドキュメント化
- ビルド・配布手順の整備（CMake 改善、CI 対応）
- `mayapy` による `is_mmd_runtime_available()` / ABI バージョン確認を CLI 検査手順に組み込む

今後のフェーズでは、ライブノードをより実用的にし、既存のインポートワークフローとのシームレスな統合を目指します。

## 参考

- 計画書: セッション内 plan.md（初回作成時）
- 設計: docs-dev/runtime-architecture.md
- 関連コード: mmd_tools/core/native/、cpp/src/、vmd_converter.py など
- mmd-anim 本体: https://github.com/yohawing/mmd-anim

この資料は作業の進捗を把握するためのものです。必要に応じて更新してください。
