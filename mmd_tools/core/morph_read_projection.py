"""Immutable read projections for model-owned vertex morph bindings."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
import math
from typing import Any, Callable, ClassVar, Mapping, Optional, Tuple

from mmd_tools.core.model_authoring_spec import (
    MmdBoneSpec,
    MmdMaterialSpec,
    MmdModelAuthoringSpec,
    MmdModelSpec,
    MmdMorphSpec,
)
from mmd_tools.core.morph_binding_resolver import MorphBinding, MorphBindingWarning
from mmd_tools.core.morph_topology import (
    MorphTopologyDiagnostic,
    MorphTopologyInspection,
)


PROJECTION_SCHEMA_VERSION = 1


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

    projection_schema_version: ClassVar[int] = PROJECTION_SCHEMA_VERSION

    raw_pmx_name: str
    global_morph_index: int
    binding_identity: str
    bindings: Tuple[MorphBinding, ...]
    warnings: Tuple[MorphBindingWarning, ...]
    runtime_preview_plugs: Tuple[str, ...] = ()
    runtime_supported: bool = False
    unsupported_reason: str = ""
    semantic_registered: bool = True

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

    projection_schema_version: ClassVar[int] = PROJECTION_SCHEMA_VERSION

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


@dataclass(frozen=True)
class MorphAuthoringReadSnapshot:
    """One refresh generation of semantic and runtime morph observations."""

    projection_schema_version: ClassVar[int] = PROJECTION_SCHEMA_VERSION

    spec: Optional[MmdModelAuthoringSpec]
    projection: MorphBlendShapeReadProjection
    topology_inspection: MorphTopologyInspection


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


ProjectionNormalizer = Callable[[Any], Tuple[Any, bool]]


def _field_names(value: Any) -> Tuple[str, ...]:
    """Return dataclass fields without accepting arbitrary duck types."""

    value_type = type(value)
    if not is_dataclass(value) or isinstance(value, type):
        raise TypeError("value must be a dataclass instance")
    return tuple(field.name for field in fields(value_type))


def _reload_compatible(value: Any, expected_type: type, *, label: str) -> bool:
    """Accept only a structurally identical object from an older reload."""

    if type(value) is expected_type:
        return False
    observed_type = type(value)
    expected_fields = tuple(field.name for field in fields(expected_type))
    if (
        not is_dataclass(value)
        or isinstance(value, type)
        or observed_type.__module__ != expected_type.__module__
        or observed_type.__qualname__ != expected_type.__qualname__
        or _field_names(value) != expected_fields
        or getattr(observed_type, "projection_schema_version", None)
        != getattr(expected_type, "projection_schema_version", None)
    ):
        raise TypeError(
            "{} has an incompatible projection class: {}.{}".format(
                label,
                observed_type.__module__,
                observed_type.__qualname__,
            )
        )
    return True


def _normalize_dataclass(
    value: Any,
    expected_type: type,
    *,
    label: str,
    transforms: Optional[dict[str, ProjectionNormalizer]] = None,
) -> Tuple[Any, bool]:
    """Rebuild one compatible old-generation dataclass."""

    changed = _reload_compatible(value, expected_type, label=label)
    normalized = {}
    for field in fields(expected_type):
        field_value = getattr(value, field.name)
        transform = (transforms or {}).get(field.name)
        if transform is not None:
            field_value, field_changed = transform(field_value)
            changed = changed or field_changed
        normalized[field.name] = field_value
    if not changed:
        return value, False
    return expected_type(**normalized), True


def _optional_transform(normalizer: ProjectionNormalizer) -> ProjectionNormalizer:
    def normalize(value: Any) -> Tuple[Any, bool]:
        if value is None:
            return None, False
        return normalizer(value)

    return normalize


def _tuple_transform(
    normalizer: ProjectionNormalizer,
    *,
    label: str,
) -> ProjectionNormalizer:
    def normalize(value: Any) -> Tuple[Tuple[Any, ...], bool]:
        if not isinstance(value, tuple):
            raise TypeError("{} must be a tuple".format(label))
        changed = False
        normalized = []
        for item in value:
            item, item_changed = normalizer(item)
            normalized.append(item)
            changed = changed or item_changed
        return tuple(normalized), changed

    return normalize


def _tuple_of_strings(value: Any, *, label: str) -> Tuple[Tuple[str, ...], bool]:
    if not isinstance(value, tuple) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise TypeError("{} must be a tuple of non-empty strings".format(label))
    return value, False


def _normalize_spec(value: Any) -> Tuple[Optional[MmdModelAuthoringSpec], bool]:
    if value is None:
        return None, False
    current = type(value) is MmdModelAuthoringSpec
    if current:
        current = (
            type(value.model) is MmdModelSpec
            and all(type(item) is MmdBoneSpec for item in value.bones)
            and all(type(item) is MmdMaterialSpec for item in value.materials)
            and all(type(item) is MmdMorphSpec for item in value.morphs)
        )
    if current:
        return value, False
    observed_type = type(value)
    if (
        not is_dataclass(value)
        or isinstance(value, type)
        or observed_type.__module__ != MmdModelAuthoringSpec.__module__
        or observed_type.__qualname__ != MmdModelAuthoringSpec.__qualname__
        or _field_names(value)
        != tuple(field.name for field in fields(MmdModelAuthoringSpec))
    ):
        raise TypeError(
            "morph snapshot spec has an incompatible class: {}.{}".format(
                observed_type.__module__, observed_type.__qualname__
            )
        )
    serializer = getattr(value, "to_mapping", None)
    if not callable(serializer):
        raise TypeError("morph snapshot spec cannot be serialized")
    return MmdModelAuthoringSpec.from_mapping(serializer()), True


def _normalize_binding(value: Any) -> Tuple[MorphBinding, bool]:
    return _normalize_dataclass(value, MorphBinding, label="morph binding")


def _normalize_warning(value: Any) -> Tuple[MorphBindingWarning, bool]:
    return _normalize_dataclass(value, MorphBindingWarning, label="morph binding warning")


def _normalize_binding_projection(
    value: Any,
) -> Tuple[MorphBindingProjection, bool]:
    normalized, changed = _normalize_dataclass(
        value,
        MorphBindingProjection,
        label="morph binding projection",
        transforms={
            "bindings": _tuple_transform(
                _normalize_binding,
                label="morph binding projection bindings",
            ),
            "warnings": _tuple_transform(
                _normalize_warning,
                label="morph binding projection warnings",
            ),
            "runtime_preview_plugs": lambda item: _tuple_of_strings(
                item,
                label="morph runtime preview plugs",
            ),
        },
    )
    _validate_binding_projection(normalized)
    return normalized, changed


def _normalize_blend_shape_projection(
    value: Any,
) -> Tuple[MorphBlendShapeReadProjection, bool]:
    normalized, changed = _normalize_dataclass(
        value,
        MorphBlendShapeReadProjection,
        label="morph blendShape projection",
        transforms={
            "owned_mesh_identities": lambda item: _tuple_of_strings(
                item,
                label="morph owned mesh identities",
            ),
            "owned_blend_shape_identities": lambda item: _tuple_of_strings(
                item,
                label="morph owned blendShape identities",
            ),
            "morphs": _tuple_transform(
                _normalize_binding_projection,
                label="morph blendShape projection morphs",
            ),
            "owned_non_intermediate_mesh_identities": lambda item: _tuple_of_strings(
                item,
                label="morph owned non-intermediate mesh identities",
            ),
        },
    )
    if not isinstance(normalized.root_identity, str) or not normalized.root_identity:
        raise ValueError("morph projection root_identity must be non-empty")
    if not isinstance(normalized.controller_identity, str):
        raise TypeError("morph projection controller_identity must be a string")
    indices = tuple(item.global_morph_index for item in normalized.morphs)
    if indices != tuple(sorted(indices)) or len(indices) != len(set(indices)):
        raise ValueError("morph projection indices must be unique and ascending")
    bindings = tuple(item.binding_identity for item in normalized.morphs)
    if any(not isinstance(item, str) or not item for item in bindings):
        raise ValueError("morph projection binding identities must be non-empty")
    if len(bindings) != len(set(bindings)):
        raise ValueError("morph projection binding identities must be unique")
    return normalized, changed


def _normalize_topology_diagnostic(
    value: Any,
) -> Tuple[MorphTopologyDiagnostic, bool]:
    return _normalize_dataclass(
        value,
        MorphTopologyDiagnostic,
        label="morph topology diagnostic",
    )


def _validate_topology_mapping(value: Any, *, label: str) -> None:
    if not isinstance(value, Mapping):
        raise TypeError("{} must be a mapping".format(label))
    for key, offsets in value.items():
        if not isinstance(key, str) or not key:
            raise TypeError("{} keys must be non-empty strings".format(label))
        if not isinstance(offsets, tuple):
            raise TypeError("{} values must be tuples".format(label))
        for offset in offsets:
            if (
                not isinstance(offset, tuple)
                or len(offset) != 2
                or isinstance(offset[0], bool)
                or not isinstance(offset[0], int)
                or offset[0] < 0
                or isinstance(offset[1], bool)
                or not isinstance(offset[1], (int, float))
                or not math.isfinite(float(offset[1]))
            ):
                raise ValueError("{} contains an invalid offset".format(label))


def _normalize_topology(
    value: Any,
) -> Tuple[MorphTopologyInspection, bool]:
    normalized, changed = _normalize_dataclass(
        value,
        MorphTopologyInspection,
        label="morph topology inspection",
        transforms={
            "diagnostics": _tuple_transform(
                _normalize_topology_diagnostic,
                label="morph topology diagnostics",
            )
        },
    )
    _validate_topology_mapping(normalized.expected, label="morph topology expected")
    _validate_topology_mapping(normalized.stored, label="morph topology stored")
    for diagnostic in normalized.diagnostics:
        if not isinstance(diagnostic.code, str) or not diagnostic.code:
            raise ValueError("morph topology diagnostic code must be non-empty")
        if not isinstance(diagnostic.detail, str):
            raise TypeError("morph topology diagnostic detail must be a string")
    return normalized, changed


def _validate_binding_projection(value: MorphBindingProjection) -> None:
    # PMX labels may be empty; index and binding identity identify the morph.
    if (
        not isinstance(value.raw_pmx_name, str)
        or isinstance(value.global_morph_index, bool)
        or not isinstance(value.global_morph_index, int)
        or value.global_morph_index < 0
        or not isinstance(value.binding_identity, str)
        or not value.binding_identity
        or type(value.runtime_supported) is not bool
        or not isinstance(value.unsupported_reason, str)
        or type(value.semantic_registered) is not bool
    ):
        raise ValueError("morph binding projection contains invalid identity fields")
    for binding in value.bindings:
        if (
            binding.raw_pmx_name != value.raw_pmx_name
            or binding.global_morph_index != value.global_morph_index
            or not isinstance(binding.blend_shape_identity, str)
            or not binding.blend_shape_identity
            or not isinstance(binding.alias, str)
            or not binding.alias
            or isinstance(binding.logical_target_index, bool)
            or not isinstance(binding.logical_target_index, int)
            or binding.logical_target_index < 0
            or not isinstance(binding.weight_plug, str)
            or not binding.weight_plug
            or not isinstance(binding.controller_identity, str)
            or isinstance(binding.controller_slot, bool)
            or not isinstance(binding.controller_slot, int)
            or binding.controller_slot < 0
        ):
            raise ValueError("morph binding does not match its projection")


def _validate_snapshot(value: MorphAuthoringReadSnapshot) -> None:
    projection = value.projection
    if value.spec is None:
        if any(item.semantic_registered for item in projection.morphs):
            raise ValueError("runtime-only morph snapshot contains semantic entries")
        return
    if any(not item.semantic_registered for item in projection.morphs):
        raise ValueError("semantic morph snapshot contains runtime-only entries")
    expected = tuple(
        (item.index, item.binding_identity, item.name) for item in value.spec.morphs
    )
    actual = tuple(
        (item.global_morph_index, item.binding_identity, item.raw_pmx_name)
        for item in projection.morphs
    )
    if expected != actual:
        raise ValueError("morph snapshot semantic identity mismatch")


def normalize_morph_authoring_snapshot(
    value: Any,
) -> Tuple[MorphAuthoringReadSnapshot, bool]:
    """Return a strict current-generation Morph snapshot.

    Maya may retain a snapshot graph across an in-process module reload. Only
    a dataclass graph with the same module, qualified names, fields, and
    projection schema is rehydrated; unrelated duck types fail closed.
    """

    normalized, changed = _normalize_dataclass(
        value,
        MorphAuthoringReadSnapshot,
        label="morph authoring snapshot",
        transforms={
            "spec": _normalize_spec,
            "projection": _normalize_blend_shape_projection,
            "topology_inspection": _normalize_topology,
        },
    )
    if not isinstance(normalized.projection, MorphBlendShapeReadProjection):
        raise TypeError("morph snapshot projection is invalid")
    _validate_snapshot(normalized)
    return normalized, changed


__all__ = [
    "MorphBindingProjection",
    "MorphBlendShapeReadProjection",
    "MorphAuthoringReadSnapshot",
    "MorphProjectionRequest",
    "PROJECTION_SCHEMA_VERSION",
    "normalize_morph_authoring_snapshot",
    "project_runtime_capabilities",
]
