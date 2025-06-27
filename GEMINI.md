# maya_mmd_tools

## プロジェクト概要

このプロジェクトは、Autodesk Maya用のPythonプラグインです。
以下の機能を提供します
- MikuMikuDance (MMD) のファイルフォーマット (.pmd, .pmx, .vmd) をMayaシーンにインポート
- PMD/PMXファイルのエクスポート


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
├── src/
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
│   ├── test_mmd_parser.py
│   └── common/
│       ├── __init__.py
│       └── test_base.py
└── doc/
    ├── design.md
    ├── testing.md
    └── project_management.md
```

## Maya モジュールファイル (.mod) について

`maya_mmd_tools.mod` ファイルは、Maya がこのプラグインを認識し、必要なパスを設定するために使用されます。このファイルを Maya の `modules` ディレクトリに配置することで、プラグインのロードが容易になります。

## userSetup.py について

`userSetup.py` ファイルは、Maya の起動時に自動的に実行され、カスタムメニューの追加やプラグインの初期設定を行うために使用されます。このファイルを Maya の `scripts` ディレクトリに配置することで、Maya 起動時に「MMD Tools」メニューが自動的に追加されます。

## README.mdについて

プロジェクトの概要、セットアップ、使用方法、開発に関する情報などをまとめた`README.md`ファイルがプロジェクトルートにあります。このファイルは、プロジェクトの全体像を把握するために重要です。


## ドキュメンティング

作成した機能は`doc`ディレクトリにドキュメントとして、内容をMarkdownとして保存してください。
機能に変更があった場合も、該当のファイルを編集してください。

## プロジェクトマネージメント

プロジェクトの進行管理、スプリント計画、タスク管理については、`doc/project_management.md`を参照してください。

## テスト

現在、自動テストのセットアップはありません。テストはMayaのGUI上で手動で行うか、`mayapy.exe` を使用してスクリプト経由で実行する必要があります。

## コーディング

- Python 3.7以降を使用してください。
- PEP 8に準拠したコードスタイルを使用してください。
- コメントとドキュメンテーションは、コードの可読性を高めるために重要です。関数やクラスの説明を適切に記述してください。
- 可能な限り、コードの再利用性を考慮してください。共通の機能は`core`ディレクトリに配置し、他のモジュールからインポートして使用します。
- docstringを使用して、関数やクラスの目的、引数、戻り値を明確に記述してください。
- 例外処理を適切に行い、エラーが発生した場合はユーザーにわかりやすいメッセージを表示してください。
- 外部モジュールを追加したくないので、ライブラリは標準のものをなるべく使用してください。
- 1つのファイルが長すぎる場合は、機能ごとに分割してください。
- 基本的に１つのファイルに複数のクラスを作るのは避けてください。

## 参考サイト

[mmdpaimaya](https://github.com/phyblas/mmdpaimaya/tree/master)
[blender_mmd_tools](https://github.com/MMD-Blender/blender_mmd_tools)