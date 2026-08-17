"""Resolve Maya material value and texture plugs for one shader backend."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MayaMaterialTextureSlotRoute:
    """One backend-specific texture slot contract."""

    semantic: str
    texture_attribute: str
    presence_attribute: str | None = None
    file_node_suffix: str = "File"


@dataclass(frozen=True)
class MayaMaterialShaderRoute:
    """One backend-specific material value/texture contract.

    The diffuse fields describe the viewport value plug used by the
    value-patch transaction.  The texture fields share the same backend
    decision so binding patches cannot accidentally target ``baseColor`` on
    MMD hardware shaders.
    """

    diffuse_attribute: str
    diffuse_attribute_type: str = "float3"
    texture_slots: tuple[MayaMaterialTextureSlotRoute, ...] = ()
    diffuse_alpha_attribute: str | None = None
    diffuse_alpha_attribute_type: str = "double"

    def texture_slot(self, semantic: str) -> MayaMaterialTextureSlotRoute | None:
        """Return the named texture slot when this backend supports it."""
        return next((slot for slot in self.texture_slots if slot.semantic == semantic), None)


_STOCK_MAIN_TEXTURE = MayaMaterialTextureSlotRoute("main", "baseColor")
_LEGACY_MAIN_TEXTURE = MayaMaterialTextureSlotRoute("main", "color")
_HARDWARE_TEXTURE_SLOTS = (
    MayaMaterialTextureSlotRoute("main", "MainTexture", "HasMainTexture"),
    MayaMaterialTextureSlotRoute(
        "sphere", "SphereTexture", "HasSphereTexture", "SphereFile"
    ),
    MayaMaterialTextureSlotRoute("toon", "ToonTexture", "HasToonTexture", "ToonFile"),
)


def material_shader_route(shader_type: str) -> MayaMaterialShaderRoute | None:
    """Return the complete material plug contract for ``shader_type``."""
    if shader_type in {"dx11Shader", "GLSLShader"}:
        return MayaMaterialShaderRoute(
            "DiffuseColorRGB",
            texture_slots=_HARDWARE_TEXTURE_SLOTS,
            diffuse_alpha_attribute="DiffuseColorA",
        )
    if shader_type == "standardSurface":
        return MayaMaterialShaderRoute("baseColor", texture_slots=(_STOCK_MAIN_TEXTURE,))
    if shader_type in {"lambert", "blinn", "phong"}:
        return MayaMaterialShaderRoute("color", texture_slots=(_LEGACY_MAIN_TEXTURE,))
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
