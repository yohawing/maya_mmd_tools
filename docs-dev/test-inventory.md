# Test Inventory

This document lists all test files under `tests/`, classifies them by
runtime dependency, and explains which tests can run in CI without Maya.

Related: [testing-overview.md](testing-overview.md)

---

## Classification

Tests are split into four categories based on what they need at runtime.

| Category | What it means | Run without Maya? |
|---|---|---|
| **pure-python** | No `maya` module in any transitive import | Yes |
| **mayapy必須** | Imports `maya.*` directly or through a base class | Only via mayapy |
| **GUI必須** | Uses `@requires_gui` or Maya Qt widgets | Requires Maya GUI |
| **外部アセット依存** | References `F:\MMD`, `build/` artifacts, or manifest files | No (local assets) |

---

## pure-python (19 files)

These tests run with plain `python -m unittest` and are the target of the
`ci_unit` Nox session.

| File | Notes |
|---|---|
| `tests/unit/test_bone_validator.py` | BoneValidator name-normalization logic |
| `tests/unit/test_cpp_fast_importer.py` | Fast-import routing; pre-seeds maya stubs |
| `tests/unit/test_exceptions.py` | Exception hierarchy (added by coverage-tests) |
| `tests/unit/test_log_formatters.py` | Log formatter helpers (added by coverage-tests) |
| `tests/unit/test_maya_bake_oracle_dumper.py` | Oracle dumper helpers; uses FakeCmds stub |
| `tests/unit/test_mmd_anim_runtime.py` | ctypes wrapper; passes when native lib absent |
| `tests/unit/test_pmd_export.py` | PMD exporter data structures |
| `tests/unit/test_pmd_parser.py` | PMD binary parser |
| `tests/unit/test_pmx_dumper.py` | PMX debug dumper |
| `tests/unit/test_pmx_export.py` | PMX exporter data structures |
| `tests/unit/test_pmx_parser.py` | PMX binary parser |
| `tests/unit/test_settings.py` | Settings store (added by coverage-tests) |
| `tests/unit/test_unicode_converter.py` | Unicode/Shift-JIS conversion |
| `tests/unit/test_vmd_export.py` | VMD exporter |
| `tests/unit/test_vmd_parser.py` | VMD binary parser |
| `tests/unit/test_vpd_parser.py` | VPD pose file parser |
| `tests/common/test_base.py` | TestBase helper |
| `tests/common/test_fixture_provider.py` | FixtureProvider helper |
| `tests/common/test_mocks.py` | Shared mock helpers |

> Note: `tests/unit/test_import_export_presenter.py` and
> `tests/unit/test_namespace_utils.py` look pure from their own source but
> fail to import because a transitive module (`mmd_tools.core.namespace_utils`,
> `mmd_tools.ui.presenters.*`) imports `maya.cmds` at module level.  They are
> therefore classified **mayapy必須** by the probe-based discovery logic.

---

## mayapy必須 (23 files)

