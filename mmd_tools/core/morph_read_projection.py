"""Immutable read projections for model-owned vertex morph bindings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Tuple

from mmd_tools.core.morph_binding_resolver import MorphBinding, MorphBindingWarning


@dataclass(frozen=True)
class MorphProjectionRequest:
    """Semantic identity needed to project one registered vertex morph."""

    raw_pmx_name: str
    global_morph_index: int
    binding_identity: str
    morph_type: str = "vertex"


@dataclass(frozen=True)
class MorphBindingProjection:
    """Canonical, model-owned blendShape bindings for one PMX morph."""

    raw_pmx_name: str
    global_morph_index: int
    binding_identity: str
    bindings: Tuple[MorphBinding, ...]
    warnings: Tuple[MorphBindingWarning, ...]
    runtime_preview_plugs: Tuple[str, ...] = ()
    runtime_supported: bool = False
    unsupported_reason: str = ""

    @property
    def preview_plugs(self) -> Tuple[str, ...]:
        """Return fixed canonical writer targets without scene rediscovery."""

        return tuple(binding.weight_plug for binding in self.bindings)

    @property
    def runtime_targets(self) -> Tuple[str, ...]:
        """Return controller-first runtime preview targets for UI actions."""

        return self.runtime_preview_plugs or self.preview_plugs


@dataclass(frozen=True)
class MorphBlendShapeReadProjection:
    """One immutable blendShape scan for an explicit model root."""

    root_identity: str
    controller_identity: str
    owned_mesh_identities: Tuple[str, ...]
    owned_blend_shape_identities: Tuple[str, ...]
    morphs: Tuple[MorphBindingProjection, ...]
    owned_non_intermediate_mesh_identities: Tuple[str, ...] = ()

    def binding_for_index(self, global_morph_index: int) -> MorphBindingProjection:
        """Return one unambiguous binding projection by global PMX index."""

        matches = tuple(
            morph for morph in self.morphs if morph.global_morph_index == global_morph_index
        )
        if len(matches) != 1:
            raise KeyError("global morph index {!r} is not unique".format(global_morph_index))
        return matches[0]


_DIRECT_RUNTIME_SUPPORT = {
    "vertex": True,
    "bone": True,
    "uv": False,
    "additional_uv1": False,
    "additional_uv2": False,
    "additional_uv3": False,
    "additional_uv4": False,
    "material": True,
    "flip": False,
    "impulse": False,
}


def project_runtime_capabilities(
    requests: Tuple[MorphProjectionRequest, ...],
    controller_topology: Mapping[int, Tuple[Tuple[int, float], ...]],
    connected_output_indices: Tuple[int, ...],
) -> Tuple[bool, ...]:
    """Evaluate runtime support from already collected, Maya-free observations."""

    connected = frozenset(connected_output_indices)
    supported = []
    for request in requests:
        if request.morph_type == "group":
            supported.append(
                any(
                    target in connected
                    and any(
                        source == request.global_morph_index and rate != 0.0
                        for source, rate in sources
                    )
                    for target, sources in controller_topology.items()
                )
            )
            continue
        supported.append(bool(_DIRECT_RUNTIME_SUPPORT.get(request.morph_type, False)))
    return tuple(supported)


__all__ = [
    "MorphBindingProjection",
    "MorphBlendShapeReadProjection",
    "MorphProjectionRequest",
    "project_runtime_capabilities",
]
