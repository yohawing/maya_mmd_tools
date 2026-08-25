"""Import/export tab state storage backed by QSettings."""

import json
import os

from .qt_compat import QSettings


FILE_HISTORY_KEY = "file_history"
FILE_HISTORY_MAX = 100
LEGACY_HISTORY_TYPES = {
    "import_path_history": "import",
    "vmd_path_history": "vmd",
    "export_path_history": "export",
}


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

    def load_file_history(self, max_items=20):
        """Return one newest-first history shared by model, VMD, and export files."""

        encoded = self.get(FILE_HISTORY_KEY, None)
        if encoded is None:
            history = self._migrate_legacy_history()
        else:
            try:
                history = json.loads(encoded)
            except Exception:
                history = []
        valid = []
        for item in history if isinstance(history, list) else ():
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            file_type = item.get("type")
            if (
                isinstance(path, str)
                and file_type in LEGACY_HISTORY_TYPES.values()
                and os.path.exists(path)
            ):
                valid.append({"path": path, "type": file_type})
        return valid[: max(1, min(FILE_HISTORY_MAX, int(max_items)))]

    def save_file_history(self, file_type, new_path):
        """Move one typed path to the front of the unified history."""

        if file_type not in LEGACY_HISTORY_TYPES.values():
            raise ValueError(f"unsupported file history type: {file_type}")
        if not new_path or not os.path.exists(new_path):
            return
        history = self.load_file_history(FILE_HISTORY_MAX)
        history = [
            item
            for item in history
            if not (item["type"] == file_type and item["path"] == new_path)
        ]
        history.insert(0, {"path": new_path, "type": file_type})
        self.set(FILE_HISTORY_KEY, json.dumps(history[:FILE_HISTORY_MAX]))

    def clear_file_history(self, file_types=None):
        """Clear selected history categories while preserving hidden legacy data."""

        selected = set(LEGACY_HISTORY_TYPES.values()) if file_types is None else set(file_types)
        unknown = selected.difference(LEGACY_HISTORY_TYPES.values())
        if unknown:
            raise ValueError(f"unsupported file history types: {sorted(unknown)}")
        remaining = [
            item for item in self.load_file_history(FILE_HISTORY_MAX) if item["type"] not in selected
        ]
        self.set(FILE_HISTORY_KEY, json.dumps(remaining))
        self.clear_histories(
            key for key, file_type in LEGACY_HISTORY_TYPES.items() if file_type in selected
        )

    def _migrate_legacy_history(self):
        """Migrate legacy lists deterministically when cross-type time is unavailable."""

        history = []
        for key, file_type in LEGACY_HISTORY_TYPES.items():
            for path in self.load_history(key, FILE_HISTORY_MAX):
                migrated_type = file_type
                if key == "import_path_history" and path.lower().endswith(".vmd"):
                    migrated_type = "vmd"
                history.append({"path": path, "type": migrated_type})
        self.set(FILE_HISTORY_KEY, json.dumps(history[:FILE_HISTORY_MAX]))
        return history

    def clear_histories(self, keys):
        """Clear multiple JSON history settings."""
        for key in keys:
            self.set(key, "[]")
