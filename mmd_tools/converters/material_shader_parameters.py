"""Shared base-value mapping for the MMD DX11 and GLSL shaders.

MMD custom attributes own authored/export values. Hardware uniforms are final
display plugs, so callers must not overwrite them while an evaluator drives them.
"""

from dataclasses import dataclass
from typing import Any, Mapping


ATTR_MMD_EDGE_ALPHA = "mmd_edge_alpha"
ATTR_MMD_DIFFUSE_ALPHA = "mmd_diffuse_alpha"


@dataclass(frozen=True)
class ShaderParameter:
    attribute: str
    attribute_type: str


# MMDShader.fx and MMDShader.ogsfx intentionally share this contract.
_COMMON_SHADER_PARAMETERS: Mapping[str, ShaderParameter] = {
    "diffuse_rgb": ShaderParameter("DiffuseColorRGB", "double3"),
    "diffuse_alpha": ShaderParameter("DiffuseColorA", "float"),
    "ambient": ShaderParameter("AmbientColor", "double3"),
    "specular": ShaderParameter("SpecularColor", "double3"),
    "specular_power": ShaderParameter("Shininess", "float"),
    "edge_size": ShaderParameter("EdgeSize", "float"),
    "sphere_mode": ShaderParameter("SphereMode", "long"),
    "opacity": ShaderParameter("Opacity", "float"),
    "texture_multiply": ShaderParameter("MainTextureMultiply", "double4"),
    "texture_add": ShaderParameter("MainTextureAdd", "double4"),
    "sphere_texture_multiply": ShaderParameter("SphereTextureMultiply", "double4"),
    "sphere_texture_add": ShaderParameter("SphereTextureAdd", "double4"),
    "toon_texture_multiply": ShaderParameter("ToonTextureMultiply", "double4"),
    "toon_texture_add": ShaderParameter("ToonTextureAdd", "double4"),
}

_BACKEND_SHADER_PARAMETERS: Mapping[str, Mapping[str, ShaderParameter]] = {
    "dx11Shader": {
        "edge_rgb": ShaderParameter("EdgeColorRGB", "double3"),
        "edge_alpha": ShaderParameter("EdgeColorA", "float"),
    },
    "GLSLShader": {"edge_color": ShaderParameter("EdgeColor", "double4")},
}


def material_base_parameter_values(material: Any) -> dict[str, Any]:
    """Convert a parsed PMD/PMX material to the shared uniform semantics."""
    diffuse = list(getattr(material, "diffuse", (0.8, 0.8, 0.8, 1.0)))
    alpha = float(diffuse[3]) if len(diffuse) > 3 else 1.0
    values: dict[str, Any] = {
        "diffuse_rgb": diffuse[:3],
        "diffuse_alpha": alpha,
        "opacity": alpha,
        "edge_size": 0.0,
        "sphere_mode": int(getattr(material, "sphere_mode", 0)),
    }
    for semantic in ("ambient", "specular"):
        if hasattr(material, semantic):
            values[semantic] = list(getattr(material, semantic))[:3]
    if hasattr(material, "specular_coefficient"):
        values["specular_power"] = material.specular_coefficient
    elif hasattr(material, "specular_power"):
        values["specular_power"] = material.specular_power
    edge = list(getattr(material, "edge_color", (0.0, 0.0, 0.0, 1.0)))
    if len(edge) == 3:
        edge.append(1.0)
    values["edge_color"] = edge
    return values


def iter_hardware_shader_values(values: Mapping[str, Any], shader_type: str):
    """Yield backend-correct bindings for recognized semantic values."""
    for semantic, value in values.items():
        if semantic == "edge_color" and shader_type == "dx11Shader":
            edge = list(value)
            yield _BACKEND_SHADER_PARAMETERS[shader_type]["edge_rgb"], edge[:3]
            yield _BACKEND_SHADER_PARAMETERS[shader_type]["edge_alpha"], (
                float(edge[3]) if len(edge) > 3 else 1.0
            )
            continue
        binding = _BACKEND_SHADER_PARAMETERS.get(shader_type, {}).get(
            semantic, _COMMON_SHADER_PARAMETERS.get(semantic)
        )
        if binding is not None:
            yield binding, value
