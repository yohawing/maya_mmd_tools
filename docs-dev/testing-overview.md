# テストドキュメント

## NVIDIA FLIP 画像比較 (report-only)

FLIP (Flicker-Free Image Quality Assessment) は NVIDIA が公開する画像比較ツールです。
参照 PNG (GoldenOracle レンダリング) と Maya キャプチャ PNG をピクセル単位で比較し、
知覚差分マップ・統計値 (mean / weighted median / max) を出力します。

Python 版の FLIP は dev dependency として `flip-evaluator` を使います。未導入の場合は以下で入れます。

```bash
python -m pip install -e .[dev]
```

```bash
uvx nox -s flip_report -- \
    --reference F:\\Develop\\MMDDev\\GoldenOracle\\runs\\fixture-render\\fixture-render-generated-visual-mmd-diffuse-lit-box\\frame-0.png \
    --test build/captures/static_render_1bone_cube.0000.png \
    --out-dir build/flip-reports/static-render-1bone \
    --basename static_render_1bone_cube \
    --csv build/flip-reports/static-render-1bone/results.csv
```

> **重要**: この session は **report-only** です。FLIP のスコア (mean / weighted median / max) は
> pass/fail 判定に使いません。Maya の viewport レンダラーと GoldenOracle のレンダラーは異なるため、
> 現在の差分はレンダラー差を反映したものであり、回帰指標として使う準備ができていません。
> しきい値と gating 条件は別途 `[decide]` タスクで検討します。

## 概要

本ドキュメントは、Autodesk Maya 用 MikuMikuDance (MMD) ファイルインポートプラグインのテスト戦略について記述します。プラグインの各コンポーネント（MMD ファイルパーサー、Maya データコンバーター、UI）の品質と正確性を保証することを目的とします。

## テスト実行システム

### アーキテクチャ

プロジェクトでは、すべてのテスト（ユニット/統合）をMaya環境内で実行する統一されたテストシステムを採用しています：

```
tests/
├── common/                  # テスト共通ユーティリティ
│   ├── test_base.py        # 基本テストクラス
│   ├── maya_test_base.py   # Mayaテスト基本クラス
│   └── custom_test_runner.py # カスタムテストランナー
├── unit/                    # ユニットテスト
├── integration/             # 統合テスト
├── run_tests.py            # メインエントリーポイント
└── maya_test_runner.py     # Maya環境内でのテスト実行
```

### 特徴

- **統一された実行環境**: すべてのテストがmayapy経由で実行されるため、環境差異による問題を排除
- **シンプルな構造**: run_tests.pyが引数解析とmayapy起動のみを担当し、実際のテスト実行はmaya_test_runner.pyが処理
- **柔軟なテストフィルタリング**: 特定のテストモジュール、クラス、メソッドを指定して実行可能
- **カラー対応出力**: テスト結果が色分けされて表示され、視認性が向上

### Nox タスクランナー

開発用の共通入口として `noxfile.py` を使用します。Nox は追加の仮想環境を作らず、既存の `mayapy` / CMake / Cargo ランナーを呼び出す薄いタスクランナーとして扱います。

基本コマンド:

