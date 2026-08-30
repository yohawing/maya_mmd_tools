"""Lightweight Qt contracts for measured UI action coverage witnesses."""

import ast
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from mmd_tools.ui.qt_compat import QApplication, QPushButton, Qt, QWidget  # noqa: E402
from tests.common.ui_action_coverage import (  # noqa: E402
    ActionInvocationSpy,
    QtSignalInvocationSpy,
    build_surface_witness,
)
from tools.gates.ui_coverage_gate import validate_report  # noqa: E402

try:  # noqa: E402
    from PySide6.QtTest import QTest
except ImportError:  # pragma: no cover - Maya 2024 uses PySide2.
    from PySide2.QtTest import QTest


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _clickable_surface():
    root = QWidget()
    button = QPushButton("Apply", root)
    button.setObjectName("testApplyButton")
    root.show()
    button.show()
    button.setEnabled(True)
    return root, button


def test_visible_enabled_surface_records_real_handler_exactly_once(qapp):
    invocations = []
    root, button = _clickable_surface()
    handler = ActionInvocationSpy.wrap(
        "Presenter.apply", lambda: invocations.append("applied"), button
    )
    button.clicked.connect(handler)

    QTest.mouseClick(button, Qt.LeftButton)
    qapp.processEvents()

    assert root.isVisible()
    assert button.isVisible()
    assert button.isEnabled()
    assert invocations == ["applied"]
    assert build_surface_witness(
        surface_id="test.apply",
        case_id="headless.test_apply",
        selector="objectName=testApplyButton",
        interaction="QTest.mouseClick(objectName=testApplyButton, Qt.LeftButton)",
        oracle="handler_effect_observed",
        action_spy=handler,
        control=button,
    ) == {
        "surface_id": "test.apply",
        "case_id": "headless.test_apply",
        "selector": "objectName=testApplyButton",
        "status": "pass",
        "runtime_witness": {
            "interaction": "QTest.mouseClick(objectName=testApplyButton, Qt.LeftButton)",
            "fired_action": "Presenter.apply",
            "oracle": "handler_effect_observed",
            "action_count": 1,
        },
    }
    root.close()


def test_qt_action_signal_spy_records_real_control_dispatch(qapp):
    root, button = _clickable_surface()
    signal_spy = QtSignalInvocationSpy(
        "QPushButton.clicked", button.clicked, source_control=button
    )

    QTest.mouseClick(button, Qt.LeftButton)
    qapp.processEvents()

    witness = build_surface_witness(
        surface_id="test.apply",
        case_id="headless.test_apply",
        attribute="apply_btn",
        interaction="QTest.mouseClick(apply_btn)",
        oracle="clicked_signal_observed",
        action_spy=signal_spy,
        control=button,
    )
    assert witness["runtime_witness"]["action_count"] == 1
    assert witness["runtime_witness"]["fired_action"] == "QPushButton.clicked"
    root.close()


def test_signal_spy_from_unrelated_control_cannot_cover_surface(qapp):
    root, button = _clickable_surface()
    unrelated = QPushButton("Unrelated", root)
    unrelated.show()
    signal_spy = QtSignalInvocationSpy(
        "QPushButton.clicked", unrelated.clicked, source_control=unrelated
    )
    QTest.mouseClick(unrelated, Qt.LeftButton)
    qapp.processEvents()

    with pytest.raises(AssertionError, match="source must match"):
        build_surface_witness(
            surface_id="test.apply",
            case_id="headless.test_apply",
            attribute="apply_btn",
            interaction="click",
            oracle="effect",
            action_spy=signal_spy,
            control=button,
        )
    root.close()


def test_spies_reject_missing_source_control(qapp):
    root, button = _clickable_surface()
    with pytest.raises(ValueError, match="source_control"):
        QtSignalInvocationSpy("QPushButton.clicked", button.clicked, None)
    with pytest.raises(ValueError, match="source_control"):
        ActionInvocationSpy.wrap("Presenter.apply", lambda: None, None)
    root.close()


def test_measured_witness_preserves_coverage_report_schema(qapp):
    root, button = _clickable_surface()
    handler = ActionInvocationSpy.wrap("example.Presenter.apply", lambda: None, button)
    button.clicked.connect(handler)
    QTest.mouseClick(button, Qt.LeftButton)
    qapp.processEvents()
    surface = build_surface_witness(
        surface_id="test.apply",
        case_id="headless.test_apply",
        selector="objectName=testApplyButton",
        interaction="QTest.mouseClick(objectName=testApplyButton, Qt.LeftButton)",
        oracle="handler_effect_observed",
        action_spy=handler,
        control=button,
    )
    manifest = {
        "schema_version": 1,
        "gate_id": "V070-UI-COVERAGE-1",
        "tabs": [
            {
                "id": tab_id,
                "selector": "objectName={}".format(tab_id),
                "module": "example.{}Tab".format(tab_id),
            }
            for tab_id in (
                "import_export",
                "export",
                "info",
                "material",
                "bone",
                "morph",
                "display_pane",
                "physics",
                "settings",
            )
        ],
        "cases": [
            {
                "id": "headless.test_apply",
                "status": "current",
                "execution_layer": "headless_qt",
                "source": "tests/unit/test_ui_action_coverage.py",
            }
        ],
        "surfaces": [
            {
                "id": "test.apply",
                "tab": "import_export",
                "kind": "action",
                "selector": "objectName=testApplyButton",
                "disposition": "qt_case",
                "case_id": "headless.test_apply",
                "expected_handler": "example.Presenter.apply",
            }
        ],
        "unmapped_surfaces": [],
    }
    report = {
        "schema_version": 1,
        "gate_id": "V070-UI-COVERAGE-1",
        "cases": [
            {
                "case_id": "headless.test_apply",
                "status": "pass",
            }
        ],
        "surfaces": [surface],
    }

    assert validate_report(manifest, report)["valid"]
    root.close()


