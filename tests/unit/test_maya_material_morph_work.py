"""Tests for scoped temporary Material Morph work bindings."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from mmd_tools.adapters.maya_material_morph_work import (
    MayaMaterialMorphWork,
    MayaMaterialMorphWorkError,
)
from mmd_tools.core.model_authoring_spec import (
    MmdMaterialSpec,
    MmdModelAuthoringSpec,
    MmdModelSpec,
    MmdMorphSpec,
)


def _offset(operation=1, material_index=0):
    neutral = 1.0 if operation == 0 else 0.0
    return {
        "material_index": material_index,
        "operation_type": operation,
        "diffuse": [neutral, neutral, neutral, neutral],
        "specular": [neutral, neutral, neutral],
        "specular_coefficient": neutral,
        "ambient": [neutral, neutral, neutral],
        "edge_color": [neutral, neutral, neutral, neutral],
        "edge_size": neutral,
        "texture_factor": [neutral, neutral, neutral, neutral],
        "sphere_texture_factor": [neutral, neutral, neutral, neutral],
        "toon_texture_factor": [neutral, neutral, neutral, neutral],
    }


def _spec(offset=None):
    return MmdModelAuthoringSpec(
        model=MmdModelSpec(name="モデル"),
        materials=(
            MmdMaterialSpec(
                name="材質",
                index=0,
                diffuse=(0.5, 0.4, 0.3, 1.0),
                specular=(0.2, 0.3, 0.4),
                specular_coefficient=2.0,
                ambient=(0.1, 0.2, 0.3),
                edge_color=(0.0, 0.1, 0.2, 1.0),
                edge_size=1.5,
                binding_identity="material0",
            ),
        ),
        morphs=(
            MmdMorphSpec(
                name="材質モーフ",
                index=0,
                panel=4,
                morph_type="material",
                offsets=(offset or _offset(),),
                binding_identity="morph0",
            ),
        ),
    )


@dataclass
class FakeCoordinator:
    spec: MmdModelAuthoringSpec
    replacements: list[tuple[str, int, list[dict[str, Any]]]] = field(default_factory=list)

    def read_spec(self, _root):
        return self.spec

    def replace_morph_offsets(self, root, index, offsets):
        self.replacements.append((root, index, offsets))
        return self.spec


@dataclass
class FakeRegistry:
    members: list[str] = field(default_factory=list)

    def ensure_model_registry(self, _root):
        return "registry"

    def list_model_registry_members(self, _root, _category):
        return list(self.members)

    def register_model_members(self, _registry, _category, members):
        self.members.extend(members)

    def unregister_model_members(self, _registry, _category, members):
        for member in members:
            self.members.remove(member)


@dataclass
class FakeAdapter:
    attrs: dict[tuple[str, str], Any] = field(default_factory=dict)
    nodes: set[str] = field(default_factory=set)
    undo_open: int = 0
    undo_close: int = 0
    undos: int = 0
    fast_selections: list[str] = field(default_factory=list)

    def shading_node(self, _node_type, **kwargs):
        node = kwargs["name"]
        self.nodes.add(node)
        self.attrs[(node, "baseColor")] = (0.0, 0.0, 0.0)
        self.attrs[(node, "specularColor")] = (0.0, 0.0, 0.0)
        return node

    def attribute_exists(self, attr, node):
        return (node, attr) in self.attrs

    def add_attr(self, node, **kwargs):
        self.attrs.setdefault((node, kwargs["longName"]), None)

    def set_attr(self, path, *values, **_kwargs):
        node, attr = path.split(".", 1)
        self.attrs[(node, attr)] = values[0] if len(values) == 1 else tuple(values)

    def get_attr(self, path):
        node, attr = path.split(".", 1)
        return self.attrs[(node, attr)]

    def delete(self, node):
        self.nodes.remove(node)

    def undo_info(self, **kwargs):
        self.undo_open += int(bool(kwargs.get("openChunk")))
        self.undo_close += int(bool(kwargs.get("closeChunk")))

    def undo(self):
        self.undos += 1

    def select(self, *_args, **_kwargs):
        return None

    def select_fast(self, node, replace=True):
        del replace
        self.fast_selections.append(node)
        return [node]


def test_create_and_clear_are_owned_undo_actions_and_leave_raw_unchanged():
    spec = _spec()
    adapter = FakeAdapter()
    registry = FakeRegistry()
    coordinator = FakeCoordinator(spec)
    service = MayaMaterialMorphWork(adapter, coordinator, registry_api=registry)

    shader = service.create("|Model", 0, 0)

    assert shader.isascii() and not shader.startswith("|")
    assert registry.members == [shader]
    assert coordinator.spec.fingerprint() == spec.fingerprint()
    assert coordinator.replacements == []
    assert adapter.attrs[(shader, "mmd_work_morph_name")] == "材質モーフ"
    assert adapter.fast_selections == [shader]

    service.clear("|Model")

    assert registry.members == []
    assert shader not in adapter.nodes
    assert adapter.undo_open == adapter.undo_close == 2
    assert adapter.undos == 0
    assert coordinator.replacements == []


def test_apply_converts_additive_work_values_through_coordinator_only():
    adapter = FakeAdapter()
    registry = FakeRegistry()
    coordinator = FakeCoordinator(_spec())
    service = MayaMaterialMorphWork(adapter, coordinator, registry_api=registry)
    shader = service.create("|Model", 0, 0)
    adapter.attrs[(shader, "baseColor")] = (0.7, 0.1, 0.8)
    adapter.attrs[(shader, "mmd_work_diffuse_alpha")] = 0.5
    adapter.attrs[(shader, "mmd_work_texture_factor_r")] = 0.25

    service.apply("|Model", 0, 0)

    root, morph_index, offsets = coordinator.replacements[-1]
    assert (root, morph_index) == ("|Model", 0)
    assert offsets[0]["diffuse"] == pytest.approx([0.2, -0.3, 0.5, -0.5])
    assert offsets[0]["texture_factor"][0] == pytest.approx(0.25)
    assert registry.members == [shader]
    assert adapter.undo_open == adapter.undo_close == 1  # Create only; Apply is coordinator-owned.


def test_create_rejects_ambiguous_target_and_multiple_work_before_write():
    adapter = FakeAdapter()
    registry = FakeRegistry()
    coordinator = FakeCoordinator(_spec(_offset(material_index=-1)))
    service = MayaMaterialMorphWork(adapter, coordinator, registry_api=registry)

    with pytest.raises(MayaMaterialMorphWorkError, match="all-material"):
        service.create("|Model", 0, 0)
    assert not adapter.nodes

    coordinator.spec = _spec()
    registry.members = ["existingWork"]
    with pytest.raises(MayaMaterialMorphWorkError, match="already owns"):
        service.create("|Model", 0, 0)
    assert not adapter.nodes


def test_apply_rejects_non_finite_shader_value_before_coordinator_write():
    adapter = FakeAdapter()
    registry = FakeRegistry()
    coordinator = FakeCoordinator(_spec())
    service = MayaMaterialMorphWork(adapter, coordinator, registry_api=registry)
    shader = service.create("|Model", 0, 0)
    adapter.attrs[(shader, "baseColor")] = (float("nan"), 0.4, 0.3)

    with pytest.raises(MayaMaterialMorphWorkError, match="finite"):
        service.apply("|Model", 0, 0)
    assert coordinator.replacements == []
