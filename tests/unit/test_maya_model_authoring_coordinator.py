"""Tests for the single-chunk structural Maya authoring coordinator."""

from __future__ import annotations

from dataclasses import dataclass, fields, make_dataclass, replace
from enum import Enum
from typing import Any

import pytest

from mmd_tools.adapters.maya_material_authoring import MaterialReindexResult
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
from mmd_tools.core.morph_topology import (
    MorphTopologyDiagnostic,
    MorphTopologyInspection,
)
from mmd_tools.core.morph_read_projection import (
    MorphAuthoringReadSnapshot,
    MorphBindingProjection,
    MorphBlendShapeReadProjection,
)
from mmd_tools.core.morph_binding_resolver import MorphBinding, MorphBindingWarning
from mmd_tools.core.material_read_projection import (
    MaterialAssignmentKind,
    MaterialAssignmentSummary,
    MaterialDetailProjection,
    MaterialListItemProjection,
    MaterialListProjection,
    MaterialListSemantic,
    MaterialPreviewState,
)


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
        self.display_payload = "old"
        self.topology_source = "{}"
        self.topology_fail_commit = False
        self.info_session = None
        self.info_value = "old"
        self.info_fail_update = False
        self.info_fail_rollback_pending = None
        self.morph_snapshot = MorphAuthoringReadSnapshot(
            spec=scene,
            projection=MorphBlendShapeReadProjection(
                root_identity="|root",
                controller_identity="controller",
                owned_mesh_identities=(),
                owned_blend_shape_identities=(),
                morphs=(),
            ),
            topology_inspection=MorphTopologyInspection({}, {}, ()),
        )
        self.material_list_projection = MaterialListProjection("|root", ())
        self.material_detail_projection = MaterialDetailProjection(
            "|root",
            scene.materials[0],
            MaterialAssignmentSummary(MaterialAssignmentKind.EMPTY, 0, 0),
            (),
            MaterialPreviewState("lambert", False),
        )

    def read_material_list_projection(self, _root: str) -> MaterialListProjection:
        self.events.append("read:material_list_projection")
        return self.material_list_projection

    def read_material_detail_projection(self, _root, _index, _binding, _assignment):
        self.events.append("read:material_detail_projection")
        return self.material_detail_projection

    def read_morph_authoring_snapshot(self, _root: str) -> MorphAuthoringReadSnapshot:
        self.events.append("read:morph_snapshot")
        return self.morph_snapshot

    def begin_info_metadata_edit(self, root: str, attr: str) -> Any:
        self.info_session = type(
            "InfoSession", (), {"root": root, "attr": attr, "token": object()}
        )()
        self.events.append("begin:info")
        return self.info_session

    def apply_info_metadata_edit(self, root: str, session: Any, value: str) -> bool:
        assert root == session.root and session is self.info_session
        self.events.append("apply:info")
        if self.info_fail_update:
            raise RuntimeError("injected info update failure")
        self.info_value = value
        return value != "old"

    def commit_info_metadata_edit(self, root: str, session: Any) -> bool:
        assert root == session.root and session is self.info_session
        self.events.append("commit:info")
        self.info_session = None
        return self.info_value != "old"

    def rollback_info_metadata_edit(self, root: str, session: Any) -> None:
        assert root == session.root
        self.events.append("rollback:info")
        if self.info_fail_rollback_pending is not None:
            error = RuntimeError("injected info rollback failure")
            error.rollback_pending = self.info_fail_rollback_pending
            raise error
        self.info_session = None
        self.info_value = "old"

    def inspect_morph_topology(self, _root: str) -> MorphTopologyInspection:
        expected = {"1": ((0, 0.5),)}
        if self.topology_source == '{"1":[[0,0.5]]}':
            return MorphTopologyInspection(expected, expected, ())
        return MorphTopologyInspection(
            expected,
            {},
            (MorphTopologyDiagnostic("stale", "stale"),),
        )

    def begin_morph_topology_repair(self, _root: str, source: str) -> None:
        self.active = True
        self.snapshot = self.scene
        self._topology_snapshot = self.topology_source
        self._topology_target = source
        self.events.append("begin:topology")

    def apply_morph_topology_repair(self, _root: str, source: str) -> str:
        assert self.active and source == self._topology_target
        self.topology_source = source
        self.events.append("apply:topology")
        return source

    def commit_morph_topology_repair(self, _root: str, result: str) -> None:
        self.events.append("commit:topology")
        if self.topology_fail_commit:
            raise RuntimeError("topology readback failed")
        assert result == self.topology_source
        self.active = False

    def begin_write(self, _root: str) -> None:
        if self.active:
            raise RuntimeError("nested transaction")
        self.active = True
        self.snapshot = self.scene
        self.payload = self.scene.to_mapping()
        self.begin_count += 1
        self.events.append("begin")

    def begin_display_frames_write(self, _root: str) -> None:
        if self.active:
            raise RuntimeError("nested transaction")
        self.active = True
        self.snapshot = self.scene
        self.events.append("begin:display")

    def apply_display_frames_write(self, _root: str, payload: str) -> None:
        assert self.active
        self.events.append("apply:display")
        if self.fail_section == "display":
            raise RuntimeError("failed display")
        self.display_payload = payload

    def commit_display_frames_write(self, _root: str, payload: str) -> None:
        assert self.active
        assert self.display_payload == payload
        self.active = False
        self.events.append("commit:display")

    def begin_material_reindex(self, _root: str, _index: int, _new_position: int) -> None:
        if self.active:
            raise RuntimeError("nested transaction")
        self.active = True
        self.snapshot = self.scene
        self.begin_count += 1
        self.events.append("begin:material_reindex")

    def begin_bone_value_patch(
        self,
        _root: str,
        _binding: str,
        _old: MmdBoneSpec,
        _new: MmdBoneSpec,
    ) -> None:
        if self.active:
            raise RuntimeError("nested transaction")
        self.active = True
        self.snapshot = self.scene
        self.events.append("begin:bone_value")

    def begin_morph_value_patch(
        self,
        _root: str,
        _binding: str,
        _old: MmdMorphSpec,
        _new: MmdMorphSpec,
    ) -> None:
        if self.active:
            raise RuntimeError("nested transaction")
        self.active = True
        self.snapshot = self.scene
        self.events.append("begin:morph_value")

    def begin_bone_register(self, _root: str, _bone: MmdBoneSpec) -> None:
        if self.active:
            raise RuntimeError("nested transaction")
        self.active = True
        self.snapshot = self.scene
        self.events.append("begin:bone_register")

    def begin_material_create(self, _root: str, _index: int) -> None:
        if self.active:
            raise RuntimeError("nested transaction")
        self.active = True
        self.snapshot = self.scene
        self.events.append("begin:material_create")
        self.begin_count += 1

    def begin_morph_create(self, _root: str, morph: MmdMorphSpec) -> int:
        if self.active:
            raise RuntimeError("nested transaction")
        self.active = True
        self.snapshot = self.scene
        self.events.append("begin:morph_create")
        self.begin_count += 1
        return len(self.scene.morphs)

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
        if hasattr(self, "_topology_snapshot"):
            self.topology_source = self._topology_snapshot
        self.scene = self.snapshot
        self.active = False
        self.rollback_count += 1
        self.events.append("rollback")


