"""Headless contracts for coordinator-owned Info metadata editing."""

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

from tests.common.mock_ui import attach_mocks
from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.core.constants import (  # noqa: E402
    ATTR_MMD_COMMENT,
    ATTR_MMD_COMMENT_EN,
    ATTR_MMD_MODEL_NAME,
    ATTR_MMD_MODEL_NAME_EN,
)
from mmd_tools.ui.presenters.info_presenter import InfoPresenter  # noqa: E402


TEST_MODEL = "test_mmd_model"
_ATTR_VALUES = {
    f"{TEST_MODEL}.{ATTR_MMD_MODEL_NAME}": "テストモデル",
    f"{TEST_MODEL}.{ATTR_MMD_MODEL_NAME_EN}": "Test Model",
    f"{TEST_MODEL}.{ATTR_MMD_COMMENT}": "テストコメント",
    f"{TEST_MODEL}.{ATTR_MMD_COMMENT_EN}": "Test Comment",
}


class _FakeSignal:
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args):
        for callback in self._callbacks:
            callback(*args)


class _FakeSceneModelService:
    def __init__(self, exists=True, attr_values=None, attr_exists=True):
        self.exists = exists
        self.attr_values = attr_values or {}
        self.attr_exists = attr_exists

    def object_exists(self, node):
        return bool(node and self.exists)

    def attribute_exists(self, node, _attr):
        return bool(node and self.attr_exists)

    def get_attr_safe(self, node, attr, default=None):
        if not self.attribute_exists(node, attr):
            return default
        value = self.attr_values.get(f"{node}.{attr}", default)
        return value if value is not None else default


class _FakeAppState:
    def __init__(self, current_model_root=None, scene_model_service=None):
        self.current_model_root = current_model_root
        self.scene_model_service = scene_model_service or _FakeSceneModelService()
        self.current_model_changed = _FakeSignal()
        self.model_refresh_completed = _FakeSignal()
        self.refresh_generation = 0
        self.refreshing = False
        self.refresh_calls = []

    def refresh_model_list(self, explicit=False):
        self.refresh_calls.append(explicit)


class _FakeCoordinator:
    def __init__(self):
        self.calls = []
        self.changed = True
        self.fail_update = False
        self.fail_rollback_pending = None
        self.fail_refresh_notification = False

    def begin_info_metadata_edit(self, root, attr):
        self.calls.append(("begin", root, attr))
        return SimpleNamespace(root=f"|canonical|{root}", attr=attr, token=object())

    def update_info_metadata_edit(self, session, value):
        self.calls.append(("update", session.root, session.attr, value))
        if self.fail_update:
            raise RuntimeError("update failed after coordinator rollback")
        return True

    def commit_info_metadata_edit(self, session):
        self.calls.append(("commit", session.root, session.attr))
        return self.changed

    def rollback_info_metadata_edit(self, session):
        self.calls.append(("rollback", session.root, session.attr))
        if self.fail_rollback_pending is not None:
            error = RuntimeError("rollback failed")
            error.rollback_pending = self.fail_rollback_pending
            raise error


def _make_view():
    view = Mock()
    attach_mocks(view, ["set_fields_enabled"])
    view.edit_started = _FakeSignal()
    view.edit_finished = _FakeSignal()
    view.teardown = _FakeSignal()
    view.destroyed = _FakeSignal()
    for name in (
        "model_name_jp_edit",
        "model_name_en_edit",
        "comment_jp_edit",
        "comment_en_edit",
    ):
        widget = Mock()
        widget.textChanged = MagicMock()
        widget.textChanged.connect = Mock()
        setattr(view, name, widget)
    return view


def _make_presenter(model=TEST_MODEL, values=None, coordinator=True):
    view = _make_view()
    service = _FakeSceneModelService(
        attr_values=values if values is not None else dict(_ATTR_VALUES)
    )
    app_state = _FakeAppState(model, service)
    authoring = _FakeCoordinator() if coordinator is True else coordinator
    presenter = InfoPresenter(
        view, app_state, authoring_coordinator=authoring
    )
    return presenter, view, app_state, authoring


