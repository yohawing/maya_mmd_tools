"""Maya observation tests for the model-owned morph read projection."""

from collections import Counter
import json

import pytest

from mmd_tools.adapters import maya_morph_read_projection as projection_module
from mmd_tools.adapters.maya_morph_read_projection import (
    MayaMorphReadProjectionAdapter,
    MayaMorphReadProjectionError,
)
from mmd_tools.core.constants import ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON
from mmd_tools.core.morph_read_projection import MorphProjectionRequest


class FakeMayaAdapter:
    def __init__(self):
        self.calls = []
        self.canonical = {
            "root": ("|root",),
            "|root": ("|root",),
            "meshA": ("|root|meshA",),
            "|root|meshA": ("|root|meshA",),
            "meshB": ("|root|meshB",),
            "|root|meshB": ("|root|meshB",),
            "controller": ("controller",),
            "faceA": ("faceA",),
            "faceB": ("faceB",),
            "morphA": ("morphA",),
            "morphB": ("morphB",),
        }
        self.meshes = ["meshA", "meshB"]
        self.histories = {
            "|root|meshA": ["skinA", "faceA"],
            "|root|meshB": ["skinB", "faceB"],
        }
        self.blend_shapes = {"faceA", "faceB"}
        self.connections = {
            "|root.mmd_morph_controller": ["controller"],
            "controller.outputWeight[2]": ["faceA.weight[2]", "faceB.weight[2]"],
            "controller.outputWeight[3]": ["faceA.weight[3]"],
            "morphA.mmd_model_root": ["|root"],
            "morphB.mmd_model_root": ["|root"],
        }
        self.aliases = {
            "faceA": ["Same", "weight[2]", "SameAgain", "weight[3]"],
            "faceB": ["Same", "weight[2]"],
        }
        self.raw_json = {
            "faceA": json.dumps(
                {
                    "2": {"name": "Same", "index": 2},
                    "3": {"name": "Same", "index": 3},
                }
            ),
            "faceB": json.dumps({"2": {"name": "Same", "index": 2}}),
        }
        self.raw_attrs = {"faceA", "faceB"}
        self.intermediate_meshes = set()
        self.morph_indices = {"morphA": 2, "morphB": 3}
        self.morph_types = {"morphA": "vertex", "morphB": "vertex"}
        self.morph_names = {"morphA": "Same", "morphB": "Same"}

    def ls(self, value, **kwargs):
        self.calls.append(("ls", _hashable(value), tuple(sorted(kwargs.items()))))
        if kwargs.get("type") == "blendShape":
            return [item for item in value if item in self.blend_shapes]
        if kwargs.get("long"):
            return list(self.canonical.get(value, ()))
        return []

    def attribute_exists(self, attr, node):
        self.calls.append(("attribute_exists", attr, node))
        if attr == "mmd_morph_controller":
            return node == "|root"
        if attr == ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON:
            return node in self.raw_attrs
        if attr == "mmd_morph_index":
            return node in self.morph_indices
        if attr == "mmd_morph_type":
            return node in self.morph_types
        if attr == "mmd_morph_name":
            return node in self.morph_names
        if attr == "mmd_model_root":
            return node in self.morph_indices
        return False

    def list_connections(self, plug, **kwargs):
        self.calls.append(("list_connections", plug, tuple(sorted(kwargs.items()))))
        return list(self.connections.get(plug, ()))

    def list_relatives(self, root, **kwargs):
        self.calls.append(("list_relatives", root, tuple(sorted(kwargs.items()))))
        return list(self.meshes)

    def list_history(self, shape):
        self.calls.append(("list_history", shape))
        return list(self.histories.get(shape, ()))

    def node_type(self, node):
        self.calls.append(("node_type", node))
        if node in self.morph_indices:
            return "network"
        return "blendShape" if node in self.blend_shapes else "unknown"

    def alias_attr(self, node, **kwargs):
        self.calls.append(("alias_attr", node, tuple(sorted(kwargs.items()))))
        return list(self.aliases.get(node, ()))

    def get_attr(self, plug, **kwargs):
        self.calls.append(("get_attr", plug, tuple(sorted(kwargs.items()))))
        node, attr = plug.rsplit(".", 1)
        if attr == ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON:
            return self.raw_json[node]
        if attr == "mmd_morph_index":
            return self.morph_indices[node]
        if attr == "mmd_morph_type":
            return self.morph_types[node]
        if attr == "mmd_morph_name":
            return self.morph_names[node]
        if attr == "intermediateObject":
            return node in self.intermediate_meshes
        raise KeyError(plug)