class FakeMetadataAdapter:
    def __init__(self, backend: FakeBackend) -> None:
        self.backend = backend

    def read_spec(self, _root: str) -> MmdModelAuthoringSpec:
        return self.backend.scene

    def read_bone_value(self, _root: str, binding: str, index: int) -> MmdBoneSpec:
        return next(
            bone
            for bone in self.backend.scene.bones
            if bone.binding_identity == binding and bone.index == index
        )

    def commit_bone_value_patch(self, _root: str, _binding: str, bone: MmdBoneSpec) -> None:
        assert self.backend.active
        self.backend.scene = replace(
            self.backend.scene,
            bones=tuple(bone if item.index == bone.index else item for item in self.backend.scene.bones),
        )
        self.backend.active = False
        self.backend.commit_count += 1
        self.backend.events.append("commit:bone_value")

    def read_morph_value(self, _root: str, binding: str, index: int) -> MmdMorphSpec:
        return next(
            morph
            for morph in self.backend.scene.morphs
            if morph.binding_identity == binding and morph.index == index
        )

    def commit_morph_value_patch(
        self,
        _root: str,
        _binding: str,
        morph: MmdMorphSpec,
    ) -> None:
        assert self.backend.active
        self.backend.scene = replace(
            self.backend.scene,
            morphs=tuple(
                morph if item.index == morph.index else item
                for item in self.backend.scene.morphs
            ),
        )
        self.backend.active = False
        self.backend.commit_count += 1
        self.backend.events.append("commit:morph_value")

    def commit_bone_register(self, _root: str, bone: MmdBoneSpec) -> None:
        assert self.backend.active
        self.backend.scene = replace(
            self.backend.scene,
            bones=self.backend.scene.bones + (bone,),
        )
        self.backend.active = False
        self.backend.commit_count += 1
        self.backend.events.append("commit:bone_register")

    def commit_material_reindex(self, _root: str, result: MaterialReindexResult) -> None:
        assert self.backend.active
        assert (result.first_index, result.second_index) == (0, 1)
        self.backend.active = False
        self.backend.commit_count += 1
        self.backend.events.append("commit:material_reindex")

    def commit_material_create(self, _root: str, material: MmdMaterialSpec) -> None:
        assert self.backend.active
        self.backend.scene = replace(
            self.backend.scene,
            materials=self.backend.scene.materials + (material,),
        )
        self.backend.active = False
        self.backend.commit_count += 1
        self.backend.events.append("commit:material_create")

    def commit_morph_create(self, _root: str, morph: MmdMorphSpec) -> None:
        assert self.backend.active
        self.backend.scene = replace(
            self.backend.scene,
            morphs=self.backend.scene.morphs + (morph,),
        )
        self.backend.active = False
        self.backend.commit_count += 1
        self.backend.events.append("commit:morph_create")

    def next_material_index(self, _root: str) -> int:
        return max((item.index for item in self.backend.scene.materials), default=-1) + 1

    def read_material_value_by_index(self, _root: str, index: int) -> MmdMaterialSpec:
        return next(item for item in self.backend.scene.materials if item.index == index)


