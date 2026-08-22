# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Defined the bounded v0.7 public Export scope for validation-gated PMX 2.0 and VMD output; PMD remains import-only.
- Verified the canonical PMX 2.0 IK and flag-dependent bone metadata subset through Maya export and fresh import on Maya 2024/2026.
- Preserved PMX 2.0 additional UV channels and UV/additional-UV morph four-component metadata through fresh import, while leaving Maya UV-set runtime evaluation and visual parity outside the supported claim.
- Standardized PMX vertex export on BDEF4. SDEF/QDEF are imported as linear skin weights without retaining their auxiliary deformation data; explicit SDEF/QDEF payloads and PMX 2.1-only Flip morphs, Impulse morphs, and soft bodies remain fail-closed.
- Standardized VMD export on the fixed Bake Timeline strategy, with the imported character scene as the sole motion authority; source-VMD identity and raw-key equivalence are no longer export contracts.
- Added canonical MaterialTab authoring for shared toon indices and non-shared custom toon path/index, with PMX parse and Maya 2024/2026 fresh-import evidence.

## [0.6.2] - 2026-08-01

### Fixed
- Fixed PMX/PMD imports under current and numeric Maya namespaces, including generated namespace regression coverage.
- Fixed split-mesh shape resolution by using the full DAG path when applying mesh attributes.
- Welded UV-seam duplicate geometric vertices through the native C++ mesh path while preserving face-corner UVs, authored normals, and source metadata for skin/morph processing.
- Replaced per-collider physics authoring constraints with offset-parent matrix follow, migrated legacy follow graphs, and pruned solver kinematic inputs that close DG cycles on complex rigs.

## [0.6.1] - 2026-07-30

### Added
- Added an opt-in MMD Control Rig authoring workflow with model-import creation, automatic binding, a manager and picker, IK/FK and twist controls, animation-layer support, reversible bake/restore operations, and direct VMD import to existing controls.
- Added experimental registered sparse VMD import and per-control rotation time curves so editable Control Rig keys can retain MMD Bezier timing and quaternion short-path evaluation.
- Added model-scoped Bone Morph routing for Control Rig motion and export, saved physics bind-basis evaluation, physics enable pre-roll, and tri-state Animator visibility controls.

### Changed
- Updated the bundled `mmd-anim` runtime to 0.3.3 and expanded runtime provenance and compiled-track introspection used by VMD import.
- Made MMD Control Rig and HumanIK ownership explicit so only one authoring route writes the underlying MMD rig at a time.
- Locked Control Rig authoring channels and preserved their authored basis, animation-layer ownership, and sparse key timing across creation, reimport, bake, save, and reopen workflows.
- Made the Physics tab Enable checkbox toggle simulation immediately instead of automatically pre-rolling every frame from the saved start frame.

### Fixed
- Stabilized native and Python CCD IK handling for local axes, bind-space reconstruction, root-space goals, non-finite inputs, near-zero quaternions, and Maya-authored iteration and angle-limit values.
- Made VMD reimport transactional across namespaced models, IK state keys, registered rotation timing, curve connections, metadata, and pre-existing motion, with exact rollback on failure.
- Kept finger controls attached to evaluated wrists, excluded zero-weight skin influences, isolated HumanIK Bone Morph writers, and preserved model-scoped morph weights during export.
- Improved live physics startup and playback with saved bind bases, deterministic pre-roll, unchanged-pose caching, and support for unattached rigid bodies.

### Known Issues
- MMD Control Rig, registered sparse VMD keys, and rotation time curves remain experimental and opt-in. For the broadest compatibility, use the normal high-precision Bake path.
- The external world-space mesh oracle can show a shared residual on SDEF-deformed vertices (observed maximum `0.026398` on the coverage fixture). Because the same residual appears on both legacy and Control Rig routes, it is excluded from bone-route parity and release acceptance.
- HumanIK retargeting remains experimental and may require manual lower-body or locomotion correction on some models.
- User-facing PMX/PMD/VMD export remains unavailable; current writer and round-trip paths are development-only.
- Additional UV, Flip, Impulse, and PMX 2.1 soft-body workflows remain unsupported.

