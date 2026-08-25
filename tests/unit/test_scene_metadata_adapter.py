"""Focused tests for the strict, transactional scene metadata boundary."""

from copy import deepcopy
from dataclasses import dataclass
from dataclasses import field
from typing import Any

import pytest

from mmd_tools.adapters.scene_metadata_adapter import SceneMetadataAdapter, SceneMetadataError
from mmd_tools.core.model_authoring_spec import (
    MmdBoneSpec,
    MmdMaterialSpec,
    MmdModelAuthoringSpec,
    MmdModelSpec,
    MmdMorphSpec,
)


@dataclass
class FakeBackend:
    """In-memory normalized backend; it intentionally has no Maya dependency."""

    model: dict[str, Any]
    bones: list[Any]
    materials: list[Any]
    morphs: list[Any]
    calls: list[str] = field(default_factory=list)
    fail_section: str | None = None
    _snapshot: tuple[Any, ...] | None = None

    def read_model_metadata(self, model_root: str) -> dict[str, Any]:
        assert model_root == "|modelRoot"
        return self.model

    def iter_bone_metadata(self, model_root: str) -> list[Any]:
        assert model_root == "|modelRoot"
        return self.bones

    def iter_material_metadata(self, model_root: str) -> list[Any]:
        assert model_root == "|modelRoot"
        return self.materials

    def iter_morph_metadata(self, model_root: str) -> list[Any]:
        assert model_root == "|modelRoot"
        return self.morphs

    def begin_write(self, model_root: str) -> None:
        assert model_root == "|modelRoot"
        self.calls.append("begin")
        self._snapshot = (deepcopy(self.model), deepcopy(self.bones), deepcopy(self.materials), deepcopy(self.morphs))

    def apply_model_metadata(self, model_root: str, metadata: dict[str, Any]) -> None:
        self.calls.append("model")
        self._maybe_fail("model")
        self.model = metadata

    def apply_bone_metadata(self, model_root: str, metadata: list[Any]) -> None:
        self.calls.append("bones")
        self._maybe_fail("bones")
        self.bones = metadata

    def apply_material_metadata(self, model_root: str, metadata: list[Any]) -> None:
        self.calls.append("materials")
        self._maybe_fail("materials")
        self.materials = metadata

    def apply_morph_metadata(self, model_root: str, metadata: list[Any]) -> None:
        self.calls.append("morphs")
        self._maybe_fail("morphs")
        self.morphs = metadata

    def commit_write(self, model_root: str) -> None:
        self.calls.append("commit")
        self._maybe_fail("commit")

    def rollback_write(self, model_root: str) -> None:
        self.calls.append("rollback")
        if self._snapshot is not None:
            self.model, self.bones, self.materials, self.morphs = deepcopy(self._snapshot)

    def _maybe_fail(self, section: str) -> None:
        if self.fail_section == section:
            raise RuntimeError(f"forced {section} failure")


def _backend(*, reverse: bool = False) -> FakeBackend:
    model = MmdModelSpec("モデル_日本語", "Model", "コメント", "Comment").to_mapping()
    bones = [
        MmdBoneSpec(name="root", index=0).to_mapping(),
        MmdBoneSpec(name="child", index=2, parent_index=0).to_mapping(),
    ]
    materials = [MmdMaterialSpec(name="材質", index=4).to_mapping(), MmdMaterialSpec(name="材質2", index=1).to_mapping()]
    morphs = [MmdMorphSpec(name="笑顔", index=3, offsets=({"vertex_index": 2},)).to_mapping()]
    if reverse:
        bones.reverse()
        materials.reverse()
        morphs.reverse()
    return FakeBackend(model, bones, materials, morphs)


def test_unicode_model_round_trip_and_sorted_fingerprint() -> None:
    first = SceneMetadataAdapter(_backend()).read_spec("|modelRoot")
    second = SceneMetadataAdapter(_backend(reverse=True)).read_spec("|modelRoot")

    assert first == second
    assert first.model.name == "モデル_日本語"
    assert [bone.index for bone in first.bones] == [0, 2]
    assert [material.index for material in first.materials] == [1, 4]
    assert first.fingerprint() == second.fingerprint()


def test_backend_mutation_after_read_does_not_mutate_spec() -> None:
    backend = _backend()
    spec = SceneMetadataAdapter(backend).read_spec("|modelRoot")
    backend.model["name"] = "changed"
    backend.materials[0]["name"] = "changed"
    backend.morphs[0]["offsets"][0]["vertex_index"] = 999

    assert spec.model.name == "モデル_日本語"
    assert spec.materials[1].name == "材質"
    assert spec.morphs[0].offsets[0]["vertex_index"] == 2


def test_empty_collections_are_valid() -> None:
    backend = _backend()
    backend.bones = []
    backend.materials = []
    backend.morphs = []

    spec = SceneMetadataAdapter(backend).read_spec("|modelRoot")
    assert spec.bones == ()
    assert spec.materials == ()
    assert spec.morphs == ()


@pytest.mark.parametrize("root", [None, "", "   ", 42])
def test_invalid_root_is_rejected(root: Any) -> None:
    with pytest.raises(SceneMetadataError, match="model_root"):
        SceneMetadataAdapter(_backend()).read_spec(root)