def _hashable(value):
    return tuple(value) if isinstance(value, list) else value


def _request(index, binding):
    return MorphProjectionRequest("Same", index, binding)


def _read(maya, requests=None):
    return MayaMorphReadProjectionAdapter(maya).read_blend_shape_projection(
        "root",
        requests or (_request(2, "morphA"), _request(3, "morphB")),
    )


def test_collects_multi_mesh_and_duplicate_raw_names_by_global_index():
    maya = FakeMayaAdapter()

    projection = _read(maya)

    assert projection.root_identity == "|root"
    assert projection.controller_identity == "controller"
    assert projection.owned_mesh_identities == ("|root|meshA", "|root|meshB")
    assert projection.owned_blend_shape_identities == ("faceA", "faceB")
    assert projection.binding_for_index(2).binding_identity == "morphA"
    assert projection.binding_for_index(2).preview_plugs == (
        "faceA.weight[2]",
        "faceB.weight[2]",
    )
    assert projection.binding_for_index(3).binding_identity == "morphB"
    assert projection.binding_for_index(3).preview_plugs == ("faceA.weight[3]",)


def test_scene_observations_are_cached_per_owned_node():
    maya = FakeMayaAdapter()

    _read(maya)

    methods = Counter(call[0] for call in maya.calls)
    assert methods["list_relatives"] == 1
    assert methods["list_history"] == 2
    assert Counter(call[1] for call in maya.calls if call[0] == "list_history") == {
        "|root|meshA": 1,
        "|root|meshB": 1,
    }
    assert Counter(call[1] for call in maya.calls if call[0] == "alias_attr") == {
        "faceA": 1,
        "faceB": 1,
    }
    raw_reads = [
        call[1]
        for call in maya.calls
        if call[0] == "get_attr"
        and call[1].endswith("." + ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON)
    ]
    assert Counter(raw_reads) == {
        "faceA." + ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON: 1,
        "faceB." + ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON: 1,
    }


def test_legacy_alias_fallback_is_preserved_as_warning():
    maya = FakeMayaAdapter()
    maya.meshes = ["meshA"]
    maya.raw_attrs.clear()
    maya.aliases["faceA"] = ["Same", "weight[2]"]
    maya.connections["controller.outputWeight[2]"] = ["faceA.weight[2]"]

    projection = _read(maya, (_request(2, "morphA"),))

    assert [warning.code for warning in projection.morphs[0].warnings] == [
        "legacy_sanitized_alias_fallback"
    ]


@pytest.mark.parametrize(
    "raw_json, error",
    [
        (json.dumps({"2": {"name": "Other", "index": 2}}), "stale_raw_name_mapping"),
        ('{"2": {"name": "Same", "index": 2}, "2": {}}', "duplicate object key"),
        ("[]", "must contain an object"),
    ],
)
def test_stale_or_malformed_raw_mapping_fails_closed(raw_json, error):
    maya = FakeMayaAdapter()
    maya.meshes = ["meshA"]
    maya.connections["controller.outputWeight[2]"] = ["faceA.weight[2]"]
    maya.aliases["faceA"] = ["Same", "weight[2]"]
    maya.raw_json["faceA"] = raw_json

    with pytest.raises(MayaMorphReadProjectionError, match=error):
        _read(maya, (_request(2, "morphA"),))


def test_foreign_blendshape_destination_fails_closed():
    maya = FakeMayaAdapter()
    maya.canonical["foreignBS"] = ("foreignBS",)
    maya.blend_shapes.add("foreignBS")
    maya.aliases["foreignBS"] = ["Same", "weight[2]"]
    maya.raw_attrs.add("foreignBS")
    maya.raw_json["foreignBS"] = json.dumps({"2": {"name": "Same", "index": 2}})
    maya.connections["controller.outputWeight[2]"] = ["foreignBS.weight[2]"]

    with pytest.raises(MayaMorphReadProjectionError, match="outside the model-owned"):
        _read(maya, (_request(2, "morphA"),))


