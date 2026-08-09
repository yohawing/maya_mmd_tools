"""Pure PMX morph authoring operations over :mod:`model_authoring_spec`.

The functions in this module deliberately operate on immutable semantic
specifications.  They validate PMX morph offset payloads before returning a
new specification, so Maya adapters can keep their scene mutations small and
transactional.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
import math
from typing import Any

from mmd_tools.core.model_authoring_spec import MmdModelAuthoringSpec, MmdMorphSpec


class MorphAuthoringError(ValueError):
    """Raised when a morph authoring operation would violate the PMX contract."""


_SUPPORTED_TYPES = {
    "vertex",
    "bone",
    "group",
    "material",
    "uv",
    "additional_uv1",
    "additional_uv2",
    "additional_uv3",
    "additional_uv4",
}
_UNSUPPORTED_TYPES = {"flip", "impulse"}
_ALL_TYPES = _SUPPORTED_TYPES | _UNSUPPORTED_TYPES


def _require_spec(spec: Any) -> MmdModelAuthoringSpec:
    if type(spec) is not MmdModelAuthoringSpec:
        raise TypeError("spec must be an MmdModelAuthoringSpec")
    return spec


def _integer(value: Any, *, field: str, minimum: int = 0, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MorphAuthoringError(f"{field} must be an integer")
    if value < minimum:
        raise MorphAuthoringError(f"{field} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise MorphAuthoringError(f"{field} must be <= {maximum}")
    return value


def _number(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MorphAuthoringError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise MorphAuthoringError(f"{field} must be finite")
    return result


def _vector(value: Any, size: int, *, field: str) -> tuple[float, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise MorphAuthoringError(f"{field} must be a sequence of {size} numbers")
    if len(value) != size:
        raise MorphAuthoringError(f"{field} must contain exactly {size} numbers")
    return tuple(_number(item, field=f"{field}[{index}]") for index, item in enumerate(value))


def _mapping(offset: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(offset, Mapping):
        raise MorphAuthoringError(f"{field} must be a mapping")
    if any(not isinstance(key, str) for key in offset):
        raise MorphAuthoringError(f"{field} keys must be strings")
    return offset


def _keys(offset: Mapping[str, Any], expected: set[str], *, field: str) -> None:
    actual = set(offset)
    unknown = actual - expected
    missing = expected - actual
    if unknown:
        raise MorphAuthoringError(f"{field} contains unknown fields: {sorted(unknown)!r}")
    if missing:
        raise MorphAuthoringError(f"{field} is missing fields: {sorted(missing)!r}")


def _ref(value: Any, *, field: str, allowed: set[int], minimum: int = 0) -> int:
    index = _integer(value, field=field, minimum=minimum)
    if index not in allowed and not (minimum == -1 and index == -1):
        raise MorphAuthoringError(f"{field} references unknown index {index}")
    return index


def _canonical_offset(spec: MmdModelAuthoringSpec, morph_type: str, offset: Any, offset_index: int) -> dict[str, Any]:
    field = f"{morph_type} offset {offset_index}"
    value = _mapping(offset, field=field)
    bone_indices = {bone.index for bone in spec.bones}
    material_indices = {material.index for material in spec.materials}
    morph_indices = {morph.index for morph in spec.morphs}

    if morph_type == "vertex":
        _keys(value, {"vertex_index", "position_offset"}, field=field)
        return {
            "vertex_index": _integer(value["vertex_index"], field=f"{field}.vertex_index"),
            "position_offset": _vector(value["position_offset"], 3, field=f"{field}.position_offset"),
        }
    if morph_type == "bone":
        _keys(value, {"bone_index", "translation", "rotation"}, field=field)
        return {
            "bone_index": _ref(value["bone_index"], field=f"{field}.bone_index", allowed=bone_indices),
            "translation": _vector(value["translation"], 3, field=f"{field}.translation"),
            "rotation": _vector(value["rotation"], 4, field=f"{field}.rotation"),
        }
    if morph_type == "group":
        _keys(value, {"morph_index", "morph_rate"}, field=field)
        return {
            "morph_index": _ref(value["morph_index"], field=f"{field}.morph_index", allowed=morph_indices),
            "morph_rate": _number(value["morph_rate"], field=f"{field}.morph_rate"),
        }
    if morph_type == "flip":
        _keys(value, {"morph_index", "flip_rate"}, field=field)
        return {
            "morph_index": _ref(value["morph_index"], field=f"{field}.morph_index", allowed=morph_indices),
            "flip_rate": _number(value["flip_rate"], field=f"{field}.flip_rate"),
        }
    if morph_type in {"uv", "additional_uv1", "additional_uv2", "additional_uv3", "additional_uv4"}:
        _keys(value, {"vertex_index", "uv_offset"}, field=field)
        return {
            "vertex_index": _integer(value["vertex_index"], field=f"{field}.vertex_index"),
            "uv_offset": _vector(value["uv_offset"], 4, field=f"{field}.uv_offset"),
        }
    if morph_type == "material":
        expected = {
            "material_index",
            "operation_type",
            "diffuse",
            "specular",
            "specular_coefficient",
            "ambient",
            "edge_color",
            "edge_size",
            "texture_factor",
            "sphere_texture_factor",
            "toon_texture_factor",
        }
        _keys(value, expected, field=field)
        material_index = _integer(value["material_index"], field=f"{field}.material_index", minimum=-1)
        if material_index != -1 and material_index not in material_indices:
            raise MorphAuthoringError(f"{field}.material_index references unknown index {material_index}")
        return {
            "material_index": material_index,
            "operation_type": _integer(value["operation_type"], field=f"{field}.operation_type", maximum=1),
            "diffuse": _vector(value["diffuse"], 4, field=f"{field}.diffuse"),
            "specular": _vector(value["specular"], 3, field=f"{field}.specular"),
            "specular_coefficient": _number(value["specular_coefficient"], field=f"{field}.specular_coefficient"),
            "ambient": _vector(value["ambient"], 3, field=f"{field}.ambient"),
            "edge_color": _vector(value["edge_color"], 4, field=f"{field}.edge_color"),
            "edge_size": _number(value["edge_size"], field=f"{field}.edge_size"),
            "texture_factor": _vector(value["texture_factor"], 4, field=f"{field}.texture_factor"),
            "sphere_texture_factor": _vector(value["sphere_texture_factor"], 4, field=f"{field}.sphere_texture_factor"),
            "toon_texture_factor": _vector(value["toon_texture_factor"], 4, field=f"{field}.toon_texture_factor"),
        }
    if morph_type == "impulse":
        expected = {"rigid_body_index", "impulse", "torque"}
        _keys(value, expected, field=field)
        return {
            "rigid_body_index": _integer(value["rigid_body_index"], field=f"{field}.rigid_body_index"),
            "impulse": _vector(value["impulse"], 3, field=f"{field}.impulse"),
            "torque": _vector(value["torque"], 3, field=f"{field}.torque"),
        }
    raise MorphAuthoringError(f"unsupported morph type: {morph_type!r}")


def _validate_policy(morph: MmdMorphSpec) -> None:
    if morph.morph_type not in _ALL_TYPES:
        raise MorphAuthoringError(f"unsupported morph type: {morph.morph_type!r}")
    if morph.morph_type in _UNSUPPORTED_TYPES:
        if morph.runtime_capability != "unsupported" or morph.loss_policy != "reject":
            raise MorphAuthoringError(
                f"{morph.morph_type} morphs require runtime_capability='unsupported' and loss_policy='reject'"
            )
        return
    if morph.runtime_capability not in {"supported", "unsupported", "experimental", "lossy"}:
        raise MorphAuthoringError(f"unsupported runtime capability for {morph.morph_type}: {morph.runtime_capability!r}")
    if morph.loss_policy not in {"none", "reject", "warn", "preserve"}:
        raise MorphAuthoringError(f"unsupported loss policy for {morph.morph_type}: {morph.loss_policy!r}")


def _validated_morph(spec: MmdModelAuthoringSpec, morph: MmdMorphSpec) -> MmdMorphSpec:
    if type(morph) is not MmdMorphSpec:
        raise TypeError("morph must be an MmdMorphSpec")
    _validate_policy(morph)
    offsets = tuple(_canonical_offset(spec, morph.morph_type, offset, index) for index, offset in enumerate(morph.offsets))
    return replace(morph, offsets=offsets)


def _with_morphs(spec: MmdModelAuthoringSpec, morphs: Sequence[MmdMorphSpec]) -> MmdModelAuthoringSpec:
    return replace(spec, morphs=tuple(morphs))


def create_morph(spec: MmdModelAuthoringSpec, morph: MmdMorphSpec | None = None) -> MmdModelAuthoringSpec:
    """Create a morph at the next PMX index, ignoring any supplied index."""
    spec = _require_spec(spec)
    next_index = max((item.index for item in spec.morphs), default=-1) + 1
    candidate = morph or MmdMorphSpec(name="New Morph", name_english="New Morph", panel=4)
    candidate = replace(candidate, index=next_index)
    candidate = _validated_morph(spec, candidate)
    return _with_morphs(spec, (*spec.morphs, candidate))


def replace_morph(spec: MmdModelAuthoringSpec, morph: MmdMorphSpec) -> MmdModelAuthoringSpec:
    """Replace an existing morph while preserving non-empty type semantics."""
    spec = _require_spec(spec)
    existing = next((item for item in spec.morphs if item.index == morph.index), None)
    if existing is None:
        raise MorphAuthoringError(f"morph index {morph.index} does not exist")
    if existing.morph_type != morph.morph_type and existing.offsets:
        raise MorphAuthoringError("cannot change morph type while existing offsets are non-empty")
    candidate = _validated_morph(spec, morph)
    return _with_morphs(spec, tuple(candidate if item.index == morph.index else item for item in spec.morphs))


def replace_morph_offsets(
    spec: MmdModelAuthoringSpec,
    index: int,
    offsets: Sequence[Mapping[str, Any]],
) -> MmdModelAuthoringSpec:
    """Validate and replace one morph's offsets with canonical numeric values."""
    spec = _require_spec(spec)
    _integer(index, field="morph index")
    existing = next((item for item in spec.morphs if item.index == index), None)
    if existing is None:
        raise MorphAuthoringError(f"morph index {index} does not exist")
    if isinstance(offsets, (str, bytes, bytearray)) or not isinstance(offsets, Sequence):
        raise MorphAuthoringError("offsets must be a sequence")
    canonical_offsets = tuple(
        _canonical_offset(spec, existing.morph_type, offset, offset_index)
        for offset_index, offset in enumerate(offsets)
    )
    candidate = _validated_morph(spec, replace(existing, offsets=canonical_offsets))
    return _with_morphs(spec, tuple(candidate if item.index == index else item for item in spec.morphs))