@dataclass(frozen=True)
class _ReloadGenerationSpec:
    """Old module-generation shape used to reproduce Maya UI reload drift."""

    model: Any
    bones: tuple[Any, ...] = ()
    materials: tuple[Any, ...] = ()
    morphs: tuple[Any, ...] = ()
    schema_version: int = 1

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "model": self.model.to_mapping(),
            "bones": [item.to_mapping() for item in self.bones],
            "materials": [item.to_mapping() for item in self.materials],
            "morphs": [item.to_mapping() for item in self.morphs],
        }


_ReloadGenerationSpec.__module__ = MmdModelAuthoringSpec.__module__
_ReloadGenerationSpec.__qualname__ = MmdModelAuthoringSpec.__qualname__


def _old_projection_dataclass(current_type):
    """Build a strict previous-generation shape for reload boundary tests."""

    old_type = make_dataclass(
        current_type.__name__,
        [(field.name, field.type) for field in fields(current_type)],
        frozen=True,
    )
    old_type.__module__ = current_type.__module__
    old_type.__qualname__ = current_type.__qualname__
    old_type.projection_schema_version = getattr(
        current_type,
        "projection_schema_version",
        None,
    )
    return old_type


class FakeMaterialAuthoring:
    def __init__(self, backend: FakeBackend) -> None:
        self.backend = backend
        self.assignments: list[tuple[int, tuple[str, ...]]] = []
        self.fail_create = False

    def create_material(
        self,
        _root: str,
        material: MmdMaterialSpec,
        *,
        narrow: bool = False,
    ) -> tuple[MmdMaterialSpec, str, str]:
        if self.fail_create:
            raise RuntimeError("create failed")
        binding = f"material{material.index}"
        bound = replace(material, binding_identity=binding)
        if not narrow:
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
        if len(new.materials) < len(_old.materials):
            assert replacement_shader == new.materials[0].binding_identity
        else:
            assert replacement_shader is None
        self.backend.scene = new
        return new

    def apply_material_reindex(
        self,
        _root: str,
        _old: MmdModelAuthoringSpec,
        new: MmdModelAuthoringSpec,
    ) -> MmdModelAuthoringSpec:
        self.backend.scene = new
        return new

    def apply_material_reindex_fast(
        self,
        _root: str,
        index: int,
        new_position: int,
    ) -> MaterialReindexResult:
        self.backend.scene = replace(
            self.backend.scene,
            materials=tuple(
                replace(
                    item,
                    index=(
                        new_position
                        if item.index == index
                        else index
                        if item.index == new_position
                        else item.index
                    ),
                )
                for item in self.backend.scene.materials
            ),
        )
        return MaterialReindexResult(*sorted((index, new_position)))


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

    def apply_bone_value_patch(
        self,
        _root: str,
        _old: MmdBoneSpec,
        new: MmdBoneSpec,
        _adapter: Any,
    ) -> MmdBoneSpec:
        self.events.append("patch")
        self.backend.scene = replace(
            self.backend.scene,
            bones=tuple(new if item.index == new.index else item for item in self.backend.scene.bones),
        )
        return new

    def prepare_selected_joint_registration(self, _root: str, joint: str, _adapter: Any) -> MmdBoneSpec:
        self.events.append("prepare_register")
        return MmdBoneSpec(
            "newJoint",
            index=2,
            parent_index=0,
            rest_position=(0.0, 0.0, 0.0),
            tail_offset=(0.0, 0.0, 0.0),
            binding_identity=joint,
        )

    def register_selected_joint(self, _root: str, bone: MmdBoneSpec, _adapter: Any) -> MmdBoneSpec:
        if self.fail_register:
            raise RuntimeError("register failed")
        self.events.append("register")
        return bone

    def register_existing_joint(self, _root: str, bone: MmdBoneSpec, _adapter: Any) -> None:
        if self.fail_register:
            raise RuntimeError("register failed")
        self.events.append("register")

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

    def apply_morph_create(self, _root: str, morph: MmdMorphSpec, _cmds: Any) -> MmdMorphSpec:
        return replace(morph, binding_identity=f"morph{morph.index}")

    def apply_morph_value_patch(
        self,
        _root: str,
        _old: MmdMorphSpec,
        new: MmdMorphSpec,
        _cmds: Any,
    ) -> MmdMorphSpec:
        self.backend.scene = replace(
            self.backend.scene,
            morphs=tuple(
                new if item.index == new.index else item
                for item in self.backend.scene.morphs
            ),
        )
        return new


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


