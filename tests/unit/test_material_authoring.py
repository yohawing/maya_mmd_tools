"""Unit tests for pure material authoring mutations."""

from dataclasses import replace

import pytest

from mmd_tools.core.material_authoring import (
    MaterialAuthoringError,
    create_material,
    delete_material,
    duplicate_material,
    replace_material,
)
from mmd_tools.core.model_authoring_spec import (
    MmdMaterialSpec,
    MmdModelAuthoringSpec,
    MmdModelSpec,
    MmdMorphSpec,
)


def _material(index: int, name: str = "材質") -> MmdMaterialSpec:
    return MmdMaterialSpec(
        name=name,
        name_english=f"Material {index}",
        index=index,
        texture_path="textures/テクスチャ.png",
        resolved_texture_path=r"C:\assets\テクスチャ.png",
        sphere_texture_path="sphere/反射.sph",
        resolved_sphere_texture_path=r"C:\assets\反射.sph",
        toon_texture_path="toon/輪郭.bmp",
        resolved_toon_texture_path=r"C:\assets\輪郭.bmp",
        memo="メモ",
    )


def _spec(*material_args, morphs=()):
    if len(material_args) == 1 and not isinstance(material_args[0], MmdMaterialSpec):
        materials = tuple(material_args[0])
    else:
        materials = tuple(material_args)
    return MmdModelAuthoringSpec(
        model=MmdModelSpec(name="モデル", name_english="Model"),
        materials=materials,
        morphs=tuple(morphs),
    )


def test_create_default_is_complete_unicode_safe_and_deterministic():
    spec = _spec()
    result = create_material(spec)
    assert result.materials == (MmdMaterialSpec(name="Material 0", name_english="Material 0", index=0),)
    assert result.fingerprint() == create_material(spec).fingerprint()
    assert spec.materials == ()


def test_create_supplied_index_cannot_override_allocation_and_paths_preserved():
    source = replace(_material(99), name="新しい材質")
    spec = _spec(_material(2, "既存"))
    result = create_material(spec, source)
    assert result.materials[-1].index == 3
    assert result.materials[-1].name == "新しい材質"
    assert result.materials[-1].texture_path == source.texture_path
    assert result.materials[-1].resolved_texture_path == source.resolved_texture_path


def test_duplicate_allocates_non_colliding_names_and_preserves_semantic_fields():
    source = _material(4, "キャラクター")
    spec = _spec(source, _material(9, "キャラクター Copy"))
    result = duplicate_material(spec, 4)
    duplicate = result.materials[-1]
    assert duplicate.index == 10
    assert duplicate.name == "キャラクター Copy (2)"
    assert duplicate.name_english == "Material 4 Copy"
    assert duplicate.texture_path == source.texture_path
    assert duplicate.resolved_texture_path == source.resolved_texture_path
    assert duplicate.diffuse == source.diffuse
    assert duplicate.memo == source.memo
    assert duplicate_material(spec, 4).fingerprint() == result.fingerprint()


def test_duplicate_missing_source_and_bool_index_fail_closed():
    spec = _spec(_material(0))
    with pytest.raises(MaterialAuthoringError):
        duplicate_material(spec, 7)
    with pytest.raises(MaterialAuthoringError):
        duplicate_material(spec, True)


def test_replace_only_changes_target_material_and_requires_existing_index():
    first = _material(0, "A")
    second = _material(1, "B")
    morph = MmdMorphSpec(name="頂点", morph_type="vertex", offsets=({"index": 1},))
    spec = _spec(first, second, morphs=(morph,))
    replacement = replace(_material(0, "置換"), diffuse=(0.25, 0.5, 0.75, 1.0))
    result = replace_material(spec, replacement)
    assert result.materials == (replacement, second)
    assert result.bones == spec.bones
    assert result.morphs == spec.morphs
    with pytest.raises(MaterialAuthoringError):
        replace_material(spec, _material(8, "不存在"))


def test_delete_reindexes_and_remaps_material_morph_offsets_and_preserves_all_sentinel():
    materials = (_material(0, "A"), _material(1, "B"), _material(2, "C"))
    material_morph = MmdMorphSpec(
        name="材質モーフ",
        morph_type="material",
        offsets=(
            {"material_index": 0, "diffuse": [1, 0, 0, 1]},
            {"material_index": 2, "diffuse": [0, 1, 0, 1]},
            {"material_index": -1, "diffuse": [0, 0, 1, 1]},
        ),
    )
    spec = _spec(materials, morphs=(material_morph,))
    result = delete_material(spec, 1)
    assert [item.index for item in result.materials] == [0, 1]
    assert [item.name for item in result.materials] == ["A", "C"]
    assert [offset["material_index"] for offset in result.morphs[0].offsets] == [0, 1, -1]
    assert [offset["diffuse"] for offset in result.morphs[0].offsets] == [
        (1.0, 0.0, 0.0, 1.0),
        (0.0, 1.0, 0.0, 1.0),
        (0.0, 0.0, 1.0, 1.0),
    ]
    assert [item.index for item in spec.materials] == [0, 1, 2]


def test_delete_rejects_deleted_references_and_malformed_or_unknown_references():
    materials = (_material(0, "A"), _material(1, "B"))
    for offsets in (
        ({"material_index": 1},),
        ({},),
        ({"material_index": True},),
        ({"material_index": "0"},),
        ({"material_index": 9},),
    ):
        spec = _spec(materials, morphs=(MmdMorphSpec(name="m", morph_type="material", offsets=offsets),))
        with pytest.raises(MaterialAuthoringError):
            delete_material(spec, 1 if offsets[0].get("material_index") == 1 else 0)


def test_non_material_offsets_are_untouched_and_input_fingerprint_is_unchanged():
    materials = (_material(0, "A"), _material(2, "C"))
    vertex = MmdMorphSpec(name="v", morph_type="vertex", offsets=({"material_index": "leave", "index": 3},))
    spec = _spec(materials, morphs=(vertex,))
    before = spec.fingerprint()
    result = delete_material(spec, 0)
    assert result.morphs == spec.morphs
    assert result.morphs[0].offsets == spec.morphs[0].offsets
    assert spec.fingerprint() == before


def test_exact_spec_type_is_required():
    class SpecSubclass(MmdModelAuthoringSpec):
        pass

    subclass = SpecSubclass(model=MmdModelSpec(name="x"))
    with pytest.raises(MaterialAuthoringError):
        create_material(subclass)


def test_delete_bool_or_missing_index_fail_closed():
    spec = _spec(_material(0))
    with pytest.raises(MaterialAuthoringError):
        delete_material(spec, True)
    with pytest.raises(MaterialAuthoringError):
        delete_material(spec, 4)
