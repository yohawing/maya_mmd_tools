"""Presenter for the HumanIK tab.

HUMANIK-FRONTEND-1 Phase B4: the tab is now a pair-specified retarget UI --
a "Character" combo (the MMD model this window acts on) and a "Source" combo
("None" or another scene MMD model; picking one connects the retarget,
picking "None" disconnects) -- plus the existing status header and the
Control Rig/Bake/Restore-Diagnostics action buttons. This presenter owns:

* populating both combos every refresh from the scene's MMD model list;
* the Character combo's selection policy (Maya-selection-follow, sticky
  last-picked value while nothing is selected, single-model auto-adopt, and
  "a manual pick wins until the *next* Maya selection change" -- see
  ``_resolve_character_root``);
* the Source combo's selection being driven by backend truth every refresh
  (``describe_frontend_state()``'s ``source`` binding), never by what the
  user last clicked -- a failed/cancelled connect or disconnect must show up
  as the combo snapping back to the real state;
* dispatching the Source combo's change to ``connect_retarget``/
  ``disconnect_retarget``, and the four remaining action buttons to
  ``humanik_menu_actions`` functions, exactly as before.

This presenter still does not reimplement model resolution UX, confirmation
dialogs, or error reporting -- all of that lives in the menu action layer
and is reused as-is here.
"""

from __future__ import annotations

from ...core.logger import get_logger
from .. import humanik_menu_actions as default_actions

logger = get_logger(__name__)