def test_info_metadata_session_uses_only_narrow_backend_methods() -> None:
    coordinator, backend, _, _ = _coordinator()
    session = coordinator.begin_info_metadata_edit("|root", "mmd_name")
    assert coordinator.update_info_metadata_edit(session, "new") is True
    assert coordinator.commit_info_metadata_edit(session) is True
    assert backend.events == ["begin:info", "apply:info", "commit:info"]
    assert backend.begin_count == 0


def test_info_metadata_update_failure_rolls_back_exactly_once() -> None:
    coordinator, backend, _, _ = _coordinator()
    session = coordinator.begin_info_metadata_edit("|root", "mmd_name")
    backend.info_fail_update = True
    with pytest.raises(MayaModelAuthoringCoordinatorError, match="update_info_metadata_edit"):
        coordinator.update_info_metadata_edit(session, "new")
    assert backend.events == ["begin:info", "apply:info", "rollback:info"]


def test_explicit_info_rollback_preserves_terminal_state() -> None:
    coordinator, backend, _, _ = _coordinator()
    session = coordinator.begin_info_metadata_edit("|root", "mmd_name")
    backend.info_fail_rollback_pending = False
    with pytest.raises(MayaModelAuthoringCoordinatorError) as caught:
        coordinator.rollback_info_metadata_edit(session)
    assert caught.value.rollback_pending is False
    assert caught.value.rollback_verified is False


def test_repair_morph_topology_uses_explicit_transaction_and_returns_clean_inspection() -> None:
    coordinator, backend, _, _ = _coordinator()

    result = coordinator.repair_morph_topology("|root")

    assert result.valid
    assert backend.topology_source == '{"1":[[0,0.5]]}'
    assert backend.events[-3:] == [
        "begin:topology",
        "apply:topology",
        "commit:topology",
    ]


def test_repair_morph_topology_commit_failure_rolls_back_exactly_once() -> None:
    coordinator, backend, _, _ = _coordinator()
    backend.topology_fail_commit = True

    with pytest.raises(MayaModelAuthoringCoordinatorError, match="verify/commit"):
        coordinator.repair_morph_topology("|root")

    assert backend.topology_source == "{}"
    assert backend.rollback_count == 1


def test_read_spec_rehydrates_a_strict_previous_module_generation() -> None:
    coordinator, backend, _, _ = _coordinator()
    current = backend.scene
    backend.scene = _ReloadGenerationSpec(
        model=current.model,
        bones=current.bones,
        materials=current.materials,
        morphs=current.morphs,
    )

    result = coordinator.read_spec("|root")

    assert type(result) is MmdModelAuthoringSpec
    assert result.to_mapping() == current.to_mapping()


def test_read_morph_authoring_snapshot_delegates_one_combined_generation() -> None:
    coordinator, backend, _, _ = _coordinator()

    result = coordinator.read_morph_authoring_snapshot("|root")

    assert result is backend.morph_snapshot
    assert backend.events == ["read:morph_snapshot"]


