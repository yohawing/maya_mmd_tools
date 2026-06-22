# maya_mmd_tools test

## プロジェクト概要

このプロジェクトは、Autodesk Maya用のPythonプラグインです。
以下の機能を提供します
- MikuMikuDance (MMD) のファイルフォーマット (.pmd, .pmx, .vmd) をMayaシーンにインポート
- モデルの編集・アニメーションの編集
- PMD/PMXファイルのエクスポート

### リポジトリ
https://github.com/yohawing/maya_mmd_tools

### 対応プラットフォーム
*   Autodesk Maya 2024でテストします。
*   Python 3.7以降
*   Windows 11 / MacOS 15.6

## ドキュメンティング

ドキュメントは以下の2つのディレクトリに分かれています：
- `docs/` - ユーザー向けドキュメント（使い方、チュートリアル、トラブルシューティング）
- `docs-dev/` - 開発者向けドキュメント（設計、実装、仕様書）

各スクリプトファイルの冒頭に、ファイルの目的や使用方法を簡潔に記述してください。
機能に変更があった場合も、該当のファイルを編集してください。

## テスト

テストについて詳しくは、`docs-dev/testing.md`に記載されています。

### タスクランナー

開発用の共通入口は `noxfile.py` に集約します。設定ファイルを増やさないため、新しいビルド・検証タスクは原則として Nox セッションとして追加してください。

Nox は追加の仮想環境を作らず、既存の `mayapy` / CMake / Cargo / Python スクリプトを呼び出す薄い CLI ランナーとして使います。

よく使うコマンド:

```bash
# 既存のユニットテスト
uvx nox -s tests

# 既存の統合テスト
uvx nox -s tests -- --type integration

# mmd-anim FFI のビルドと Python native ロード確認
uvx nox -s ffi_build
uvx nox -s native_smoke

# C++ プラグインの CLI ビルドと mayapy smoke
uvx nox -s cpp_build -- --maya 2024 --config Debug
uvx nox -s maya_smoke -- --maya 2024 --config Debug

# mayapy でのオフスクリーン viewport キャプチャ smoke（GUI不要、C++プラグイン非依存の最小 smoke）
uvx nox -s maya_viewport_capture -- --maya 2024
uvx nox -s maya_viewport_capture -- --maya 2024 --out build/captures/viewport_smoke.png --width 640 --height 480

# C++ スタンドアローン CLI runtime smoke (Maya GUI / mayapy 不要、manifest から PMX/VMD 評価)
uvx nox -s cpp_cli_smoke -- --manifest <GoldenOracle-manifest.json> [--case <name>]

# C++/native 経路をまとめて検証 (manifest 指定時は cpp_cli_smoke が maya_smoke 前に挿入)
uvx nox -s cpp_verify -- --maya 2024 --config Debug
uvx nox -s cpp_verify -- --maya 2024 --config Debug --manifest <...>
```

### Maya GUI / DX11 viewport 検証メモ

- Codex/PowerShell 直下の `maya.exe` 起動は licensing error で落ちることがある。
- Maya 2026 / DX11 GUI 検証は `explorer.exe` 経由で起動すると成功した。
- 自動 attach したい場合だけ、一時 `userSetup.mel` で `commandPort :7721` を開き、検証後に削除する。
- `127.0.0.1:7721` の Listen と、Maya 内の `DirectX11` / `API : DirectX V.11` を確認する。

### E2E テスト (commandPort)

Maya GUI を commandPort 付きで起動し、Python を送り込んでパイプラインを検証する。
ログファイルの completion marker で完了判定。unit テストが green でも import/rig/render 系の変更は必ず E2E で実機確認すること。

```bash
# Native rig primitive E2E（IK/付与のネイティブパス検証、port 7724）
python tests/viewport/e2e_native_rig.py --maya 2026 --model "tests/data/Lumine/Lumine.pmx"

# Viewport snapshot（DX11 レンダリング検証、port 7722）
python tests/viewport/gui_snapshot.py --maya 2026

# 非ASCII テクスチャ resolve E2E（MMD_E2E_MODEL_PATH 環境変数で指定）
python tests/viewport/resolve_e2e.py
```

