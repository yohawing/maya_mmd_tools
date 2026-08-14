"""Presenter for the model metadata (Info) tab."""

from maya import cmds

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
    _UNDO_CHUNK_NAME = "MMD Info Edit"

    def __init__(self, view, app_state):
        self.view = view
        self.app_state = app_state
        self.scene_model_service = self.app_state.scene_model_service
        self._text_change_callbacks = {}
        self._undo_chunk_open = False
        self._edit_session_root = None
        self._edit_session_blocked = False
        self._undo_state_uncertain = False
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
            teardown.connect(self.end_edit_session)
        destroyed = getattr(self.view, "destroyed", None)
        if destroyed is not None and hasattr(destroyed, "connect"):
            destroyed.connect(self.end_edit_session)

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
                    not self._undo_chunk_open
                    and not self._loading
                    and not self._edit_session_blocked
                    and bool(has_focus())
                ):
                    self.begin_edit_session()
                self.update_model_info(_attr_name)

            self._text_change_callbacks[attr_name] = _on_changed
            widget.textChanged.connect(_on_changed)

    def begin_edit_session(self, *_args):
        """Open one Maya undo chunk for the currently focused Info widget."""
        root = self.app_state.current_model_root
        if not root or not self._model_exists(root):
            return

        # A failed close leaves Maya's undo-stack state unknown.  Do not risk
        # nesting another chunk or writing outside a known-safe transaction;
        # a later teardown/retry must close the uncertain chunk first.
        if self._undo_state_uncertain:
            return

        # A fresh FocusIn starts a new attempt after a previous write/open
        # failure.  Until that point, fail closed and never write outside an
        # undo chunk.
        self._edit_session_blocked = False

        if self._undo_chunk_open:
            if self._edit_session_root == root:
                return
            # A focus-in from another root must never extend the old root's
            # transaction.
            self.end_edit_session()
            if self._undo_state_uncertain or self._undo_chunk_open or self._edit_session_blocked:
                return

        try:
            cmds.undoInfo(openChunk=True, chunkName=self._UNDO_CHUNK_NAME)
        except Exception:
            logger.error("Failed to open Info undo chunk for '%s'.", root, exc_info=True)
            self._undo_chunk_open = False
            self._edit_session_root = None
            self._edit_session_blocked = True
            return

        self._undo_chunk_open = True
        self._edit_session_root = root

    def end_edit_session(self, *_args):
        """Close an open Info chunk; safe to call repeatedly or at teardown."""
        if not self._undo_chunk_open:
            self._edit_session_root = None
            self._edit_session_blocked = False
            return

        try:
            cmds.undoInfo(closeChunk=True)
        except Exception:
            logger.error("Failed to close Info undo chunk.", exc_info=True)
            self._undo_state_uncertain = True
            self._edit_session_blocked = True
            # Keep the root/open markers so a later teardown can retry the
            # close operation instead of silently claiming the chunk closed.
            return

        self._undo_state_uncertain = False
        self._undo_chunk_open = False
        self._edit_session_root = None
        self._edit_session_blocked = False
        pending_generation = self._pending_refresh_generation
        if pending_generation is not None and not self._loading:
            self.refresh_for_generation(pending_generation)

    # Explicit lifecycle alias for MainWindow/window teardown callers.
    shutdown = end_edit_session

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
        self.end_edit_session()
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
        """Mark metadata stale without touching Maya or an active edit chunk."""
        self._pending_refresh_generation = generation

    def refresh_for_generation(self, generation):
        """Reload a visible tab once per generation, preserving focus edits."""
        if self._pending_refresh_generation != generation:
            if self._last_refresh_generation == generation:
                return True
            self.load_model_info()
            self._last_refresh_generation = generation
            return True
        if self._undo_chunk_open or self._edit_session_root is not None:
            self._last_refresh_generation = generation
            return True
        self.load_model_info()
        self._pending_refresh_generation = None
        self._last_refresh_generation = generation
        return True

    def load_model_info(self):
        # Any completed direct load (including constructor/eager loads) has
        # consumed the current generation, so tab activation cannot duplicate
        # the same graph read.
        self._last_refresh_generation = getattr(self.app_state, "refresh_generation", 0)
        current_model_root = self.app_state.current_model_root
        if not current_model_root or not self._model_exists(current_model_root):
            logger.warning("No model selected or model does not exist.")
            self.end_edit_session()
            self.view.set_fields_enabled(False)
            self.clear_fields()
            return

        # A direct reload while a field is focused is also a transaction
        # boundary.  on_current_model_changed already performs this call, and
        # end_edit_session is idempotent.
        self.end_edit_session()
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

    def _set_undoable_string(self, model_root, attr_name, value):
        """Write one Info string through cmds.setAttr for Maya undo support."""
        try:
            if not self.scene_model_service.attribute_exists(model_root, attr_name):
                logger.error("Cannot write missing Info attribute '%s.%s'.", model_root, attr_name)
                return False
            cmds.setAttr("%s.%s" % (model_root, attr_name), "" if value is None else str(value), type="string")
            return True
        except Exception as exc:
            logger.error("Failed to update Info attribute '%s.%s': %s", model_root, attr_name, exc, exc_info=True)
            return False

    def update_model_info(self, attr_name=None, value=None):
        """Immediately write one changed field (or all fields for direct callers)."""
        if self._loading or self._edit_session_blocked or self._undo_state_uncertain:
            return

        current_model_root = self.app_state.current_model_root
        if self._edit_session_root is not None:
            # A stale callback after model switching is fail-closed.  Never
            # retarget old text to the new model root.
            if self._edit_session_root != current_model_root:
                self.end_edit_session()
                return
            current_model_root = self._edit_session_root

        if not current_model_root or not self._model_exists(current_model_root):
            self.end_edit_session()
            return

        # Every production textChanged write must be inside the session opened
        # for the same model root.  Direct callers must explicitly open a
        # bounded session first.
        if not self._undo_chunk_open or self._edit_session_root != current_model_root:
            return

        if attr_name is None:
            updates = [(field_attr, self._read_field_value(field_attr)) for _, field_attr in self._FIELD_ATTRIBUTES]
        else:
            try:
                updates = [(attr_name, self._read_field_value(attr_name) if value is None else value)]
            except KeyError:
                logger.error("Unknown Info field attribute '%s'.", attr_name)
                self.end_edit_session()
                return

        for field_attr, field_value in updates:
            if not self._set_undoable_string(current_model_root, field_attr, field_value):
                # An error must not leave a chunk open around a partially
                # applied edit.
                had_session = self._undo_chunk_open or self._edit_session_root is not None
                self.end_edit_session()
                if had_session:
                    self._edit_session_blocked = True
                return

        logger.debug(f"Updated model info for {current_model_root}")
        try:
            self.app_state.clear_cache()
        except Exception:
            logger.error("Failed to clear model info cache.", exc_info=True)