@pytest.mark.parametrize("click_count", [0, 2])
def test_zero_or_multiple_handler_invocations_cannot_pass(qapp, click_count):
    root, button = _clickable_surface()
    handler = ActionInvocationSpy.wrap("Presenter.apply", lambda: None, button)
    button.clicked.connect(handler)
    for _index in range(click_count):
        QTest.mouseClick(button, Qt.LeftButton)
    qapp.processEvents()

    with pytest.raises(AssertionError, match="must fire exactly once"):
        build_surface_witness(
            surface_id="test.apply",
            case_id="headless.test_apply",
            attribute="apply_btn",
            interaction="click",
            oracle="effect",
            action_spy=handler,
            control=button,
        )
    root.close()


@pytest.mark.parametrize("status", ["blocked", "not_run", "fail"])
def test_unreached_or_incomplete_surface_status_cannot_pass(qapp, status):
    root, button = _clickable_surface()
    handler = ActionInvocationSpy.wrap("Presenter.apply", lambda: None, button)
    button.clicked.connect(handler)
    QTest.mouseClick(button, Qt.LeftButton)
    qapp.processEvents()

    with pytest.raises(AssertionError, match="status must be pass"):
        build_surface_witness(
            surface_id="test.apply",
            case_id="headless.test_apply",
            attribute="apply_btn",
            interaction="click",
            oracle="effect",
            action_spy=handler,
            control=button,
            status=status,
        )
    root.close()


def test_each_surface_is_checked_independently_after_prior_failure(qapp):
    """One surface's failure cannot disguise a later unexecuted action."""
    first_root, first_button = _clickable_surface()
    second_root, _second_button = _clickable_surface()
    first = ActionInvocationSpy.wrap("Presenter.first", lambda: None, first_button)
    second = ActionInvocationSpy.wrap("Presenter.second", lambda: None, _second_button)
    first_button.clicked.connect(first)
    QTest.mouseClick(first_button, Qt.LeftButton)
    qapp.processEvents()

    first_witness = build_surface_witness(
        surface_id="test.first",
        case_id="headless.first",
        attribute="first_btn",
        interaction="click",
        oracle="effect",
        action_spy=first,
        control=first_button,
    )
    assert first_witness["runtime_witness"]["action_count"] == 1
    with pytest.raises(AssertionError, match="fired 0 time"):
        build_surface_witness(
            surface_id="test.second",
            case_id="headless.second",
            attribute="second_btn",
            interaction="click",
            oracle="effect",
            action_spy=second,
            control=_second_button,
        )
    first_root.close()
    second_root.close()


@pytest.mark.parametrize("state", ["hidden", "disabled"])
def test_hidden_or_disabled_surface_cannot_emit_passing_witness(qapp, state):
    root, button = _clickable_surface()
    handler = ActionInvocationSpy.wrap("Presenter.apply", lambda: None, button)
    button.clicked.connect(handler)
    QTest.mouseClick(button, Qt.LeftButton)
    qapp.processEvents()
    if state == "hidden":
        button.hide()
    else:
        button.setEnabled(False)

    with pytest.raises(AssertionError, match="must be visible and enabled"):
        build_surface_witness(
            surface_id="test.apply",
            case_id="headless.test_apply",
            attribute="apply_btn",
            interaction="click",
            oracle="effect",
            action_spy=handler,
            control=button,
        )
    root.close()


def test_gui_coverage_sources_do_not_hard_code_successful_action_count():
    """Every passing GUI witness must obtain its count from an observed action."""
    def literal_value(node):
        return getattr(node, "value", getattr(node, "s", getattr(node, "n", None)))

    def is_literal_one(node):
        value = literal_value(node)
        return type(value) is int and value == 1

    gui_dir = Path(__file__).parents[1] / "gui"
    violations = []
    for source_path in sorted(gui_dir.glob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if (
                        literal_value(key) == "action_count"
                        and is_literal_one(value)
                    ):
                        violations.append("{}:{} dict literal".format(source_path, node.lineno))
            elif isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if (
                        keyword.arg == "action_count"
                        and is_literal_one(keyword.value)
                    ):
                        violations.append(
                            "{}:{} call argument".format(source_path, node.lineno)
                        )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                positional = node.args.args[-len(node.args.defaults) :]
                for argument, default in zip(positional, node.args.defaults):
                    if (
                        argument.arg == "action_count"
                        and is_literal_one(default)
                    ):
                        violations.append(
                            "{}:{} default argument".format(source_path, node.lineno)
                        )

    assert violations == []
