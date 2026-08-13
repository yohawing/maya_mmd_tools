"""Resolve the Maya viewport diffuse plug for one material shader backend."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MayaMaterialDiffuseRoute:
    """One backend-specific viewport diffuse plug contract."""

    attribute: str
    attribute_type: str = "float3"


def material_diffuse_route(
    shader_type: str,
    *,
    has_main_texture: bool,
) -> MayaMaterialDiffuseRoute | None:
    """Return the writable viewport diffuse plug, if this edit owns one.

    Canonical PMX data always lives in ``diffuse_color``.  Stock Maya shaders
    let a texture connection own their final color plug, while the MMD
    hardware shaders keep ``DiffuseColorRGB`` as an independent multiplier.
    """
    if shader_type in {"dx11Shader", "GLSLShader"}:
        return MayaMaterialDiffuseRoute("DiffuseColorRGB")
    if shader_type == "standardSurface":
        return None if has_main_texture else MayaMaterialDiffuseRoute("baseColor")
    if shader_type in {"lambert", "blinn", "phong"}:
        return None if has_main_texture else MayaMaterialDiffuseRoute("color")
    return None
