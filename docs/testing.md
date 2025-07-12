# テストドキュメント

## 概要

本ドキュメントは、Autodesk Maya 用 MikuMikuDance (MMD) ファイルインポートプラグインのテスト戦略について記述します。プラグインの各コンポーネント（MMD ファイルパーサー、Maya データコンバーター、UI）の品質と正確性を保証することを目的とします。

## テスト実行システム

### アーキテクチャ

プロジェクトでは、すべてのテスト（ユニット/統合）をMaya環境内で実行する統一されたテストシステムを採用しています：

```
tests/
├── common/                  # テスト共通ユーティリティ
│   ├── test_base.py        # 基本テストクラス
│   ├── maya_test_base.py   # Maya統合テスト基本クラス
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

## モック機能

### 概要

プロジェクトには、Maya環境なしでユニットテストを実行できる包括的なモックシステムが実装されています。これにより、ビジネスロジックのテストがより高速かつ独立して実行できます。

### モックシステムの構成

```
tests/common/
├── maya_mock.py           # Maya APIの基本モック実装
├── maya_mock_helpers.py   # MMD関連オブジェクトのファクトリ
├── pmd_mock.py           # PMDファイルフォーマットのモック
├── pmx_mock.py           # PMXファイルフォーマットのモック
└── vmd_mock.py           # VMDファイルフォーマットのモック
```

### 主な機能

- **maya.cmds モック**: ジョイント、メッシュ、アニメーション操作
- **maya.api.OpenMaya モック**: ベクトル、クォータニオン、行列操作
- **MMDオブジェクトファクトリ**: 標準ボーン階層、IKセットアップ、メッシュ作成
- **ファイルフォーマットモック**: テスト用のバイナリデータ生成

### 使用方法

詳細な使用方法については、[モック使用ガイド](mock_usage.md)を参照してください。

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
