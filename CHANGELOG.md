# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
