"""Pure semantic material authoring operations for MMD model specifications.

This module contains the Maya-independent mutation core used by material
authoring adapters.  Every operation treats :class:`MmdModelAuthoringSpec`
as immutable input and returns a new validated specification.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable, Optional, Sequence

from mmd_tools.core.model_authoring_spec import (
    MmdMaterialSpec,
    MmdModelAuthoringSpec,
    MmdMorphSpec,
)


class MaterialAuthoringError(ValueError):
    """Raised when a material mutation would produce an invalid model spec."""


# Material fields which can be changed without changing a Maya binding or
# texture graph.  Keep this allowlist explicit: a mixed edit must take the
# existing binding transaction rather than being guessed into the narrow
# path.
MATERIAL_VALUE_FIELDS = frozenset(
    {
        "name",
        "name_english",
        "diffuse",
        "specular",
        "specular_coefficient",
        "ambient",
        "draw_flags",
        "edge_color",
        "edge_size",
        "memo",
    }
)


def classify_material_change(old: MmdMaterialSpec, new: MmdMaterialSpec) -> str:
    """Classify one material replacement for transaction routing.

    Returns ``"noop"`` for an identical semantic material, ``"value"`` when
    every changed field is in :data:`MATERIAL_VALUE_FIELDS`, and
    ``"binding"`` for texture/provenance, toon/sphere binding, index, or
    binding-identity changes.  The latter category deliberately includes
    mixed edits so callers retain the established full binding transaction.
    """
    old = _require_material(old)
    new = _require_material(new)
    old_mapping = old.to_mapping()
    new_mapping = new.to_mapping()
    changed = {
        field
        for field in old_mapping
        if old_mapping[field] != new_mapping[field]
    }
    if not changed:
        return "noop"
    return "value" if changed.issubset(MATERIAL_VALUE_FIELDS) else "binding"


def _require_spec(spec: Any) -> MmdModelAuthoringSpec:
    """Validate and return an exact model authoring spec instance."""
    if not isinstance(spec, MmdModelAuthoringSpec):
        raise MaterialAuthoringError("spec must be an MmdModelAuthoringSpec")
    return spec


def _require_material(material: Any) -> MmdMaterialSpec:
    """Validate and return an exact material spec instance."""
    if not isinstance(material, MmdMaterialSpec):
        raise MaterialAuthoringError("material must be an MmdMaterialSpec")
    return material


def _require_index(index: Any, *, field: str = "index") -> int:
    """Validate a non-boolean integer index."""
    if isinstance(index, bool) or not isinstance(index, int):
        raise MaterialAuthoringError(f"{field} must be an integer")
    return index


def _next_index(materials: Iterable[MmdMaterialSpec]) -> int:
    """Return the deterministic next index after the current materials."""
    indices = [material.index for material in materials]
    return max(indices, default=-1) + 1


def _unique_name(base: str, used: set[str], *, fallback: str) -> str:
    """Return a deterministic, Unicode-safe name not present in ``used``."""
    root = base or fallback
    candidate = root
    suffix = 2
    while candidate in used:
        candidate = f"{root} ({suffix})"
        suffix += 1
    return candidate


def _duplicate_names(material: MmdMaterialSpec, materials: tuple[MmdMaterialSpec, ...], index: int) -> tuple[str, str]:
    """Allocate deterministic non-colliding names for a duplicated material."""
    used_names = {item.name for item in materials}
    name_root = material.name or f"Material {index}"
    name = _unique_name(f"{name_root} Copy", used_names, fallback=f"Material {index}")

    used_english = {item.name_english for item in materials}
    english_root = material.name_english or name
    english = _unique_name(f"{english_root} Copy", used_english, fallback=name)
    return name, english


def _default_material(index: int, materials: tuple[MmdMaterialSpec, ...]) -> MmdMaterialSpec:
    """Create the complete deterministic default material for ``index``."""
    name = _unique_name(f"Material {index}", {item.name for item in materials}, fallback=f"Material {index}")
    english = _unique_name(
        f"Material {index}",
        {item.name_english for item in materials},
        fallback=name,
    )
    return MmdMaterialSpec(name=name, name_english=english, index=index)


def create_material(spec: MmdModelAuthoringSpec, material: Optional[MmdMaterialSpec] = None) -> MmdModelAuthoringSpec:
    """Append a material using a deterministic index allocation.

    The requested index on a supplied ``material`` is deliberately ignored;
    the returned material always receives the next allocated PMX index.
    """
    spec = _require_spec(spec)
    materials = tuple(spec.materials)
    index = _next_index(materials)
    if material is None:
        created = _default_material(index, materials)
    else:
        source = _require_material(material)
        created = replace(source, index=index)
    return MmdModelAuthoringSpec(
        model=spec.model,
        bones=spec.bones,
        materials=materials + (created,),
        morphs=spec.morphs,
        schema_version=spec.schema_version,
    )


def duplicate_material(spec: MmdModelAuthoringSpec, source_index: int) -> MmdModelAuthoringSpec:
    """Duplicate one material and append it at the next deterministic index."""
    spec = _require_spec(spec)
    source_index = _require_index(source_index, field="source_index")
    materials = tuple(spec.materials)
    try:
        source = next(item for item in materials if item.index == source_index)
    except StopIteration as exc:
        raise MaterialAuthoringError(f"material index does not exist: {source_index}") from exc
    index = _next_index(materials)
    name, name_english = _duplicate_names(source, materials, index)
    duplicated = replace(source, index=index, name=name, name_english=name_english)
    return MmdModelAuthoringSpec(
        model=spec.model,
        bones=spec.bones,
        materials=materials + (duplicated,),
        morphs=spec.morphs,
        schema_version=spec.schema_version,
    )


def replace_material(spec: MmdModelAuthoringSpec, material: MmdMaterialSpec) -> MmdModelAuthoringSpec:
    """Replace an existing material while preserving every other collection."""
    spec = _require_spec(spec)
    material = _require_material(material)
    target_index = material.index
    if target_index not in {item.index for item in spec.materials}:
        raise MaterialAuthoringError(f"material index does not exist: {target_index}")
    materials = tuple(material if item.index == target_index else item for item in spec.materials)
    return MmdModelAuthoringSpec(
        model=spec.model,
        bones=spec.bones,
        materials=materials,
        morphs=spec.morphs,
        schema_version=spec.schema_version,
    )


def _remap_material_morphs(
    morphs: tuple[MmdMorphSpec, ...],
    old_to_new: dict[int, int],
    deleted_index: int | None,
) -> tuple[MmdMorphSpec, ...]:
    """Validate and transactionally remap material morph offsets."""
    updated: list[MmdMorphSpec] = []
    for morph in morphs:
        if morph.morph_type != "material":
            updated.append(morph)
            continue
        offsets: list[dict[str, Any]] = []
        for offset_number, offset in enumerate(morph.offsets):
            if "material_index" not in offset:
                raise MaterialAuthoringError(
                    f"morph {morph.index} offset {offset_number} is missing material_index"
                )
            material_index = offset["material_index"]
            if isinstance(material_index, bool) or not isinstance(material_index, int):
                raise MaterialAuthoringError(
                    f"morph {morph.index} offset {offset_number} material_index must be an integer"
                )
            if material_index == -1:
                new_index = -1
            elif deleted_index is not None and material_index == deleted_index:
                raise MaterialAuthoringError(
                    f"material {deleted_index} is referenced by morph {morph.index} offset {offset_number}"
                )
            elif material_index not in old_to_new:
                raise MaterialAuthoringError(
                    f"morph {morph.index} offset {offset_number} references unknown material {material_index}"
                )
            else:
                new_index = old_to_new[material_index]
            updated_offset = dict(offset)
            updated_offset["material_index"] = new_index
            offsets.append(updated_offset)
        updated.append(replace(morph, offsets=tuple(offsets)))
    return tuple(updated)


def reindex_materials(
    spec: MmdModelAuthoringSpec,
    ordered_indices: Sequence[int],
) -> MmdModelAuthoringSpec:
    """Reorder every material and remap material-morph references atomically."""
    spec = _require_spec(spec)
    if isinstance(ordered_indices, (str, bytes, bytearray)) or not isinstance(
        ordered_indices, Sequence
    ):
        raise MaterialAuthoringError("ordered_indices must be a sequence")
    requested = tuple(
        _require_index(index, field=f"ordered_indices[{position}]")
        for position, index in enumerate(ordered_indices)
    )
    materials_by_index = {material.index: material for material in spec.materials}
    if len(requested) != len(materials_by_index) or set(requested) != set(materials_by_index):
        raise MaterialAuthoringError("ordered_indices must contain every material index exactly once")
    old_to_new = {old_index: new_index for new_index, old_index in enumerate(requested)}
    materials = tuple(
        replace(materials_by_index[old_index], index=old_to_new[old_index])
        for old_index in requested
    )
    morphs = _remap_material_morphs(tuple(spec.morphs), old_to_new, None)
    return MmdModelAuthoringSpec(
        model=spec.model,
        bones=spec.bones,
        materials=materials,
        morphs=morphs,
        schema_version=spec.schema_version,
    )


def move_material(
    spec: MmdModelAuthoringSpec,
    index: int,
    new_position: int,
) -> MmdModelAuthoringSpec:
    """Move one material by exactly one adjacent position.

    Material reordering in the authoring UI is intentionally narrower than
    the general permutation API.  Restricting this pure operation to an
    adjacent swap lets the Maya binding layer update only the two affected
    shader indices and any Material Morph offsets that reference them.
    """
    spec = _require_spec(spec)
    index = _require_index(index, field="material index")
    new_position = _require_index(new_position, field="new_position")
    ordered = [material.index for material in sorted(spec.materials, key=lambda item: item.index)]
    try:
        current_position = ordered.index(index)
    except ValueError as exc:
        raise MaterialAuthoringError(f"material index does not exist: {index}") from exc
    if new_position < 0 or new_position >= len(ordered):
        raise MaterialAuthoringError(
            f"new_position must be within 0..{max(0, len(ordered) - 1)}"
        )
    if abs(current_position - new_position) != 1:
        raise MaterialAuthoringError("material move must target an adjacent position")
    ordered[current_position], ordered[new_position] = (
        ordered[new_position],
        ordered[current_position],
    )
    return reindex_materials(spec, ordered)


def delete_material(spec: MmdModelAuthoringSpec, index: int) -> MmdModelAuthoringSpec:
    """Delete and contiguously reindex one material with morph remapping."""
    spec = _require_spec(spec)
    index = _require_index(index)
    materials = tuple(spec.materials)
    if index not in {item.index for item in materials}:
        raise MaterialAuthoringError(f"material index does not exist: {index}")

    remaining = tuple(item for item in materials if item.index != index)
    old_to_new = {item.index: new_index for new_index, item in enumerate(remaining)}
    reindexed = tuple(replace(item, index=old_to_new[item.index]) for item in remaining)
    morphs = _remap_material_morphs(tuple(spec.morphs), old_to_new, index)
    return MmdModelAuthoringSpec(
        model=spec.model,
        bones=spec.bones,
        materials=reindexed,
        morphs=morphs,
        schema_version=spec.schema_version,
    )


__all__ = [
    "MaterialAuthoringError",
    "MATERIAL_VALUE_FIELDS",
    "classify_material_change",
    "create_material",
    "duplicate_material",
    "replace_material",
    "reindex_materials",
    "move_material",
    "delete_material",
]
