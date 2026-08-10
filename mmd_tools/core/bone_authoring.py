"""Pure semantic operations for PMX bone authoring.

The functions in this module operate only on :class:`MmdModelAuthoringSpec`
values.  They intentionally do not inspect Maya transforms or scene metadata;
the Maya adapter must perform those transactions separately before a later
binding/export step.  In particular, display-frame JSON and physics
``relatedBoneIndex`` references remain Maya-scene preconditions and are not
silently rewritten here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
import math
from typing import Any

from mmd_tools.core.model_authoring_spec import (
    MmdBoneSpec,
    MmdModelAuthoringSpec,
    MmdMorphSpec,
)
from mmd_tools.core.pmx_data.bone import PmxBoneFlag


class BoneAuthoringError(ValueError):
    """Raised for any malformed or semantically invalid bone operation."""


@dataclass(frozen=True)
class BoneResetPlan:
    """Immutable preflight result for one scene-as-authority bone reset.

    ``current_spec`` is the snapshot used during planning and
    ``target_spec`` is the complete semantic payload that a transaction may
    apply.  The plan deliberately contains no Maya handles or mutable lists;
    adapters can therefore reject a stale plan before opening an undo chunk.
    """

    current_spec: MmdModelAuthoringSpec
    target_spec: MmdModelAuthoringSpec | None
    expected_fingerprint: str
    requested_order: tuple[str, ...] = ()
    added_bindings: tuple[str, ...] = ()
    removed_bindings: tuple[str, ...] = ()
    rest_updated_indices: tuple[int, ...] = ()
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.blockers and self.target_spec is not None

    @property
    def diff(self) -> dict[str, int]:
        return {
            "added": len(self.added_bindings),
            "removed": len(self.removed_bindings),
            "rest_updated": len(self.rest_updated_indices),
            "reindexed": sum(
                old.index != new.index
                for old in self.current_spec.bones
                for new in ((self.target_spec or self.current_spec).bones)
                if old.binding_identity is not None
                and old.binding_identity == new.binding_identity
            ),
        }


_REFERENCE_FIELDS = (
    "parent_index",
    "connect_bone_index",
    "grant_parent_index",
    "ik_target_index",
)


def _fail(message: str) -> None:
    raise BoneAuthoringError(message)


def _require_spec(spec: Any) -> MmdModelAuthoringSpec:
    if not isinstance(spec, MmdModelAuthoringSpec):
        _fail("spec must be an MmdModelAuthoringSpec")
    return spec


def _require_bone(bone: Any) -> MmdBoneSpec:
    if not isinstance(bone, MmdBoneSpec):
        _fail("bone must be an MmdBoneSpec")
    return bone


def _require_index(value: Any, *, context: str, allow_minus_one: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{context} must be an integer")
    minimum = -1 if allow_minus_one else 0
    if value < minimum:
        _fail(f"{context} must be >= {minimum}")
    return value


def _require_nonempty_identity(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{context} must be a non-empty string")
    return value


def _require_vector3(value: Any, *, context: str) -> tuple[float, float, float]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        _fail(f"{context} must be a sequence of exactly three finite numbers")
    if len(value) != 3:
        _fail(f"{context} must contain exactly three numbers")
    result: list[float] = []
    for index, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            _fail(f"{context}[{index}] must be a number")
        converted = float(item)
        if not math.isfinite(converted):
            _fail(f"{context}[{index}] must be finite")
        result.append(converted)
    return (result[0], result[1], result[2])


def _ref_index(value: Any, *, context: str, indices: set[int], allow_minus_one: bool = False) -> int | None:
    if value is None:
        return None
    result = _require_index(value, context=context, allow_minus_one=allow_minus_one)
    if result == -1 and allow_minus_one:
        return result
    if result not in indices:
        _fail(f"{context} references unknown bone index {result}")
    return result


def _validate_ik_links(
    links: Any,
    *,
    indices: set[int],
    context: str,
    owner_index: int | None = None,
) -> tuple[dict[str, Any], ...]:
    if isinstance(links, (str, bytes, bytearray)) or not isinstance(links, Sequence):
        _fail(f"{context} must be a sequence")
    normalized: list[dict[str, Any]] = []
    for link_index, link in enumerate(links):
        if not isinstance(link, Mapping):
            _fail(f"{context}[{link_index}] must be a mapping")
        if "bone" not in link:
            _fail(f"{context}[{link_index}] is missing bone")
        bone_index = _ref_index(link["bone"], context=f"{context}[{link_index}].bone", indices=indices)
        if bone_index is None:
            _fail(f"{context}[{link_index}].bone must not be null")
        if owner_index is not None and bone_index == owner_index:
            _fail(f"{context}[{link_index}].bone cannot reference itself")
        normalized_link = dict(link)
        normalized_link["bone"] = bone_index
        normalized.append(normalized_link)
    return tuple(normalized)


def _validate_bone_refs(bone: MmdBoneSpec, *, indices: set[int], context: str = "bone") -> None:
    if isinstance(bone.parent_index, bool) or not isinstance(bone.parent_index, int):
        _fail(f"{context}.parent_index must be an integer")
    if bone.parent_index < -1:
        _fail(f"{context}.parent_index must be >= -1")
    if bone.parent_index != -1:
        if bone.parent_index not in indices:
            _fail(f"{context}.parent_index references unknown bone index {bone.parent_index}")
        if bone.parent_index == bone.index:
            _fail(f"{context}.parent_index cannot reference itself")
    if bone.connect_bone_index is not None:
        checked = _ref_index(
            bone.connect_bone_index,
            context=f"{context}.connect_bone_index",
            indices=indices,
            allow_minus_one=True,
        )
        if checked == bone.index:
            _fail(f"{context}.connect_bone_index cannot reference itself")
    for field in ("grant_parent_index", "ik_target_index"):
        value = getattr(bone, field)
        if value is None:
            continue
        checked = _ref_index(value, context=f"{context}.{field}", indices=indices)
        if checked == bone.index:
            _fail(f"{context}.{field} cannot reference itself")
    _validate_ik_links(
        bone.ik_links,
        indices=indices,
        context=f"{context}.ik_links",
        owner_index=bone.index,
    )


def _validate_spec(spec: MmdModelAuthoringSpec) -> None:
    indices = {bone.index for bone in spec.bones}
    if len(indices) != len(spec.bones):
        _fail("spec contains duplicate bone indices")
    bindings = [bone.binding_identity for bone in spec.bones if bone.binding_identity is not None]
    if len(bindings) != len(set(bindings)):
        _fail("spec contains duplicate bone binding identities")
    for bone in spec.bones:
        _validate_bone_refs(bone, indices=indices, context=f"bones[{bone.index}]")
    for morph in spec.morphs:
        if morph.morph_type != "bone":
            continue
        for offset_index, offset in enumerate(morph.offsets):
            if not isinstance(offset, Mapping) or "bone_index" not in offset:
                _fail(f"morphs[{morph.index}].offsets[{offset_index}] is missing bone_index")
            offset_bone = _ref_index(
                offset["bone_index"],
                context=f"morphs[{morph.index}].offsets[{offset_index}].bone_index",
                indices=indices,
            )
            if offset_bone is None:
                _fail(f"morphs[{morph.index}].offsets[{offset_index}].bone_index must not be null")


def _remap_ref(value: int | None, mapping: Mapping[int, int], *, context: str, allow_minus_one: bool = False) -> int | None:
    if value is None:
        return None
    checked = _require_index(value, context=context, allow_minus_one=allow_minus_one)
    if checked == -1 and allow_minus_one:
        return checked
    if checked not in mapping:
        _fail(f"{context} references unknown bone index {checked}")
    return mapping[checked]


def _reindex_with_mapping(spec: MmdModelAuthoringSpec, mapping: Mapping[int, int]) -> MmdModelAuthoringSpec:
    """Return a spec remapped by an already validated old-to-new index map."""
    new_bones: list[MmdBoneSpec] = []
    for bone in spec.bones:
        links = []
        for link in bone.ik_links:
            if "bone" not in link:
                _fail(f"bones[{bone.index}].ik_links entry is missing bone")
            copied = dict(link)
            copied["bone"] = _remap_ref(
                link["bone"],
                mapping,
                context=f"bones[{bone.index}].ik_links.bone",
            )
            links.append(copied)
        new_bones.append(
            replace(
                bone,
                index=mapping[bone.index],
                parent_index=_remap_ref(
                    bone.parent_index,
                    mapping,
                    context=f"bones[{bone.index}].parent_index",
                    allow_minus_one=True,
                ),
                connect_bone_index=_remap_ref(
                    bone.connect_bone_index,
                    mapping,
                    context=f"bones[{bone.index}].connect_bone_index",
                    allow_minus_one=True,
                ),
                grant_parent_index=_remap_ref(
                    bone.grant_parent_index,
                    mapping,
                    context=f"bones[{bone.index}].grant_parent_index",
                ),
                ik_target_index=_remap_ref(
                    bone.ik_target_index,
                    mapping,
                    context=f"bones[{bone.index}].ik_target_index",
                ),
                ik_links=tuple(links),
            )
        )
    new_morphs: list[MmdMorphSpec] = []
    for morph in spec.morphs:
        if morph.morph_type != "bone":
            new_morphs.append(morph)
            continue
        offsets = []
        for offset_index, offset in enumerate(morph.offsets):
            if not isinstance(offset, Mapping) or "bone_index" not in offset:
                _fail(f"morphs[{morph.index}].offsets[{offset_index}] is missing bone_index")
            copied = dict(offset)
            copied["bone_index"] = _remap_ref(
                offset["bone_index"],
                mapping,
                context=f"morphs[{morph.index}].offsets[{offset_index}].bone_index",
            )
            offsets.append(copied)
        new_morphs.append(replace(morph, offsets=tuple(offsets)))
    # Keep the exact existing material/morph objects where no rewrite was
    # needed; MmdModelAuthoringSpec canonicalizes collection order itself.
    return MmdModelAuthoringSpec(
        model=spec.model,
        bones=tuple(new_bones),
        materials=spec.materials,
        morphs=tuple(new_morphs),
        schema_version=spec.schema_version,
    )


def register_bone(spec: MmdModelAuthoringSpec, bone: MmdBoneSpec) -> MmdModelAuthoringSpec:
    """Register a bound bone at the next available explicit PMX index."""
    try:
        spec = _require_spec(spec)
        bone = _require_bone(bone)
        _validate_spec(spec)
        identity = _require_nonempty_identity(bone.binding_identity, context="bone.binding_identity")
        identities = {item.binding_identity for item in spec.bones if item.binding_identity is not None}
        if identity in identities:
            _fail(f"duplicate bone binding identity {identity!r}")
        indices = {item.index for item in spec.bones}
        next_index = max(indices, default=-1) + 1
        registered = replace(bone, index=next_index, binding_identity=identity)
        _validate_bone_refs(registered, indices=indices, context="bone")
        return MmdModelAuthoringSpec(
            model=spec.model,
            bones=spec.bones + (registered,),
            materials=spec.materials,
            morphs=spec.morphs,
            schema_version=spec.schema_version,
        )
    except BoneAuthoringError:
        raise
    except Exception as exc:
        raise BoneAuthoringError(str(exc)) from None


def replace_bone(spec: MmdModelAuthoringSpec, bone: MmdBoneSpec) -> MmdModelAuthoringSpec:
    """Replace one bone while preserving its explicit index and binding identity."""
    try:
        spec = _require_spec(spec)
        bone = _require_bone(bone)
        _validate_spec(spec)
        existing = next((item for item in spec.bones if item.index == bone.index), None)
        if existing is None:
            _fail(f"unknown bone index {bone.index}")
        if bone.binding_identity != existing.binding_identity:
            _fail("replacement bone binding identity does not match existing bone")
        indices = {item.index for item in spec.bones}
        _validate_bone_refs(bone, indices=indices, context=f"bone[{bone.index}]")
        bones = tuple(bone if item.index == bone.index else item for item in spec.bones)
        return MmdModelAuthoringSpec(
            model=spec.model,
            bones=bones,
            materials=spec.materials,
            morphs=spec.morphs,
            schema_version=spec.schema_version,
        )
    except BoneAuthoringError:
        raise
    except Exception as exc:
        raise BoneAuthoringError(str(exc)) from None


def replace_bone_semantic(spec: MmdModelAuthoringSpec, bone: MmdBoneSpec) -> MmdModelAuthoringSpec:
    """Replace editable bone semantics while preserving PMX-derived fields.

    Rest position and tail representation are scene/import semantics owned by
    Reset and persisted metadata.  Keeping them from the current spec makes a
    normal Apply fail closed against accidental UI/default values and leaves
    the ``CONNECT_BONE`` flag paired with its saved connect index.
    """
    try:
        spec = _require_spec(spec)
        bone = _require_bone(bone)
        existing = next((item for item in spec.bones if item.index == bone.index), None)
        if existing is None:
            _fail(f"unknown bone index {bone.index}")
        derived_flags = int(bone.flags) & ~int(PmxBoneFlag.CONNECT_BONE)
        preserved_flags = int(existing.flags) & int(PmxBoneFlag.CONNECT_BONE)
        preserved = replace(
            bone,
            rest_position=existing.rest_position,
            flags=derived_flags | preserved_flags,
            connect_bone_index=existing.connect_bone_index,
            tail_offset=existing.tail_offset,
        )
        return replace_bone(spec, preserved)
    except BoneAuthoringError:
        raise
    except Exception as exc:
        raise BoneAuthoringError(str(exc)) from None


def capture_rest(
    spec: MmdModelAuthoringSpec,
    index: int,
    rest_position: Sequence[float],
) -> MmdModelAuthoringSpec:
    """Capture an explicit finite rest-position vector for one bone."""
    try:
        spec = _require_spec(spec)
        _validate_spec(spec)
        target_index = _require_index(index, context="index")
        position = _require_vector3(rest_position, context="rest_position")
        if not any(item.index == target_index for item in spec.bones):
            _fail(f"unknown bone index {target_index}")
        bones = tuple(
            replace(item, rest_position=position) if item.index == target_index else item for item in spec.bones
        )
        return MmdModelAuthoringSpec(
            model=spec.model,
            bones=bones,
            materials=spec.materials,
            morphs=spec.morphs,
            schema_version=spec.schema_version,
        )
    except BoneAuthoringError:
        raise
    except Exception as exc:
        raise BoneAuthoringError(str(exc)) from None


def reindex_bones(spec: MmdModelAuthoringSpec, ordered_indices: Sequence[int]) -> MmdModelAuthoringSpec:
    """Reindex all bones contiguously and rewrite every semantic bone reference."""
    try:
        spec = _require_spec(spec)
        _validate_spec(spec)
        if isinstance(ordered_indices, (str, bytes, bytearray)) or not isinstance(ordered_indices, Sequence):
            _fail("ordered_indices must be a sequence")
        requested = tuple(_require_index(value, context="ordered_indices entry") for value in ordered_indices)
        current = tuple(item.index for item in spec.bones)
        if len(requested) != len(current) or set(requested) != set(current) or len(set(requested)) != len(requested):
            _fail("ordered_indices must be an exact permutation of current bone indices")
        mapping = {old: new for new, old in enumerate(requested)}
        return _reindex_with_mapping(spec, mapping)
    except BoneAuthoringError:
        raise
    except Exception as exc:
        raise BoneAuthoringError(str(exc)) from None


def unregister_bone(spec: MmdModelAuthoringSpec, index: int) -> MmdModelAuthoringSpec:
    """Remove an unreferenced bone and compact surviving explicit indices."""
    try:
        spec = _require_spec(spec)
        _validate_spec(spec)
        target = _require_index(index, context="index")
        if target not in {bone.index for bone in spec.bones}:
            _fail(f"unknown bone index {target}")
        for bone in spec.bones:
            if bone.index == target:
                continue
            for field in _REFERENCE_FIELDS:
                if getattr(bone, field) == target:
                    _fail(f"bone {target} is referenced by bones[{bone.index}].{field}")
            for link_index, link in enumerate(bone.ik_links):
                if link.get("bone") == target:
                    _fail(f"bone {target} is referenced by bones[{bone.index}].ik_links[{link_index}]")
        for morph in spec.morphs:
            if morph.morph_type != "bone":
                continue
            for offset_index, offset in enumerate(morph.offsets):
                if offset.get("bone_index") == target:
                    _fail(f"bone {target} is referenced by morphs[{morph.index}].offsets[{offset_index}]")
        survivors = tuple(bone.index for bone in spec.bones if bone.index != target)
        remaining = replace(
            spec,
            bones=tuple(bone for bone in spec.bones if bone.index != target),
        )
        return reindex_bones(remaining, survivors)
    except BoneAuthoringError:
        raise
    except Exception as exc:
        raise BoneAuthoringError(str(exc)) from None


def make_bone_reset_plan(
    spec: MmdModelAuthoringSpec,
    discovered_bones: Sequence[MmdBoneSpec],
    *,
    requested_order: Sequence[str] | None = None,
    blockers: Sequence[str] = (),
    warnings: Sequence[str] = (),
) -> BoneResetPlan:
    """Build a complete target spec without performing any scene writes.

    ``discovered_bones`` contains one descriptor per descendant joint.  A
    descriptor whose binding already exists in ``spec`` contributes only its
    current rest position; all other semantic fields remain authoritative in
    the persisted spec.  New descriptors are appended after the requested
    pending order and are compacted to contiguous PMX indices.
    """

    try:
        spec = _require_spec(spec)
        _validate_spec(spec)
        if isinstance(discovered_bones, (str, bytes, bytearray)) or not isinstance(discovered_bones, Sequence):
            _fail("discovered_bones must be a sequence")
        discovered = tuple(_require_bone(item) for item in discovered_bones)
        bindings = tuple(_require_nonempty_identity(item.binding_identity, context="bone.binding_identity") for item in discovered)
        if len(set(bindings)) != len(bindings):
            _fail("discovered bone bindings must be unique")
        existing_by_binding = {
            item.binding_identity: item
            for item in spec.bones
            if item.binding_identity is not None
        }
        discovered_by_binding = dict(zip(bindings, discovered))
        order = tuple(requested_order or ())
        if len(set(order)) != len(order):
            _fail("requested_order must not contain duplicate bindings")
        if any(binding not in discovered_by_binding for binding in order):
            _fail("requested_order contains a non-descendant binding")
        ordered_bindings = list(order)
        ordered_bindings.extend(binding for binding in bindings if binding not in ordered_bindings)
        target_items: list[MmdBoneSpec] = []
        updated_indices: list[int] = []
        added: list[str] = []
        removed = [binding for binding in existing_by_binding if binding not in discovered_by_binding]
        removed_indices = {existing_by_binding[binding].index for binding in removed}
        semantic_blockers: list[str] = []
        for bone in spec.bones:
            if bone.index in removed_indices:
                continue
            for field in _REFERENCE_FIELDS:
                if getattr(bone, field) in removed_indices:
                    semantic_blockers.append(
                        f"removed bone is referenced by bones[{bone.index}].{field}"
                    )
            for link in bone.ik_links:
                if link.get("bone") in removed_indices:
                    semantic_blockers.append(
                        f"removed bone is referenced by bones[{bone.index}].ik_links"
                    )
        for morph in spec.morphs:
            if morph.morph_type != "bone":
                continue
            for offset in morph.offsets:
                if isinstance(offset, Mapping) and offset.get("bone_index") in removed_indices:
                    semantic_blockers.append(f"removed bone is referenced by morphs[{morph.index}]")
        blockers = tuple([*map(str, blockers), *semantic_blockers])
        next_index = max((item.index for item in spec.bones), default=-1) + 1
        for binding in ordered_bindings:
            descriptor = discovered_by_binding[binding]
            previous = existing_by_binding.get(binding)
            if previous is None:
                # The adapter supplies conservative defaults for a new joint.
                target_items.append(replace(descriptor, index=next_index, binding_identity=binding))
                next_index += 1
                added.append(binding)
                continue
            updated = replace(previous, rest_position=descriptor.rest_position, binding_identity=binding)
            if previous.rest_position != updated.rest_position:
                updated_indices.append(previous.index)
            target_items.append(updated)
        intermediate = MmdModelAuthoringSpec(
            model=spec.model,
            bones=tuple(target_items),
            materials=spec.materials,
            morphs=spec.morphs,
            schema_version=spec.schema_version,
        )
        # Validate removal blockers and rewrite every semantic reference in one
        # pure operation.  Any reference to a removed bone fails before Maya
        # writes begin.
        requested_indices = tuple(item.index for item in target_items)
        target = reindex_bones(intermediate, requested_indices)
        _validate_spec(target)
        return BoneResetPlan(
            current_spec=spec,
            target_spec=target,
            expected_fingerprint=spec.fingerprint(),
            requested_order=tuple(ordered_bindings),
            added_bindings=tuple(added),
            removed_bindings=tuple(removed),
            rest_updated_indices=tuple(updated_indices),
            blockers=tuple(str(item) for item in blockers),
            warnings=tuple(str(item) for item in warnings),
        )
    except BoneAuthoringError as exc:
        return BoneResetPlan(
            current_spec=spec,
            target_spec=None,
            expected_fingerprint=spec.fingerprint(),
            blockers=tuple([*map(str, blockers), str(exc)]),
            warnings=tuple(map(str, warnings)),
        )
    except Exception as exc:
        return BoneResetPlan(
            current_spec=spec,
            target_spec=None,
            expected_fingerprint=spec.fingerprint(),
            blockers=tuple([*map(str, blockers), str(exc)]),
            warnings=tuple(map(str, warnings)),
        )


__all__ = [
    "BoneAuthoringError",
    "register_bone",
    "replace_bone",
    "replace_bone_semantic",
    "capture_rest",
    "reindex_bones",
    "unregister_bone",
    "BoneResetPlan",
    "make_bone_reset_plan",
]