def test_backend_failure_has_root_and_section_context() -> None:
    class FailingBackend(FakeBackend):
        def iter_bone_metadata(self, model_root: str) -> list[Any]:
            raise RuntimeError("backend unavailable")

    with pytest.raises(SceneMetadataError, match=r"bones.*\|modelRoot.*backend unavailable"):
        SceneMetadataAdapter(FailingBackend(**_backend().__dict__)).read_spec("|modelRoot")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda backend: backend.materials[0].update(index=1),
        lambda backend: backend.bones[0].update(index=-1),
        lambda backend: backend.materials[0].update(diffuse=[float("nan")] * 4),
        lambda backend: backend.materials[0].update(unknown_field=True),
        lambda backend: backend.morphs[0].update(offsets=[{"delta": {1, 2}}]),
        lambda backend: backend.morphs[0].update(offsets=[1]),
    ],
)
def test_malformed_metadata_fails_closed(mutate) -> None:
    backend = _backend()
    mutate(backend)

    with pytest.raises(SceneMetadataError):
        SceneMetadataAdapter(backend).read_spec("|modelRoot")


def test_non_mapping_collection_entry_fails_closed() -> None:
    backend = _backend()
    backend.bones.append(1)

    with pytest.raises(SceneMetadataError, match="bones metadata entry"):
        SceneMetadataAdapter(backend).read_spec("|modelRoot")


def test_required_model_metadata_and_unknown_model_field_fail() -> None:
    backend = _backend()
    backend.model.pop("comment")
    with pytest.raises(SceneMetadataError, match="model metadata"):
        SceneMetadataAdapter(backend).read_spec("|modelRoot")

    backend = _backend()
    backend.model["extra"] = True
    with pytest.raises(SceneMetadataError, match="model metadata"):
        SceneMetadataAdapter(backend).read_spec("|modelRoot")


def test_duplicate_index_error_is_not_reindexed() -> None:
    backend = _backend()
    backend.materials[1]["index"] = backend.materials[0]["index"]
    with pytest.raises(SceneMetadataError, match="duplicate"):
        SceneMetadataAdapter(backend).read_spec("|modelRoot")


def test_write_read_round_trip_preserves_unicode_and_fingerprint() -> None:
    backend = _backend()
    adapter = SceneMetadataAdapter(backend)
    spec = adapter.read_spec("|modelRoot")
    backend.calls.clear()

    adapter.write_spec("|modelRoot", spec)

    assert backend.calls == ["begin", "model", "bones", "materials", "morphs", "commit"]
    restored = adapter.read_spec("|modelRoot")
    assert restored == spec
    assert restored.fingerprint() == spec.fingerprint()
    assert restored.model.name == "モデル_日本語"


def test_write_preserves_canonical_explicit_indices() -> None:
    backend = _backend()
    spec = _unsorted_spec()
    SceneMetadataAdapter(backend).write_spec("|modelRoot", spec)

    assert [entry["index"] for entry in backend.materials] == [1, 4]
    assert [entry["index"] for entry in backend.bones] == [0, 2]
    assert SceneMetadataAdapter(backend).read_spec("|modelRoot") == spec


def test_backend_mutating_write_payload_cannot_mutate_spec() -> None:
    class MutatingBackend(FakeBackend):
        def apply_model_metadata(self, model_root: str, metadata: dict[str, Any]) -> None:
            metadata["name"] = "backend mutation"
            super().apply_model_metadata(model_root, metadata)

        def apply_bone_metadata(self, model_root: str, metadata: list[Any]) -> None:
            metadata[0]["name"] = "backend mutation"
            super().apply_bone_metadata(model_root, metadata)

        def apply_morph_metadata(self, model_root: str, metadata: list[Any]) -> None:
            metadata[0]["offsets"][0]["vertex_index"] = 999
            super().apply_morph_metadata(model_root, metadata)

    backend = MutatingBackend(**_backend().__dict__)
    spec = SceneMetadataAdapter(backend).read_spec("|modelRoot")
    before = spec.fingerprint()
    SceneMetadataAdapter(backend).write_spec("|modelRoot", spec)

    assert spec.fingerprint() == before
    assert spec.model.name == "モデル_日本語"
    assert spec.morphs[0].offsets[0]["vertex_index"] == 2


@pytest.mark.parametrize("failed_section", ["materials", "commit"])
def test_write_failure_rolls_back_and_never_reports_success(failed_section: str) -> None:
    backend = _backend()
    original = deepcopy((backend.model, backend.bones, backend.materials, backend.morphs))
    backend.fail_section = failed_section
    spec = SceneMetadataAdapter(backend).read_spec("|modelRoot")
    backend.calls.clear()

    with pytest.raises(SceneMetadataError, match=failed_section):
        SceneMetadataAdapter(backend).write_spec("|modelRoot", spec)

    assert backend.calls[-1] == "rollback"
    assert (backend.model, backend.bones, backend.materials, backend.morphs) == original
    if failed_section == "materials":
        assert "commit" not in backend.calls
    else:
        assert "commit" in backend.calls


def test_invalid_write_input_causes_zero_backend_writes() -> None:
    backend = _backend()
    adapter = SceneMetadataAdapter(backend)
    spec = adapter.read_spec("|modelRoot")
    backend.calls.clear()

    with pytest.raises(SceneMetadataError):
        adapter.write_spec("", spec)
    with pytest.raises(SceneMetadataError):
        adapter.write_spec("|modelRoot", object())

    assert backend.calls == []


def _unsorted_spec() -> MmdModelAuthoringSpec:
    """Build an intentionally unsorted spec for write-order assertions."""
    return MmdModelAuthoringSpec(
        model=MmdModelSpec("モデル_日本語", "Model", "コメント", "Comment"),
        bones=(
            MmdBoneSpec(name="child", index=2, parent_index=0),
            MmdBoneSpec(name="root", index=0),
        ),
        materials=(MmdMaterialSpec(name="材質", index=4), MmdMaterialSpec(name="材質2", index=1)),
        morphs=(MmdMorphSpec(name="笑顔", index=3, offsets=({"vertex_index": 2},)),),
    )
