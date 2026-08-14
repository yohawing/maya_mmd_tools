"""Focused tests for the selected-material value patch transaction."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from tests.common.maya_stub import install_maya_stub, install_headless_ui_stubs

install_maya_stub()
install_headless_ui_stubs()

from mmd_tools.adapters.maya_scene_metadata_backend import (  # noqa: E402
    MayaSceneMetadataBackend,
    MayaSceneMetadataError,
)
from mmd_tools.adapters.maya_material_shader_route import (  # noqa: E402
    material_diffuse_route,
    material_shader_route,
)
from mmd_tools.adapters.maya_material_authoring import MayaMaterialAuthoring  # noqa: E402
from mmd_tools.core.material_authoring import classify_material_change  # noqa: E402
from mmd_tools.core.model_authoring_spec import (  # noqa: E402
    MmdMaterialSpec,
)
from tests.unit.test_material_presenter import TestMaterialPresenter  # noqa: E402
from tests.unit.test_maya_material_authoring import (  # noqa: E402
    FakeCmdsAdapter,
    FakeRegistry,
    _authoring,
    _material,
)
from tests.unit.test_maya_model_authoring_coordinator import _coordinator  # noqa: E402
from tests.unit.test_maya_scene_metadata_backend import _material as _backend_material  # noqa: E402
from tests.unit.test_maya_scene_metadata_backend import _writable_scene  # noqa: E402


def test_classifier_routes_noop_value_binding_and_mixed_changes() -> None:
    prior = MmdMaterialSpec("A", index=0, binding_identity="shader")
    assert classify_material_change(prior, prior) == "noop"
    assert classify_material_change(prior, replace(prior, name="B")) == "value"
    assert classify_material_change(prior, replace(prior, resolved_texture_path="C:/a.png")) == "binding"
    assert classify_material_change(
        prior,
        replace(prior, name="B", resolved_texture_path="C:/a.png"),
    ) == "binding"


def test_diffuse_route_is_backend_specific_and_texture_aware() -> None:
    assert material_diffuse_route("standardSurface", has_main_texture=False).diffuse_attribute == "baseColor"
    assert material_diffuse_route("standardSurface", has_main_texture=True) is None
    assert material_diffuse_route("dx11Shader", has_main_texture=False).diffuse_attribute == "DiffuseColorRGB"
    assert material_diffuse_route("dx11Shader", has_main_texture=True).diffuse_attribute == "DiffuseColorRGB"
    assert material_diffuse_route("GLSLShader", has_main_texture=False).diffuse_attribute == "DiffuseColorRGB"
    assert material_diffuse_route("lambert", has_main_texture=False).diffuse_attribute == "color"
    assert material_diffuse_route("unknownShader", has_main_texture=False) is None


def test_material_shader_route_uses_hardware_main_texture_contract() -> None:
    for shader_type in ("dx11Shader", "GLSLShader"):
        route = material_shader_route(shader_type)
        assert route is not None
        assert route.diffuse_attribute == "DiffuseColorRGB"
        main = route.texture_slot("main")
        sphere = route.texture_slot("sphere")
        toon = route.texture_slot("toon")
        assert main is not None
        assert main.texture_attribute == "MainTexture"
        assert main.presence_attribute == "HasMainTexture"
        assert sphere is not None
        assert sphere.texture_attribute == "SphereTexture"
        assert sphere.presence_attribute == "HasSphereTexture"
        assert toon is not None
        assert toon.texture_attribute == "ToonTexture"
        assert toon.presence_attribute == "HasToonTexture"
    assert material_shader_route("unknownShader") is None


def test_adapter_writes_only_changed_values_and_keeps_texture_graph_untouched() -> None:
    cmds = FakeCmdsAdapter()
    registry = FakeRegistry()
    adapter = _authoring(cmds, registry)
    bound, shader, _ = adapter.create_material("|Model_root", _material())
    old = bound

    cmds.calls.clear()
    adapter.apply_material_value_patch(
        "|Model_root",
        old,
        replace(bound, name="edited"),
    )
    assert {
        call[1][0].rsplit(".", 1)[1]
        for call in cmds.calls
        if call[0] == "set_attr"
    } == {"mmd_material_name"}
    assert not any(call[0] == "shading_node" for call in cmds.calls)
    assert not any(
        call[0] == "list_connections" and call[2].get("type") == "file"
        for call in cmds.calls
    )

    cmds.calls.clear()
    adapter.apply_material_value_patch(
        "|Model_root",
        old,
        replace(bound, diffuse=(0.2, 0.3, 0.4, 1.0)),
    )
    written = {
        call[1][0].rsplit(".", 1)[1]
        for call in cmds.calls
        if call[0] == "set_attr"
    }
    assert written == {"diffuse_color", "mmd_diffuse_alpha"}
    assert "baseColor" not in written  # resolved main texture owns the graph


def test_adapter_diffuse_patch_updates_base_color_without_resolved_texture() -> None:
    cmds = FakeCmdsAdapter()
    registry = FakeRegistry()
    adapter = _authoring(cmds, registry)
    source = replace(_material(), texture_path=None, resolved_texture_path=None)
    bound, _shader, _ = adapter.create_material("|Model_root", source)
    old = bound
    cmds.calls.clear()
    adapter.apply_material_value_patch(
        "|Model_root",
        old,
        replace(bound, diffuse=(0.2, 0.3, 0.4, 1.0)),
    )
    written = {
        call[1][0].rsplit(".", 1)[1]
        for call in cmds.calls
        if call[0] == "set_attr"
    }
    assert "baseColor" in written


def test_adapter_dx11_diffuse_patch_updates_hardware_parameter_without_base_color() -> None:
    cmds = FakeCmdsAdapter()
    registry = FakeRegistry()
    adapter = _authoring(cmds, registry)
    source = replace(_material(), texture_path=None, resolved_texture_path=None)
    bound, shader, _ = adapter.create_material("|Model_root", source)
    cmds.types[shader] = "dx11Shader"
    cmds.attrs.pop((shader, "baseColor"), None)
    cmds.attrs[(shader, "DiffuseColorRGB")] = tuple(bound.diffuse[:3])
    cmds.calls.clear()

    adapter.apply_material_value_patch(
        "|Model_root",
        bound,
        replace(bound, diffuse=(0.2, 0.3, 0.4, 1.0)),
    )

    written = {
        call[1][0].rsplit(".", 1)[1]
        for call in cmds.calls
        if call[0] == "set_attr"
    }
    assert "DiffuseColorRGB" in written
    assert "baseColor" not in written


def test_backend_dx11_value_patch_uses_hardware_diffuse_route() -> None:
    cmds, backend, _adapter = _writable_scene()
    old = backend.read_material_value("|root", "mat", 0)
    cmds.node_types["mat"] = "dx11Shader"
    cmds.attrs[("mat", "DiffuseColorRGB")] = [backend._maya_float3(old.diffuse[:3])]

    backend.begin_material_value_patch("|root", "mat", old, replace(old, name="edited"))

    assert backend._write_transaction is not None
    assert backend._write_transaction["diffuse_route"].diffuse_attribute == "DiffuseColorRGB"
    backend.rollback_write("|root")


def test_adapter_binding_patch_updates_only_selected_texture_graph() -> None:
    cmds = FakeCmdsAdapter()
    registry = FakeRegistry()
    rebuilds: list[str] = []
    adapter = MayaMaterialAuthoring(
        cmds,
        registry,
        runtime_rebuilders={"material": lambda root: rebuilds.append(root)},
    )
    bound, shader, _ = adapter.create_material("|Model_root", _material())
    file_node = next(
        call[1][0]
        for call in cmds.calls
        if call[0] == "shading_node" and call[1][0] == "file"
    )
    cmds.connections[f"{shader}.baseColor"] = [file_node]
    updated = replace(bound, resolved_texture_path="C:/textures/edited.png")

    cmds.calls.clear()
    result = adapter.apply_material_binding_patch("|Model_root", bound, updated)

    assert result == updated
    assert any(
        call[0] == "set_attr"
        and call[1][0].endswith(".fileTextureName")
        and call[1][1] == "C:/textures/edited.png"
        for call in cmds.calls
    )
    assert not any(
        call[0] == "set_attr" and call[1][0].endswith(".baseColor")
        for call in cmds.calls
    )
    assert rebuilds == ["|Model_root", "|Model_root"]
    assert shader == updated.binding_identity


def test_binding_patch_removes_texture_graph_before_writing_base_color() -> None:
    """Clearing a resolved texture disconnects it before baseColor is set."""
    cmds = FakeCmdsAdapter()
    registry = FakeRegistry()
    adapter = _authoring(cmds, registry)
    bound, shader, shading_group = adapter.create_material("|Model_root", _material())

    file_node = next(
        call[1][0]
        for call in cmds.calls
        if call[0] == "shading_node" and call[1][0] == "file"
    )
    # Model the bidirectional Maya connection used by listConnections().
    cmds.connections[shader] = [shading_group, file_node]
    cmds.connections[f"{shader}.baseColor"] = [file_node]
    cmds.connections[file_node] = [shader]
    cmds.types[file_node] = "file"
    cmds.calls.clear()

    updated = replace(bound, texture_path=None, resolved_texture_path=None)
    adapter.apply_material_binding_patch("|Model_root", bound, updated)

    disconnect_index = next(
        index
        for index, call in enumerate(cmds.calls)
        if call[0] == "disconnect_attr" and call[1][1].endswith(".baseColor")
    )
    delete_index = next(
        index
        for index, call in enumerate(cmds.calls)
        if call[0] == "delete" and call[1][0] == file_node
    )
    base_color_index = next(
        index
        for index, call in enumerate(cmds.calls)
        if call[0] == "set_attr" and call[1][0].endswith(".baseColor")
    )
    assert disconnect_index < base_color_index
    assert delete_index < base_color_index


def test_binding_patch_uses_dx11_main_texture_and_presence_flag() -> None:
    cmds = FakeCmdsAdapter()
    registry = FakeRegistry()
    adapter = _authoring(cmds, registry)
    source = replace(_material(), texture_path=None, resolved_texture_path=None)
    bound, shader, shading_group = adapter.create_material("|Model_root", source)
    cmds.types[shader] = "dx11Shader"
    cmds.attrs.pop((shader, "baseColor"), None)
    cmds.attrs[(shader, "DiffuseColorRGB")] = tuple(bound.diffuse[:3])
    cmds.connections[shader] = [shading_group]
    cmds.calls.clear()

    updated = replace(
        bound,
        texture_path="textures/edited.png",
        resolved_texture_path="C:/textures/edited.png",
    )
    adapter.apply_material_binding_patch("|Model_root", bound, updated)

    assert any(
        call[0] == "connect_attr"
        and call[1][1].endswith(".MainTexture")
        for call in cmds.calls
    )
    assert any(
        call[0] == "set_attr"
        and call[1][0].endswith(".HasMainTexture")
        and call[1][1] == 1
        for call in cmds.calls
    )
    assert not any(
        call[0] == "connect_attr" and call[1][1].endswith(".baseColor")
        for call in cmds.calls
    )


def test_binding_patch_binds_dx11_sphere_slot_without_reconnecting_main() -> None:
    cmds = FakeCmdsAdapter()
    registry = FakeRegistry()
    adapter = _authoring(cmds, registry)
    bound, shader, shading_group = adapter.create_material("|Model_root", _material())
    main_file = next(
        node for node, node_type in cmds.types.items() if node_type == "file"
    )
    cmds.types[shader] = "dx11Shader"
    cmds.attrs.pop((shader, "baseColor"), None)
    cmds.attrs[(shader, "DiffuseColorRGB")] = tuple(bound.diffuse[:3])
    cmds.attrs[(shader, "HasMainTexture")] = 1
    cmds.connections[shader] = [shading_group, main_file]
    cmds.connections[f"{shader}.MainTexture"] = [f"{main_file}.outColor"]
    cmds.calls.clear()

    updated = replace(
        bound,
        sphere_texture_path="C:/textures/sphere.png",
        resolved_sphere_texture_path="C:/textures/sphere.png",
        sphere_mode=1,
    )
    adapter.apply_material_binding_patch("|Model_root", bound, updated)

    main_connects = [
        call
        for call in cmds.calls
        if call[0] == "connect_attr" and call[1][1].endswith(".MainTexture")
    ]
    assert not main_connects, main_connects
    assert any(
        call[0] == "connect_attr" and call[1][1].endswith(".SphereTexture")
        for call in cmds.calls
    )
    assert any(
        call[0] == "set_attr"
        and call[1][0].endswith(".HasSphereTexture")
        and call[1][1] == 1
        for call in cmds.calls
    )
    assert any(
        call[0] == "set_attr"
        and call[1][0].endswith(".SphereMode")
        and call[1][1] == 1
        for call in cmds.calls
    )


def test_binding_patch_binds_custom_toon_slot_and_clears_only_sphere() -> None:
    cmds = FakeCmdsAdapter()
    registry = FakeRegistry()
    adapter = _authoring(cmds, registry)
    source = replace(_material(), texture_path=None, resolved_texture_path=None)
    bound, shader, shading_group = adapter.create_material("|Model_root", source)
    cmds.types[shader] = "dx11Shader"
    cmds.attrs.pop((shader, "baseColor"), None)
    cmds.attrs[(shader, "DiffuseColorRGB")] = tuple(bound.diffuse[:3])
    cmds.connections[shader] = [shading_group]

    textured = replace(
        bound,
        sphere_texture_path="C:/textures/sphere.png",
        resolved_sphere_texture_path="C:/textures/sphere.png",
        toon_texture_path="C:/textures/toon.png",
        resolved_toon_texture_path="C:/textures/toon.png",
        shared_toon=False,
    )
    adapter.apply_material_binding_patch("|Model_root", bound, textured)
    sphere_source = cmds.connections[f"{shader}.SphereTexture"][0]
    toon_source = cmds.connections[f"{shader}.ToonTexture"][0]
    cmds.calls.clear()

    cleared = replace(
        textured,
        sphere_texture_path=None,
        resolved_sphere_texture_path=None,
    )
    adapter.apply_material_binding_patch("|Model_root", textured, cleared)

    assert any(
        call[0] == "disconnect_attr"
        and call[1] == (sphere_source, f"{shader}.SphereTexture")
        for call in cmds.calls
    )
    assert not any(
        call[0] == "disconnect_attr"
        and call[1] == (toon_source, f"{shader}.ToonTexture")
        for call in cmds.calls
    )
    assert not any(
        call[0] == "connect_attr" and call[1][1].endswith(".ToonTexture")
        for call in cmds.calls
    )


def test_binding_patch_keeps_file_node_shared_by_another_texture_slot() -> None:
    cmds = FakeCmdsAdapter()
    registry = FakeRegistry()
    adapter = _authoring(cmds, registry)
    source = replace(_material(), texture_path=None, resolved_texture_path=None)
    bound, shader, shading_group = adapter.create_material("|Model_root", source)
    cmds.types[shader] = "dx11Shader"
    cmds.attrs.pop((shader, "baseColor"), None)
    cmds.attrs[(shader, "DiffuseColorRGB")] = tuple(bound.diffuse[:3])
    shared_file = cmds.shading_node("file", name="sharedFile")
    shared_source = f"{shared_file}.outColor"
    cmds.connections[shader] = [shading_group, shared_file]
    cmds.connections[f"{shader}.MainTexture"] = [shared_source]
    cmds.connections[f"{shader}.SphereTexture"] = [shared_source]
    cmds.connections[shared_source] = [
        f"{shader}.MainTexture",
        f"{shader}.SphereTexture",
    ]
    textured = replace(
        bound,
        texture_path="C:/textures/shared.png",
        resolved_texture_path="C:/textures/shared.png",
        sphere_texture_path="C:/textures/shared.png",
        resolved_sphere_texture_path="C:/textures/shared.png",
    )
    cmds.calls.clear()

    cleared = replace(
        textured,
        sphere_texture_path=None,
        resolved_sphere_texture_path=None,
    )
    adapter.apply_material_binding_patch("|Model_root", textured, cleared)

    assert any(
        call[0] == "disconnect_attr"
        and call[1] == (shared_source, f"{shader}.SphereTexture")
        for call in cmds.calls
    )
    assert not any(
        call[0] == "delete" and call[1] == (shared_file,)
        for call in cmds.calls
    )


def test_binding_patch_clears_dx11_main_texture_and_presence_flag() -> None:
    cmds = FakeCmdsAdapter()
    registry = FakeRegistry()
    adapter = _authoring(cmds, registry)
    bound, shader, shading_group = adapter.create_material("|Model_root", _material())
    file_node = next(
        call[1][0]
        for call in cmds.calls
        if call[0] == "shading_node" and call[1][0] == "file"
    )
    cmds.types[shader] = "dx11Shader"
    cmds.attrs.pop((shader, "baseColor"), None)
    cmds.attrs[(shader, "DiffuseColorRGB")] = tuple(bound.diffuse[:3])
    cmds.attrs[(shader, "HasMainTexture")] = 1
    cmds.connections[shader] = [shading_group, file_node]
    cmds.connections[f"{shader}.MainTexture"] = [file_node]
    cmds.connections[file_node] = [shader]
    cmds.types[file_node] = "file"
    cmds.calls.clear()

    updated = replace(bound, texture_path=None, resolved_texture_path=None)
    adapter.apply_material_binding_patch("|Model_root", bound, updated)

    assert any(
        call[0] == "disconnect_attr"
        and call[1][1].endswith(".MainTexture")
        for call in cmds.calls
    )
    assert any(call[0] == "delete" and call[1][0] == file_node for call in cmds.calls)
    assert any(
        call[0] == "set_attr"
        and call[1][0].endswith(".HasMainTexture")
        and call[1][1] == 0
        for call in cmds.calls
    )
    assert not any(
        call[0] == "disconnect_attr" and call[1][1].endswith(".baseColor")
        for call in cmds.calls
    )


def test_binding_patch_ignores_unrelated_file_nodes_for_dx11_main_route() -> None:
    cmds = FakeCmdsAdapter()
    registry = FakeRegistry()
    adapter = _authoring(cmds, registry)
    source = replace(_material(), texture_path=None, resolved_texture_path=None)
    bound, shader, shading_group = adapter.create_material("|Model_root", source)
    cmds.types[shader] = "dx11Shader"
    cmds.attrs.pop((shader, "baseColor"), None)
    cmds.attrs[(shader, "DiffuseColorRGB")] = tuple(bound.diffuse[:3])

    main_file = cmds.shading_node("file", name="mainFile")
    sphere_file = cmds.shading_node("file", name="sphereFile")
    toon_file = cmds.shading_node("file", name="toonFile")
    cmds.connections[shader] = [shading_group, sphere_file, toon_file]
    cmds.connections[f"{shader}.MainTexture"] = [main_file]
    cmds.connections[main_file] = [shader]
    cmds.types[main_file] = "file"
    cmds.types[sphere_file] = "file"
    cmds.types[toon_file] = "file"
    cmds.calls.clear()

    updated = replace(
        bound,
        texture_path="textures/edited.png",
        resolved_texture_path="C:/textures/edited.png",
    )
    result = adapter.apply_material_binding_patch("|Model_root", bound, updated)
    assert result == updated
    assert not any(call[0] == "shading_node" for call in cmds.calls)
    assert any(
        call[0] == "connect_attr"
        and call[1] == (f"{main_file}.outColor", f"{shader}.MainTexture")
        for call in cmds.calls
    )

    cmds.calls.clear()
    cleared = replace(updated, texture_path=None, resolved_texture_path=None)
    adapter.apply_material_binding_patch("|Model_root", result, cleared)
    assert any(
        call[0] == "disconnect_attr"
        and call[1] == (f"{main_file}.outColor", f"{shader}.MainTexture")
        for call in cmds.calls
    )
    assert any(call[0] == "delete" and call[1] == (main_file,) for call in cmds.calls)
    assert not any(call[0] == "delete" and call[1] in {(sphere_file,), (toon_file,)} for call in cmds.calls)


def test_adapter_narrow_create_skips_runtime_rebuild_but_clones_local_texture() -> None:
    cmds = FakeCmdsAdapter()
    registry = FakeRegistry()
    adapter = MayaMaterialAuthoring(
        cmds,
        registry,
        runtime_rebuilders={"material": lambda _root: (_ for _ in ()).throw(AssertionError("runtime rebuild"))},
    )
    source = replace(_material(), index=0, binding_identity=None)
    bound, shader, _ = adapter.create_material("|Model_root", source, narrow=True)
    assert bound.binding_identity == shader
    assert any(call[0] == "shading_node" and call[1][0] == "file" for call in cmds.calls)
    # The narrow create is not allowed to rebuild the model-wide Material Morph graph.
    assert not any("material_morph" in str(call) for call in cmds.calls)


def test_coordinator_material_create_uses_only_narrow_hooks() -> None:
    coordinator, backend, _materials, _ = _coordinator()
    backend.events.clear()
    coordinator._metadata.read_spec = lambda _root: (_ for _ in ()).throw(AssertionError("full read"))

    created = coordinator.create_material("|root")

    assert isinstance(created, MmdMaterialSpec)
    assert created.index == 1
    assert backend.events == ["begin:material_create", "commit:material_create"]
    assert backend.rebase_count == 0


def test_backend_material_create_strict_registry_shader_sg_readback() -> None:
    cmds, backend, _adapter = _writable_scene()
    backend.begin_material_create("|root", 1)
    _backend_material(cmds, "mat2", 1)
    cmds.connections[("mat2", "shadingEngine")] = ["mat2SG"]
    cmds.nodes.add("mat2SG")
    cmds.node_types["mat2SG"] = "shadingEngine"
    cmds.connections[("registry.materialMembers", None)].append("mat2")
    actual = MmdMaterialSpec.from_mapping(backend._read_material("mat2"))

    backend.commit_material_create("|root", actual)

    assert cmds.undo_chunk_open is False


def test_coordinator_material_create_commit_failure_rolls_back_without_full_read() -> None:
    coordinator, backend, _materials, _ = _coordinator()
    coordinator._metadata.commit_material_create = lambda *_args: (_ for _ in ()).throw(
        RuntimeError("readback mismatch")
    )
    coordinator._metadata.read_spec = lambda _root: (_ for _ in ()).throw(AssertionError("full read"))

    with pytest.raises(Exception, match="create_material failed"):
        coordinator.create_material("|root")

    assert backend.rollback_count == 1
    assert backend.events[-1] == "rollback"


def test_adapter_value_patch_writes_changed_color_coefficient_and_flags_only() -> None:
    cmds = FakeCmdsAdapter()
    registry = FakeRegistry()
    adapter = _authoring(cmds, registry)
    bound, _shader, _ = adapter.create_material("|Model_root", _material())
    old = bound
    updated = replace(
        bound,
        specular=(0.1, 0.2, 0.3),
        specular_coefficient=0.4,
        ambient=(0.2, 0.3, 0.4),
        draw_flags=0x10,
        edge_color=(0.5, 0.6, 0.7, 0.8),
        edge_size=2.0,
    )
    cmds.calls.clear()
    adapter.apply_material_value_patch("|Model_root", old, updated)
    written = {
        call[1][0].rsplit(".", 1)[1]
        for call in cmds.calls
        if call[0] == "set_attr"
    }
    assert written == {
        "specular_color",
        "shininess",
        "ambient_color",
        "mmd_draw_flags",
        "edge_flag",
        "mmd_edge_color",
        "mmd_edge_alpha",
        "mmd_edge_size",
    }


def test_coordinator_narrow_path_does_not_call_full_read_or_metadata_hooks() -> None:
    coordinator, backend, materials, _ = _coordinator()
    prior = backend.scene.materials[0]
    target = replace(prior, name="edited")
    full_reads: list[str] = []
    def full_read(root: str) -> None:
        full_reads.append(root)

    def selected_read(_root: str, _binding: str, _index: int) -> MmdMaterialSpec:
        return prior

    def begin_value(*_args: object) -> None:
        backend.events.append("begin:value")

    def commit_value(*_args: object) -> None:
        backend.events.append("commit:value")

    def patch_value(_root: str, _old: MmdMaterialSpec, new: MmdMaterialSpec) -> MmdMaterialSpec:
        return new

    coordinator._metadata.read_spec = full_read  # type: ignore[method-assign]
    coordinator._metadata.read_material_value = selected_read
    coordinator._backend.begin_material_value_patch = begin_value
    coordinator._metadata.commit_material_value_patch = commit_value
    materials.apply_material_value_patch = patch_value

    result = coordinator.apply_material_value_patch("|root", target)

    assert isinstance(result, MmdMaterialSpec)
    assert result.name == "edited"
    assert full_reads == []
    assert backend.events == ["begin:value", "commit:value"]


def test_coordinator_narrow_failure_rolls_back_without_full_hooks() -> None:
    coordinator, backend, materials, _ = _coordinator()
    prior = backend.scene.materials[0]
    def selected_read(_root: str, _binding: str, _index: int) -> MmdMaterialSpec:
        return prior

    def begin_value(*_args: object) -> None:
        backend.events.append("begin:value")

    def commit_value(*_args: object) -> None:
        raise RuntimeError("fingerprint mismatch")

    def rollback(_root: str) -> None:
        backend.events.append("rollback:value")

    def patch_value(_root: str, _old: MmdMaterialSpec, new: MmdMaterialSpec) -> MmdMaterialSpec:
        return new

    coordinator._metadata.read_material_value = selected_read
    coordinator._backend.begin_material_value_patch = begin_value
    coordinator._metadata.commit_material_value_patch = commit_value
    backend.rollback_write = rollback
    materials.apply_material_value_patch = patch_value

    with pytest.raises(Exception, match="apply_material_value_patch failed"):
        coordinator.apply_material_value_patch("|root", replace(prior, name="edited"))
    assert backend.events == ["begin:value", "rollback:value"]


def test_coordinator_binding_patch_does_not_read_unrelated_materials() -> None:
    coordinator, backend, materials, _ = _coordinator()
    prior = backend.scene.materials[0]
    target = replace(prior, resolved_texture_path="C:/textures/edited.png")
    coordinator._metadata.read_spec = lambda _root: (_ for _ in ()).throw(  # type: ignore[method-assign]
        AssertionError("full read is forbidden")
    )
    coordinator._metadata.read_material_value = lambda _root, _binding, _index: prior
    backend.begin_material_binding_patch = lambda *_args: backend.events.append("begin:binding")
    coordinator._metadata.commit_material_binding_patch = lambda *_args: backend.events.append(
        "commit:binding"
    )
    materials.apply_material_binding_patch = lambda _root, _old, new: new

    result = coordinator.apply_material_binding_patch("|root", target)

    assert result == target
    assert backend.events == ["begin:binding", "commit:binding"]
    assert backend.rebase_count == 0


def test_coordinator_noop_does_not_open_a_narrow_transaction() -> None:
    coordinator, backend, _materials, _ = _coordinator()
    prior = backend.scene.materials[0]
    coordinator._metadata.read_material_value = lambda _root, _binding, _index: prior  # noqa: E731
    backend.begin_material_value_patch = lambda *_args: backend.events.append("begin:value")  # noqa: E731
    coordinator._metadata.commit_material_value_patch = lambda *_args: backend.events.append("commit:value")  # noqa: E731
    result = coordinator.apply_material_value_patch("|root", prior)
    assert result == prior
    assert backend.events == []


class _SelectedMaterialCmds:
    def __init__(self, root: str, shader: str) -> None:
        self.root = root
        self.shader = shader
        self.attrs: dict[tuple[str, str], object] = {}

    def ls(self, node: str, **_kwargs: object) -> list[str]:
        return [node]

    def attribute_exists(self, attr: str, node: str) -> bool:
        return (node, attr) in self.attrs

    def get_attr(self, path: str) -> object:
        node, attr = path.rsplit(".", 1)
        return self.attrs[(node, attr)]

    def list_connections(self, _query: str, **_kwargs: object) -> list[str]:
        return []

    def undo_info(self, **kwargs: object) -> bool:
        return bool(kwargs.get("query"))


def test_selected_reader_requires_registry_ownership_and_matching_index() -> None:
    root, shader = "|root", "shader"
    cmds = _SelectedMaterialCmds(root, shader)
    backend = MayaSceneMetadataBackend(cmds)
    def members(_root: str) -> list[str]:
        return [shader]

    backend._registry_material_members = members  # type: ignore[method-assign]
    material = MmdMaterialSpec("A", index=3, binding_identity=shader)
    from mmd_tools.core.constants import ATTR_MMD_MATERIAL_INDEX

    cmds.attrs[(shader, ATTR_MMD_MATERIAL_INDEX)] = 3
    def read_material(_shader: str) -> dict[str, object]:
        return material.to_mapping()

    backend._read_material = read_material  # type: ignore[method-assign]
    assert backend.read_material_value(root, shader, 3) == material
    with pytest.raises(MayaSceneMetadataError, match="index mismatch"):
        backend.read_material_value(root, shader, 4)
    def no_members(_root: str) -> list[str]:
        return []

    backend._registry_material_members = no_members  # type: ignore[method-assign]
    with pytest.raises(MayaSceneMetadataError, match="not owned"):
        backend.read_material_value(root, shader, 3)


def test_backend_canonicalizes_base_color_to_maya_float3_precision() -> None:
    assert MayaSceneMetadataBackend._maya_float3((0.72, 0.48, 0.36)) == (
        0.7200000286102295,
        0.47999998927116394,
        0.36000001430511475,
    )


def test_backend_value_commit_accepts_maya_float_round_trip_precision() -> None:
    cmds, backend, _adapter = _writable_scene()
    old = backend.read_material_value("|root", "mat", 0)
    new = replace(old, edge_size=0.6)
    cmds.attrs[("mat", "baseColor")] = [tuple(old.diffuse[:3])]

    backend.begin_material_value_patch("|root", "mat", old, new)
    cmds.set_attr("mat.mmd_edge_size", 0.6000000238418579)
    backend.commit_material_value_patch("|root", "mat", new)

    assert cmds.undo_chunk_open is False


def test_backend_value_commit_rejects_materially_different_float() -> None:
    cmds, backend, _adapter = _writable_scene()
    old = backend.read_material_value("|root", "mat", 0)
    new = replace(old, edge_size=0.6)
    cmds.attrs[("mat", "baseColor")] = [tuple(old.diffuse[:3])]

    backend.begin_material_value_patch("|root", "mat", old, new)
    cmds.set_attr("mat.mmd_edge_size", 0.61)
    with pytest.raises(MayaSceneMetadataError, match="fingerprint mismatch"):
        backend.commit_material_value_patch("|root", "mat", new)
    backend.rollback_write("|root")

    assert cmds.attrs[("mat", "mmd_edge_size")] == old.edge_size


def test_backend_value_commit_mismatch_rolls_back_selected_preimage() -> None:
    class UndoCmds(FakeCmdsAdapter):
        def __init__(self) -> None:
            super().__init__()
            self._undo_attrs: dict[tuple[str, str], object] | None = None

        def undo_info(self, **kwargs: object) -> bool:
            if kwargs.get("query"):
                return True
            if kwargs.get("openChunk"):
                self._undo_attrs = dict(self.attrs)
            return True

        def undo(self) -> None:
            if self._undo_attrs is not None:
                self.attrs = self._undo_attrs

    cmds = UndoCmds()
    registry = FakeRegistry()
    authoring = _authoring(cmds, registry)
    bound, shader, _ = authoring.create_material("|Model_root", _material())
    backend = MayaSceneMetadataBackend(cmds)
    backend._registry_material_members = lambda _root: [shader]  # noqa: E731
    old = bound
    new = replace(old, name="edited")
    backend.begin_material_value_patch("|Model_root", shader, old, new)
    cmds.set_attr(f"{shader}.mmd_material_name", "wrong", type="string")
    with pytest.raises(MayaSceneMetadataError, match="fingerprint mismatch"):
        backend.commit_material_value_patch("|Model_root", shader, new)
    backend.rollback_write("|Model_root")
    assert cmds.attrs[(shader, "mmd_material_name")] == old.name


def test_backend_binding_patch_verifies_selected_material_and_rolls_back() -> None:
    cmds, backend, _adapter = _writable_scene()
    cmds.attrs[("mat", "mmd_texture_path")] = "textures/original.png"
    cmds.attrs[("mat", "mmd_resolved_texture_path")] = "C:/textures/original.png"
    old = backend.read_material_value("|root", "mat", 0)
    new = replace(old, resolved_texture_path="C:/textures/edited.png")

    backend.begin_material_binding_patch("|root", "mat", old, new)
    cmds.set_attr("mat.mmd_resolved_texture_path", "C:/textures/edited.png", type="string")
    backend.commit_material_binding_patch("|root", "mat", new)

    assert cmds.undo_chunk_open is False
    assert backend.read_material_value("|root", "mat", 0) == new

    backend.begin_material_binding_patch("|root", "mat", new, old)
    cmds.set_attr("mat.mmd_resolved_texture_path", "C:/textures/wrong.png", type="string")
    with pytest.raises(MayaSceneMetadataError, match="fingerprint mismatch"):
        backend.commit_material_binding_patch("|root", "mat", old)
    backend.rollback_write("|root")
    assert backend.read_material_value("|root", "mat", 0) == new


def test_presenter_value_apply_uses_selected_row_without_full_reload() -> None:
    fixture = TestMaterialPresenter()
    fixture.setUp()
    presenter = fixture.presenter
    prior = MmdMaterialSpec("A", name_english="A", index=0, binding_identity="shader")
    coordinator = Mock()
    def selected_read(_root: str, _index: int, _binding: str) -> MmdMaterialSpec:
        return prior

    def apply_value(_root: str, material: MmdMaterialSpec) -> MmdMaterialSpec:
        return material

    coordinator.read_material_value.side_effect = selected_read
    coordinator.apply_material_value_patch.side_effect = apply_value
    coordinator.read_spec.side_effect = AssertionError("full read is forbidden")
    coordinator.replace_material.side_effect = AssertionError("full replace is forbidden")
    presenter.load_materials = Mock(side_effect=AssertionError("full list reload is forbidden"))
    presenter.authoring_coordinator = coordinator
    presenter.current_material = "shader"
    presenter.current_material_index = 0
    presenter.app_state.current_model_root = "|root"
    presenter.material_data = {
        "diffuse": (1.0, 1.0, 1.0),
        "specular": (0.0, 0.0, 0.0),
        "ambient": (0.0, 0.0, 0.0),
        "edge_color": (0.0, 0.0, 0.0),
        "edge_alpha": 1.0,
    }
    presenter.view.material_jp_name_edit.text.return_value = "B"
    presenter.view.material_en_name_edit.text.return_value = "A"
    presenter.view.texture_path_edit.text.return_value = ""
    presenter.view.sphere_map_path_edit.text.return_value = ""
    presenter.view.transparency_spin.value.return_value = 0.0
    presenter.view.specular_coefficient_spin.value.return_value = 0.0
    presenter.view.edge_size_spin.value.return_value = 1.0
    presenter.view.sphere_mode_combo.currentIndex.return_value = 0
    presenter.view.toon_sharing_check.isChecked.return_value = False
    presenter.view.toon_texture_index_spin.value.return_value = -1
    presenter.view.toon_texture_path_edit.text.return_value = ""
    for name in (
        "both_face_check",
        "ground_shadow_check",
        "self_shadow_map_check",
        "self_shadow_check",
        "edge_draw_check",
        "vertex_color_check",
        "point_draw_check",
        "line_draw_check",
    ):
        getattr(presenter.view, name).isChecked.return_value = False

    row = Mock()
    row.data.side_effect = lambda role: "shader" if role == 256 else None
    presenter.view.material_list.count.return_value = 1
    presenter.view.material_list.item.return_value = row

    result = presenter._apply_authoring_changes()

    assert isinstance(result, MmdMaterialSpec)
    coordinator.read_spec.assert_not_called()
    coordinator.replace_material.assert_not_called()
    presenter.load_materials.assert_not_called()
    row.setText.assert_called_once()
    assert presenter.has_unsaved_changes is False


def test_presenter_texture_edit_uses_selected_binding_route() -> None:
    fixture = TestMaterialPresenter()
    fixture.setUp()
    presenter = fixture.presenter
    prior = MmdMaterialSpec(
        "A",
        name_english="A",
        index=0,
        sphere_texture_path="sphere.png",
        binding_identity="shader",
    )
    coordinator = Mock()
    coordinator.read_material_value.side_effect = lambda _root, _index, _binding: prior  # noqa: E731
    coordinator.apply_material_binding_patch.side_effect = lambda _root, material: material  # noqa: E731
    coordinator.read_spec.side_effect = AssertionError("full read is forbidden")
    coordinator.replace_material.side_effect = AssertionError("full replace is forbidden")
    presenter.authoring_coordinator = coordinator
    presenter.current_material = "shader"
    presenter.current_material_index = 0
    presenter.app_state.current_model_root = "|root"
    presenter.material_data = {
        "diffuse": (1.0, 1.0, 1.0),
        "specular": (0.0, 0.0, 0.0),
        "ambient": (0.0, 0.0, 0.0),
        "edge_color": (0.0, 0.0, 0.0),
        "edge_alpha": 1.0,
    }
    presenter.view.material_jp_name_edit.text.return_value = "A"
    presenter.view.material_en_name_edit.text.return_value = "A"
    presenter.view.texture_path_edit.text.return_value = ""
    presenter.view.sphere_map_path_edit.text.return_value = ""
    presenter.view.transparency_spin.value.return_value = 0.0
    presenter.view.specular_coefficient_spin.value.return_value = 0.0
    presenter.view.edge_size_spin.value.return_value = 1.0
    presenter.view.sphere_mode_combo.currentIndex.return_value = 0
    presenter.view.toon_sharing_check.isChecked.return_value = False
    presenter.view.toon_texture_index_spin.value.return_value = -1
    presenter.view.toon_texture_path_edit.text.return_value = ""
    for name in (
        "both_face_check",
        "ground_shadow_check",
        "self_shadow_map_check",
        "self_shadow_check",
        "edge_draw_check",
        "vertex_color_check",
        "point_draw_check",
        "line_draw_check",
    ):
        getattr(presenter.view, name).isChecked.return_value = False

    presenter._apply_authoring_changes()

    coordinator.apply_material_binding_patch.assert_called_once()
    replacement = coordinator.apply_material_binding_patch.call_args.args[1]
    assert replacement.sphere_texture_path is None
    assert replacement.resolved_sphere_texture_path is None
    coordinator.replace_material.assert_not_called()
    coordinator.read_spec.assert_not_called()
    coordinator.apply_material_value_patch.assert_not_called()


def test_presenter_absolute_aux_texture_path_is_also_resolved() -> None:
    path = "C:/textures/sphere.png"
    from mmd_tools.ui.presenters.material_presenter import MaterialPresenter

    assert MaterialPresenter._authoring_aux_texture_paths(None, None, path) == (
        path,
        path,
    )
    assert MaterialPresenter._authoring_aux_texture_paths("", None, "") == (
        None,
        None,
    )


def test_presenter_absolute_main_texture_path_keeps_export_provenance() -> None:
    fixture = TestMaterialPresenter()
    fixture.setUp()
    presenter = fixture.presenter
    path = "C:/textures/main.png"
    presenter.view.texture_path_edit.text.return_value = path
    presenter.material_data = {}

    assert presenter._authoring_main_texture_paths(MmdMaterialSpec("A", index=0)) == (
        path,
        path,
    )


def test_presenter_rejects_only_changed_missing_texture_files(tmp_path) -> None:
    fixture = TestMaterialPresenter()
    fixture.setUp()
    presenter = fixture.presenter
    existing = tmp_path / "existing.png"
    existing.write_bytes(b"png")
    missing = tmp_path / "missing.png"
    presenter.maya_adapter.workspace.side_effect = lambda **kwargs: kwargs["expandName"]
    prior = MmdMaterialSpec(
        "A",
        index=0,
        resolved_texture_path=str(missing),
    )

    presenter._validate_changed_authoring_texture_files(prior, prior)
    presenter._validate_changed_authoring_texture_files(
        prior,
        replace(prior, resolved_texture_path=str(existing)),
    )
    with pytest.raises(ValueError, match="main texture file does not exist"):
        presenter._validate_changed_authoring_texture_files(
            prior,
            replace(prior, resolved_texture_path=str(tmp_path / "other-missing.png")),
        )
