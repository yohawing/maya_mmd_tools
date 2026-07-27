"""Settings application service for presenters.

This module keeps UI presenters independent from the core settings singleton
while preserving the existing optionVar-backed storage behavior.
"""

import json
import math

from ..core import settings_keys as setting_keys
from ..core.constants import DEFAULT_IMPORT_PHYSICS, DEFAULT_SCALE_FACTOR
from ..core.settings import get_settings


_SETTINGS_EXPORT_CATEGORIES = ("import", "export", "logging", "ui")

# Dev-only import keys: forced to these values in normal mode (development_mode=False).
# In dev mode the saved setting is used instead.
_NORMAL_MODE_IMPORT_OVERRIDES = {
    "import_models": True,
    "separate_meshes_by_material": False,
    "disable_backface_culling": True,
    "uv_set_name": "map#",
    "texture_search_path": "",
    "add_semi_standard_bones": False,
    "translate_names": True,
}

_REDUCE_BAKE_TOLERANCE_ENDPOINTS = {
    "translate": (0.1, 5.0e-4),
    "rotate": (0.05, 1.0e-4),
    "morph": (0.05, 1.0e-3),
}


def normalize_reduce_bake_quality(quality):
    """Clamp and quantize Reduce Quality to the UI's 0.01 slider grid."""
    try:
        quality = float(quality)
    except (TypeError, ValueError):
        quality = 1.0
    if not math.isfinite(quality):
        quality = 1.0
    return round(max(0.0, min(1.0, quality)), 2)


