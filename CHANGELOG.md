# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-07-11

### Added
- Added pass/fail GoldenOracle viewport image comparisons with case-specific thresholds and flat-gray regression detection.
- Added Maya Bullet import and preview simulation for PMX/PMD rigid bodies and joints, enabled by default when Bullet is available.
- Added development-mode PMX round-trip preservation for rigid bodies, joints, and display frames, including focused round-trip gates.
- Added an editable Physics panel with model-scoped filtering, selection, validated atomic updates, and persisted UI state.
- Added native `mmd-anim` physics motion bake with a Maya E2E route gate covering physics-driven bone channels and disabled preview feedback.
- Added HumanIK definition and control-rig creation from supported imported MMD skeletons.

### Changed
- Integrated generated-PMX GoldenOracle visual comparisons for Maya 2025 GLSL and Maya 2026 DX11 into the aggregate release gate.
- Expanded the aggregate release gate to run mayapy unit/integration suites on Maya 2024-2027, with fixed Maya 2025 OpenGL/GLSL and Maya 2026 DX11 viewport captures.
- Updated the bundled and release-gate `mmd-anim` integration to v0.2.0.
- Unified physics collider visibility under the model Physics group and added DX11 collider meshes for viewport display.
- Expanded Morph tab routing so vertex, bone, material, and group morphs share editable weights and PMX panel categories.
- Limited custom import scale to Development Mode; normal imports now use scale 1.0.
- Reduced routine importer, runtime, rig, morph, shader, physics, and UI selection log noise.
- Simplified Import/Export settings and kept unfinished user-facing export actions hidden.

### Fixed
- Fixed model-root viewport visibility controls: mesh and joint attributes now drive the `Geometry` and `Skeleton` parent groups immediately after import, while the nonfunctional IK/controller controls were removed.
- Fixed out-of-order IK mini-chain slot construction and added a tentacle-chain regression fixture.
- Fixed VMD import physics feedback routing so legacy Bullet preview is restored when native bake is not used and blocked when native bake owns the final animation.
- Fixed PMX capsule/cylinder height mapping and synchronized collider visibility from the model root.
- Fixed Morph tab blendShape addressing and routed bone/material morph weights through the correct IK and shader-backend paths.
- Made unavailable bone-morph runtime nodes fail soft and report partial import outcomes instead of aborting the complete import.

### Known Issues
- Physics and native physics motion bake remain experimental; the native bake path is opt-in.
- Physics collider DX11 pixel parity for Bullet preview OFF/ON is not a release blocker and still requires a reliable real-viewport capture gate.
- Display frames are preserved for PMX round-trip but do not yet have a dedicated editing UI.
- User-facing PMX/PMD/VMD export remains unavailable; current writer and round-trip paths are development-only.
- Additional UV, Flip, Impulse, and PMX 2.1 soft-body workflows remain unsupported.
- Bake mode remains the recommended fidelity path for complex VMD motion; rig mode may differ for jointOrient, IK, append, and local-axis cases.

## [0.3.1] - 2026-07-03

### Added
- Added regression gates for import scale drift, animLayer graph comparison, import ordering, and camera motion release oracle coverage.
- Added runtime-backed VMD light motion sampling and auto-clear behavior for re-imported camera and light motion.
- Added progress reporting during MMD model imports.

### Changed
- Updated Windows Release C++ plug-ins and native runtime artifacts for Maya 2024, 2025, 2026, and 2027.
- Improved VMD import/export performance, including faster animLayer API keying and split runtime helper modules.
- Cleaned up animation import settings and model combo labeling in the UI.
- Documented the expanded release verification gates.

### Fixed
- Fixed PMX import scale bind drift by applying import scale before mesh, bone, and morph coordinate generation.
- Fixed sparse VMD camera rig evaluation, interpolation, bake transforms, and oracle gating.
- Fixed duplicate no-namespace mesh imports and duplicate skeleton IK ancestor lookup.
- Fixed PMX joint path refresh after reparenting and sanitized rig node names derived from joint paths.
- Fixed light VMD clear import ordering and material morph integration fixture path handling.

### Known Issues
- macOS Toon capture, `glslShader`, and live VP2.0 behavior still require final real-machine confirmation before publishing the tag.
- The opt-in C++ fast-import `mesh_only=False` path still does not create joints/skinCluster in `maya_smoke`; full joint/skinCluster creation is deferred to a future C++ fast path task.
- Bake mode remains the recommended fidelity path for VMD motion because it bakes final poses from the `mmd-anim` runtime.
- Rig mode remains experimental for complex Bake/Rig mesh parity cases involving jointOrient, IK, append, or local-axis behavior.

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
