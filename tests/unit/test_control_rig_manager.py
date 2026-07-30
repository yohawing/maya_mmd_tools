"""Pure contracts for the UUID-authoritative Control Rig Manager surface."""

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

# AnimatorToolsetWindow imports the legacy ``maya.OpenMayaUI`` module rather
# than the API 2.0 namespace covered by the shared headless stub.
maya_open_maya_ui = ModuleType("maya.OpenMayaUI")
maya_open_maya_ui.MQtUtil = MagicMock()
sys.modules["maya.OpenMayaUI"] = maya_open_maya_ui
import maya  # noqa: E402

maya.OpenMayaUI = maya_open_maya_ui

from mmd_tools.ui.control_rig_manager import ControlRigManagerWindow  # noqa: E402
from mmd_tools.ui.animator_toolset_window import AnimatorToolsetWindow  # noqa: E402


class _Combo:
    def __init__(self, uuid="model-uuid"):
        self.uuid = uuid

    def currentIndex(self):
        return 0

    def itemData(self, _index, _role):
        return self.uuid


def _manager(*, uuid="model-uuid", root="|character|MMDModel"):
    manager = ControlRigManagerWindow.__new__(ControlRigManagerWindow)
    manager.character_combo = _Combo(uuid)
    manager._model_records = [{"root": root, "uuid": uuid, "label": "Character"}]
    manager._cmds = MagicMock()
    manager._cmds.ls.side_effect = lambda value, **_kwargs: [root] if value == uuid else [uuid]
    manager._status_callback = None
    manager.state_changed = MagicMock()
    manager._set_status = MagicMock()
    manager.refresh = MagicMock()
    return manager


def test_selected_root_is_resolved_from_combo_uuid_not_display_name():
    manager = _manager()

    assert manager.selected_uuid() == "model-uuid"
    assert manager.selected_model_root() == "|character|MMDModel"
    manager._cmds.ls.assert_called_once_with("model-uuid", long=True)


def test_refresh_is_read_only_and_does_not_call_lifecycle_core(monkeypatch):
    manager = _manager()
    manager._scene_model_service = MagicMock()
    manager._scene_model_service.list_mmd_models.return_value = ["|character|MMDModel"]
    manager._scene_model_service.get_model_display_name.return_value = "Character"
    manager.character_combo = MagicMock()
    manager.character_combo.currentIndex.return_value = -1
    manager._sync_selected_state = MagicMock()
    del manager.refresh

    with patch("mmd_tools.core.mmd_control_rig_builder.build_mmd_control_rig") as build, patch(
        "mmd_tools.core.mmd_control_rig_motion.enter_mmd_control_rig_edit"
    ) as enter, patch("mmd_tools.core.mmd_control_rig_motion.bake_mmd_control_rig") as bake, patch(
        "mmd_tools.core.mmd_control_rig_motion.restore_mmd_control_rig_attached"
    ) as restore, patch("mmd_tools.core.mmd_control_rig_builder.remove_mmd_control_rig") as remove:
        manager.refresh()

    manager._scene_model_service.list_mmd_models.assert_called_once_with()
    build.assert_not_called()
    enter.assert_not_called()
    bake.assert_not_called()
    restore.assert_not_called()
    remove.assert_not_called()


def test_action_dispatch_uses_selected_uuid_root_and_core_transaction():
    manager = _manager()
    metadata = {"state": "EDIT", "owner": "CONTROL_OWNED"}
    with patch("mmd_tools.core.mmd_control_rig_builder.build_mmd_control_rig") as build, patch(
        "mmd_tools.core.mmd_control_rig_motion.enter_mmd_control_rig_edit",
        return_value=metadata,
    ) as enter:
        manager.perform_action("setup")

    build.assert_called_once_with("|character|MMDModel")
    enter.assert_called_once_with("|character|MMDModel")
    manager._set_status.assert_called_once()
    manager.state_changed.emit.assert_called_once_with("|character|MMDModel", "setup")


def test_show_manager_reuses_one_modeless_instance(monkeypatch):
    monkeypatch.setattr(ControlRigManagerWindow, "__init__", lambda self, **_kwargs: None)
    for method in ("refresh", "show", "raise_", "activateWindow"):
        monkeypatch.setattr(
            ControlRigManagerWindow,
            method,
            lambda self: None,
            raising=False,
        )
    ControlRigManagerWindow._instance = None

    assert ControlRigManagerWindow.show_manager() is not None
    instance = ControlRigManagerWindow._instance
    assert ControlRigManagerWindow.show_manager() is instance
    ControlRigManagerWindow._instance = None


def test_status_callback_failure_clears_destroyed_animator_sink():
    manager = ControlRigManagerWindow.__new__(ControlRigManagerWindow)
    manager._status_callback = MagicMock(
        side_effect=RuntimeError("wrapped C++ object has been deleted")
    )
    manager._translator = MagicMock()
    manager._translator.translate.return_value = "status"
    manager.diagnostics_label = MagicMock()

    manager._set_status("status_error", error=RuntimeError("stale"))

    assert manager._status_callback is None


def test_clear_status_callback_preserves_newer_animator_sink():
    manager = ControlRigManagerWindow.__new__(ControlRigManagerWindow)
    old_callback = MagicMock()
    new_callback = MagicMock()
    manager._status_callback = new_callback

    manager.clear_status_callback(old_callback)
    assert manager._status_callback is new_callback

    manager.clear_status_callback(new_callback)
    assert manager._status_callback is None


def test_animator_cleanup_detaches_manager_callbacks_before_presenter_teardown():
    manager = MagicMock()
    status_callback = MagicMock()
    state_callback = MagicMock()
    window = AnimatorToolsetWindow.__new__(AnimatorToolsetWindow)
    window._cleanup_done = False
    window._control_rig_manager = manager
    window._control_rig_status_callback = status_callback
    window._control_rig_state_callback = state_callback
    window._control_rig_manager_connected = True
    window._save_window_size = MagicMock()
    window.animation_presenter = MagicMock()

    AnimatorToolsetWindow._cleanup(window)

    manager.clear_status_callback.assert_called_once_with(status_callback)
    manager.state_changed.disconnect.assert_called_once_with(state_callback)
    window.animation_presenter.disconnect_signals.assert_called_once_with()
    assert window._control_rig_manager is None
    assert window._control_rig_status_callback is None
    assert window._control_rig_state_callback is None
    assert window._control_rig_manager_connected is False
