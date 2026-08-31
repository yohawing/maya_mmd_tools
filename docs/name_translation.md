# MMD名前翻訳プリセット

`MMD > Tools > Translate MMD Names` では、CSV辞書を指定してPMXの日本語名から
`EnglishName` を設定できます。配布物には、主要な標準・準標準ボーン、IK、指、
ツイスト、目・髪・補助ボーン、衣装・アクセサリー・材質・物理補助語、およびよく使われる表情モーフを収録した
`mmd_tools/config/name_translation_presets/mmd_standard_names.csv` が含まれます。

CSVはUTF-8の2列形式です。1列目が元の日本語名、2列目が設定する英語名で、先頭の
`Japanese,English` ヘッダーは省略できます。ダイアログの`Dictionary CSV`には、この
同梱プリセットのパスが初期表示されます。元のPMX名は変更されず、翻訳対象にない名前は
そのまま残ります。モデル固有のボーン名、材質名、すべての表情・補助ボーンを網羅する
辞書ではないため、必要に応じてCSVをコピーして行を追加してください。

2026-08-31時点の追加語彙は、[Hogarth-MMD/mmd_tools_translation の
translations.csv](https://github.com/Hogarth-MMD/mmd_tools_translation/blob/master/translations.csv)
を候補コーパスとして照合し、既存のレビュー済み行を優先して選別・正規化したものです。
コメント、人物・モデル・製品名、ファイル名、文、露骨な語彙、曖昧な略語、および自動継承できる
ASCII接尾辞の派生名は除外しています。コーパスをそのまま複製したものではありません。

プリセットを使うときは、Translate MMD Names の `Browse…` から上記CSVを選び、まず
`Preview` で対象と未登録名を確認してから適用します。CSVのキーは完全一致なので、
`左足IK` と `左足ＩＫ` のような表記違いは別行として登録されています。
`スカート,Skirt`のような行は、末尾が半角英数字の`スカート_1`、`スカート_02`、
`スカート_L`、`スカート_left`などにも自動継承され、接尾辞の大文字・小文字は保持されます。
複数の接尾辞（`スカート_left_02`）も対象です。派生名をCSVへ明示すれば、その完全一致の
翻訳が優先されます。ダイアログで `Use exact CSV matches only` を有効にすると、
この自動継承を無効にして完全一致のキーだけを使えます。
