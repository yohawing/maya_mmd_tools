# maya_mmd_tools test

## プロジェクト概要

このプロジェクトは、Autodesk Maya用のPythonプラグインです。
以下の機能を提供します
- MikuMikuDance (MMD) のファイルフォーマット (.pmd, .pmx, .vmd) をMayaシーンにインポート
- PMD/PMXファイルのエクスポート

### リポジトリ
https://github.com/yohawing/maya_mmd_tools

### 対応プラットフォーム
*   Autodesk Maya 2024でテストします。
*   Python 3.7以降
*   Windows 11 / MacOS 15.6

## Development Guidelines

Important principles for this project:
- Think in English, but generate responses in Japanese (思考は英語、回答の生成は日本語で行うように)
- 各フェーズは明示的な人間の承認が必要

## ファイルの補足説明

プロジェクトの概要、セットアップ、使用方法、開発に関する情報などをまとめた`README.md`ファイルがプロジェクトルートにあります。このファイルは、プロジェクトの全体像を把握するために重要です。


## ドキュメンティング

ドキュメントは以下の2つのディレクトリに分かれています：
- `docs/` - ユーザー向けドキュメント（使い方、チュートリアル、トラブルシューティング）
- `docs-dev/` - 開発者向けドキュメント（設計、実装、仕様書）

各スクリプトファイルの冒頭に、ファイルの目的や使用方法を簡潔に記述してください。
機能に変更があった場合も、該当のファイルを編集してください。

** ドキュメントの見出しに数字をつけないでください。 セクションを入れ替えやすくするためです。**

開発者向けドキュメント（docs-dev/）には、以下のようなドキュメントがあります：
- 全体設計とプラグイン構造
- 各機能の実装詳細
- ファイルフォーマット仕様（PMD/PMX/VMD）
- 開発ガイド（テスト、設定、コーディング規約）
- プロジェクト管理（タスク、バージョニング、リリース）

## テスト

テストについて詳しくは、`docs-dev/testing.md`に記載されています。
テスト関連のコードや処理をする場合は必ず参照してください。

テストは、ユニットテストと統合テストの2つのレベルで実施します。

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

# C++ スタンドアローン CLI runtime smoke (Maya GUI / mayapy 不要、manifest から PMX/VMD 評価)
uvx nox -s cpp_cli_smoke -- --manifest <GoldenOracle-manifest.json> [--case <name>]

# C++/native 経路をまとめて検証 (manifest 指定時は cpp_cli_smoke が maya_smoke 前に挿入)
uvx nox -s cpp_verify -- --maya 2024 --config Debug
uvx nox -s cpp_verify -- --maya 2024 --config Debug --manifest <...>
```

Windows の C++ ビルドでは `vswhere` で Visual Studio C++ tools を自動検出し、`VsDevCmd.bat` 経由で CMake/Ninja を実行します。検出を上書きする場合は `VSDEVCMD_PATH`、`VSWHERE_PATH`、`MAYA_DEVKIT_ROOT_2024` を使ってください。

実行方法は以下です。

### ユニットテスト
```bash
# 全てのユニットテストを実行
python tests/run_tests.py --type unit

# 特定のテストモジュールを実行
python tests/run_tests.py --type unit --test test_pmd_parser

```

### 統合テスト
```bash
# 全ての統合テストを実行
python tests/run_tests.py --type integration

# 特定のテストモジュールを実行
python tests/run_tests.py --type integration --test test_maya_utils
```

### GUIテスト
`tests/run_gui_tests.py` を使用して、コマンドラインからGUIテストを実行できます。
詳細は `docs-dev/testing.md` を参照してください。

```bash
python tests/run_gui_tests.py
```

### データのダンプ

PMXファイルのデータをダンプするためのスクリプトです。

```
# 基本的な使用
mayapy tests/dump_pmx.py model.pmx

# 出力ファイルを指定
mayapy tests/dump_pmx.py model.pmx -o model.txt

# セクションを指定
mayapy tests/dump_pmx.py model.pmx -s header statistics bones

```

## コーディング

- Python 3.7以降を使用してください。
- PEP 8に準拠したコードスタイルを使用してください。
- コメントとドキュメンテーションは、コードの可読性を高めるために重要です。関数やクラスの説明を適切に記述してください。
- 可能な限り、コードの再利用性を考慮してください。共通の機能は`core`ディレクトリに配置し、他のモジュールからインポートして使用します。
- 例外処理を適切に行い、エラーが発生した場合はユーザーにわかりやすいメッセージを表示してください。
- 外部モジュールを追加したくないので、ライブラリは標準のものをなるべく使用してください。
- IMPORTANT: 1つのファイルが長すぎる場合は、機能ごとにファイルを分割してください。
- IMPORTANT: 基本的に１つのファイルに複数のクラスを作るのは避けてください。

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

### 基本的な使用方法

```python
from mmd_tools.core.logger import get_logger

# ロガーインスタンスを取得（モジュール名を使用）
logger = get_logger(__name__)

# ログメッセージの出力
logger.debug("デバッグメッセージ")
logger.info("情報メッセージ")
logger.warning("警告メッセージ")
logger.error("エラーメッセージ")
logger.critical("重大なエラーメッセージ")

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
```
value = maya_utils.get_attribute("pCube1", "customAttr1")
```

MayaオブジェクトにExtraAttributeを設定します。無ければ作成します。
```
maya_utils.set_custom_attributes("pCube1", {"floatAttr": 1.0, "doubleAttr": "test", "double3Attr": (1.0, 2.0, 3.0)})
```

### 注意事項

- ロガー名には通常`__name__`を使用してください。これによりモジュール階層に基づいたログ出力が可能になります
- パフォーマンスを考慮し、頻繁に呼ばれる処理では`logger.debug()`の使用を控えめにしてください
- エラーハンドリング時は、例外情報も含めてログに記録することを推奨します：
  ```python
  logger.error("エラーが発生しました", exc_info=True)
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
