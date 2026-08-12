"""Fake-command tests for Maya material binding authoring."""

import json
from dataclasses import dataclass, field, replace
from typing import Any

import pytest

from tests.common.maya_stub import install_maya_stub

install_maya_stub()

from mmd_tools.adapters.maya_material_authoring import (  # noqa: E402
    ATTR_MMD_DIFFUSE_ALPHA,
    ATTR_MMD_MATERIAL_INDEX,
    ATTR_MMD_MATERIAL_MORPH_OFFSETS,
    ATTR_MMD_MATERIAL_NAME,
    ATTR_MMD_ORIGINAL_TEXTURE_PATH,
    ATTR_MMD_RESOLVED_TEXTURE_PATH,
    ATTR_MMD_RESOLVED_TOON_TEXTURE_PATH,
    ATTR_MMD_SPHERE_TEXTURE_INDEX,
    ATTR_MMD_SHARED_TOON_FLAG,
    ATTR_MMD_TEXTURE_INDEX,
    ATTR_MMD_TEXTURE_PATH,
    ATTR_MMD_TOON_TEXTURE_PATH,
    MayaMaterialAuthoring,
    MayaMaterialAuthoringError,
)
from mmd_tools.core.model_authoring_spec import (  # noqa: E402
    MmdMaterialSpec,
    MmdModelAuthoringSpec,
    MmdModelSpec,
    MmdMorphSpec,
)


