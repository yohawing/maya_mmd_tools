"""Settings application service for presenters.

This module keeps UI presenters independent from the core settings singleton
while preserving the existing optionVar-backed storage behavior.
"""

import json

from ..core.settings import get_settings


_SETTINGS_EXPORT_CATEGORIES = ("import", "export", "logging", "ui")

# Dev-only import keys: forced to these values in normal mode (development_mode=False).
# In dev mode the saved setting is used instead.
_NORMAL_MODE_IMPORT_OVERRIDES = {
    "import_models": True,
    "import_physics": False,
    "separate_meshes_by_material": False,
    "split_meshes_by_morph_groups": False,
    "hide_hidden_geometry": False,
    "auto_classify_transparency": False,
    "disable_backface_culling": True,
    "uv_set_name": "map#",
    "texture_search_path": "",
    "add_semi_standard_bones": False,
    "translate_names": True,
}


class SettingsService:
    """Presenter-facing API for plugin settings."""

    def __init__(self, settings_store=None):
        self._settings = settings_store if settings_store is not None else get_settings()

    @property
    def data(self):
        """Return the underlying nested settings dictionary."""
        return self._settings.data

    def get(self, key_path, default=None):
        """Read a setting by dot-separated key path."""
        return self._settings.get(key_path, default)

    def set(self, key_path, value):
        """Write a setting by dot-separated key path."""
        self._settings.set(key_path, value)

    def save(self):
        """Persist the current settings store."""
        self._settings.save()

    def reset(self):
        """Reset the current settings store to JSON defaults."""
        self._settings.reset()

    def is_development_mode(self):
        """Return whether Development Mode is enabled."""
        return self.get("ui.general.development_mode", False)

    def set_development_mode_log_levels(self, enabled):
        """Set the logging level for Development Mode and return the level."""
        level_str = "INFO" if enabled else "WARNING"
        self.set("logging.level", level_str)
        return level_str

    def load_settings_tab_state(self):
        """Return settings needed by the Settings tab view."""
        return {
            "development_mode": self.get("ui.general.development_mode", False),
            "logging_enabled": self.get("logging.enabled", True),
            "logging_level": self.get("logging.level", "WARNING"),
            "log_file_path": self.get("logging.log_file_path", "logs/mmd_tools.log"),
            "language": self.get("ui.general.language", "ja"),
        }

    def save_settings_tab_state(self, state):
        """Persist settings supplied by the Settings tab presenter."""
        self.set("ui.general.development_mode", state["development_mode"])
        if "language" in state:
            self.set("ui.general.language", state["language"])
        self.set("logging.enabled", state["logging_enabled"])
        self.set("logging.level", state["logging_level"])
        self.set("logging.log_file_path", state["log_file_path"])
        self.save()

    def export_settings_data(self):
        """Return the JSON-exportable settings categories."""
        all_settings = self.data
        return {category: all_settings.get(category, {}) for category in _SETTINGS_EXPORT_CATEGORIES}

    def write_settings_json(self, file_path):
        """Write exportable settings to a JSON file."""
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(self.export_settings_data(), f, ensure_ascii=False, indent=2)

    def read_settings_json(self, file_path):
        """Read settings JSON from a file."""
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def import_settings_data(self, data):
        """Import settings data for supported top-level categories."""
        for category in _SETTINGS_EXPORT_CATEGORIES:
            if category in data:
                for key, value in data[category].items():
                    self.set(f"{category}.{key}", value)

    def import_settings_json(self, file_path):
        """Read and import settings from a JSON file."""
        self.import_settings_data(self.read_settings_json(file_path))

    def build_vmd_import_options(self, target_model=None):
        """Build VMD import options from persisted settings."""
        is_dev = self.is_development_mode()
        return {
            "start_frame": self.get("import.animation.animation_start_frame", 1),
            "vmd_fps": self.get("import.animation.vmd_fps", 30),
            "import_bone_animation": self.get("import.animation.import_animations", True),
            "import_morph_animation": self.get("import.animation.import_morph_animation", True),
            "import_camera_animation": self.get("import.animation.import_camera_animation", True),
            "import_light_animation": self.get("import.animation.import_light_animation", True),
            "resample_curves": self.get("import.animation.resample_curves", False) if is_dev else False,
            "bake_mode": self.get("import.rig.bake_mode", False),
            "target_model": target_model,
        }

    def build_pmx_import_options(self, custom_namespace=None):
        """Build PMX/PMD import options from persisted settings."""
        is_dev = self.is_development_mode()
        opts = {
            "scale": self.get("import.general.scale_factor", 1.0),
            "use_namespace": self.get("import.general.use_namespace", False),
            "custom_namespace": custom_namespace,
            "import_models": self.get("import.model.import_models", True),
            "create_mmd_shaders": self.get("import.model.create_mmd_shaders", True),
            "separate_meshes_by_material": self.get("import.model.separate_meshes_by_material", False),
            "split_meshes_by_morph_groups": self.get("import.model.split_meshes_by_morph_groups", False),
            "hide_hidden_geometry": self.get("import.model.hide_hidden_geometry", False),
            "auto_classify_transparency": self.get("import.model.auto_classify_transparency", False),
            "auto_resolve_textures": self.get("import.model.auto_resolve_textures", True),
            "disable_backface_culling": self.get("import.model.disable_backface_culling", True),
            "uv_set_name": self.get("import.model.uv_set_name", "map#"),
            "texture_search_path": self.get("import.model.texture_search_path", ""),
            "import_physics": self.get("import.physics.import_physics", False),
            "import_morphs": self.get("import.morph.import_morphs", True),
            "add_semi_standard_bones": self.get("import.rig.add_semi_standard_bones", False),
            "translate_names": self.get("import.naming.translate_names", True),
        }
        if not is_dev:
            opts.update(_NORMAL_MODE_IMPORT_OVERRIDES)
        opts["use_cpp_fast_load"] = self.get("import.native.use_cpp_fast_load", False)
        opts["cpp_fast_load_mesh_only"] = self.get("import.native.cpp_fast_load_mesh_only", True)
        return opts

    def should_show_texture_issue_dialog(self):
        """Return whether post-import texture issue diagnostics should be shown."""
        return self.get("import.model.show_texture_issue_dialog", True)

    def build_export_options(self, file_path):
        """Build PMX/PMD export options from persisted settings."""
        return {
            "file_path": file_path,
            "export_format": self.get("export.general.export_format", "pmx"),
            "apply_scale": self.get("export.general.apply_scale", True),
        }
