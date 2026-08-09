"""Focused tests for the injected Create MMD Model workflow."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from mmd_tools.actions.create_model_action import CreateModelAction, CreateModelActionError, CreateModelRequest
from mmd_tools.adapters.maya_model_template_initializer import (
    MayaModelTemplateInitializer,
    MayaModelTemplateInitializerError,
)
from mmd_tools.adapters.maya_scene_metadata_backend import MayaSceneMetadataBackend
from mmd_tools.core.constants import (
    ATTR_MMD_AMBIENT_COLOR,
    ATTR_MMD_DIFFUSE_COLOR,
    ATTR_MMD_DISPLAY_FRAMES_JSON,
    ATTR_MMD_DRAW_FLAGS,
    ATTR_MMD_EDGE_COLOR,
    ATTR_MMD_EDGE_SIZE,
    ATTR_MMD_IMPORT_SCALE,
    ATTR_MMD_MATERIAL,
    ATTR_MMD_MATERIAL_INDEX,
    ATTR_MMD_MATERIAL_NAME,
    ATTR_MMD_MATERIAL_NAME_EN,
    ATTR_MMD_MEMO,
    ATTR_MMD_MODEL_REGISTRY,
    ATTR_MMD_REGISTRY_MATERIAL_MEMBERS,
    ATTR_MMD_SHARED_TOON_FLAG,
    ATTR_MMD_SHININESS,
    ATTR_MMD_SPECULAR_COLOR,
    ATTR_MMD_SPHERE_MODE,
    ATTR_MMD_TOON_TEXTURE_INDEX,
)
from mmd_tools.core.model_template import list_model_templates


class FakeMayaAdapter:
    """In-memory adapter implementing the strict metadata read surface."""

    def __init__(self) -> None:
        self.nodes: dict[str, str] = {}
        self.parents: dict[str, str] = {}
        self.attrs: dict[tuple[str, str], Any] = {}
        self.connections: dict[str, list[str]] = {}
        self.undo_open = False
        self.undo_open_count = 0
        self.undo_close_count = 0
        self.undo_count = 0
        self.mesh_calls: list[tuple[str, str, str, str]] = []

    def object_exists(self, node: str) -> bool:
        return node in self.nodes

    def create_node(self, node_type: str, name: str, **kwargs: Any) -> str:
        parent = kwargs.get("parent")
        short = name.rsplit("|", 1)[-1]
        if parent:
            base = f"{parent}|{short}"
        else:
            base = f"|{short}" if node_type in {"transform", "joint", "mesh"} else short
        node = base
        suffix = 1
        while node in self.nodes:
            node = f"{base}{suffix}"
            suffix += 1
        self.nodes[node] = node_type
        if parent:
            self.parents[node] = parent
        return node

    def shading_node(self, node_type: str, **kwargs: Any) -> str:
        return self.create_node(node_type, kwargs.get("name", "shader"))

    def sets(self, *args: Any, **kwargs: Any) -> str | None:
        if not args:
            return self.create_node("shadingEngine", kwargs.get("name", "sg"))
        return None

    def connect_attr(self, source: str, destination: str, **kwargs: Any) -> None:
        source_node = source.rsplit(".", 1)[0]
        self.connections[destination] = [source_node]

    def ls(self, node: str | None = None, **kwargs: Any) -> list[str]:
        if kwargs.get("type") == "network":
            return [name for name, node_type in self.nodes.items() if node_type == "network"]
        if node is None:
            return list(self.nodes)
        return [node] if node in self.nodes else []

    def list_relatives(self, node: str, **kwargs: Any) -> list[str]:
        descendants = []
        pending = [child for child, parent in self.parents.items() if parent == node]
        while pending:
            child = pending.pop(0)
            descendants.append(child)
            pending.extend(grandchild for grandchild, parent in self.parents.items() if parent == child)
        node_type = kwargs.get("type")
        if node_type:
            descendants = [child for child in descendants if self.nodes.get(child) == node_type]
        if kwargs.get("parent"):
            return [self.parents[node]] if node in self.parents else []
        return descendants

    def node_type(self, node: str) -> str:
        return self.nodes.get(node, "")

    def attribute_exists(self, attr: str, node: str) -> bool:
        return (node, attr) in self.attrs

    def add_attr(self, node: str, **kwargs: Any) -> None:
        attr = kwargs["longName"]
        self.attrs.setdefault((node, attr), None)

    def set_attr(self, path: str, *values: Any, **kwargs: Any) -> None:
        node, attr = path.rsplit(".", 1)
        if kwargs.get("type") == "double3":
            self.attrs[(node, attr)] = tuple(float(value) for value in values)
        else:
            self.attrs[(node, attr)] = values[0]

    def get_attr(self, path: str) -> Any:
        node, attr = path.rsplit(".", 1)
        return self.attrs[(node, attr)]

    def list_connections(self, query: str, **kwargs: Any) -> list[str]:
        direct = self.connections.get(query)
        if direct is not None:
            return list(direct)
        values: list[str] = []
        prefix = query + "["
        for destination, sources in self.connections.items():
            if destination.startswith(prefix):
                values.extend(sources)
        return values

    def undo_info(self, **kwargs: Any) -> bool | None:
        if kwargs.get("query") and kwargs.get("state"):
            return True
        if kwargs.get("openChunk"):
            assert not self.undo_open
            self.undo_open = True
            self.undo_open_count += 1
        elif kwargs.get("closeChunk"):
            assert self.undo_open
            self.undo_open = False
            self.undo_close_count += 1
        return None

    def undo(self) -> None:
        self.undo_count += 1


def _backend(adapter: FakeMayaAdapter) -> MayaSceneMetadataBackend:
    return MayaSceneMetadataBackend(adapter)


class FakeMaterialAuthoring:
    """Canonical writer stub with the same return contract as MayaMaterialAuthoring."""

    def __init__(self, adapter: FakeMayaAdapter) -> None:
        self._adapter = adapter

    def create_material(self, root: str, material: Any) -> tuple[Any, str, str]:
        shader = self._adapter.create_node("standardSurface", "mmdMaterial_0")
        shading_group = self._adapter.create_node("shadingEngine", "mmdMaterial_0_SG")
        self._set(shader, ATTR_MMD_MATERIAL, 1)
        self._set(shader, ATTR_MMD_MATERIAL_INDEX, material.index)
        self._set(shader, ATTR_MMD_MATERIAL_NAME, material.name, string=True)
        self._set(shader, ATTR_MMD_MATERIAL_NAME_EN, material.name_english, string=True)
        self._set(shader, ATTR_MMD_DIFFUSE_COLOR, material.diffuse[:3])
        self._set(shader, "mmd_diffuse_alpha", material.diffuse[3])
        self._set(shader, ATTR_MMD_SPECULAR_COLOR, material.specular)
        self._set(shader, ATTR_MMD_SHININESS, material.specular_coefficient)
        self._set(shader, ATTR_MMD_AMBIENT_COLOR, material.ambient)
        self._set(shader, ATTR_MMD_DRAW_FLAGS, material.draw_flags)
        self._set(shader, ATTR_MMD_EDGE_COLOR, material.edge_color[:3])
        self._set(shader, "mmd_edge_alpha", material.edge_color[3])
        self._set(shader, ATTR_MMD_EDGE_SIZE, material.edge_size)
        self._set(shader, ATTR_MMD_SPHERE_MODE, material.sphere_mode)
        self._set(shader, ATTR_MMD_SHARED_TOON_FLAG, int(material.shared_toon))
        self._set(shader, ATTR_MMD_TOON_TEXTURE_INDEX, -1)
        self._set(shader, ATTR_MMD_MEMO, material.memo, string=True)
        registry = self._adapter.connections[f"{root}.{ATTR_MMD_MODEL_REGISTRY}"][0]
        self._adapter.connect_attr(
            f"{shader}.message",
            f"{registry}.{ATTR_MMD_REGISTRY_MATERIAL_MEMBERS}[0]",
        )
        return replace(material, binding_identity=shader), shader, shading_group

    def _set(self, node: str, attr: str, value: Any, *, string: bool = False) -> None:
        if not self._adapter.attribute_exists(attr, node):
            self._adapter.add_attr(node, longName=attr, dataType="string" if string else "double")
        self._adapter.set_attr(f"{node}.{attr}", value, type="string" if string else None)


def _mesh_factory(root: str, joint: str, shader: str, shading_group: str, adapter: FakeMayaAdapter) -> None:
    adapter.mesh_calls.append((root, joint, shader, shading_group))


def test_create_model_initializes_root_registry_bindings_and_display_frames() -> None:
    adapter = FakeMayaAdapter()
    initializer = MayaModelTemplateInitializer(
        adapter,
        metadata_backend_factory=_backend,
        material_authoring_factory=FakeMaterialAuthoring,
        mesh_factory=_mesh_factory,
    )

    result = initializer.create("pmx20-basic-v1", "モデル名", "Model Name")

    assert result.root.startswith("|") and result.root.isascii()
    assert result.registry.isascii() and not result.registry.startswith("|")
    assert result.spec.model.name == "モデル名"
    assert result.spec.model.name_english == "Model Name"
    assert result.spec.bones[0].binding_identity.startswith(result.root + "|")
    assert result.spec.materials[0].binding_identity == "mmdMaterial_0"
    assert result.spec.fingerprint() == result.fingerprint
    frames = json.loads(adapter.attrs[(result.root, ATTR_MMD_DISPLAY_FRAMES_JSON)])
    assert frames[0]["elements"] == [{"type": 0, "index": 0}]
    assert adapter.connections[f"{result.registry}.{ATTR_MMD_REGISTRY_MATERIAL_MEMBERS}[0]"]
    assert adapter.attrs[(result.root, ATTR_MMD_IMPORT_SCALE)] == 1.0
    assert adapter.mesh_calls
    assert adapter.undo_open_count == adapter.undo_close_count == 1
    assert adapter.undo_count == 0


def test_create_semistandard_model_registers_full_rig_and_cube_intent() -> None:
    adapter = FakeMayaAdapter()
    initializer = MayaModelTemplateInitializer(
        adapter,
        metadata_backend_factory=_backend,
        material_authoring_factory=FakeMaterialAuthoring,
        mesh_factory=_mesh_factory,
    )

    result = initializer.create("pmx20-semistandard-v1", "準標準モデル", "Semi Model")

    assert result.root.endswith("_root")
    assert len(result.spec.bones) == 100
    assert result.spec.bones[53].ik_target_index == 52
    assert result.spec.bones[53].ik_links[0]["bone"] == 51
    assert result.spec.materials[0].name == "Default Material"
    assert len(adapter.list_relatives(result.root, allDescendents=True, type="joint")) == 100
    assert adapter.mesh_calls == [(result.root, result.spec.bones[0].binding_identity, "mmdMaterial_0", "mmdMaterial_0_SG")]
    assert adapter.undo_open_count == adapter.undo_close_count == 1


def test_create_model_rolls_back_one_transaction_on_mesh_failure() -> None:
    adapter = FakeMayaAdapter()

    def broken_mesh(*_args: Any) -> None:
        raise RuntimeError("mesh failed")

    initializer = MayaModelTemplateInitializer(
        adapter,
        metadata_backend_factory=_backend,
        material_authoring_factory=FakeMaterialAuthoring,
        mesh_factory=broken_mesh,
    )
    with pytest.raises(MayaModelTemplateInitializerError, match="mesh failed"):
        initializer.create("pmx20-basic-v1", "モデル", "Model")
    assert adapter.undo_open_count == adapter.undo_close_count == 1
    assert adapter.undo_count == 1


def test_create_model_action_is_safe_without_injected_initializer() -> None:
    with pytest.raises(CreateModelActionError, match="injected template initializer"):
        CreateModelAction().execute(CreateModelRequest("pmx20-basic-v1", "モデル"))

    class Stub:
        def create(self, *args: Any) -> tuple[Any, ...]:
            return args

    assert CreateModelAction(Stub()).execute(CreateModelRequest("pmx20-basic-v1", "モデル")) == (
        "pmx20-basic-v1",
        "モデル",
        "",
    )


def test_template_selector_options_are_curated_and_immutable() -> None:
    options = list_model_templates()
    assert isinstance(options, tuple)
    assert [(option.template_id, option.label) for option in options] == [
        ("pmx20-semistandard-v1", "準標準ボーン"),
        ("pmx20-basic-v1", "1ボーン1Cube"),
    ]
