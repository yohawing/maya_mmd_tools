# maya_mmd_tools

## プロジェクト概要

このプロジェクトは、Autodesk Maya用のPythonプラグインです。MikuMikuDance (MMD) のファイルフォーマット (.pmd, .pmx, .vmd) をMayaシーンにインポートする機能を提供します。

### 対応プラットフォーム
*   Autodesk Maya 2024でテストします。
*   Python 3.7以降
*   Windows 11

### ディレクトリ構成

ディレクトリに変更があった場合は、以下を編集してください。

```
F:/Develop/maya_mmd_tools/
├── src/
│   ├── __init__.py
│   ├── plugin_main.py
│   └── mmd_importer.py
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