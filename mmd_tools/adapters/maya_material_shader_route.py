"""Resolve Maya material value and texture plugs for one shader backend."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MayaMaterialShaderRoute:
    """One backend-specific material value/texture plug contract.

    The diffuse fields describe the viewport value plug used by the
    value-patch transaction.  The texture fields share the same backend
    decision so binding patches cannot accidentally target ``baseColor`` on
    MMD hardware shaders.
    """

    diffuse_attribute: str
    diffuse_attribute_type: str = "float3"
    main_texture_attribute: str = "baseColor"
    main_texture_presence_attribute: str | None = None
    main_texture_presence_type: str = "long"


def material_shader_route(shader_type: str) -> MayaMaterialShaderRoute | None:
    """Return the complete material plug contract for ``shader_type``."""
    if shader_type in {"dx11Shader", "GLSLShader"}:
        return MayaMaterialShaderRoute(
            "DiffuseColorRGB",
            main_texture_attribute="MainTexture",
            main_texture_presence_attribute="HasMainTexture",
        )
    if shader_type == "standardSurface":
        return MayaMaterialShaderRoute("baseColor")
    if shader_type in {"lambert", "blinn", "phong"}:
        return MayaMaterialShaderRoute("color")
    return None


def material_diffuse_route(
    shader_type: str,
    *,
    has_main_texture: bool,
) -> MayaMaterialShaderRoute | None:
    """Return the writable viewport diffuse plug, if this edit owns one.

    Canonical PMX data always lives in ``diffuse_color``.  Stock Maya shaders
    let a texture connection own their final color plug, while the MMD
    hardware shaders keep ``DiffuseColorRGB`` as an independent multiplier.
    """
    if shader_type not in {"dx11Shader", "GLSLShader", "standardSurface", "lambert", "blinn", "phong"}:
        return None
    route = material_shader_route(shader_type)
    if route is None:
        return None
    if shader_type in {"standardSurface", "lambert", "blinn", "phong"}:
        return None if has_main_texture else route
    return route
