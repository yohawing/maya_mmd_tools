# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-06-27

### Added
- Native MMD runtime/import pipeline based on `mmd-anim` v0.1.7, including native PMX parsing, runtime FFI loading, and refreshed C++ plug-ins for Maya 2024-2027.
- Rig mode runtime path with JO-aware space handling, native rig construction, append/IK DG nodes, and focused Bake/Rig parity regression coverage.
- VMD runtime/bake improvements including batched frame evaluation, Bezier tangent application, IK on/off frame handling, morph key batching, and camera/light fixtures.
- Runtime morph infrastructure for vertex, bone, and material morphs, including DG graph builders and oracle-backed regression fixtures.
- Drag-and-drop installer and import workflow, including Maya `modules` installation, generated `.mod` files for bundled Maya versions, and VMD-only drop warnings before a model is loaded.
- MMD Tools UI and drag-and-drop import routing for supported MMD import entry points.
- GoldenOracle/numeric manifests, Nox verification sessions, and tracked TestModel smoke fixtures.

### Changed
- Release ZIP documentation is limited to the public English README and Japanese README; local release/TODO/developer docs are ignored and not packaged.
- Native runtime artifacts now use `mmd_runtime_ffi.dll` on Windows while keeping compatibility with legacy `mmd_anim_ffi` library names where needed.
- C++ fast-import smoke defaults no longer depend on local Lumine/Addiction assets.
- Import UI and README guidance now favor drag-and-drop install/import and normal UI import paths.
- File > Import is deferred from the release surface; users should use the MMD Tools UI or drag-and-drop import.
- Windows Release and macOS universal plug-in artifacts were rebuilt for Maya 2024, 2025, 2026, and 2027.

### Fixed
- Stabilized MMD import entry points and model-root resolution for VMD import.
- Fixed material morph runtime shader attribute detection and model-root filtering.
- Fixed vertex morph Z-flip/template reuse behavior and reduced stale morph node issues.
- Reduced quaternion warning spam and toon texture enum noise.
- Improved PMX import performance through native parsing, flat skin weights, batched joint attributes, morph conversion optimizations, and per-vertex normal handling.

### Removed
- Removed tracked local-only developer documents (`docs-dev/`, `AGENTS.md`) and stale internal README files from the public repository surface.
- Removed local-asset hardcoded defaults from smoke/viewport scripts.
- Removed stale skipped tests and dead legacy manifest IK/grant constraint methods.

### Known Issues
- The opt-in C++ fast-import `mesh_only=False` path still does not create joints/skinCluster in `maya_smoke`; full joint/skinCluster creation is deferred to a future C++ fast path task.
- Bake mode is the recommended fidelity path for VMD motion because it bakes final poses from the `mmd-anim` runtime.
- Rig mode remains experimental for complex Bake/Rig mesh parity cases involving jointOrient, IK, append, or local-axis behavior.

## [0.2.0] - 2026-06-21

### Added
- DX11 MMD toon shader with gamma-correct viewport look (sRGB-linear rendering space + un-tone-mapped view transform applied automatically on import).
- MMD light controller (`mmd_light` null) created on import; drives a single directional light via vectorProduct connection.
- Per-material DX11 transparency modes: opaque, cutout, and blend selectable from Material tab with batch apply.
- PMX double-sided draw flag reflected as `doubleSided` shape attribute with correct backface culling.
- Automatic non-ASCII texture path resolution: textures with multi-byte paths are copied to an ASCII-safe cache and relinked to dx11Shader slots.
- Deterministic texture cache naming: cache filenames derived from `sha256(PMX-relative path)` for idempotent import/resolve.
- Shader outline rendering is now opt-in from the Material tab (off by default due to draw-order constraints).
- Chinese (zh_CN) translation key completion.
- UI architecture: MayaCmdsAdapter, SceneModelService, SettingsService, and Action classes (ImportModelAction, ImportVmdAction, ExportModelAction) extracted for testability.
- Headless unit tests for Bone, Morph, Material, ImportExport, Info, DisplayPane, and Physics presenters.

### Changed
- DX11 shader status downgraded from "Supported" to "Partial" in README to reflect outline fidelity limitations.
- UI presenter messages standardized to English.
- Dead Display Pane tab hidden; offset details marked as not yet supported.
- Release UI and exporter paths hardened with honest "not implemented" guards.

### Fixed
- Degenerate faces (zero-area triangles with duplicate vertex indices) in PMX files now skipped during import, preventing `setFaceVertexNormals` crash.
- Rigid body creation errors no longer cause entire PMX physics import to return None.
- Grant parent self-reference no longer creates a self-constraint loop.
- `reset_to_defaults` now actually resets settings.
- dx11Shader compound attribute set/create uses cmds fallback on live nodes.
- dx11Shader texture slots properly rebound after post-hoc texture resolve.
- Material tab Apply guards dx11-incompatible attribute sets.
- Duplicate Maya script editor logging eliminated.
- VP2-parity MMD shader restored with improved transparency compositing.
- CullFront used for single-sided dx11 materials (CullBack was inverted on real GPU).

### Notes
- This is an early `0.x` release. Production use is not recommended.
- Please report bugs and feedback through [GitHub Issues](https://github.com/yohawing/maya_mmd_tools/issues).

## [0.1.0] - 2026-05-08

### Added
- PMD/PMX model import.
- VMD animation import for bones, morphs, cameras, and lights.
- Basic MMD Tools UI with Info, Material, Morph, and Bone tabs.
- Japanese/English UI text support.
- Namespace support for loading multiple models:
  - PMX/PMD importer namespace generation
  - Japanese model name conversion for namespace-safe names
  - Sequential namespace management for duplicate model names
  - VMD importer namespace detection
- NamespaceUtils class:
  - Namespace generation and management
  - Context manager support
  - Cleanup support after import errors
- Maya Python API 2.0 based helper paths for performance-sensitive operations.

### Changed
- Import setting `use_namespace` is now wired to importer behavior.
- Bone list display order follows MMD bone indices.
- Custom attribute names were standardized.
- Release docs and README now use simple `0.x` versioning without alpha/beta suffixes.

### Fixed
- Improved unit and integration test stability.
- Fixed multiple UI issues.
- Improved VMD importer support for morph, camera, and light animation.

### Removed
- Removed unused mocks.
- Removed parent-bone and destination selection buttons from the basic info panel.

### Known Issues
- Large models may have performance issues.
- Some PMX files may fail to import.
- Newly imported VMD motions may not play back correctly after import.
- Physics support is incomplete.
- PMD/PMX/VMD export is not implemented.

### Notes
- This is an early `0.x` release. Production use is not recommended.
- Please report bugs and feedback through [GitHub Issues](https://github.com/yohawing/maya_mmd_tools/issues).