```bash
# 既存のユニットテストを実行
uvx nox -s tests

# 既存の統合テストを実行
uvx nox -s tests -- --type integration

# mmd-anim FFI をビルド
uvx nox -s ffi_build

# Python から native runtime をロードできるか確認
uvx nox -s native_smoke

# Maya C++ プラグインをビルド
uvx nox -s cpp_build -- --maya 2024 --config Debug

# mayapy で C++ プラグインをロードし、mmdRuntimeInstance ノードを作成
uvx nox -s maya_smoke -- --maya 2024 --config Debug

# mayapy での最小限オフスクリーン viewport キャプチャ smoke（GUI 不要・プラグイン非依存）
# シンプルなポリゴンキューブシーンを作成し、playblast で PNG を出力して存在と非ゼロサイズを検証
uvx nox -s maya_viewport_capture -- --maya 2024
uvx nox -s maya_viewport_capture -- --maya 2024 --out build/captures/viewport_smoke.png --frame 1 --width 640 --height 480

# C++ スタンドアローン CLI runtime smoke (Maya GUI / mayapy 不要)
# GoldenOracle スタイルの manifest (サブセット) を読み、PMX/VMD を RuntimeBridge で評価してサニティ報告
uvx nox -s cpp_cli_smoke -- --manifest <path-to-manifest.json> [--case <name>] [--limit <n>]

# mayapy で PMX fixture を import し、GoldenOracle 風 fixed camera/light で静止画キャプチャ (report-only、画像比較 gate ではない)
# この session は MMD shader 割当を行わず、import + 固定カメラ/ライトのキャプチャ基盤を提供する
uvx nox -s maya_static_render -- --maya 2024
uvx nox -s maya_static_render -- --maya 2024 --model tests/data/for_unit_test/test_1bone_cube.pmx --out build/captures/static_render_1bone_cube.png --frame 0 --width 1024 --height 1024

# C++/native 経路をまとめて CLI だけで検証 (manifest 指定時は cpp_cli_smoke が maya_smoke 前に実行される)
uvx nox -s cpp_verify -- --maya 2024 --config Debug
uvx nox -s cpp_verify -- --maya 2024 --config Debug --manifest <path-to-manifest.json> [--case <name>]

# F:\MMD などのローカル素材から batch import manifest を生成 (manifest は build/ 以下に置き、コミットしない)
uvx nox -s maya_batch_import -- --maya 2024 --scan-root F:\MMD --write-manifest build/batch-import/manifest.json --max-models 20 --max-motions 20

# 生成済み manifest を Maya standalone で import 検証
uvx nox -s maya_batch_import -- --maya 2024 --manifest build/batch-import/manifest.json
uvx nox -s maya_batch_import -- --maya 2024 --manifest build/batch-import/manifest.json --case <case-name> --save-scenes
```

Maya の場所は `MAYA_LOCATION` または `MAYA_LOCATION_2024`、devkit の場所は `MAYA_DEVKIT_ROOT` または `MAYA_DEVKIT_ROOT_2024` で上書きできます。Windows では既定で `C:/Program Files/Autodesk/Maya2024`、macOS では `/Applications/Autodesk/maya2024/Maya.app/Contents` を探索します。

Windows の C++ ビルドは `vswhere` で Visual Studio C++ tools を自動検出し、`VsDevCmd.bat` 経由で CMake/Ninja を実行します。自動検出が合わない場合は `VSDEVCMD_PATH` または `VSWHERE_PATH` で上書きできます。

`maya_viewport_capture` は Maya の Viewport 2.0 / playblast 経路が CLI から PNG を出せるかを見る最小 smoke です。`playblast` は `viewport_smoke.0000.png` のようにフレーム番号付きファイルを出力する場合があります。この smoke は実際に生成された PNG を検出して、存在と非ゼロサイズを検証します。

この smoke は DX11Shader / `mmd_tools/shaders/MMDShader.fx` の検証へ拡張するための基盤です。まずはプラグイン非依存の cube capture を安定 gate にし、その後 Windows 専用の shader capture session を追加して、MMD material / toon / sphere map / alpha の viewport 表示を PNG と簡易 pixel check で確認します。

`maya_static_render` は GoldenOracle の `static-render.json` に相当する固定カメラ・固定ライトのキャプチャ基盤です。PMX fixture を `mmd_tools.io.mmd_importer.import_mmd_file()` から import し、FOV 25°, light direction [0.5,-1,0.5], color [1,1,1] の固定設定で 1024x1024 PNG を出力します。（`displayRGBColor` は mayapy batch mode では使用できないため、背景色変更は行いません。デフォルトのビューポート背景色が使用されます。）

この session は **report-only** であり、画像比較 (FLIP / pixel-diff) による pass/fail 判定は行いません。黒一色などの明らかな失敗を避けるため、PNG の非ブランク検査だけを行います。

### Manifest-driven Visual Regression

`maya_visual_regression` は GoldenOracle 互換の render manifest を入力に、Maya GUI / DX11 Viewport 2.0 で PMX fixture を import して PNG と diagnostics JSON を出力する report-only harness です。

manifest はローカル環境依存の素材パスを含むため、リポジトリ内へハードコードしません。必ず CLI から `--manifest` で渡してください。`--case`、`--tag`、`--limit` で対象 case を絞り込めます。