## [0.6.0] - 2026-07-26

### Added
- Added opt-in real-time physics playback backed by `mmd-anim`. The Physics tab can enable per-frame Maya timeline evaluation, with bounded fixed-step catch-up for forward jumps and deterministic reset behavior for backward or oversized jumps.
- Added experimental HumanIK retargeting between imported MMD models: a standalone dockable HumanIK Editor with pair-based Character/Source selection, automatic characterization from the rest pose, a full finger profile, external HIK characters as retarget sources, and baking retargeted motion back to the MMD rig including MMD leg IK. Setup, target preview, Control Rig, Bake, and Restore run as reversible transactions with journaled ownership.
- Published the Animator Toolset as a standalone dockable window on the MMD menu, with SVG-based Body and Finger pickers, a Morph picker and editor, a non-destructive rest pose toggle, and English/Japanese/Chinese localization.
- Restored a dedicated Display Frames tab for editing PMX frame names, special-frame flags, and ordered bone/morph items with validation, undo, and metadata round-trip support; Animator Toolset remains a separate read-only picker surface.
- Added an opt-in `Reduce Bake Keys` option for Bake Motion that thins dense baked keys through the bundled `mmd-anim` pose reducer, with a quality slider that trades key count against replay tolerance.
- Added a model README dialog after menu and drag-and-drop imports, with a display policy setting.
- Allowed the MMD Tools menu to be torn off.

### Changed
- Expanded the reviewed, corpus-driven MMD name vocabulary so more Japanese bone, material, and morph names resolve to readable ASCII-safe Maya names.
- Refused VMD import while a HumanIK TARGET or HumanIK Control Rig owns the MMD rig, instead of writing conflicting keys.
- Updated the bundled `mmd-anim` runtime to 0.3.2 (native ABI 3) for pose reduction and DCC curve output.
- Cached native IK chain configuration to avoid rebuilding it on every evaluation.
- Standardized product icon buttons and hid namespaces in editor model lists.

### Fixed
- Preserved PMX authored vertex normals end to end: normal and fast import no longer average, unlock, or recompute them; only skinClusters whose authored normals actually differ from the geometric normals are kept off the GPU deformer path; and the GLSL shader transforms normals with the inverse transpose matrix.
- Fixed MMD shader appearance on both backends: sphere texture composition order, additive sphere shading, default light color, texture enablement for hardware shaders, hardware material alpha squaring, fallback texture and PMX color composition, texture alpha on fallback materials, and outline scaling on high-DPI viewports.
- Aligned fast import with the normal import path for morph names, model README data, node naming, and material/morph name sanitization.
- Preserved the full PMX morph index range instead of dropping high indices.
- Invalidated all IK outputs when a goal is edited, and kept IK targets and skin deformation aligned when the model root is moved.
- Recursively cleaned up namespaces left behind by failed imports and preflighted morph controller availability so partial failures report instead of leaving broken scene state.
- Reported physics initialization failures once instead of repeatedly, and skipped Black PMX initialization while physics is off.
- Resolved the MMD plug-in path in `userSetup` and surfaced plug-in startup failures instead of failing silently.

### Known Issues
- HumanIK retargeting is experimental and limited to MMD-to-MMD retargeting between imported models. Characterization can leave a residual stance offset on some models, lower-body and locomotion results may need manual correction, and HumanIK-owned scene state must be released through the HumanIK Editor rather than by deleting nodes.
- The MMD-native Control Rig (NURBS curve controllers layered over the imported MMD rig) is Development Mode only and unsupported in this release; its baked output does not yet match the external `mmd-anim` oracle within tolerance.
- `Reduce Bake Keys` is off by default and applies only to Bake Motion; rig-mode imports are unaffected.
- User-facing PMX/PMD/VMD export remains unavailable; current writer and round-trip paths are development-only.
- Additional UV, Flip, Impulse, and PMX 2.1 soft-body workflows remain unsupported.
- Native VMD physics bake and real-time physics playback remain experimental and opt-in. Live physics currently supports Spring 6DOF joints only; backward or oversized time jumps reset the simulation, and physics caching is unsupported.
- Bake mode remains the recommended fidelity path for complex VMD motion; rig mode may differ for jointOrient, IK, append, and local-axis cases.