def _remap_offsets(morph: MmdMorphSpec, remap: Mapping[int, int], spec: MmdModelAuthoringSpec) -> MmdMorphSpec:
    if morph.morph_type not in {"group", "flip"}:
        return morph
    offsets = []
    for offset_number, offset in enumerate(morph.offsets):
        if "morph_index" not in offset:
            raise MorphAuthoringError(f"morph {morph.index} offset {offset_number} is missing morph_index")
        index = _integer(
            offset["morph_index"],
            field=f"morph {morph.index} offset {offset_number}.morph_index",
        )
        if index not in remap:
            raise MorphAuthoringError(f"morph offset references unknown index {index}")
        updated = dict(offset)
        updated["morph_index"] = remap[index]
        offsets.append(updated)
    return _validated_morph(spec, replace(morph, offsets=tuple(offsets)))


def delete_morph(spec: MmdModelAuthoringSpec, index: int) -> MmdModelAuthoringSpec:
    """Delete an unreferenced morph and compact all following PMX indices."""
    spec = _require_spec(spec)
    _integer(index, field="morph index")
    if not any(item.index == index for item in spec.morphs):
        raise MorphAuthoringError(f"morph index {index} does not exist")
    for morph in spec.morphs:
        if morph.morph_type in {"group", "flip"}:
            for offset in morph.offsets:
                if offset.get("morph_index") == index:
                    raise MorphAuthoringError(f"morph index {index} is referenced by {morph.index}")
    survivors = [morph for morph in spec.morphs if morph.index != index]
    remap = {old.index: new for new, old in enumerate(survivors)}
    compacted = [replace(morph, index=remap[morph.index]) for morph in survivors]
    interim = _with_morphs(spec, compacted)
    return _with_morphs(interim, tuple(_remap_offsets(morph, remap, interim) for morph in interim.morphs))


