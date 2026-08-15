"""Tests for the production Maya authoring dependency composition."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mmd_tools.adapters import maya_authoring_factory as factory
from mmd_tools.core.constants import ATTR_MMD_IMPORT_SCALE


class _CmdsAdapter:
    def __init__(self, module) -> None:
        self.module = module

    def get_attr(self, path: str) -> float:
        return self.module.scales[path]

    def attribute_exists(self, attr: str, node: str) -> bool:
        return f"{node}.{attr}" in self.module.scales


class _Backend:
    def __init__(self, adapter) -> None:
        self.adapter = adapter

    def mark_mutation(self) -> None:
        pass


class _Metadata:
    def __init__(self, backend) -> None:
        self.backend = backend


class _Materials:
    def __init__(self, adapter, registry_api, **kwargs) -> None:
        self.adapter = adapter
        self.registry_api = registry_api
        self.mutation_boundary = kwargs.get("mutation_boundary")


class _Coordinator:
    def __init__(self, metadata, backend, materials, adapter, **kwargs) -> None:
        self.metadata = metadata
        self.backend = backend
        self.materials = materials
        self.adapter = adapter
        self.morph_authoring = kwargs["morph_authoring"]
        self.model_scale_resolver = kwargs["model_scale_resolver"]


class _Initializer:
    def __init__(self, adapter, **kwargs) -> None:
        self.adapter = adapter
        self.metadata_backend_factory = kwargs["metadata_backend_factory"]
        self.material_authoring_factory = kwargs["material_authoring_factory"]


class _CreateModelAction:
    def __init__(self, initializer) -> None:
        self.initializer = initializer


def test_factory_shares_one_graph_and_injects_runtime_rebuilders(monkeypatch) -> None:
    registry = object()
    module = SimpleNamespace(
        scales={f"|root.{ATTR_MMD_IMPORT_SCALE}": 0.1}
    )
    observed = {}
    rebuilders = {"bone": object(), "material": object()}

    monkeypatch.setattr(factory, "MayaCmdsAdapter", _CmdsAdapter)
    monkeypatch.setattr(factory, "MayaSceneMetadataBackend", _Backend)
    monkeypatch.setattr(factory, "SceneMetadataAdapter", _Metadata)
    monkeypatch.setattr(factory, "MayaMaterialAuthoring", _Materials)
    monkeypatch.setattr(factory, "MayaModelAuthoringCoordinator", _Coordinator)
    monkeypatch.setattr(factory, "MayaModelTemplateInitializer", _Initializer)
    monkeypatch.setattr(factory, "CreateModelAction", _CreateModelAction)
    monkeypatch.setattr(factory, "maya_runtime_rebuilders", lambda: rebuilders)

    def apply(root, old, new, adapter, **kwargs):
        observed.update(root=root, old=old, new=new, adapter=adapter, **kwargs)
        return "bound"

    monkeypatch.setattr(factory, "apply_morph_spec_change", apply)
    composition = factory.build_maya_authoring_composition(module, registry_api=registry)

    assert not hasattr(composition, "run_authoring_e2e")
    assert composition.metadata_backend.adapter is composition.cmds_adapter
    assert composition.metadata_adapter.backend is composition.metadata_backend
    assert composition.material_authoring.adapter is composition.cmds_adapter
    assert composition.material_authoring.mutation_boundary == composition.metadata_backend.mark_mutation
    assert composition.coordinator.adapter is composition.cmds_adapter
    assert composition.coordinator.morph_authoring("|root", "old", "new") == "bound"
    assert observed["registry_api"] is registry
    assert observed["runtime_rebuilders"] is rebuilders
    assert composition.coordinator.model_scale_resolver("|root") == 0.1
    assert composition.model_scale_resolver("|root") == 0.1
    assert composition.model_initializer.adapter is composition.cmds_adapter
    assert composition.model_initializer.metadata_backend_factory(None) is composition.metadata_backend
    assert composition.model_initializer.material_authoring_factory(None) is composition.material_authoring
    assert composition.create_model_action.initializer is composition.model_initializer


def test_factory_model_scale_resolver_rejects_legacy_root_without_persisted_scale(monkeypatch) -> None:
    monkeypatch.setattr(factory, "MayaCmdsAdapter", _CmdsAdapter)
    monkeypatch.setattr(factory, "MayaSceneMetadataBackend", _Backend)
    monkeypatch.setattr(factory, "SceneMetadataAdapter", _Metadata)
    monkeypatch.setattr(factory, "MayaMaterialAuthoring", _Materials)
    monkeypatch.setattr(factory, "MayaModelAuthoringCoordinator", _Coordinator)
    monkeypatch.setattr(factory, "MayaModelTemplateInitializer", _Initializer)
    monkeypatch.setattr(factory, "CreateModelAction", _CreateModelAction)
    monkeypatch.setattr(factory, "maya_runtime_rebuilders", dict)
    module = SimpleNamespace(scales={})
    composition = factory.build_maya_authoring_composition(module, registry_api=object())

    with pytest.raises(RuntimeError, match="legacy model"):
        composition.coordinator.model_scale_resolver("|root")


@pytest.mark.parametrize("value", [0.0, -1.0, float("inf"), float("nan"), True, "1"])
def test_factory_model_scale_resolver_rejects_invalid_persisted_scale(monkeypatch, value) -> None:
    monkeypatch.setattr(factory, "MayaCmdsAdapter", _CmdsAdapter)
    monkeypatch.setattr(factory, "MayaSceneMetadataBackend", _Backend)
    monkeypatch.setattr(factory, "SceneMetadataAdapter", _Metadata)
    monkeypatch.setattr(factory, "MayaMaterialAuthoring", _Materials)
    monkeypatch.setattr(factory, "MayaModelAuthoringCoordinator", _Coordinator)
    monkeypatch.setattr(factory, "MayaModelTemplateInitializer", _Initializer)
    monkeypatch.setattr(factory, "CreateModelAction", _CreateModelAction)
    monkeypatch.setattr(factory, "maya_runtime_rebuilders", dict)
    module = SimpleNamespace(scales={f"|root.{ATTR_MMD_IMPORT_SCALE}": value})
    composition = factory.build_maya_authoring_composition(module, registry_api=object())

    with pytest.raises(RuntimeError, match="persisted MMD import scale"):
        composition.model_scale_resolver("|root")
