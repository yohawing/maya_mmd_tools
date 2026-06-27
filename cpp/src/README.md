# C++ Maya Plugin Sources (Phase 2 in progress)

このディレクトリは maya_mmd_tools の C++ 側拡張 (mmd-anim を活用した**ライブランタイムノード**) のソースを置く場所です。

## 現在の状態 (Phase 2 進行中)

- `CMakeLists.txt` 更新済み (Maya devkit 検出 + mmd-anim-ffi ヘッダ対応 + 複数ソース)
- `pluginMain.cpp` で `mmdRuntimeInstance` ノードを登録
- `mmdRuntimeBridge.{h,cpp}` : mmd-anim-ffi の C++ ラッパー (model/clip/instance 管理 + evaluate/get matrices)
- `mmdRuntimeNode.{h,cpp}` : MPxNode スケルトン (time 入力 → compute で evaluate → 出力配列)

ビルドはまだ完全には通らない可能性が高い (Maya devkit + FFI DLL が必要) が、コード構造は整いつつある。

## 主要ファイル

- `pluginMain.cpp` — プラグイン初期化 / ノード登録
- `mmdRuntimeBridge.*` — FFI 呼び出しの C++ 薄い層 (ポインタ管理、エラー隠蔽)
- `mmdRuntimeNode.*` — ライブ評価ノード (将来的にジョイント/モーフを駆動)

## ビルド

`CMakeLists.txt` の先頭コメントを参照。Maya 2024/2025/2026 対応を想定。

事前ビルドの `mmd_runtime_ffi.dll` は `mmd_tools/native/win64/` などに置く。

## 次のステップ (Phase 2 残り)

- ノードのデータ入力 (PMX/VMD バイト or パス) の実装
- 出力アトリビュートを本物の matrix 配列 / float 配列に
- Python 側からのノード利用 or デフォーマ連携
- VMD インポート時の「ライブクリップアタッチ」統合
- リソース解放と Maya undo/redo 対応

## mmd-anim-ffi 連携

`external/mmd-anim/crates/mmd-anim-ffi/include/mmd_runtime.h` を使用。
Python wrapper (`mmd_tools/core/native/mmd_anim_runtime.py`) とセマンティクスを揃える。

## 注意

この C++ 部分はオプショナル。Python + FFI だけで bake パス (Phase 1) はすでに動作する。
ライブ評価が必要になったらこのノードを有効化する。
