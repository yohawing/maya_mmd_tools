"""Immutable read projections for model-owned vertex morph bindings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from mmd_tools.core.morph_binding_resolver import MorphBinding, MorphBindingWarning


@dataclass(frozen=True)
class MorphProjectionRequest:
    """Semantic identity needed to project one registered vertex morph."""

    raw_pmx_name: str
    global_morph_index: int
    binding_identity: str


@dataclass(frozen=True)
class MorphBindingProjection:
    """Canonical, model-owned blendShape bindings for one PMX morph."""

    raw_pmx_name: str
    global_morph_index: int
    binding_identity: str
    bindings: Tuple[MorphBinding, ...]
    warnings: Tuple[MorphBindingWarning, ...]

    @property
    def preview_plugs(self) -> Tuple[str, ...]:
        """Return fixed canonical writer targets without scene rediscovery."""

        return tuple(binding.weight_plug for binding in self.bindings)


@dataclass(frozen=True)
class MorphBlendShapeReadProjection:
    """One immutable blendShape scan for an explicit model root."""

    root_identity: str
    controller_identity: str
    owned_mesh_identities: Tuple[str, ...]
    owned_blend_shape_identities: Tuple[str, ...]
    morphs: Tuple[MorphBindingProjection, ...]

    def binding_for_index(self, global_morph_index: int) -> MorphBindingProjection:
        """Return one unambiguous binding projection by global PMX index."""

        matches = tuple(
            morph for morph in self.morphs if morph.global_morph_index == global_morph_index
        )
        if len(matches) != 1:
            raise KeyError("global morph index {!r} is not unique".format(global_morph_index))
        return matches[0]


__all__ = [
    "MorphBindingProjection",
    "MorphBlendShapeReadProjection",
    "MorphProjectionRequest",
]
