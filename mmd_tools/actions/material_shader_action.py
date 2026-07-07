"""Material shader action helpers for presenter workflows."""

import os
from typing import Optional

from mmd_tools.core import maya_utils
from mmd_tools.core.logger import get_logger
from mmd_tools.core.pmx_data.material import PmxSphereMode
from mmd_tools.converters import mesh_converter

logger = get_logger(__name__)

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


def apply_sphere_map(
    material: str,
    sphere_path: str,
    sphere_mode: int,
    *,
    maya_adapter=None,
    path_exists=None,
    get_attribute_func=None,
    set_attribute_func=None,
) -> bool:
    """Apply an MMD sphere map approximation to a Maya material.

    Returns False for disabled, missing, or unsupported sphere-map inputs.
    Unexpected Maya operation errors propagate so the UI boundary can report
    them with the appropriate presenter status message.
    """
    if not material or not sphere_path or sphere_mode <= 0:
        return False

    exists = path_exists or os.path.exists
    if not exists(sphere_path):
        logger.warning("Sphere map file not found: %s", sphere_path)
        return False

    if maya_adapter is None:
        from mmd_tools.adapters.maya_cmds_adapter import MayaCmdsAdapter

        maya_adapter = MayaCmdsAdapter()

    get_attribute = get_attribute_func or maya_utils.get_attribute
    set_attribute = set_attribute_func or maya_utils.set_attribute

    sphere_file_node = None
    for node in maya_adapter.ls(type="file") or []:
        if get_attribute(node, "fileTextureName") == sphere_path:
            sphere_file_node = node
            break

    if not sphere_file_node:
        sphere_file_node = maya_adapter.shading_node("file", asTexture=True, name=f"{material}_sphere")
        set_attribute(sphere_file_node, "fileTextureName", sphere_path, "str")

    if sphere_mode == PmxSphereMode.MULTIPLY:
        layered_texture = maya_adapter.shading_node("layeredTexture", asTexture=True, name=f"{material}_layered")
        base_file = maya_adapter.list_connections(f"{material}.baseColor", type="file")
        if base_file:
            maya_adapter.connect_attr(f"{base_file[0]}.outColor", f"{layered_texture}.inputs[0].color")
            set_attribute(layered_texture, "inputs[0].blendMode", 0, "int")
        maya_adapter.connect_attr(f"{sphere_file_node}.outColor", f"{layered_texture}.inputs[1].color")
        set_attribute(layered_texture, "inputs[1].blendMode", 6, "int")
        maya_adapter.connect_attr(f"{layered_texture}.outColor", f"{material}.baseColor", force=True)
    elif sphere_mode == PmxSphereMode.ADDITIVE:
        maya_adapter.connect_attr(
            f"{sphere_file_node}.outColor",
            f"{material}.emissionColor",
            force=True,
        )
        set_attribute(material, "emission", 0.5, "float")
    elif sphere_mode == PmxSphereMode.SUB_TEXTURE:
        maya_adapter.connect_attr(
            f"{sphere_file_node}.outColor",
            f"{material}.specularColor",
            force=True,
        )
    else:
        return False

    logger.info("Applied sphere map to material '%s' with mode %s", material, sphere_mode)
    return True
