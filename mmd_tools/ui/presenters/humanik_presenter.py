"""Presenter for the HumanIK tab.

The tab is a thin status display over ``describe_frontend_state()`` plus
buttons that dispatch to the existing ``humanik_menu_actions`` functions --
this presenter intentionally does not reimplement model resolution UX,
confirmation dialogs, or error reporting, all of which already live in the
menu action layer and are reused as-is here.
"""

from __future__ import annotations

from ...core.logger import get_logger
from .. import humanik_menu_actions as default_actions

logger = get_logger(__name__)


class HumanIkPresenter:
    """Load HumanIK lifecycle state from the session and drive the tab view."""

    # Tab button attribute -> stable action name accepted by
    # ``humanik_menu_actions.dispatch_action``. ``bake_to_mmd_rig`` is handled
    # separately (see ``_on_bake_clicked``) because it needs the frame-range
    # SpinBoxes applied to Maya's playback range first.
    _DISPATCH_ATTR_TO_ACTION = {
        "setup_characterize_btn": "setup_and_characterize",
        "enter_source_btn": "enter_source_mode",
        "enter_target_btn": "enter_target_mode",
        "create_control_rig_btn": "create_control_rig",
        "restore_btn": "restore_mmd_rig",
        "diagnostics_btn": "diagnostics",
    }

    def __init__(self, view, app_state, *, actions_module=None, cmds_module=None, **_kwargs):
        self.view = view
        self.app_state = app_state
        self._actions = actions_module or default_actions
        self._cmds_module = cmds_module
        self._active = False
        self._connect_signals()

    # -- wiring ------------------------------------------------------------

    def _connect_signals(self):
        refresh_btn = getattr(self.view, "refresh_btn", None)
        if refresh_btn is not None:
            refresh_btn.clicked.connect(self.refresh)

        for attr, action_name in self._DISPATCH_ATTR_TO_ACTION.items():
            button = getattr(self.view, attr, None)
            if button is not None:
                button.clicked.connect(self._make_dispatcher(action_name))

        bake_btn = getattr(self.view, "bake_btn", None)
        if bake_btn is not None:
            bake_btn.clicked.connect(self._on_bake_clicked)

    def _make_dispatcher(self, action_name):
        def _handler(*_args):
            self._dispatch(action_name)

        return _handler

    def _dispatch(self, action_name):
        try:
            self._actions.dispatch_action(action_name)
        except Exception:
            # The menu action layer already reports failures to the user
            # (see ``_report_action_failure``/``_display_error``); this is
            # only a safety net so a stray exception can never skip refresh.
            logger.debug("HumanIK tab dispatch of '%s' raised", action_name, exc_info=True)
        finally:
            self.refresh()

    def _on_bake_clicked(self, *_args):
        # The SpinBox values are passed to the menu action explicitly; the
        # user's playback range is never modified as a side effect of baking.
        start, end = self.view.bake_frame_range()
        try:
            self._actions.bake_to_mmd_rig(int(start), int(end))
        except Exception:
            # Same safety net as ``_dispatch``: the menu action layer already
            # reports failures to the user.
            logger.debug("HumanIK tab bake dispatch raised", exc_info=True)
        finally:
            self.refresh()

    def _maya_cmds(self):
        if self._cmds_module is not None:
            return self._cmds_module
        try:
            from maya import cmds

            return cmds
        except Exception:
            return None

    # -- lifecycle -----------------------------------------------------

    def on_tab_activated(self):
        """Called when the HumanIK tab becomes the active main tab.

        Scene state is only read while this tab is visible: activation is
        the sole trigger for a scan, matching the lazy-refresh pattern the
        Morph/Physics tabs use in ``MainWindow._on_main_tab_changed``.
        """
        self._active = True
        self.refresh()

    def on_tab_deactivated(self):
        self._active = False

    # -- refresh -------------------------------------------------------

    def refresh(self, *_args):
        model_root = self._resolve_display_model_root()
        session = self._actions.get_humanik_session()
        try:
            state = session.describe_frontend_state(model_root)
        except Exception:
            logger.warning("HumanIK describe_frontend_state failed", exc_info=True)
            state = {}
        self.view.set_state(state)
        self._sync_bake_frame_range()

    def _resolve_display_model_root(self):
        """Resolve a model for display only: selection-derived, never a dialog.

        Unlike the menu actions' ``resolve_model_root``, a status refresh
        must never surprise the user with a picker dialog or an exception --
        an empty or ambiguous selection both fall back to ``None`` (a
        model-agnostic snapshot).
        """
        try:
            return self._actions.resolve_selected_model_root_for_display(
                cmds_module=self._cmds_module
            )
        except Exception:
            logger.debug("HumanIK tab model resolution for display failed", exc_info=True)
            return None

    def _sync_bake_frame_range(self):
        cmds = self._maya_cmds()
        if cmds is None:
            return
        try:
            start = int(round(float(cmds.playbackOptions(query=True, minTime=True))))
            end = int(round(float(cmds.playbackOptions(query=True, maxTime=True))))
        except Exception:
            logger.debug("HumanIK tab could not read the current playback range", exc_info=True)
            return
        self.view.set_bake_frame_range(start, end)
