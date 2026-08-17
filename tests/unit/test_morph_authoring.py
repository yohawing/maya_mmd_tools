"""Unit tests for pure semantic PMX morph authoring operations."""

from __future__ import annotations

import copy
import math
from dataclasses import replace

import pytest

from mmd_tools.core.model_authoring_spec import (
    MmdBoneSpec,
    MmdMaterialSpec,
    MmdModelAuthoringSpec,
    MmdModelSpec,
    MmdMorphSpec,
)
from mmd_tools.core.morph_authoring import (
    MorphAuthoringError,
    create_morph,
    delete_morph,
    move_morph,
    reindex_morphs,
    replace_morph,
    replace_morph_offsets,
)


def _spec(*morphs: MmdMorphSpec) -> MmdModelAuthoringSpec:
    return MmdModelAuthoringSpec(
        model=MmdModelSpec("モデル", "Model", "コメント", "Comment"),
        bones=(MmdBoneSpec("bone", index=0),),
        materials=(MmdMaterialSpec("材質", index=0),),
        morphs=morphs,
    )


def _morph(index: int, morph_type: str = "vertex", offsets=(), **kwargs) -> MmdMorphSpec:
    name = kwargs.pop("name", f"m{index}")
    return MmdMorphSpec(name=name, index=index, morph_type=morph_type, offsets=offsets, **kwargs)


def test_create_allocates_next_index_and_ignores_supplied_index() -> None:
    original = _spec(_morph(2))
    supplied = _morph(99, name="日本語モーフ")
    created = create_morph(original, supplied)
    assert [m.index for m in created.morphs] == [2, 3]
    assert created.morphs[-1].name == "日本語モーフ"
    assert created.morphs[-1].panel == supplied.panel
    assert created.morphs[-1].runtime_capability == "supported"
    empty = create_morph(_spec())
    assert empty.morphs[0].index == 0
    assert empty.morphs[0].panel == 4
    assert empty.morphs[0].morph_type == "vertex"
    assert empty.morphs[0].offsets == ()


@pytest.mark.parametrize(
    ("morph_type", "offset"),
    [
        ("vertex", {"vertex_index": 0, "position_offset": [1, 2, 3]}),
        ("bone", {"bone_index": 0, "translation": [1, 2, 3], "rotation": [0, 0, 0, 1]}),
        ("group", {"morph_index": 0, "morph_rate": 0.5}),
        (
            "material",
            {
                "material_index": -1,
                "operation_type": 1,
                "diffuse": [1, 2, 3, 4],
                "specular": [1, 2, 3],
                "specular_coefficient": 2,
                "ambient": [1, 2, 3],
                "edge_color": [1, 2, 3, 4],
                "edge_size": 2,
                "texture_factor": [1, 2, 3, 4],
                "sphere_texture_factor": [1, 2, 3, 4],
                "toon_texture_factor": [1, 2, 3, 4],
            },
        ),
        ("uv", {"vertex_index": 0, "uv_offset": [1, 2, 3, 4]}),
        ("additional_uv1", {"vertex_index": 0, "uv_offset": [1, 2, 3, 4]}),
        ("additional_uv2", {"vertex_index": 0, "uv_offset": [1, 2, 3, 4]}),
        ("additional_uv3", {"vertex_index": 0, "uv_offset": [1, 2, 3, 4]}),
        ("additional_uv4", {"vertex_index": 0, "uv_offset": [1, 2, 3, 4]}),
    ],
)
def test_replace_offsets_accepts_each_supported_schema(morph_type, offset) -> None:
    target = _morph(0, morph_type)
    result = replace_morph_offsets(_spec(target), 0, [offset])
    canonical = result.morphs[0].offsets[0]
    assert set(canonical) == set(offset)
    assert all(isinstance(value, tuple) for value in canonical.values() if isinstance(value, tuple))


def test_group_reference_and_unsupported_policy_are_enforced() -> None:
    group = _morph(1, "group", ({"morph_index": 0, "morph_rate": 1},))
    result = replace_morph_offsets(_spec(_morph(0), group), 1, [{"morph_index": 0, "morph_rate": 0.25}])
    assert result.morphs[1].offsets[0]["morph_rate"] == 0.25
    flip = _morph(1, "flip", ({"morph_index": 0, "flip_rate": 1},), runtime_capability="unsupported", loss_policy="reject")
    assert replace_morph_offsets(_spec(_morph(0), flip), 1, flip.offsets).morphs[1].morph_type == "flip"
    with pytest.raises(MorphAuthoringError, match="require runtime_capability"):
        replace_morph(_spec(_morph(0), flip), replace(flip, runtime_capability="supported"))


def test_impulse_schema_is_kept_but_requires_reject_policy() -> None:
    impulse = _morph(
        0,
        "impulse",
        ({"rigid_body_index": 12, "impulse": [0, 1, 2], "torque": [3, 4, 5]},),
        runtime_capability="unsupported",
        loss_policy="reject",
    )
    result = replace_morph_offsets(_spec(impulse), 0, impulse.offsets)
    assert result.morphs[0].offsets[0]["impulse"] == (0.0, 1.0, 2.0)
    with pytest.raises(MorphAuthoringError, match="require runtime_capability"):
        replace_morph(_spec(impulse), replace(impulse, runtime_capability="supported"))


