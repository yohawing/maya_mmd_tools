"""Narrow aggregate writers for the Maya authoring metadata transaction.

The backend owns transaction lifetime, binding fingerprints, and undo state.
These writers only validate one aggregate payload, write its Maya attributes,
and update the corresponding section of the transaction target.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
import json
from typing import Any

from mmd_tools.core.constants import (
    ATTR_MMD_AMBIENT_COLOR,
    ATTR_MMD_AXIS_DIRECTION,
    ATTR_MMD_BONE_FLAGS,
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_BONE_NAME_EN,
    ATTR_MMD_BONE_OFFSET,
    ATTR_MMD_BONE_PARENT_INDEX,
    ATTR_MMD_CONNECT_BONE_INDEX,
    ATTR_MMD_CONNECTION_BONE,
    ATTR_MMD_CONNECT_INDEX,
    ATTR_MMD_DEFORM_LAYER,
    ATTR_MMD_DIFFUSE_COLOR,
    ATTR_MMD_EDGE_COLOR,
    ATTR_MMD_EDGE_SIZE,
    ATTR_MMD_EXTERNAL_PARENT_KEY,
    ATTR_MMD_FIXED_AXIS,
    ATTR_MMD_GRANT_PARENT,
    ATTR_MMD_GRANT_PARENT_INDEX,
    ATTR_MMD_GRANT_RATE,
    ATTR_MMD_IK_LIMIT_ANGLE,
    ATTR_MMD_IK_LINKS,
    ATTR_MMD_IK_LOOP,
    ATTR_MMD_IK_TARGET,
    ATTR_MMD_IK_TARGET_INDEX,
    ATTR_MMD_LOCAL_X_AXIS,
    ATTR_MMD_LOCAL_Z_AXIS,
    ATTR_MMD_MATERIAL_NAME,
    ATTR_MMD_MATERIAL_NAME_EN,
    ATTR_MMD_MEMO,
    ATTR_MMD_PMX_REST_POSITION,
    ATTR_MMD_SHARED_TOON_FLAG,
    ATTR_MMD_SHININESS,
    ATTR_MMD_SPECULAR_COLOR,
    ATTR_MMD_SPHERE_MODE,
    ATTR_MMD_TOON_TEXTURE_INDEX,
    ATTR_MMD_X_AXIS_DIRECTION,
    ATTR_MMD_Z_AXIS_DIRECTION,
    ATTR_MMD_DRAW_FLAGS,
    ATTR_MMD_COMMENT,
    ATTR_MMD_COMMENT_EN,
    ATTR_MMD_MODEL_NAME,
    ATTR_MMD_MODEL_NAME_EN,
)


ErrorFactory = Callable[[str], Exception]
RequireExactMapping = Callable[[Any, set[str], str], None]
WriteItems = Callable[[Iterable[Mapping[str, Any]], str], list[dict[str, Any]]]
RequireSameBindings = Callable[[Sequence[Mapping[str, Any]], Mapping[int, Any], str], None]
SetScalar = Callable[[str, str, Any], None]
SetString = Callable[[str, str, Any], None]
SetVector = Callable[[str, str, Any], None]
SetExistingScalar = Callable[[str, str, Any], None]
SetExistingString = Callable[[str, str, str], None]
SetOptionalScalar = Callable[[str, str, Any, str], None]
SetOptionalString = Callable[[str, str, str], None]
SetOptionalVector = Callable[[str, str, Sequence[Any]], None]
DeleteExistingAttr = Callable[[str, str], None]
WriteOptionalBoneReference = Callable[
    [str, int | None, tuple[str, ...], str, Mapping[int, Mapping[str, Any]]], None
]


@dataclass(frozen=True)
class MetadataWriterContext:
    """Backend helper callbacks used by the aggregate writers."""

    error_factory: ErrorFactory
    require_exact_mapping: RequireExactMapping
    write_items: WriteItems
    require_same_bindings: RequireSameBindings
    set_scalar: SetScalar
    set_string: SetString
    set_vector: SetVector
    set_existing_scalar: SetExistingScalar
    set_existing_string: SetExistingString
    set_optional_scalar: SetOptionalScalar
    set_optional_string: SetOptionalString
    set_optional_vector: SetOptionalVector
    delete_existing_attr: DeleteExistingAttr
    write_optional_bone_reference: WriteOptionalBoneReference
    diffuse_alpha_attribute: str = "mmd_diffuse_alpha"
    edge_alpha_attribute: str = "mmd_edge_alpha"


Transaction = MutableMapping[str, Any]


class ModelMetadataWriter:
    """Write the model-header aggregate inside an existing transaction."""

    def __init__(self, context: MetadataWriterContext) -> None:
        self._context = context

    def write(self, transaction: Transaction, metadata: Mapping[str, Any]) -> None:
        expected = {"name", "name_english", "comment", "comment_english"}
        self._context.require_exact_mapping(metadata, expected, "model metadata")
        attrs = {
            "name": ATTR_MMD_MODEL_NAME,
            "name_english": ATTR_MMD_MODEL_NAME_EN,
            "comment": ATTR_MMD_COMMENT,
            "comment_english": ATTR_MMD_COMMENT_EN,
        }
        for field, attr in attrs.items():
            self._context.set_string(transaction["root"], attr, metadata[field])
        transaction["target"]["model"] = dict(metadata)


class BoneMetadataWriter:
    """Write the PMX bone aggregate inside an existing transaction."""

    def __init__(self, context: MetadataWriterContext) -> None:
        self._context = context

    def write(self, transaction: Transaction, metadata: Iterable[Mapping[str, Any]]) -> None:
        context = self._context
        items = context.write_items(metadata, "bone")
        context.require_same_bindings(items, transaction["bone_bindings"], "bone")
        target_by_index = {item["index"]: item for item in items}
        for item in items:
            node = item["binding_identity"]
            context.set_string(node, ATTR_MMD_BONE_NAME, item["name"])
            context.set_string(node, ATTR_MMD_BONE_NAME_EN, item["name_english"])
            context.set_scalar(node, ATTR_MMD_BONE_PARENT_INDEX, item["parent_index"])
            context.set_vector(node, ATTR_MMD_PMX_REST_POSITION, item["rest_position"])
            context.set_scalar(node, ATTR_MMD_DEFORM_LAYER, item["transform_layer"])
            context.set_scalar(node, ATTR_MMD_BONE_FLAGS, item["flags"])
            context.write_optional_bone_reference(
                node,
                item["connect_bone_index"],
                (ATTR_MMD_CONNECT_INDEX, ATTR_MMD_CONNECT_BONE_INDEX),
                ATTR_MMD_CONNECTION_BONE,
                target_by_index,
            )
            if item["connect_bone_index"] is not None:
                context.set_optional_vector(node, ATTR_MMD_BONE_OFFSET, (0.0, -1.0, 0.0))
            elif item["tail_offset"] is not None:
                context.set_optional_vector(node, ATTR_MMD_BONE_OFFSET, item["tail_offset"])
            context.write_optional_bone_reference(
                node,
                item["grant_parent_index"],
                (ATTR_MMD_GRANT_PARENT_INDEX,),
                ATTR_MMD_GRANT_PARENT,
                target_by_index,
            )
            if item["grant_parent_index"] is not None:
                context.set_optional_scalar(node, ATTR_MMD_GRANT_RATE, item["grant_ratio"], "double")
            else:
                context.delete_existing_attr(node, ATTR_MMD_GRANT_PARENT_INDEX)
                context.set_existing_string(node, ATTR_MMD_GRANT_PARENT, "")
                context.set_existing_scalar(node, ATTR_MMD_GRANT_RATE, 1.0)
            for attr, value in (
                (ATTR_MMD_FIXED_AXIS, item["fixed_axis"]),
                (ATTR_MMD_AXIS_DIRECTION, item["fixed_axis"]),
                (ATTR_MMD_LOCAL_X_AXIS, item["local_axis_x"]),
                (ATTR_MMD_X_AXIS_DIRECTION, item["local_axis_x"]),
                (ATTR_MMD_LOCAL_Z_AXIS, item["local_axis_z"]),
                (ATTR_MMD_Z_AXIS_DIRECTION, item["local_axis_z"]),
            ):
                if value is not None:
                    context.set_optional_vector(node, attr, value)
            if item["fixed_axis"] is None:
                context.set_optional_vector(node, ATTR_MMD_FIXED_AXIS, (0.0, 0.0, 1.0))
                context.delete_existing_attr(node, ATTR_MMD_AXIS_DIRECTION)
            if item["local_axis_x"] is None and item["local_axis_z"] is None:
                context.set_optional_vector(node, ATTR_MMD_LOCAL_X_AXIS, (1.0, 0.0, 0.0))
                context.set_optional_vector(node, ATTR_MMD_LOCAL_Z_AXIS, (0.0, 0.0, 1.0))
                context.delete_existing_attr(node, ATTR_MMD_X_AXIS_DIRECTION)
                context.delete_existing_attr(node, ATTR_MMD_Z_AXIS_DIRECTION)
            if item["external_parent_key"] is not None:
                context.set_optional_scalar(
                    node,
                    ATTR_MMD_EXTERNAL_PARENT_KEY,
                    item["external_parent_key"],
                    "long",
                )
            else:
                context.set_existing_scalar(node, ATTR_MMD_EXTERNAL_PARENT_KEY, -1)
            context.write_optional_bone_reference(
                node,
                item["ik_target_index"],
                (ATTR_MMD_IK_TARGET_INDEX,),
                ATTR_MMD_IK_TARGET,
                target_by_index,
            )
            if item["ik_target_index"] is not None:
                context.set_optional_scalar(node, ATTR_MMD_IK_LOOP, item["ik_loop_count"], "long")
            if item["ik_target_index"] is not None and item["ik_limit_radian"] is not None:
                context.set_optional_scalar(
                    node,
                    ATTR_MMD_IK_LIMIT_ANGLE,
                    item["ik_limit_radian"],
                    "double",
                )
            if item["ik_target_index"] is not None:
                context.set_optional_string(
                    node,
                    ATTR_MMD_IK_LINKS,
                    json.dumps(item["ik_links"], ensure_ascii=False, separators=(",", ":")),
                )
            else:
                context.delete_existing_attr(node, ATTR_MMD_IK_TARGET_INDEX)
                context.set_existing_string(node, ATTR_MMD_IK_TARGET, "")
                context.set_existing_scalar(node, ATTR_MMD_IK_LOOP, 10)
                context.set_existing_scalar(node, ATTR_MMD_IK_LIMIT_ANGLE, 2.0)
                context.set_existing_string(node, ATTR_MMD_IK_LINKS, "[]")
        transaction["target"]["bones"] = items


class MaterialMetadataWriter:
    """Write the material value aggregate inside an existing transaction."""

    def __init__(self, context: MetadataWriterContext) -> None:
        self._context = context

    def write(self, transaction: Transaction, metadata: Iterable[Mapping[str, Any]]) -> None:
        context = self._context
        items = context.write_items(metadata, "material")
        context.require_same_bindings(items, transaction["material_bindings"], "material")
        original_by_index = {item["index"]: item for item in transaction["target"]["materials"]}
        path_fields = {
            "texture_path",
            "resolved_texture_path",
            "sphere_texture_path",
            "resolved_sphere_texture_path",
            "toon_texture_path",
            "resolved_toon_texture_path",
        }
        for item in items:
            original = original_by_index[item["index"]]
            changed_paths = sorted(field for field in path_fields if item[field] != original[field])
            if changed_paths:
                raise context.error_factory(
                    f"material {item['index']} texture path changes require a binding transaction: {changed_paths!r}"
                )
        for item in items:
            node = item["binding_identity"]
            context.set_string(node, ATTR_MMD_MATERIAL_NAME, item["name"])
            context.set_string(node, ATTR_MMD_MATERIAL_NAME_EN, item["name_english"])
            context.set_vector(node, ATTR_MMD_DIFFUSE_COLOR, item["diffuse"][:3])
            context.set_scalar(node, context.diffuse_alpha_attribute, item["diffuse"][3])
            context.set_vector(node, ATTR_MMD_SPECULAR_COLOR, item["specular"])
            context.set_scalar(node, ATTR_MMD_SHININESS, item["specular_coefficient"])
            context.set_vector(node, ATTR_MMD_AMBIENT_COLOR, item["ambient"])
            context.set_scalar(node, ATTR_MMD_DRAW_FLAGS, item["draw_flags"])
            context.set_vector(node, ATTR_MMD_EDGE_COLOR, item["edge_color"][:3])
            context.set_scalar(node, context.edge_alpha_attribute, item["edge_color"][3])
            context.set_scalar(node, ATTR_MMD_EDGE_SIZE, item["edge_size"])
            context.set_scalar(node, ATTR_MMD_SPHERE_MODE, item["sphere_mode"])
            context.set_scalar(node, ATTR_MMD_SHARED_TOON_FLAG, int(item["shared_toon"]))
            toon_index = -1 if item["toon_texture_index"] is None else item["toon_texture_index"]
            context.set_scalar(node, ATTR_MMD_TOON_TEXTURE_INDEX, toon_index)
            context.set_string(node, ATTR_MMD_MEMO, item["memo"])
        transaction["target"]["materials"] = items


class MorphMetadataWriter:
    """Write the morph value aggregate inside an existing transaction."""

    def __init__(self, context: MetadataWriterContext) -> None:
        self._context = context

    def write(self, transaction: Transaction, metadata: Iterable[Mapping[str, Any]]) -> None:
        context = self._context
        items = context.write_items(metadata, "morph")
        context.require_same_bindings(items, transaction["morph_bindings"], "morph")
        original_by_index = {item["index"]: item for item in transaction["target"]["morphs"]}
        for item in items:
            original = original_by_index[item["index"]]
            for field in ("morph_type", "offsets", "runtime_capability", "loss_policy"):
                if item[field] != original[field]:
                    raise context.error_factory(
                        f"morph {item['index']} {field} changes require a binding transaction"
                    )
        for item in items:
            node = item["binding_identity"]
            context.set_string(node, "mmd_morph_name", item["name"])
            context.set_string(node, "mmd_morph_name_en", item["name_english"])
            context.set_scalar(node, "mmd_morph_panel", item["panel"])
        transaction["target"]["morphs"] = items

__all__ = [
    "BoneMetadataWriter",
    "MaterialMetadataWriter",
    "MetadataWriterContext",
    "ModelMetadataWriter",
    "MorphMetadataWriter",
]
