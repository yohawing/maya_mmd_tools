# Maya File Translator Integration

## 概要

Maya MMD Toolsプラグインは、Mayaの標準的なFile > Import/Export機能と統合されたMMDファイルトランスレーターを提供します。これにより、ユーザーはMayaの標準的なワークフローでMMDファイル（PMX、PMD、VMD）をインポート/エクスポートできます。

## アーキテクチャ

### ファイルトランスレーター

プラグインは以下の2つのファイルトランスレーターを提供します：

1. **MmdFileTranslator**: PMX/PMDモデルファイル用
   - 拡張子: *.pmx, *.pmd
   - Import/Export両対応
   - Maya の File > Open でシーンファイルとして開くことも可能

2. **VmdFileTranslator**: VMDモーションファイル用
   - 拡張子: *.vmd
   - Import/Export両対応
   - シーンファイルとしては開けない（canBeOpened = False）

### 実装詳細

#### MmdFileTranslator クラス

```python
class MmdFileTranslator(ommpx.MPxFileTranslator):
    # プラグイン識別情報
    kPluginTranslatorTypeName = "MMD Model"
    kPluginTranslatorTypeId = om.MTypeId(0x00001234)
```

主要メソッド：
- `doRead()`: ファイル読み込み（インポート）処理
- `doWrite()`: ファイル書き込み（エクスポート）処理
- `filter()`: サポートする拡張子を返す
- `haveReadMethod()`, `haveWriteMethod()`: 読み書き能力を宣言

#### VmdFileTranslator クラス

```python
class VmdFileTranslator(ommpx.MPxFileTranslator):
    kPluginTranslatorTypeName = "MMD Motion"
    kPluginTranslatorTypeId = om.MTypeId(0x00001235)
```

VMDファイル専用のトランスレーター。モーションデータのため、独立したシーンファイルとしては扱えません。

### 統合ポイント

#### mmd_importer.py との統合

ファイルトランスレーターは、既存の `mmd_importer.py` の `import_mmd_file()` 関数を使用してファイルのインポートを行います：

```python
def _import_pmx(self, file_path, options):
    from ..io.mmd_importer import import_mmd_file
    success = import_mmd_file(file_path)
```

これにより、コードの重複を避け、統一されたインポート処理を維持できます。

#### プラグイン登録

ファイルトランスレーターは、プラグインの初期化時に登録されます：

```python
def register_file_translators(plugin):
    plugin.registerFileTranslator(
        MmdFileTranslator.kPluginTranslatorTypeName,
        None,
        MmdFileTranslator.creator,
        None, None, True  # canBeOpened
    )
```

## 使用方法

### ユーザー向け

1. **インポート**: Maya の File > Import で MMD ファイルを選択
2. **エクスポート**: Maya の File > Export で MMD 形式を選択
3. **開く**: PMX/PMD ファイルを File > Open でシーンとして開く

### 開発者向け

ファイルトランスレーターを拡張する場合：

1. 新しいオプションを `_parse_options()` メソッドで解析
2. エクスポート機能を対応するエクスポーターモジュールで実装
3. エラーハンドリングとユーザーフィードバックを適切に実装

## テスト

`test_plugin.py` を使用してファイルトランスレーターが正しく登録されているかテストできます：

```python
# Maya Script Editor で実行
exec(open("/path/to/maya_mmd_tools/test_plugin.py").read())
```

## 今後の拡張

- オプションダイアログの追加
- 進捗表示の改善
- バッチ処理サポート
- プレビュー機能の実装
