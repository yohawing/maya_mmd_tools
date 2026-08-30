"""One independent headless Qt test item per production authoring surface."""

import json
import os
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.common.maya_stub import install_maya_stub  # noqa: E402

install_maya_stub(profile="headless")
open_maya_ui = ModuleType("maya.OpenMayaUI")
open_maya_ui.MQtUtil = Mock()
sys.modules["maya.OpenMayaUI"] = open_maya_ui

from mmd_tools.ui.qt_compat import QApplication  # noqa: E402
from tests.common.authoring_ui_surface_cases import (  # noqa: E402
    HEADLESS_CASE_ID,
    MANIFEST_PATH,
    create_production_main_window,
    exercise_surface,
    load_headless_surfaces,
)
from tools.gates.ui_coverage_gate import validate_report  # noqa: E402


SURFACES = load_headless_surfaces()
_WITNESSES = []


def teardown_module():
    """Emit fresh aggregate evidence only when the release gate requests it."""
    raw_path = os.environ.get("MMD_UI_COVERAGE_HEADLESS_REPORT")
    if not raw_path:
        return
    report_path = Path(raw_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    report_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "gate_id": manifest["gate_id"],
                "cases": [{"case_id": HEADLESS_CASE_ID, "status": "pass", "maya_versions": []}],
                "surfaces": _WITNESSES,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("surface", SURFACES, ids=lambda surface: surface["id"])
def test_authoring_surface_dispatches_exactly_once(qapp, surface):
    patcher = pytest.MonkeyPatch()
    window = None
    try:
        window, handler_spy = create_production_main_window(patcher, surface)
        qapp.processEvents()
        witness = exercise_surface(window, surface, qapp, handler_spy)
    finally:
        if window is not None:
            window.close()
            window.deleteLater()
            qapp.processEvents()
        patcher.undo()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    scoped_manifest = dict(manifest)
    scoped_manifest["surfaces"] = [surface]
    scoped_report = {
        "schema_version": 1,
        "gate_id": manifest["gate_id"],
        "cases": [{"case_id": HEADLESS_CASE_ID, "status": "pass"}],
        "surfaces": [witness],
    }
    assert validate_report(scoped_manifest, scoped_report)["valid"]
    _WITNESSES.append(witness)


def test_headless_matrix_owns_all_declared_safe_qt_cases_without_maya_claims():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    owner = next(case for case in manifest["cases"] if case["id"] == HEADLESS_CASE_ID)
    assert owner["execution_layer"] == "headless_qt"
    assert "required_maya_versions" not in owner
    assert SURFACES
    assert len({surface["id"] for surface in SURFACES}) == len(SURFACES)
    assert "import_export.clear_history" not in {surface["id"] for surface in SURFACES}
    assert {surface["case_id"] for surface in SURFACES} == {HEADLESS_CASE_ID}