These tests require `mayapy` (Maya's embedded Python interpreter).  Run
them via the existing `tests` Nox session.

| File | Why |
|---|---|
| `tests/unit/test_application_state.py` | `from maya import cmds` |
| `tests/unit/test_bone_converter.py` | `MayaTestBase` → `maya.cmds` |
| `tests/unit/test_bone_presenter.py` | `MayaTestBase` |
| `tests/unit/test_import_export_presenter.py` | transitive `maya.cmds` in presenter |
| `tests/unit/test_info_presenter.py` | `MayaTestBase` |
| `tests/unit/test_logger.py` | `MayaTestBase` |
| `tests/unit/test_material_presenter.py` | `MayaTestBase` |
| `tests/unit/test_maya_utils.py` | `from maya import cmds` directly |
| `tests/unit/test_morph_presenter.py` | `MayaTestBase` |
| `tests/unit/test_namespace_utils.py` | transitive `maya.cmds` in `namespace_utils` |
| `tests/unit/test_vmd_converter.py` | `MayaTestBase` |
| `tests/unit/test_vmd_importer.py` | `MayaTestBase` |
| `tests/integration/test_bone_converter.py` | Maya scene manipulation |
| `tests/integration/test_mesh_converter.py` | Maya scene manipulation |
| `tests/integration/test_morph_converter.py` | Maya scene manipulation |
| `tests/integration/test_namespace_import.py` | Maya namespace APIs |
| `tests/integration/test_physics_converter.py` | Maya rigid body APIs |
| `tests/integration/test_pmd_exporter.py` | Maya scene write |
| `tests/integration/test_pmd_importer.py` | Maya scene read |
| `tests/integration/test_pmx_exporter.py` | Maya scene write |
| `tests/integration/test_pmx_importer.py` | Maya scene read |
| `tests/integration/test_rig_converter.py` | Maya rig APIs |
| `tests/integration/test_vmd_converter.py` | Maya animation APIs |
| `tests/integration/test_vmd_exporter.py` | Maya animation write |
| `tests/integration/test_vmd_importer.py` | Maya animation read |

Run command:

```
uvx nox -s tests
uvx nox -s tests -- --type integration
```

---

## GUI必須 (4+ files)

These tests use `@requires_gui` (from `tests/common/gui_test_base.py`) or
instantiate Maya Qt widgets and must run inside a full Maya GUI session.

| File | Notes |
|---|---|
| `tests/gui/guitest_import_export_tab_gui.py` | Import/Export tab widgets |
| `tests/gui/guitest_translator.py` | Translator UI |
| `tests/gui/guitest_ui_components.py` | Generic UI components; also needs maya |
| `tests/gui/test_material_tab.py` | Placeholder only (empty after refactor) |

Run command:

```
uvx nox -s gui_tests
```

---

## 外部アセット依存 (runner scripts, not test_*.py)

These are not discovered by `unittest` automatically but are invoked
through dedicated Nox sessions that require local asset paths.

| File | Nox session | Requires |
|---|---|---|
| `tests/viewport/smoke_viewport_capture.py` | `maya_viewport_capture` | mayapy |
| `tests/viewport/static_render_capture.py` | `maya_static_render` | mayapy + PMX fixture |
| `tests/viewport/visual_regression_capture.py` | `maya_visual_regression` | Maya GUI + manifest |
| `tests/track6/track6_runner.py` | `maya_batch_import` | mayapy + `F:\MMD` assets |
| `tests/roundtrip/pmx_roundtrip_runner.py` | `pmx_roundtrip` | mayapy + PMX fixtures |
| `tests/cpp/smoke_runtime_node.py` | `maya_smoke` / `cpp_verify` | mayapy + C++ plugin |

---

## CI Gate: `ci_unit`

The `ci_unit` Nox session is the minimal CI gate for changes that do not
touch Maya-specific code.  It runs without Maya installed.

### How it works

1. For each `tests/unit/test_*.py`, run `python -c "import tests.unit.<stem>"`.
2. Files that import successfully are collected; files that raise
   `ModuleNotFoundError` for `maya` are skipped with a log notice.
3. All collected modules are passed to `python -m unittest`.

This probe-based approach means **new tests added to `tests/unit/` are
automatically included** the next time `ci_unit` runs, with no changes to
`noxfile.py` required.

### Usage

```bash
# Run pure-python unit tests (no Maya required)
uvx nox -s ci_unit

# List available sessions
uvx nox --list
```

### Current results (as of 2026-06-16)

- 16 modules collected (pure-python)
- 12 modules skipped (mayapy required)
- 225 tests, all passing

---

## Stale / Skip Notes

| File | Status | Reason |
|---|---|---|
| `tests/gui/test_material_tab.py` | Empty stub | Tests removed due to Qt widget issues in Maya standalone; classified GUI for safety |
| `tests/unit/test_import_export_presenter.py` | mayapy required | Indirect `maya.cmds` import via presenter module; needs mock refactor to run pure-python |
| `tests/unit/test_namespace_utils.py` | mayapy required | `namespace_utils.py` imports `maya.cmds` at module level |

---

## Lead Handoff Notes

- `tests/integration/test_animation_converter.py` was initially classified
  pure-python by header scan but its status should be re-verified; it was
  excluded from `ci_unit` because the probe-based discovery only scans
  `tests/unit/`.  Integration tests are excluded by design.
- If `test_import_export_presenter.py` or `test_namespace_utils.py` are
  refactored to lazy-import maya, the `ci_unit` probe will automatically
  pick them up without any noxfile change.
- The `tests/unit/test_mmd_anim_runtime.py` runtime path is conditionally
  enabled when `mmd_runtime_ffi.dll` is present; CI without the native lib
  still passes (tests degrade gracefully).
