"""Versioned, Maya-independent product model templates.

Templates provide a small, deterministic starting point for model authoring.
The packaged JSON is parsed through the same strict semantic contract used by
the authoring adapters; callers cannot select an arbitrary filesystem path.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
import copy
import importlib.resources
import json
import math
from typing import Any

from .model_authoring_spec import MmdModelAuthoringSpec


TEMPLATE_SCHEMA_VERSION = 1
_TEMPLATE_RESOURCE_PATHS = {
    "pmx20-semistandard-v1": "config/model_templates/pmx20_semistandard_v1.json",
    "pmx20-basic-v1": "config/model_templates/pmx20_basic_v1.json",
}
_FRAME_KEYS = {"name", "name_english", "special", "elements"}
_ELEMENT_KEYS = {"type", "index"}
_ELEMENT_TYPES = {"bone", "morph"}


class ModelTemplateError(ValueError):
    """Raised when a packaged model template is unknown or malformed."""


def _freeze_json(value: Any, *, path: str = "value") -> Any:
    """Recursively copy JSON-shaped data into immutable containers."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ModelTemplateError(f"{path} must contain finite numbers")
        return value
    if isinstance(value, Mapping):
        frozen = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ModelTemplateError(f"{path} mapping keys must be strings")
            frozen[key] = _freeze_json(item, path=f"{path}.{key}")
        # MappingProxyType is deliberately local to this module: the public
        # template payload never exposes the authoring spec's private helper.
        from types import MappingProxyType

        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, path=f"{path}[{index}]") for index, item in enumerate(value))
    raise ModelTemplateError(f"{path} must be JSON-shaped")


def _require_mapping(value: Any, *, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ModelTemplateError(f"{path} must be an object")
    return value


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], *, path: str) -> None:
    actual = set(value)
    unknown = actual - expected
    missing = expected - actual
    if unknown:
        raise ModelTemplateError(f"{path} contains unknown fields: {sorted(unknown)!r}")
    if missing:
        raise ModelTemplateError(f"{path} is missing fields: {sorted(missing)!r}")


def _string(value: Any, *, path: str) -> str:
    if not isinstance(value, str):
        raise ModelTemplateError(f"{path} must be a string")
    return value


def _integer(value: Any, *, path: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ModelTemplateError(f"{path} must be an integer")
    if value < minimum:
        raise ModelTemplateError(f"{path} must be >= {minimum}")
    return value


def _normalize_display_frames(raw_frames: Any, spec: MmdModelAuthoringSpec) -> tuple[Mapping[str, Any], ...]:
    if isinstance(raw_frames, (str, bytes, bytearray)) or not isinstance(raw_frames, Sequence):
        raise ModelTemplateError("display_frames must be an array")
    bone_indices = {bone.index for bone in spec.bones}
    morph_indices = {morph.index for morph in spec.morphs}
    normalized: list[dict[str, Any]] = []
    for frame_index, raw_frame in enumerate(raw_frames):
        frame = _require_mapping(raw_frame, path=f"display_frames[{frame_index}]")
        _require_exact_keys(frame, _FRAME_KEYS, path=f"display_frames[{frame_index}]")
        name = _string(frame["name"], path=f"display_frames[{frame_index}].name")
        name_english = _string(frame["name_english"], path=f"display_frames[{frame_index}].name_english")
        special = frame["special"]
        if not isinstance(special, bool):
            raise ModelTemplateError(f"display_frames[{frame_index}].special must be a boolean")
        raw_elements = frame["elements"]
        if isinstance(raw_elements, (str, bytes, bytearray)) or not isinstance(raw_elements, Sequence):
            raise ModelTemplateError(f"display_frames[{frame_index}].elements must be an array")
        elements: list[dict[str, Any]] = []
        for element_index, raw_element in enumerate(raw_elements):
            element = _require_mapping(
                raw_element,
                path=f"display_frames[{frame_index}].elements[{element_index}]",
            )
            _require_exact_keys(
                element,
                _ELEMENT_KEYS,
                path=f"display_frames[{frame_index}].elements[{element_index}]",
            )
            element_type = _string(
                element["type"],
                path=f"display_frames[{frame_index}].elements[{element_index}].type",
            )
            if element_type not in _ELEMENT_TYPES:
                raise ModelTemplateError(
                    f"display_frames[{frame_index}].elements[{element_index}].type must be 'bone' or 'morph'"
                )
            element_index_value = _integer(
                element["index"],
                path=f"display_frames[{frame_index}].elements[{element_index}].index",
            )
            valid_indices = bone_indices if element_type == "bone" else morph_indices
            if element_index_value not in valid_indices:
                raise ModelTemplateError(
                    f"display_frames[{frame_index}].elements[{element_index}] references unknown {element_type} "
                    f"index {element_index_value}"
                )
            elements.append({"type": element_type, "index": element_index_value})
        normalized.append({"name": name, "name_english": name_english, "special": special, "elements": elements})
    return tuple(_freeze_json(frame, path=f"display_frames[{index}]") for index, frame in enumerate(normalized))


@dataclass(frozen=True)
class MmdModelTemplate:
    """Immutable versioned model template and its display-frame metadata."""

    template_id: str
    label: str
    spec: MmdModelAuthoringSpec
    display_frames: tuple[Mapping[str, Any], ...]

    def __post_init__(self) -> None:
        template_id = _string(self.template_id, path="template_id")
        label = _string(self.label, path="label")
        if not isinstance(self.spec, MmdModelAuthoringSpec):
            raise ModelTemplateError("spec must be an MmdModelAuthoringSpec")
        frames = _normalize_display_frames(self.display_frames, self.spec)
        object.__setattr__(self, "template_id", template_id)
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "display_frames", frames)

    def to_mapping(self) -> dict[str, Any]:
        """Return a fresh mutable JSON-shaped template payload."""
        return {
            "template_schema_version": TEMPLATE_SCHEMA_VERSION,
            "template_id": self.template_id,
            "label": self.label,
            "authoring_spec": self.spec.to_mapping(),
            "display_frames": [
                {
                    "name": frame["name"],
                    "name_english": frame["name_english"],
                    "special": frame["special"],
                    "elements": [dict(element) for element in frame["elements"]],
                }
                for frame in self.display_frames
            ],
        }


