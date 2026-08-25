from __future__ import annotations

from typing import Any
from unittest.mock import Mock, call

import pytest

from mmd_tools.adapters.maya_authoring_metadata_writers import (
    BoneMetadataWriter,
    MaterialMetadataWriter,
    MetadataWriterContext,
    ModelMetadataWriter,
    MorphMetadataWriter,
)
from mmd_tools.adapters.maya_scene_metadata_backend import MayaSceneMetadataError


def _context() -> MetadataWriterContext:
    return MetadataWriterContext(
        error_factory=MayaSceneMetadataError,
        require_exact_mapping=Mock(),
        write_items=lambda metadata, _context: [dict(item) for item in metadata],
        require_same_bindings=Mock(),
        set_scalar=Mock(),
        set_string=Mock(),
        set_vector=Mock(),
        set_existing_scalar=Mock(),
        set_existing_string=Mock(),
        set_optional_scalar=Mock(),
        set_optional_string=Mock(),
        set_optional_vector=Mock(),
        delete_existing_attr=Mock(),
        write_optional_bone_reference=Mock(),
    )


def _transaction(**sections: Any) -> dict[str, Any]:
    target = {"model": {}, "bones": [], "materials": [], "morphs": []}
    target.update(sections)
    return {
        "root": "|root",
        "target": target,
        "bone_bindings": {},
        "material_bindings": {},
        "morph_bindings": {},
    }


def test_model_writer_uses_injected_helpers_and_updates_only_model_target() -> None:
    context = _context()
    transaction = _transaction()
    metadata = {
        "name": "モデル",
        "name_english": "Model",
        "comment": "コメント",
        "comment_english": "Comment",
    }

    ModelMetadataWriter(context).write(transaction, metadata)

    assert transaction["target"]["model"] == metadata
    assert context.set_string.call_args_list == [
        call("|root", "mmd_model_name", "モデル"),
        call("|root", "mmd_model_name_en", "Model"),
        call("|root", "mmd_comment", "コメント"),
        call("|root", "mmd_comment_en", "Comment"),
    ]
    assert transaction["target"]["bones"] == []
    assert transaction["target"]["materials"] == []
    assert transaction["target"]["morphs"] == []


def test_material_writer_rejects_texture_path_changes_before_attribute_writes() -> None:
    context = _context()
    original = {
        "index": 0,
        "binding_identity": "mat",
        "texture_path": "old.png",
        "resolved_texture_path": None,
        "sphere_texture_path": None,
        "resolved_sphere_texture_path": None,
        "toon_texture_path": None,
        "resolved_toon_texture_path": None,
    }
    changed = dict(original, texture_path="new.png")
    transaction = _transaction(materials=[original])
    transaction["material_bindings"] = {0: "mat"}

    with pytest.raises(MayaSceneMetadataError, match="texture path changes"):
        MaterialMetadataWriter(context).write(transaction, [changed])

    context.set_string.assert_not_called()
    assert transaction["target"]["materials"] == [original]


def test_morph_writer_rejects_immutable_fields_before_attribute_writes() -> None:
    context = _context()
    original = {
        "index": 0,
        "binding_identity": "morph",
        "morph_type": "group",
        "offsets": [{"morph_index": 0, "morph_rate": 1.0}],
        "runtime_capability": "supported",
        "loss_policy": "none",
    }
    changed = dict(original, offsets=[])
    transaction = _transaction(morphs=[original])
    transaction["morph_bindings"] = {0: "morph"}

    with pytest.raises(MayaSceneMetadataError, match="offsets changes"):
        MorphMetadataWriter(context).write(transaction, [changed])

    context.set_string.assert_not_called()
    assert transaction["target"]["morphs"] == [original]


def test_bone_writer_delegates_optional_reference_writes_and_updates_target() -> None:
    context = _context()
    item = {
        "index": 0,
        "binding_identity": "joint",
        "name": "Bone",
        "name_english": "Bone",
        "parent_index": -1,
        "rest_position": (0.0, 0.0, 0.0),
        "transform_layer": 0,
        "flags": 0,
        "connect_bone_index": None,
        "tail_offset": None,
        "grant_parent_index": None,
        "grant_ratio": 0.0,
        "fixed_axis": None,
        "local_axis_x": None,
        "local_axis_z": None,
        "external_parent_key": None,
        "ik_target_index": None,
        "ik_loop_count": 0,
        "ik_limit_radian": None,
        "ik_links": (),
    }
    transaction = _transaction(bones=[])
    transaction["bone_bindings"] = {0: "joint"}

    BoneMetadataWriter(context).write(transaction, [item])

    assert transaction["target"]["bones"] == [item]
    assert context.write_optional_bone_reference.call_count == 3
    assert context.set_string.call_args_list[:2] == [
        call("joint", "mmd_bone_name", "Bone"),
        call("joint", "mmd_bone_name_en", "Bone"),
    ]