```bash
uvx nox -s maya_visual_regression -- \
  --maya 2024 \
  --manifest <path-to-GoldenOracle-render-manifest.json> \
  --case fixture-render-generated-visual-mmd-diffuse-lit-box \
  --out build/visual-regression/local

# 既に Maya GUI の commandPort を開いている場合
uvx nox -s maya_visual_regression -- \
  --manifest <path-to-GoldenOracle-render-manifest.json> \
  --case fixture-render-generated-visual-mmd-diffuse-lit-box \
  --out build/visual-regression/attach-smoke \
  --port 7721 \
  --attach-existing \
  --no-compare
```

出力は `--out/visual-regression-report.json` と、case ごとの `actual-frame-<frame>.png` / `diagnostics.json` です。`--no-compare` を付けない場合、manifest の oracle PNG が解決でき、Pillow が利用可能なら簡易 pixel diff も report に含めます。

DX11 shader は Maya プロセス内で同一 `.fx` path がキャッシュされるため、harness は実行ごとに `MMDShader.fx` を `--out/shaders/` へ内容ハッシュ付きでコピーしてから `dx11Shader.shader` に設定します。shader 変更直後の検証で古い effect が残る落とし穴を避けるためです。

`playblast` は必ず harness が設定した visible `modelPanel` を `editorPanelName` で指定して撮影します。複数 panel がある Maya GUI では、画面で texture 表示が有効でも別 panel を撮ると未割当表示のような緑 capture になることがあります。case ごとの `diagnostics.json` には capture panel、各 modelPanel の `displayTextures` / `useDefaultMaterial` / `rendererName`、mesh の `intermediateObject`、dx11Shader の `listTechniques`、中心ピクセル RGB が記録されます。

切り分け用に `--debug-lambert-control` で赤 lambert のオブジェクトレベル割当、`--hide-orig-shapes` で `*Orig` mesh shape の一時 intermediate 化を行えます。これらは scene 内の一時操作で、出力 diagnostics の `debug_actions` に記録されます。

### PMX Roundtrip

`pmx_roundtrip` は PMX の import → parse → export → re-import の一貫性を検証する manifest-driven runner です。

各 case について:
1. 元 PMX を `PmxData` でパースして構造化データを取得
2. new scene → Maya import（元 PMX）
3. `PmxData` を exporter dict に変換
4. `--out-dir/exports/<safe_case>.pmx` に export
5. 出力 PMX を再度パースしてバイナリ整合性を検証
6. new scene → Maya import（出力 PMX）

SDEF 頂点、追加 UV レイヤー、IK、ソフトボディ、カスタム表示枠など、exporter dict が対応しない PMX データは警告として記録し、pass/fail には影響しません。

```bash
# デフォルト: manifest_template.json から limit 1 で実行
uvx nox -s pmx_roundtrip -- --maya 2024

# 全 case を実行
uvx nox -s pmx_roundtrip -- --maya 2024 --manifest tests/roundtrip/manifest_template.json

# 特定 case のみ
uvx nox -s pmx_roundtrip -- --maya 2024 --case 1bone

# 出力先を指定（build 以下である必要あり）
uvx nox -s pmx_roundtrip -- --maya 2024 --out-dir build/roundtrip-custom
```

出力は `--out-dir/results.json` に保存され、`total` / `passed` / `passed_with_diffs` / `skipped_unsupported` / `failed` の集計と各 case の `warnings` / `diffs` / `error` を含みます。空 mesh や exporter dict 未対応の PMX は、元 PMX の import まで確認したうえで `skipped_unsupported` として記録します。

### Track 6 batch import

`maya_batch_import` はローカル素材を対象にした manifest-driven の batch import runner です。`--scan-root` / `--write-manifest` で PMX/PMD と VMD を探索し、manifest を `build/` 以下へ生成します。生成物には絶対パスが入るためコミット対象にしません。

`--manifest` 実行では Maya standalone を起動し、各 case を new scene から `mmd_tools.io.mmd_importer.import_mmd_file()` で model import、必要なら同じ API で VMD import します。結果は `build/batch-import/results.json` に保存され、`--case` で失敗 case を 1 件だけ再実行できます。`--save-scenes` を付けた場合のみ `build/batch-import/scenes/` に `.ma` を保存します。

`--capture` を付けると各 case の import 後に offscreen playblast で PNG を 1 枚保存し、PNG の存在・非ゼロサイズ・非ブランクを検査します。既定は 640x480、frame 0、FOV 25 度で、出力先は `build/batch-import/captures/` です。capture に失敗した case は batch import 失敗として扱います。

