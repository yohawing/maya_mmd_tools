"""Material shader action helpers for presenter workflows."""

from typing import Optional

from mmd_tools.converters import mesh_converter


TRANSPARENCY_MODES = (
    mesh_converter.TRANSPARENCY_MODE_OPAQUE,
    mesh_converter.TRANSPARENCY_MODE_CUTOUT,
    mesh_converter.TRANSPARENCY_MODE_BLEND,
)


def transparency_mode_index(material: str) -> int:
    """Return the UI combo index for a material's DX11 transparency mode."""
    mode = mesh_converter.get_transparency_mode(material)
    try:
        return TRANSPARENCY_MODES.index(mode)
    except ValueError:
        return 0


def shader_outline_enabled(material: str) -> bool:
    """Return whether generated DX11 shader outline is enabled."""
    return mesh_converter.get_shader_outline_enabled(material)


def transparency_mode_from_index(index: int) -> Optional[str]:
    """Return a converter transparency mode for a UI combo index."""
    if 0 <= index < len(TRANSPARENCY_MODES):
        return TRANSPARENCY_MODES[index]
    return None


def apply_shader_settings(
    material: str,
    *,
    transparency_mode_index_value: int,
    outline_enabled: bool,
    edge_size: float,
) -> bool:
    """Apply DX11 transparency and outline settings to one material."""
    mode = transparency_mode_from_index(transparency_mode_index_value)
    if mode is None:
        return False
    mesh_converter.apply_transparency_mode(material, mode)
    mesh_converter.apply_shader_outline(material, outline_enabled, edge_size)
    return True
