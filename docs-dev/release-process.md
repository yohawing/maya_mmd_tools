# リリースプロセス

Maya MMD Tools のリリース手順。CI は lint + ZIP パッケージのみ。
C++ プラグインと Rust FFI ライブラリはローカルでビルドし ZIP に同梱する。

## 前提条件

### 開発マシン (Windows)

- Visual Studio 2022 (MSVC)
- CMake 3.20+
- Rust toolchain (`rustup`)
- Maya 2024 / 2025 / 2026 / 2027 の DevKit
- Python 3.7+ (`ruff` インストール済み)

### ビルドマシン (macOS)

- Xcode Command Line Tools
- CMake 3.20+
- Rust toolchain (`rustup`)
- Maya 2024 / 2025 / 2026 / 2027 の DevKit

---

## ステップ 1: コード品質確認

```bash
# テスト
python tests/run_tests.py --type unit

# lint (fix=true が pyproject にあるので --no-fix で確認のみ)
ruff check --no-fix .

# 未コミット変更がないこと
git status
```

---

## ステップ 2: バージョン番号更新

3 箇所を一致させる:

| ファイル | 場所 |
|---|---|
| `mmd_tools/__init__.py` | `__version__ = "X.Y.Z"` |
| `pyproject.toml` | `version = "X.Y.Z"` |
| `maya_mmd_tools.mod` | 各 `MAYAVERSION` 行のバージョン |

CHANGELOG.md に `[X.Y.Z]` セクションを追加。

---

## ステップ 3: C++ ビルド (ローカル)

### 3a. Rust FFI ライブラリ (`mmd_anim_ffi`)

各プラットフォームで `cargo build` を実行し、成果物を `mmd_tools/native/` にコピー。

#### Windows (x64)

```powershell
cd external/mmd-anim
cargo build -p mmd-anim-ffi --release

copy target\release\mmd_anim_ffi.dll ..\..\mmd_tools\native\win64\
```

#### macOS (arm64 / x86_64)

```bash
cd external/mmd-anim
cargo build -p mmd-anim-ffi --release

cp target/release/libmmd_anim_ffi.dylib ../../mmd_tools/native/macos/
```

#### Linux (x86_64) — 必要な場合のみ

```bash
cd external/mmd-anim
cargo build -p mmd-anim-ffi --release

cp target/release/libmmd_anim_ffi.so ../../mmd_tools/native/linux/
```

### 3b. Maya C++ プラグイン (`mmd_tools_cpp`)

Maya バージョンごとにビルド。DevKit のパスは環境に合わせる。

#### Windows — Maya 2024 / 2025 / 2026 / 2027

```powershell
# 各バージョンでくり返す (MAYA_VER = 2024, 2025, 2026, 2027)
$MAYA_VER = "2024"

cmake -S cpp/src -B build/cpp/maya${MAYA_VER} `
  -G "Visual Studio 17 2022" `
  -DMAYA_VERSION=${MAYA_VER} `
  -DMAYA_DEVKIT_ROOT="C:/Program Files/Autodesk/Maya${MAYA_VER}/devkit"

cmake --build build/cpp/maya${MAYA_VER} --config Release
```

成果物: `plug-ins/{MAYA_VER}/Release/mmd_tools_cpp.mll` + `mmd_anim_ffi.dll`

#### macOS — Maya 2024 / 2025 / 2026 / 2027

```bash
MAYA_VER=2024

cmake -S cpp/src -B build/cpp/maya${MAYA_VER} \
  -DMAYA_VERSION=${MAYA_VER} \
  -DMAYA_DEVKIT_ROOT="/path/to/Maya${MAYA_VER}/devkit"

cmake --build build/cpp/maya${MAYA_VER} --config Release
```

成果物: `plug-ins/{MAYA_VER}/Release/mmd_tools_cpp.bundle`

### 3c. ビルド成果物の確認

リリース ZIP に含めるべきファイル一覧:

```
mmd_tools/native/win64/mmd_anim_ffi.dll
mmd_tools/native/macos/libmmd_anim_ffi.dylib

plug-ins/2024/Release/mmd_tools_cpp.mll   (Windows)
plug-ins/2024/Release/mmd_tools_cpp.bundle (macOS)
plug-ins/2025/Release/...
plug-ins/2026/Release/...
plug-ins/2027/Release/...
```

---

## ステップ 4: PR 作成 & マージ

```bash
git checkout develop
git add -A
git commit -m "chore: prepare vX.Y.Z release"
git push origin develop

# PR 作成 (develop → main)
gh pr create --base main --head develop --title "Release vX.Y.Z"
```

PR をレビュー後、GitHub 上でマージ。

---

## ステップ 5: タグ作成 & リリース

```bash
git checkout main
git pull origin main

git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin vX.Y.Z
```

タグ push で GitHub Actions (`release.yml`) が起動:
1. `ruff check` で lint
2. リリース ZIP を自動ビルド
3. GitHub Release (pre-release) を作成し ZIP をアタッチ

### release.yml が ZIP に含めるもの

```
mmd_tools/          — Python パッケージ (mmd_tools/native/ 内のバイナリ含む)
plug-ins/           — Python プラグイン + C++ プラグイン (Maya バージョン別)
docs/               — ユーザードキュメント
config/             — 設定ファイル
shaders/            — HLSL シェーダー (存在する場合)
resources/          — アイコン等 (存在する場合)
README.md
LICENSE
CHANGELOG.md
maya_mmd_tools.mod
userSetup.py
pyproject.toml
```

---

## ステップ 6: develop にマージバック

```bash
git checkout develop
git merge --no-ff main
git push origin develop
```

---

## ステップ 7: リリース後確認

- [ ] GitHub Release ページに ZIP がアタッチされている
- [ ] ZIP 内に C++ バイナリ (各 Maya バージョン) が含まれている
- [ ] ZIP 内に FFI ライブラリ (win64/macos) が含まれている
- [ ] 新しいタグで `__version__` と一致している
- [ ] Pre-release フラグが適切に設定されている (0.x は Pre-release 推奨)

---

## クイックリファレンス: ビルド成果物マトリクス

| 成果物 | ビルドツール | Windows 出力 | macOS 出力 |
|---|---|---|---|
| `mmd_anim_ffi` | `cargo build -p mmd-anim-ffi --release` | `mmd_anim_ffi.dll` | `libmmd_anim_ffi.dylib` |
| `mmd_tools_cpp` | CMake + MSVC / Xcode | `mmd_tools_cpp.mll` | `mmd_tools_cpp.bundle` |

| Maya バージョン | ビルドターゲット | 備考 |
|---|---|---|
| 2024 | `-DMAYA_VERSION=2024` | Python 3.10 |
| 2025 | `-DMAYA_VERSION=2025` | Python 3.10 |
| 2026 | `-DMAYA_VERSION=2026` | Python 3.10 |
| 2027 | `-DMAYA_VERSION=2027` | Python 3.11 (要確認) |
