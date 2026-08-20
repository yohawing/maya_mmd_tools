"""Pure route selection for one VMD model import.

The converter still owns Maya preflight and mutation.  This module only makes
the mutually exclusive route decision explicit after those capabilities have
been observed, so individual boolean flags do not silently select competing
animation owners.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


class VmdImportRouteError(ValueError):
    """Raised when VMD route inputs cannot describe a safe import."""


@dataclass(frozen=True)
class VmdImportPlan:
    """Immutable route selected for one VMD conversion."""

    route: str
    use_runtime_bake: bool = False
    use_registered_sparse: bool = False


def plan_vmd_import_route(
    *,
    scene_animation_only: bool,
    target_model: Optional[str],
    bake_mode: bool,
    create_mmd_control_rig: bool,
    runtime_bake_available: bool,
    registered_sparse_available: bool,
) -> VmdImportPlan:
    """Select exactly one VMD route from preflighted capabilities.

    ``runtime_bake_available`` and ``registered_sparse_available`` are facts
    computed by the converter; this function never probes Maya or the native
    runtime.  Control Rig ownership always wins among model routes, while a
    Bake/Control-Rig combination remains invalid rather than being silently
    resolved by precedence.
    """
    if scene_animation_only:
        return VmdImportPlan(route="scene_animation_only")
    if not isinstance(target_model, str) or not target_model.strip():
        raise VmdImportRouteError("VMD model motion requires an explicit target model")
    if create_mmd_control_rig and bake_mode:
        raise VmdImportRouteError(
            "MMD Control Rig import cannot be combined with Bake Motion"
        )
    if create_mmd_control_rig:
        return VmdImportPlan(
            route="control_rig",
            use_registered_sparse=bool(registered_sparse_available),
        )
    if runtime_bake_available:
        return VmdImportPlan(route="runtime_bake", use_runtime_bake=True)
    if registered_sparse_available:
        return VmdImportPlan(route="registered_sparse", use_registered_sparse=True)
    return VmdImportPlan(route="legacy")


__all__ = ["VmdImportPlan", "VmdImportRouteError", "plan_vmd_import_route"]
