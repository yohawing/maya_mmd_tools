# テストドキュメント

## 1. 概要

本ドキュメントは、Autodesk Maya 用 MikuMikuDance (MMD) ファイルインポートプラグインのテスト戦略について記述します。プラグインの各コンポーネント（MMD ファイルパーサー、Maya データコンバーター、UI）の品質と正確性を保証することを目的とします。

## 2. テスト戦略

## テスト戦略

### テスト実行システム

プロジェクトでは、`tests/run_tests.py`を使用してテストを実行します。このスクリプトは以下の機能を提供します：

- **テストタイプの指定**: ユニットテスト（`--type unit`）と統合テスト（`--type integration`）を選択可能
- **テストフィルタリング**: 特定のテストモジュール、クラス、メソッドを指定して実行可能
- **Maya環境の自動処理**: 統合テストでは自動的にmayapyを使用してMaya環境で実行
- **柔軟なマッチング**: 完全なテスト名と部分的なパターンマッチングの両方に対応

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
python tests/run_tests.py --type unit --test test_pmd_parser.TestPmdParser
python tests/run_tests.py --type integration --test test_maya_utils.TestMayaUtils

# 特定のテストメソッドを実行
python tests/run_tests.py --type unit --test test_pmd_parser.TestPmdParser.test_parse_header
python tests/run_tests.py --type integration --test test_maya_utils.TestMayaUtils.test_create_material
```

#### パターンマッチングを使用

```bash
# 部分的なマッチングで複数のテストを実行
python tests/run_tests.py --type integration --test TestMayaUtils
python tests/run_tests.py --type unit --test test_pmd
python tests/run_tests.py --type integration --test converter
```

run_tests.pyは以下の機能を持ちます

- すべてのテストの実行
  - `--type all` オプションを指定して実行
- ユニットテストの実行：
  - `--type unit` オプションを指定して実行
- 統合テストの実行：
  - `--type integration` オプションを指定して実行
  - mayaが必要なため、通常のインストールディレクトリから自動的に `mayapy.exe` へのパスを解決して、
- 特定のテストケースの実行：
  - `--test <test_case_name>` オプションを指定して実行

### 高度なオプション

#### Maya バージョンの指定

```bash
# 特定のMayaバージョンを指定（デフォルトは2024）
python tests/run_tests.py --type integration --maya 2023 --test test_maya_utils
python tests/run_tests.py --type integration --maya 2025
```

#### 直接mayapyを使用

```bash
# mayapyを直接使用して統合テストを実行
'C:\Program Files\Autodesk\Maya2024\bin\mayapy.exe' tests\run_tests.py --type integration

# 特定のテストを実行
'C:\Program Files\Autodesk\Maya2024\bin\mayapy.exe' tests\run_tests.py --type integration --test test_maya_utils
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

### テスト実行の仕組み

#### ユニットテスト（`--type unit`）

- **実行環境**: 通常のPython環境
- **対象ディレクトリ**: `tests/unit/`
- **実行方法**: `unittest.TestLoader().discover()`でテストを発見し、`CustomTestRunner`で実行
- **Maya依存性**: なし

#### 統合テスト（`--type integration`）

- **実行環境**: Maya Python環境（mayapy.exe）
- **対象ディレクトリ**: `tests/integration/`
- **実行方法**: 
  1. 通常のPython環境で実行された場合は、自動的にmayapyを起動
  2. mayapy環境で実行された場合は、直接テストを実行
- **Maya依存性**: あり（Maya APIやcmdsを使用）

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

### 実行例

```bash
# 開発中によく使用されるコマンド例

# メッシュ変換関連のテストのみ実行
python tests/run_tests.py --type integration --test mesh_converter

# Maya ユーティリティ関連のテストのみ実行
python tests/run_tests.py --type integration --test maya_utils

# 特定のテストメソッドのデバッグ
python tests/run_tests.py --type integration --test test_maya_utils.TestMayaUtils.test_create_material

# PMDパーサーのユニットテスト
python tests/run_tests.py --type unit --test test_pmd_parser

# 全てのコンバーター関連のテスト
python tests/run_tests.py --type integration --test converter
```