def test_read_morph_authoring_snapshot_rehydrates_strict_previous_generation() -> None:
    coordinator, backend, _, _ = _coordinator()
    current = backend.scene
    morph = MmdMorphSpec(
        "Morph",
        index=0,
        morph_type="vertex",
        binding_identity="morph0",
    )
    old_spec = _ReloadGenerationSpec(
        model=current.model,
        bones=current.bones,
        materials=current.materials,
        morphs=(morph,),
    )
    old_binding = _old_projection_dataclass(MorphBinding)(
        "Morph",
        0,
        "blendShape",
        "Morph",
        0,
        "blendShape.weight[0]",
        "controller",
        0,
    )
    old_warning = _old_projection_dataclass(MorphBindingWarning)(
        "legacy",
        "legacy alias",
    )
    old_morph = _old_projection_dataclass(MorphBindingProjection)(
        "Morph",
        0,
        "morph0",
        (old_binding,),
        (old_warning,),
        ("controller.inputWeight[0]",),
        True,
        "",
        True,
    )
    old_projection = _old_projection_dataclass(MorphBlendShapeReadProjection)(
        "|root",
        "controller",
        (),
        ("blendShape",),
        (old_morph,),
        (),
    )
    old_topology = _old_projection_dataclass(MorphTopologyInspection)({}, {}, ())
    backend.morph_snapshot = _old_projection_dataclass(MorphAuthoringReadSnapshot)(
        old_spec,
        old_projection,
        old_topology,
    )

    result = coordinator.read_morph_authoring_snapshot("|root")

    assert type(result) is MorphAuthoringReadSnapshot
    assert type(result.spec) is MmdModelAuthoringSpec
    assert type(result.projection) is MorphBlendShapeReadProjection
    assert type(result.projection.morphs[0]) is MorphBindingProjection
    assert type(result.projection.morphs[0].bindings[0]) is MorphBinding
    assert type(result.projection.morphs[0].warnings[0]) is MorphBindingWarning
    assert type(result.topology_inspection) is MorphTopologyInspection
    assert result.projection.morphs[0].binding_identity == "morph0"
    assert result.spec.morphs[0].binding_identity == "morph0"


def test_read_morph_authoring_snapshot_rejects_schema_drift_and_wrong_root() -> None:
    coordinator, backend, _, _ = _coordinator()
    old_snapshot = _old_projection_dataclass(MorphAuthoringReadSnapshot)
    old_snapshot.projection_schema_version = 999
    backend.morph_snapshot = old_snapshot(
        backend.morph_snapshot.spec,
        backend.morph_snapshot.projection,
        backend.morph_snapshot.topology_inspection,
    )

    with pytest.raises(
        MayaModelAuthoringCoordinatorError,
        match="morph authoring snapshot reader returned an invalid result",
    ):
        coordinator.read_morph_authoring_snapshot("|root")

    backend.morph_snapshot = MorphAuthoringReadSnapshot(
        backend.morph_snapshot.spec,
        replace(backend.morph_snapshot.projection, root_identity="|other"),
        backend.morph_snapshot.topology_inspection,
    )
    with pytest.raises(
        MayaModelAuthoringCoordinatorError,
        match="morph authoring snapshot returned the wrong root",
    ):
        coordinator.read_morph_authoring_snapshot("|root")


def test_read_material_list_projection_delegates_one_typed_generation() -> None:
    coordinator, backend, _, _ = _coordinator()

    result = coordinator.read_material_list_projection("|root")

    assert result is backend.material_list_projection
    assert backend.events == ["read:material_list_projection"]


def test_read_material_list_projection_rehydrates_strict_previous_generation() -> None:
    coordinator, backend, _, _ = _coordinator()
    old_kind = Enum(
        "MaterialAssignmentKind",
        {member.name: member.value for member in MaterialAssignmentKind},
        module=MaterialAssignmentKind.__module__,
    )
    old_kind.__qualname__ = MaterialAssignmentKind.__qualname__
    old_assignment = _old_projection_dataclass(MaterialAssignmentSummary)
    old_semantic = _old_projection_dataclass(MaterialListSemantic)
    old_item = _old_projection_dataclass(MaterialListItemProjection)
    old_projection = _old_projection_dataclass(MaterialListProjection)

    backend.material_list_projection = old_projection(
        "|root",
        (
            old_item(
                old_semantic(0, "material0", "Material", "Material EN"),
                old_assignment(old_kind.EXPLICIT_FACES, 1, 2),
            ),
        ),
    )

    result = coordinator.read_material_list_projection("|root")

    assert type(result) is MaterialListProjection
    assert type(result.items[0]) is MaterialListItemProjection
    assert type(result.items[0].semantic) is MaterialListSemantic
    assert type(result.items[0].assignment) is MaterialAssignmentSummary
    assert result.root_identity == "|root"
    assert result.items[0].binding_identity == "material0"
    assert result.items[0].assignment == MaterialAssignmentSummary(
        MaterialAssignmentKind.EXPLICIT_FACES,
        1,
        2,
    )


