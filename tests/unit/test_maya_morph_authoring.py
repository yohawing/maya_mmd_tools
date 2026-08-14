"""Unit tests for transactional Maya morph structural authoring."""

from dataclasses import dataclass, field, replace
import json
from typing import Any

import pytest

from tests.common.maya_stub import install_maya_stub

install_maya_stub()

from mmd_tools.adapters.maya_morph_authoring import (  # noqa: E402
    MayaMorphAuthoringError,
    apply_morph_spec_change,
    apply_morph_value_patch,
)
from mmd_tools.core.model_authoring_spec import (  # noqa: E402
    MmdModelAuthoringSpec,
    MmdModelSpec,
    MmdMorphSpec,
)
from mmd_tools.core.constants import (  # noqa: E402
    ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON,
    ATTR_MMD_IMPORT_SCALE,
    ATTR_MMD_SOURCE_VERTEX_INDICES,
)


@dataclass
class FakeAdapter:
    """Small explicit controller/network command surface."""

    types: dict[str, str] = field(
        default_factory=lambda: {"|Model": "transform", "controller": "mmdMorphController"}
    )
    attrs: dict[tuple[str, str], Any] = field(default_factory=dict)
    connections: dict[str, list[str]] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.attrs[("|Model", "mmd_morph_controller")] = None
        self.connections["|Model.mmd_morph_controller"] = ["controller"]

    def object_exists(self, node: str) -> bool:
        return node in self.types

    def ls(self, node: Any, long: bool = False, **kwargs: Any) -> list[str]:
        if isinstance(node, (list, tuple)):
            return [
                item
                for item in node
                if item in self.types and (not kwargs.get("type") or self.types[item] == kwargs["type"])
            ]
        if node in {"Model", "|Model"}:
            return ["|Model"]
        return [node] if node in self.types else []

    def node_type(self, node: str) -> str:
        return self.types[node]

    def all_node_types(self) -> list[str]:
        return ["network", "mmdMorphController"]

    def attribute_exists(self, attr: str, node: str) -> bool:
        return (node, attr) in self.attrs

    def get_attr(self, plug: str, **kwargs: Any) -> Any:
        node, attr = plug.split(".", 1)
        if kwargs.get("multiIndices"):
            return self.attrs.get((node, attr), [])
        return self.attrs.get((node, attr), 0.0)

    def set_attr(self, plug: str, *values: Any, **kwargs: Any) -> None:
        self.calls.append(("set_attr", (plug, *values), kwargs))
        node, attr = plug.split(".", 1)
        if values:
            self.attrs[(node, attr)] = values[0]

    def add_attr(self, node: str, **kwargs: Any) -> None:
        self.calls.append(("add_attr", (node,), kwargs))
        attr = kwargs["longName"]
        self.attrs[(node, attr)] = kwargs.get("defaultValue")

    def create_node(self, node_type: str, name: str) -> str:
        if any(ord(char) > 127 for char in name):
            raise AssertionError("node identity must be ASCII")
        candidate = name
        suffix = 1
        while candidate in self.types:
            candidate = f"{name}{suffix}"
            suffix += 1
        self.types[candidate] = node_type
        self.calls.append(("create_node", (node_type,), {"name": name}))
        return candidate

    def list_connections(self, plug: str, **kwargs: Any) -> list[str]:
        return list(self.connections.get(plug, []))

    def connect_attr(self, source: str, destination: str, **kwargs: Any) -> None:
        self.calls.append(("connect_attr", (source, destination), kwargs))
        self.connections.setdefault(source, [])
        if destination not in self.connections[source]:
            self.connections[source].append(destination)
        if source.endswith(".message"):
            self.connections.setdefault(destination, []).append(source.split(".", 1)[0])

    def disconnect_attr(self, source: str, destination: str) -> None:
        self.calls.append(("disconnect_attr", (source, destination), {}))
        if destination in self.connections.get(source, []):
            self.connections[source].remove(destination)
        if source in self.connections.get(destination, []):
            self.connections[destination].remove(source)

    def alias_attr(self, *args: str, **kwargs: Any) -> Any:
        if kwargs.get("query"):
            if args and "." in args[0]:
                return self.aliases.get(args[0])
            result: list[str] = []
            for plug, alias in self.aliases.items():
                if args and not plug.startswith(f"{args[0]}."):
                    continue
                result.extend((alias, plug.split(".", 1)[1]))
            return result
        if kwargs.get("remove"):
            self.aliases.pop(args[0], None)
            return None
        alias, plug = args
        self.aliases[plug] = alias
        return None

    def list_attr(self, node: str, **kwargs: Any) -> list[str]:
        names = ["message", "inputWeight", "outputWeight", "topologyVersion", "groupTopology"]
        if kwargs.get("shortNames"):
            names = ["msg", "iw", "ow", "tv", "gt"]
        names.extend(
            alias for plug, alias in self.aliases.items() if plug.startswith(f"{node}.")
        )
        return names

    def delete(self, node: str) -> None:
        self.calls.append(("delete", (node,), {}))
        self.types.pop(node, None)

    def blend_shape(self, node: str, **kwargs: Any) -> list[Any]:
        if kwargs.get("geometryIndices"):
            return self.attrs.get((node, "geometryIndices"), [])
        if kwargs.get("geometry"):
            return self.attrs.get((node, "geometry"), [])
        name = kwargs.get("name")
        if name:
            self.types[name] = "blendShape"
            self.attrs[(name, "geometry")] = [node]
            self.attrs[(name, "geometryIndices")] = [0]
            self.attrs[(name, "weight")] = []
            self.calls.append(("blend_shape", (node,), kwargs))
            return [name]
        if kwargs.get("edit") and kwargs.get("target"):
            base, target_index, target, target_weight = kwargs["target"]
            assert base == target
            assert target_weight == 1.0
            item = (
                f"{node}.inputTarget[0].inputTargetGroup[{target_index}]"
                ".inputTargetItem[6000]"
            )
            self.connections[f"{item}.inputGeomTarget"] = [f"{base}Shape.worldMesh[0]"]
            self.aliases[f"{node}.weight[{target_index}]"] = str(base).rsplit("|", 1)[-1]
            self.calls.append(("blend_shape", (node,), kwargs))
            return [node]
        raise AssertionError(kwargs)

    def list_relatives(self, node: str, **kwargs: Any) -> list[str]:
        if kwargs.get("allDescendents") and kwargs.get("type") == "mesh":
            return [item for item, kind in self.types.items() if kind == "mesh" and item.startswith(f"{node}|")]
        if kwargs.get("parent") and node == "|Model|faceShape":
            return ["|Model|face"]
        return []

    def list_history(self, node: str) -> list[str]:
        return list(self.attrs.get((node, "history"), []))

    def poly_evaluate(self, shape: str, vertex: bool = True) -> int:
        assert vertex
        return int(self.attrs[(shape, "vertexCount")])

    def remove_multi_instance(self, plug: str, **kwargs: Any) -> None:
        self.calls.append(("remove_multi_instance", (plug,), kwargs))
        self.aliases.pop(plug, None)


