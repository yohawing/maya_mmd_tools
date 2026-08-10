"""Focused adjacent Morph reindex fast-path contracts."""

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from tests.common.maya_stub import install_headless_ui_stubs, install_maya_stub

install_maya_stub()
install_headless_ui_stubs()

from mmd_tools.adapters.maya_model_authoring_coordinator import (  # noqa: E402
    MayaModelAuthoringCoordinator,
    MayaModelAuthoringCoordinatorError,
)
from mmd_tools.adapters.maya_morph_authoring import apply_morph_reindex  # noqa: E402
from mmd_tools.adapters.maya_scene_metadata_backend import (  # noqa: E402
    MayaSceneMetadataBackend,
    MayaSceneMetadataError,
)
from mmd_tools.core.model_authoring_spec import (  # noqa: E402
    MmdBoneSpec,
    MmdMaterialSpec,
    MmdModelAuthoringSpec,
    MmdModelSpec,
    MmdMorphSpec,
)
from mmd_tools.core.morph_authoring import (  # noqa: E402
    MorphReindexResult,
    swap_adjacent_morphs,
)
from mmd_tools.ui.presenters.morph_presenter import MorphPresenter  # noqa: E402


def _morph(index: int, *, morph_type: str = "vertex", binding: str | None = None, offsets=()):
    return MmdMorphSpec(
        name=f"m{index}",
        name_english=f"m{index}",
        index=index,
        panel=4,
        morph_type=morph_type,
        offsets=offsets,
        binding_identity=binding or f"|Root|m{index}",
    )


def _spec(*morphs: MmdMorphSpec) -> MmdModelAuthoringSpec:
    return MmdModelAuthoringSpec(
        model=MmdModelSpec("jp", "en", "comment", "comment-en"),
        bones=(MmdBoneSpec("bone", index=0),),
        materials=(MmdMaterialSpec("material", index=0),),
        morphs=morphs,
    )


def test_pure_adjacent_swap_remaps_group_reference_and_rejects_non_adjacent() -> None:
    original = _spec(
        _morph(0, morph_type="group", offsets=({"morph_index": 1, "morph_rate": 0.5},)),
        _morph(1),
        _morph(2),
    )
    swapped = swap_adjacent_morphs(original, 0, 1)
    assert [item.index for item in swapped.morphs] == [0, 1, 2]
    assert swapped.morphs[1].name == "m0"
    assert swapped.morphs[1].offsets[0]["morph_index"] == 0
    with pytest.raises(ValueError, match="adjacent"):
        swap_adjacent_morphs(original, 0, 2)