def test_read_material_list_projection_rejects_schema_drift_and_wrong_root() -> None:
    coordinator, backend, _, _ = _coordinator()
    old_projection = _old_projection_dataclass(MaterialListProjection)
    old_projection.projection_schema_version = 999
    backend.material_list_projection = old_projection("|root", ())

    with pytest.raises(
        MayaModelAuthoringCoordinatorError,
        match="material list projection reader returned an invalid result",
    ):
        coordinator.read_material_list_projection("|root")

    backend.material_list_projection = MaterialListProjection("|other", ())
    with pytest.raises(
        MayaModelAuthoringCoordinatorError,
        match="material list projection returned the wrong root",
    ):
        coordinator.read_material_list_projection("|root")


def test_read_material_detail_projection_rehydrates_strict_previous_generation() -> None:
    coordinator, backend, _, _ = _coordinator()
    current_material = backend.scene.materials[0]
    old_kind = Enum(
        "MaterialAssignmentKind",
        {member.name: member.value for member in MaterialAssignmentKind},
        module=MaterialAssignmentKind.__module__,
    )
    old_kind.__qualname__ = MaterialAssignmentKind.__qualname__
    old_assignment = _old_projection_dataclass(MaterialAssignmentSummary)
    old_preview = _old_projection_dataclass(MaterialPreviewState)
    old_material = _old_projection_dataclass(MmdMaterialSpec)

    def to_mapping(value):
        return {field.name: getattr(value, field.name) for field in fields(MmdMaterialSpec)}

    old_material.to_mapping = to_mapping
    old_detail = _old_projection_dataclass(MaterialDetailProjection)
    backend.material_detail_projection = old_detail(
        "|root",
        old_material(
            **{
                field.name: getattr(current_material, field.name)
                for field in fields(MmdMaterialSpec)
            }
        ),
        old_assignment(old_kind.EMPTY, 0, 0),
        (),
        old_preview("lambert", False),
    )

    result = coordinator.read_material_detail_projection(
        "|root",
        0,
        "material0",
        MaterialAssignmentSummary(MaterialAssignmentKind.EMPTY, 0, 0),
    )

    assert type(result) is MaterialDetailProjection
    assert type(result.material) is MmdMaterialSpec
    assert type(result.assignment) is MaterialAssignmentSummary
    assert type(result.preview) is MaterialPreviewState
    assert result.material.binding_identity == "material0"


def test_read_material_detail_projection_delegates_and_checks_identity() -> None:
    coordinator, backend, *_ = _coordinator()
    assignment = MaterialAssignmentSummary(MaterialAssignmentKind.EMPTY, 0, 0)

    result = coordinator.read_material_detail_projection(
        "|root", 0, "material0", assignment
    )

    assert result is backend.material_detail_projection
    assert backend.events == ["read:material_detail_projection"]

    backend.material_detail_projection = MaterialDetailProjection(
        "|other",
        backend.scene.materials[0],
        assignment,
        (),
        MaterialPreviewState("lambert", False),
    )
    with pytest.raises(MayaModelAuthoringCoordinatorError, match="wrong binding"):
        coordinator.read_material_detail_projection(
            "|root", 0, "material0", assignment
        )


def test_write_display_frames_uses_only_narrow_transaction() -> None:
    coordinator, backend, _, _ = _coordinator()

    result = coordinator.write_display_frames("|root", "payload")

    assert result == "payload"
    assert backend.display_payload == "payload"
    assert backend.events == ["begin:display", "apply:display", "commit:display"]
    assert backend.begin_count == 0


def test_write_display_frames_rolls_back_apply_failure() -> None:
    coordinator, backend, _, _ = _coordinator()
    backend.fail_section = "display"

    with pytest.raises(MayaModelAuthoringCoordinatorError, match="write_display_frames"):
        coordinator.write_display_frames("|root", "payload")

    assert backend.events == ["begin:display", "apply:display", "rollback"]
    assert backend.rollback_count == 1


def test_read_spec_rejects_reload_generation_with_schema_drift() -> None:
    coordinator, backend, _, _ = _coordinator()
    current = backend.scene
    backend.scene = _ReloadGenerationSpec(
        model=current.model,
        bones=current.bones,
        materials=current.materials,
        morphs=current.morphs,
        schema_version=999,
    )

    with pytest.raises(
        MayaModelAuthoringCoordinatorError,
        match="reload-spec normalization failed.*unsupported schema_version",
    ):
        coordinator.read_spec("|root")


def test_read_spec_rejects_unrelated_duck_typed_value() -> None:
    coordinator, backend, _, _ = _coordinator()
    backend.scene = type("DuckSpec", (), {"to_mapping": _spec().to_mapping})()

    with pytest.raises(MayaModelAuthoringCoordinatorError, match="invalid spec.*DuckSpec"):
        coordinator.read_spec("|root")