def test_initial_load_uses_read_service_and_enables_fields() -> None:
    _presenter, view, _app_state, _coordinator = _make_presenter()
    view.set_fields_enabled.assert_called_with(True)
    view.model_name_jp_edit.setText.assert_called_with("テストモデル")
    view.model_name_en_edit.setText.assert_called_with("Test Model")
    view.comment_jp_edit.setPlainText.assert_called_with("テストコメント")
    view.comment_en_edit.setPlainText.assert_called_with("Test Comment")


def test_missing_composition_preserves_reads_but_writes_fail_closed() -> None:
    presenter, view, _app_state, coordinator = _make_presenter(coordinator=None)
    assert coordinator is None
    view.model_name_jp_edit.setText.assert_called_with("テストモデル")
    view.edit_started.emit(view.model_name_jp_edit)
    presenter.update_model_info(ATTR_MMD_MODEL_NAME, "blocked")
    assert presenter._edit_session is None


def test_missing_model_clears_and_disables_fields() -> None:
    presenter, view, app_state, _coordinator = _make_presenter()
    app_state.current_model_root = None
    presenter.load_model_info()
    view.set_fields_enabled.assert_called_with(False)
    view.model_name_jp_edit.clear.assert_called()
    view.comment_jp_edit.clear.assert_called()


def test_focus_session_updates_fixed_field_and_refreshes_once() -> None:
    presenter, view, app_state, coordinator = _make_presenter()
    widget = view.model_name_jp_edit
    view.edit_started.emit(widget)
    presenter.update_model_info(ATTR_MMD_MODEL_NAME, "一")
    presenter.update_model_info(ATTR_MMD_MODEL_NAME, "二")
    view.edit_finished.emit(widget)
    assert coordinator.calls == [
        ("begin", TEST_MODEL, ATTR_MMD_MODEL_NAME),
        ("update", f"|canonical|{TEST_MODEL}", ATTR_MMD_MODEL_NAME, "一"),
        ("update", f"|canonical|{TEST_MODEL}", ATTR_MMD_MODEL_NAME, "二"),
        ("commit", f"|canonical|{TEST_MODEL}", ATTR_MMD_MODEL_NAME),
    ]
    assert app_state.refresh_calls == [True]


def test_unchanged_focus_commit_does_not_refresh() -> None:
    presenter, view, app_state, coordinator = _make_presenter()
    coordinator.changed = False
    widget = view.comment_jp_edit
    view.edit_started.emit(widget)
    presenter.update_model_info(ATTR_MMD_COMMENT, "same")
    view.edit_finished.emit(widget)
    assert app_state.refresh_calls == []


def test_text_changed_lazily_begins_session_for_focused_widget() -> None:
    presenter, view, _app_state, coordinator = _make_presenter()
    widget = view.model_name_en_edit
    widget.hasFocus.return_value = True
    widget.text.return_value = "lazy"
    presenter._text_change_callbacks[ATTR_MMD_MODEL_NAME_EN]()
    assert coordinator.calls[:2] == [
        ("begin", TEST_MODEL, ATTR_MMD_MODEL_NAME_EN),
        ("update", f"|canonical|{TEST_MODEL}", ATTR_MMD_MODEL_NAME_EN, "lazy"),
    ]


def test_different_field_callback_rolls_back_instead_of_retargeting() -> None:
    presenter, view, _app_state, coordinator = _make_presenter()
    view.edit_started.emit(view.model_name_jp_edit)
    presenter.update_model_info(ATTR_MMD_COMMENT, "stale")
    assert coordinator.calls[-1] == (
        "rollback",
        f"|canonical|{TEST_MODEL}",
        ATTR_MMD_MODEL_NAME,
    )