各 case の `audit` には、import 後の scene inspection と Python logger 由来の診断が入ります。`missing_textures` は存在しない `fileTextureName` を持つ file node、`shader_errors` は shader mode 時の dx11Shader 作成・設定エラー、`log_warnings` / `log_errors` は case 実行中の warning/error log です。summary には `missing_texture_count` / `shader_error_count` / `warning_count` / `error_log_count` が出ます。

```bash
# separate_meshes_by_material を一時的に有効化して profile 計測
# mesh transform/shape 数・material 数・skinCluster 数・blendShape 数を results.json/profile に記録
uvx nox -s maya_batch_import -- --maya 2024 --manifest build/batch-import/manifest.json --limit 1 --separate-meshes --out-dir build/batch-import/separate-meshes-audit

# 代表 case を import し、PNG capture まで確認
uvx nox -s maya_batch_import -- --maya 2024 --manifest build/batch-import/manifest.json --case <case-name> --capture

# capture サイズや frame を指定
uvx nox -s maya_batch_import -- --maya 2024 --manifest build/batch-import/manifest.json --limit 1 --capture --capture-width 640 --capture-height 480 --capture-frame 0

# dx11Shader 作成経路を audit する（case は import 失敗時のみ failed、shader diagnostics は results.json に記録）
uvx nox -s maya_batch_import -- --maya 2024 --manifest tests/track6/manifest_template.json --limit 1 --shader --out-dir build/batch-import/audit-shader-smoke
```

#### `--shader` / `--no-shader` オプション

`--no-shader`（デフォルト）では MMD shader 割当を行わず、basic lambert fallback で non-blank PNG を保証します。`--shader` を指定すると PMX importer の `create_mmd_shaders=True` 経路で指定 backend の shader node（`dx11Shader` または `GLSLShader`）を作成し、basic lambert で上書きしません。代わりに shader node 数・shader path・technique・shadingEngine 接続を stdout に診断出力します。

```bash
# デフォルト（no-shader）: lambert fallback、non-blank PNG を保証
uvx nox -s maya_static_render -- --maya 2024

# shader mode を明示有効化（auto backend: dx11Shader、不可なら glslShader）
uvx nox -s maya_static_render -- --maya 2024 --shader

# no-shader 明示指定
uvx nox -s maya_static_render -- --maya 2024 --no-shader
```

> **注意**: shader mode は動作環境（DX11 対応 GPU / Viewport 2.0 / glslShader）に依存します。デフォルト gate は常に `--no-shader` にし、`--shader` は環境が整っている場合のみ使ってください。
>
> `--shader --shader-backend auto` では、まず `cmds.loadPlugin("dx11Shader", quiet=True)` と `dx11Shader.outColor` を確認し、利用できない場合は `glslShader` / `GLSLShader.outColor` の probe にフォールバックします。`--shader-backend dx11` または `--shader-backend glsl` を明示した場合は、その backend が利用できなければ `RuntimeError` で停止します。

`--shader-backend glsl` の mayapy standalone 診断では `--diagnostics-out` と `--allow-blank` を併用できます。`--vp2-device default|gl|glcore|dx11` は mayapy サブプロセス起動前に `MAYA_VP2_DEVICE_OVERRIDE` を設定し、diagnostics JSON の `vp2` セクションに実デバイス情報を記録します。

```bash
uvx nox -s maya_static_render -- --maya 2024 --shader --shader-backend glsl --allow-blank --diagnostics-out build/captures/glsl_device_baseline.diag.json --out build/captures/static_render_1bone_cube_shader_glsl_baseline.png
uvx nox -s maya_static_render -- --maya 2024 --shader --shader-backend glsl --vp2-device glcore --allow-blank --diagnostics-out build/captures/glsl_device_glcore.diag.json --out build/captures/static_render_1bone_cube_shader_glsl_glcore.png
```

#### macOS / GLSL Toon 検証

macOS では dx11Shader を使えないため、`--shader-backend auto` は dx11Shader が利用できない場合に `glslShader` へフォールバックする必要があります。実機確認では、まず `auto` のフォールバック経路を確認し、必要なら `glsl` を明示して切り分けます。