@dataclass
class _ReindexAdapter:
    types: dict[str, str] = field(
        default_factory=lambda: {
            "|Root": "transform",
            "|Root|m0": "network",
            "|Root|m1": "network",
            "|Root|controller": "mmdMorphController",
        }
    )
    attrs: dict[tuple[str, str], object] = field(default_factory=dict)
    connections: dict[str, list[str]] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        self.attrs[("|Root", "mmd_morph_controller")] = None
        for index, name in ((0, "m0"), (1, "m1")):
            node = f"|Root|{name}"
            self.attrs[(node, "mmd_morph_index")] = index
            self.attrs[(node, "mmd_morph_type")] = "flip"
            self.attrs[(node, "mmd_flip_morph_offsets_json")] = "[]"
            self.connections.setdefault(f"|Root|controller.outputWeight[{index}]", []).append(
                f"{node}.weight"
            )
            self.connections[f"|Root|controller.inputWeight[{index}]"] = [f"src{index}.out"]
            self.attrs[("|Root|controller", f"inputWeight[{index}]")] = float(index) / 10.0
            self.attrs[(f"src{index}", "out")] = float(index) / 10.0
            self.aliases[f"|Root|controller.inputWeight[{index}]"] = f"weight{index}"
        self.connections["|Root.mmd_morph_controller"] = ["|Root|controller"]
        self.attrs[("|Root|controller", "groupTopology")] = '{"1":[[0,0.5]]}'
        self.attrs[("|Root", "mmd_display_frames_json")] = (
            '[{"name":"f","elements":[{"type":1,"index":0}]}]'
        )

    def object_exists(self, node):
        return node in self.types

    def ls(self, node=None, long=False, **kwargs):
        if kwargs.get("type"):
            return [name for name, kind in self.types.items() if kind == kwargs["type"]]
        if isinstance(node, (list, tuple)):
            return [item for item in node if item in self.types]
        return [node] if node in self.types else []

    def node_type(self, node):
        return self.types[node]

    def all_node_types(self):
        return ["network", "mmdMorphController"]

    def attribute_exists(self, attr, node):
        return (node, attr) in self.attrs

    def get_attr(self, plug, **kwargs):
        node, attr = plug.split(".", 1)
        return self.attrs.get((node, attr), [] if kwargs.get("multiIndices") else 0.0)

    def set_attr(self, plug, *values, **kwargs):
        if not values and "lock" in kwargs:
            return
        if not values:
            return
        node, attr = plug.split(".", 1)
        self.attrs[(node, attr)] = values[0] if len(values) == 1 else list(values)

    def list_connections(self, plug, **kwargs):
        return list(self.connections.get(plug, []))

    def connect_attr(self, source, destination, **kwargs):
        self.connections.setdefault(source, [])
        if destination not in self.connections[source]:
            self.connections[source].append(destination)
        if source.endswith(".message"):
            self.connections.setdefault(destination, []).append(source.split(".", 1)[0])
        if ".inputWeight[" in destination:
            self.connections[destination] = [source]
            source_node, source_attr = source.split(".", 1)
            self.attrs[(destination.split(".", 1)[0], destination.split(".", 1)[1])] = self.attrs.get(
                (source_node, source_attr), 0.0
            )

    def disconnect_attr(self, source, destination):
        if destination in self.connections.get(source, []):
            self.connections[source].remove(destination)
        if source in self.connections.get(destination, []):
            self.connections[destination].remove(source)

    def alias_attr(self, *args, **kwargs):
        if kwargs.get("query"):
            node = args[0]
            result = []
            for plug, alias in self.aliases.items():
                if plug.startswith(f"{node}."):
                    result.extend((alias, plug.split(".", 1)[1]))
            return result
        if kwargs.get("remove"):
            self.aliases.pop(args[0], None)
            return None
        alias, plug = args
        self.aliases[plug] = alias


class _Registry:
    def list_model_registry_members(self, root, category):
        assert category == "morph"
        return ["|Root|m0", "|Root|m1"]


class _BackendAdapter(_ReindexAdapter):
    def __post_init__(self):
        super().__post_init__()
        self.types["|Root|registry"] = "network"
        self.attrs[("|Root", "mmd_model_registry")] = None
        self.attrs[("|Root|registry", "mmd_model_registry_schema")] = "1"
        self.attrs[("|Root|registry", "modelRoot")] = None
        self.attrs[("|Root|registry", "morphMembers")] = None
        self.connections["|Root.mmd_model_registry"] = ["|Root|registry"]
        self.connections["|Root|registry.modelRoot"] = ["|Root"]
        self.connections["|Root|registry.morphMembers"] = [
            "|Root|m0",
            "|Root|m1",
        ]
        self.undo_open = False
        self.undo_count = 0

    def create_node(self, node_type, name):
        """Provide the minimal node creation surface used by narrow create tests."""
        candidate = name
        suffix = 1
        while candidate in self.types:
            candidate = f"{name}{suffix}"
            suffix += 1
        self.types[candidate] = node_type
        if node_type == "mmdMorphController":
            self.attrs[(candidate, "groupTopology")] = None
        return candidate

    def add_attr(self, node, **kwargs):
        """Persist newly declared attrs for the backend create preimage fake."""
        self.attrs[(node, kwargs["longName"])] = kwargs.get("defaultValue", 0.0)

    def undo_info(self, **kwargs):
        if kwargs.get("query") and kwargs.get("state"):
            return True
        if kwargs.get("openChunk"):
            self.undo_open = True
        if kwargs.get("closeChunk"):
            self.undo_open = False

    def undo(self):
        self.undo_count += 1