def test_model_switch_rolls_back_old_root_before_loading_new_values() -> None:
    presenter, view, app_state, coordinator = _make_presenter()
    view.comment_en_edit.toPlainText.return_value = "old comment"
    view.edit_started.emit(view.comment_en_edit)
    view.comment_en_edit.setPlainText.reset_mock()
    app_state.current_model_root = "new_root"
    app_state.scene_model_service.attr_values = {
        "new_root.mmd_model_name": "New",
    }
    presenter.on_current_model_changed("new_root")
    assert coordinator.calls[-1] == (
        "rollback",
        f"|canonical|{TEST_MODEL}",
        ATTR_MMD_COMMENT_EN,
    )
    view.model_name_jp_edit.setText.assert_called_with("New")
    assert not any(
        call.args == ("old comment",)
        for call in view.comment_en_edit.setPlainText.call_args_list
    )


def test_pending_rollback_blocks_new_root_load_and_keeps_retry_identity() -> None:
    presenter, view, app_state, coordinator = _make_presenter()
    view.model_name_jp_edit.text.return_value = "old UI"
    view.edit_started.emit(view.model_name_jp_edit)
    coordinator.fail_rollback_pending = True
    app_state.current_model_root = "new_root"
    app_state.scene_model_service.attr_values = {
        "new_root.mmd_model_name": "New authoritative",
    }
    view.model_name_jp_edit.setText.reset_mock()
    presenter.on_current_model_changed("new_root")
    assert presenter._edit_session is not None
    view.model_name_jp_edit.setText.assert_not_called()

    # Re-focusing the same field retries rollback; it must not unblock the
    # unresolved transaction or start sending updates into it.
    view.edit_started.emit(view.model_name_jp_edit)
    presenter.update_model_info(ATTR_MMD_MODEL_NAME, "must not write")
    assert presenter._edit_session_blocked is True
    assert not any(call[0] == "update" for call in coordinator.calls)

    coordinator.fail_rollback_pending = None
    view.edit_started.emit(view.model_name_jp_edit)
    assert presenter._edit_session_blocked is False
    assert not any(
        call == ("begin", "new_root", ATTR_MMD_MODEL_NAME)
        for call in coordinator.calls
    )
    view.model_name_jp_edit.setText.assert_called_with("New authoritative")

    # The next genuine FocusIn captures only the authoritative new-root text.
    view.model_name_jp_edit.text.return_value = "New authoritative"
    view.edit_started.emit(view.model_name_jp_edit)
    assert coordinator.calls[-1] == ("begin", "new_root", ATTR_MMD_MODEL_NAME)


def test_terminal_rollback_failure_clears_dead_session_and_allows_new_load() -> None:
    presenter, view, app_state, coordinator = _make_presenter()
    view.edit_started.emit(view.model_name_jp_edit)
    coordinator.fail_rollback_pending = False
    app_state.current_model_root = "new_root"
    app_state.scene_model_service.attr_values = {
        "new_root.mmd_model_name": "New",
    }
    presenter.on_current_model_changed("new_root")
    assert presenter._edit_session is None
    view.model_name_jp_edit.setText.assert_called_with("New")


def test_focus_out_never_commits_a_blocked_pending_session() -> None:
    presenter, view, app_state, coordinator = _make_presenter()
    view.model_name_jp_edit.text.return_value = "old"
    view.edit_started.emit(view.model_name_jp_edit)
    coordinator.fail_rollback_pending = True
    app_state.current_model_root = "new_root"
    presenter.on_current_model_changed("new_root")

    view.edit_finished.emit(view.model_name_jp_edit)
    assert presenter._edit_session is not None
    assert not any(call[0] == "commit" for call in coordinator.calls)
    assert app_state.refresh_calls == []

    coordinator.fail_rollback_pending = None
    view.edit_finished.emit(view.model_name_jp_edit)
    assert presenter._edit_session is None
    assert not any(call[0] == "commit" for call in coordinator.calls)
    assert app_state.refresh_calls == []