## [0.5.0] - 2026-07-19

### Added
- Added a PMX Editor-inspired Physics tab for imported rigid bodies and joints, with editable shape, pose, binding, collision, limit, and spring properties; validation and undo; collider display; and validated PMX round-trip for supported fields.

### Changed
- Exposed the Import Physics option in normal mode and enabled PMX/PMD physics import by default; users can still opt out before import.
- Enabled Create MMD Shaders by default for PMX/PMD imports.
- Updated the bundled `mmd-anim` runtime to v0.3.1.
- Added Maya 2027 release plug-in/runtime artifacts and release verification.
- Centralized supported morph weights on model-scoped controller attributes and improved VMD morph binding and key recovery.

### Fixed
- Fixed material morph sliders on the VP2 OpenGL/GLSL path.
- Fixed VMD IK, append-bone, and model-scoped morph handling across import and playback paths.

### Known Issues
- PMX/PMD physics data is imported by default, but native VMD physics bake remains experimental and opt-in; real-time/live physics simulation is unsupported.
- Create, duplicate, and delete object authoring remain unsupported, as do Controller/IK/arbitrary-key pre-physics poses, animated Collider collision, hair/skirt live collision, random scrubbing, and physics caches.

## [0.4.0] - 2026-07-13

### Added
- Enabled complete-or-none PMX material morph routing for DX11 and GLSL, including diffuse alpha, specular, ambient, edge, and all texture factors.
- Added pass/fail GoldenOracle viewport image comparisons with case-specific thresholds and flat-gray regression detection.
- Kept the Physics panel as a Development Mode-only placeholder for a future scene-physics backend.
- Added native `mmd-anim` physics motion bake with a Maya E2E route gate covering physics-driven bone channels and disabled preview feedback.
- Added HumanIK definition and control-rig creation from supported imported MMD skeletons.

### Changed
- Integrated generated-PMX GoldenOracle visual comparisons for Maya 2025 GLSL and Maya 2026 DX11 into the aggregate release gate.
- Expanded the aggregate release gate to run mayapy unit/integration suites on Maya 2024-2027, with fixed Maya 2025 OpenGL/GLSL and Maya 2026 DX11 viewport captures.
- Updated the bundled and release-gate `mmd-anim` integration to v0.2.0.
- Expanded Morph tab routing so vertex, bone, material, and group morphs share editable weights and PMX panel categories.
- Limited custom import scale to Development Mode; normal imports now use scale 1.0.
- Reduced routine importer, runtime, rig, morph, shader, physics, and UI selection log noise.
- Simplified Import/Export settings and kept unfinished user-facing export actions hidden.
- Raised the supported Python floor to 3.10 and aligned Ruff's parser target with Maya 2024+.
- Deferred PMX 2.1 soft-body decoding on the native fast-import path until soft-body data is requested.
- Retired the Maya scene-physics backend; physics animation now uses the native bake workflow.

### Fixed
- Fixed model-root viewport visibility controls: mesh and joint attributes now drive the `Geometry` and `Skeleton` parent groups immediately after import, while the nonfunctional IK/controller controls were removed.
- Fixed out-of-order IK mini-chain slot construction and added a tentacle-chain regression fixture.
- Fixed Morph tab blendShape addressing and routed bone/material morph weights through the correct IK and shader-backend paths.
- Made unavailable bone-morph runtime nodes fail soft and report partial import outcomes instead of aborting the complete import.
- Fixed IK evaluation so ancestor motion can trigger a solve while baked FK poses that already match their goal are not solved twice.
- Fixed morph metadata reads when legacy scenes do not expose an expected morph-index attribute.
- Fixed native FFI byte-buffer cleanup when decoding or conversion raises an exception.
- Made the CI unit gate fail on unexpected import errors instead of silently classifying them as Maya-only skips.

### Known Issues
- Native physics motion bake remains experimental and opt-in; interactive scene physics is unavailable.
- Display frames are preserved for PMX round-trip; releases through 0.5.0 do not include the dedicated editing UI added under Unreleased.
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