```bash
uvx nox -s maya_static_render -- --maya 2024 --shader --shader-backend auto --diagnostics-out build/captures/macos_auto_toon.diag.json --out build/captures/macos_auto_toon.png
uvx nox -s maya_static_render -- --maya 2024 --shader --shader-backend glsl --diagnostics-out build/captures/macos_glsl_toon.diag.json --out build/captures/macos_glsl_toon.png
```

期待する artifact:
- `build/captures/macos_auto_toon.png` または `build/captures/macos_glsl_toon.png`: non-blank の Toon shader capture
- `build/captures/macos_auto_toon.diag.json` または `build/captures/macos_glsl_toon.diag.json`: `GLSLShader_count`、shader path、shadingEngine membership、VP2 device info を含む構造化 diagnostics

diagnostics JSON では、少なくとも 1 個の `GLSLShader` node、存在する shader file への `shader` path、shadingEngine 接続が確認できる必要があります。

> **重要**: `docs/TODO.md` の macOS Toon 項目は、macOS 実機で **non-blank** の Toon capture と diagnostics が得られるまで完了扱いにしません。`GLSLShader` node が作成されただけ、または all-black PNG が出ただけでは不十分です。

#### `--view-transform`, `--display`, `--rendering-space` オプション

`maya_static_render` はキャプチャ前に `cmds.colorManagementPrefs` で View Transform / Display / Rendering Space を明示設定します。
指定された値が Maya 環境で利用可能かどうかを事前に query し、存在しない場合はエラーで停止します。

```bash
# デフォルト: View Transform = Un-tone-mapped (sRGB), Display = sRGB, Rendering Space = ACEScg
uvx nox -s maya_static_render -- --maya 2024

# View Transform を ACES 1.0 SDR-video (sRGB) に変更
uvx nox -s maya_static_render -- --maya 2024 --shader --view-transform "ACES 1.0 SDR-video (sRGB)" --out build/captures/static_render_1bone_cube_shader_aces.png

# View Transform を Un-tone-mapped (sRGB) に明示指定
uvx nox -s maya_static_render -- --maya 2024 --shader --view-transform "Un-tone-mapped (sRGB)" --out build/captures/static_render_1bone_cube_shader_untone.png

# Display / Rendering Space も指定可能
uvx nox -s maya_static_render -- --maya 2024 --display sRGB --rendering-space ACEScg
```

> **注意**: 存在しない View Transform / Display / Rendering Space を指定すると RuntimeError で失敗し、利用可能な値の一覧がエラーメッセージに表示されます。

`flip_report` の結果では mean / weighted median / max をすべて記録します。将来 gate に昇格するときは weighted median を primary、max を局所破綻検出の補助、mean を傾向監視として扱います。現在の GoldenOracle 参照 PNG との比較は renderer / asset 差が大きいため threshold には使わず、同一 asset / camera / light / View Transform の Maya-Maya 回帰参照が固定できてから warning、failure の順で gate 化します。

### 基本的なテスト実行方法

#### 全てのテストを実行

```bash
# 全てのユニットテストを実行
python tests/run_tests.py --type unit

# 全ての統合テストを実行
python tests/run_tests.py --type integration
```

#### 特定のテストを実行

```bash
# 特定のテストモジュールを実行
python tests/run_tests.py --type unit --test test_pmd_parser
python tests/run_tests.py --type integration --test test_maya_utils

# 特定のテストクラスを実行
python tests/run_tests.py --type unit --test TestPmdParser

# 特定のテストメソッドを実行
python tests/run_tests.py --type unit --test test_parse_pmd_header_success
```

### 高度なオプション

#### Maya バージョンの指定

```bash
# 特定のMayaバージョンを指定（デフォルトは2024）
python tests/run_tests.py --type integration --maya 2023 --test test_maya_utils
python tests/run_tests.py --type integration --maya 2025
```


### テストの発見とデバッグ

#### 利用可能なテストの確認

存在しないテスト名を指定すると、利用可能なテストの一覧が表示されます：

```bash
python tests/run_tests.py --type integration --test nonexistent_test
```

