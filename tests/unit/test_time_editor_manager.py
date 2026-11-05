"""Headless contracts for the model-scoped Time Editor entry point."""
from unittest.mock import MagicMock, patch

import pytest

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()
from mmd_tools.ui.time_editor_manager import TimeEditorManagerWindow  # noqa: E402

CORE = "mmd_tools.core.mmd_time_editor"


def _manager(uuid="model-uuid"):
    manager = TimeEditorManagerWindow.__new__(TimeEditorManagerWindow)
    manager.character_combo = MagicMock()
    manager.character_combo.currentData.return_value = uuid
    manager._cmds = MagicMock()
    manager._cmds.ls.return_value = ["|renamed|Model"]
    manager._service = MagicMock()
    manager._translator = MagicMock()
    manager._translator.translate.side_effect = lambda key, _section: key
    manager.message_label = MagicMock()
    manager.state_label = MagicMock()
    manager.buttons = {key: MagicMock() for key in ("create", "remove", "open")}
    return manager


def test_selected_model_is_resolved_again_from_uuid():
    manager = _manager()
    assert manager.selected_model_root() == "|renamed|Model"
    manager._cmds.ls.assert_called_once_with("model-uuid", long=True)
    manager._cmds.ls.return_value = ["|newParent|RenamedAgain"]
    assert manager.selected_model_root() == "|newParent|RenamedAgain"


@pytest.mark.parametrize("matches", [[], ["|one", "|two"]])
def test_missing_or_ambiguous_uuid_is_not_a_model(matches):
    manager = _manager()
    manager._cmds.ls.return_value = matches
    assert manager.selected_model_root() is None


def test_refresh_only_lists_and_inspects_without_lifecycle_changes():
    manager = _manager()
    manager._service.list_mmd_models.return_value = ["|renamed|Model"]
    manager._cmds.ls.side_effect = lambda value, **kwargs: ["model-uuid"] if kwargs.get("uuid") else ["|renamed|Model"]
    with patch(CORE + ".create_model_clip") as create, patch(CORE + ".remove_model_clip") as remove, patch(CORE + ".inspect_model_clip", return_value={"active": True}) as inspect:
        manager.refresh()
    manager._service.list_mmd_models.assert_called_once_with()
    inspect.assert_called_once_with("|renamed|Model")
    create.assert_not_called()
    remove.assert_not_called()
    manager.buttons["create"].setEnabled.assert_called_once_with(False)
    manager.buttons["remove"].setEnabled.assert_called_once_with(True)
    manager.character_combo.blockSignals.assert_any_call(False)


@pytest.mark.parametrize("action", ["create", "remove"])
def test_explicit_action_dispatches_selected_root_and_refreshes(action):
    manager = _manager()
    manager.refresh = MagicMock()
    with patch(CORE + ".create_model_clip") as create, patch(CORE + ".remove_model_clip") as remove:
        manager.perform_action(action)
    called, untouched = (create, remove) if action == "create" else (remove, create)
    called.assert_called_once_with("|renamed|Model")
    untouched.assert_not_called()
    manager.refresh.assert_called_once_with()


def test_open_uses_native_window_without_model_or_lifecycle():
    manager = _manager(uuid=None)
    with patch(CORE + ".create_model_clip") as create, patch(CORE + ".remove_model_clip") as remove:
        manager.perform_action("open")
    manager._cmds.TimeEditorWindow.assert_called_once_with()
    manager._cmds.ls.assert_not_called()
    create.assert_not_called()
    remove.assert_not_called()


@pytest.mark.parametrize("action", ["create", "remove"])
def test_no_selection_rejects_lifecycle_action(action):
    manager = _manager(uuid=None)
    with patch(CORE + ".create_model_clip") as create, patch(CORE + ".remove_model_clip") as remove:
        manager.perform_action(action)
    create.assert_not_called()
    remove.assert_not_called()
    manager.message_label.setText.assert_called_once_with("no_model")


def test_inspection_failure_disables_both_mutating_buttons():
    manager = _manager()
    with patch(CORE + ".inspect_model_clip", side_effect=RuntimeError("ownership changed")):
        manager._refresh_status()
    manager.buttons["create"].setEnabled.assert_called_once_with(False)
    manager.buttons["remove"].setEnabled.assert_called_once_with(False)
    manager.message_label.setText.assert_called_once_with("ownership changed")
    manager.state_label.setText.assert_called_once_with("invalid")
