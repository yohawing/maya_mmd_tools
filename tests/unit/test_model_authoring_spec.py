"""Focused tests for the immutable model authoring contract."""

from copy import deepcopy

import pytest

from mmd_tools.core.model_authoring_spec import (
    MmdBoneSpec,
    MmdMaterialSpec,
    MmdModelAuthoringSpec,
    MmdModelSpec,
    MmdMorphSpec,
)


def _spec() -> MmdModelAuthoringSpec:
    return MmdModelAuthoringSpec(
        model=MmdModelSpec(
            name="モデル_日本語",
            name_english="Model",
            comment="コメント",
            comment_english="Comment",
        ),
        bones=(
            MmdBoneSpec(name="root", index=0),
            MmdBoneSpec(
                name="child",
                index=1,
                parent_index=0,
                external_parent_key=-1,
                binding_identity="|root|child",
            ),
        ),
        materials=(
            MmdMaterialSpec(
                name="材質",
                index=0,
                texture_path="textures/顔.png",
                resolved_texture_path=r"C:\\project\\textures\\顔.png",
            ),
        ),
        morphs=(
            MmdMorphSpec(
                name="笑顔",
                index=0,
                morph_type="vertex",
                offsets=({"vertex_index": 1, "delta": [0.1, 0.2, 0.3]},),
            ),
        ),
    )


def test_unicode_round_trip_and_stable_fingerprint() -> None:
    spec = _spec()
    payload = spec.to_mapping()
    restored = MmdModelAuthoringSpec.from_mapping(deepcopy(payload))

    assert restored == spec
    assert restored.to_mapping() == payload
    assert restored.fingerprint() == spec.fingerprint()
    assert spec.materials[0].source_texture_path == "textures/顔.png"
    assert spec.materials[0].resolved_texture_path != spec.materials[0].source_texture_path


def test_collection_order_is_canonicalized_by_explicit_index() -> None:
    first = MmdModelAuthoringSpec(
        model=MmdModelSpec("model"),
        bones=(MmdBoneSpec("child", index=2), MmdBoneSpec("root", index=0)),
        materials=(MmdMaterialSpec("second", index=3), MmdMaterialSpec("first", index=1)),
    )
    second = MmdModelAuthoringSpec(
        model=MmdModelSpec("model"),
        bones=tuple(reversed(first.bones)),
        materials=tuple(reversed(first.materials)),
    )

    assert [bone.index for bone in first.bones] == [0, 2]
    assert [material.index for material in first.materials] == [1, 3]
    assert first == second
    assert first.fingerprint() == second.fingerprint()


def test_input_mutation_cannot_change_accepted_spec() -> None:
    offsets = [{"vertex_index": 2, "delta": [1.0, 2.0, 3.0]}]
    spec = MmdMorphSpec(name="morph", offsets=offsets)
    offsets[0]["vertex_index"] = 99
    offsets[0]["delta"][0] = 99.0

    assert spec.to_mapping()["offsets"] == [{"vertex_index": 2, "delta": [1.0, 2.0, 3.0]}]
    with pytest.raises(TypeError):
        spec.offsets[0]["vertex_index"] = 4  # type: ignore[index]


@pytest.mark.parametrize(
    ("mutator", "error"),
    [
        (lambda payload: payload.update(schema_version=999), ValueError),
        (lambda payload: payload.update(unexpected=True), ValueError),
        (lambda payload: payload["materials"].append(payload["materials"][0]), ValueError),
        (lambda payload: payload["bones"].append({**payload["bones"][0], "index": 0}), ValueError),
        (lambda payload: payload["materials"][0].update(index=-1), ValueError),
        (lambda payload: payload["materials"][0].update(diffuse=[float("nan")] * 4), ValueError),
        (lambda payload: payload["morphs"][0].update(offsets=[{"bad": {1, 2}}]), TypeError),
        (lambda payload: payload["morphs"][0].update(offsets=[1]), TypeError),
    ],
)
def test_parser_fails_closed(mutator, error) -> None:
    payload = _spec().to_mapping()
    mutator(payload)
    with pytest.raises(error):
        MmdModelAuthoringSpec.from_mapping(payload)


def test_material_and_morph_indices_must_be_unique() -> None:
    first = MmdMaterialSpec(name="one", index=0)
    second = MmdMaterialSpec(name="two", index=0)
    with pytest.raises(ValueError, match="duplicate"):
        MmdModelAuthoringSpec(MmdModelSpec("model"), materials=(first, second))


def test_material_binding_identity_round_trip_and_legacy_mapping_normalization() -> None:
    material = MmdMaterialSpec(name="材質", binding_identity="|modelRoot|materialSG")
    payload = material.to_mapping()
    restored = MmdMaterialSpec.from_mapping(payload)

    assert payload["binding_identity"] == "|modelRoot|materialSG"
    assert restored == material
    assert restored.to_mapping() == payload

    legacy_payload = dict(payload)
    legacy_payload.pop("binding_identity")
    legacy_restored = MmdMaterialSpec.from_mapping(legacy_payload)
    assert legacy_restored.binding_identity is None
    assert legacy_restored.to_mapping()["binding_identity"] is None


@pytest.mark.parametrize("value", ["", 1, False, [], {}])
def test_material_binding_identity_rejects_empty_or_non_string(value) -> None:
    with pytest.raises((TypeError, ValueError)):
        MmdMaterialSpec(name="材質", binding_identity=value)


def test_loss_policy_uses_validation_vocabulary() -> None:
    accepted = MmdMorphSpec(name="reject", loss_policy="reject")
    assert accepted.loss_policy == "reject"
    with pytest.raises(ValueError, match="loss_policy"):
        MmdMorphSpec(name="error", loss_policy="error")


def test_external_parent_key_is_numeric_and_round_trips() -> None:
    bone = MmdBoneSpec(name="bone", external_parent_key=-1)
    restored = MmdBoneSpec.from_mapping(bone.to_mapping())
    assert restored == bone
    with pytest.raises(TypeError):
        MmdBoneSpec(name="string", external_parent_key="-1")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        MmdBoneSpec(name="bool", external_parent_key=True)  # type: ignore[arg-type]


def test_connect_bone_missing_target_sentinel_round_trips() -> None:
    bone = MmdBoneSpec(name="terminal", flags=1, connect_bone_index=-1)

    assert MmdBoneSpec.from_mapping(bone.to_mapping()) == bone
    with pytest.raises(ValueError, match="connect_bone_index"):
        MmdBoneSpec(name="invalid", flags=1, connect_bone_index=-2)