def test_create_and_duplicate_material_generate_fresh_binding_identities() -> None:
    coordinator, backend, materials, _ = _coordinator()
    created = coordinator.create_material("|root")
    assert created.binding_identity == "material1"
    assert materials.assignments == []
    assert backend.events == ["begin:material_create", "commit:material_create"]

    backend.rebase_count = 0
    duplicated = coordinator.duplicate_material("|root", 0)
    assert duplicated.binding_identity == "material2"
    assert duplicated.binding_identity != backend.scene.materials[0].binding_identity
    assert materials.assignments == []
    assert backend.begin_count == 2
    assert backend.commit_count == 2


def test_create_and_duplicate_allow_registry_owned_unassigned_materials() -> None:
    coordinator, backend, materials, _ = _coordinator()
    created = coordinator.create_material("|root")
    assert created.binding_identity == "material1"
    assert materials.assignments == []

    backend.rebase_count = 0
    duplicated = coordinator.duplicate_material("|root", 0)
    assert duplicated.binding_identity == "material2"
    assert materials.assignments == []


def test_replace_material_uses_public_binding_api_in_one_transaction() -> None:
    coordinator, backend, _materials, _ = _coordinator()
    replacement = replace(backend.scene.materials[0], name="材質編集", diffuse=(0.2, 0.3, 0.4, 1.0))

    result = coordinator.replace_material("|root", replacement)

    assert result.materials[0].name == "材質編集"
    assert result.materials[0].binding_identity == "material0"
    _assert_one_successful_transaction(backend)


def test_reindex_materials_uses_one_binding_transaction() -> None:
    coordinator, backend, _, _ = _coordinator()
    coordinator.create_material("|root")
    backend.rebase_count = 0

    result = coordinator.reindex_materials("|root", (1, 0))

    assert [(item.index, item.binding_identity) for item in result.materials] == [
        (0, "material1"),
        (1, "material0"),
    ]
    assert backend.begin_count == 2
    assert backend.rebase_count == 1
    assert backend.commit_count == 2


def test_move_material_preserves_full_spec_return_contract() -> None:
    coordinator, backend, _materials, _ = _coordinator()
    coordinator.create_material("|root")

    result = coordinator.move_material("|root", 1, 0)

    assert isinstance(result, MmdModelAuthoringSpec)
    assert [(item.index, item.binding_identity) for item in result.materials] == [
        (0, "material1"),
        (1, "material0"),
    ]


def test_move_material_uses_narrow_transaction_without_full_metadata_hooks() -> None:
    coordinator, backend, _materials, _ = _coordinator()
    coordinator.create_material("|root")
    backend.events.clear()
    backend.rebase_count = 0

    result = coordinator.move_material_fast("|root", 1, 0)

    assert (result.first_index, result.second_index) == (0, 1)
    assert [(item.index, item.binding_identity) for item in backend.scene.materials] == [
        (0, "material1"),
        (1, "material0"),
    ]
    assert backend.events == ["begin:material_reindex", "commit:material_reindex"]
    assert backend.rebase_count == 0


def test_move_material_uses_specialized_transaction_without_reading_spec() -> None:
    coordinator, backend, materials, _ = _coordinator()
    coordinator.create_material("|root")
    original = backend.scene
    calls: list[str] = []

    def begin(_root: str, _index: int, _new_position: int) -> None:
        backend.active = True
        backend.snapshot = backend.scene
        calls.append("begin")

    def write(_root: str, index: int, new_position: int) -> MaterialReindexResult:
        calls.append("write")
        backend.scene = replace(
            backend.scene,
            materials=tuple(
                replace(item, index=new_position if item.index == index else index if item.index == new_position else item.index)
                for item in backend.scene.materials
            ),
        )
        return MaterialReindexResult(*sorted((index, new_position)))

    def commit(_root: str, _result: Any) -> None:
        assert backend.active
        backend.active = False
        calls.append("commit")

    backend.begin_material_reindex = begin  # type: ignore[attr-defined]
    materials.apply_material_reindex_fast = write  # type: ignore[attr-defined]
    coordinator._metadata.commit_material_reindex = commit  # type: ignore[attr-defined]
    coordinator._metadata.read_spec = lambda _root: (_ for _ in ()).throw(AssertionError("full read"))  # type: ignore[method-assign]

    result = coordinator.move_material_fast("|root", 0, 1)

    assert calls == ["begin", "write", "commit"]
    assert result.first_index == 0
    assert backend.scene != original
    assert not backend.active