def test_backend_narrow_begin_commit_and_rollback_fingerprint():
    adapter = _BackendAdapter()
    adapter.types["|Root|boneAccum"] = "mmdBoneMorphAccum"
    adapter.all_node_types = lambda: ["network", "mmdMorphController", "mmdBoneMorphAccum"]
    adapter.attrs[("|Root|boneAccum", "contribution")] = [0, 1]
    adapter.attrs[("|Root|boneAccum", "contribution[0].morphOrder")] = 0
    adapter.attrs[("|Root|boneAccum", "contribution[1].morphOrder")] = 1
    adapter.connections["|Root|boneAccum.contribution[0].weight"] = ["|Root|m0.weight"]
    adapter.connections["|Root|boneAccum.contribution[1].weight"] = ["|Root|m1.weight"]
    adapter.connections.setdefault("|Root|m0.weight", []).append(
        "|Root|boneAccum.contribution[0].weight"
    )
    adapter.connections.setdefault("|Root|m1.weight", []).append(
        "|Root|boneAccum.contribution[1].weight"
    )
    backend = MayaSceneMetadataBackend(adapter)
    backend.begin_morph_reindex("|Root", 0, 1)
    result = apply_morph_reindex("|Root", 0, 1, adapter, registry_api=_Registry())
    backend.commit_morph_reindex("|Root", result)
    assert adapter.undo_open is False
    assert adapter.undo_count == 0

    backend.begin_morph_reindex("|Root", 0, 1)
    adapter.attrs[("|Root|controller", "groupTopology")] = "{}"
    with pytest.raises(MayaSceneMetadataError, match="rollback fingerprint"):
        backend.rollback_write("|Root")
    assert adapter.undo_count == 1


def test_adapter_swaps_controller_refs_and_only_selected_indices() -> None:
    adapter = _ReindexAdapter()
    result = apply_morph_reindex("|Root", 0, 1, adapter, registry_api=_Registry())
    assert isinstance(result, MorphReindexResult)
    assert adapter.attrs[("|Root|m0", "mmd_morph_index")] == 1
    assert adapter.attrs[("|Root|m1", "mmd_morph_index")] == 0
    assert adapter.connections["|Root|controller.outputWeight[0]"] == ["|Root|m1.weight"]
    assert adapter.connections["|Root|controller.outputWeight[1]"] == ["|Root|m0.weight"]
    assert adapter.attrs[("|Root|controller", "groupTopology")] == '{"0":[[1,0.5]]}'
    assert '"index":1' in adapter.attrs[("|Root", "mmd_display_frames_json")]
    assert dict(result.bindings) == {0: "|Root|m1", 1: "|Root|m0"}


@pytest.mark.parametrize(
    "attr,value",
    [
        ("groupTopology", '{"1":[["0",0.5]]}'),
        ("mmd_display_frames_json", '[{"elements":[{"type":true,"index":0}]}]'),
    ],
)
def test_adapter_rejects_malformed_reference_json_before_controller_write(attr, value):
    adapter = _ReindexAdapter()
    node = "|Root|controller" if attr == "groupTopology" else "|Root"
    adapter.attrs[(node, attr)] = value
    with pytest.raises(Exception):
        apply_morph_reindex("|Root", 0, 1, adapter, registry_api=_Registry())
    assert adapter.attrs[("|Root|m0", "mmd_morph_index")] == 0
    assert adapter.attrs[("|Root|m1", "mmd_morph_index")] == 1


def test_adapter_swaps_only_selected_runtime_morph_order_contributions():
    adapter = _ReindexAdapter()
    adapter.types["|Root|boneAccum"] = "mmdBoneMorphAccum"
    adapter.all_node_types = lambda: ["network", "mmdMorphController", "mmdBoneMorphAccum"]
    adapter.attrs[("|Root|boneAccum", "contribution")] = [0, 1, 2]
    adapter.attrs[("|Root|boneAccum", "contribution[0].morphOrder")] = 0
    adapter.attrs[("|Root|boneAccum", "contribution[1].morphOrder")] = 1
    adapter.attrs[("|Root|boneAccum", "contribution[2].morphOrder")] = 7
    adapter.connections["|Root|boneAccum.contribution[0].weight"] = ["|Root|m0.weight"]
    adapter.connections["|Root|boneAccum.contribution[1].weight"] = ["|Root|m1.weight"]
    adapter.connections["|Root|boneAccum.contribution[2].weight"] = ["other.weight"]
    apply_morph_reindex("|Root", 0, 1, adapter, registry_api=_Registry())
    assert adapter.attrs[("|Root|boneAccum", "contribution[0].morphOrder")] == 1
    assert adapter.attrs[("|Root|boneAccum", "contribution[1].morphOrder")] == 0
    assert adapter.attrs[("|Root|boneAccum", "contribution[2].morphOrder")] == 7