class HumanIkPresenter:
    """Load HumanIK lifecycle state from the session and drive the tab view."""

    # Tab button attribute -> stable action name accepted by
    # ``humanik_menu_actions.dispatch_action``. ``bake_to_mmd_rig`` is handled
    # separately (see ``_on_bake_clicked``) because it passes the frame-range
    # SpinBox values to the action explicitly. ``setup_and_characterize``/
    # ``enter_source_mode``/``enter_target_mode`` have no button anymore --
    # the Character/Source combos replace them (HUMANIK-FRONTEND-1 Phase B4).
    _DISPATCH_ATTR_TO_ACTION = {
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
        # Character combo selection-policy state (see
        # ``_resolve_character_root``): the last Maya-selection-derived root
        # observed (to detect "the selection just changed"), a user's manual
        # combo pick that wins until that happens, and the last value
        # actually shown (sticky fallback while nothing else applies).
        self._last_seen_selection = None
        self._character_override = None
        self._character_sticky = None
        self._character_root = None
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

        character_combo = getattr(self.view, "character_combo", None)
        if character_combo is not None:
            character_combo.currentIndexChanged.connect(self._on_character_combo_changed)

        source_combo = getattr(self.view, "source_combo", None)
        if source_combo is not None:
            source_combo.currentIndexChanged.connect(self._on_source_combo_changed)

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

    def _on_character_combo_changed(self, _index=None):
        combo = getattr(self.view, "character_combo", None)
        if combo is None:
            return
        value = combo.currentData()
        if not value:
            return
        # A manual pick wins over Maya-selection-follow until the selection
        # itself changes (see ``_resolve_character_root``).
        self._character_override = value
        self.refresh()

    def _on_source_combo_changed(self, _index=None):
        combo = getattr(self.view, "source_combo", None)
        if combo is None:
            return
        value = combo.currentData()
        character_root = self._character_root
        try:
            if value:
                # ``connect_retarget`` itself validates both models are
                # present/distinct and reports any failure to the user; a
                # missing Character model surfaces the same way as any other
                # connect failure, rather than being silently swallowed here.
                self._actions.connect_retarget(value, character_root)
            else:
                self._actions.disconnect_retarget()
        except Exception:
            # Safety net matching ``_dispatch``: the action layer already
            # reports failures to the user; refresh below re-syncs the combo
            # to whatever the real backend state ended up being.
            logger.debug("HumanIK tab source combo dispatch raised", exc_info=True)
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

        Also subscribes to ``humanik_control_rig_watch``'s pluggable warning
        callback (HUMANIK-FRONTEND-1 Phase C) for as long as this tab is
        active, so an out-of-band Control Rig created through Maya's own
        HumanIK UI while the tab is visible shows up as a banner here, not
        only as a logged/``cmds.warning`` message. See
        ``_on_control_rig_watch_warning`` for the "tab not visible -> do
        nothing" guard this registration makes redundant but that method
        keeps anyway as a second line of defense.
        """
        self._active = True
        self._register_control_rig_watch_callback()
        self.refresh()

    def on_tab_deactivated(self):
        self._active = False
        self._deregister_control_rig_watch_callback()

    def _register_control_rig_watch_callback(self):
        try:
            from ...core import humanik_control_rig_watch

            humanik_control_rig_watch.register_control_rig_warning_callback(
                self._on_control_rig_watch_warning
            )
        except Exception:
            logger.debug(
                "HumanIK tab could not subscribe to the control rig watch callback",
                exc_info=True,
            )

    def _deregister_control_rig_watch_callback(self):
        try:
            from ...core import humanik_control_rig_watch

            humanik_control_rig_watch.deregister_control_rig_warning_callback(
                self._on_control_rig_watch_warning
            )
        except Exception:
            logger.debug(
                "HumanIK tab could not unsubscribe from the control rig watch callback",
                exc_info=True,
            )

    def _on_control_rig_watch_warning(self, _message, *, character=None, model_root=None):
        """Push a live ``humanik_control_rig_watch`` warning onto the tab.

        Invoked synchronously from ``humanik_control_rig_watch``'s
        ``maya.utils.executeDeferred`` handler -- per that module's docstring
        this always runs on Maya's main thread once idle events are flushed
        (never from a worker thread), so this may touch Qt widgets directly
        without a cross-thread signal/slot hop. Still guarded: this callback
        stays registered only while the tab is active (see
        ``on_tab_activated``/``on_tab_deactivated``), but ``_active`` is
        re-checked here too in case a deregister racing a queued
        ``executeDeferred`` call ever slips through -- if the tab is not
        active, or the view has no banner method (a bare/test view double),
        this does nothing.
        """
        if not self._active:
            return
        show_banner = getattr(self.view, "show_control_rig_warning", None)
        if show_banner is None:
            return
        try:
            show_banner(character=character, model_root=model_root)
        except Exception:
            logger.debug("HumanIK tab failed to render control rig watch warning", exc_info=True)

    # -- refresh -------------------------------------------------------

    def refresh(self, *_args):
        models = self._list_scene_models()
        selected_root = self._resolve_display_model_root()
        character_root = self._resolve_character_root(selected_root, models)
        self._character_root = character_root

        character_options = [(self._label_for(root), root) for root in models]
        self._set_character_options(character_options, character_root)

        session = self._actions.get_humanik_session()
        try:
            state = session.describe_frontend_state(character_root)
        except Exception:
            logger.warning("HumanIK describe_frontend_state failed", exc_info=True)
            state = {}
        self.view.set_state(state)

        self._refresh_source_combo(models, character_root, state, session)
        self._sync_bake_frame_range()

    def _refresh_source_combo(self, models, character_root, state, session):
        """Populate the Source combo and select it from backend truth, not memory.

        ``state["source"]`` (from ``describe_frontend_state``) is the single
        source of truth for what is actually bound as SOURCE right now; a
        failed or cancelled connect/disconnect must show up here as the
        combo reverting to what the backend actually has, never to whatever
        the user most recently clicked.

        HUMANIK-EXTERNAL-SOURCE-1 ES-3: besides "None" and every other scene
        MMD model, the combo also lists every scene HIK character that is
        *not* MMD-driven (``session.list_source_candidates()``'s
        ``isMmd=False`` rows -- e.g. a mocap performer characterized outside
        mmd_tools), labeled ``"<character> (HIK)"``. An unlocked external
        character is still listed (rather than hidden or pre-disabled) --
        picking one dispatches to ``connect_retarget`` exactly like any other
        item, and ``enter_external_source_mode``'s existing not-locked
        RuntimeError surfaces through the same error-reporting path every
        other connect failure already uses; the combo then re-syncs to
        backend truth (still "None") on the next refresh. Item data is a
        ``("mmd", model_root)``/``("external", character)`` pair so
        ``humanik_menu_actions.connect_retarget`` can tell the two kinds
        apart; ``None`` still means "no source" (disconnect).
        """
        none_label = self.view.tr("humanik_none", "labels") if hasattr(self.view, "tr") else "None"
        options = [(none_label, None)]
        for root in models:
            if root == character_root:
                continue
            options.append((self._label_for(root), ("mmd", root)))
        for row in self._list_external_source_candidates(session):
            character = row.get("character")
            if not character:
                continue
            options.append((f"{character} (HIK)", ("external", character)))
        source_binding = (state or {}).get("source") or {}
        if source_binding.get("external"):
            backend_source = ("external", source_binding.get("character"))
        elif source_binding.get("modelRoot"):
            backend_source = ("mmd", source_binding.get("modelRoot"))
        else:
            backend_source = None
        self._set_source_options(options, backend_source)

    def _list_external_source_candidates(self, session):
        lister = getattr(session, "list_source_candidates", None)
        if not callable(lister):
            return []
        try:
            rows = lister() or []
        except Exception:
            logger.debug("HumanIK tab could not list external source candidates", exc_info=True)
            return []
        return [row for row in rows if not row.get("isMmd")]

    def _set_character_options(self, options, selected_value):
        setter = getattr(self.view, "set_character_options", None)
        if setter is not None:
            setter(options, selected_value)

    def _set_source_options(self, options, selected_value):
        setter = getattr(self.view, "set_source_options", None)
        if setter is not None:
            setter(options, selected_value)

    def _list_scene_models(self):
        lister = getattr(self._actions, "list_scene_mmd_models", None)
        if lister is None:
            return []
        try:
            return list(lister(cmds_module=self._cmds_module))
        except Exception:
            logger.debug("HumanIK tab could not list scene MMD models", exc_info=True)
            return []

    def _resolve_character_root(self, selected_root, models):
        """Resolve the Character combo's selected model root.

        Priority, matching the design decision in ``TODO.md``
        (HUMANIK-FRONTEND-1 Phase B4):

        1. The Maya selection, whenever it just *changed* to a resolvable
           model -- this always wins and clears any earlier manual pick.
        2. A manual combo pick (``_character_override``), as long as the
           selection has not changed since it was made and the picked model
           still exists in the scene.
        3. The current Maya selection, if any (covers the first refresh with
           a selection already made and no override yet).
        4. The last value shown (``_character_sticky``), if it still exists
           -- "no selection" sticks to whatever was last resolved.
        5. The scene's only model, if there is exactly one (also the
           fallback when several models exist and nothing else applies).
        """
        if selected_root != self._last_seen_selection:
            self._character_override = None
            self._last_seen_selection = selected_root

        if self._character_override is not None and self._character_override in models:
            root = self._character_override
        elif selected_root is not None and selected_root in models:
            root = selected_root
        elif self._character_sticky is not None and self._character_sticky in models:
            root = self._character_sticky
        elif models:
            root = models[0]
        else:
            root = None

        if root is not None:
            self._character_sticky = root
        return root

    @staticmethod
    def _label_for(model_root):
        value = str(model_root or "").strip("|")
        return value.rsplit("|", 1)[-1] or str(model_root)

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
