"""
mmd_tools settings compatibility module.

This module re-exports the settings API from ``mmd_tools.core.settings`` so
``import mmd_tools.settings`` works without mutating ``sys.modules`` at package
initialization time.
"""

from .core.settings import Settings, SettingsProxy, get_settings, settings

__all__ = [
    "Settings",
    "SettingsProxy",
    "get_settings",
    "settings",
]
