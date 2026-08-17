"""Presenter for the model metadata (Info) tab."""

from mmd_tools.core.constants import (
    ATTR_MMD_COMMENT,
    ATTR_MMD_COMMENT_EN,
    ATTR_MMD_MODEL_NAME_EN,
    ATTR_MMD_MODEL_NAME,
)
from ...core.logger import get_logger

logger = get_logger(__name__)


class InfoPresenter:
    """Keep Info widgets and model-root string attributes synchronized.

    Metadata remains immediate: every ``textChanged`` writes the affected
    attribute.  A focus session wraps those writes in one named Maya undo
    chunk, while the root captured at focus-in prevents a queued old-widget
    update from being applied to a newly selected model.
    """

    _FIELD_ATTRIBUTES = (
        ("model_name_jp_edit", ATTR_MMD_MODEL_NAME),
        ("model_name_en_edit", ATTR_MMD_MODEL_NAME_EN),
        ("comment_jp_edit", ATTR_MMD_COMMENT),
        ("comment_en_edit", ATTR_MMD_COMMENT_EN),
    )
    def __init__(self, view, app_state, authoring_coordinator=None):
        self.view = view
        self.app_state = app_state
        self.authoring_coordinator = authoring_coordinator
        self.scene_model_service = self.app_state.scene_model_service
        self._text_change_callbacks = {}
        self._edit_session = None
        self._edit_session_selected_root = None
        self._edit_session_widget = None
        self._edit_session_blocked = False
        self._loading = False
        self._pending_refresh_generation = None
        self._last_refresh_generation = None
        self.connect_signals()

        # 既に選択されているモデルがある場合は情報をロード
        if self.app_state.current_model_root:
            self.view.set_fields_enabled(True)
            self.load_model_info()

    def connect_signals(self):
        # ApplicationStateのシグナル
        self.app_state.current_model_changed.connect(self.on_current_model_changed)
        refresh_signal = getattr(self.app_state, "model_refresh_completed", None)
        if refresh_signal is not None and hasattr(refresh_signal, "connect"):
            refresh_signal.connect(self.on_model_refresh)

        # Focus transitions are supplied by InfoTab's shared event-filter seam.
        # Keep this optional for the headless presenter view stubs.
        edit_started = getattr(self.view, "edit_started", None)
        if edit_started is not None and hasattr(edit_started, "connect"):
            edit_started.connect(self.begin_edit_session)
        edit_finished = getattr(self.view, "edit_finished", None)
        if edit_finished is not None and hasattr(edit_finished, "connect"):
            edit_finished.connect(self.end_edit_session)
        teardown = getattr(self.view, "teardown", None)
        if teardown is not None and hasattr(teardown, "connect"):
            teardown.connect(self.rollback_edit_session)
        destroyed = getattr(self.view, "destroyed", None)
        if destroyed is not None and hasattr(destroyed, "connect"):
            destroyed.connect(self.rollback_edit_session)

        self._connect_text_signals()

    def _connect_text_signals(self):
        """Connect each widget to an attribute-specific immediate writer."""
        for widget_name, attr_name in self._FIELD_ATTRIBUTES:
            widget = getattr(self.view, widget_name)

            def _on_changed(*_args, _attr_name=attr_name, _widget=widget):
                # A model switch can keep keyboard focus on the same widget
                # while the old root's chunk is closed.  Re-open lazily on the
                # first real post-switch text change so that the new root is
                # still coalesced through its next FocusOut.
                has_focus = getattr(_widget, "hasFocus", lambda: False)
                if (
                    self._edit_session is None
                    and not self._loading
                    and not self._edit_session_blocked
                    and bool(has_focus())
                ):
                    self.begin_edit_session(_widget)
                self.update_model_info(_attr_name)

            self._text_change_callbacks[attr_name] = _on_changed
            widget.textChanged.connect(_on_changed)

    def _attr_for_widget(self, widget):
        for widget_name, attr in self._FIELD_ATTRIBUTES:
            if getattr(self.view, widget_name) is widget:
                return attr
        return None

    def begin_edit_session(self, widget=None, *_args):
        """Begin one coordinator-owned edit fixed to the focused field."""
        root = self.app_state.current_model_root
        attr = self._attr_for_widget(widget)
        if (
            self.authoring_coordinator is None
            or not root
            or attr is None
            or not self._model_exists(root)
        ):
            return
        if self._edit_session is not None:
            if self._edit_session_blocked:
                blocked_root = self._edit_session_selected_root
                self.rollback_edit_session()
                if self._edit_session is not None:
                    return
                if self._edit_session_blocked:
                    if blocked_root != self.app_state.current_model_root:
                        self.load_model_info()
                    return
                if blocked_root != self.app_state.current_model_root:
                    self.load_model_info()
                    return
            elif self._edit_session_selected_root == root and self._edit_session.attr == attr:
                return
            else:
                previous_root = self._edit_session_selected_root
                self.rollback_edit_session()
                if self._edit_session is not None:
                    return
                if self._edit_session_blocked:
                    if previous_root != self.app_state.current_model_root:
                        self.load_model_info()
                    return
        self._edit_session_blocked = False
        try:
            self._edit_session = self.authoring_coordinator.begin_info_metadata_edit(
                root, attr
            )
            self._edit_session_selected_root = root
            self._edit_session_widget = widget
        except Exception:
            logger.error("Failed to begin Info edit for '%s.%s'.", root, attr, exc_info=True)
            self._edit_session = None
            self._edit_session_selected_root = None
            self._edit_session_widget = None
            self._edit_session_blocked = True

    def end_edit_session(self, *_args):
        """Commit one completed focus edit and notify the model list once."""
        session = self._edit_session
        if session is None:
            self._edit_session_blocked = False
            return
        if self._edit_session_blocked:
            self.rollback_edit_session()
            return
        selected_root = self._edit_session_selected_root
        widget = self._edit_session_widget
        self._edit_session = None
        self._edit_session_selected_root = None
        self._edit_session_widget = None
        try:
            changed = self.authoring_coordinator.commit_info_metadata_edit(session)
        except Exception as exc:
            logger.error("Failed to commit Info edit.", exc_info=True)
            if getattr(exc, "rollback_pending", False):
                self._edit_session = session
                self._edit_session_selected_root = selected_root
                self._edit_session_widget = widget
            else:
                self._restore_authoritative_field(widget, session.attr, selected_root)
            self._edit_session_blocked = True
            return
        self._edit_session_blocked = False
        if changed:
            try:
                self.app_state.refresh_model_list(explicit=True)
            except Exception:
                # Notification is deliberately outside the committed Maya
                # transaction; it must never turn a valid edit into rollback.
                logger.error("Failed to refresh model list after Info edit.", exc_info=True)
        pending_generation = self._pending_refresh_generation
        if pending_generation is not None and not self._loading:
            self.refresh_for_generation(pending_generation)

    def rollback_edit_session(self, *_args):
        """Rollback the old fixed-root session; safe to call repeatedly."""
        session = self._edit_session
        if session is None:
            self._edit_session_blocked = False
            return
        selected_root = self._edit_session_selected_root
        widget = self._edit_session_widget
        try:
            self.authoring_coordinator.rollback_info_metadata_edit(session)
        except Exception as exc:
            logger.error("Failed to rollback Info edit.", exc_info=True)
            if not getattr(exc, "rollback_pending", True):
                self._edit_session = None
                self._edit_session_selected_root = None
                self._edit_session_widget = None
                self._restore_authoritative_field(
                    widget, session.attr, selected_root
                )
            self._edit_session_blocked = True
            return
        self._edit_session = None
        self._edit_session_selected_root = None
        self._edit_session_widget = None
        self._restore_authoritative_field(widget, session.attr, selected_root)
        self._edit_session_blocked = False

    def _restore_authoritative_field(self, widget, attr, selected_root):
        """Reload one fixed field after an unverified terminal rollback."""
        if (
            widget is None
            or selected_root != self.app_state.current_model_root
            or not self._model_exists(selected_root)
        ):
            return
        value = self.scene_model_service.get_attr_safe(selected_root, attr, None)
        if not isinstance(value, str):
            return
        previous = widget.blockSignals(True)
        was_loading = self._loading
        self._loading = True
        try:
            if attr in (ATTR_MMD_COMMENT, ATTR_MMD_COMMENT_EN):
                widget.setPlainText(value)
            else:
                widget.setText(value)
        finally:
            self._loading = was_loading
            widget.blockSignals(previous)

    # Explicit lifecycle alias for MainWindow/window teardown callers.
    shutdown = rollback_edit_session

    def _model_exists(self, model_root):
        try:
            return bool(self.scene_model_service.object_exists(model_root))
        except Exception:
            logger.error("Failed to query model '%s'.", model_root, exc_info=True)
            return False

    def on_current_model_changed(self, model_root):
        """Close the old-root session before loading the new root's values."""
        if getattr(self.app_state, "refreshing", False) is True:
            self.on_model_refresh(getattr(self.app_state, "refresh_generation", 0))
            return
        self._pending_refresh_generation = None
        # This order is intentional: loading text must never be interpreted as
        # an edit against the newly selected root.
        self.rollback_edit_session()
        if self._edit_session is not None:
            return
        self._loading = True
        try:
            if model_root:
                self.view.set_fields_enabled(True)
                self.load_model_info()
            else:
                self.view.set_fields_enabled(False)
                self.clear_fields()
        finally:
            self._loading = False

    def on_model_refresh(self, generation):
        """Rollback an old-root edit before accepting a refresh generation."""
        self.rollback_edit_session()
        if self._edit_session is not None:
            return
        self._pending_refresh_generation = generation

    def refresh_for_generation(self, generation):
        """Reload a visible tab once per generation, preserving focus edits."""
        if self._pending_refresh_generation != generation:
            if self._last_refresh_generation == generation:
                return True
            if self.load_model_info() is False:
                return False
            self._last_refresh_generation = generation
            return True
        if self.load_model_info() is False:
            return False
        self._pending_refresh_generation = None
        self._last_refresh_generation = generation
        return True

    def load_model_info(self):
        current_model_root = self.app_state.current_model_root
        if not current_model_root or not self._model_exists(current_model_root):
            logger.warning("No model selected or model does not exist.")
            self.rollback_edit_session()
            if self._edit_session is not None:
                return False
            self.view.set_fields_enabled(False)
            self.clear_fields()
            self._last_refresh_generation = getattr(
                self.app_state, "refresh_generation", 0
            )
            return True

        # A direct reload while a field is focused is also a transaction
        # boundary.  on_current_model_changed already performs this call, and
        # rollback_edit_session is idempotent.
        self.rollback_edit_session()
        if self._edit_session is not None:
            return False
        was_loading = self._loading
        self._loading = True
        try:
            # アトリビュートの存在を確認
            if not self.scene_model_service.attribute_exists(current_model_root, ATTR_MMD_MODEL_NAME):
                logger.warning("Attribute %s not found on %s", ATTR_MMD_MODEL_NAME, current_model_root)

            # 文字列アトリビュートの値を安全に取得
            model_name_jp = self.scene_model_service.get_attr_safe(current_model_root, ATTR_MMD_MODEL_NAME, "")
            model_name_en = self.scene_model_service.get_attr_safe(current_model_root, ATTR_MMD_MODEL_NAME_EN, "")
            comment_jp = self.scene_model_service.get_attr_safe(current_model_root, ATTR_MMD_COMMENT, "")
            comment_en = self.scene_model_service.get_attr_safe(current_model_root, ATTR_MMD_COMMENT_EN, "")

            logger.debug(f"Loaded values - JP: '{model_name_jp}', EN: '{model_name_en}'")

            self.view.model_name_jp_edit.setText(model_name_jp)
            self.view.model_name_en_edit.setText(model_name_en)
            self.view.comment_jp_edit.setPlainText(comment_jp)
            self.view.comment_en_edit.setPlainText(comment_en)

            logger.debug(f"Loaded model info for {current_model_root}")
        except Exception as exc:
            logger.error("Failed to load model info: %s", exc, exc_info=True)
            self.clear_fields()
        finally:
            self._loading = was_loading
        # Only a completed load consumes the generation.  A pending rollback
        # returns above and leaves the visible fields/generation untouched.
        self._last_refresh_generation = getattr(
            self.app_state, "refresh_generation", 0
        )
        return True

    def clear_fields(self):
        """Clear fields without turning the load into metadata writes."""
        was_loading = self._loading
        self._loading = True
        try:
            self.view.model_name_jp_edit.clear()
            self.view.model_name_en_edit.clear()
            self.view.comment_jp_edit.clear()
            self.view.comment_en_edit.clear()
        finally:
            self._loading = was_loading

    def _read_field_value(self, attr_name):
        for widget_name, field_attr in self._FIELD_ATTRIBUTES:
            if field_attr != attr_name:
                continue
            widget = getattr(self.view, widget_name)
            if widget_name.startswith("comment_"):
                return widget.toPlainText()
            return widget.text()
        raise KeyError(attr_name)

    def update_model_info(self, attr_name=None, value=None):
        """Immediately write the one field fixed by the active focus session."""
        if self._loading or self._edit_session_blocked:
            return
        session = self._edit_session
        if session is None:
            return
        if self._edit_session_selected_root != self.app_state.current_model_root:
            self.rollback_edit_session()
            return
        if attr_name != session.attr:
            logger.error("Info edit target changed during a focus session.")
            self.rollback_edit_session()
            return
        try:
            field_value = self._read_field_value(attr_name) if value is None else value
            self.authoring_coordinator.update_info_metadata_edit(session, field_value)
        except Exception as exc:
            # Coordinator owns rollback on mutation failure.
            logger.error("Failed to update Info metadata.", exc_info=True)
            if not getattr(exc, "rollback_pending", False):
                self._restore_authoritative_field(
                    self._edit_session_widget,
                    session.attr,
                    self._edit_session_selected_root,
                )
                self._edit_session = None
                self._edit_session_selected_root = None
                self._edit_session_widget = None
            self._edit_session_blocked = True
            return
        logger.debug(f"Updated model info for {session.root}")
