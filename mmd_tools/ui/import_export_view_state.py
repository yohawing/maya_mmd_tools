"""Import/export tab state storage backed by QSettings."""

import json
import os

from .qt_compat import QSettings


class ImportExportViewState:
    """Persist ImportExportTab view-only state."""

    def __init__(self, settings_store=None):
        self._settings = settings_store or QSettings("maya_mmd_tools", "ImportExportTab")

    def get(self, key, default=None):
        """Read a raw view setting."""
        return self._settings.value(key, default)

    def set(self, key, value):
        """Write a raw view setting."""
        self._settings.setValue(key, value)

    def load_history(self, key, max_items=10):
        """Return existing file paths from a JSON history setting."""
        history_json = self.get(key, "[]")
        try:
            history = json.loads(history_json)

            valid_history = []
            for path in history:
                if isinstance(path, str) and os.path.exists(path):
                    valid_history.append(path)
            return valid_history[:max_items]
        except Exception:
            return []

    def save_history(self, key, new_path, max_items=10):
        """Store a file path at the front of a JSON history setting."""
        if not new_path or not os.path.exists(new_path):
            return

        history = self.load_history(key, max_items)
        if new_path in history:
            history.remove(new_path)
        history.insert(0, new_path)
        self.set(key, json.dumps(history[:max_items]))

    def clear_histories(self, keys):
        """Clear multiple JSON history settings."""
        for key in keys:
            self.set(key, "[]")
