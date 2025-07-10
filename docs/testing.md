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
```


run_tests.pyは以下の機能を持ちます

- すべてのテストの実行
  - `--type all` オプションを指定して実行
- ユニットテストの実行：
  - `--type unit` オプションを指定して実行
- 統合テストの実行：
  - `--type integration` オプションを指定して実行
- 特定のテストケースの実行：
  - `--test <test_case_name>` オプションを指定して実行

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

## 4. テストの実行環境

*   **Maya:** テストは特定の Maya バージョン（例: Maya 2023, 2024）で実行されることを想定します。
*   **Python:** Maya にバンドルされている Python 環境 (`mayapy.exe`) を使用します。

## 5. テストの自動化 (今後の検討事項)

*   Maya のバッチモードや `mayapy.exe` を利用した自動テストフレームワークの導入。
*   CI/CD パイプラインへのテスト組み込み。
