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

Run these gates before opening or updating the release PR.

Static and CI-equivalent checks:

```powershell
rtk ruff check --no-fix .
git diff --check
```

Python, native runtime, and numeric oracle checks:

```powershell
uvx nox -s tests
uvx nox -s tests -- --type integration
uvx nox -s ffi_build -- --release
uvx nox -s native_smoke
uvx nox -s golden_oracle
```

The aggregate release gate runs the complete mayapy unit and integration suites
on Maya 2024, 2025, 2026, and 2027. It also runs fixed viewport/shader captures
for the two representative VP2 paths:

| Maya | VP2 device | Shader backend |
|---|---|---|
| 2025 | OpenGL Core Profile | GLSLShader |
| 2026 | DirectX 11 | dx11Shader |

```powershell
uvx nox -s release_gate -- --with-cpp --cpp-config Release
```

The remaining asset/oracle E2E gates use the primary `--maya` version (2024 by
default). Override it with `--maya <version>` when narrowing a rerun.

If an open Maya session has loaded the default development DLL, build and smoke
an alternate target without replacing it:

```powershell
uvx nox -s ffi_build -- --release --cargo-target-dir build/mmd-anim-unlocked-target
uvx nox -s native_export_smoke -- --strict --ffi-path build/mmd-anim-unlocked-target/release
```

`native_export_smoke --strict` requires the PMX parts export ABI. If the JSON
writer ABIs for VMD/PMD are not present in the native runtime, the smoke reports
them in `skippedOptional` and still passes because Python keeps the fallback
writer path for those formats.

For the aggregate gate, pass the same alternate target once:

```powershell
uvx nox -s release_gate -- --ffi-cargo-target-dir build/mmd-anim-unlocked-target
```

Maya-local regression gates:

```powershell
uvx nox -s release_camera_motion_oracle -- --maya 2024
uvx nox -s import_scale_drift_e2e -- --maya 2024 --expect fixed
uvx nox -s anim_layer_graph_compare -- --maya 2024
uvx nox -s import_order_e2e -- --maya 2024
```

For C++/Maya runtime verification:

```powershell
uvx nox -s cpp_verify -- --maya 2024 --config Release
uvx nox -s cpp_verify -- --maya 2025 --config Release
uvx nox -s cpp_verify -- --maya 2026 --config Release
uvx nox -s cpp_verify -- --maya 2027 --config Release
```

`uvx nox -s release_gate -- --with-cpp --cpp-config Release` runs the same
Maya 2024-2027 `cpp_verify` matrix. To narrow a local rerun, repeat
`--cpp-maya`, for example `--cpp-maya 2024 --cpp-maya 2026`.

If a bundled Maya version cannot run in the local environment, run at least `cpp_build` for that version and record the skipped smoke reason in the release PR:

```powershell
uvx nox -s cpp_build -- --maya <version> --config Release
```

`release_camera_motion_oracle` is a camera-motion gate with a repo-local generated fixture by default (`tests/data/camera_motion/manifest.json`), so the release gate does not silently skip when the local GoldenOracle checkout is absent. It runs Bake mode as a strict current/keyframe comparison, and Sparse mode as a keyframe gate with current playback recorded report-only because the editable sparse camera rig uses Maya curves between sparse keys. Pass `--manifest F:\Develop\MMDDev\GoldenOracle\manifests\camera_motion.json` and `--all-cases` for a full local audit after refreshing or accepting the nanoem camera baselines.

If GUI, viewport, or Rig/Bake parity behavior changed, add the relevant viewport / commandPort smoke result to the PR notes. Local asset manifests must be passed by CLI argument or a local-only release gate and should not be added to CI-only tests.

## Release ZIP Contents

The GitHub Actions release workflow builds `dist/maya_mmd_tools-X.Y.Z.zip` from:

```text
mmd_tools/
plug-ins/
docs/README_ja.md
README.md
LICENSE
drag_drop_install.py
maya_mmd_tools.mod
userSetup.py
pyproject.toml
config/      # if present
resources/   # if present
shaders/     # if present
examples/    # if present
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