出力例：
```
Error: No tests found matching '--test nonexistent_test' in the 'integration' suite.

Available tests in this suite:
  - test_animation_converter.TestAnimationConverter.test_convert_vmd_animation
  - test_maya_utils.TestMayaUtils.test_assign_material
  - test_maya_utils.TestMayaUtils.test_create_material
  - test_maya_utils.TestMayaUtils.test_create_mesh_with_uvs
  - test_maya_utils.TestMayaUtils.test_sanitize_maya_name
  - test_maya_utils.TestMayaUtils.test_set_custom_attributes
  - test_mesh_converter.TestMeshConverter.test_convert_pmd_mesh
  - test_mesh_converter.TestMeshConverter.test_convert_pmx_mesh
  ...
```

### エラーハンドリング

#### テストの発見に失敗した場合

```bash
# 指定したテストが見つからない場合
python tests/run_tests.py --type integration --test invalid_test

# 出力: エラーメッセージと利用可能なテストの一覧
```

#### Maya環境の問題

```bash
# mayapyが見つからない場合
Error: mayapy executable not found at C:\Program Files\Autodesk\Maya2024\bin\mayapy.exe.

# 別のMayaバージョンを試す
python tests/run_tests.py --type integration --maya 2023
```

## テストフィクスチャ

### TestFixtureProviderの使用

`TestFixtureProvider`は、テストで使用するMMDファイル（PMD、PMX、VMD）やテクスチャファイルへのアクセスを提供するクラスです。`tests/data`ディレクトリに配置されたテストファイルを自動的に探索し、キャッシュして高速にアクセスできるようにします。

#### 基本的な使用方法

```python
from tests.common.test_fixture_provider import TestFixtureProvider

class TestPmdParser(unittest.TestCase):
    def setUp(self):
        self.fixture_provider = TestFixtureProvider()
        
    def test_parse_pmd_file(self):
        """PMDファイルのパーステスト"""
        # デフォルトのPMDファイルを取得
        pmd_path = self.fixture_provider.get_pmd_file()
        
        # 特定のPMDファイルを取得（拡張子なしで指定）
        specific_pmd = self.fixture_provider.get_pmd_file('miku_v2')
        
        # パーサーでファイルを読み込む
        parser = PmdParser()
        result = parser.parse(pmd_path)
        self.assertIsNotNone(result)
```

#### 主要メソッド

##### ファイルパス取得メソッド

- `get_pmd_file(name=None)`: PMDファイルのパスを取得
- `get_pmx_file(name=None)`: PMXファイルのパスを取得
- `get_vmd_file(name=None)`: VMDファイルのパスを取得
- `get_texture_file(model_name, texture_name)`: テクスチャファイルのパスを取得

##### 利用可能なファイル一覧取得メソッド

- `get_available_pmd_files()`: 利用可能なPMDファイル名のリスト
- `get_available_pmx_files()`: 利用可能なPMXファイル名のリスト
- `get_available_vmd_files()`: 利用可能なVMDファイル名のリスト

##### データロードメソッド（キャッシュ機能付き）

- `load_pmd_data(name=None)`: PMDファイルをパースしてTupleで返す
- `load_pmx_data(name=None)`: PMXファイルをパースしてTupleで返す
- `load_vmd_data(name=None)`: VMDファイルをパースしてTupleで返す

##### 一時ファイル作成メソッド

- `create_temp_file(content, extension)`: 一時ファイルを作成してパスを返す
- `cleanup_temp_files()`: 作成した一時ファイルをすべて削除

#### 高度な使用例

```python
class TestTextureHandling(unittest.TestCase):
    def setUp(self):
        self.fixture_provider = TestFixtureProvider()
        
    def tearDown(self):
        # 一時ファイルのクリーンアップ
        self.fixture_provider.cleanup_temp_files()
        
    def test_texture_conversion(self):
        """テクスチャ変換のテスト"""
        # 利用可能なPMXファイルを確認
        available_files = self.fixture_provider.get_available_pmx_files()
        print(f"Available PMX files: {available_files}")
        
        # PMXデータをロード（キャッシュされる）
        pmx_data = self.fixture_provider.load_pmx_data('model_with_textures')
        
        # 一時的なテクスチャファイルを作成
        test_texture = b'\x89PNG\r\n\x1a\n...'  # PNGバイナリデータ
        temp_texture_path = self.fixture_provider.create_temp_file(test_texture, '.png')
        
        # テクスチャ変換処理
        converter = TextureConverter()
        result = converter.convert(temp_texture_path)
        self.assertTrue(result)
```

#### カスタムデータディレクトリの指定

