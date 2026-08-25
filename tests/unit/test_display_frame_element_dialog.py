"""Focused contracts for the display-frame element selection dialog."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from mmd_tools.ui.qt_compat import QApplication, Qt
    from mmd_tools.ui.widgets.display_frame_element_dialog import DisplayFrameElementDialog
except ImportError as exc:  # pragma: no cover - environments without Qt skip UI tests.
    pytest.skip(f"Qt binding unavailable: {exc}", allow_module_level=True)
if not callable(getattr(QApplication, "instance", None)):  # pragma: no cover - headless stubs.
    pytest.skip("Qt application object unavailable", allow_module_level=True)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _candidate_labels(dialog):
    return [dialog.candidate_list.item(row).text() for row in range(dialog.candidate_list.count())]


def test_search_type_index_identity_and_same_name_candidates(qapp):
    dialog = DisplayFrameElementDialog(
        [
            {"type": 0, "index": 3, "name": "Center"},
            {"type": 0, "index": 7, "name": "Center"},
            {"type": 1, "index": 2, "name": "Smile"},
        ],
        allowed_types=(0, 1),
    )
    assert dialog.element_type_combo.count() == 2
    assert {"Center [3]", "Center [7]"}.issubset(set(_candidate_labels(dialog)))

    dialog.search_edit.setText("7")
    assert _candidate_labels(dialog) == ["Center [7]"]
    dialog.candidate_list.setCurrentRow(0)
    assert dialog.candidate_list.currentItem().data(Qt.UserRole) == (0, 7)
    assert "7" in dialog.pmx_index_label.text()

    dialog.search_edit.clear()
    dialog.element_type_combo.setCurrentIndex(1)
    assert _candidate_labels(dialog) == ["Smile [2]"]


def test_facial_type_is_locked_and_cancel_or_disabled_selection_is_noop(qapp):
    dialog = DisplayFrameElementDialog(
        [{"type": 1, "index": 2, "name": "Smile"}],
        allowed_types=(1,),
    )
    assert dialog.element_type_combo.count() == 1
    assert not dialog.element_type_combo.isEnabled()
    dialog.reject()
    assert dialog.selected_element is None

    duplicate = DisplayFrameElementDialog(
        [
            {
                "type": 1,
                "index": 2,
                "name": "Smile",
                "disabled": True,
                "disabled_reason": "Already in this frame",
            }
        ],
        allowed_types=(1,),
    )
    assert "Already in this frame" in duplicate.candidate_list.item(0).toolTip()
    duplicate.candidate_list.setCurrentRow(0)
    duplicate._accept_selection()
    assert duplicate.selected_element is None
