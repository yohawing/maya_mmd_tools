"""Maya observation tests for the Material list read projection."""

from collections import Counter

import pytest

from mmd_tools.adapters.maya_material_read_projection import (
    MayaMaterialReadProjectionAdapter,
    MayaMaterialReadProjectionError,
)
from mmd_tools.core.constants import (
    ATTR_MMD_MATERIAL,
    ATTR_MMD_MODEL_REGISTRY,
    ATTR_MMD_REGISTRY_MATERIAL_MEMBERS,
    ATTR_MMD_REGISTRY_ROOT,
    ATTR_MMD_REGISTRY_SCHEMA,
)
from mmd_tools.core.material_read_projection import MaterialAssignmentKind
from mmd_tools.core.model_authoring_spec import MmdMaterialSpec


class FakeMayaAdapter:
    def __init__(self):
        self.calls = []
        self.canonical = {
            "root": ("|root",),
            "meshAShape": ("|root|meshA|meshAShape",),
            "meshBShape": ("|root|meshB|meshBShape",),
            "|root|meshA": ("|root|meshA",),
            "|root|meshB": ("|root|meshB",),
            "|root|meshA|meshAShape": ("|root|meshA|meshAShape",),
            "|root|meshB|meshBShape": ("|root|meshB|meshBShape",),
            "matA": ("matA",),
            "matB": ("matB",),
            "registry": ("registry",),
            "sgA": ("sgA",),
            "sgB": ("sgB",),
            "sgFace": ("sgFace",),
            "fileA": ("fileA",),
        }
        self.meshes = ["meshAShape", "meshBShape"]
        self.attributes = {
            ("|root", ATTR_MMD_MODEL_REGISTRY),
            ("registry", ATTR_MMD_REGISTRY_SCHEMA),
            ("registry", ATTR_MMD_REGISTRY_ROOT),
            ("registry", ATTR_MMD_REGISTRY_MATERIAL_MEMBERS),
            ("matA", ATTR_MMD_MATERIAL),
            ("matB", ATTR_MMD_MATERIAL),
        }
        self.connections = {
            "|root.{}".format(ATTR_MMD_MODEL_REGISTRY): ["registry"],
            "registry.{}".format(ATTR_MMD_REGISTRY_ROOT): ["|root"],
            "registry.{}".format(ATTR_MMD_REGISTRY_MATERIAL_MEMBERS): ["matA", "matB"],
            "|root|meshA|meshAShape": ["sgA", "sgFace"],
            "|root|meshB|meshBShape": ["sgB"],
            "matA": ["sgA", "sgFace"],
            "matB": ["sgB"],
        }
        self.set_members = {
            "sgA": ["|root|meshA"],
            "sgFace": ["|root|meshA|meshAShape.f[2:4]"],
            "sgB": ["|root|meshB|meshBShape.f[0:7]"],
        }
        self.raising_sets = set()

    def ls(self, value, **kwargs):
        self.calls.append(("ls", value, tuple(sorted(kwargs.items()))))
        return list(self.canonical.get(value, ()))

    def list_relatives(self, root, **kwargs):
        self.calls.append(("list_relatives", root, tuple(sorted(kwargs.items()))))
        return list(self.meshes)

    def attribute_exists(self, attr, node):
        self.calls.append(("attribute_exists", attr, node))
        return (node, attr) in self.attributes

    def get_attr(self, plug):
        self.calls.append(("get_attr", plug))
        if plug == "registry.{}".format(ATTR_MMD_REGISTRY_SCHEMA):
            return "1"
        raise KeyError(plug)

    def list_connections(self, plug, **kwargs):
        self.calls.append(("list_connections", plug, tuple(sorted(kwargs.items()))))
        return list(self.connections.get(plug, ()))

    def sets(self, shading_group, **kwargs):
        self.calls.append(("sets", shading_group, tuple(sorted(kwargs.items()))))
        if shading_group in self.raising_sets:
            raise RuntimeError("sets query unavailable")
        return list(self.set_members.get(shading_group, ()))


def _materials():
    # Deliberately reverse input order; output is semantic PMX index order.
    return (
        MmdMaterialSpec("B", index=2, binding_identity="matB"),
        MmdMaterialSpec("A", index=0, binding_identity="matA"),
    )


def _read(maya, materials=None):
    return MayaMaterialReadProjectionAdapter(maya).read_list_projection(
        "root",
        _materials() if materials is None else materials,
    )


def test_registry_projection_orders_semantics_and_classifies_mixed_and_faces():
    maya = FakeMayaAdapter()

    projection = _read(maya)

    assert projection.root_identity == "|root"
    assert tuple(item.index for item in projection.items) == (0, 2)
    first, second = projection.items
    assert first.binding_identity == "matA"
    assert first.assignment.kind is MaterialAssignmentKind.MIXED
    assert first.assignment.mesh_count == 1
    assert first.assignment.face_count == 3
    assert second.assignment.kind is MaterialAssignmentKind.EXPLICIT_FACES
    assert second.assignment.face_count == 8


