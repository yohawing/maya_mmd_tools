"""Focused tests for Maya-independent model authoring service routing."""

from dataclasses import dataclass, replace

import pytest

from mmd_tools.adapters.model_authoring_service import ModelAuthoringService, ModelAuthoringServiceError
from mmd_tools.core.model_authoring_spec import (
    MmdBoneSpec,
    MmdMaterialSpec,
    MmdModelAuthoringSpec,
    MmdModelSpec,
    MmdMorphSpec,
)


@dataclass
class FakeMetadataAdapter:
    """Small in-memory read/write adapter with observable call order."""

    current: MmdModelAuthoringSpec
    calls: list[tuple[str, str, MmdModelAuthoringSpec | None]] | None = None
    fail_write: bool = False

    def __post_init__(self) -> None:
        if self.calls is None:
            self.calls = []

    def read_spec(self, model_root: str) -> MmdModelAuthoringSpec:
        self.calls.append(("read", model_root, None))
        return self.current

    def write_spec(self, model_root: str, spec: MmdModelAuthoringSpec) -> None:
        self.calls.append(("write", model_root, spec))
        if self.fail_write:
            raise RuntimeError("write failed")
        self.current = spec


def _spec() -> MmdModelAuthoringSpec:
    return MmdModelAuthoringSpec(
        model=MmdModelSpec("モデル_日本語", "Model", "コメント", "Comment"),
        bones=(
            MmdBoneSpec(name="root", index=0, binding_identity="|modelRoot|root"),
            MmdBoneSpec(name="child", index=1, parent_index=0, binding_identity="|modelRoot|child"),
        ),
        materials=(MmdMaterialSpec(name="材質", index=0, binding_identity="|modelRoot|材質"),),
        morphs=(MmdMorphSpec(name="笑顔", index=0, morph_type="vertex"),),
    )


def _service(spec: MmdModelAuthoringSpec | None = None) -> tuple[ModelAuthoringService, FakeMetadataAdapter]:
    adapter = FakeMetadataAdapter(spec or _spec())
    return ModelAuthoringService(adapter), adapter


def test_material_operations_read_mutate_write_full_spec() -> None:
    service, adapter = _service()
    created = service.create_material("|modelRoot")
    assert created.materials[-1].index == 1
    assert [call[0] for call in adapter.calls] == ["read", "write"]
    assert created.model.name == "モデル_日本語"
    assert adapter.current.fingerprint() == created.fingerprint()

    adapter.calls.clear()
    duplicated = service.duplicate_material("|modelRoot", 0)
    assert duplicated.materials[-1].name.endswith("Copy")
    assert [call[0] for call in adapter.calls] == ["read", "write"]

    adapter.calls.clear()
    replacement = replace(duplicated.materials[0], name="置換材質")
    replaced = service.replace_material("|modelRoot", replacement)
    assert replaced.materials[0].name == "置換材質"
    assert [call[0] for call in adapter.calls] == ["read", "write"]

    adapter.calls.clear()
    deleted = service.delete_material("|modelRoot", 0)
    assert [material.index for material in deleted.materials] == [0, 1]
    assert [call[0] for call in adapter.calls] == ["read", "write"]


def test_bone_operations_route_explicit_root_and_return_new_specs() -> None:
    service, adapter = _service()
    registered = service.register_bone(
        "|modelRoot",
        MmdBoneSpec(name="追加", binding_identity="|modelRoot|追加"),
    )
    assert registered.bones[-1].index == 2

    adapter.calls.clear()
    replaced = service.replace_bone("|modelRoot", replace(registered.bones[1], name="child2"))
    assert replaced.bones[1].name == "child2"
    adapter.calls.clear()
    captured = service.capture_rest("|modelRoot", 1, (1.0, 2.0, 3.0))
    assert captured.bones[1].rest_position == (1.0, 2.0, 3.0)
    adapter.calls.clear()
    reindexed = service.reindex_bones("|modelRoot", (1, 0, 2))
    assert sorted(bone.index for bone in reindexed.bones) == [0, 1, 2]
    assert [call[0] for call in adapter.calls] == ["read", "write"]


def test_unregister_bone_routes_and_compacts() -> None:
    root_only = replace(_spec(), bones=(_spec().bones[0],))
    service, adapter = _service(root_only)
    result = service.unregister_bone("|modelRoot", 0)
    assert result.bones == ()
    assert [call[0] for call in adapter.calls] == ["read", "write"]


def test_morph_operations_route_all_explicit_mutations() -> None:
    service, adapter = _service()
    created = service.create_morph("|modelRoot")
    assert created.morphs[-1].index == 1

    adapter.calls.clear()
    replacement = replace(created.morphs[0], name="笑顔2")
    replaced = service.replace_morph("|modelRoot", replacement)
    assert replaced.morphs[0].name == "笑顔2"

    adapter.calls.clear()
    offsets = ({"vertex_index": 4, "position_offset": (0.1, 0.2, 0.3)},)
    offset_replaced = service.replace_morph_offsets("|modelRoot", 0, offsets)
    assert offset_replaced.morphs[0].offsets[0]["vertex_index"] == 4

    adapter.calls.clear()
    moved = service.move_morph("|modelRoot", 0, 1)
    assert sorted(morph.index for morph in moved.morphs) == [0, 1]

    adapter.calls.clear()
    reindexed = service.reindex_morphs("|modelRoot", (1, 0))
    assert sorted(morph.index for morph in reindexed.morphs) == [0, 1]

    adapter.calls.clear()
    deleted = service.delete_morph("|modelRoot", 1)
    assert len(deleted.morphs) == 1
    assert [call[0] for call in adapter.calls] == ["read", "write"]


def test_mutation_failure_performs_no_write() -> None:
    service, adapter = _service()

    with pytest.raises(ModelAuthoringServiceError, match="replace_material mutation failed"):
        service.replace_material("|modelRoot", MmdMaterialSpec(name="missing", index=99))

    assert [call[0] for call in adapter.calls] == ["read"]


def test_invalid_root_performs_no_read_or_write() -> None:
    service, adapter = _service()

    with pytest.raises(ModelAuthoringServiceError, match="model_root"):
        service.create_material("   ")

    assert adapter.calls == []


def test_write_failure_is_contextual_and_not_reported_as_success() -> None:
    service, adapter = _service()
    adapter.fail_write = True

    with pytest.raises(ModelAuthoringServiceError, match="create_material write failed"):
        service.create_material("|modelRoot")

    assert [call[0] for call in adapter.calls] == ["read", "write"]
    assert adapter.current == _spec()


def test_packaged_template_returns_fresh_spec_without_adapter_calls() -> None:
    service, adapter = _service()
    first = service.instantiate_template("pmx20-basic-v1", "新モデル", "New Model")
    second = service.instantiate_template("pmx20-basic-v1", "新モデル", "New Model")

    assert first.model.name == "新モデル"
    assert first.model.name_english == "New Model"
    assert first == second
    assert first.fingerprint() == second.fingerprint()
    assert adapter.calls == []


def test_service_constructor_rejects_non_adapter() -> None:
    with pytest.raises(TypeError):
        ModelAuthoringService(object())  # type: ignore[arg-type]
