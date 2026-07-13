"""Development-only placeholder presenter for a future physics backend."""

from __future__ import annotations


class PhysicsPresenter:
    """Keep the Physics tab usable until its replacement backend is ready."""

    def __init__(self, view, app_state, **_kwargs):
        self.view = view
        self.app_state = app_state
        self._connect_signals()
        self._clear_view()

    def _connect_signals(self):
        current_model_changed = getattr(self.app_state, "current_model_changed", None)
        if current_model_changed is not None and hasattr(current_model_changed, "connect"):
            current_model_changed.connect(self.on_current_model_changed)
        refresh_btn = getattr(self.view, "refresh_btn", None)
        if refresh_btn is not None and hasattr(refresh_btn, "clicked"):
            refresh_btn.clicked.connect(lambda *_args: self.refresh_physics(force=True))

    def on_current_model_changed(self, _model_root):
        self.refresh_physics(force=True)

    def refresh_physics(self, force=False):
        """Clear stale rows until the replacement metadata backend exists."""
        self._clear_view()
        return bool(force)

    def load_physics(self):
        self._clear_view()

    def invalidate_physics_cache(self, *_args):
        return None

    def filter_rigid_bodies(self, _text):
        return None

    def filter_joints(self, _text):
        return None

    def _clear_view(self):
        for name in ("rigid_body_list", "joint_list"):
            widget = getattr(self.view, name, None)
            if widget is not None and hasattr(widget, "clear"):
                widget.clear()
        set_enabled = getattr(self.view, "set_physics_details_enabled", None)
        if callable(set_enabled):
            set_enabled(False)
        reset_details = getattr(self.view, "reset_details", None)
        if callable(reset_details):
            reset_details()
