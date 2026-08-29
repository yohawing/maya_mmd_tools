"""Compose the production Maya model-authoring transaction boundary.

The UI imports this module only while Maya is running.  Keeping construction in
one place guarantees that Material, Bone, and Morph presenters share the same
cmds adapter, metadata backend, undo owner, and runtime morph rebuilders.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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


@dataclass(frozen=True)
class MayaAuthoringComposition:
    """Production objects shared by all model-authoring presenters."""

    cmds_adapter: MayaCmdsAdapter
    metadata_backend: MayaSceneMetadataBackend
    metadata_adapter: SceneMetadataAdapter
    material_authoring: MayaMaterialAuthoring
    coordinator: MayaModelAuthoringCoordinator
    model_initializer: MayaModelTemplateInitializer
    create_model_action: CreateModelAction
    material_morph_work: MayaMaterialMorphWork


def build_maya_authoring_composition(
    cmds_module: Any | None = None,
    *,
    registry_api: Any = model_registry,
) -> MayaAuthoringComposition:
    """Build the complete production authoring graph for one Maya UI window."""
    cmds_adapter = MayaCmdsAdapter(cmds_module)
    metadata_backend = MayaSceneMetadataBackend(cmds_adapter)
    metadata_adapter = SceneMetadataAdapter(metadata_backend)
    material_authoring = MayaMaterialAuthoring(
        cmds_adapter,
        registry_api=registry_api,
        mutation_boundary=metadata_backend.mark_mutation,
    )
    rebuilders = maya_runtime_rebuilders()

    def apply_morph_change(root: str, old_spec: Any, new_spec: Any) -> Any:
        return apply_morph_spec_change(
            root,
            old_spec,
            new_spec,
            cmds_adapter,
            registry_api=registry_api,
            runtime_rebuilders=rebuilders,
        )

    coordinator = MayaModelAuthoringCoordinator(
        metadata_adapter,
        metadata_backend,
        material_authoring,
        cmds_adapter,
        morph_authoring=apply_morph_change,
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

    return MayaAuthoringComposition(
        cmds_adapter=cmds_adapter,
        metadata_backend=metadata_backend,
        metadata_adapter=metadata_adapter,
        material_authoring=material_authoring,
        coordinator=coordinator,
        model_initializer=model_initializer,
        create_model_action=CreateModelAction(model_initializer),
        material_morph_work=material_morph_work,
    )


__all__ = ["MayaAuthoringComposition", "build_maya_authoring_composition"]