```python
# デフォルトは tests/data ディレクトリ
default_provider = TestFixtureProvider()

# カスタムディレクトリを指定
custom_provider = TestFixtureProvider(data_dir='/path/to/custom/test/data')
```

### 注意事項

- TestFixtureProviderは初期化時にディレクトリを探索してファイルをキャッシュするため、大量のテストファイルがある場合でも高速に動作します
- ファイル名は拡張子なしで指定します（例: 'miku_v2.pmd' → 'miku_v2'）
- テストファイルが見つからない場合は`FileNotFoundError`が発生します
- 一時ファイルは`tearDown`で必ず`cleanup_temp_files()`を呼び出してクリーンアップしてください

### テストデータの配置

テストで使用するデータファイルは以下のディレクトリに配置されています：

- `tests/data/`: 統合テスト用のMMDファイル（PMD、PMX、VMD）とテクスチャファイル
- `tests/data/for_unit_test/`: 単体テスト用の軽量なテストデータ
  - `test_1bone_cube.pmx`: 1ボーンのみを持つシンプルなキューブモデル
  - `test_basic_bone.pmx`: 基本的なボーン構造を持つモデル
  - `test_semi_basic_bone.pmx`: やや複雑なボーン構造を持つモデル

## モックシステムの詳細

### PMD/PMX/VMDファイルフォーマットのモック

プロジェクトには、テスト用のMMDファイルフォーマットモックが実装されています：

```
tests/common/
├── pmd_mock.py           # PMDファイルフォーマットのモック
├── pmx_mock.py           # PMXファイルフォーマットのモック
└── vmd_mock.py           # VMDファイルフォーマットのモック
```

これらのモックは、実際のバイナリファイルを作成せずにMMDデータ構造をテストできるようにします。

### 使用例

```python
# PMDモックの使用
from tests.common.pmd_mock import create_test_pmd_data

pmd_data = create_test_pmd_data()
# pmd_dataを使用してパーサーやコンバーターをテスト

# PMXモックの使用
from tests.common.pmx_mock import create_test_pmx_data

pmx_data = create_test_pmx_data()
# pmx_dataを使用してテスト

# VMDモックの使用
from tests.common.vmd_mock import create_test_vmd_data

vmd_data = create_test_vmd_data()
# vmd_dataを使用してアニメーションテスト
```

## UIテスト

UIのテストは2つのカテゴリに分類されます。

### 新規追加テスト: UITranslatorのGUIテスト

多言語対応システム（UITranslator）のGUIテストが`tests/gui/guitest_translator.py`に追加されました。

#### テスト内容
- UITranslatorのシングルトン動作確認
- サポート言語（日本語、英語、繁体字中国語、簡体字中国語）の確認
- 翻訳ファイルの読み込み確認
- 各言語での翻訳動作確認
- 言語切り替え時のUI更新確認
- BaseTabクラスとの統合確認
- 特殊文字を含む翻訳の処理確認

#### 実行方法
```bash
# すべてのGUIテストを実行（translatorテストを含む）
python tests/run_gui_tests.py

# Maya内から直接実行する場合
# Script Editorで以下を実行:
import sys
sys.path.append(r'F:\Develop\maya_mmd_tools')
from tests.gui.guitest_translator import TestUITranslator
import unittest
suite = unittest.TestLoader().loadTestsFromTestCase(TestUITranslator)
unittest.TextTestRunner(verbosity=2).run(suite)
```

### テストディレクトリ構造

```
tests/
├── unit/                    # ユニットテスト（UIのビジネスロジックを含む）
│   ├── test_application_state.py
│   ├── test_info_presenter.py
│   └── test_import_export_presenter.py
└── gui/                     # GUI環境でのみ実行可能なテスト
    ├── gui_test_base.py
    ├── run_gui_tests.py
    └── test_ui_components.py
```

### 1. UIビジネスロジックテスト（Unit）

Maya standalone環境で実行可能なUIのビジネスロジックテスト。PresenterやApplicationStateなどをテストします。

#### 特徴
- Qtウィジェットはモック化される
- Maya APIは完全に利用可能
- CI/CDパイプラインで自動実行可能
- 高速に実行できる

#### 実行方法
```bash
# すべてのユニットテストを実行（UIビジネスロジックを含む）
python tests/run_tests.py --type unit

# 特定のUIテストを実行
python tests/run_tests.py --type unit --test test_info_presenter
```