def resolve_reduce_bake_tolerances_from_quality(quality):
    """Map the user-facing quality scalar to deterministic channel tolerances.

    Quality ``1`` is the conservative, highest-fidelity endpoint and quality
    ``0`` is the strongest reduction endpoint.  Log interpolation keeps the
    relative scale of each channel useful across the deliberately wide ranges.
    Non-finite or invalid values fall back to the conservative default.
    """
    quality = normalize_reduce_bake_quality(quality)

    tolerances = {}
    for channel, (max_tolerance, min_tolerance) in _REDUCE_BAKE_TOLERANCE_ENDPOINTS.items():
        if quality <= 0.0:
            tolerances[channel] = max_tolerance
        elif quality >= 1.0:
            tolerances[channel] = min_tolerance
        else:
            tolerances[channel] = max_tolerance * (min_tolerance / max_tolerance) ** quality
    return tolerances


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
        if key_path == setting_keys.IMPORT_ANIMATION_REDUCE_QUALITY:
            value = normalize_reduce_bake_quality(value)
        self._settings.set(key_path, value)

    def save(self):
        """Persist the current settings store."""
        self._settings.save()

    def reset(self):
        """Reset the current settings store to JSON defaults."""
        self._settings.reset()

    def is_development_mode(self):
        """Return whether Development Mode is enabled."""
        return self.get(setting_keys.UI_GENERAL_DEVELOPMENT_MODE, False)

    def resolve_import_scale(self):
        """Return the effective PMX/PMD import scale for the current mode.

        Development mode uses the persisted ``import.general.scale_factor``.
        Normal mode always returns ``DEFAULT_SCALE_FACTOR`` (1.0) and never
        writes over the stored development value.
        """
        if self.is_development_mode():
            return float(self.get(setting_keys.IMPORT_GENERAL_SCALE_FACTOR, DEFAULT_SCALE_FACTOR))
        return float(DEFAULT_SCALE_FACTOR)

    def resolve_reduce_bake_tolerances(self):
        """Return effective reduction tolerances from persisted quality."""
        quality = self.get(setting_keys.IMPORT_ANIMATION_REDUCE_QUALITY, 1.0)
        return resolve_reduce_bake_tolerances_from_quality(quality)

    def set_development_mode_log_levels(self, enabled):
        """Set the logging level for Development Mode and return the level."""
        level_str = "INFO" if enabled else "WARNING"
        self.set(setting_keys.LOGGING_LEVEL, level_str)
        return level_str

    def load_settings_tab_state(self):
        """Return settings needed by the Settings tab view."""
        return {
            "development_mode": self.get(setting_keys.UI_GENERAL_DEVELOPMENT_MODE, False),
            "command_port": self.get(setting_keys.UI_DEV_COMMAND_PORT, 3939),
            "logging_enabled": self.get(setting_keys.LOGGING_ENABLED, True),
            "logging_level": self.get(setting_keys.LOGGING_LEVEL, "WARNING"),
            "log_file_path": self.get(setting_keys.LOGGING_LOG_FILE_PATH, "logs/mmd_tools.log"),
            "language": self.get(setting_keys.UI_GENERAL_LANGUAGE, "ja"),
        }

    def save_settings_tab_state(self, state):
        """Persist settings supplied by the Settings tab presenter."""
        self.set(setting_keys.UI_GENERAL_DEVELOPMENT_MODE, state["development_mode"])
        if "command_port" in state:
            self.set(setting_keys.UI_DEV_COMMAND_PORT, int(state["command_port"]))
        if "language" in state:
            self.set(setting_keys.UI_GENERAL_LANGUAGE, state["language"])
        self.set(setting_keys.LOGGING_ENABLED, state["logging_enabled"])
        self.set(setting_keys.LOGGING_LEVEL, state["logging_level"])
        self.set(setting_keys.LOGGING_LOG_FILE_PATH, state["log_file_path"])
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
        bake_mode = bool(self.get(setting_keys.IMPORT_RIG_BAKE_MODE, False))
        tolerances = self.resolve_reduce_bake_tolerances()
        return {
            "start_frame": self.get(setting_keys.IMPORT_ANIMATION_START_FRAME, 1),
            "vmd_fps": self.get(setting_keys.IMPORT_ANIMATION_VMD_FPS, 30),
            "import_bone_animation": self.get(setting_keys.IMPORT_ANIMATION_IMPORT_ANIMATIONS, True),
            "import_morph_animation": self.get(setting_keys.IMPORT_ANIMATION_IMPORT_MORPH_ANIMATION, True),
            "import_camera_animation": self.get(setting_keys.IMPORT_ANIMATION_IMPORT_CAMERA_ANIMATION, True),
            "import_light_animation": self.get(setting_keys.IMPORT_ANIMATION_IMPORT_LIGHT_ANIMATION, True),
            "motion_scale": self.get(setting_keys.IMPORT_ANIMATION_MOTION_SCALE, 1.0),
            "clear_existing_motion": self.get(setting_keys.IMPORT_ANIMATION_CLEAR_EXISTING_MOTION, False),
            "create_mmd_control_rig": self.get(setting_keys.IMPORT_ANIMATION_CREATE_MMD_CONTROL_RIG, False),
            "resample_curves": self.get(setting_keys.IMPORT_ANIMATION_RESAMPLE_CURVES, False) if is_dev else False,
            "bake_mode": bake_mode,
            "use_native_physics_bake": self.get(setting_keys.IMPORT_ANIMATION_USE_NATIVE_PHYSICS_BAKE, False),
            "reduce_bake_keys": self.get(setting_keys.IMPORT_ANIMATION_REDUCE_BAKE_KEYS, False) if bake_mode else False,
            "reduce_translate_tolerance": tolerances["translate"],
            "reduce_rotate_tolerance": tolerances["rotate"],
            "reduce_morph_tolerance": tolerances["morph"],
            "target_model": target_model,
        }

    def build_pmx_import_options(self, custom_namespace=None):
        """Build PMX/PMD import options from persisted settings."""
        is_dev = self.is_development_mode()
        opts = {
            "scale": self.resolve_import_scale(),
            "use_namespace": self.get(setting_keys.IMPORT_GENERAL_USE_NAMESPACE, False),
            "custom_namespace": custom_namespace,
            "import_models": self.get(setting_keys.IMPORT_MODEL_IMPORT_MODELS, True),
            "create_mmd_shaders": self.get(setting_keys.IMPORT_MODEL_CREATE_MMD_SHADERS, True),
            "separate_meshes_by_material": self.get(setting_keys.IMPORT_MODEL_SEPARATE_MESHES_BY_MATERIAL, False),
            "auto_resolve_textures": self.get(setting_keys.IMPORT_MODEL_AUTO_RESOLVE_TEXTURES, True),
            "disable_backface_culling": self.get(setting_keys.IMPORT_MODEL_DISABLE_BACKFACE_CULLING, True),
            "uv_set_name": self.get(setting_keys.IMPORT_MODEL_UV_SET_NAME, "map#"),
            "texture_search_path": self.get(setting_keys.IMPORT_MODEL_TEXTURE_SEARCH_PATH, ""),
            "import_physics": self.get(setting_keys.IMPORT_PHYSICS_IMPORT_PHYSICS, DEFAULT_IMPORT_PHYSICS),
            "import_morphs": self.get(setting_keys.IMPORT_MORPH_IMPORT_MORPHS, True),
            "add_semi_standard_bones": self.get(setting_keys.IMPORT_RIG_ADD_SEMI_STANDARD_BONES, False),
            "translate_names": self.get(setting_keys.IMPORT_NAMING_TRANSLATE_NAMES, True),
        }
        if not is_dev:
            opts.update(_NORMAL_MODE_IMPORT_OVERRIDES)
        opts["use_cpp_fast_load"] = self.get(setting_keys.IMPORT_NATIVE_USE_CPP_FAST_LOAD, False)
        opts["cpp_fast_load_mesh_only"] = self.get(setting_keys.IMPORT_NATIVE_CPP_FAST_LOAD_MESH_ONLY, True)
        opts["use_cpp_rig_nodes"] = self.get(setting_keys.IMPORT_NATIVE_USE_CPP_RIG_NODES, False)
        return opts

    def should_show_texture_issue_dialog(self):
        """Return whether post-import texture issue diagnostics should be shown."""
        return self.get(setting_keys.IMPORT_MODEL_SHOW_TEXTURE_ISSUE_DIALOG, True)

    def build_export_options(self, file_path):
        """Build PMX/PMD export options from persisted settings."""
        return {
            "file_path": file_path,
            "export_format": self.get(setting_keys.EXPORT_GENERAL_EXPORT_FORMAT, "pmx"),
            "apply_scale": self.get(setting_keys.EXPORT_GENERAL_APPLY_SCALE, True),
        }
