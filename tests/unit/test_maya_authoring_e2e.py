from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from mmd_tools.adapters import maya_authoring_e2e
from mmd_tools.core.bone_authoring import capture_rest, register_bone, reindex_bones, unregister_bone
from mmd_tools.core.material_authoring import create_material, delete_material
from mmd_tools.core.model_authoring_spec import (
    MmdBoneSpec,
    MmdMaterialSpec,
    MmdModelAuthoringSpec,
    MmdModelSpec,
)
from mmd_tools.core.morph_authoring import (
    create_morph,
    reindex_morphs,
    replace_morph_offsets,
)


def _spec() -> MmdModelAuthoringSpec:
    return MmdModelAuthoringSpec(
        model=MmdModelSpec("モデル", "Model"),
        bones=(
            MmdBoneSpec(
                name="root",
                name_english="root",
                index=0,
                binding_identity="|root|root",
                tail_offset=(0.0, 1.0, 0.0),
            ),
        ),
        materials=(
            MmdMaterialSpec(
                name="mat",
                name_english="mat",
                index=0,
                binding_identity="shader0",
            ),
        ),
    )


def test_writer_not_called_negative_executes_action_and_preserves_target(tmp_path):
    target = tmp_path / "blocked.pmx"
    target.write_bytes(b"sentinel")

    result = maya_authoring_e2e.writer_not_called_case(target)

    assert result["status"] == "pass"
    assert result["writer_calls"] == 0
    assert result["result_succeeded"] is False
    assert result["blocking"] is True
    assert result["target_unchanged"] is True
    assert target.read_bytes() == b"sentinel"


def test_normalize_spec_payload_removes_bindings_before_fingerprint() -> None:
    first = _spec()
    second = replace(
        first,
        bones=(replace(first.bones[0], binding_identity="|fresh|root"),),
        materials=(replace(first.materials[0], binding_identity="freshShader"),),
    )
    normalized_first = maya_authoring_e2e.normalize_spec_payload(first)
    normalized_second = maya_authoring_e2e.normalize_spec_payload(second)
    assert normalized_first == normalized_second
    assert normalized_first["fingerprint"].startswith("sha256:")
    assert normalized_first["bones"][0]["binding_identity"] == "bones:0"


def test_normalize_spec_payload_rejects_unknown_top_level_fields() -> None:
    payload = _spec().to_mapping()
    payload["unexpected"] = True
    with pytest.raises(maya_authoring_e2e.MayaAuthoringE2EError):
        maya_authoring_e2e.normalize_spec_payload(payload)


class _FakeCmds:
    def list_relatives(self, node, **kwargs):
        if kwargs.get("type") == "mesh":
            return ["|root|mesh|meshShape"]
        if node == "|root|mesh|meshShape" and kwargs.get("parent"):
            return ["|root|mesh"]
        return []

    def create_node(self, node_type, *, name, parent=None):
        assert node_type == "joint"
        assert parent == "|root"
        return "|root|e2eBone"

    def ls(self, node, **kwargs):
        assert kwargs.get("long") is True
        return [node]

    def new_scene(self, *, force=True):
        assert force is True


class _FakeMaterialAuthoring:
    def apply_material_spec_change(self, _root, _old, new, _replacement=None):
        return new


class _FakeCoordinator:
    def __init__(self):
        self.spec = _spec()

    def read_spec(self, _root):
        return self.spec

    def _set(self, value):
        assert isinstance(value, MmdModelAuthoringSpec)
        self.spec = value
        return value

    def _execute(self, _root, _operation, target, structural_write):
        return self._set(structural_write())

    def create_material(self, _root, _targets):
        value = create_material(self.spec)
        material = max(value.materials, key=lambda item: item.index)
        value = replace(
            value,
            materials=tuple(
                replace(item, binding_identity="shader1")
                if item.index == material.index
                else item
                for item in value.materials
            ),
        )
        return self._set(value)

    def assign_material(self, _root, _index, _targets):
        return self.spec

    def replace_material(self, _root, material):
        self.spec = replace(
            self.spec,
            materials=tuple(
                material if item.index == material.index else item for item in self.spec.materials
            ),
        )
        return self.spec

    def delete_material(self, _root, index):
        value = delete_material(self.spec, index)
        return self._set(value)

    def register_selected_joint(self, _root, joint):
        bone = MmdBoneSpec(
            name="e2eBone",
            name_english="e2eBone",
            index=1,
            parent_index=-1,
            binding_identity=joint,
        )
        return self._set(register_bone(self.spec, bone))

    def capture_rest(self, _root, index, _joint):
        return self._set(capture_rest(self.spec, index, (0.0, 0.0, 0.0)))

    def reindex_bones(self, _root, order):
        return self._set(reindex_bones(self.spec, order))

    def unregister_bone(self, _root, index):
        return self._set(unregister_bone(self.spec, index))

    def create_morph(self, _root, morph):
        return self._set(create_morph(self.spec, morph))

    def replace_morph_offsets(self, _root, index, offsets):
        return self._set(replace_morph_offsets(self.spec, index, offsets))

    def reindex_morphs(self, _root, order):
        return self._set(reindex_morphs(self.spec, order))


class _FakeMetadata:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.fresh = None

    def read_spec(self, root):
        return self.fresh if root == "|freshRoot" else self.coordinator.spec


class _FakeExport:
    def execute(self, request):
        Path(request.file_path).write_bytes(b"PMX fake")
        return SimpleNamespace(succeeded=True, exported_path=request.file_path)


def test_run_authoring_e2e_executes_all_operations_with_injected_dependencies(monkeypatch, tmp_path):
    coordinator = _FakeCoordinator()
    metadata = _FakeMetadata(coordinator)
    source = coordinator.spec

    def parser(path):
        assert Path(path).is_file()
        return object()

    def importer(_parser, _path, *, options):
        assert options == {"import_physics": False}
        metadata.fresh = replace(
            coordinator.spec,
            bones=tuple(replace(item, binding_identity="|fresh|root") for item in coordinator.spec.bones),
            materials=tuple(replace(item, binding_identity="freshShader") for item in coordinator.spec.materials),
            morphs=tuple(replace(item, binding_identity="freshMorph") for item in coordinator.spec.morphs),
        )
        return "|freshRoot"

    monkeypatch.setattr(maya_authoring_e2e, "_safe_output_path", lambda _paths: tmp_path / "e2e.pmx")
    result = maya_authoring_e2e.run_authoring_e2e(
        initializer=SimpleNamespace(create=lambda *_args: SimpleNamespace(root="|root", spec=source)),
        template_id="pmx20-basic-v1",
        model_name="モデル",
        model_name_english="Model",
        asset_paths={},
        coordinator=coordinator,
        metadata_adapter=metadata,
        cmds_adapter=_FakeCmds(),
        material_authoring=_FakeMaterialAuthoring(),
        export_action=_FakeExport(),
        pmx_parser=parser,
        pmx_importer=importer,
    )
    assert [item["name"] for item in result["operations"]] == list(maya_authoring_e2e.REQUIRED_OPERATIONS)
    assert result["before"] == result["after"]
    assert all(item["status"] == "pass" for item in result["negative_cases"])