#### テスト例
```python
from unittest.mock import Mock
from tests.common.maya_test_base import MayaTestBase
from mmd_tools.ui.presenters.info_presenter import InfoPresenter

class TestInfoPresenter(MayaTestBase):
    def setUp(self):
        super().setUp()
        # Qtウィジェットをモック化
        self.mock_view = Mock()
        self.presenter = InfoPresenter(self.mock_view)
    
    def test_update_model_info(self):
        """モデル情報更新のテスト"""
        # テスト用のMMDモデルを作成
        model = self._create_test_mmd_model()
        
        # プレゼンターのロジックをテスト
        self.presenter.update_model_info(model)
        
        # viewへの呼び出しを検証
        self.mock_view.set_model_name_jp.assert_called()
```

### 2. GUIインタラクションテスト

実際のQtウィジェットの作成、表示、インタラクションをテストします。
MayaのGUIセッションが必要です。

#### 特徴
- 実際のQtウィジェットを作成・操作
- ウィンドウの表示やUIイベントをテスト
- 実行には完全なMaya GUI環境が必要

#### 実行方法

##### 方法1: コマンドラインからの自動実行 (推奨)

新しく導入されたテストランナーを使用し、コマンドラインからGUIテストを自動実行します。
この方法は、Mayaの起動、テスト実行、終了までを完全に自動化します。

```bash
# tests/gui ディレクトリ内のすべてのGUIテストを実行
python tests/run_gui_tests.py

# 特定のMayaバージョンを指定して実行
python tests/run_gui_tests.py --maya_version 2023
```

このコマンドは以下の処理を自動的に行います。
1. Mayaアプリケーションをバックグラウンドで起動します。
2. `commandPort` を通じてMayaに接続します。
3. `tests/gui` ディレクトリ内のテストを実行するよう指示します。
4. テストのログを `logs/ui_test_results.log` に出力し、同時にコンソールにも表示します。
5. テスト完了後、Mayaアプリケーションを自動的に終了します。

##### 方法2: Maya GUI内からの手動実行 (旧方式)

従来通り、MayaのScript Editorやシェルフから手動でテストを実行することも可能です。

**Script Editorから実行:**
```python
# Maya Script Editorで以下を実行
import sys
# プロジェクトルートへのパスを適宜変更してください
sys.path.append(r'F:\Develop\maya_mmd_tools')
from tests.gui import run_gui_tests

# すべてのGUIテストを実行
run_gui_tests.run()
```

**シェルフボタンから実行:**
1. `scripts/run_ui_tests_gui.py` の内容は、コマンドラインランナーに置き換えられました。シェルフから実行したい場合は、上記Script Editorのコードをシェルフボタンに登録してください。

#### GuiTestBaseクラス
GUI環境でのテストをサポートする基底クラスが提供されています：

```python
from tests.common.gui_test_base import GuiTestBase, requires_gui

@requires_gui
class TestMainWindow(GuiTestBase):
    def test_window_creation(self):
        """ウィンドウが正しく作成されるかテスト"""
        from mmd_tools.ui.main_window import MainWindow
        window = MainWindow()
        self.assertIsNotNone(window)
        window.close()
```

### GUIテストの注意事項

- コマンドラインからの実行 (`scripts/run_ui_tests_gui.py`) を推奨します。
- CI/CD環境など、GUIを持たない環境ではGUIインタラクションテストは実行できません。


## テストの実行環境

### 必要な環境

*   **Maya:** テストは特定の Maya バージョン（例: Maya 2023, 2024）で実行されることを想定します。
*   **Python:** Maya にバンドルされている Python 環境 (`mayapy.exe`) を使用します。
*   **OS:** Windows、macOS、Linux（WSL環境でのWindows版Mayaの実行もサポート）

### WSL環境での実行

WSL環境でWindows版のMayaを使用する場合、run_tests.pyが自動的にパスを変換します：

```bash
# WSL環境から実行
python tests/run_tests.py --type unit

# 自動的にWindowsパスに変換されて実行される
# /mnt/c/Program Files/Autodesk/Maya2024/bin/mayapy.exe
```

## テストの自動化 (今後の検討事項)

*   CI/CD パイプラインへのテスト組み込み
*   テストカバレッジの測定と可視化
*   パフォーマンステストの追加
