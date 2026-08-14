"""Contracts for the dedicated native Material value command boundary."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from mmd_tools.adapters.maya_material_authoring import MayaMaterialAuthoring
from mmd_tools.adapters.native_authoring_command import (
    COMMAND_SET_MATERIAL_VALUES,
    NativeAuthoringCommandGateway,
    NativeCommandDomainError,
    NativeCommandProtocolError,
    NativeCommandUnavailable,
)
from mmd_tools.core.model_authoring_spec import MmdMaterialSpec
from tests.unit.test_maya_model_authoring_coordinator import _coordinator


class _NodeType:
    def __init__(self, value="dx11Shader", *, undo_enabled=True):
        self.value = value
        self.undo_enabled = undo_enabled

    def node_type(self, _node):
        return self.value

    def undo_info(self, **_kwargs):
        return self.undo_enabled


def _material():
    return MmdMaterialSpec(
        "Material",
        name_english="Material",
        index=0,
        diffuse=(0.8, 0.7, 0.6, 1.0),
        specular=(0.1, 0.2, 0.3),
        specular_coefficient=5.0,
        ambient=(0.2, 0.2, 0.2),
        draw_flags=0,
        edge_color=(0.0, 0.0, 0.0, 1.0),
        edge_size=1.0,
        memo="old",
        binding_identity="shader",
    )


def _updates(old, new):
    authoring = object.__new__(MayaMaterialAuthoring)
    authoring._cmds = _NodeType()
    return authoring._material_value_updates("shader", old, new)


def test_python_expands_n1_n4_n8_to_only_intended_write_fields():
    old = _material()
    n1 = replace(old, name_english="N1")
    n4 = replace(
        old,
        name_english="N4",
        memo="new",
        edge_size=2.0,
        specular_coefficient=7.0,
    )
    n8 = replace(
        n4,
        diffuse=(0.2, 0.3, 0.4, 0.5),
        ambient=(0.4, 0.3, 0.2),
        edge_color=(0.1, 0.2, 0.3, 0.4),
        draw_flags=0x10,
    )

    assert [row["field"] for row in _updates(old, n1)] == ["name_english"]
    assert len(_updates(old, n4)) == 4
    assert len(_updates(old, n8)) == 12
    assert {row["field"] for row in _updates(old, n8)} == {
        "name_english",
        "memo",
        "edge_size",
        "specular_coefficient",
        "diffuse_color",
        "diffuse_alpha",
        "viewport_diffuse",
        "ambient",
        "edge_color",
        "edge_alpha",
        "draw_flags",
        "edge_flag",
    }


def test_textured_standard_surface_keeps_viewport_route_out_of_write_set():
    authoring = object.__new__(MayaMaterialAuthoring)
    authoring._cmds = _NodeType("standardSurface")
    old = replace(_material(), texture_path="texture.png", resolved_texture_path="C:/texture.png")
    updates = authoring._material_value_updates(
        "shader", old, replace(old, diffuse=(0.2, 0.3, 0.4, 0.5))
    )
    assert [row["field"] for row in updates] == ["diffuse_color", "diffuse_alpha"]


class _GatewayCmds:
    def __init__(self, result=None, *, exists=True):
        self.result = result
        self.exists = exists
        self.calls = []

    def command_exists(self, command):
        self.calls.append(("exists", command))
        return self.exists

    def invoke_native_command(self, command, **kwargs):
        self.calls.append(("invoke", command, kwargs))
        return self.result


def test_gateway_sends_canonical_identity_and_validates_ordered_readback():
    result = json.dumps(
        {
            "version": 1,
            "command": COMMAND_SET_MATERIAL_VALUES,
            "ok": True,
            "phase": "redo",
            "fields": ["name_english"],
            "plugs": ["shader.mmd_material_name_en"],
            "values": ["日本語"],
        }
    )
    cmds = _GatewayCmds(result)
    NativeAuthoringCommandGateway(cmds).set_material_values(
        "|root", "shader", 0, [{"field": "name_english", "value": "日本語"}]
    )
    payload = json.loads(cmds.calls[1][2]["payload"])
    assert payload == {
        "root": "|root",
        "shader": "shader",
        "material_index": 0,
        "updates": [{"field": "name_english", "value": "日本語"}],
        "version": 1,
    }


def test_gateway_rejects_reordered_or_missing_native_readback():
    bad = json.dumps(
        {
            "version": 1,
            "command": COMMAND_SET_MATERIAL_VALUES,
            "ok": True,
            "phase": "redo",
            "fields": ["memo"],
            "plugs": ["shader.mmd_memo"],
            "values": ["x"],
        }
    )
    with pytest.raises(NativeCommandProtocolError, match="canonical values"):
        NativeAuthoringCommandGateway(_GatewayCmds(bad)).set_material_values(
            "|root", "shader", 0, [{"field": "name_english", "value": "x"}]
        )


class _NativeGateway:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def set_material_values(self, *args):
        self.calls.append(args)
        if self.error:
            raise self.error
        return {}


class _NativeMaterialAuthoring(MayaMaterialAuthoring):
    def _require_root(self, root):
        return root

    def _resolve_material_value_binding(self, _root, material):
        return material.binding_identity, "shaderSG"


def test_only_unavailable_native_command_is_fallback_eligible(monkeypatch):
    old = _material()
    new = replace(old, name_english="new")
    gateway = _NativeGateway(NativeCommandUnavailable("missing"))
    adapter = _NativeMaterialAuthoring(_NodeType(), native_authoring_gateway=gateway)
    monkeypatch.setenv("MMD_AUTHORING_MATERIAL_VALUE_MODE", "auto")
    assert adapter.try_apply_native_material_value_patch("|root", old, new) is None


def test_registered_native_domain_failure_never_falls_back(monkeypatch):
    old = _material()
    new = replace(old, name_english="new")
    gateway = _NativeGateway(NativeCommandDomainError("locked", "locked", "prepare"))
    adapter = _NativeMaterialAuthoring(_NodeType(), native_authoring_gateway=gateway)
    monkeypatch.setenv("MMD_AUTHORING_MATERIAL_VALUE_MODE", "auto")
    with pytest.raises(NativeCommandDomainError, match="locked"):
        adapter.try_apply_native_material_value_patch("|root", old, new)


def test_native_route_rejects_disabled_undo_before_transport(monkeypatch):
    old = _material()
    gateway = _NativeGateway()
    adapter = _NativeMaterialAuthoring(
        _NodeType(undo_enabled=False), native_authoring_gateway=gateway
    )
    monkeypatch.setenv("MMD_AUTHORING_MATERIAL_VALUE_MODE", "native")
    with pytest.raises(Exception, match="undo must be enabled"):
        adapter.try_apply_native_material_value_patch(
            "|root", old, replace(old, name_english="new")
        )
    assert gateway.calls == []


def test_coordinator_native_success_bypasses_python_undo_transaction():
    coordinator, backend, materials, _ = _coordinator()
    prior = backend.scene.materials[0]
    target = replace(prior, name_english="native")
    coordinator._metadata.read_material_value = lambda *_args: prior
    materials.try_apply_native_material_value_patch = lambda _root, old, new: (
        new if old == prior else None
    )
    materials.apply_material_value_patch = lambda *_args: (_ for _ in ()).throw(
        AssertionError("Python mutation fallback is forbidden after native success")
    )
    backend.begin_material_value_patch = lambda *_args: (_ for _ in ()).throw(
        AssertionError("Python undo chunk is forbidden around the undoable native command")
    )

    assert coordinator.apply_material_value_patch("|root", target) == target
    assert backend.events == []


def test_cpp_registry_preflight_checks_schema_backlink_and_unique_membership():
    source = (
        Path(__file__).resolve().parents[2]
        / "cpp"
        / "src"
        / "MmdAuthoringMaterialValueCommand.cpp"
    ).read_text(encoding="utf-8")
    assert 'findPlug("mmd_model_registry_schema"' in source
    assert 'findPlug("modelRoot"' in source
    assert "roots.length() != 1U" in source
    assert "element.connectedTo(sources, true, false, &status)" in source
    assert "if (!status) return false;" in source
    assert "matches == 1U" in source