def test_terminal_rollback_on_field_change_defers_new_begin_to_next_focus() -> None:
    presenter, view, _app_state, coordinator = _make_presenter()
    view.edit_started.emit(view.model_name_jp_edit)
    view.model_name_jp_edit.setText.reset_mock()
    coordinator.fail_rollback_pending = False
    view.edit_started.emit(view.comment_jp_edit)
    assert [call[0] for call in coordinator.calls].count("begin") == 1
    assert presenter._edit_session is None
    assert presenter._edit_session_blocked is True
    view.model_name_jp_edit.setText.assert_called_with("テストモデル")

    coordinator.fail_rollback_pending = None
    view.edit_started.emit(view.comment_jp_edit)
    assert [call[0] for call in coordinator.calls].count("begin") == 2
    assert presenter._edit_session.attr == ATTR_MMD_COMMENT


def test_terminal_retry_of_blocked_session_stays_closed_for_current_focus() -> None:
    presenter, view, _app_state, coordinator = _make_presenter()
    view.edit_started.emit(view.model_name_jp_edit)
    presenter._edit_session_blocked = True
    coordinator.fail_rollback_pending = False
    view.edit_started.emit(view.model_name_jp_edit)
    assert presenter._edit_session is None
    assert presenter._edit_session_blocked is True
    assert [call[0] for call in coordinator.calls].count("begin") == 1

def test_refresh_and_teardown_rollback_active_session() -> None:
    presenter, view, _app_state, coordinator = _make_presenter()
    view.edit_started.emit(view.comment_jp_edit)
    presenter.on_model_refresh(3)
    assert coordinator.calls[-1][0] == "rollback"
    view.edit_started.emit(view.comment_jp_edit)
    view.destroyed.emit()
    assert [call[0] for call in coordinator.calls].count("rollback") == 2


def test_generation_refresh_does_not_load_or_consume_while_rollback_pending() -> None:
    presenter, view, _app_state, coordinator = _make_presenter()
    view.model_name_jp_edit.text.return_value = "visible edit"
    view.edit_started.emit(view.model_name_jp_edit)
    coordinator.fail_rollback_pending = True
    presenter._pending_refresh_generation = 7
    presenter._last_refresh_generation = 6
    view.model_name_jp_edit.setText.reset_mock()

    assert presenter.refresh_for_generation(7) is False
    assert presenter._edit_session is not None
    assert presenter._last_refresh_generation == 6
    assert presenter._pending_refresh_generation == 7
    view.model_name_jp_edit.setText.assert_not_called()


def test_update_failure_is_not_rolled_back_twice_by_presenter() -> None:
    presenter, view, _app_state, coordinator = _make_presenter()
    coordinator.fail_update = True
    view.model_name_jp_edit.text.return_value = "preimage"
    view.edit_started.emit(view.model_name_jp_edit)
    presenter.update_model_info(ATTR_MMD_MODEL_NAME, "bad")
    presenter.rollback_edit_session()
    assert not any(call[0] == "rollback" for call in coordinator.calls)
    assert presenter._edit_session is None
    view.model_name_jp_edit.setText.assert_called_with("テストモデル")


def test_lazy_first_change_failure_restores_scene_authority_not_post_change_text() -> None:
    presenter, view, _app_state, coordinator = _make_presenter()
    coordinator.fail_update = True
    widget = view.model_name_jp_edit
    widget.hasFocus.return_value = True
    widget.text.return_value = "post-change"
    widget.setText.reset_mock()
    presenter._text_change_callbacks[ATTR_MMD_MODEL_NAME]()
    widget.setText.assert_called_with("テストモデル")
    assert not any(call.args == ("post-change",) for call in widget.setText.call_args_list)


def test_post_commit_refresh_failure_does_not_trigger_rollback() -> None:
    presenter, view, app_state, coordinator = _make_presenter()

    def fail_refresh(explicit=False):
        raise RuntimeError(f"notification failed: {explicit}")

    app_state.refresh_model_list = fail_refresh
    view.edit_started.emit(view.model_name_jp_edit)
    presenter.update_model_info(ATTR_MMD_MODEL_NAME, "committed")
    view.edit_finished.emit(view.model_name_jp_edit)
    assert coordinator.calls[-1][0] == "commit"
    assert not any(call[0] == "rollback" for call in coordinator.calls)
