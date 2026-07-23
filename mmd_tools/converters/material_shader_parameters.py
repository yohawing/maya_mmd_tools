"""Shared base-value mapping for the MMD DX11 and GLSL shaders.

MMD custom attributes own authored/export values. Hardware uniforms are final
display plugs, so callers must not overwrite them while an evaluator drives them.
"""

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple


ATTR_MMD_EDGE_ALPHA = "mmd_edge_alpha"
ATTR_MMD_DIFFUSE_ALPHA = "mmd_diffuse_alpha"


@dataclass(frozen=True)
class ShaderParameter:
    attribute: str
    attribute_type: str


@dataclass(frozen=True)
class HardwareMorphRoute:
    """One hardware uniform route owned by ``mmdMaterialMorphEval``."""

    semantic: str
    uniform: str
    evaluator_base: Optional[str]
    evaluator_output: str
    size: int
    attribute_type: str


# MMDShader.fx and MMDShader.ogsfx intentionally share this contract.
_COMMON_SHADER_PARAMETERS: Mapping[str, ShaderParameter] = {
    "sphere_mode": ShaderParameter("SphereMode", "long"),
    "opacity": ShaderParameter("Opacity", "float"),
}

_COMMON_HARDWARE_MORPH_ROUTES = (
    HardwareMorphRoute(
        "diffuse_rgb", "DiffuseColorRGB", "baseDiffuse", "outputDiffuse", 3, "double3"
    ),
    HardwareMorphRoute(
        "diffuse_alpha", "DiffuseColorA", "baseDiffuseA", "outputDiffuseAlpha", 1, "float"
    ),
    HardwareMorphRoute(
        "specular", "SpecularColor", "baseSpecular", "outputSpecular", 3, "double3"
    ),
    HardwareMorphRoute(
        "specular_power",
        "Shininess",
        "baseSpecularCoefficient",
        "outputSpecularCoefficient",
        1,
        "float",
    ),
    HardwareMorphRoute(
        "ambient", "AmbientColor", "baseAmbient", "outputAmbient", 3, "double3"
    ),
    HardwareMorphRoute(
        "edge_size", "EdgeSize", "baseEdgeSize", "outputEdgeSize", 1, "float"
    ),
    HardwareMorphRoute(
        "texture_multiply", "MainTextureMultiply", None, "outputTextureMultiply", 4, "double4"
    ),
    HardwareMorphRoute(
        "texture_add", "MainTextureAdd", None, "outputTextureAdd", 4, "double4"
    ),
    HardwareMorphRoute(
        "sphere_texture_multiply",
        "SphereTextureMultiply",
        None,
        "outputSphereTextureMultiply",
        4,
        "double4",
    ),
    HardwareMorphRoute(
        "sphere_texture_add", "SphereTextureAdd", None, "outputSphereTextureAdd", 4, "double4"
    ),
    HardwareMorphRoute(
        "toon_texture_multiply",
        "ToonTextureMultiply",
        None,
        "outputToonTextureMultiply",
        4,
        "double4",
    ),
    HardwareMorphRoute(
        "toon_texture_add", "ToonTextureAdd", None, "outputToonTextureAdd", 4, "double4"
    ),
)

_BACKEND_HARDWARE_MORPH_ROUTES = {
    "dx11Shader": (
        HardwareMorphRoute(
            "edge_color", "EdgeColorRGB", "baseEdgeColor", "outputEdgeColor", 3, "double3"
        ),
        HardwareMorphRoute(
            "edge_color", "EdgeColorA", "baseEdgeColorA", "outputEdgeColorA", 1, "float"
        ),
    ),
    "GLSLShader": (
        HardwareMorphRoute(
            "edge_color", "EdgeColor", "baseEdgeColor", "outputEdgeColor", 4, "double4"
        ),
    ),
}


def hardware_morph_routes(shader_type: str) -> Tuple[HardwareMorphRoute, ...]:
    """Return the complete evaluator route contract for a hardware shader type."""
    return _COMMON_HARDWARE_MORPH_ROUTES + _BACKEND_HARDWARE_MORPH_ROUTES.get(
        shader_type, ()
    )


def hardware_morph_route_for_uniform(
    uniform: str, shader_type: str
) -> Optional[HardwareMorphRoute]:
    """Return the evaluator route owning *uniform*, if it is morph-driven."""
    return next(
        (route for route in hardware_morph_routes(shader_type) if route.uniform == uniform),
        None,
    )


def material_base_parameter_values(material: Any) -> dict[str, Any]:
    """Convert a parsed PMD/PMX material to the shared uniform semantics."""
    diffuse = list(getattr(material, "diffuse", (0.8, 0.8, 0.8, 1.0)))
    alpha = float(diffuse[3]) if len(diffuse) > 3 else 1.0
    values: dict[str, Any] = {
        "diffuse_rgb": diffuse[:3],
        "diffuse_alpha": alpha,
        # The shader multiplies texture alpha, DiffuseColorA (the PMX
        # material/morph alpha), and Opacity.  Opacity is a neutral runtime
        # multiplier for ordinary materials; keeping PMX alpha only in
        # DiffuseColorA avoids applying it twice.
        "opacity": 1.0,
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
        routes = [
            route for route in hardware_morph_routes(shader_type)
            if route.semantic == semantic
        ]
        if routes:
            components = (
                list(value) if semantic == "edge_color" and len(routes) > 1 else None
            )
            for route in routes:
                route_value = value
                if components is not None:
                    route_value = (
                        components[:3]
                        if route.size == 3
                        else (
                            float(components[3])
                            if route.size == 1 and len(components) > 3
                            else 1.0
                        )
                        if route.size == 1
                        else components
                    )
                yield ShaderParameter(route.uniform, route.attribute_type), route_value
            continue
        binding = _COMMON_SHADER_PARAMETERS.get(semantic)
        if binding is not None:
            yield binding, value
