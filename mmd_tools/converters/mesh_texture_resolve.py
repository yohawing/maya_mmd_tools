"""Texture path resolution helpers for PMX/PMD mesh import."""

from __future__ import annotations

import os

STANDARD_TOON_TEXTURE_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "shaders", "toon_textures")
)


def resolve_texture_path(texture_dir, texture_path):
    """Resolve a texture path once, preserving already-absolute paths."""
    if not texture_path:
        return None
    if os.path.isabs(texture_path):
        return os.path.normpath(texture_path)
    return os.path.normpath(os.path.join(texture_dir, texture_path))


def resolve_pmx_toon_texture_path(texture_dir, material, all_textures):
    """Resolve a PMX custom/shared toon texture to an absolute file path."""
    if not hasattr(material, "shared_toon_flag") or not hasattr(material, "toon_texture_index"):
        return None

    toon_index = int(material.toon_texture_index)
    if toon_index < 0:
        return None

    # PMX shared_toon_flag: 0 = regular texture table, 1 = shared toon01..toon10.
    if int(material.shared_toon_flag) == 0:
        if not all_textures or toon_index >= len(all_textures):
            return None
        return resolve_texture_path(texture_dir, all_textures[toon_index])

    if toon_index > 9:
        return None
    return os.path.join(STANDARD_TOON_TEXTURE_DIR, f"toon{toon_index + 1:02d}.bmp")