@dataclass
class FakeRegistry:
    members: list[str]
    calls: list[tuple[str, list[str]]] = field(default_factory=list)

    def ensure_model_registry(self, root: str) -> str:
        return "registry"

    def list_model_registry_members(self, root: str, category: str) -> list[str]:
        assert category == "morph"
        return list(self.members)

    def register_model_members(self, registry: str, category: str, members: list[str]) -> None:
        self.calls.append(("register", list(members)))
        self.members.extend(members)

    def unregister_model_members(self, registry: str, category: str, members: list[str]) -> None:
        self.calls.append(("unregister", list(members)))
        self.members[:] = [member for member in self.members if member not in members]


def _morph(name: str, index: int, kind: str, node: str, offsets: tuple[dict[str, Any], ...] = ()) -> MmdMorphSpec:
    return MmdMorphSpec(
        name=name,
        name_english=name,
        index=index,
        panel=4,
        morph_type=kind,
        offsets=offsets,
        binding_identity=node,
    )


def _spec(*morphs: MmdMorphSpec) -> MmdModelAuthoringSpec:
    return MmdModelAuthoringSpec(model=MmdModelSpec(name="モデル"), morphs=morphs)


def _install_morph(adapter: FakeAdapter, morph: MmdMorphSpec) -> None:
    node = str(morph.binding_identity)
    adapter.types[node] = "network"
    adapter.attrs[(node, "mmd_morph_index")] = morph.index
    adapter.attrs[(node, "mmd_morph_type")] = morph.morph_type
    adapter.attrs[(node, "weight")] = 0.0
    adapter.attrs[("controller", f"inputWeight[{morph.index}]")] = 0.25 + morph.index
    adapter.connections[f"controller.outputWeight[{morph.index}]"] = (
        [] if morph.morph_type == "vertex" else [f"{node}.weight"]
    )
    adapter.aliases[f"controller.inputWeight[{morph.index}]"] = f"old_{morph.index}"


