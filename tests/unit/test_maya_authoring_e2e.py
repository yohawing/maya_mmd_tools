from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from mmd_tools.adapters import maya_authoring_e2e
from mmd_tools.core.bone_authoring import capture_rest, register_bone, reindex_bones, unregister_bone
from mmd_tools.core.material_authoring import create_material, delete_material, reindex_materials
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


@pytest.mark.parametrize(
    "materials",
    [
        [],
        [SimpleNamespace(material_index=1, name="Material 1", name_english="Material 1", face_count=0)],
        [SimpleNamespace(material_index=1, name="wrong", name_english="Material 1", face_count=0)],
        [SimpleNamespace(material_index=1, name="Material 1", name_english="Material 1", face_count=3)],
    ],
)
def test_unassigned_material_parse_evidence_fails_closed(materials) -> None:
    material = MmdMaterialSpec(name="Material 1", name_english="Material 1", index=1)
    with pytest.raises(maya_authoring_e2e.MayaAuthoringE2EError):
        maya_authoring_e2e._require_exported_unassigned_material(
            SimpleNamespace(materials=materials), material
        )


class _FakeCmds:
    def __init__(self):
        self.set_calls = []

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

    def sets(self, node, **kwargs):
        assert kwargs.get("e") is True
        assert kwargs.get("forceElement")
        self.set_calls.append((node, kwargs["forceElement"]))


class _FakeMaterialAuthoring:
    def resolve_material(self, _root, material):
        assert material.binding_identity
        return material.binding_identity, f"{material.binding_identity}SG"

    def apply_material_spec_change(self, _root, _old, new, _replacement=None):
        return new


class _FakeCoordinator:
    def __init__(self):
        self.spec = _spec()
        self.material_serial = 0

    def read_spec(self, _root):
        return self.spec

    def _set(self, value):
        assert isinstance(value, MmdModelAuthoringSpec)
        self.spec = value
        return value

    def _execute(self, _root, _operation, target, structural_write):
        return self._set(structural_write())

    def create_material(self, _root):
        value = create_material(self.spec)
        material = max(value.materials, key=lambda item: item.index)
        self.material_serial += 1
        value = replace(
            value,
            materials=tuple(
                replace(item, binding_identity=f"shader{self.material_serial}")
                if item.index == material.index
                else item
                for item in value.materials
            ),
        )
        return self._set(value)

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

    def reindex_materials(self, _root, order):
        return self._set(reindex_materials(self.spec, order))

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
        return SimpleNamespace(
            materials=[
                SimpleNamespace(
                    material_index=0,
                    name="mat",
                    name_english="mat",
                    face_count=3,
                ),
                SimpleNamespace(
                    material_index=1,
                    name="Material 1",
                    name_english="Material 1",
                    face_count=0,
                ),
            ]
        )

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