def test_move_material_specialized_write_failure_rolls_back() -> None:
    coordinator, backend, materials, _ = _coordinator()
    coordinator.create_material("|root")
    original = backend.scene

    def begin(_root: str, _index: int, _new_position: int) -> None:
        backend.active = True
        backend.snapshot = backend.scene

    def write(_root: str, _index: int, _new_position: int) -> tuple[int, int]:
        backend.scene = replace(backend.scene, materials=())
        raise RuntimeError("write failed")

    backend.begin_material_reindex = begin  # type: ignore[attr-defined]
    materials.apply_material_reindex_fast = write  # type: ignore[attr-defined]
    coordinator._metadata.commit_material_reindex = lambda *_args: None  # type: ignore[attr-defined]

    with pytest.raises(MayaModelAuthoringCoordinatorError, match="write failed"):
        coordinator.move_material_fast("|root", 0, 1)

    assert backend.scene == original
    assert backend.rollback_count == 1


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
        MmdBoneSpec("new", index=2, parent_index=0, binding_identity="newJoint"),
    )

    assert result.binding_identity == "|root|newJoint"
    assert bones.events == ["register"]
    assert backend.events == ["begin:bone_register", "commit:bone_register"]


def test_register_selected_joint_uses_zero_rest_and_registered_parent() -> None:
    coordinator, backend, _, bones = _coordinator()
    result = coordinator.register_selected_joint("|root", "newJoint")

    registered = result
    assert registered.name == "newJoint"
    assert registered.parent_index == 0
    assert registered.rest_position == (0.0, 0.0, 0.0)
    assert registered.binding_identity == "|root|newJoint"
    assert bones.events == ["prepare_register", "register"]
    assert backend.events == ["begin:bone_register", "commit:bone_register"]
    assert backend.rollback_count == 0


def test_capture_rest_is_preflighted_before_begin_and_then_fully_applied() -> None:
    coordinator, backend, _, bones = _coordinator()
    result = coordinator.capture_rest("|root", 1, "|root|spare")

    assert bones.events == ["capture", "patch"]
    assert result.rest_position == (2.0, 3.0, 4.0)
    assert backend.events == ["begin:bone_value", "commit:bone_value"]
    assert backend.rollback_count == 0


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
    assert backend.rebase_count == 0
    assert backend.events == ["begin:bone_value", "commit:bone_value"]
    assert backend.rollback_count == 0


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
    assert created.binding_identity == "morph0"
    assert coordinator.read_spec("|root").morphs[0] == created

    backend.rebase_count = 0
    replaced = coordinator.replace_morph(
        "|root",
        replace(created, name="Smile Wide"),
    )
    assert replaced.morphs[0].name == "Smile Wide"
    assert backend.rebase_count == 0
    backend.rebase_count = 0
    deleted = coordinator.delete_morph("|root", 0)
    assert deleted.morphs == ()
    assert backend.begin_count == 2
    assert backend.commit_count == 3


def test_replace_morph_numeric_offsets_uses_selected_narrow_transaction() -> None:
    coordinator, backend, _, _ = _coordinator()
    original = MmdMorphSpec(
        "Weighted",
        index=0,
        morph_type="bone",
        binding_identity="morph0",
        offsets=(
            {
                "bone_index": 0,
                "translation": (0.0, 0.0, 0.0),
                "rotation": (0.0, 0.0, 0.0, 1.0),
            },
        ),
    )
    backend.scene = replace(backend.scene, morphs=(original,))
    backend.events.clear()
    backend.rebase_count = 0

    result = coordinator.replace_morph_offsets(
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

    assert result.morphs[0].offsets[0]["translation"] == (1.0, 2.0, 3.0)
    assert backend.rebase_count == 0
    assert backend.events == ["begin:morph_value", "commit:morph_value"]
    assert backend.rollback_count == 0


def test_morph_change_without_structural_writer_fails_before_transaction() -> None:
    coordinator, backend, _, _ = _coordinator()
    coordinator._morphs = None
    backend.begin_morph_create = None
    with pytest.raises(MayaModelAuthoringCoordinatorError, match="narrow morph transaction"):
        coordinator.create_morph("|root", MmdMorphSpec("Smile", morph_type="bone"))
    assert backend.begin_count == 0


def test_morph_offsets_move_requires_narrow_path_and_reindex_remains_full() -> None:
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

    with pytest.raises(MayaModelAuthoringCoordinatorError, match="narrow morph reindex"):
        coordinator.move_morph("|root", 0, 1)
    backend.rebase_count = 0
    reindexed = coordinator.reindex_morphs("|root", (1, 0))
    assert [morph.name for morph in reindexed.morphs] == ["B", "A"]
    assert backend.begin_count == 4
    assert backend.commit_count == 4


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


def test_material_create_failure_rolls_back_original_spec() -> None:
    coordinator, backend, materials, _ = _coordinator()
    original = backend.scene
    materials.fail_create = True
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
