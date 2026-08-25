"""Pure semantic tests for the PMX bone authoring operations."""

from copy import deepcopy

import pytest

from mmd_tools.core.bone_authoring import (
    BoneAuthoringError,
    capture_rest,
    register_bone,
    reindex_bones,
    replace_bone,
    unregister_bone,
)
from mmd_tools.core.model_authoring_spec import (
    MmdBoneSpec,
    MmdModelAuthoringSpec,
    MmdModelSpec,
    MmdMorphSpec,
)


def _base_spec() -> MmdModelAuthoringSpec:
    return MmdModelAuthoringSpec(
        model=MmdModelSpec("モデル", name_english="Model"),
        bones=(
            MmdBoneSpec(name="root", index=0, binding_identity="|モデル|root"),
            MmdBoneSpec(
                name="mid",
                index=2,
                parent_index=0,
                connect_bone_index=0,
                grant_parent_index=0,
                ik_target_index=0,
                ik_links=({"bone": 0, "angle": 0.5},),
                binding_identity="|モデル|mid",
            ),
            MmdBoneSpec(
                name="tip",
                index=5,
                parent_index=2,
                binding_identity="|モデル|tip",
            ),
        ),
        morphs=(
            MmdMorphSpec(
                name="boneMorph",
                index=0,
                morph_type="bone",
                offsets=({"bone_index": 5, "translation": [1.0, 2.0, 3.0]},),
            ),
            MmdMorphSpec(
                name="vertexMorph",
                index=1,
                morph_type="vertex",
                offsets=({"vertex_index": 7, "delta": [0.1, 0.2, 0.3]},),
            ),
        ),
    )


def test_register_allocates_next_index_and_preserves_unicode_binding() -> None:
    spec = _base_spec()
    result = register_bone(
        spec,
        MmdBoneSpec(name="追加", index=999, parent_index=5, binding_identity="|モデル|追加"),
    )

    added = result.bones[-1]
    assert added.index == 6
    assert added.parent_index == 5
    assert added.name == "追加"
    assert added.binding_identity == "|モデル|追加"
    assert spec.bones[-1].index == 5


def test_register_requires_unique_nonempty_binding_identity() -> None:
    spec = _base_spec()
    with pytest.raises(BoneAuthoringError):
        register_bone(spec, MmdBoneSpec("empty", binding_identity="   "))
    with pytest.raises(BoneAuthoringError):
        register_bone(spec, MmdBoneSpec("duplicate", binding_identity="|モデル|mid"))
    with pytest.raises(BoneAuthoringError):
        register_bone(spec, MmdBoneSpec("unknown-parent", parent_index=99, binding_identity="|new"))

    duplicate_existing = MmdModelAuthoringSpec(
        model=MmdModelSpec("model"),
        bones=(
            MmdBoneSpec("a", index=0, binding_identity="|same"),
            MmdBoneSpec("b", index=1, binding_identity="|same"),
        ),
    )
    with pytest.raises(BoneAuthoringError, match="duplicate bone binding"):
        register_bone(duplicate_existing, MmdBoneSpec("new", binding_identity="|new"))


def test_replace_requires_existing_index_and_same_binding_identity() -> None:
    spec = _base_spec()
    replacement = MmdBoneSpec(
        name="中間（更新）",
        index=2,
        parent_index=0,
        binding_identity="|モデル|mid",
        rest_position=(1.0, 2.0, 3.0),
    )
    result = replace_bone(spec, replacement)
    assert result.bones[1] == replacement
    with pytest.raises(BoneAuthoringError):
        replace_bone(spec, MmdBoneSpec("bad", index=2, binding_identity="|other"))
    with pytest.raises(BoneAuthoringError):
        replace_bone(spec, MmdBoneSpec("missing", index=99, binding_identity="|missing"))

    unbound = MmdModelAuthoringSpec(model=MmdModelSpec("model"), bones=(MmdBoneSpec("unbound", index=0),))
    assert replace_bone(unbound, MmdBoneSpec("unbound-updated", index=0)).bones[0].name == "unbound-updated"