def _install_vertex_target(
    adapter: FakeAdapter,
    morph: MmdMorphSpec,
    *,
    blend_shape: str = "faceBS",
    geometry: str = "|Model|faceShape",
    target_index: int = 3,
    source_indices: tuple[int, ...] = (4, 7),
) -> None:
    adapter.types[blend_shape] = "blendShape"
    adapter.types[geometry] = "mesh"
    adapter.connections.setdefault(f"controller.outputWeight[{morph.index}]", []).append(
        f"{blend_shape}.oldVertexAlias"
    )
    adapter.aliases[f"{blend_shape}.weight[{target_index}]"] = "oldVertexAlias"
    adapter.attrs[(blend_shape, ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON)] = json.dumps(
        {str(target_index): {"name": morph.name, "index": morph.index}},
        ensure_ascii=False,
    )
    adapter.attrs[(blend_shape, "geometry")] = [geometry]
    adapter.attrs[(blend_shape, "geometryIndices")] = [2]
    adapter.attrs[(blend_shape, "weight")] = [target_index]
    adapter.attrs[(blend_shape, f"weight[{target_index}]")] = 0.75
    adapter.attrs[(geometry, "vertexCount")] = len(source_indices)
    adapter.attrs[(geometry, ATTR_MMD_SOURCE_VERTEX_INDICES)] = list(source_indices)
    adapter.attrs[(geometry, "history")] = [blend_shape]
    group = f"inputTarget[2].inputTargetGroup[{target_index}].inputTargetItem"
    adapter.attrs[(blend_shape, group)] = [5500, 6000]


def test_create_unicode_uv_morph_uses_ascii_node_and_binds_controller() -> None:
    adapter = FakeAdapter()
    registry = FakeRegistry([])
    old = _spec()
    new_morph = _morph(
        "UV_日本語",
        0,
        "uv",
        "placeholder",
        ({"vertex_index": 2, "uv_offset": (0.1, 0.2, 0.3, 0.4)},),
    )
    new = _spec(replace(new_morph, binding_identity=None))

    bound = apply_morph_spec_change("|Model", old, new, adapter, registry)

    created = bound.morphs[0].binding_identity
    assert created == "mmdMorph_0"
    assert adapter.attrs[(created, "mmd_morph_name")] == "UV_日本語"
    assert adapter.attrs[(created, "mmd_morph_index")] == 0
    assert adapter.connections["controller.outputWeight[0]"] == [f"{created}.weight"]
    controller_alias = adapter.aliases["controller.inputWeight[0]"]
    assert controller_alias.isascii() and controller_alias.isidentifier()
    assert controller_alias != "morph_0"
    assert registry.members == [created]
    assert adapter.attrs[("controller", "groupTopology")] == "{}"


def test_duplicate_new_morph_names_receive_unique_controller_aliases() -> None:
    adapter = FakeAdapter()
    registry = FakeRegistry([])
    new = _spec(
        replace(_morph("New Morph", 0, "bone", "unused0"), binding_identity=None),
        replace(_morph("New Morph", 1, "material", "unused1"), binding_identity=None),
    )

    apply_morph_spec_change("|Model", _spec(), new, adapter, registry)

    assert adapter.aliases["controller.inputWeight[0]"] == "New_Morph"
    assert adapter.aliases["controller.inputWeight[1]"] == "New_Morph_1"


