"""Compose the production Maya model-authoring transaction boundary.

The UI imports this module only while Maya is running.  Keeping construction in
one place guarantees that Material, Bone, and Morph presenters share the same
cmds adapter, metadata backend, undo owner, and runtime morph rebuilders.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Callable

from mmd_tools.actions.create_model_action import CreateModelAction
from mmd_tools.adapters.maya_cmds_adapter import MayaCmdsAdapter
from mmd_tools.adapters.maya_material_authoring import MayaMaterialAuthoring
from mmd_tools.adapters.maya_material_morph_work import MayaMaterialMorphWork
from mmd_tools.adapters.maya_model_authoring_coordinator import MayaModelAuthoringCoordinator
from mmd_tools.adapters.maya_model_template_initializer import MayaModelTemplateInitializer
from mmd_tools.adapters.maya_morph_authoring import apply_morph_spec_change, maya_runtime_rebuilders
from mmd_tools.adapters.maya_scene_metadata_backend import MayaSceneMetadataBackend
from mmd_tools.adapters.scene_metadata_adapter import SceneMetadataAdapter
from mmd_tools.core import model_registry
from mmd_tools.core.constants import ATTR_MMD_IMPORT_SCALE


@dataclass(frozen=True)
class MayaAuthoringComposition:
    """Production objects shared by all model-authoring presenters."""

    cmds_adapter: MayaCmdsAdapter
    metadata_backend: MayaSceneMetadataBackend
    metadata_adapter: SceneMetadataAdapter
    material_authoring: MayaMaterialAuthoring
    coordinator: MayaModelAuthoringCoordinator
    model_scale_resolver: Callable[[str], float]
    model_initializer: MayaModelTemplateInitializer
    create_model_action: CreateModelAction
    material_morph_work: MayaMaterialMorphWork
    run_authoring_e2e: Callable[..., dict[str, Any]]


def build_maya_authoring_composition(
    cmds_module: Any | None = None,
    *,
    registry_api: Any = model_registry,
) -> MayaAuthoringComposition:
    """Build the complete production authoring graph for one Maya UI window."""
    cmds_adapter = MayaCmdsAdapter(cmds_module)
    metadata_backend = MayaSceneMetadataBackend(cmds_adapter)
    metadata_adapter = SceneMetadataAdapter(metadata_backend)
    material_authoring = MayaMaterialAuthoring(cmds_adapter, registry_api=registry_api)
    rebuilders = maya_runtime_rebuilders()

    def apply_morph_change(root: str, old_spec: Any, new_spec: Any) -> Any:
        return apply_morph_spec_change(
            root,
            old_spec,
            new_spec,
            cmds_adapter,
            registry_api=registry_api,
            runtime_rebuilders=rebuilders,
            model_scale_resolver=resolve_model_scale,
        )

    def resolve_model_scale(root: str) -> float:
        if not cmds_adapter.attribute_exists(ATTR_MMD_IMPORT_SCALE, root):
            raise RuntimeError(
                f"{root!r} has no persisted MMD import scale; "
                "Capture Rest is unavailable for this legacy model"
            )
        value = cmds_adapter.get_attr(f"{root}.{ATTR_MMD_IMPORT_SCALE}")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RuntimeError(f"{root!r} has an invalid persisted MMD import scale")
        scale = float(value)
        if not math.isfinite(scale) or scale <= 0.0:
            raise RuntimeError(f"{root!r} has a non-positive or non-finite persisted MMD import scale")
        return scale

    coordinator = MayaModelAuthoringCoordinator(
        metadata_adapter,
        metadata_backend,
        material_authoring,
        cmds_adapter,
        morph_authoring=apply_morph_change,
        model_scale_resolver=resolve_model_scale,
    )
    model_initializer = MayaModelTemplateInitializer(
        cmds_adapter,
        metadata_backend_factory=lambda _adapter: metadata_backend,
        material_authoring_factory=lambda _adapter: material_authoring,
    )
    material_morph_work = MayaMaterialMorphWork(
        cmds_adapter,
        coordinator,
        registry_api=registry_api,
    )

    def run_authoring_e2e(**kwargs: Any) -> dict[str, Any]:
        """Execute the Maya authoring round-trip with this shared composition."""
        from mmd_tools.adapters.maya_authoring_e2e import run_authoring_e2e as run_e2e

        return run_e2e(
            coordinator=coordinator,
            metadata_adapter=metadata_adapter,
            cmds_adapter=cmds_adapter,
            material_authoring=material_authoring,
            **kwargs,
        )

    return MayaAuthoringComposition(
        cmds_adapter=cmds_adapter,
        metadata_backend=metadata_backend,
        metadata_adapter=metadata_adapter,
        material_authoring=material_authoring,
        coordinator=coordinator,
        model_scale_resolver=resolve_model_scale,
        model_initializer=model_initializer,
        create_model_action=CreateModelAction(model_initializer),
        material_morph_work=material_morph_work,
        run_authoring_e2e=run_authoring_e2e,
    )


__all__ = ["MayaAuthoringComposition", "build_maya_authoring_composition"]
