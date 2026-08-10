"""Immutable, Maya-independent semantic contracts for MMD authoring.

The classes in this module are the format-neutral boundary between authoring
metadata and Maya bindings.  They deliberately contain no Maya API objects;
callers can serialize a complete specification, restore it, and compare its
deterministic fingerprint before an adapter or exporter consumes it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from typing import Any, Iterator, Optional

from mmd_tools.validation.snapshot import fingerprint_payload


SCHEMA_VERSION = 1


class _ReloadCompatibleSpecMeta(type):
    """Accept only strictly equivalent dataclasses from an older reload generation."""

    def __instancecheck__(cls, instance: Any) -> bool:
        if type(instance) is cls:
            return True
        observed_type = type(instance)
        if (
            observed_type.__module__ != cls.__module__
            or observed_type.__qualname__ != cls.__qualname__
            or tuple(getattr(observed_type, "__dataclass_fields__", ()))
            != tuple(getattr(cls, "__dataclass_fields__", ()))
        ):
            return False
        serializer = getattr(instance, "to_mapping", None)
        if not callable(serializer):
            return False
        try:
            cls.from_mapping(serializer())
        except Exception:
            return False
        return True
_RUNTIME_CAPABILITIES = {"supported", "unsupported", "lossy", "experimental"}
_LOSS_POLICIES = {"none", "reject", "warn", "preserve"}
_MORPH_TYPES = {
    "vertex",
    "group",
    "bone",
    "material",
    "uv",
    "additional_uv1",
    "additional_uv2",
    "additional_uv3",
    "additional_uv4",
    "flip",
    "impulse",
}


class _FrozenDict(Mapping[str, Any]):
    """Small immutable mapping used for recursively frozen JSON values."""

    __slots__ = ("_items", "_initialized")

    def __init__(self, values: Mapping[str, Any]) -> None:
        object.__setattr__(self, "_items", tuple((key, values[key]) for key in sorted(values)))
        object.__setattr__(self, "_initialized", True)

    def __setattr__(self, key: str, value: Any) -> None:
        if getattr(self, "_initialized", False):
            raise AttributeError("_FrozenDict is immutable")
        object.__setattr__(self, key, value)

    def __getitem__(self, key: str) -> Any:
        for item_key, item_value in self._items:
            if item_key == key:
                return item_value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:
        return f"_FrozenDict({dict(self._items)!r})"

    def __hash__(self) -> int:
        return hash(self._items)


def _freeze_json(value: Any, *, path: str = "value") -> Any:
    """Copy a JSON-shaped value into immutable tuples and mappings.

    ``dict`` and ``list`` inputs are accepted as authoring input, but never
    retained in a frozen dataclass.  Unsupported Python objects, non-string
    mapping keys, and non-finite numbers are rejected.
    """
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must contain finite numbers")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} mapping keys must be strings")
            frozen[key] = _freeze_json(item, path=f"{path}.{key}")
        return _FrozenDict(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, path=f"{path}[{index}]") for index, item in enumerate(value))
    raise TypeError(f"{path} must be JSON-shaped")


def _json_value(value: Any) -> Any:
    """Convert an immutable JSON value into a fresh JSON-shaped value."""
    if isinstance(value, _FrozenDict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


def _require_mapping(value: Any, *, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{context} must be a mapping")
    return value


def _require_keys(value: Mapping[str, Any], expected: set[str], *, context: str) -> None:
    actual = set(value)
    unknown = actual - expected
    missing = expected - actual
    if unknown:
        unknown_display = sorted((repr(field) for field in unknown))
        raise ValueError(f"{context} contains unknown fields: {unknown_display!r}")
    if missing:
        raise ValueError(f"{context} is missing fields: {sorted(missing)!r}")


def _string(value: Any, *, path: str, optional: bool = False) -> Optional[str]:
    if optional and value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{path} must be a string{', or null' if optional else ''}")
    return value


def _integer(value: Any, *, path: str, minimum: Optional[int] = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{path} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{path} must be >= {minimum}")
    return value


def _number(value: Any, *, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{path} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{path} must be finite")
    return result


def _vector(value: Any, size: int, *, path: str) -> tuple[float, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise TypeError(f"{path} must be a sequence of {size} numbers")
    if len(value) != size:
        raise ValueError(f"{path} must contain exactly {size} numbers")
    return tuple(_number(item, path=f"{path}[{index}]") for index, item in enumerate(value))


def _optional_vector(value: Any, size: int, *, path: str) -> Optional[tuple[float, ...]]:
    if value is None:
        return None
    return _vector(value, size, path=path)


def _optional_index(value: Any, *, path: str, allow_parent_root: bool = False) -> Optional[int]:
    if value is None:
        return None
    minimum = -1 if allow_parent_root else 0
    return _integer(value, path=path, minimum=minimum)


@dataclass(frozen=True)
class MmdModelSpec(metaclass=_ReloadCompatibleSpecMeta):
    """PMX model-level names and comments."""

    name: str
    name_english: str = ""
    comment: str = ""
    comment_english: str = ""

    def __post_init__(self) -> None:
        for field in ("name", "name_english", "comment", "comment_english"):
            _string(getattr(self, field), path=f"model.{field}")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "name_english": self.name_english,
            "comment": self.comment,
            "comment_english": self.comment_english,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MmdModelSpec":
        mapping = _require_mapping(value, context="model")
        _require_keys(mapping, {"name", "name_english", "comment", "comment_english"}, context="model")
        return cls(**mapping)


@dataclass(frozen=True)
class MmdMaterialSpec(metaclass=_ReloadCompatibleSpecMeta):
    """Semantic PMX material data, independent of Maya shader nodes."""

    name: str
    name_english: str = ""
    index: int = 0
    diffuse: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
    specular: tuple[float, float, float] = (0.0, 0.0, 0.0)
    specular_coefficient: float = 0.0
    ambient: tuple[float, float, float] = (0.0, 0.0, 0.0)
    draw_flags: int = 0
    edge_color: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    edge_size: float = 1.0
    texture_path: Optional[str] = None
    resolved_texture_path: Optional[str] = None
    sphere_texture_path: Optional[str] = None
    resolved_sphere_texture_path: Optional[str] = None
    sphere_mode: int = 0
    shared_toon: bool = False
    toon_texture_index: Optional[int] = None
    toon_texture_path: Optional[str] = None
    resolved_toon_texture_path: Optional[str] = None
    memo: str = ""
    binding_identity: Optional[str] = None

    @property
    def pmx_index(self) -> int:
        """Return the PMX material index (compatibility spelling)."""
        return self.index

    @property
    def source_texture_path(self) -> Optional[str]:
        """Return the source-relative texture path."""
        return self.texture_path

    def __post_init__(self) -> None:
        _string(self.name, path="material.name")
        _string(self.name_english, path="material.name_english")
        _integer(self.index, path="material.index", minimum=0)
        for field, size in (("diffuse", 4), ("specular", 3), ("ambient", 3), ("edge_color", 4)):
            object.__setattr__(self, field, _vector(getattr(self, field), size, path=f"material.{field}"))
        object.__setattr__(self, "specular_coefficient", _number(self.specular_coefficient, path="material.specular_coefficient"))
        object.__setattr__(self, "edge_size", _number(self.edge_size, path="material.edge_size"))
        _integer(self.draw_flags, path="material.draw_flags", minimum=0)
        _integer(self.sphere_mode, path="material.sphere_mode", minimum=0)
        if self.sphere_mode > 3:
            raise ValueError("material.sphere_mode must be between 0 and 3")
        if not isinstance(self.shared_toon, bool):
            raise TypeError("material.shared_toon must be a boolean")
        _optional_index(self.toon_texture_index, path="material.toon_texture_index")
        for field in (
            "texture_path",
            "resolved_texture_path",
            "sphere_texture_path",
            "resolved_sphere_texture_path",
            "toon_texture_path",
            "resolved_toon_texture_path",
        ):
            _string(getattr(self, field), path=f"material.{field}", optional=True)
        _string(self.memo, path="material.memo")
        _string(self.binding_identity, path="material.binding_identity", optional=True)
        if self.binding_identity == "":
            raise ValueError("material.binding_identity must be null or a non-empty string")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "name_english": self.name_english,
            "index": self.index,
            "diffuse": list(self.diffuse),
            "specular": list(self.specular),
            "specular_coefficient": self.specular_coefficient,
            "ambient": list(self.ambient),
            "draw_flags": self.draw_flags,
            "edge_color": list(self.edge_color),
            "edge_size": self.edge_size,
            "texture_path": self.texture_path,
            "resolved_texture_path": self.resolved_texture_path,
            "sphere_texture_path": self.sphere_texture_path,
            "resolved_sphere_texture_path": self.resolved_sphere_texture_path,
            "sphere_mode": self.sphere_mode,
            "shared_toon": self.shared_toon,
            "toon_texture_index": self.toon_texture_index,
            "toon_texture_path": self.toon_texture_path,
            "resolved_toon_texture_path": self.resolved_toon_texture_path,
            "memo": self.memo,
            "binding_identity": self.binding_identity,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MmdMaterialSpec":
        mapping = _require_mapping(value, context="material")
        expected = set(cls.__dataclass_fields__)
        legacy_expected = expected - {"binding_identity"}
        if "binding_identity" in mapping:
            _require_keys(mapping, expected, context="material")
            return cls(**mapping)
        _require_keys(mapping, legacy_expected, context="material")
        normalized = dict(mapping)
        normalized["binding_identity"] = None
        return cls(**normalized)


@dataclass(frozen=True)
class MmdBoneSpec(metaclass=_ReloadCompatibleSpecMeta):
    """Semantic PMX bone definition and its optional Maya binding identity."""

    name: str
    name_english: str = ""
    index: int = 0
    parent_index: int = -1
    rest_position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    transform_layer: int = 0
    flags: int = 0
    connect_bone_index: Optional[int] = None
    tail_offset: Optional[tuple[float, float, float]] = None
    grant_parent_index: Optional[int] = None
    grant_ratio: float = 0.0
    grant_local: bool = False
    fixed_axis: Optional[tuple[float, float, float]] = None
    local_axis_x: Optional[tuple[float, float, float]] = None
    local_axis_z: Optional[tuple[float, float, float]] = None
    external_parent_key: Optional[int] = None
    ik_target_index: Optional[int] = None
    ik_loop_count: int = 0
    ik_limit_radian: Optional[float] = None
    ik_links: tuple[Mapping[str, Any], ...] = ()
    binding_identity: Optional[str] = None

    @property
    def pmx_index(self) -> int:
        """Return the PMX bone index (compatibility spelling)."""
        return self.index

    def __post_init__(self) -> None:
        _string(self.name, path="bone.name")
        _string(self.name_english, path="bone.name_english")
        _integer(self.index, path="bone.index", minimum=0)
        _integer(self.parent_index, path="bone.parent_index", minimum=-1)
        object.__setattr__(self, "rest_position", _vector(self.rest_position, 3, path="bone.rest_position"))
        _integer(self.transform_layer, path="bone.transform_layer", minimum=0)
        _integer(self.flags, path="bone.flags", minimum=0)
        _optional_index(self.connect_bone_index, path="bone.connect_bone_index", allow_parent_root=True)
        for field in ("grant_parent_index", "ik_target_index"):
            _optional_index(getattr(self, field), path=f"bone.{field}")
        object.__setattr__(self, "tail_offset", _optional_vector(self.tail_offset, 3, path="bone.tail_offset"))
        object.__setattr__(self, "grant_ratio", _number(self.grant_ratio, path="bone.grant_ratio"))
        if not isinstance(self.grant_local, bool):
            raise TypeError("bone.grant_local must be a boolean")
        for field in ("fixed_axis", "local_axis_x", "local_axis_z"):
            object.__setattr__(self, field, _optional_vector(getattr(self, field), 3, path=f"bone.{field}"))
        _optional_index(self.external_parent_key, path="bone.external_parent_key", allow_parent_root=True)
        _integer(self.ik_loop_count, path="bone.ik_loop_count", minimum=0)
        if self.ik_limit_radian is not None:
            object.__setattr__(self, "ik_limit_radian", _number(self.ik_limit_radian, path="bone.ik_limit_radian"))
        if isinstance(self.ik_links, (str, bytes, bytearray)) or not isinstance(self.ik_links, Sequence):
            raise TypeError("bone.ik_links must be a sequence")
        frozen_links = tuple(_freeze_json(link, path="bone.ik_links") for link in self.ik_links)
        if any(not isinstance(link, _FrozenDict) for link in frozen_links):
            raise TypeError("bone.ik_links entries must be mappings")
        object.__setattr__(self, "ik_links", frozen_links)
        _string(self.binding_identity, path="bone.binding_identity", optional=True)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "name_english": self.name_english,
            "index": self.index,
            "parent_index": self.parent_index,
            "rest_position": list(self.rest_position),
            "transform_layer": self.transform_layer,
            "flags": self.flags,
            "connect_bone_index": self.connect_bone_index,
            "tail_offset": list(self.tail_offset) if self.tail_offset is not None else None,
            "grant_parent_index": self.grant_parent_index,
            "grant_ratio": self.grant_ratio,
            "grant_local": self.grant_local,
            "fixed_axis": list(self.fixed_axis) if self.fixed_axis is not None else None,
            "local_axis_x": list(self.local_axis_x) if self.local_axis_x is not None else None,
            "local_axis_z": list(self.local_axis_z) if self.local_axis_z is not None else None,
            "external_parent_key": self.external_parent_key,
            "ik_target_index": self.ik_target_index,
            "ik_loop_count": self.ik_loop_count,
            "ik_limit_radian": self.ik_limit_radian,
            "ik_links": [_json_value(link) for link in self.ik_links],
            "binding_identity": self.binding_identity,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MmdBoneSpec":
        mapping = _require_mapping(value, context="bone")
        _require_keys(mapping, set(cls.__dataclass_fields__), context="bone")
        return cls(**mapping)


@dataclass(frozen=True)
class MmdMorphSpec(metaclass=_ReloadCompatibleSpecMeta):
    """PMX morph definition with immutable raw offsets and loss policy."""

    name: str
    name_english: str = ""
    index: int = 0
    panel: int = 0
    morph_type: str = "vertex"
    offsets: tuple[Mapping[str, Any], ...] = ()
    binding_identity: Optional[str] = None
    runtime_capability: str = "supported"
    loss_policy: str = "none"

    @property
    def pmx_index(self) -> int:
        """Return the PMX global morph index (compatibility spelling)."""
        return self.index

    @property
    def pmx_type(self) -> str:
        """Return the PMX morph type spelling."""
        return self.morph_type

    def __post_init__(self) -> None:
        _string(self.name, path="morph.name")
        _string(self.name_english, path="morph.name_english")
        _integer(self.index, path="morph.index", minimum=0)
        _integer(self.panel, path="morph.panel", minimum=0)
        if self.panel > 4:
            raise ValueError("morph.panel must be between 0 and 4")
        _string(self.morph_type, path="morph.morph_type")
        if self.morph_type not in _MORPH_TYPES:
            raise ValueError(f"morph.morph_type must be one of {sorted(_MORPH_TYPES)!r}")
        if isinstance(self.offsets, (str, bytes, bytearray)) or not isinstance(self.offsets, Sequence):
            raise TypeError("morph.offsets must be a sequence of mappings")
        frozen_offsets = tuple(_freeze_json(offset, path="morph.offsets") for offset in self.offsets)
        if any(not isinstance(offset, _FrozenDict) for offset in frozen_offsets):
            raise TypeError("morph.offsets entries must be mappings")
        object.__setattr__(self, "offsets", frozen_offsets)
        _string(self.binding_identity, path="morph.binding_identity", optional=True)
        _string(self.runtime_capability, path="morph.runtime_capability")
        if self.runtime_capability not in _RUNTIME_CAPABILITIES:
            raise ValueError(f"morph.runtime_capability must be one of {sorted(_RUNTIME_CAPABILITIES)!r}")
        _string(self.loss_policy, path="morph.loss_policy")
        if self.loss_policy not in _LOSS_POLICIES:
            raise ValueError(f"morph.loss_policy must be one of {sorted(_LOSS_POLICIES)!r}")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "name_english": self.name_english,
            "index": self.index,
            "panel": self.panel,
            "morph_type": self.morph_type,
            "offsets": [_json_value(offset) for offset in self.offsets],
            "binding_identity": self.binding_identity,
            "runtime_capability": self.runtime_capability,
            "loss_policy": self.loss_policy,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MmdMorphSpec":
        mapping = _require_mapping(value, context="morph")
        _require_keys(mapping, set(cls.__dataclass_fields__), context="morph")
        return cls(**mapping)


@dataclass(frozen=True)
class MmdModelAuthoringSpec(metaclass=_ReloadCompatibleSpecMeta):
    """Core immutable semantic authoring payload for one MMD model."""

    model: MmdModelSpec
    bones: tuple[MmdBoneSpec, ...] = ()
    materials: tuple[MmdMaterialSpec, ...] = ()
    morphs: tuple[MmdMorphSpec, ...] = ()
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _integer(self.schema_version, path="schema_version", minimum=0)
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {self.schema_version}")
        if not isinstance(self.model, MmdModelSpec):
            raise TypeError("model must be an MmdModelSpec")
        for field, expected_type in (
            ("bones", MmdBoneSpec),
            ("materials", MmdMaterialSpec),
            ("morphs", MmdMorphSpec),
        ):
            value = getattr(self, field)
            if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
                raise TypeError(f"{field} must be a sequence")
            frozen = tuple(value)
            if any(not isinstance(item, expected_type) for item in frozen):
                raise TypeError(f"{field} entries must be {expected_type.__name__}")
            indices = [item.index for item in frozen]
            if len(indices) != len(set(indices)):
                raise ValueError(f"{field} contains duplicate indices")
            # Explicit PMX indices define semantic order. Canonicalizing here
            # keeps direct construction, backend reads, and fingerprints
            # independent of incidental enumeration order.
            object.__setattr__(self, field, tuple(sorted(frozen, key=lambda item: item.index)))
        bone_indices = {bone.index for bone in self.bones}
        for bone in self.bones:
            for field in ("parent_index", "connect_bone_index", "grant_parent_index", "ik_target_index"):
                index = getattr(bone, field)
                if index is not None and index != -1 and index not in bone_indices:
                    raise ValueError(f"bone.{field} references unknown index {index}")
            if bone.parent_index == bone.index:
                raise ValueError("bone.parent_index cannot reference itself")

    def to_mapping(self) -> dict[str, Any]:
        """Return a fresh JSON-shaped mapping suitable for persistence."""
        return {
            "schema_version": self.schema_version,
            "model": self.model.to_mapping(),
            "bones": [bone.to_mapping() for bone in self.bones],
            "materials": [material.to_mapping() for material in self.materials],
            "morphs": [morph.to_mapping() for morph in self.morphs],
        }

    to_dict = to_mapping

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MmdModelAuthoringSpec":
        """Parse a strict mapping, rejecting schema drift and malformed data."""
        mapping = _require_mapping(value, context="authoring spec")
        _require_keys(mapping, {"schema_version", "model", "bones", "materials", "morphs"}, context="authoring spec")
        schema_version = _integer(mapping["schema_version"], path="schema_version", minimum=0)
        if schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {schema_version}")
        def _items(raw: Any, parser: Any, context: str) -> tuple[Any, ...]:
            if isinstance(raw, (str, bytes, bytearray)) or not isinstance(raw, Sequence):
                raise TypeError(f"{context} must be a sequence")
            return tuple(parser(item) for item in raw)
        return cls(
            schema_version=schema_version,
            model=MmdModelSpec.from_mapping(mapping["model"]),
            bones=_items(mapping["bones"], MmdBoneSpec.from_mapping, "bones"),
            materials=_items(mapping["materials"], MmdMaterialSpec.from_mapping, "materials"),
            morphs=_items(mapping["morphs"], MmdMorphSpec.from_mapping, "morphs"),
        )

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> "MmdModelAuthoringSpec":
        """Alias for :meth:`from_mapping` used by metadata adapters."""
        return cls.from_mapping(value)

    def fingerprint(self) -> str:
        """Return the deterministic SHA-256 fingerprint of this spec."""
        return fingerprint_payload(self.to_mapping())

    @property
    def payload_fingerprint(self) -> str:
        """Return :meth:`fingerprint` as a property for snapshot consumers."""
        return self.fingerprint()


def parse_model_authoring_spec(value: Mapping[str, Any]) -> MmdModelAuthoringSpec:
    """Parse a JSON-shaped mapping into :class:`MmdModelAuthoringSpec`."""
    return MmdModelAuthoringSpec.from_mapping(value)


__all__ = [
    "SCHEMA_VERSION",
    "MmdModelSpec",
    "MmdMaterialSpec",
    "MmdBoneSpec",
    "MmdMorphSpec",
    "MmdModelAuthoringSpec",
    "parse_model_authoring_spec",
]