def test_reindex_rewrites_raw_runtime_attrs_connections_and_group_topology() -> None:
    adapter = FakeAdapter()
    bone = _morph("Bone", 0, "bone", "boneNode", ({"bone_index": 0, "translation": (0, 0, 0), "rotation": (0, 0, 0, 1)},))
    material = _morph("Material", 1, "material", "materialNode", ())
    group = _morph("Group", 2, "group", "groupNode", ({"morph_index": 0, "morph_rate": 0.5},))
    for morph in (bone, material, group):
        _install_morph(adapter, morph)
    registry = FakeRegistry(["boneNode", "materialNode", "groupNode"])
    old = _spec(bone, material, group)
    new = _spec(
        replace(material, index=0),
        replace(bone, index=1),
        replace(group, offsets=({"morph_index": 1, "morph_rate": 0.5},)),
    )

    rebuilt: list[tuple[str, str]] = []
    bound = apply_morph_spec_change(
        "|Model",
        old,
        new,
        adapter,
        registry,
        runtime_rebuilders={
            "bone": lambda root: rebuilt.append(("bone", root)) or {"success": True},
            "material": lambda root: rebuilt.append(("material", root)) or {"success": True},
        },
    )

    assert [morph.binding_identity for morph in bound.morphs] == ["materialNode", "boneNode", "groupNode"]
    assert adapter.attrs[("materialNode", "mmd_morph_index")] == 0
    assert adapter.attrs[("boneNode", "mmd_morph_index")] == 1
    assert adapter.connections["controller.outputWeight[0]"] == ["materialNode.weight"]
    assert adapter.connections["controller.outputWeight[1]"] == ["boneNode.weight"]
    assert adapter.attrs[("controller", "groupTopology")] == '{"1":[[2,0.5]]}'
    assert adapter.attrs[("groupNode", "mmd_group_morph_offsets_json")] == '[{"morph_index":1,"morph_rate":0.5}]'
    assert rebuilt == [("bone", "|Model")]


def test_delete_non_vertex_unregisters_after_controller_disconnect() -> None:
    adapter = FakeAdapter()
    morph = _morph("Bone", 0, "bone", "boneNode")
    _install_morph(adapter, morph)
    registry = FakeRegistry(["boneNode"])

    apply_morph_spec_change("|Model", _spec(morph), _spec(), adapter, registry)

    assert "boneNode" not in adapter.types
    assert registry.members == []
    disconnect_index = next(i for i, call in enumerate(adapter.calls) if call[0] == "disconnect_attr")
    delete_index = next(i for i, call in enumerate(adapter.calls) if call[0] == "delete")
    assert disconnect_index < delete_index


def test_vertex_name_and_panel_update_alias_mapping_and_network_metadata() -> None:
    adapter = FakeAdapter()
    old = _morph(
        "Smile",
        0,
        "vertex",
        "vertexNode",
        ({"vertex_index": 4, "position_offset": (0.1, 0.0, 0.0)},),
    )
    _install_morph(adapter, old)
    _install_vertex_target(adapter, old)
    new = replace(old, name="Smile Wide", panel=2)

    apply_morph_spec_change(
        "|Model",
        _spec(old),
        _spec(new),
        adapter,
        FakeRegistry(["vertexNode"]),
    )

    assert adapter.attrs[("vertexNode", "mmd_morph_name")] == "Smile Wide"
    assert adapter.attrs[("vertexNode", "mmd_morph_panel")] == 2
    assert adapter.aliases["faceBS.weight[3]"] == "Smile_Wide"
    mapping = json.loads(adapter.attrs[("faceBS", ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON)])
    assert mapping == {"3": {"name": "Smile Wide", "index": 0}}


def test_selected_vertex_patch_resolves_old_alias_before_rename() -> None:
    adapter = FakeAdapter()
    old = _morph("Smile", 0, "vertex", "vertexNode")
    _install_morph(adapter, old)
    _install_vertex_target(adapter, old)
    adapter.attrs[("|Model", ATTR_MMD_IMPORT_SCALE)] = 1.0
    new = replace(old, name="Smile Wide")

    result = apply_morph_value_patch(
        "|Model",
        old,
        new,
        adapter,
        FakeRegistry(["vertexNode"]),
    )

    assert result == new
    assert adapter.aliases["faceBS.weight[3]"] == "Smile_Wide"
    mapping = json.loads(adapter.attrs[("faceBS", ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON)])
    assert mapping == {"3": {"name": "Smile Wide", "index": 0}}