def test_root_and_membership_scans_are_fixed_and_cached_per_shading_group():
    maya = FakeMayaAdapter()
    # Sharing is malformed as material ownership, but one material can expose
    # the same SG twice without causing duplicate set queries.
    maya.connections["matA"] = ["sgA", "sgA", "sgFace"]

    _read(maya)

    methods = Counter(call[0] for call in maya.calls)
    assert methods["list_relatives"] == 1
    assert Counter(call[1] for call in maya.calls if call[0] == "sets") == {
        "sgA": 1,
        "sgFace": 1,
        "sgB": 1,
    }
    assert Counter(
        call[1] for call in maya.calls if call[0] == "list_relatives"
    ) == {"|root": 1}


def test_registry_owned_unassigned_and_unavailable_set_query_are_distinct():
    maya = FakeMayaAdapter()
    maya.connections["matA"] = []
    maya.raising_sets.add("sgB")

    projection = _read(maya)

    assert projection.item_for_binding("matA").assignment.kind is MaterialAssignmentKind.EMPTY
    assert projection.item_for_binding("matB").assignment.kind is MaterialAssignmentKind.UNKNOWN


def test_legacy_discovery_is_limited_to_root_mesh_shading_inputs():
    maya = FakeMayaAdapter()
    maya.attributes.remove(("|root", ATTR_MMD_MODEL_REGISTRY))
    maya.connections["sgA"] = ["fileA", "matA"]
    maya.connections["sgFace"] = ["matA"]
    maya.connections["sgB"] = ["matB"]

    projection = _read(maya)

    assert tuple(item.binding_identity for item in projection.items) == ("matA", "matB")
    assert not any(
        call[0] == "ls" and call[1] == "*"
        for call in maya.calls
    )


def test_other_root_assignment_and_instanced_mesh_fail_closed():
    maya = FakeMayaAdapter()
    maya.canonical["|other|mesh"] = ("|other|mesh",)
    maya.set_members["sgB"] = ["|other|mesh"]
    with pytest.raises(MayaMaterialReadProjectionError, match="outside model root"):
        _read(maya)

    maya = FakeMayaAdapter()
    maya.canonical["meshAShape"] = (
        "|root|meshA|meshAShape",
        "|other|meshA|meshAShape",
    )
    with pytest.raises(MayaMaterialReadProjectionError, match="instanced"):
        _read(maya)


def test_duplicate_registry_binding_index_and_semantic_binding_fail_closed():
    maya = FakeMayaAdapter()
    maya.connections[
        "registry.{}".format(ATTR_MMD_REGISTRY_MATERIAL_MEMBERS)
    ] = ["matA", "matA"]
    with pytest.raises(MayaMaterialReadProjectionError, match="duplicate canonical"):
        _read(maya)

    maya = FakeMayaAdapter()
    duplicate_index = (
        MmdMaterialSpec("A", index=0, binding_identity="matA"),
        MmdMaterialSpec("B", index=0, binding_identity="matB"),
    )
    with pytest.raises(MayaMaterialReadProjectionError, match="duplicate material index"):
        _read(maya, duplicate_index)

    maya = FakeMayaAdapter()
    duplicate_binding = (
        MmdMaterialSpec("A", index=0, binding_identity="matA"),
        MmdMaterialSpec("Again", index=1, binding_identity="matA"),
    )
    with pytest.raises(MayaMaterialReadProjectionError, match="duplicate semantic"):
        _read(maya, duplicate_binding)


def test_semantic_batch_must_exactly_match_discovered_ownership():
    maya = FakeMayaAdapter()

    with pytest.raises(MayaMaterialReadProjectionError, match="exactly match"):
        _read(maya, (_materials()[0],))
    with pytest.raises(TypeError, match="tuple"):
        MayaMaterialReadProjectionAdapter(maya).read_list_projection("root", list(_materials()))


def test_semantic_spec_binding_must_itself_be_canonical_not_an_alias():
    maya = FakeMayaAdapter()
    maya.canonical["matAlias"] = ("matA",)
    aliased = (
        MmdMaterialSpec("A", index=0, binding_identity="matAlias"),
        _materials()[0],
    )

    with pytest.raises(MayaMaterialReadProjectionError, match="already be canonical"):
        _read(maya, aliased)


def test_whole_object_member_with_multiple_owned_shapes_is_ambiguous():
    maya = FakeMayaAdapter()
    maya.meshes.insert(1, "meshASecondShape")
    maya.canonical["meshASecondShape"] = ("|root|meshA|meshASecondShape",)
    maya.canonical["|root|meshA|meshASecondShape"] = (
        "|root|meshA|meshASecondShape",
    )
    maya.connections["|root|meshA|meshASecondShape"] = ["sgA"]

    with pytest.raises(MayaMaterialReadProjectionError, match="exactly one owned mesh"):
        _read(maya)


def test_malformed_registry_never_falls_back_to_legacy_discovery():
    maya = FakeMayaAdapter()
    maya.connections["registry.{}".format(ATTR_MMD_REGISTRY_ROOT)] = ["|other"]
    maya.canonical["|other"] = ("|other",)

    with pytest.raises(MayaMaterialReadProjectionError, match="another root"):
        _read(maya)
