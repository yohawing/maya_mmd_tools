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
├── src/
│   ├── __init__.py
│   ├── plugin_main.py
│   ├── ui.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── mmd_parser.py
│   │   └── maya_utils.py
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
│   └── (テストファイルはここに入ります)
└── doc/
    ├── design.md
    ├── testing.md
    └── project_management.md
```

## Maya モジュールファイル (.mod) について

`maya_mmd_tools.mod` ファイルは、Maya がこのプラグインを認識し、必要なパスを設定するために使用されます。このファイルを Maya の `modules` ディレクトリに配置することで、プラグインのロードが容易になります。

## README.mdについて

プロジェクトの概要、セットアップ、使用方法、開発に関する情報などをまとめた`README.md`ファイルがプロジェクトルートにあります。このファイルは、プロジェクトの全体像を把握するために重要です。


## ドキュメンティング

作成した機能は`doc`ディレクトリにドキュメントとして、内容をMarkdownとして保存してください。
機能に変更があった場合も、該当のファイルを編集してください。

## プロジェクトマネージメント

プロジェクトの進行管理、スプリント計画、タスク管理については、`doc/project_management.md`を参照してください。

## テスト

現在、自動テストのセットアップはありません。テストはMayaのGUI上で手動で行うか、`mayapy.exe` を使用してスクリプト経由で実行する必要があります。

## リンティングとフォーマット

コードの品質を保つため、`ruff` を使用します。

コードの静的解析とフォーマットを行うには、以下のコマンドを実行します。

```shell
# ruff check .
# black .
```

## 参考サイト

[mmdpaimaya](https://github.com/phyblas/mmdpaimaya/tree/master)
[blender_mmd_tools](https://github.com/MMD-Blender/blender_mmd_tools)