def test_adapter_remaps_exact_vertex_target_name_mapping():
    adapter = _ReindexAdapter()
    adapter.types["|Root|blend"] = "blendShape"
    adapter.attrs[("|Root|m0", "mmd_morph_type")] = "vertex"
    adapter.attrs[("|Root|m1", "mmd_morph_type")] = "vertex"
    adapter.attrs[("|Root|blend", "mmd_blendshape_morph_names_json")] = (
        '{"0":{"name":"m0","index":0},"1":{"name":"m1","index":1}}'
    )
    adapter.connections["|Root|controller.outputWeight[0]"] = ["|Root|blend.weight[0]"]
    adapter.connections["|Root|controller.outputWeight[1]"] = ["|Root|blend.weight[1]"]
    apply_morph_reindex("|Root", 0, 1, adapter, registry_api=_Registry())
    mapping = __import__("json").loads(
        adapter.attrs[("|Root|blend", "mmd_blendshape_morph_names_json")]
    )
    assert mapping["0"]["index"] == 1
    assert mapping["1"]["index"] == 0


class _NarrowMetadata:
    fail = False

    def read_spec(self, root):
        raise AssertionError("full spec read is forbidden on narrow move")

    def commit_morph_reindex(self, root, result):
        if self.fail:
            raise RuntimeError("commit mismatch")
        self.commits = getattr(self, "commits", 0) + 1


class _NarrowBackend:
    def __init__(self, fail=False):
        self.fail = fail
        self.begins = 0
        self.rollbacks = 0

    def begin_morph_reindex(self, root, index, target):
        self.begins += 1

    def rollback_write(self, root):
        self.rollbacks += 1

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class _NarrowMorph:
    def apply_morph_reindex(self, root, index, target, cmds):
        return MorphReindexResult(index, target, (index, target), ((index, "m1"), (target, "m0")))


class _NoopMaterial:
    def create_material(self, *args, **kwargs):
        return None

    resolve_material = create_material
    delete_material = create_material


class _NoopBone:
    def capture_rest_position(self, *args, **kwargs):
        return None

    apply_bone_reindex = capture_rest_position
    unregister_existing_joint = capture_rest_position
    register_existing_joint = capture_rest_position


class _Cmds:
    def object_exists(self, root):
        return True

    def ls(self, *args, **kwargs):
        return []

    def list_relatives(self, *args, **kwargs):
        return []

    def xform(self, *args, **kwargs):
        return None


def test_coordinator_move_uses_narrow_transaction_and_rolls_back_commit_failure():
    backend = _NarrowBackend()
    metadata = _NarrowMetadata()
    coordinator = MayaModelAuthoringCoordinator(
        metadata,
        backend,
        _NoopMaterial(),
        _Cmds(),
        bone_api=_NoopBone(),
        morph_authoring=_NarrowMorph(),
    )
    result = coordinator.move_morph("|Root", 0, 1)
    assert result.swapped_indices == (0, 1)
    assert backend.begins == 1
    assert metadata.commits == 1

    metadata.fail = True
    with pytest.raises(MayaModelAuthoringCoordinatorError, match="commit mismatch"):
        coordinator.move_morph("|Root", 0, 1)
    assert backend.rollbacks == 1


class _Item:
    def __init__(self, key):
        self.key = key
        self.text = ""

    def data(self, role):
        return self.key

    def setText(self, text):
        self.text = text


class _List:
    def __init__(self, items):
        self.items = items
        self.current = None

    def count(self):
        return len(self.items)

    def item(self, index):
        return self.items[index]

    def setCurrentItem(self, item):
        self.current = item


def test_presenter_swaps_two_rows_without_reload_and_remaps_cached_refs():
    presenter = object.__new__(MorphPresenter)
    presenter.current_morph = "a"
    presenter.morph_data = {
        "a": {"index": 0, "name_jp": "A", "name_en": "A", "type": 1},
        "b": {
            "index": 1,
            "name_jp": "B",
            "name_en": "B",
            "type": 0,
            "mmd_morph_type": "group",
            "offsets": [{"morph_index": 0, "morph_rate": 1.0}],
        },
    }
    presenter._authoring_spec = _spec(_morph(0, binding="a"), _morph(1, binding="b"))
    presenter._authoring_morphs_by_index = {item.index: item for item in presenter._authoring_spec.morphs}
    presenter._morphs_by_index = {}
    presenter.view = SimpleNamespace(morph_list=_List([_Item("a"), _Item("b")]))
    result = MorphReindexResult(0, 1, (0, 1), ((0, "b"), (1, "a")))
    presenter._swap_morph_rows(result, 0, 1)
    assert [item.key for item in presenter.view.morph_list.items] == ["b", "a"]
    assert presenter.morph_data["b"]["offsets"][0]["morph_index"] == 1
    assert presenter._authoring_morphs_by_index[1].binding_identity == "a"
