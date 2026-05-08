"""Maya plugin entry point for Maya MMD Tools."""

from mmd_tools.plugin_main import (
    initializePlugin,
    maya_useNewAPI,
    uninitializePlugin,
)

__all__ = [
    "initializePlugin",
    "maya_useNewAPI",
    "uninitializePlugin",
]
