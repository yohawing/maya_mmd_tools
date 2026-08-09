"""Tests for the single-chunk structural Maya authoring coordinator."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from mmd_tools.adapters.maya_model_authoring_coordinator import (
    MayaModelAuthoringCoordinator,
    MayaModelAuthoringCoordinatorError,
)
from mmd_tools.core.model_authoring_spec import (
    MmdBoneSpec,
    MmdMaterialSpec,
    MmdModelAuthoringSpec,
    MmdModelSpec,
    MmdMorphSpec,
)
from mmd_tools.core.pmx_data.bone import PmxBoneFlag


def _spec() -> MmdModelAuthoringSpec:
    return MmdModelAuthoringSpec(
        model=MmdModelSpec("Model"),
        bones=(
            MmdBoneSpec("root", index=0, binding_identity="|root|root"),
            MmdBoneSpec("spare", index=1, binding_identity="|root|spare"),
        ),
        materials=(MmdMaterialSpec("Material", index=0, binding_identity="material0"),),
    )


class FakeBackend:
    def __init__(self, scene: MmdModelAuthoringSpec) -> None:
        self.scene = scene
        self.active = False
        self.snapshot: MmdModelAuthoringSpec | None = None
        self.payload: dict[str, Any] | None = None
        self.events: list[str] = []
        self.begin_count = 0
        self.rebase_count = 0
        self.commit_count = 0
        self.rollback_count = 0
        self.fail_section: str | None = None

    def begin_write(self, _root: str) -> None:
        if self.active:
            raise RuntimeError("nested transaction")
        self.active = True
        self.snapshot = self.scene
        self.payload = self.scene.to_mapping()
        self.begin_count += 1
        self.events.append("begin")

    def rebase_write_bindings(self, _root: str, target: MmdModelAuthoringSpec) -> None:
        assert self.active
        assert self.rebase_count == 0
        for scene_items, target_items in (
            (self.scene.bones, target.bones),
            (self.scene.materials, target.materials),
            (self.scene.morphs, target.morphs),
        ):
            assert {(item.index, item.binding_identity) for item in scene_items} == {
                (item.index, item.binding_identity) for item in target_items
            }
        self.payload = self.scene.to_mapping()
        self.rebase_count += 1
        self.events.append("rebase")

    def _apply(self, section: str, payload: Any) -> None:
        assert self.active and self.payload is not None
        self.events.append(f"apply:{section}")
        if self.fail_section == section:
            raise RuntimeError(f"failed {section}")
        self.payload[section] = payload

    def apply_model_metadata(self, _root: str, payload: Any) -> None:
        self._apply("model", payload)

    def apply_bone_metadata(self, _root: str, payload: Any) -> None:
        self._apply("bones", payload)

    def apply_material_metadata(self, _root: str, payload: Any) -> None:
        self._apply("materials", payload)

    def apply_morph_metadata(self, _root: str, payload: Any) -> None:
        self._apply("morphs", payload)

    def commit_write(self, _root: str) -> None:
        assert self.active and self.payload is not None
        self.scene = MmdModelAuthoringSpec.from_mapping(self.payload)
        self.active = False
        self.commit_count += 1
        self.events.append("commit")

    def rollback_write(self, _root: str) -> None:
        assert self.active and self.snapshot is not None
        self.scene = self.snapshot
        self.active = False
        self.rollback_count += 1
        self.events.append("rollback")


class FakeMetadataAdapter:
    def __init__(self, backend: FakeBackend) -> None:
        self.backend = backend

    def read_spec(self, _root: str) -> MmdModelAuthoringSpec:
        return self.backend.scene


class FakeMaterialAuthoring:
    def __init__(self, backend: FakeBackend) -> None:
        self.backend = backend
        self.assignments: list[tuple[int, tuple[str, ...]]] = []
        self.fail_create = False

    def create_material(self, _root: str, material: MmdMaterialSpec) -> tuple[MmdMaterialSpec, str, str]:
        if self.fail_create:
            raise RuntimeError("create failed")
        binding = f"material{material.index}"
        bound = replace(material, binding_identity=binding)
        self.backend.scene = replace(
            self.backend.scene,
            materials=self.backend.scene.materials + (bound,),
        )
        return bound, binding, f"{binding}SG"

    def resolve_material(self, _root: str, material: MmdMaterialSpec) -> tuple[str, str] | None:
        if material.binding_identity is None:
            return None
        return material.binding_identity, f"{material.binding_identity}SG"

    def assign_material(self, _root: str, material: MmdMaterialSpec, targets: tuple[str, ...]) -> None:
        self.assignments.append((material.index, targets))

    def replace_material(
        self,
        _root: str,
        _old: MmdModelAuthoringSpec,
        new: MmdModelAuthoringSpec,
    ) -> MmdModelAuthoringSpec:
        self.backend.scene = new
        return new

    def delete_material(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("delete must remain fail-closed")

    def apply_material_spec_change(
        self,
        _root: str,
        _old: MmdModelAuthoringSpec,
        new: MmdModelAuthoringSpec,
        replacement_shader: str,
    ) -> MmdModelAuthoringSpec:
        assert replacement_shader == new.materials[0].binding_identity
        self.backend.scene = new
        return new


class FakeBoneApi:
    def __init__(self, backend: FakeBackend) -> None:
        self.backend = backend
        self.events: list[str] = []
        self.fail_register = False

    def capture_rest_position(self, _root: str, joint: str, scale: float, _adapter: Any) -> tuple[float, ...]:
        self.events.append("capture")
        assert joint in {"|root|spare", "|root|newJoint"}
        assert scale == 2.0
        return (2.0, 3.0, 4.0)

    def register_existing_joint(self, _root: str, bone: MmdBoneSpec, _adapter: Any) -> None:
        if self.fail_register:
            raise RuntimeError("register failed")
        self.events.append("register")
        self.backend.scene = replace(
            self.backend.scene,
            bones=self.backend.scene.bones + (bone,),
        )

    def apply_bone_reindex(
        self,
        _root: str,
        _old: MmdModelAuthoringSpec,
        new: MmdModelAuthoringSpec,
        _adapter: Any,
    ) -> None:
        self.events.append("reindex")
        self.backend.scene = new

    def unregister_existing_joint(self, _root: str, joint: str, _adapter: Any) -> None:
        self.events.append("unregister")
        self.backend.scene = replace(
            self.backend.scene,
            bones=tuple(item for item in self.backend.scene.bones if item.binding_identity != joint),
        )


class FakeCmds:
    def __init__(self) -> None:
        self.nodes = {
            "|root",
            "|root|root",
            "|root|spare",
            "|root|newJoint",
            "newJoint",
        }
        self.positions: dict[str, tuple[float, float, float]] = {}

    def object_exists(self, node: str) -> bool:
        return node in self.nodes

    def ls(self, node: str, **kwargs: Any) -> list[str]:
        if kwargs.get("long") and node == "newJoint":
            return ["|root|newJoint"]
        return [node] if node in self.nodes else []

    def list_relatives(self, node: str, **_kwargs: Any) -> list[str]:
        if node in {"|root|spare", "|root|newJoint"}:
            return ["|root|root"]
        return []

    def xform(self, node: str, **kwargs: Any) -> None:
        self.positions[node] = tuple(kwargs["translation"])


class FakeMorphAuthoring:
    def __init__(self, backend: FakeBackend) -> None:
        self.backend = backend
        self.calls: list[tuple[MmdModelAuthoringSpec, MmdModelAuthoringSpec]] = []

    def __call__(
        self,
        _root: str,
        old_spec: MmdModelAuthoringSpec,
        new_spec: MmdModelAuthoringSpec,
    ) -> MmdModelAuthoringSpec:
        self.calls.append((old_spec, new_spec))
        bound = replace(
            new_spec,
            morphs=tuple(
                replace(morph, binding_identity=morph.binding_identity or f"morph{morph.index}")
                for morph in new_spec.morphs
            ),
        )
        self.backend.scene = bound
        return bound


def _coordinator() -> tuple[
    MayaModelAuthoringCoordinator,
    FakeBackend,
    FakeMaterialAuthoring,
    FakeBoneApi,
]:
    backend = FakeBackend(_spec())
    materials = FakeMaterialAuthoring(backend)
    bones = FakeBoneApi(backend)
    morphs = FakeMorphAuthoring(backend)
    coordinator = MayaModelAuthoringCoordinator(
        FakeMetadataAdapter(backend),
        backend,
        materials,
        FakeCmds(),
        bone_api=bones,
        morph_authoring=morphs,
        model_scale_resolver=lambda _root: 2.0,
    )
    return coordinator, backend, materials, bones


def _assert_one_successful_transaction(backend: FakeBackend) -> None:
    assert backend.begin_count == 1
    assert backend.rebase_count == 1
    assert backend.commit_count == 1
    assert backend.rollback_count == 0
    assert backend.events.count("begin") == 1
    assert backend.events[-1] == "commit"


def test_create_and_duplicate_material_generate_fresh_binding_identities() -> None:
    coordinator, backend, materials, _ = _coordinator()
    created = coordinator.create_material("|root")
    assert created.materials[-1].binding_identity == "material1"
    assert materials.assignments == []
    _assert_one_successful_transaction(backend)

    backend.rebase_count = 0
    duplicated = coordinator.duplicate_material("|root", 0)
    assert duplicated.materials[-1].binding_identity == "material2"
    assert duplicated.materials[-1].binding_identity != duplicated.materials[0].binding_identity
    assert materials.assignments == []
    assert backend.begin_count == 2
    assert backend.commit_count == 2


def test_create_and_duplicate_allow_registry_owned_unassigned_materials() -> None:
    coordinator, backend, materials, _ = _coordinator()
    created = coordinator.create_material("|root")
    assert created.materials[-1].binding_identity == "material1"
    assert materials.assignments == []

    backend.rebase_count = 0
    duplicated = coordinator.duplicate_material("|root", 0)
    assert duplicated.materials[-1].binding_identity == "material2"
    assert materials.assignments == []


def test_replace_material_uses_public_binding_api_in_one_transaction() -> None:
    coordinator, backend, _materials, _ = _coordinator()
    replacement = replace(backend.scene.materials[0], name="材質編集", diffuse=(0.2, 0.3, 0.4, 1.0))

    result = coordinator.replace_material("|root", replacement)

    assert result.materials[0].name == "材質編集"
    assert result.materials[0].binding_identity == "material0"
    _assert_one_successful_transaction(backend)


def test_delete_material_uses_injected_structural_change_and_one_transaction() -> None:
    coordinator, backend, _, _ = _coordinator()
    coordinator.create_material("|root")
    backend.rebase_count = 0

    result = coordinator.delete_material("|root", 0)

    assert len(result.materials) == 1
    assert result.materials[0].index == 0
    assert result.materials[0].binding_identity == "material1"
    assert backend.begin_count == 2
    assert backend.commit_count == 2


def test_delete_material_without_structural_api_fails_before_transaction() -> None:
    coordinator, backend, materials, _ = _coordinator()
    materials.apply_material_spec_change = None
    with pytest.raises(MayaModelAuthoringCoordinatorError, match="requires apply_material_spec_change"):
        coordinator.delete_material("|root", 0)
    assert backend.begin_count == 0
    assert backend.scene == _spec()


def test_register_bone_canonicalizes_binding_and_persists_generated_identity() -> None:
    coordinator, backend, _, bones = _coordinator()
    result = coordinator.register_bone(
        "|root",
        MmdBoneSpec("new", parent_index=0, binding_identity="newJoint"),
    )

    assert result.bones[-1].binding_identity == "|root|newJoint"
    assert bones.events == ["register"]
    _assert_one_successful_transaction(backend)


def test_register_selected_joint_uses_zero_rest_and_registered_parent() -> None:
    coordinator, backend, _, bones = _coordinator()
    result = coordinator.register_selected_joint("|root", "newJoint")

    registered = result.bones[-1]
    assert registered.name == "newJoint"
    assert registered.parent_index == 0
    assert registered.rest_position == (0.0, 0.0, 0.0)
    assert registered.binding_identity == "|root|newJoint"
    assert bones.events == ["register"]
    _assert_one_successful_transaction(backend)


def test_capture_rest_is_preflighted_before_begin_and_then_fully_applied() -> None:
    coordinator, backend, _, bones = _coordinator()
    result = coordinator.capture_rest("|root", 1, "|root|spare")

    assert bones.events == ["capture"]
    assert result.bones[1].rest_position == (2.0, 3.0, 4.0)
    assert backend.events[0] == "begin"
    _assert_one_successful_transaction(backend)


def test_replace_bone_updates_semantics_and_world_position_in_one_transaction() -> None:
    coordinator, backend, _, _ = _coordinator()
    replacement = replace(
        backend.scene.bones[1],
        name="edited",
        flags=0,
        tail_offset=(0.0, 2.0, 0.0),
    )

    result = coordinator.replace_bone("|root", replacement, (2.0, 4.0, -6.0))

    assert result.bones[1].name == "edited"
    assert result.bones[1].rest_position == (1.0, 2.0, 3.0)
    assert coordinator._cmds.positions["|root|spare"] == (2.0, 4.0, -6.0)
    _assert_one_successful_transaction(backend)


def test_replace_bone_semantic_preserves_rest_tail_and_does_not_xform() -> None:
    coordinator, backend, _, _ = _coordinator()
    original = replace(
        backend.scene.bones[1],
        flags=int(PmxBoneFlag.CONNECT_BONE),
        connect_bone_index=0,
        tail_offset=(0.0, 3.0, 0.0),
        rest_position=(4.0, 5.0, 6.0),
    )
    backend.scene = replace(backend.scene, bones=(backend.scene.bones[0], original))
    replacement = replace(
        original,
        name="edited",
        flags=0,
        connect_bone_index=None,
        tail_offset=(8.0, 9.0, 10.0),
        rest_position=(100.0, 100.0, 100.0),
    )

    result = coordinator.replace_bone_semantic("|root", replacement)

    edited = result.bones[1]
    assert edited.name == "edited"
    assert edited.rest_position == (4.0, 5.0, 6.0)
    assert edited.tail_offset == (0.0, 3.0, 0.0)
    assert edited.connect_bone_index == 0
    assert edited.flags & PmxBoneFlag.CONNECT_BONE
    assert coordinator._cmds.positions == {}
    _assert_one_successful_transaction(backend)


@pytest.mark.parametrize("invalid_scale", [True, 0.0, float("nan"), float("inf")])
def test_capture_rest_rejects_invalid_model_scale_before_write(invalid_scale: Any) -> None:
    coordinator, backend, _, _ = _coordinator()
    coordinator._model_scale_resolver = lambda _root: invalid_scale

    with pytest.raises(MayaModelAuthoringCoordinatorError, match="must be positive"):
        coordinator.capture_rest("|root", 1, "|root|spare")
    assert backend.begin_count == 0


def test_morph_crud_uses_injected_structural_writer_and_canonical_binding() -> None:
    coordinator, backend, _, _ = _coordinator()
    created = coordinator.create_morph(
        "|root",
        MmdMorphSpec("Smile", morph_type="bone", panel=1),
    )
    assert created.morphs[0].binding_identity == "morph0"
    assert coordinator.read_spec("|root") == created

    backend.rebase_count = 0
    replaced = coordinator.replace_morph(
        "|root",
        replace(created.morphs[0], name="Smile Wide"),
    )
    assert replaced.morphs[0].name == "Smile Wide"
    backend.rebase_count = 0
    deleted = coordinator.delete_morph("|root", 0)
    assert deleted.morphs == ()
    assert backend.begin_count == 3
    assert backend.commit_count == 3


def test_morph_change_without_structural_writer_fails_before_transaction() -> None:
    coordinator, backend, _, _ = _coordinator()
    coordinator._morphs = None
    with pytest.raises(MayaModelAuthoringCoordinatorError, match="structural writer"):
        coordinator.create_morph("|root", MmdMorphSpec("Smile", morph_type="bone"))
    assert backend.begin_count == 0


def test_morph_offsets_move_and_reindex_share_the_transaction_boundary() -> None:
    coordinator, backend, _, _ = _coordinator()
    coordinator.create_morph("|root", MmdMorphSpec("A", morph_type="bone"))
    backend.rebase_count = 0
    coordinator.create_morph("|root", MmdMorphSpec("B", morph_type="bone"))
    backend.rebase_count = 0

    updated = coordinator.replace_morph_offsets(
        "|root",
        0,
        (
            {
                "bone_index": 0,
                "translation": (1.0, 2.0, 3.0),
                "rotation": (0.0, 0.0, 0.0, 1.0),
            },
        ),
    )
    assert updated.morphs[0].offsets[0]["bone_index"] == 0
    backend.rebase_count = 0

    moved = coordinator.move_morph("|root", 0, 1)
    assert [morph.name for morph in moved.morphs] == ["B", "A"]
    backend.rebase_count = 0
    reindexed = coordinator.reindex_morphs("|root", (1, 0))
    assert [morph.name for morph in reindexed.morphs] == ["A", "B"]
    assert backend.begin_count == 5
    assert backend.commit_count == 5


def test_reindex_and_unregister_bones_use_structural_api_then_one_full_apply() -> None:
    coordinator, backend, _, bones = _coordinator()
    reindexed = coordinator.reindex_bones("|root", [1, 0])
    assert [item.binding_identity for item in reindexed.bones] == ["|root|spare", "|root|root"]
    assert bones.events == ["reindex"]
    _assert_one_successful_transaction(backend)

    backend.rebase_count = 0
    bones.events.clear()
    unregistered = coordinator.unregister_bone("|root", 0)
    assert [item.binding_identity for item in unregistered.bones] == ["|root|root"]
    assert bones.events == ["unregister", "reindex"]
    assert backend.begin_count == 2
    assert backend.commit_count == 2


def test_pure_preflight_failure_performs_no_maya_write() -> None:
    coordinator, backend, _, _ = _coordinator()
    with pytest.raises(MayaModelAuthoringCoordinatorError, match="preflight failed"):
        coordinator.reindex_bones("|root", [0])
    assert backend.begin_count == 0
    assert backend.events == []


@pytest.mark.parametrize("failure", ["structure", "materials"])
def test_structural_or_full_apply_failure_rolls_back_original_spec(failure: str) -> None:
    coordinator, backend, materials, _ = _coordinator()
    original = backend.scene
    if failure == "structure":
        materials.fail_create = True
    else:
        backend.fail_section = "materials"

    with pytest.raises(MayaModelAuthoringCoordinatorError, match="create_material failed"):
        coordinator.create_material("|root")

    assert backend.scene == original
    assert backend.begin_count == 1
    assert backend.commit_count == 0
    assert backend.rollback_count == 1
    assert backend.events[-1] == "rollback"


def test_nested_transaction_is_rejected_without_rolling_back_outer_owner() -> None:
    coordinator, backend, _, _ = _coordinator()
    backend.begin_write("|root")

    with pytest.raises(MayaModelAuthoringCoordinatorError, match="nested transaction"):
        coordinator.create_material("|root")

    assert backend.active is True
    assert backend.rollback_count == 0
    backend.rollback_write("|root")