def test_capture_rest_requires_finite_non_boolean_vector() -> None:
    spec = _base_spec()
    result = capture_rest(spec, 2, [1, 2.5, -3])
    assert result.bones[1].rest_position == (1.0, 2.5, -3.0)
    assert spec.bones[1].rest_position == (0.0, 0.0, 0.0)
    for invalid in ([True, 0, 0], [float("nan"), 0, 0], [0, 0], [0, 0, 0, 0]):
        with pytest.raises(BoneAuthoringError):
            capture_rest(spec, 2, invalid)


def test_reindex_remaps_all_bone_references_and_preserves_nonbone_morph() -> None:
    spec = _base_spec()
    result = reindex_bones(spec, [5, 0, 2])

    assert [bone.name for bone in result.bones] == ["tip", "root", "mid"]
    by_name = {bone.name: bone for bone in result.bones}
    assert by_name["tip"].index == 0
    assert by_name["root"].index == 1
    assert by_name["mid"].index == 2
    assert by_name["tip"].parent_index == 2
    assert by_name["mid"].parent_index == 1
    assert by_name["mid"].connect_bone_index == 1
    assert by_name["mid"].grant_parent_index == 1
    assert by_name["mid"].ik_target_index == 1
    assert by_name["mid"].ik_links[0]["bone"] == 1
    assert result.morphs[0].offsets[0]["bone_index"] == 0
    assert result.morphs[1] == spec.morphs[1]


def test_replace_and_reindex_preserve_connect_bone_missing_target_sentinel() -> None:
    spec = MmdModelAuthoringSpec(
        model=MmdModelSpec("model"),
        bones=(
            MmdBoneSpec(
                "terminal",
                index=0,
                flags=1,
                connect_bone_index=-1,
                binding_identity="terminal",
            ),
            MmdBoneSpec("other", index=1, binding_identity="other"),
        ),
    )

    replaced = replace_bone(
        spec,
        MmdBoneSpec(
            "terminal updated",
            index=0,
            flags=1,
            connect_bone_index=-1,
            binding_identity="terminal",
        ),
    )
    reindexed = reindex_bones(replaced, [1, 0])

    terminal = next(bone for bone in reindexed.bones if bone.name == "terminal updated")
    assert terminal.index == 1
    assert terminal.connect_bone_index == -1


def test_reindex_rejects_non_permutation_and_malformed_refs() -> None:
    spec = _base_spec()
    for ordered in ([0, 2], [0, 2, 2], [0, 2, 9], [False, 2, 5]):
        with pytest.raises(BoneAuthoringError) as exc_info:
            reindex_bones(spec, ordered)
        assert type(exc_info.value) is BoneAuthoringError

    malformed = MmdModelAuthoringSpec(
        model=spec.model,
        bones=spec.bones,
        morphs=(
            MmdMorphSpec(name="bad", morph_type="bone", offsets=({"bone_index": 99},)),
        ),
    )
    with pytest.raises(BoneAuthoringError):
        reindex_bones(malformed, [0, 2, 5])


def test_unregister_rejects_all_reference_kinds() -> None:
    spec = _base_spec()
    for target in (0, 2, 5):
        with pytest.raises(BoneAuthoringError):
            unregister_bone(spec, target)


def test_unregister_unreferenced_bone_compacts_and_preserves_input() -> None:
    spec = MmdModelAuthoringSpec(
        model=MmdModelSpec("model"),
        bones=(
            MmdBoneSpec("root", index=0, binding_identity="root"),
            MmdBoneSpec("unused", index=2, binding_identity="unused"),
            MmdBoneSpec("tip", index=5, parent_index=0, binding_identity="tip"),
        ),
    )
    before = deepcopy(spec.to_mapping())
    fingerprint = spec.fingerprint()
    result = unregister_bone(spec, 2)

    assert [bone.index for bone in result.bones] == [0, 1]
    assert [bone.name for bone in result.bones] == ["root", "tip"]
    assert result.bones[1].parent_index == 0
    assert spec.to_mapping() == before
    assert spec.fingerprint() == fingerprint


def test_operations_do_not_mutate_nested_payloads() -> None:
    spec = _base_spec()
    original = spec.to_mapping()
    fingerprint = spec.fingerprint()
    _ = reindex_bones(spec, [5, 0, 2])
    assert spec.to_mapping() == original
    assert spec.fingerprint() == fingerprint
