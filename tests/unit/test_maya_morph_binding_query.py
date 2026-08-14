"""Tests for the Maya observation boundary of morph binding resolution."""

from __future__ import annotations

import json
from typing import Any

import pytest

from mmd_tools.adapters.maya_morph_binding_query import (
    MayaMorphBindingQueryError,
    resolve_maya_morph_binding,
)
from mmd_tools.core.morph_binding_resolver import (
    MorphBindingRequest,
    MorphBindingResolutionError,
)


class FakeMaya:
    def __init__(self) -> None:
        self.destinations = ["faceBS.RenamedAlias"]
        self.long_names = {"faceBS": "|rig|faceBS"}
        self.aliases = {
            "|rig|faceBS": ["RenamedAlias", "weight[7]"],
        }
        self.raw = {
            "|rig|faceBS": {"7": {"name": "笑い", "index": 4}},
        }
        self.raw_json = None

    def list_connections(self, plug: str, **kwargs: Any) -> list[str]:
        assert plug == "controller.outputWeight[4]"
        assert kwargs == {"source": False, "destination": True, "plugs": True}
        return list(self.destinations)

    def ls(self, node: str, **kwargs: Any) -> list[str]:
        assert kwargs == {"long": True}
        value = self.long_names.get(node)
        return [value] if value is not None else []

    def node_type(self, node: str) -> str:
        return "blendShape"

    def alias_attr(self, node: str, **kwargs: Any) -> list[str]:
        assert kwargs == {"query": True}
        return list(self.aliases[node])

    def attribute_exists(self, attr: str, node: str) -> bool:
        return node in self.raw

    def get_attr(self, path: str) -> str:
        node, _attr = path.rsplit(".", 1)
        return self.raw_json or json.dumps(self.raw[node], ensure_ascii=False)


def _request() -> MorphBindingRequest:
    return MorphBindingRequest(
        raw_pmx_name="笑い",
        global_morph_index=4,
        controller_identity="controller",
        controller_slot=4,
    )


def test_query_canonicalizes_alias_destination_and_uses_raw_identity() -> None:
    maya = FakeMaya()

    resolution = resolve_maya_morph_binding(maya, _request())

    binding = resolution.bindings[0]
    assert binding.blend_shape_identity == "|rig|faceBS"
    assert binding.alias == "RenamedAlias"
    assert binding.logical_target_index == 7
    assert binding.weight_plug == "|rig|faceBS.weight[7]"
    assert resolution.warnings == ()


def test_query_collects_multiple_blendshape_destinations() -> None:
    maya = FakeMaya()
    maya.destinations.append("bodyBS.weight[2]")
    maya.long_names["bodyBS"] = "|rig|bodyBS"
    maya.aliases["|rig|bodyBS"] = ["BodySmile", "w[2]"]
    maya.raw["|rig|bodyBS"] = {"2": {"name": "笑い", "index": 4}}

    resolution = resolve_maya_morph_binding(maya, _request())

    assert [binding.blend_shape_identity for binding in resolution.bindings] == [
        "|rig|bodyBS",
        "|rig|faceBS",
    ]


def test_query_uses_supplied_destination_snapshot_without_graph_requery() -> None:
    maya = FakeMaya()

    def unexpected_query(*_args: Any, **_kwargs: Any) -> list[str]:
        raise AssertionError("output graph must not be queried twice")

    maya.list_connections = unexpected_query  # type: ignore[method-assign]

    resolution = resolve_maya_morph_binding(
        maya,
        _request(),
        destination_values=("faceBS.RenamedAlias",),
    )

    assert resolution.bindings[0].logical_target_index == 7


def test_query_preserves_resolver_error_for_stale_raw_mapping() -> None:
    maya = FakeMaya()
    maya.raw["|rig|faceBS"]["7"]["name"] = "別名"

    with pytest.raises(MorphBindingResolutionError, match="stale_raw_name_mapping"):
        resolve_maya_morph_binding(maya, _request())


def test_query_fails_closed_without_unique_canonical_identity() -> None:
    maya = FakeMaya()
    maya.long_names.clear()

    with pytest.raises(MayaMorphBindingQueryError, match="unique canonical identity"):
        resolve_maya_morph_binding(maya, _request())


def test_query_fails_closed_on_duplicate_raw_mapping_key() -> None:
    maya = FakeMaya()
    maya.raw_json = (
        '{"7":{"name":"笑い","index":4},'
        '"7":{"name":"別名","index":4}}'
    )

    with pytest.raises(MayaMorphBindingQueryError, match="duplicate object key"):
        resolve_maya_morph_binding(maya, _request())
