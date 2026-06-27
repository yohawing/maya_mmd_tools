# Release Guide

Maya MMD Tools のリリース手順とバージョン方針はこのファイルを正とします。
ユーザー向けの使い方は `README.md` / `docs/README_ja.md`、変更点は `CHANGELOG.md` に書きます。

## Release Policy

- バージョン形式は SemVer の `MAJOR.MINOR.PATCH` です。
- 当面は `0.x` 系を早期リリースとして扱います。`alpha` / `beta` / `rc` のようなプレリリース識別子は使いません。
- `0.x` では API、設定、UI、ファイル構成が変わる可能性があります。
- リリース作業は `develop` から `main` への PR で行い、PR のマージは GitHub 上で行います。
- タグは `main` のリリースコミットに対して `vX.Y.Z` 形式で作成します。
- `.github/workflows/release.yml` はタグ push 時に GitHub Release を作成します。現状の workflow は `prerelease: false` です。Pre-release 表示にしたい場合は workflow を変更するか、公開後に GitHub Release の設定を編集します。

## Version Checklist

リリース前に次を同じ `X.Y.Z` に揃えます。

| File | Marker |
|---|---|
| `pyproject.toml` | `project.version` |
| `mmd_tools/__init__.py` | `__version__` |
| `maya_mmd_tools.mod` | each `MAYAVERSION` entry |
| `CHANGELOG.md` | `## [X.Y.Z] - YYYY-MM-DD` |

`CHANGELOG.md` の対象バージョンセクションは空にしないでください。release workflow はこのセクションを GitHub Release notes として使います。

## Build Prerequisites

### Windows

- Visual Studio 2022 with MSVC
- CMake 3.20+
- Rust toolchain
- Maya 2024 / 2025 / 2026 / 2027 and matching DevKit
- Python 3.7+ for Maya-side compatibility checks
- `uvx` / `nox`

### macOS

- Xcode Command Line Tools
- CMake 3.20+
- Rust toolchain
- Maya 2024 / 2025 / 2026 / 2027 and matching DevKit
- `uvx` / `nox`

Maya install locations can be overridden with `MAYA_LOCATION_<version>` and `MAYA_DEVKIT_ROOT_<version>` environment variables.

## Native Runtime Build

Build the mmd-anim FFI runtime first:

```powershell
uvx nox -s ffi_build -- --release
uvx nox -s native_smoke
```

The release package must include the platform runtime libraries:

| Platform | Runtime artifact |
|---|---|
| Windows | `mmd_tools/native/win64/mmd_runtime_ffi.dll` |
| macOS | `mmd_tools/native/macos/libmmd_runtime_ffi.dylib` |
| Linux, if enabled | `mmd_tools/native/linux/libmmd_runtime_ffi.so` |

If the nox session only builds under `external/mmd-anim/target/release`, copy the produced library into the matching `mmd_tools/native/<platform>/` directory before packaging.

## Maya C++ Plug-in Build

Build the C++ plug-in for each bundled Maya version.

```powershell
uvx nox -s cpp_build -- --maya 2024 --config Release
uvx nox -s cpp_build -- --maya 2025 --config Release
uvx nox -s cpp_build -- --maya 2026 --config Release
uvx nox -s cpp_build -- --maya 2027 --config Release
```

Expected release artifacts:

| Platform | Plug-in artifact |
|---|---|
| Windows | `plug-ins/<maya-version>/Release/mmd_tools_cpp.mll` |
| macOS | `plug-ins/<maya-version>/Release/mmd_tools_cpp.bundle` |

Each `plug-ins/<maya-version>/Release/` directory should also contain the matching runtime library needed by the C++ plug-in on that platform.

## Verification Gates

Run the relevant gates before opening or updating the release PR.

```powershell
uvx nox -s tests
uvx nox -s ffi_build -- --release
uvx nox -s native_smoke
uvx nox -s golden_oracle
```

For C++/Maya runtime verification:

```powershell
uvx nox -s cpp_verify -- --maya 2024 --config Release
uvx nox -s maya_smoke -- --maya 2024 --config Release
```

Repeat `cpp_verify` or at least `cpp_build` for all Maya versions included in the release ZIP.

For changed Python files, run lint narrowly:

```powershell
rtk ruff check <changed files>
git diff --check
```

If GUI, viewport, or Rig/Bake parity behavior changed, add the relevant viewport / commandPort smoke result to the PR notes. Local asset manifests must be passed by CLI argument and should not be hard-coded in tracked tests.

## Release ZIP Contents

The GitHub Actions release workflow builds `dist/maya_mmd_tools-X.Y.Z.zip` from:

```text
mmd_tools/
plug-ins/
docs/
README.md
RELEASE.md
LICENSE
CHANGELOG.md
drag_drop_install.py
maya_mmd_tools.mod
userSetup.py
pyproject.toml
config/      optional
resources/   optional
shaders/     optional
examples/    optional
```

Before publishing, inspect the ZIP or workflow artifact and confirm:

- `drag_drop_install.py` is present.
- `maya_mmd_tools.mod` contains the release version and all bundled Maya versions.
- `mmd_tools/native/` contains the intended runtime libraries.
- `plug-ins/<version>/Release/` contains the intended Maya C++ plug-ins.
- No local test assets, build directories, caches, or machine-specific paths are included.

## PR Flow

Prepare the release branch state on `develop`:

```powershell
git checkout develop
git status
git add <release files>
git commit -m "Prepare vX.Y.Z release"
git push origin develop
```

Open or update the release PR:

```powershell
gh pr create --base main --head develop --title "Release vX.Y.Z"
```

The PR description should summarize:

- user-visible import/install changes
- native runtime / C++ plug-in changes
- Rig/Bake/VMD/morph behavior changes
- docs and package changes
- validation results and any remaining known limitations

## Tag And Publish

After the PR is reviewed and merged:

```powershell
git checkout main
git pull origin main
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin vX.Y.Z
```

The tag push runs `.github/workflows/release.yml`:

1. `ruff check --no-fix .`
2. Build the release ZIP.
3. Extract the matching `CHANGELOG.md` section.
4. Upload the ZIP as an artifact.
5. Publish the GitHub Release for tag builds.

## Post-release Checks

- GitHub Release exists for `vX.Y.Z`.
- The release notes match `CHANGELOG.md`.
- The ZIP is attached and opens correctly.
- Drag-and-drop install works from a clean extracted ZIP.
- Maya starts after install and `MMD > MMD Tools` appears.
- A PMX/PMD model import works.
- A VMD import works after a model is loaded.
- Dropping a VMD before loading a model shows a warning and does not import.
- `main` and `develop` are brought back into the expected state after release.

## Quick Artifact Matrix

| Artifact | Windows | macOS |
|---|---|---|
| mmd-anim FFI runtime | `mmd_runtime_ffi.dll` | `libmmd_runtime_ffi.dylib` |
| Maya C++ plug-in | `mmd_tools_cpp.mll` | `mmd_tools_cpp.bundle` |
| Maya versions | 2024 / 2025 / 2026 / 2027 | 2024 / 2025 / 2026 / 2027 |