### Lint

```bash
rtk ruff check <changed files only>
```
pyproject.toml で `fix = true` のため `ruff check` はファイルを自動修正する。lint は触ったファイルのみに限定する。

## コーディング

- コメントとドキュメンテーションは、コードの可読性を高めるために重要です。関数やクラスの説明を適切に記述してください。
- 可能な限り、コードの再利用性を考慮してください。共通の機能は`core`ディレクトリに配置し、他のモジュールからインポートして使用します。
- 例外処理を適切に行い、エラーが発生した場合はユーザーにわかりやすいメッセージを表示してください。

### Python コーディングスタイル

Docstring: Googleスタイルのdocstringを使用して、関数やクラスの目的、引数、戻り値を明確に記述してください。
命名規則:
    クラス: PascalCase
    関数/変数: snake_case
    定数: UPPER_SNAKE_CASE
    プライベート: 先頭に _
インポート順序: 標準ライブラリ → サードパーティ → ローカル（ruffが自動整理）
- UIの作成は、pyside2にフォールバック可能なpyside6のコードで記述してください。
- 高速化が期待できる箇所は、Maya Python API2.0を使用してください。

### Maya Python API 2.0 / Devkit 参照

Python API 2.0 で高速化する実装では、Maya 2026 devkit のローカルリファレンスも参照してください。

- `C:\Program Files\Autodesk\Maya2026\devkit\include`
- `C:\Users\yohaw\Documents\maya\2024\maya-2024-developer-help-enu`

特に VMD/runtime bake の高速化では、`cmds.setKeyframe` を大量に呼ぶ前に、Maya Python API 2.0 の animCurve 直接作成・一括キー投入で置き換えられないか検討してください。


## ロガーの使用方法

このプロジェクトでは、Maya環境に最適化された統一的なロガーシステムを使用します。
`mmd_tools.core.logger`モジュールの`get_logger`関数を使用してロガーインスタンスを取得してください。


- ロガー名には通常`__name__`を使用してください。これによりモジュール階層に基づいたログ出力が可能になります
- パフォーマンスを考慮し、頻繁に呼ばれる処理では`logger.debug()`の使用を控えめにしてください
- エラーハンドリング時は、例外情報も含めてログに記録することを推奨します：
  ```python
  logger.error("エラーが発生しました", exc_info=True)
  ```

### よく使うUtility関数

#### `mmd_tools.core.maya_utils`

OpenMaya API 2.0を使用してアトリビュート値を設定します。
cmds.setAttrの代わりに使用します。

```python
maya_utils.set_attribute("pCube1", "customAttr1", 1.0, "float")
maya_utils.set_attribute("pCube1", "customAttr2", "example", "str")
maya_utils.set_attribute("pCube1", "customAttr3", [0.5, 0.5, 0.5], "double3")
```

OpenMaya API 2.0を使用してアトリビュート値を取得します。
cmds.getAttrの代わりに使用します。
```python
value = maya_utils.get_attribute("pCube1", "customAttr1")
```

## ユーティリティークラスの使用方法

`mmd_tools.core.utils`モジュールには、Maya環境に依存しないユーティリティ関数が含まれています。
`mmd_tools.core.maya_utils`モジュールには、Maya環境に特化したユーティリティ関数が含まれています。

新しい汎用的な関数などを実装する時は、Utilityモジュールに追加できないか検討してください。

## 参考サイト

- [mmdpaimaya](https://github.com/phyblas/mmdpaimaya/tree/master)
- [blender_mmd_tools](https://github.com/MMD-Blender/blender_mmd_tools)
- [Maya Python API 2.0 Documentation](https://help.autodesk.com/view/MAYAUL/2024/ENU/?guid=MAYA_API_REF_py_ref_index_html)
- [Maya Commands Python Index](https://help.autodesk.com/view/MAYAUL/2024/ENU/index.html?contextId=COMMANDSPYTHON-INDEX)
