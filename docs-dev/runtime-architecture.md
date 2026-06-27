# mmd-anim Runtime 統合アーキテクチャ

このドキュメントは、maya_mmd_tools に mmd-anim (https://github.com/yohawing/mmd-anim) を導入して「本物の MMD アニメーションランタイム」を実現するための設計と進め方を記述します。

## 背景と課題

現在の VMD インポート / アニメーション変換 (主に `mmd_tools/converters/vmd_converter.py`) には以下のような限界があります。

- VMD に含まれる MMD 特有のベジェ補間データ (64 バイト/ボーンキー) を完全に無視（フェーズ1では線形補間のみ）。
- 付与変形 (append / grant) と IK の組み合わせ評価が Maya の constraints / ikHandle による近似に留まり、評価順序・ループ回数・ローカル付与などで本家 MMD と差異が出やすい。
- 座標変換 (Z 反転 + Quaternion 調整) のロジックが `apply_rotation` や `get_parent_world_rotation` などに散在し、保守が難しい。
- RigConverter での PMX IK 構築が一部コメントアウト状態。

これにより「インポートしたモーションが本家と動きが違う」という既知の問題が発生しています。

mmd-anim はこの問題を根本から解決するための**共有アニメーション評価基盤**です。
Rust で実装されたコアが PMX + VMD を読み込み、任意フレームで MMD 忠実な姿勢 (ワールド行列、スキニング行列、モーフ、IK 状態) を計算します。

## 目標

- **一次目標 (ユーザークラリフィケーション)**: Maya シーン内で mmd-anim により**ライブ評価**を行い、ベイク不要で正確な MMD アニメーションを再生できるカスタムノード/機構を提供する。
- 副次的には、高精度ベイクパスも提供し、既存ワークフローとの互換性を保つ。
- 事前ビルドのネイティブライブラリを同梱することで、通常のユーザーが Rust をインストールしなくても利用可能にする。

## 全体アーキテクチャ

```
[ Maya シーン ]
    │
    ▼
mmd_tools Python レイヤー
    ├── io / converters (既存のモデルインポート + VMD ベイクパスは維持)
    └── core/native/
            └── mmd_anim_runtime.py          ← ctypes ラッパー (Phase 0 で実装)
                    │
                    ▼  (オプショナル)
            mmd_runtime_ffi.dll / .dylib     ← mmd-anim-ffi (事前ビルド同梱)
                    │
                    ▼
            Rust mmd-anim-runtime コア (評価エンジン)

将来 (Phase 2+)
    cpp/  (Maya C++ プラグイン)
        └── カスタムノード (MMDRuntimeInstance など)
              Maya DG 評価時に mmd-anim を呼び出し、ジョイントやメッシュを駆動
```

### 重要な設計原則

1. **オプショナル完全フォールバック**
   - ネイティブライブラリが存在しなくても Maya プラグイン全体が動作する。
   - `is_mmd_runtime_available()` が False の場合は従来の Python パスを使用。

2. **生データ優先**
   - 可能な限り PMX/VMD の**生バイト列**を mmd-anim に渡す (`create_from_pmx_bytes` / `create_from_vmd_bytes_for_model`)。
   - Python 側のパーサで一旦構造体にした後、再構築する手間を避ける。

3. **事前ビルド優先**
   - 開発者・ユーザーに cargo / Rust ツールチェーンを要求しない。
   - リリース時には `mmd_tools/native/win64/mmd_runtime_ffi.dll` などを同梱。

4. **C++ との分離**
   - 最初は Python + ctypes で PoC とベイクを実現。
   - 本格ライブ評価は C++ Maya プラグイン (`cpp/src/`) で実装 (既存の幻のスキャフォールディングを本実装化)。

## データフロー (ライブ評価時)

1. モデルインポート時に PMX 生バイトをモデル root のカスタム属性 (または内部保持) に保存。
2. VMD インポート時または UI 操作で VMD 生バイト + モデルから `MmdRuntimeClip` を作成。
3. `MmdRuntimeInstance` を生成し、Maya の時間 (フレーム) に応じて `evaluate_clip_frame(frame)` を呼ぶ。
4. 得られた行列・モーフウェイト・IK 状態を Maya ジョイント / blendShape / カスタムデフォーマに適用。
5. (将来) C++ ノードが DG 評価のタイミングで上記を自動実行。

## 現在の Python 実装との関係

- `BoneConverter` / `RigConverter` で作成されるジョイント階層・skinCluster・blendShape は基本的に維持。
- ライブ使用時は、付与ボーン用の `orientConstraint` / `pointConstraint` や IK ハンドルを「参考情報」または「無効化可能」として扱う。
- VmdConverter は「レガシーベイクパス」として残す。新規に native 利用時の高精度ベイクロジックを追加。

## 制限事項 (mmd-anim 仕様に由来)

- 物理演算 (剛体・ジョイントのシミュレーション) は行われない。データ読み書きは可能だが、揺れものはホスト側 (Maya の nHair / nCloth など) で対応。
- VMD のカメラ・照明データはモデル用クリップとは別。既存のカメラ/ライト変換ロジックは当面維持。
- mmd-anim は評価段階 (0.1.x)。API が将来的に変更される可能性あり → ABI バージョン確認とラッパーでの隔離を実施。

## 開発・ビルド手順 (Phase 0 時点)

### Python ラッパーのみを使う場合 (すぐに試せる)
```powershell
# native ライブラリがなくても import 自体は成功する
python -c "from mmd_tools.core.native import is_mmd_runtime_available; print(is_mmd_runtime_available())"
```

### ネイティブライブラリを自分で用意する場合
`mmd-anim` は `external/mmd-anim` に Git submodule として配置します。
submodule はソースを取得するだけなので、`git submodule update` だけでは `mmd_runtime_ffi.dll` は生成されません。

開発時は submodule 内で `mmd-anim-ffi` をビルドし、Python wrapper が `external/mmd-anim/target/release/` の成果物を直接ロードする運用を基本とします。
配布時だけ、生成済み DLL を `mmd_tools/native/win64/` にコピーして同梱します。

```powershell
# 初回取得、または submodule が未初期化の環境
git submodule update --init --recursive

# 開発用 DLL を生成
cd external/mmd-anim
cargo build -p mmd-anim-ffi --release
cd ..\..
```

Python wrapper (`mmd_tools/core/native/mmd_anim_runtime.py`) の検索優先順は以下です。

- `MMD_ANIM_FFI_PATH` による明示指定
- `mmd_tools/native/win64/` などの配布用 native ディレクトリ
- `external/mmd-anim/target/release/` の開発用ビルド成果物
- カレントディレクトリ、`plug-ins`

このため、開発中は `external/mmd-anim/target/release/mmd_runtime_ffi.dll` が存在すればコピーなしでロードできます。
リリースパッケージではユーザーに Rust を要求しないため、次のように native ディレクトリへコピーしてから配布します。

```powershell
copy external\mmd-anim\target\release\mmd_runtime_ffi.dll mmd_tools\native\win64\
```

ロード確認は Maya 2024 の `mayapy` で行います。

```powershell
& "C:\Program Files\Autodesk\Maya2024\bin\mayapy.exe" -c "import sys; sys.path.insert(0, r'F:\Develop\maya_mmd_tools'); from mmd_tools.core.native.mmd_anim_runtime import is_mmd_runtime_available, get_runtime_library_path, get_mmd_runtime_library; print(is_mmd_runtime_available()); print(get_runtime_library_path()); lib = get_mmd_runtime_library(); print(lib.mmd_runtime_abi_version() if lib else 'SKIP')"
```

開発用 DLL が正常にロードされた場合の期待値は以下です。

```text
True
F:\Develop\maya_mmd_tools\external\mmd-anim\target\release\mmd_runtime_ffi.dll
1
```

DLL がない環境では `False`、`None`、`SKIP` になります。
これは異常ではなく、native が存在しない場合に従来パスへフォールバックするための設計です。

C++ ライブ評価ノードは Python wrapper と同じ検索パスを必ずしも使わないため、`mmdRuntimeInstance` の MayaBatch 検査では DLL 配置または C++ 側ローダーの検索パスを別途確認してください。

### C++ プラグインをビルドする場合 (Phase 2 以降)
- `cpp/src/CMakeLists.txt` を用いて Maya バージョン別の .mll を生成。
- mmd_runtime_ffi.dll とリンク (または動的ロード)。

## テスト戦略

- ユニットテスト: ラッパーのロード・バージョン確認・フォールバック挙動。
- 統合テスト: 実在するテストフィクスチャ (Lat式ミク、mmt_test_model など) を使って、native 利用時に特定フレームのボーン位置やモーフ値が期待通り (または従来より改善) であることを確認。
- 黄金データ比較: 将来的に mmd-anim 側の CLI や合成テストケースと突き合わせ。

native ライブラリが存在しない環境でも全テストがパスするよう、必ず `if is_mmd_runtime_available(): ... else: self.skipTest(...)` または警告ログでスキップする。

## フェーズ進行計画 (概要)

- **Phase 0**: サブモジュール + Python FFI ラッパー骨組み + レイアウト + 基本ドキュメント + C++ 最小スケルトン。
- **Phase 1**: 高精度ベイクパス (VMD インポート改善)。
- **Phase 2**: C++ によるライブ評価ランタイムノードの本実装。
- **Phase 3**: UI 統合、検証、ドキュメント・リリースプロセス整備。
- **Phase 4**: 安定化・追従・拡張。

各フェーズの終了時には人間による明示的な承認を得てから次に進みます。

## 参考

- mmd-anim 本家ドキュメント (external/mmd-anim/README.md および docs/)
- `mmd_tools/core/native/mmd_anim_runtime.py` (実装)
- `mmd_tools/native/README.md` (バイナリ配置)
- 既存: `docs-dev/architecture.md`, `spec-vmd.md`, `spec-pmx.md`
- FFI ヘッダ: `external/mmd-anim/crates/mmd-anim-ffi/include/mmd_runtime.h`

---

このドキュメントは実装の進行に合わせて随時更新してください。
新規ファイル作成時は冒頭に目的を簡潔に記述するルールに従っています。