def test_selected_vertex_patch_rejects_stale_raw_mapping_before_write() -> None:
    adapter = FakeAdapter()
    old = _morph("Smile", 0, "vertex", "vertexNode")
    _install_morph(adapter, old)
    _install_vertex_target(adapter, old)
    adapter.attrs[("faceBS", ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON)] = json.dumps(
        {"3": {"name": "Different", "index": 0}}
    )
    calls_before = list(adapter.calls)

    with pytest.raises(MayaMorphAuthoringError, match="stale_raw_name_mapping"):
        apply_morph_value_patch(
            "|Model",
            old,
            replace(old, name="Smile Wide"),
            adapter,
            FakeRegistry(["vertexNode"]),
        )

    assert adapter.calls == calls_before


def test_selected_vertex_patch_rejects_duplicate_same_blendshape_before_write() -> None:
    adapter = FakeAdapter()
    old = _morph("Smile", 0, "vertex", "vertexNode")
    _install_morph(adapter, old)
    _install_vertex_target(adapter, old)
    adapter.connections["controller.outputWeight[0]"].append("faceBS.weight[3]")
    calls_before = list(adapter.calls)

    with pytest.raises(MayaMorphAuthoringError, match="duplicate_blendshape_candidate"):
        apply_morph_value_patch(
            "|Model",
            old,
            replace(old, name="Smile Wide"),
            adapter,
            FakeRegistry(["vertexNode"]),
        )

    assert adapter.calls == calls_before


def test_vertex_offsets_rewrite_split_mesh_full_weight_sparse_targets() -> None:
    adapter = FakeAdapter()
    old = _morph(
        "Move",
        0,
        "vertex",
        "vertexNode",
        ({"vertex_index": 4, "position_offset": (0.0, 0.0, 0.0)},),
    )
    _install_morph(adapter, old)
    _install_vertex_target(adapter, old, source_indices=(4, 7))
    _install_vertex_target(
        adapter,
        old,
        blend_shape="bodyBS",
        geometry="|Model|bodyShape",
        target_index=8,
        source_indices=(9, 12),
    )
    new = replace(
        old,
        offsets=(
            {"vertex_index": 4, "position_offset": (1.0, 2.0, 3.0)},
            {"vertex_index": 9, "position_offset": (-1.0, 0.5, -2.0)},
        ),
    )

    apply_morph_spec_change(
        "|Model",
        _spec(old),
        _spec(new),
        adapter,
        FakeRegistry(["vertexNode"]),
        model_scale_resolver=lambda _root: 2.0,
    )

    component_calls = [call for call in adapter.calls if call[0] == "set_attr" and call[2].get("type") == "componentList"]
    point_calls = [call for call in adapter.calls if call[0] == "set_attr" and call[2].get("type") == "pointArray"]
    assert [call[1][0] for call in component_calls] == [
        "bodyBS.inputTarget[2].inputTargetGroup[8].inputTargetItem[6000].inputComponentsTarget",
        "faceBS.inputTarget[2].inputTargetGroup[3].inputTargetItem[6000].inputComponentsTarget",
    ]
    assert component_calls[0][1][1:] == (1, "vtx[0]")
    assert point_calls[0][1][1:] == (1, (-2.0, 1.0, 4.0, 1.0))
    assert point_calls[1][1][1:] == (1, (2.0, 4.0, -6.0, 1.0))


def test_vertex_offset_preflight_rejects_missing_full_weight_item_without_write() -> None:
    adapter = FakeAdapter()
    old = _morph("Move", 0, "vertex", "vertexNode")
    _install_morph(adapter, old)
    _install_vertex_target(adapter, old)
    adapter.attrs[("faceBS", "inputTarget[2].inputTargetGroup[3].inputTargetItem")] = [5500]
    new = replace(old, offsets=({"vertex_index": 4, "position_offset": (1.0, 0.0, 0.0)},))
    calls_before = list(adapter.calls)

    with pytest.raises(MayaMorphAuthoringError, match=r"inputTargetItem\[6000\]"):
        apply_morph_spec_change(
            "|Model",
            _spec(old),
            _spec(new),
            adapter,
            FakeRegistry(["vertexNode"]),
            model_scale_resolver=lambda _root: 1.0,
        )

    assert adapter.calls == calls_before