### 2.1. ユニットテスト

主に Maya 環境に依存しないロジックに対して実施します。

*   **対象モジュール:** `mmd_parser.py`
*   **目的:** MMD バイナリファイルの解析が正確に行われ、期待されるデータ構造に変換されることを検証します。
*   **テスト項目例:**
    *   PMD/PMX/VMD ファイルのヘッダ情報が正しく読み取れるか。
    *   頂点、面、ボーン、モーフ、物理演算データが正しくパースされるか。
    *   不正なファイル形式や破損したファイルに対するエラーハンドリング。
*   **実行方法:** Python の `unittest` フレームワークを使用し、通常の Python 環境で実行します。

### 2.2. 統合テスト

MMD データを Maya のシーン要素に変換するロジックに対して実施します。Maya 環境が必要です。

*   **対象モジュール:** `maya_converter.py` およびそのサブモジュール (`maya_mesh_converter.py`, `maya_bone_converter.py` など)
*   **目的:** パースされた MMD データが Maya シーン内で正しく表現されることを検証します。
*   **テスト項目例:**
    *   MMD メッシュが Maya のメッシュとして正しく生成され、頂点数、面数、UV が一致するか。
    *   ボーン階層が正しく構築され、スキニングが適用されるか。
    *   モーフターゲットがブレンドシェイプとして正しく機能するか。
    *   物理演算の剛体とジョイントが Maya のリジッドボディ/コンストレインとして正しく設定されるか。
    *   VMD アニメーションが Maya のキーフレームとして正しく適用されるか。
*   **実行方法:** `mayapy.exe` を使用してテストスクリプトを実行し、Maya のコマンド (`maya.cmds`) を介してシーンの状態を検証します。

### 2.3. UI テスト (手動)

ユーザーインターフェースの機能と操作性を検証します。

*   **対象モジュール:** `ui.py`
*   **目的:** ユーザーがプラグインを直感的に操作でき、期待通りの動作をすることを確認します。
*   **テスト項目例:**
    *   ファイル選択ダイアログが正しく表示され、ファイルを選択できるか。
    *   インポートオプションが正しく機能し、シーンに反映されるか。
    *   進捗バーやログメッセージが適切に表示されるか。
*   **実行方法:** Maya の GUI 上でプラグインをロードし、手動で操作して動作を確認します。

### 2.4. エンドツーエンドテスト (手動)

実際の MMD ファイルを使用して、プラグイン全体の動作を検証します。

*   **目的:** 実際の MMD モデルとモーションファイルが、Maya シーンに完全にインポートされ、期待通りの結果が得られることを確認します。
*   **テスト項目例:**
    *   様々な種類の PMD/PMX モデル（シンプルなものから複雑なものまで）のインポート。
    *   VMD モーションの適用と再生。
    *   物理演算のシミュレーション。
    *   エラーが発生した場合の適切なメッセージ表示。
*   **実行方法:** Maya の GUI 上でプラグインを使用し、様々な MMD ファイルをインポートして目視で確認します。

## 3. テストデータ / フィクスチャ

*   **MMD サンプルファイル:** テスト用に、様々な特徴を持つ PMD, PMX, VMD ファイルを用意します。これには、シンプルなモデル、複雑なモデル、モーフや物理演算を含むモデル、様々なアニメーションを含むモーションファイルなどが含まれます。

テストデータは、`tests/data` ディレクトリに配置し、各テストケースで適切に参照します。
テストに使用されるファイルは、著作権や利用規約に違反しないデータである必要があります。


## 4. テストの実行環境

*   **Maya:** テストは特定の Maya バージョン（例: Maya 2023, 2024）で実行されることを想定します。
*   **Python:** Maya にバンドルされている Python 環境 (`mayapy.exe`) を使用します。

## 5. テストの自動化 (今後の検討事項)

*   Maya のバッチモードや `mayapy.exe` を利用した自動テストフレームワークの導入。
*   CI/CD パイプラインへのテスト組み込み。