@dataclass(frozen=True)
class ModelTemplateOption:
    """Curated selector entry exposed to UI composition code."""

    template_id: str
    label: str


def list_model_templates() -> tuple[ModelTemplateOption, ...]:
    """Return the immutable list of packaged template identifiers and labels."""
    return tuple(
        ModelTemplateOption(template_id=template_id, label=load_model_template(template_id).label)
        for template_id in _TEMPLATE_RESOURCE_PATHS
    )


def _parse_model_template_mapping(value: Mapping[str, Any]) -> MmdModelTemplate:
    """Parse a mapping for tests and resource loading; no filesystem path is accepted."""
    mapping = _require_mapping(value, path="model template")
    _require_exact_keys(
        mapping,
        {"template_schema_version", "template_id", "label", "authoring_spec", "display_frames"},
        path="model template",
    )
    schema_version = _integer(mapping["template_schema_version"], path="template_schema_version")
    if schema_version != TEMPLATE_SCHEMA_VERSION:
        raise ModelTemplateError(f"unsupported template_schema_version: {schema_version}")
    template_id = _string(mapping["template_id"], path="template_id")
    if template_id not in _TEMPLATE_RESOURCE_PATHS:
        raise ModelTemplateError(f"unknown model template: {template_id!r}")
    label = _string(mapping["label"], path="label")
    spec_mapping = _require_mapping(mapping["authoring_spec"], path="authoring_spec")
    try:
        spec = MmdModelAuthoringSpec.from_mapping(spec_mapping)
    except (TypeError, ValueError) as exc:
        raise ModelTemplateError(f"invalid authoring_spec: {exc}") from exc
    return MmdModelTemplate(
        template_id=template_id,
        label=label,
        spec=spec,
        display_frames=mapping["display_frames"],
    )


def parse_model_template_mapping(value: Mapping[str, Any]) -> MmdModelTemplate:
    """Parse a JSON-shaped template mapping without accessing the filesystem."""
    return _parse_model_template_mapping(copy.deepcopy(value))


def load_model_template(template_id: str) -> MmdModelTemplate:
    """Load a curated, packaged template by identifier."""
    if not isinstance(template_id, str):
        raise ModelTemplateError("template_id must be a string")
    resource_path = _TEMPLATE_RESOURCE_PATHS.get(template_id)
    if resource_path is None:
        raise ModelTemplateError(f"unknown model template: {template_id!r}")
    try:
        resource = importlib.resources.files("mmd_tools").joinpath(resource_path)
        with resource.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        raise ModelTemplateError(f"could not load model template {template_id!r}: {exc}") from exc
    return _parse_model_template_mapping(payload)


def instantiate_model_template(
    template_id: str,
    model_name: str,
    model_name_english: str = "",
) -> MmdModelTemplate:
    """Create a fresh template instance with only model names overridden."""
    model_name = _string(model_name, path="model_name")
    model_name_english = _string(model_name_english, path="model_name_english")
    template = load_model_template(template_id)
    model = replace(template.spec.model, name=model_name, name_english=model_name_english)
    spec = replace(template.spec, model=model)
    # Reparse a fresh mapping so callers never share mutable payloads from a
    # previous load or instance, even though the public result is immutable.
    return _parse_model_template_mapping(
        {
            "template_schema_version": TEMPLATE_SCHEMA_VERSION,
            "template_id": template.template_id,
            "label": template.label,
            "authoring_spec": spec.to_mapping(),
            "display_frames": [
                {
                    "name": frame["name"],
                    "name_english": frame["name_english"],
                    "special": frame["special"],
                    "elements": [dict(element) for element in frame["elements"]],
                }
                for frame in template.display_frames
            ],
        }
    )


__all__ = [
    "TEMPLATE_SCHEMA_VERSION",
    "ModelTemplateError",
    "MmdModelTemplate",
    "ModelTemplateOption",
    "list_model_templates",
    "parse_model_template_mapping",
    "load_model_template",
    "instantiate_model_template",
]