def test_flip_and_impulse_use_raw_pmx_field_names_strictly() -> None:
    flip = _morph(0, "flip", runtime_capability="unsupported", loss_policy="reject")
    with pytest.raises(MorphAuthoringError, match="unknown fields"):
        replace_morph_offsets(_spec(flip), 0, [{"morph_index": 0, "morph_rate": 1}])
    impulse = _morph(0, "impulse", runtime_capability="unsupported", loss_policy="reject")
    with pytest.raises(MorphAuthoringError, match="unknown fields"):
        replace_morph_offsets(
            _spec(impulse),
            0,
            [{"rigid_body_index": 0, "local_flag": True, "velocity": [0, 0, 0], "torque": [0, 0, 0]}],
        )


@pytest.mark.parametrize(
    "offset",
    [
        {"vertex_index": True, "position_offset": [0, 0, 0]},
        {"vertex_index": 0, "position_offset": [0, 0, math.inf]},
        {"vertex_index": 0, "position_offset": [0, 0, 0], "extra": 1},
        {"vertex_index": 0},
    ],
)
def test_offset_validation_rejects_bool_nonfinite_unknown_and_missing(offset) -> None:
    with pytest.raises(MorphAuthoringError):
        replace_morph_offsets(_spec(_morph(0)), 0, [offset])


def test_material_offset_validates_reference_and_operation_type() -> None:
    base = _morph(0, "material")
    good = {
        "material_index": 0,
        "operation_type": 0,
        "diffuse": [0, 0, 0, 0],
        "specular": [0, 0, 0],
        "specular_coefficient": 0,
        "ambient": [0, 0, 0],
        "edge_color": [0, 0, 0, 0],
        "edge_size": 0,
        "texture_factor": [0, 0, 0, 0],
        "sphere_texture_factor": [0, 0, 0, 0],
        "toon_texture_factor": [0, 0, 0, 0],
    }
    assert replace_morph_offsets(_spec(base), 0, [good]).morphs[0].offsets[0]["operation_type"] == 0
    with pytest.raises(MorphAuthoringError):
        replace_morph_offsets(_spec(base), 0, [{**good, "material_index": 3}])
    with pytest.raises(MorphAuthoringError):
        replace_morph_offsets(_spec(base), 0, [{**good, "operation_type": True}])


def test_replace_requires_existing_and_rejects_nonempty_type_change() -> None:
    old = _morph(0, "vertex", ({"vertex_index": 0, "position_offset": [1, 0, 0]},))
    with pytest.raises(MorphAuthoringError, match="non-empty"):
        replace_morph(_spec(old), _morph(0, "bone"))
    with pytest.raises(MorphAuthoringError, match="does not exist"):
        replace_morph(_spec(), _morph(4))


def test_delete_references_and_compacts_with_reference_remap() -> None:
    base = _morph(0)
    group = _morph(1, "group", ({"morph_index": 0, "morph_rate": 1},))
    other = _morph(2)
    with pytest.raises(MorphAuthoringError, match="referenced"):
        delete_morph(_spec(base, group, other), 0)
    unreferenced = _spec(_morph(0), _morph(1), _morph(2, "group", ({"morph_index": 1, "morph_rate": 1},)))
    result = delete_morph(unreferenced, 0)
    assert [m.index for m in result.morphs] == [0, 1]
    assert result.morphs[1].offsets[0]["morph_index"] == 0


def test_reindex_and_move_update_group_references() -> None:
    spec = _spec(_morph(0), _morph(1), _morph(2, "group", ({"morph_index": 0, "morph_rate": 1},)))
    original_fingerprint = spec.fingerprint()
    reordered = reindex_morphs(spec, [2, 0, 1])
    assert [m.name for m in reordered.morphs] == ["m2", "m0", "m1"]
    assert reordered.morphs[0].offsets[0]["morph_index"] == 1
    moved = move_morph(spec, 0, 2)
    assert [m.name for m in moved.morphs] == ["m1", "m2", "m0"]
    assert moved.morphs[1].offsets[0]["morph_index"] == 2
    with pytest.raises(MorphAuthoringError):
        reindex_morphs(spec, [0, 0, 1])
    assert spec.fingerprint() == original_fingerprint


@pytest.mark.parametrize("bad_reference", [True, "0"])
def test_reindex_rejects_non_integer_existing_group_reference(bad_reference) -> None:
    malformed = _spec(
        _morph(0),
        _morph(1, "group", ({"morph_index": bad_reference, "morph_rate": 1},)),
    )

    with pytest.raises(MorphAuthoringError, match="morph_index"):
        reindex_morphs(malformed, [1, 0])


def test_operations_are_immutable_and_fingerprints_deterministic() -> None:
    offsets = [{"vertex_index": 0, "position_offset": [1, 2, 3]}]
    before = copy.deepcopy(offsets)
    spec = _spec(_morph(0))
    result = replace_morph_offsets(spec, 0, offsets)
    offsets[0]["position_offset"][0] = 99
    assert offsets != before
    assert result.morphs[0].offsets[0]["position_offset"] == (1.0, 2.0, 3.0)
    assert result.fingerprint() == MmdModelAuthoringSpec.from_mapping(result.to_mapping()).fingerprint()
