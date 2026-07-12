# Maya MMD Tools ユーザードキュメント

[English](../README.md)

Maya MMD Toolsは、Autodesk MayaでMikuMikuDance (MMD) のPMD/PMXモデルとVMDモーションを読み込むためのツールです。

MMDのリグの再現及び、アニメーションのインポート・編集・書き出しの一連の流れを実現することを目的としています。

## 機能サポート一覧

凡例: ✅ 対応 · 🔶 一部対応／制限あり · 🧪 試験的 · ⛔ 未対応

> 本ツールはアルファ版です。詳細は下記の[既知の制限](#既知の制限)を参照してください。

### インポート — モデル　（PMX,PMD）

| 機能 | 状態 | 備考 |
|---|---|---|
| メッシュ | ✅ | |
| マテリアル・テクスチャ | 🔶 | Dx11シェーダーによるMMDトゥーンシェーダーの実装。描画順をコントロールできないため、半透明マテリアルは再現性が低いです。 また日本語・中国語パスのテクスチャを安全なパスにFallbackします。 |
| Maya向け名前解決 | ✅ | ASCII文字に変換 |
| プライマリ UV | ✅ | |
| 追加 UV（UV1–4） | ⛔ | 未対応 |
| エッジ／輪郭フラグ | 🔶 | 描画順の問題があるため、オプションでONにできます。 |
| ボーン・スケルトン | 🔶 |　複雑なモデルでは既知の問題があります。 |
| リグ（IK・付与・ローカル軸 ） | ✅ | 対応 |
| 表示枠 | 🔶 | Development ModeのPMX round-trip用metadataとして保持。専用編集UIは未対応。 |
| モーフ（頂点・ボーン・マテリアル・グループ・UV） | 🔶 | 頂点・ボーン・マテリアル・グループの操作に対応。マテリアルモーフはMMD hardware shaderの全parameterを一括駆動し、不完全なbackendではfail closed。UV・Flip・Impulseは未対応。 |
| 剛体・ジョイント | 🔶 | シーン上のpreview物理は未対応。VMD物理はopt-inのnative runtimeベイクを利用。 |
| ソフトボディ（PMX 2.1） | ⛔ | 非対応 |
| HumanIK | 🧪 | Boneタブからインポート済みMMDスケルトンのHumanIK定義/control rigを作成（実験的） |
| エクスポート | ⛔ | 未対応 |

### アニメーション（VMD）

| 機能 | 状態 | 備考 |
|---|---|---|
| ボーンアニメーション | 🔶 | ベイクモードは [mmd-anim](https://github.com/yohawing/mmd-anim) による最終姿勢ベイクです。リグモードは編集しやすい sparse key と live MMD リグノードを保持しますが、複雑なモーションでは試験的です。 |
| モーフアニメーション | 🔶 | 頂点・ボーン、およびcomplete hardware shader経路のマテリアルモーフに対応。 |
| カメラアニメーション | ✅ | `mmd_camera` を作成・キー設定 |
| 照明アニメーション | ✅ | `mmd_light` コントローラを駆動 |
| IK オン／オフフレーム | 🔶 | インポート・ベイクに対応。ランタイムベイクでは最終姿勢に反映され、リグモードでは `mmdCcdIk.enabled` にキーを設定します。 |
| エクスポート | ⛔ | 未対応 |

## 既知の制限

- **エクスポートは未対応です。** 現時点では PMX/PMD/VMD の読み込み用ツールです。
- **VPD ポーズはドラッグ＆ドロップで適用します。** 選択中の MMD モデル、または同時にドロップした PMX/PMD モデルに対して、現在フレームへポーズを適用してキーを作成します。
- **追加 UV / multi-UV は適用されません。**
- **マテリアルモーフのshader runtime結線はcomplete-or-noneです。** diffuse/alpha、specular、ambient、edge color/size、texture/sphere/toon factorを一体で接続し、不完全なbackendは変更せず警告します。
- **UV、Flip、Impulse モーフは未対応です。** 頂点・ボーン・マテリアル・グループの操作に対応します。
- **シーン上の物理previewは未対応です。** native physics motion bakeのみopt-inで利用できます。Development ModeのPhysicsタブは将来backend用に残しています。
- **表示枠はPMX round-trip用に保持されますが、専用編集UIは未対応です。**
- **VMD の忠実度を優先する場合はベイクモードを使用してください。** ベイクモードは `mmd-anim` runtime の最終姿勢を焼き込みます。
- **リグモードは複雑なモーションの一致性が未保証です。** sparse key と live `mmdCcdIk` / `mmdAppend` ノードを保持して編集しやすくしますが、jointOrient、IK、付与、ローカル軸を含むケースでは、ベイクモードや MMD のメッシュ変形と完全には一致しない場合があります。
- **HumanIK セットアップには有効な full-body skeleton が必要です。** Boneタブの実行時に Maya が control rig を作成できない場合はエラーを表示します。
- **Maya の `File > Import` は今回のリリースの対応導線ではありません。** MMD Tools UI またはドラッグ＆ドロップ import を使用してください。
- 大規模モデルでは処理が重くなることがあり、一部の PMX ファイルはインポートに失敗する場合があります。

## システム要件

### 必須要件

- **Maya**: 2024以降
- **OS**: Windows 11 / macOS 15.6
- **Python**: 3.10以降（Maya 2024以降に同梱）

## インストール

### ファイルのダウンロード

1. [GitHubリリースページ](https://github.com/yohawing/maya_mmd_tools/releases)から最新版をダウンロードします。
2. ZIPファイルを一時フォルダへ解凍します。

### ドラッグ＆ドロップでインストール

1. Mayaを起動します。
2. 解凍したフォルダ内の `drag_drop_install.py` をMayaのビューポートへドラッグ＆ドロップします。
3. インストール確認ダイアログを確認します。
4. Mayaを再起動します。

インストーラはMaya MMD Tools本体のファイル一式をMayaのユーザー `modules` フォルダへコピーし、その隣に `maya_mmd_tools.mod` を作成します。`maya_mmd_tools.mod` にはZIPに同梱されているMayaバージョン分のエントリが書き込まれるため、1つのMayaバージョンからインストールすれば、同じコピーを他の同梱バージョンのMayaからも参照できます。
Maya起動時にプラグインが読み込まれることを確認した後は、一時的に解凍したフォルダを削除して構いません。

Windowsでは、以下の場所へコピーされます。

```text
C:\Users\<ユーザー名>\Documents\maya\modules\maya_mmd_tools
```

macOSでは、以下の場所へコピーされます。

```text
~/Documents/maya/modules/maya_mmd_tools
```

生成されるモジュールファイルは、その隣に作成されます。

```text
C:\Users\<ユーザー名>\Documents\maya\modules\maya_mmd_tools.mod
```

### プラグインの有効化

1. Mayaを起動します。既に起動している場合は再起動します。
2. `Window > Settings/Preferences > Plug-in Manager` を開きます。
3. `mmd_tools_plugin.py` を探します。
4. `Loaded` にチェックを入れます。
5. 自動読み込みしたい場合は `Auto load` にもチェックを入れます。

## インストールの確認

### メニューで確認

Mayaのメニューバーに `MMD > MMD Tools` が追加されていることを確認します。

## クイックスタート

### MMD Tools UIを開く

1. `MMD > MMD Tools` を選択します。
2. MMD Tools UIウィンドウが開きます。
3. 各タブで設定を確認・調整できます。

主なタブ:

- **Info**: モデル情報の確認
- **Material**: マテリアル設定
- **Morph**: 表情の調整
- **Bone**: ボーン情報

### モデルをインポートする

1. Import/ExportタブでPMXまたはPMDファイルを選択します。
2. `Import Model` をクリックします。

インポートが成功すると、アウトライナーに `model_root` グループが作成され、ビューポートにモデルが表示されます。マテリアルとテクスチャも自動的に適用されます。
テクスチャがマルチバイト文字によって読み込めない場合は、テクスチャを自動修復をONにしてください。自動で読み込める名前のテクスチャに自動でコピーされ使用されます。

### アニメーションをインポートする

1. Import/ExportタブでVMDファイルを選択します。
2. （任意）アニメーションのインポート設定で VMD FPS（30 または 60、既定は 30）を指定します。インポート前に Maya シーンの時間単位が変更されます。
3. `Import Animation` をクリックします。
4. シーン内の対応するモデルにアニメーションが適用されます。

※MMDファイルをMayaのビューポートへドラッグ＆ドロップしてインポートすることもできます。（実験機能です）

Maya の `File > Import` は MMD ファイルの対応導線ではありません。Import/Export タブまたはドラッグ＆ドロップを使用してください。

## ビューポートの設定

MMDのトゥーン表現を再現するシェーダーは、MMDシェーダーの作成オプションと、`dx11Shader.dll`プラグインを有効にすることで確認出来ます。
また、インポート時に以下の設定が自動で適用されます。

- **レンダリング空間** → `ACEScg`　→ `scene-linear Rec.709-sRGB`。
- **ビュー変換（View Transform）** `ACES 1.0 SDR-video (sRGB)` → `Un-tone-mapped (sRGB)`

いずれもMMDらしい色調（sRGBガンマ空間入出力）を再現する目的での適用になります。

## サポート

問題が解決しない場合は、以下の情報を添えて [GitHub Issues](https://github.com/yohawing/maya_mmd_tools/issues) で報告してください。
