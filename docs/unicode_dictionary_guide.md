# Unicode文字列辞書ファイルの設定ガイド

## 概要

maya_mmd_toolsでは、MMDの多言語名（日本語・中国語・韓国語等）をMaya互換のASCII名に変換するための辞書をJSONファイルで設定できます。

このシステムは以下の特徴があります：
- **言語に依存しないフラット構造**: どの言語も同じ辞書で管理
- **ハッシュフォールバック**: 辞書にない文字列は自動的にハッシュ化
- **Maya安全名保証**: 変換結果は必ずMayaで使用可能な文字のみ
- **双方向変換**: 変換した名前を元の文字列に復元可能

## 要件
MMDのエンコーディングは、CP932をやUTF-16LEなどマルチバイト文字が多用されていて、中国語や日本語にも対応しているが、MayaではASCII文字しか使用できないため、多くの文字がエンコードエラーになってしまいます。

要件としては、

- 日本語、中国語、特殊文字のASCII変換・復元
- エクスポート時に日本語、簡体字、繁体字、英語と選べる
- 追加の変換マップをユーザーが設定可能

## 実装方針

これらを解決するために、
- パース時点では、CP932をUTF-8かUTF-16LEにしてメモリに保持
- ExtraAttributeにはUTF-8が使えるので変換前の文字列の保持をする
- パスの解決はWindowsではShift-JIS を使用するため、範囲外の文字によるパスのエラーはユーザーに解決して貰う必要がある。
- UTFをASCIIに変換するためのロジックを、
- - 辞書マップによる固定変換（MMDでよく使われる文字の指定）
- - それ以外はprefix+ハッシュによる機械的な変換によって一意性と作業性の担保
- エクスポート時に、ExtraAttributeの値を参照する。

この手順によって対応する。

| ※pykakasiといった外部ライブラリは挙動が読みづらいのと、インストールの手間となるため排する。

## 辞書ファイルの場所

- **デフォルト辞書**: `mmd_tools/config/unicode_dictionary.json`
- **カスタム辞書**: `UnicodeToAsciiConverter(dictionary_path="path/to/your/dict.json")`でカスタム辞書を使用可能

## 辞書ファイルのパースの方針

### パースの流れ
1. 単語リストの処理：完全一致を探す
2. 数字の処理：全角数字を半角に変換
3. Prefix、Suffixの処理：
4. 辞書に無い文字列の処理：ハッシュ化して一意名を生成

### Prefix、Suffixについて

- 辞書にPrefixとSuffixを追加できる。
- 設定したものは、メインの辞書の処理
- 先頭の”左””右”は自動的に`left_`や`right_`に変換
- 最後の数字（全角数字含む）は自動的に`_{数字}`に変換。例: `左腕１` → `left_arm_1`

### パース例

- `左腕1` → `left_arm_1`
- `右腕2` → `right_arm_2`
- `上半身3` → `spine3`
- `左腕捩1` → `left_arm_twist_1`
- `右つまさきＩＫ先` → `right_toe_ik_end`
- `肩` → `shoulder`
- `肩P` → `shoulder_p`

## 辞書ファイルの構造

```json
{
  "_meta": {
    "version": "1.0",
    "description": "多言語対応Unicode→ASCII変換辞書",
    "last_updated": "2025-01-01"
  },
  "dictionary": {
    "ボーン": "bone",
    "左腕": "left_arm",
    "右腕": "right_arm",
    "头部": "head_cn",
    "手臂": "arm_cn",
    "다리": "leg_kr"
  },
  "maya_invalid_chars": {
    ":": "_colon_",
    " ": "_space_",
    "-": "_dash_",
    ".": "_dot_",
    "|": "_pipe_"
  }
}
```

### 各フィールドの説明

- **`_meta`**: 辞書のメタデータ（任意、管理用）
- **`dictionary`**: Unicode文字列→ASCII文字列の変換辞書（すべての言語を同一階層で管理）
- **`maya_invalid_chars`**: Mayaで無効な文字の置換ルール（オプション）

## 使用方法

### 1. 基本的な使い方（utils.pyのAPIを使用）

```python
from mmd_tools.core import utils

# Unicode文字列をMaya安全名に変換
converted = utils.convert_utf8_to_ascii("ボーン")
print(converted)  # "bone"

converted = utils.convert_utf8_to_ascii("头部")
print(converted)  # "head_cn"

# 辞書にない文字列は自動的にハッシュ化
converted = utils.convert_utf8_to_ascii("未知の文字列")
print(converted)  # "#5L2g5a6a"


```

### 2. 辞書エントリの追加

```python
# メモリ内に追加（一時的）
utils.add_dictionary_entry("新しいボーン", "new_bone")

# ファイルに保存（永続的）
utils.save_custom_dictionary_entry("新しいボーン", "new_bone")
```

### 3. 直接UnicodeToAsciiConverterを使用（上級者向け）

```python
from mmd_tools.core.unicode_converter import UnicodeToAsciiConverter

# デフォルト辞書を使用
converter = UnicodeToAsciiConverter()

# カスタム辞書を使用
converter = UnicodeToAsciiConverter(dictionary_path="path/to/custom_dict.json")

# 変換実行
converted = converter.convert("ボーン")
```
### 4. バッチ変換

