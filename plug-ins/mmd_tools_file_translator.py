"""Maya API 1.0 plug-in wrapper for the MMD File > Import translator."""

from mmd_tools.io.mmd_file_translator import (
    deregister_file_translator,
    register_file_translator,
)


def initializePlugin(mobject):
    """Register the MMD file translator."""
    register_file_translator(mobject)


def uninitializePlugin(mobject):
    """Deregister the MMD file translator."""
    deregister_file_translator(mobject)
