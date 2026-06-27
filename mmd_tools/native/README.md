# mmd-anim Runtime Native Components

このディレクトリは **mmd-anim** (https://github.com/yohawing/mmd-anim) が提供する
C ABI 共有ライブラリ (`mmd_runtime_ffi`) を配置するための場所です。

## 目的

- Maya 内で MMD アニメーションを **高精度・忠実に評価** するためのバックエンドを提供。
- 現在の Python 実装では再現が難しい以下の機能を mmd-anim の Rust コアで肩代わり：
  - MMD 特有のベジェ補間 (VMD 64 バイト補間データ)
  - 付与変形 (append transform) + IK ソルバの正確な組み合わせ評価
  - 任意フレーム (小数フレーム含む) のワールド行列 / スキニング行列 / モーフウェイト / IK 状態取得

## 配置レイアウト

```
mmd_tools/native/
├── README.md               # このファイル
├── win64/
│   └── mmd_runtime_ffi.dll # ← ここに Windows 用事前ビルドを置く (必須)
├── macos/
│   └── libmmd_runtime_ffi.dylib (または mmd_runtime_ffi.dylib)
└── linux/
    └── libmmd_runtime_ffi.so
```

Python ラッパー (`mmd_tools/core/native/mmd_anim_runtime.py`) は上記パスを優先的に検索します。
環境変数 `MMD_ANIM_FFI_PATH` で明示的に指定することも可能です。

## 事前ビルドバイナリの入手方法 (ユーザーは Rust 不要)

### 推奨 (将来のリリース時)
- 公式リリース ZIP に `mmd_runtime_ffi.dll` が同梱される予定です。
- インストール後、 `mmd_tools/native/win64/` 以下に DLL が存在していれば自動で利用されます。

### 開発者 / メンテナが自分でビルドする場合

1. Rust ツールチェーンをインストール (`rustup` 推奨)。
2. mmd-anim リポジトリをクローン (本リポジトリでは `external/mmd-anim` にサブモジュールとして追加済み)。
3. 以下を実行して cdylib をビルド：

   ```powershell
   # Windows (x64) の場合
   cd external/mmd-anim
   cargo build -p mmd-anim-ffi --release

   # 成果物例
   # target/release/mmd_runtime_ffi.dll
   ```

4. 生成された `mmd_runtime_ffi.dll` を `maya_mmd_tools/mmd_tools/native/win64/` にコピー。

   ```powershell
   copy target\release\mmd_runtime_ffi.dll ..\..\mmd_tools\native\win64\
   ```

macOS / Linux も同様 (`cargo build -p mmd-anim-ffi --release`)。

**注意**: mmd-anim-ffi は 0.1.x 系では crates.io に公開されていないため、ソースからのビルドが必要です。

## Python からの利用例

```python
from mmd_tools.core.native.mmd_anim_runtime import (
    is_mmd_runtime_available,
    MmdRuntimeModel,
    MmdRuntimeClip,
    MmdRuntimeInstance,
)

if is_mmd_runtime_available():
    model = MmdRuntimeModel.from_pmx_bytes(pmx_bytes)
    clip = MmdRuntimeClip.from_vmd_bytes_for_model(model, vmd_bytes)
    inst = MmdRuntimeInstance.for_model(model)

    inst.evaluate_clip_frame(clip, 120.5)  # 小数フレームも可

    world_mats = inst.get_world_matrices()   # List[List[float]] (ボーン数 x 16)
    morphs     = inst.get_morph_weights()
    ik_states  = inst.get_ik_enabled()
else:
    print("mmd-anim runtime は利用できません (フォールバックします)")
```

## 現在のステータス (Phase 0 時点)

- Python ラッパー骨組み完成 (ctypes + 安全なラップ + 自動フォールバック)
- 事前ビルド同梱を優先する設計
- ライブ評価ノード (C++ Maya プラグイン) のバックエンドとして将来的に使用予定
- 物理は非対応 (mmd-anim 仕様)。既存の physics_converter は別途使用してください。

## トラブルシューティング

- `is_mmd_runtime_available()` が False のまま
  - DLL が正しい場所にあるか確認
  - 64bit / Maya 同梱 Python と ABI が一致しているか
  - 依存 DLL (msvcrt など) が足りていない可能性 → Visual C++ 再頒布可能パッケージを確認

- クラッシュする場合
  - まず `get_mmd_runtime_library()` でロードできているかログを確認
  - ABI バージョン不一致の警告が出ていないか

## 関連ドキュメント

- `docs-dev/runtime-architecture.md` (作成中 / 作成予定)
- mmd-anim 本家 README (external/mmd-anim/README.md または https://github.com/yohawing/mmd-anim)
- AGENTS.md の「ロガーの使用方法」「ユーティリティークラスの使用方法」

この仕組みにより、将来的に「mmd-anim を導入した本物のランタイム」を Maya に持ち込む基盤が整います。