def test_empty_vertex_create_builds_exportable_target_and_controller_binding() -> None:
    adapter = FakeAdapter()
    adapter.types["|Model|face"] = "transform"
    adapter.types["|Model|faceShape"] = "mesh"
    adapter.types["|Model|faceShapeOrig"] = "mesh"
    adapter.attrs[("|Model|faceShape", "vertexCount")] = 2
    adapter.attrs[("|Model|faceShape", ATTR_MMD_SOURCE_VERTEX_INDICES)] = [4, 7]
    adapter.attrs[("|Model|faceShape", "history")] = []
    adapter.attrs[("|Model|faceShapeOrig", "intermediateObject")] = True
    new = MmdMorphSpec(name="Empty", morph_type="vertex")

    bound = apply_morph_spec_change("|Model", _spec(), _spec(new), adapter, FakeRegistry([]))

    assert bound.morphs[0].binding_identity == "mmdMorph_0"
    assert adapter.types["mmdVertexMorph_0_blendShape"] == "blendShape"
    assert adapter.aliases["mmdVertexMorph_0_blendShape.weight[0]"] == "Empty"
    mapping = json.loads(
        adapter.attrs[("mmdVertexMorph_0_blendShape", ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON)]
    )
    assert mapping == {"0": {"name": "Empty", "index": 0}}
    assert adapter.connections["controller.outputWeight[0]"] == [
        "mmdVertexMorph_0_blendShape.weight[0]"
    ]
    target_item = (
        "mmdVertexMorph_0_blendShape.inputTarget[0].inputTargetGroup[0]"
        ".inputTargetItem[6000]"
    )
    typed_array_calls = [
        call
        for call in adapter.calls
        if call[0] == "set_attr" and call[1][0].startswith(target_item)
    ]
    assert typed_array_calls == []
    assert (
        "blend_shape",
        ("mmdVertexMorph_0_blendShape",),
        {"edit": True, "target": ("|Model|face", 0, "|Model|face", 1.0)},
    ) in adapter.calls
    assert (
        "disconnect_attr",
        (
            "|Model|faceShape.worldMesh[0]",
            f"{target_item}.inputGeomTarget",
        ),
        {},
    ) in adapter.calls


def test_empty_vertex_create_without_owned_mesh_rejects_before_write() -> None:
    adapter = FakeAdapter()
    new = MmdMorphSpec(name="Empty", morph_type="vertex")

    with pytest.raises(MayaMorphAuthoringError, match="target creation"):
        apply_morph_spec_change("|Model", _spec(), _spec(new), adapter, FakeRegistry([]))

    assert not any(call[0] == "create_node" for call in adapter.calls)


def test_vertex_delete_logically_unmaps_and_zeroes_retained_target() -> None:
    adapter = FakeAdapter()
    old = _morph("Delete", 0, "vertex", "vertexNode")
    _install_morph(adapter, old)
    _install_vertex_target(adapter, old)

    apply_morph_spec_change(
        "|Model",
        _spec(old),
        _spec(),
        adapter,
        FakeRegistry(["vertexNode"]),
    )

    assert "faceBS.weight[3]" not in adapter.aliases
    assert json.loads(adapter.attrs[("faceBS", ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON)]) == {}
    assert adapter.attrs[("faceBS", "weight[3]")] == 0.0
    assert (
        "disconnect_attr",
        ("controller.outputWeight[0]", "faceBS.oldVertexAlias"),
        {},
    ) in adapter.calls
    removed = [call[1][0] for call in adapter.calls if call[0] == "remove_multi_instance"]
    assert "faceBS.inputTarget[2].inputTargetGroup[3]" not in removed
    assert "faceBS.weight[3]" not in removed