@dataclass
class FakeCmdsAdapter:
    """Small explicit Maya command surface with no active-selection behavior."""

    attrs: dict[tuple[str, str], Any] = field(default_factory=dict)
    types: dict[str, str] = field(default_factory=lambda: {"|Model_root": "transform"})
    connections: dict[str, list[str]] = field(default_factory=dict)
    members: dict[str, list[str]] = field(default_factory=dict)
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = field(default_factory=list)
    sequence: int = 0

    def object_exists(self, node: str) -> bool:
        return node in self.types or node in {"|Model_root|Geometry|mesh", "|Other|mesh"}

    def ls(self, node: str, long: bool = False, **_kwargs: Any) -> list[str]:
        if node == "|Model_root" or node == "Model_root":
            return ["|Model_root"]
        if node in {"|Model_root|Geometry|mesh", "mesh", "|Other|mesh"}:
            return ["|Model_root|Geometry|mesh" if node == "mesh" else node]
        return [node] if node in self.types else []

    def attribute_exists(self, attr: str, node: str) -> bool:
        return (node, attr) in self.attrs

    def get_attr(self, path: str) -> Any:
        node, attr = path.rsplit(".", 1)
        return self.attrs[(node, attr)]

    def add_attr(self, node: str, **kwargs: Any) -> None:
        self.calls.append(("add_attr", (node,), kwargs))
        attr = kwargs.get("longName") or kwargs.get("long_name")
        self.attrs.setdefault((node, attr), None)

    def set_attr(self, path: str, *values: Any, **kwargs: Any) -> None:
        node, attr = path.rsplit(".", 1)
        self.calls.append(("set_attr", (path, *values), kwargs))
        self.attrs[(node, attr)] = tuple(values) if len(values) > 1 else values[0]

    def shading_node(self, node_type: str, **kwargs: Any) -> str:
        self.sequence += 1
        node = f"{kwargs.get('name', node_type)}{self.sequence}"
        self.types[node] = node_type
        self.calls.append(("shading_node", (node_type,), kwargs))
        return node

    def sets(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(("sets", args, kwargs))
        if kwargs.get("query"):
            return list(self.members.get(args[0], []))
        if kwargs.get("forceElement"):
            target, shading_group = args[0], kwargs["forceElement"]
            self.members.setdefault(shading_group, []).append(target)
            return shading_group
        self.sequence += 1
        shading_group = f"mmdMaterial_{self.sequence}_SG"
        self.types[shading_group] = "shadingEngine"
        self.members.setdefault(shading_group, [])
        return shading_group

    def connect_attr(self, source: str, destination: str, **kwargs: Any) -> None:
        self.calls.append(("connect_attr", (source, destination), kwargs))
        shader = source.rsplit(".", 1)[0]
        sg = destination.rsplit(".", 1)[0]
        self.connections.setdefault(shader, []).append(sg)
        if destination.endswith(".baseColor"):
            self.connections.setdefault(sg, []).append(shader)

    def disconnect_attr(self, source: str, destination: str) -> None:
        self.calls.append(("disconnect_attr", (source, destination), {}))

    def list_connections(self, node: str, **kwargs: Any) -> list[str]:
        if kwargs.get("type") == "shadingEngine":
            return [
                candidate
                for candidate in self.connections.get(node, [])
                if self.types.get(candidate) == "shadingEngine"
            ]
        if kwargs.get("type") == "file":
            return [candidate for candidate in self.connections.get(node, []) if self.types.get(candidate) == "file"]
        return []

    def node_type(self, node: str) -> str:
        return self.types[node]

    def delete(self, node: str) -> None:
        self.calls.append(("delete", (node,), {}))
        self.types.pop(node, None)


@dataclass
class FakeRegistry:
    members: list[str] = field(default_factory=list)
    morph_members: list[str] = field(default_factory=list)
    calls: list[tuple[str, Any]] = field(default_factory=list)

    def ensure_model_registry(self, root: str) -> str:
        self.calls.append(("ensure", root))
        return "|Model_root|registry"

    def list_model_registry_members(self, root: str, category: str) -> list[str]:
        self.calls.append(("list", root, category))
        return list(self.morph_members if category == "morph" else self.members)

    def register_model_members(self, registry: str, category: str, members: list[str]) -> None:
        self.calls.append(("register", registry, category, list(members)))
        self.members.extend(member for member in members if member not in self.members)

    def unregister_model_members(self, registry: str, category: str, members: list[str]) -> None:
        self.calls.append(("unregister", registry, category, list(members)))
        target = self.morph_members if category == "morph" else self.members
        target[:] = [member for member in target if member not in members]


def _material() -> MmdMaterialSpec:
    return MmdMaterialSpec(
        name="材質_日本語",
        name_english="Material",
        index=4,
        texture_path="textures/顔.png",
        resolved_texture_path=r"C:\\textures\\顔.png",
        binding_identity="|Model_root|materialBinding",
    )


def _authoring_spec(
    materials: tuple[MmdMaterialSpec, ...],
    morphs: tuple[MmdMorphSpec, ...] = (),
) -> MmdModelAuthoringSpec:
    return MmdModelAuthoringSpec(
        model=MmdModelSpec(name="Model"),
        materials=materials,
        morphs=morphs,
    )


def _authoring(cmds: Any, registry: Any) -> MayaMaterialAuthoring:
    """Build the adapter with a deterministic test-only runtime rebuilder."""
    return MayaMaterialAuthoring(
        cmds,
        registry,
        runtime_rebuilders={"material": lambda _root: None},
    )


def _material_offset(material_index: int) -> dict[str, Any]:
    return {
        "material_index": material_index,
        "operation_type": 0,
        "diffuse": [1.0, 1.0, 1.0, 1.0],
        "specular": [0.0, 0.0, 0.0],
        "specular_coefficient": 0.0,
        "ambient": [0.0, 0.0, 0.0],
        "edge_color": [0.0, 0.0, 0.0, 1.0],
        "edge_size": 1.0,
        "texture_factor": [1.0, 1.0, 1.0, 1.0],
        "sphere_texture_factor": [1.0, 1.0, 1.0, 1.0],
        "toon_texture_factor": [1.0, 1.0, 1.0, 1.0],
    }


def test_create_writes_unicode_attrs_paths_and_registry() -> None:
    cmds = FakeCmdsAdapter()
    registry = FakeRegistry()
    adapter = _authoring(cmds, registry)

    bound, shader, shading_group = adapter.create_material("|Model_root", _material())

    assert cmds.types[shader] == "standardSurface"
    assert cmds.types[shading_group] == "shadingEngine"
    assert cmds.attrs[(shader, ATTR_MMD_MATERIAL_NAME)] == "材質_日本語"
    assert cmds.attrs[(shader, ATTR_MMD_MATERIAL_INDEX)] == 4
    assert bound.binding_identity == shader
    assert (shader, "mmd_material_binding_identity") not in cmds.attrs
    assert cmds.attrs[(shader, ATTR_MMD_ORIGINAL_TEXTURE_PATH)] == "textures/顔.png"
    assert cmds.attrs[(shader, ATTR_MMD_TEXTURE_INDEX)] == -1
    assert cmds.attrs[(shader, ATTR_MMD_SPHERE_TEXTURE_INDEX)] == -1
    assert cmds.attrs[(shader, ATTR_MMD_DIFFUSE_ALPHA)] == 1.0
    assert cmds.attrs[(shader, ATTR_MMD_SHARED_TOON_FLAG)] == 0
    shared_add = [
        call
        for call in cmds.calls
        if call[0] == "add_attr"
        and call[1][0] == shader
        and call[2].get("longName") == ATTR_MMD_SHARED_TOON_FLAG
    ]
    assert shared_add and shared_add[0][2]["attributeType"] == "long"
    assert registry.members == [shader]
    shader_calls = [call for call in cmds.calls if call[0] == "shading_node"]
    assert shader_calls[0][2]["name"] == "mmdMaterial_4"
    assert shader_calls[1][2]["name"] == "mmdMaterial_4_File"


def test_replace_material_updates_texture_graph_and_clears_shared_toon_provenance() -> None:
    cmds = FakeCmdsAdapter()
    registry = FakeRegistry()
    rebuild_calls: list[str] = []
    authoring = MayaMaterialAuthoring(
        cmds,
        registry,
        runtime_rebuilders={"material": lambda root: rebuild_calls.append(root)},
    )
    bound, shader, _ = authoring.create_material("|Model_root", _material())
    old_spec = _authoring_spec((bound,))
    updated = replace(
        bound,
        texture_path=None,
        resolved_texture_path=None,
        shared_toon=True,
        toon_texture_index=3,
        toon_texture_path="stale/toon.png",
        resolved_toon_texture_path=r"C:\stale\toon.png",
    )
    new_spec = _authoring_spec((updated,))

    authoring.replace_material("|Model_root", old_spec, new_spec)

    assert not [node for node, node_type in cmds.types.items() if node_type == "file"]
    assert cmds.attrs[(shader, ATTR_MMD_TEXTURE_PATH)] == ""
    assert cmds.attrs[(shader, ATTR_MMD_RESOLVED_TEXTURE_PATH)] == ""
    assert cmds.attrs[(shader, ATTR_MMD_TOON_TEXTURE_PATH)] == ""
    assert cmds.attrs[(shader, ATTR_MMD_RESOLVED_TOON_TEXTURE_PATH)] == ""
    assert rebuild_calls == ["|Model_root", "|Model_root"]


def test_replace_material_preserves_matching_texture_table_indices() -> None:
    cmds = FakeCmdsAdapter()
    registry = FakeRegistry()
    authoring = _authoring(cmds, registry)
    source = replace(_material(), sphere_texture_path="sphere.png")
    bound, shader, _ = authoring.create_material("|Model_root", source)
    cmds.attrs[(shader, ATTR_MMD_TEXTURE_INDEX)] = 7
    cmds.attrs[(shader, ATTR_MMD_SPHERE_TEXTURE_INDEX)] = 8
    old_spec = _authoring_spec((bound,))
    new_spec = _authoring_spec((replace(bound, name="edited"),))

    authoring.replace_material("|Model_root", old_spec, new_spec)

    assert cmds.attrs[(shader, ATTR_MMD_TEXTURE_INDEX)] == 7
    assert cmds.attrs[(shader, ATTR_MMD_SPHERE_TEXTURE_INDEX)] == 8


def test_existing_binding_is_reused_by_identity_and_index() -> None:
    cmds = FakeCmdsAdapter()
    registry = FakeRegistry()
    existing_shader = "|Model_root|existingShader"
    existing_sg = "|Model_root|existingSG"
    cmds.types.update({existing_shader: "standardSurface", existing_sg: "shadingEngine"})
    cmds.attrs[(existing_shader, ATTR_MMD_MATERIAL_INDEX)] = 4
    cmds.connections[existing_shader] = [existing_sg]
    registry.members = [existing_shader]
    requested = _material()
    requested = replace(requested, binding_identity=existing_shader)

    bound, shader, sg = _authoring(cmds, registry).create_material("|Model_root", requested)

    assert bound.binding_identity == existing_shader
    assert (shader, sg) == (existing_shader, existing_sg)
    assert not any(
        call[0] == "shading_node" and call[1][0] == "standardSurface"
        for call in cmds.calls
    )


def test_existing_binding_reuses_one_main_texture_file_node() -> None:
    cmds = FakeCmdsAdapter()
    registry = FakeRegistry()
    authoring = _authoring(cmds, registry)

    bound, shader, _shading_group = authoring.create_material("|Model_root", _material())
    before = [node for node, node_type in cmds.types.items() if node_type == "file"]
    call_count = sum(call[0] == "shading_node" for call in cmds.calls)

    rebound, rebound_shader, _ = authoring.create_material("|Model_root", bound)

    after = [node for node, node_type in cmds.types.items() if node_type == "file"]
    assert rebound.binding_identity == shader == rebound_shader
    assert after == before
    assert sum(call[0] == "shading_node" for call in cmds.calls) == call_count


def test_assign_requires_targets_under_root_and_never_uses_selection() -> None:
    cmds = FakeCmdsAdapter()
    registry = FakeRegistry()
    bound, shader, shading_group = _authoring(cmds, registry).create_material("|Model_root", _material())

    result = _authoring(cmds, registry).assign_material(
        "|Model_root", bound, ["|Model_root|Geometry|mesh.f[2]"]
    )
    assert result == (shader, shading_group)
    assert any(call[0] == "sets" and call[2].get("forceElement") == shading_group for call in cmds.calls)
    with pytest.raises(MayaMaterialAuthoringError, match="outside model root"):
        _authoring(cmds, registry).assign_material("|Model_root", bound, ["|Other|mesh"])


def test_delete_reassigns_members_unregisters_and_deletes_old_nodes() -> None:
    cmds = FakeCmdsAdapter()
    registry = FakeRegistry()
    authoring = _authoring(cmds, registry)
    bound, old_shader, old_sg = authoring.create_material("|Model_root", _material())
    replacement_sg = "|Model_root|replacementSG"
    cmds.types[replacement_sg] = "shadingEngine"
    cmds.members[old_sg] = ["|Model_root|Geometry|mesh.f[0]"]

    authoring.delete_material("|Model_root", bound, replacement_sg)

    assert cmds.members[replacement_sg] == ["|Model_root|Geometry|mesh.f[0]"]
    assert old_shader not in registry.members
    assert old_sg not in cmds.types
    assert old_shader not in cmds.types
    assert any(call[0] == "disconnect_attr" for call in cmds.calls)


def test_apply_material_spec_change_reindexes_survivors_and_material_morph_offsets() -> None:
    cmds = FakeCmdsAdapter(
        attrs={
            ("shaderA", ATTR_MMD_MATERIAL_INDEX): 0,
            ("shaderB", ATTR_MMD_MATERIAL_INDEX): 1,
            ("morphNode", ATTR_MMD_MATERIAL_MORPH_OFFSETS): json.dumps(
                [_material_offset(0)], separators=(",", ":")
            ),
        },
        types={
            "|Model_root": "transform",
            "shaderA": "standardSurface",
            "shaderB": "standardSurface",
            "morphNode": "network",
        },
    )
    registry = FakeRegistry(members=["shaderA", "shaderB"], morph_members=["morphNode"])
    material_a = replace(_material(), index=0, binding_identity="shaderA", name="A")
    material_b = replace(_material(), index=1, binding_identity="shaderB", name="B")
    morph = MmdMorphSpec(
        name="material morph",
        index=0,
        morph_type="material",
        offsets=(_material_offset(0),),
        binding_identity="morphNode",
    )
    old_spec = _authoring_spec((material_a, material_b), (morph,))
    new_morph = replace(morph, offsets=(_material_offset(1),))
    new_spec = _authoring_spec(
        (
            replace(material_a, index=1),
            replace(material_b, index=0),
        ),
        (new_morph,),
    )

    rebuild_calls: list[str] = []
    result = MayaMaterialAuthoring(
        cmds,
        registry,
        runtime_rebuilders={"material": lambda root: rebuild_calls.append(root)},
    ).apply_material_spec_change(
        "|Model_root", old_spec, new_spec
    )

    assert result == new_spec
    assert cmds.attrs[("shaderA", ATTR_MMD_MATERIAL_INDEX)] == 1
    assert cmds.attrs[("shaderB", ATTR_MMD_MATERIAL_INDEX)] == 0
    assert json.loads(cmds.attrs[("morphNode", ATTR_MMD_MATERIAL_MORPH_OFFSETS)])[
        0
    ]["material_index"] == 1
    assert rebuild_calls == ["|Model_root"]
    assert not any(call[0] == "delete" for call in cmds.calls)


def test_apply_material_reindex_fast_path_writes_only_swapped_indices_and_morph_json() -> None:
    cmds = FakeCmdsAdapter(
        attrs={
            ("shaderA", ATTR_MMD_MATERIAL_INDEX): 0,
            ("shaderB", ATTR_MMD_MATERIAL_INDEX): 1,
            ("shaderC", ATTR_MMD_MATERIAL_INDEX): 2,
            ("morphNode", ATTR_MMD_MATERIAL_MORPH_OFFSETS): json.dumps(
                [_material_offset(0)], separators=(",", ":")
            ),
        },
        types={
            "|Model_root": "transform",
            "shaderA": "standardSurface",
            "shaderB": "standardSurface",
            "shaderC": "standardSurface",
            "morphNode": "network",
        },
    )
    registry = FakeRegistry(
        members=["shaderA", "shaderB", "shaderC"], morph_members=["morphNode"]
    )
    material_a = replace(_material(), index=0, binding_identity="shaderA", name="A")
    material_b = replace(_material(), index=1, binding_identity="shaderB", name="B")
    material_c = replace(_material(), index=2, binding_identity="shaderC", name="C")
    morph = MmdMorphSpec(
        name="material morph",
        index=0,
        morph_type="material",
        offsets=(_material_offset(0),),
        binding_identity="morphNode",
    )
    old_spec = _authoring_spec((material_a, material_b, material_c), (morph,))
    new_spec = _authoring_spec(
        (replace(material_a, index=1), replace(material_b, index=0), material_c),
        (replace(morph, offsets=(_material_offset(1),)),),
    )
    queue_calls: list[tuple[str, int, int]] = []
    rebuild_calls: list[str] = []
    adapter = MayaMaterialAuthoring(
        cmds,
        registry,
        runtime_rebuilders={"material": lambda root: rebuild_calls.append(root)},
        native_queue_reindexer=lambda root, first, second: queue_calls.append(
            (root, first, second)
        ),
    )

    result = adapter.apply_material_reindex("|Model_root", old_spec, new_spec)

    assert result == new_spec
    assert cmds.attrs[("shaderA", ATTR_MMD_MATERIAL_INDEX)] == 1
    assert cmds.attrs[("shaderB", ATTR_MMD_MATERIAL_INDEX)] == 0
    assert cmds.attrs[("shaderC", ATTR_MMD_MATERIAL_INDEX)] == 2
    assert json.loads(cmds.attrs[("morphNode", ATTR_MMD_MATERIAL_MORPH_OFFSETS)])[0][
        "material_index"
    ] == 1
    assert queue_calls == [("|Model_root", 0, 1)]
    assert rebuild_calls == []
    written_attrs = {
        call[1][0].rsplit(".", 1)[1]
        for call in cmds.calls
        if call[0] == "set_attr"
    }
    assert written_attrs == {ATTR_MMD_MATERIAL_INDEX, ATTR_MMD_MATERIAL_MORPH_OFFSETS}


def test_apply_material_reindex_fast_path_does_not_require_full_specs() -> None:
    cmds = FakeCmdsAdapter(
        attrs={
            ("shaderA", ATTR_MMD_MATERIAL_INDEX): 0,
            ("shaderB", ATTR_MMD_MATERIAL_INDEX): 1,
            ("morphNode", "mmd_morph_type"): "material",
            ("morphNode", ATTR_MMD_MATERIAL_MORPH_OFFSETS): json.dumps(
                [_material_offset(0)], separators=(",", ":")
            ),
        },
        types={
            "|Model_root": "transform",
            "shaderA": "standardSurface",
            "shaderB": "standardSurface",
            "morphNode": "network",
        },
    )
    registry = FakeRegistry(
        members=["shaderA", "shaderB"], morph_members=["morphNode"]
    )
    adapter = _authoring(cmds, registry)

    result = adapter.apply_material_reindex_fast("|Model_root", 0, 1)

    assert result.first_index == 0
    assert result.second_index == 1
    assert cmds.attrs[("shaderA", ATTR_MMD_MATERIAL_INDEX)] == 1
    assert cmds.attrs[("shaderB", ATTR_MMD_MATERIAL_INDEX)] == 0
    assert json.loads(cmds.attrs[("morphNode", ATTR_MMD_MATERIAL_MORPH_OFFSETS)])[0][
        "material_index"
    ] == 1


def test_native_queue_reindex_fails_closed_on_descendant_discovery_error() -> None:
    class FailingDescendantCmds(FakeCmdsAdapter):
        def all_node_types(self) -> list[str]:
            return ["transform", "mmdRenderShape"]

        def list_relatives(self, *_args: Any, **_kwargs: Any) -> list[str]:
            raise RuntimeError("descendant query failed")

        def mmd_render_queue_reindex(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("native updater must not run after discovery failure")

    adapter = _authoring(FailingDescendantCmds(), FakeRegistry())

    with pytest.raises(MayaMaterialAuthoringError, match="failed to discover native render shapes"):
        adapter._update_native_render_queue("|Model_root", 0, 1)


def test_native_queue_reindex_fails_closed_on_non_sequence_and_accepts_empty() -> None:
    class InvalidDescendantCmds(FakeCmdsAdapter):
        def all_node_types(self) -> list[str]:
            return ["transform", "mmdRenderShape"]

        def list_relatives(self, *_args: Any, **_kwargs: Any) -> object:
            return object()

        def mmd_render_queue_reindex(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("native updater must not run for an invalid listing")

    with pytest.raises(MayaMaterialAuthoringError, match="must be a sequence"):
        _authoring(InvalidDescendantCmds(), FakeRegistry())._update_native_render_queue(
            "|Model_root", 0, 1
        )

    class EmptyDescendantCmds(FakeCmdsAdapter):
        def all_node_types(self) -> list[str]:
            return ["transform", "mmdRenderShape"]

        def list_relatives(self, *_args: Any, **_kwargs: Any) -> list[str]:
            return []

        def mmd_render_queue_reindex(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("native updater must not run for an empty listing")

    _authoring(EmptyDescendantCmds(), FakeRegistry())._update_native_render_queue(
        "|Model_root", 0, 1
    )


def test_native_queue_reindex_skips_discovery_when_type_is_unregistered() -> None:
    class UnregisteredNativeCmds(FakeCmdsAdapter):
        def all_node_types(self) -> list[str]:
            return ["transform", "mesh"]

        def list_relatives(self, *_args: Any, **_kwargs: Any) -> list[str]:
            raise AssertionError("descendant discovery is invalid for an unregistered type")

        def mmd_render_queue_reindex(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("native updater must not run for an unregistered type")

    _authoring(UnregisteredNativeCmds(), FakeRegistry())._update_native_render_queue(
        "|Model_root", 0, 1
    )


def test_apply_material_spec_change_rejects_deleted_material_reference_before_writes() -> None:
    cmds = FakeCmdsAdapter(
        attrs={
            ("shaderA", ATTR_MMD_MATERIAL_INDEX): 0,
            ("shaderB", ATTR_MMD_MATERIAL_INDEX): 1,
        },
        types={
            "|Model_root": "transform",
            "shaderA": "standardSurface",
            "shaderB": "standardSurface",
            "morphNode": "network",
        },
    )
    registry = FakeRegistry(members=["shaderA", "shaderB"], morph_members=["morphNode"])
    material_a = replace(_material(), index=0, binding_identity="shaderA", name="A")
    material_b = replace(_material(), index=1, binding_identity="shaderB", name="B")
    morph = MmdMorphSpec(
        name="material morph",
        index=0,
        morph_type="material",
        offsets=(_material_offset(0),),
        binding_identity="morphNode",
    )
    old_spec = _authoring_spec((material_a, material_b), (morph,))
    new_spec = _authoring_spec(
        (replace(material_b, index=0),),
        (replace(morph, offsets=(_material_offset(-1),)),),
    )

    with pytest.raises(MayaMaterialAuthoringError, match="deleted material"):
        _authoring(cmds, registry).apply_material_spec_change(
            "|Model_root", old_spec, new_spec, "replacementSG"
        )

    assert not any(
        call[0] in {"set_attr", "delete", "disconnect_attr"}
        or (call[0] == "sets" and call[2].get("forceElement"))
        for call in cmds.calls
    )


def test_apply_material_spec_change_delete_preserves_mesh_assignment_and_registry_ownership() -> None:
    old_sg = "oldSG"
    replacement_sg = "replacementSG"
    mesh = "|Model_root|Geometry|mesh.f[0]"
    cmds = FakeCmdsAdapter(
        attrs={
            ("shaderA", ATTR_MMD_MATERIAL_INDEX): 0,
            ("shaderB", ATTR_MMD_MATERIAL_INDEX): 1,
        },
        types={
            "|Model_root": "transform",
            "shaderA": "standardSurface",
            "shaderB": "standardSurface",
            old_sg: "shadingEngine",
            replacement_sg: "shadingEngine",
        },
        connections={"shaderA": [old_sg]},
        members={old_sg: [mesh]},
    )
    registry = FakeRegistry(members=["shaderA", "shaderB"])
    material_a = replace(_material(), index=0, binding_identity="shaderA", name="A")
    material_b = replace(_material(), index=1, binding_identity="shaderB", name="B")
    old_spec = _authoring_spec((material_a, material_b))
    new_spec = _authoring_spec((replace(material_b, index=0),))

    _authoring(cmds, registry).apply_material_spec_change(
        "|Model_root", old_spec, new_spec, replacement_sg
    )

    assert cmds.attrs[("shaderB", ATTR_MMD_MATERIAL_INDEX)] == 0
    assert cmds.members[replacement_sg] == [mesh]
    assert registry.members == ["shaderB"]
    assert old_sg not in cmds.types
    assert "shaderA" not in cmds.types


def test_create_failure_propagates_without_claiming_success() -> None:
    class FailingCmds(FakeCmdsAdapter):
        def set_attr(self, path: str, *values: Any, **kwargs: Any) -> None:
            raise RuntimeError("set failed")

    with pytest.raises(MayaMaterialAuthoringError, match="failed to create material"):
        _authoring(FailingCmds(), FakeRegistry()).create_material("|Model_root", _material())