```python
# 複数の文字列を一度に変換
names = ["ボーン", "头部", "未知の名前"]
converted_batch = utils.convert_utf8_to_ascii_batch(names)
print(converted_batch)  # ["bone", "head_cn", "utfb64_..."]

# 復元もバッチで実行可能
restored_batch = utils.restore_ascii_to_utf8_batch(converted_batch)
print(restored_batch)  # ["ボーン", "头部", "未知の名前"]
```

### 5. 辞書のリロードとエクスポート

```python
# 辞書をリロード（ファイルの変更を反映）
utils.reload_dictionary("path/to/custom_dictionary.json")

# 現在の辞書をファイルに保存
utils.export_dictionary("exported_dictionary.json")
```

## 辞書エントリの命名規則

### 推奨する命名パターン

- **一般的な規則**: 分かりやすい英語名を使用（日本語: `bone`, 中国語: `head_cn`, 韓国語: `leg_kr` など）
- **ボーン系**: `bone_name` (例: `left_arm`, `head`, `center`)
- **モーフ系**: `morph_name` (例: `smile`, `blink`, `a_sound`)
- **材質系**: `material_name` (例: `skin`, `hair`, `eye`)

### 注意事項

- 英語名は **ASCII文字のみ** を使用してください
- Mayaで無効な文字（`:`, ` `, `-`, `.`, `|`）は自動的に置換されます
- 英語名はMayaのノード名として使用されるため、わかりやすい名前にしてください


## カスタム辞書ファイルの編集例

### 1. 新しいボーンを追加

```json
{
  "dictionary": {
    "カスタムボーン": "custom_bone",
    "特殊IK": "special_ik",
    "補助ボーン": "helper_bone"
  }
}
```

### 2. 多言語対応のモーフを追加

```json
{
  "dictionary": {
    "特殊表情": "special_expression",
    "カスタム笑い": "custom_smile",
    "オリジナルポーズ": "original_pose",
    "微笑": "smile_cn",
    "惊讶": "surprise_cn"
  }
}
```

### 3. Maya無効文字の追加

```json
{
  "maya_invalid_chars": {
    "#": "_hash_",
    "%": "_percent_", 
    "&": "_and_",
    "@": "_at_"
  }
}
```

### 間違った例
```json
{
  "dictionary": {
    "ボーン１": "bone",
    "左腕": "left arm",  // スペースはMaya無効文字
    "右腕": "right-arm", // ハイフンはMaya無効文字
    "左親指１": "left_thumb_1", // 数字は自動的に_#に変換される
    "左髪ＩＫ先": "left_hair_ik_end", // 先頭の
    "手臂": "arm_cn"
  }
}
```

## トラブルシューティング

### よくある問題と解決方法

1. **辞書ファイルが読み込まれない**
   - ファイルパスが正しいか確認
   - JSONの構文エラーがないか確認
   - ファイルのエンコーディングがUTF-8になっているか確認

2. **変換が期待通りに動作しない**
   - 辞書の再読み込みを実行: `utils.reload_dictionary()`
   - キャッシュをクリア: `converter.clear_cache()`（直接UnicodeToAsciiConverterを使用している場合）

3. **Mayaで名前が表示されない**
   - Maya無効文字が自動変換されているか確認
   - ASCII文字のみを使用しているか確認

### 高度な機能とデバッグ

```python
from mmd_tools.core.unicode_converter import UnicodeToAsciiConverter

# デバッグ情報を確認
converter = UnicodeToAsciiConverter()

# 辞書の内容を確認
print("登録済み辞書エントリ数:", len(converter.unicode_to_ascii))

# 変換キャッシュをクリア
converter.clear_cache()

# 一意名生成のテスト
unique_name = converter.get_unique_name("bone", existing_names=["bone", "bone_1"])
print(unique_name)  # "bone_2"
```

## 変換の内部動作

### 変換プロセス

1. **辞書検索**: まず登録済み辞書で完全一致を検索
2. **ハッシュフォールバック**: 辞書にない場合は`#`プレフィックス付きでハッシュ化し8文字に切り詰める
3. **Maya安全化**: 結果をMayaで使用可能な文字のみに変換

### ハッシュ化について

```python
# 辞書にない文字列
unknown = "未知の名前"
converted = converter.convert(unknown)
# 結果: "#5L2g5a6a"
```

切り詰める文字数によって、一意性を担保できる確率が変わります。
8文字で 16^8 = 4,294,967,296 通りの組み合わせが可能です。
4文字で、 16^4 = 65,536 通りの組み合わせが可能です。

### 変換の確認

```python
# 辞書情報の確認
info = utils.get_dictionary_info()
print(info)

# 変換方式の確認
from mmd_tools.core.name_converter import get_converter
converter = get_converter()
encoding_type = converter.get_encoding_type("変換済み名前")
print(encoding_type)  # "dictionary", "hash", "original"

# 変換統計の確認
stats = converter.get_conversion_stats(["name1", "name2", "name3"])
print(stats)
```

## 注意事項

- 辞書ファイルの変更後は、Mayaプラグインの再読み込みが必要な場合があります
- 大量の辞書エントリを追加すると、変換処理が遅くなる可能性があります
- 辞書にない日本語名は自動的にハッシュ化されます
- カスタム辞書ファイルはバックアップを取ることを推奨します
