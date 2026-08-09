"""Maya-independent application routing for model authoring operations.

The service deliberately owns no scene state.  Each mutating operation reads
one immutable specification through :class:`SceneMetadataAdapter`, delegates
to a pure authoring function, and writes the complete replacement spec.  A
failed pure mutation therefore performs no write at all; Maya bindings remain
behind the adapter boundary.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Callable

from mmd_tools.adapters.scene_metadata_adapter import SceneMetadataAdapter
from mmd_tools.core.bone_authoring import (
    capture_rest,
    register_bone,
    reindex_bones,
    replace_bone,
    unregister_bone,
)
from mmd_tools.core.material_authoring import (
    create_material,
    delete_material,
    duplicate_material,
    replace_material,
)
from mmd_tools.core.model_authoring_spec import (
    MmdBoneSpec,
    MmdMaterialSpec,
    MmdModelAuthoringSpec,
    MmdMorphSpec,
)
from mmd_tools.core.model_template import instantiate_model_template
from mmd_tools.core.morph_authoring import (
    create_morph,
    delete_morph,
    move_morph,
    reindex_morphs,
    replace_morph,
    replace_morph_offsets,
)


class ModelAuthoringServiceError(ValueError):
    """Raised when an authoring service operation cannot be completed."""


class ModelAuthoringService:
    """Route pure authoring mutations through a scene metadata adapter."""

    def __init__(self, metadata_adapter: SceneMetadataAdapter) -> None:
        if not callable(getattr(metadata_adapter, "read_spec", None)) or not callable(
            getattr(metadata_adapter, "write_spec", None)
        ):
            raise TypeError("metadata_adapter must provide read_spec and write_spec")
        self._metadata_adapter = metadata_adapter

    def _mutate(
        self,
        model_root: str,
        operation: str,
        mutation: Callable[[MmdModelAuthoringSpec], MmdModelAuthoringSpec],
    ) -> MmdModelAuthoringSpec:
        """Read, mutate, and write one complete spec transactionally."""
        if not isinstance(model_root, str) or not model_root.strip():
            raise ModelAuthoringServiceError("model_root must be a non-empty string")
        try:
            current = self._metadata_adapter.read_spec(model_root)
        except Exception as exc:
            raise ModelAuthoringServiceError(f"{operation} read failed for root {model_root!r}: {exc}") from exc
        try:
            updated = mutation(current)
        except Exception as exc:
            raise ModelAuthoringServiceError(f"{operation} mutation failed for root {model_root!r}: {exc}") from exc
        if not isinstance(updated, MmdModelAuthoringSpec):
            raise ModelAuthoringServiceError(f"{operation} returned an invalid authoring spec")
        try:
            self._metadata_adapter.write_spec(model_root, updated)
        except Exception as exc:
            raise ModelAuthoringServiceError(f"{operation} write failed for root {model_root!r}: {exc}") from exc
        return updated

    # Material authoring -------------------------------------------------
    def create_material(self, model_root: str, material: MmdMaterialSpec | None = None) -> MmdModelAuthoringSpec:
        """Create one material and persist the resulting full specification."""
        return self._mutate(model_root, "create_material", lambda spec: create_material(spec, material))

    def duplicate_material(self, model_root: str, source_index: int) -> MmdModelAuthoringSpec:
        """Duplicate a material by explicit PMX index."""
        return self._mutate(model_root, "duplicate_material", lambda spec: duplicate_material(spec, source_index))

    def replace_material(self, model_root: str, material: MmdMaterialSpec) -> MmdModelAuthoringSpec:
        """Replace one existing material by its explicit PMX index."""
        return self._mutate(model_root, "replace_material", lambda spec: replace_material(spec, material))

    def delete_material(self, model_root: str, index: int) -> MmdModelAuthoringSpec:
        """Delete one material and let the pure operation remap references."""
        return self._mutate(model_root, "delete_material", lambda spec: delete_material(spec, index))

    # Bone authoring -----------------------------------------------------
    def register_bone(self, model_root: str, bone: MmdBoneSpec) -> MmdModelAuthoringSpec:
        """Register a bound bone at the next available explicit index."""
        return self._mutate(model_root, "register_bone", lambda spec: register_bone(spec, bone))

    def replace_bone(self, model_root: str, bone: MmdBoneSpec) -> MmdModelAuthoringSpec:
        """Replace an existing bone while preserving its binding identity."""
        return self._mutate(model_root, "replace_bone", lambda spec: replace_bone(spec, bone))

    def capture_rest(
        self,
        model_root: str,
        index: int,
        rest_position: Sequence[float],
    ) -> MmdModelAuthoringSpec:
        """Capture one explicit PMX rest position."""
        return self._mutate(model_root, "capture_rest", lambda spec: capture_rest(spec, index, rest_position))

    def reindex_bones(self, model_root: str, ordered_indices: Sequence[int]) -> MmdModelAuthoringSpec:
        """Apply an exact bone permutation and rewrite references."""
        return self._mutate(model_root, "reindex_bones", lambda spec: reindex_bones(spec, ordered_indices))

    def unregister_bone(self, model_root: str, index: int) -> MmdModelAuthoringSpec:
        """Remove one unreferenced bone and compact surviving indices."""
        return self._mutate(model_root, "unregister_bone", lambda spec: unregister_bone(spec, index))

    # Morph authoring ----------------------------------------------------
    def create_morph(self, model_root: str, morph: MmdMorphSpec | None = None) -> MmdModelAuthoringSpec:
        """Create one morph at the next available explicit index."""
        return self._mutate(model_root, "create_morph", lambda spec: create_morph(spec, morph))

    def replace_morph(self, model_root: str, morph: MmdMorphSpec) -> MmdModelAuthoringSpec:
        """Replace one existing morph after pure semantic validation."""
        return self._mutate(model_root, "replace_morph", lambda spec: replace_morph(spec, morph))

    def replace_morph_offsets(
        self,
        model_root: str,
        index: int,
        offsets: Sequence[Mapping[str, Any]],
    ) -> MmdModelAuthoringSpec:
        """Replace one morph's offsets after type-specific validation."""
        return self._mutate(
            model_root,
            "replace_morph_offsets",
            lambda spec: replace_morph_offsets(spec, index, offsets),
        )

    def delete_morph(self, model_root: str, index: int) -> MmdModelAuthoringSpec:
        """Delete one unreferenced morph and compact surviving indices."""
        return self._mutate(model_root, "delete_morph", lambda spec: delete_morph(spec, index))

    def reindex_morphs(self, model_root: str, ordered_indices: Sequence[int]) -> MmdModelAuthoringSpec:
        """Apply an exact morph permutation and rewrite references."""
        return self._mutate(model_root, "reindex_morphs", lambda spec: reindex_morphs(spec, ordered_indices))

    def move_morph(self, model_root: str, index: int, new_position: int) -> MmdModelAuthoringSpec:
        """Move one morph to a zero-based position."""
        return self._mutate(model_root, "move_morph", lambda spec: move_morph(spec, index, new_position))

    # Packaged templates -------------------------------------------------
    @staticmethod
    def instantiate_template(
        template_id: str,
        model_name: str,
        model_name_english: str = "",
    ) -> MmdModelAuthoringSpec:
        """Return a fresh packaged template spec without touching scene state."""
        try:
            template = instantiate_model_template(template_id, model_name, model_name_english)
            return template.spec
        except Exception as exc:
            raise ModelAuthoringServiceError(f"template instantiation failed for {template_id!r}: {exc}") from exc

    @staticmethod
    def instantiate_model_template(
        template_id: str,
        model_name: str,
        model_name_english: str = "",
    ) -> MmdModelAuthoringSpec:
        """Compatibility spelling for :meth:`instantiate_template`."""
        return ModelAuthoringService.instantiate_template(template_id, model_name, model_name_english)


__all__ = ["ModelAuthoringServiceError", "ModelAuthoringService"]
