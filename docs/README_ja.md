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
| マテリアル・テクスチャ | 🔶 | DX11・OpenGLシェーダーによるMMDトゥーンシェーダーの実装。VP2.0は再現性が低いです。  |
| Maya向け名前解決 | ✅ | ASCII文字に変換。 また日本語・中国語パスのテクスチャを安全なパスに自動解決します。 |
| UV | 🔶 |プライマリUVは対応済み。　追加 UV（UV1–4）未対応 |
| エッジ／輪郭フラグ | 🔶 | 描画順の問題があるため、オプションでONにできます。 |
| ボーン・スケルトン | 🔶 | 複雑なモデルでは既知の問題があります。 |
| リグ（IK・付与・ローカル軸 ） | 🔶 | 一部対応。複雑なモデルでは既知の問題があります。 |
| 表示枠 | 🔶 | Development ModeのPMX round-trip用metadataとして保持。専用編集UIは未対応。 |
| モーフ（頂点・ボーン・マテリアル・グループ・UV） | 🔶 | 頂点・ボーンモーフ対応済み。マテリアル、UV、Flip、Impulse モーフは未対応です。 |
| 剛体・ジョイント | ⛔  | 未対応 |
| ソフトボディ（PMX 2.1） | ⛔ | 非対応 |
| HumanIK | ⛔  | 未対応 |
| エクスポート | ⛔ | 未対応 |

### アニメーション（VMD）

| 機能 | 状態 | 備考 |
|---|---|---|
| ボーンアニメーション | 🔶 | MayaDGでのMMDリグに対応しています。 ベイクモードは [mmd-anim](https://github.com/yohawing/mmd-anim) による最終姿勢ベイクです。 |
| VPD | ✅ | ドラッグ＆ドロップのみの対応 |
| モーフアニメーション | 🔶 | 頂点・ボーンモーフ対応済み。マテリアル,UV、Flip、Impulse モーフ未対応です。 |
| カメラアニメーション | ✅ | `mmd_camera` を作成・キー設定 |
| 照明アニメーション | ✅ | `mmd_light` コントローラを駆動 |
| IK オン／オフフレーム | 🔶 | インポート・ベイクに対応。ランタイムベイクでは最終姿勢に反映され、リグモードでは `mmdCcdIk.enabled` にキーを設定します。 |
| 物理 | 🔶 | ベイクモードのみ対応 |
| エクスポート | ⛔ | 未対応 |

## 既知の制限

- **エクスポートは未対応です。** 現時点では PMX/PMD/VMD の読み込み用ツールです。
- **リグモードは複雑なモーションの一致性が未保証です。** sparse key と live `mmdCcdIk` / `mmdAppend` ノードを保持して編集しやすくしますが、jointOrient、IK、付与、ローカル軸を含むケースでは、ベイクモードや MMD のメッシュ変形と完全には一致しない場合があります。

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

インストーラはMaya MMD Tools本体のファイル一式をMayaのユーザー `modules` フォルダへコピーし、その隣に `maya_mmd_tools.mod` を作成します。

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

※テクスチャがマルチバイト文字によって読み込めない場合は、テクスチャを自動修復をONにしてください。自動で読み込める名前のテクスチャに自動でコピーされ使用されます。

### アニメーションをインポートする

1. Import/ExportタブでVMDファイルを選択します。
2. （任意）アニメーションのインポート設定で VMD FPS（30 または 60、既定は 30）を指定します。インポート前に Maya シーンの時間単位が変更されます。
3. `Import Animation` をクリックします。
4. シーン内の対応するモデルにアニメーションが適用されます。

※MMDファイルをMayaのビューポートへドラッグ＆ドロップしてインポートすることもできます。（実験機能です）

Maya の `File > Import` は MMD ファイルの対応導線ではありません。Import/Export タブまたはドラッグ＆ドロップを使用してください。

## ビューポートの設定

MMDのトゥーン表現を再現するシェーダーは、MMDシェーダーの作成オプションと、レンダリング環境に応じたシェーダープラグインを有効にすることで確認出来ます。Windows 環境では `dx11Shader` プラグイン、MacOS 環境では `glslShader`（GLSLShader）プラグインを使用します。
また、インポート時に以下の設定が自動で適用されます。

- **レンダリング空間** → `ACEScg`　→ `scene-linear Rec.709-sRGB`。
- **ビュー変換（View Transform）** `ACES 1.0 SDR-video (sRGB)` → `Un-tone-mapped (sRGB)`

いずれもMMDらしい色調（sRGBガンマ空間入出力）を再現する目的での適用になります。

## サポート

問題が解決しない場合は、以下の情報を添えて [GitHub Issues](https://github.com/yohawing/maya_mmd_tools/issues) で報告してください。