def test_ambiguous_canonical_identity_and_duplicate_indices_fail_before_projection():
    maya = FakeMayaAdapter()
    maya.canonical["morphA"] = ("morphA", "other:morphA")
    with pytest.raises(MayaMorphReadProjectionError, match="no unique canonical identity"):
        _read(maya, (_request(2, "morphA"),))

    maya = FakeMayaAdapter()
    with pytest.raises(MayaMorphReadProjectionError, match="duplicate global morph index"):
        _read(maya, (_request(2, "morphA"), _request(2, "morphB")))


def test_distinct_input_aliases_cannot_resolve_to_one_semantic_binding():
    maya = FakeMayaAdapter()
    maya.canonical["morphAlias"] = ("morphA",)

    with pytest.raises(
        MayaMorphReadProjectionError,
        match="duplicate canonical morph binding identity",
    ):
        _read(maya, (_request(2, "morphA"), _request(3, "morphAlias")))


@pytest.mark.parametrize(
    "mutation, error",
    [
        (lambda maya: maya.morph_indices.__setitem__("morphA", 9), "index does not match"),
        (lambda maya: maya.morph_indices.pop("morphA"), "must be a network node"),
        (lambda maya: maya.morph_types.__setitem__("morphA", "bone"), "type does not match"),
        (lambda maya: maya.morph_names.__setitem__("morphA", "Other"), "raw name does not match"),
        (
            lambda maya: maya.connections.__setitem__("morphA.mmd_model_root", ["meshA"]),
            "is not owned",
        ),
    ],
)
def test_semantic_binding_type_index_and_legacy_root_ownership_fail_closed(mutation, error):
    maya = FakeMayaAdapter()
    mutation(maya)

    with pytest.raises(MayaMorphReadProjectionError, match=error):
        _read(maya, (_request(2, "morphA"),))


def test_registry_membership_is_authoritative_when_present(monkeypatch):
    maya = FakeMayaAdapter()
    monkeypatch.setattr(
        projection_module,
        "list_model_registry_members_from_adapter",
        lambda *_args: ["morphA"],
    )
    maya.connections.pop("morphA.mmd_model_root")

    projection = _read(maya, (_request(2, "morphA"),))
    assert projection.morphs[0].binding_identity == "morphA"

    monkeypatch.setattr(
        projection_module,
        "list_model_registry_members_from_adapter",
        lambda *_args: ["morphB"],
    )
    with pytest.raises(MayaMorphReadProjectionError, match="not owned by the model registry"):
        _read(maya, (_request(2, "morphA"),))


def test_projects_network_capabilities_and_non_intermediate_meshes_without_bindings():
    maya = FakeMayaAdapter()
    maya.morph_types.update({"morphA": "group", "morphB": "material"})
    maya.intermediate_meshes.add("|root|meshB")
    requests = (
        MorphProjectionRequest("Same", 2, "morphA", "group"),
        MorphProjectionRequest("Same", 3, "morphB", "material"),
    )

    projection = MayaMorphReadProjectionAdapter(maya).read_blend_shape_projection(
        "root",
        requests,
        {"3": ((2, 1.0),)},
    )

    assert projection.owned_non_intermediate_mesh_identities == ("|root|meshA",)
    assert projection.binding_for_index(2).runtime_supported is True
    assert projection.binding_for_index(2).bindings == ()
    assert projection.binding_for_index(2).runtime_targets == (
        "controller.inputWeight[2]",
    )
    assert projection.binding_for_index(3).runtime_supported is True


@pytest.mark.parametrize(
    "topology, error",
    [
        ({"03": ((2, 1.0),)}, "target is invalid"),
        ({True: ((2, 1.0),)}, "target is invalid"),
        ({-1: ((2, 1.0),)}, "target is invalid"),
        ({3: ((2, 1.0),), "3": ((2, 1.0),)}, "duplicated after normalization"),
    ],
)
def test_topology_target_keys_fail_closed_unless_canonical_decimal(topology, error):
    with pytest.raises(MayaMorphReadProjectionError, match=error):
        MayaMorphReadProjectionAdapter._normalize_topology(topology)
