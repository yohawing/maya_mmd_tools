"""Focused contracts for the dedicated native Material outline command."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from mmd_tools.adapters.native_authoring_command import (
    COMMAND_SET_MATERIAL_OUTLINE,
    NativeAuthoringCommandGateway,
    NativeCommandProtocolError,
    NativeCommandUnavailable,
)
from tests.unit.test_maya_material_authoring import (
    FakeCmdsAdapter,
    FakeRegistry,
    _material,
)
from tests.unit.test_maya_model_authoring_coordinator import _coordinator
from mmd_tools.adapters.maya_material_authoring import MayaMaterialAuthoring


class _NativeCmds:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def command_exists(self, command):
        self.calls.append(("exists", command))
        return True

    def invoke_native_command(self, command, **kwargs):
        self.calls.append(("invoke", command, kwargs))
        return self.result


def _outline_preimage():
    return {
        "technique": {"exists": True, "value": "Main"},
        "EdgeSize": {"exists": True, "value": 1.0},
        "mmd_shader_outline_enabled": {"exists": True, "value": False},
        "mmdDoubleSided": {"exists": True, "value": False},
        "mmdTransparencyMode": {"exists": True, "value": "opaque"},
    }


def _outline_target():
    return {
        "technique": "MainOutline",
        "mmdDoubleSided": False,
        "mmd_shader_outline_enabled": True,
        "EdgeSize": 1.25,
    }


def test_gateway_sends_exact_policy_precondition_and_validates_readback():
    fields = [
        "name_english",
        "edge_size",
        "technique",
        "mmdDoubleSided",
        "mmd_shader_outline_enabled",
        "EdgeSize",
    ]
    cmds = _NativeCmds(
        json.dumps(
            {
                "version": 1,
                "command": COMMAND_SET_MATERIAL_OUTLINE,
                "ok": True,
                "phase": "redo",
                "fields": fields,
                "plugs": [f"shader.{field}" for field in fields],
                "values": ["New", 1.25, "MainOutline", False, True, 1.25],
            }
        )
    )
    NativeAuthoringCommandGateway(cmds).set_material_outline(
        "|root",
        "shader",
        0,
        [
            {"field": "name_english", "value": "New"},
            {"field": "edge_size", "value": 1.25},
        ],
        _outline_preimage(),
        _outline_target(),
    )
    payload = json.loads(cmds.calls[1][2]["payload"])
    assert payload["outline_preimage"] == _outline_preimage()
    assert payload["outline_target"] == _outline_target()


def test_gateway_rejects_registered_outline_readback_mismatch():
    cmds = _NativeCmds(
        json.dumps(
            {
                "version": 1,
                "command": COMMAND_SET_MATERIAL_OUTLINE,
                "ok": True,
                "phase": "redo",
                "fields": ["technique"],
                "plugs": ["shader.technique"],
                "values": ["wrong"],
            }
        )
    )
    with pytest.raises(NativeCommandProtocolError, match="canonical values"):
        NativeAuthoringCommandGateway(cmds).set_material_outline(
            "|root", "shader", 0, [], _outline_preimage(), _outline_target()
        )


class _Gateway:
    def __init__(self, unavailable=False):
        self.unavailable = unavailable
        self.calls = []

    def set_material_outline(self, *args):
        self.calls.append(args)
        if self.unavailable:
            raise NativeCommandUnavailable("missing")
        return {"ok": True}


def _native_adapter(unavailable=False):
    material = _material()
    shader = str(material.binding_identity)
    cmds = FakeCmdsAdapter()
    cmds.types[shader] = "dx11Shader"
    cmds.types["materialSG"] = "shadingEngine"
    cmds.connections[shader] = ["materialSG"]
    cmds.undo_info = lambda **_kwargs: True
    cmds.attrs[(shader, "mmd_material_index")] = material.index
    for attr, state in _outline_preimage().items():
        cmds.attrs[(shader, attr)] = state["value"]
    registry = FakeRegistry(members=[shader])
    gateway = _Gateway(unavailable=unavailable)
    return MayaMaterialAuthoring(
        cmds, registry, native_authoring_gateway=gateway
    ), gateway, material


def test_python_owns_outline_policy_and_semantic_write_expansion(monkeypatch):
    monkeypatch.setenv("MMD_AUTHORING_MATERIAL_OUTLINE_MODE", "native")
    adapter, gateway, old = _native_adapter()
    new = replace(old, name_english="New", edge_size=1.25)
    assert adapter.try_apply_native_material_outline_patch(
        "|Model_root", old, new, True
    ) == new
    root, shader, index, updates, preimage, target = gateway.calls[0]
    assert (root, shader, index) == ("|Model_root", old.binding_identity, old.index)
    assert updates == [
        {"field": "name_english", "value": "New"},
        {"field": "edge_size", "value": 1.25},
    ]
    assert preimage == _outline_preimage()
    assert target["mmd_shader_outline_enabled"] is True
    assert target["EdgeSize"] == 1.25


def test_auto_falls_back_only_when_outline_command_is_unavailable(monkeypatch):
    monkeypatch.setenv("MMD_AUTHORING_MATERIAL_OUTLINE_MODE", "auto")
    adapter, _gateway, old = _native_adapter(unavailable=True)
    assert adapter.try_apply_native_material_outline_patch(
        "|Model_root", old, old, False
    ) is None


def test_auto_does_not_fallback_after_registered_outline_failure(monkeypatch):
    monkeypatch.setenv("MMD_AUTHORING_MATERIAL_OUTLINE_MODE", "auto")
    adapter, gateway, old = _native_adapter()

    def fail_registered(*_args):
        raise NativeCommandProtocolError("registered command failed")

    gateway.set_material_outline = fail_registered
    with pytest.raises(NativeCommandProtocolError, match="registered command failed"):
        adapter.try_apply_native_material_outline_patch("|Model_root", old, old, False)


def test_coordinator_native_outline_uses_shared_transaction_and_python_readback():
    coordinator, backend, materials, _ = _coordinator()
    prior = backend.scene.materials[0]
    events = backend.events
    coordinator._metadata.read_material_value = lambda *_args: prior
    materials.native_material_outline_patch_available = lambda: True

    def native_outline(_root, _old, new, _enabled, *, outline_target_sink):
        events.append("native:outline")
        outline_target_sink.update(
            {
                "technique": {"exists": True, "value": "MainOutline"},
                "EdgeSize": {"exists": True, "value": 1.0},
                "mmd_shader_outline_enabled": {"exists": True, "value": True},
                "mmdDoubleSided": {"exists": True, "value": False},
                "mmdTransparencyMode": {"exists": True, "value": "opaque"},
            }
        )
        return new

    materials.try_apply_native_material_outline_patch = native_outline
    materials.apply_material_value_patch = lambda *_args: (_ for _ in ()).throw(
        AssertionError("native outline must not fall back after availability")
    )
    materials.apply_material_outline = lambda *_args: (_ for _ in ()).throw(
        AssertionError("native outline must not use the Python outline writer")
    )
    backend.begin_material_value_patch = lambda *_args: events.append("begin:outline")
    backend.commit_material_value_patch = lambda *_args: events.append("commit:outline")

    assert coordinator.apply_material_value_patch("|root", prior, outline_enabled=True) == prior
    assert events == ["begin:outline", "native:outline", "commit:outline"]


def test_coordinator_registered_outline_failure_rolls_back_without_python_fallback():
    coordinator, backend, materials, _ = _coordinator()
    prior = backend.scene.materials[0]
    coordinator._metadata.read_material_value = lambda *_args: prior
    materials.native_material_outline_patch_available = lambda: True
    materials.try_apply_native_material_outline_patch = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        NativeCommandProtocolError("registered outline failure")
    )
    materials.apply_material_value_patch = lambda *_args: (_ for _ in ()).throw(
        AssertionError("registered native failure must not fall back")
    )
    materials.apply_material_outline = lambda *_args: (_ for _ in ()).throw(
        AssertionError("registered native failure must not use Python outline")
    )
    backend.begin_material_value_patch = lambda *_args: backend.events.append("begin:outline")
    backend.commit_material_value_patch = lambda *_args: (_ for _ in ()).throw(
        AssertionError("registered native failure must not commit")
    )
    backend.rollback_write = lambda *_args: backend.events.append("rollback:outline")

    with pytest.raises(Exception, match="apply_material_value_patch failed"):
        coordinator.apply_material_value_patch("|root", prior, outline_enabled=True)

    assert backend.events == ["begin:outline", "rollback:outline"]


def test_cpp_source_fixes_identity_policy_and_transaction_authority():
    source = (
        Path(__file__).resolve().parents[2]
        / "cpp"
        / "src"
        / "MmdAuthoringMaterialOutlineCommand.cpp"
    ).read_text(encoding="utf-8")
    assert 'utf8(fn.typeName()) != "dx11Shader"' in source
    assert 'findPlug("mmd_model_registry_schema"' in source
    assert 'findPlug("modelRoot"' in source
    assert '"mmdTransparencyMode", FieldSpec::String' in source
    assert "outline_preimage_mismatch" in source
    assert "mutation.before" in source
    assert "rollback could not be verified" in source
    assert "fixed DX11 domain" in source
    assert '{"opaque", "cutout", "blend"}' in source
    assert 'target["EdgeSize"].get<double>() > 2.0' in source
