# maya_mmd_tools

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
*   Windows 11

### ディレクトリ構成

ディレクトリに変更があった場合は、以下を編集してください。

```
F:/Develop/maya_mmd_tools/
├── maya_mmd_tools.mod
├── userSetup.py
├── mmd_tools/
│   ├── __init__.py
│   ├── plugin_main.py
│   ├── ui.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── mmd_parser.py
│   │   ├── pmd_parser.py
│   │   ├── pmx_parser.py
│   │   ├── vmd_parser.py
│   │   ├── maya_utils.py
│   │   ├── pmd_data/
│   │   │   ├── __init__.py
│   │   │   ├── header.py
│   │   │   ├── vertex.py
│   │   │   ├── material.py
│   │   │   ├── bone.py
│   │   │   ├── ik.py
│   │   │   ├── morph.py
│   │   │   ├── display_frame.py
│   │   │   ├── rigid_body.py
│   │   │   └── joint.py
│   │   ├── pmx_data/
│   │   │   ├── __init__.py
│   │   │   ├── header.py
│   │   │   ├── vertex.py
│   │   │   ├── face.py
│   │   │   ├── material.py
│   │   │   ├── bone.py
│   │   │   ├── ik.py
│   │   │   ├── ik_link.py
│   │   │   ├── morph.py
│   │   │   ├── display_frame.py
│   │   │   ├── rigid_body.py
│   │   │   └── joint.py
│   │   └── vmd_data/
│   │       ├── __init__.py
│   │       ├── header.py
│   │       ├── bone_frame.py
│   │       ├── morph_frame.py
│   │       ├── camera_frame.py
│   │       ├── light_frame.py
│   │       ├── shadow_frame.py
│   │       └── ik_show_hide_frame.py
│   ├── converters/
│   │   ├── __init__.py
│   │   ├── mesh_converter.py
│   │   ├── bone_converter.py
│   │   ├── morph_converter.py
│   │   ├── physics_converter.py
│   │   └── animation_converter.py
│   └── io/
│       ├── __init__.py
│       ├── mmd_importer.py
│       ├── pmd_exporter.py
│       ├── pmx_exporter.py
│       └── vmd_exporter.py
├── resources/
│   ├── icons/
│   └── ui/
├── tests/
│   ├── run_tests.py
│   ├── common/
│   │   ├── __init__.py
│   │   └── test_base.py
│   ├── unit/
│   │   ├── __init__.py
│   │   ├── test_mmd_parser.py
│   │   ├── test_pmd_parser.py
│   │   ├── test_pmx_parser.py
│   │   ├── test_vmd_parser.py
│   └── integration/
│       ├── __init__.py
│       ├── test_ui.py
│       ├── test_mesh_converter.py
│       ├── test_bone_converter.py
│       ├── test_morph_converter.py
│       ├── test_physics_converter.py
│       ├── test_animation_converter.py
│       ├── test_pmd_exporter.py
│       ├── test_pmx_exporter.py
│       ├── test_vmd_exporter.py
└── docs/
    ├── design.md
    ├── testing.md
    ├── project_management.md
    ├── settings.md
    ├── unicode_dictionary_guide.md
    ├── pmx_spec.md
```

## ファイルの補足説明


プロジェクトの概要、セットアップ、使用方法、開発に関する情報などをまとめた`README.md`ファイルがプロジェクトルートにあります。このファイルは、プロジェクトの全体像を把握するために重要です。


## ドキュメンティング

`docs`ディレクトリ内にドキュメントをMarkdownで残してください。
また、各スクリプトファイルの冒頭に、ファイルの目的や使用方法を簡潔に記述してください。
機能に変更があった場合も、該当のファイルを編集してください。

** ドキュメントの見出しに数字をつけないでください。 セクションを入れ替えやすくするためです。**

docsディレクトリには、以下のファイルがあります。
- `design.md`: プロジェクトの設計やアーキテクチャ
- `testing.md`: テストの実行方法やテストケースの説明
- `project_management.md`: プロジェクトの進行管理やタスク管理
- `settings.md`: プロジェクトの設定方法や利用可能な設定項目
- `unicode_dictionary_guide.md`: 日本語から英語への翻訳辞書の構造と使用方法
- `pmx_spec.md`: PMXファイルフォーマットの仕様

## プロジェクトマネージメント

プロジェクトの進行管理、スプリント計画、タスク管理については、`docs/project_management.md`を参照してください。

## テスト

テストについて詳しくは、`docs/testing.md`に記載されています。
テスト関連のコードや処理をする場合は必ず参照してください。

テストは、ユニットテストと統合テストの2つのレベルで実施します。

実行方法は以下です。
```
// run all unit test
python tests/run_tests.py --type unit
// unit test for specific test case
python tests/run_tests.py --test test_pmd_parser.TestPmdParser

// integration test
python tests/run_tests.py --type integration
// integration test for specific test case
python tests/run_tests.py --type integration --test test_mmd_parser.TestMmdParser
```

## コーディング

- Python 3.7以降を使用してください。
- PEP 8に準拠したコードスタイルを使用してください。
- コメントとドキュメンテーションは、コードの可読性を高めるために重要です。関数やクラスの説明を適切に記述してください。
- 可能な限り、コードの再利用性を考慮してください。共通の機能は`core`ディレクトリに配置し、他のモジュールからインポートして使用します。
- Googleスタイルのdocstringを使用して、関数やクラスの目的、引数、戻り値を明確に記述してください。
- 例外処理を適切に行い、エラーが発生した場合はユーザーにわかりやすいメッセージを表示してください。
- 外部モジュールを追加したくないので、ライブラリは標準のものをなるべく使用してください。
- 1つのファイルが長すぎる場合は、機能ごとにファイルを分割してください。
- 基本的に１つのファイルに複数のクラスを作るのは避けてください。
- UIの作成は、pyside2にフォールバック可能なpyside6のコードで記述してください。
- 高速化が期待できる箇所は、Maya Python API2.0を使用してください。


## 参考サイト

- [mmdpaimaya](https://github.com/phyblas/mmdpaimaya/tree/master)
- [blender_mmd_tools](https://github.com/MMD-Blender/blender_mmd_tools)