def reindex_morphs(spec: MmdModelAuthoringSpec, ordered_indices: Sequence[int]) -> MmdModelAuthoringSpec:
    """Apply an exact morph permutation and update group/flip references."""
    spec = _require_spec(spec)
    if isinstance(ordered_indices, (str, bytes, bytearray)) or not isinstance(ordered_indices, Sequence):
        raise MorphAuthoringError("ordered_indices must be a sequence")
    current = [morph.index for morph in spec.morphs]
    requested = list(ordered_indices)
    if any(isinstance(index, bool) or not isinstance(index, int) for index in requested):
        raise MorphAuthoringError("ordered_indices must contain integers")
    if len(requested) != len(current) or set(requested) != set(current):
        raise MorphAuthoringError("ordered_indices must be an exact permutation of existing indices")
    remap = {old: new for new, old in enumerate(requested)}
    by_index = {morph.index: morph for morph in spec.morphs}
    interim = _with_morphs(spec, tuple(replace(by_index[old], index=remap[old]) for old in requested))
    return _with_morphs(interim, tuple(_remap_offsets(morph, remap, interim) for morph in interim.morphs))


def move_morph(spec: MmdModelAuthoringSpec, index: int, new_position: int) -> MmdModelAuthoringSpec:
    """Move one morph to a zero-based position in the current ordering."""
    spec = _require_spec(spec)
    _integer(index, field="morph index")
    _integer(new_position, field="new_position", maximum=max(0, len(spec.morphs) - 1))
    ordered = [morph.index for morph in spec.morphs]
    if index not in ordered:
        raise MorphAuthoringError(f"morph index {index} does not exist")
    ordered.remove(index)
    ordered.insert(new_position, index)
    return reindex_morphs(spec, ordered)


__all__ = [
    "MorphAuthoringError",
    "create_morph",
    "replace_morph",
    "replace_morph_offsets",
    "delete_morph",
    "reindex_morphs",
    "move_morph",
]