def test_vertex_reindex_keeps_physical_slot_and_updates_global_mapping() -> None:
    adapter = FakeAdapter()
    vertex = _morph("Vertex", 0, "vertex", "vertexNode")
    bone = _morph("Bone", 1, "bone", "boneNode")
    _install_morph(adapter, vertex)
    _install_morph(adapter, bone)
    _install_vertex_target(adapter, vertex)

    apply_morph_spec_change(
        "|Model",
        _spec(vertex, bone),
        _spec(replace(bone, index=0), replace(vertex, index=1)),
        adapter,
        FakeRegistry(["vertexNode", "boneNode"]),
    )

    mapping = json.loads(adapter.attrs[("faceBS", ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON)])
    assert mapping == {"3": {"name": "Vertex", "index": 1}}
    assert adapter.aliases["faceBS.weight[3]"] == "oldVertexAlias"
    assert "faceBS.oldVertexAlias" in adapter.connections["controller.outputWeight[1]"]


@pytest.mark.parametrize(
    ("old_morph", "new_morph", "message"),
    [
        (
            _morph("Vertex", 0, "vertex", "vertexNode", ({"vertex_index": 0, "position_offset": (0, 0, 0)},)),
            _morph("Vertex", 0, "vertex", "vertexNode", ({"vertex_index": 0, "position_offset": (1, 0, 0)},)),
            "target binding",
        ),
        (
            None,
            MmdMorphSpec(name="Flip", morph_type="flip", runtime_capability="unsupported", loss_policy="reject"),
            "policy-rejected",
        ),
    ],
)
def test_fail_closed_edits_validate_before_any_write(old_morph: Any, new_morph: MmdMorphSpec, message: str) -> None:
    adapter = FakeAdapter()
    registry = FakeRegistry([] if old_morph is None else [old_morph.binding_identity])
    if old_morph is not None:
        _install_morph(adapter, old_morph)
    calls_before = list(adapter.calls)

    with pytest.raises(MayaMorphAuthoringError, match=message):
        apply_morph_spec_change(
            "|Model",
            _spec(*(() if old_morph is None else (old_morph,))),
            _spec(new_morph),
            adapter,
            registry,
        )

    assert adapter.calls == calls_before


def test_registry_mismatch_fails_before_scene_write() -> None:
    adapter = FakeAdapter()
    morph = _morph("Bone", 0, "bone", "boneNode")
    _install_morph(adapter, morph)

    with pytest.raises(MayaMorphAuthoringError, match="not registry-owned"):
        apply_morph_spec_change("|Model", _spec(morph), _spec(morph), adapter, FakeRegistry([]))

    assert not any(call[0] in {"set_attr", "create_node", "delete"} for call in adapter.calls)


def test_material_offsets_require_explicit_live_preview_rebuilder_before_write() -> None:
    adapter = FakeAdapter()
    morph = _morph("Material", 0, "material", "materialNode", ())
    _install_morph(adapter, morph)
    registry = FakeRegistry(["materialNode"])
    updated = replace(
        morph,
        offsets=(
            {
                "material_index": -1,
                "operation_type": 1,
                "diffuse": (0, 0, 0, 0),
                "specular": (0, 0, 0),
                "specular_coefficient": 0,
                "ambient": (0, 0, 0),
                "edge_color": (0, 0, 0, 0),
                "edge_size": 0,
                "texture_factor": (0, 0, 0, 0),
                "sphere_texture_factor": (0, 0, 0, 0),
                "toon_texture_factor": (0, 0, 0, 0),
            },
        ),
    )

    with pytest.raises(MayaMorphAuthoringError, match="runtime rebuilders"):
        apply_morph_spec_change("|Model", _spec(morph), _spec(updated), adapter, registry)

    assert not any(call[0] == "set_attr" for call in adapter.calls)


def test_first_morph_creates_controller_when_template_has_none() -> None:
    adapter = FakeAdapter()
    adapter.attrs.pop(("|Model", "mmd_morph_controller"))
    adapter.connections.pop("|Model.mmd_morph_controller")
    adapter.types.pop("controller")
    registry = FakeRegistry([])
    created = MmdMorphSpec(name="Group", morph_type="group")

    bound = apply_morph_spec_change("|Model", _spec(), _spec(created), adapter, registry)

    assert bound.morphs[0].binding_identity == "mmdMorph_0"
    assert adapter.types["mmdMorphController"] == "mmdMorphController"
    assert adapter.connections["mmdMorphController.message"] == ["|Model.mmd_morph_controller"]
