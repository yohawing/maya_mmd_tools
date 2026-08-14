import json
from unittest.mock import Mock

import pytest

from mmd_tools.adapters.native_morph_binding_query import (
    COMMAND_QUERY_MORPH_BINDINGS,
    NativeMorphBindingQueryError,
    NativeMorphBindingQueryGateway,
)


def _result():
    return {
        "version": 1,
        "command": COMMAND_QUERY_MORPH_BINDINGS,
        "ok": True,
        "requestedController": "controller",
        "controller": "controller",
        "slot": 3,
        "destinations": [
            {"node": "faceBS", "nodeType": "blendShape", "plug": "faceBS.weight[7]"},
            {"node": "bodyBS", "nodeType": "blendShape", "plug": "bodyBS.weight[2]"},
        ],
        "blendShapes": [
            {
                "node": "faceBS",
                "aliases": [{"alias": "smile", "plug": "faceBS.weight[7]"}],
                "rawNameMappingJson": '{"7":{"name":"笑い","index":4}}',
            },
            {
                "node": "bodyBS",
                "aliases": [{"alias": "smileBody", "plug": "bodyBS.weight[2]"}],
                "rawNameMappingJson": None,
            },
        ],
    }


def test_unavailable_native_query_returns_none_without_invocation():
    adapter = Mock()
    adapter.command_exists.return_value = False
    assert NativeMorphBindingQueryGateway(adapter).query_if_available("controller", 3) is None
    adapter.invoke_native_command.assert_not_called()


def test_native_query_parses_multi_mesh_raw_and_legacy_observations():
    adapter = Mock()
    adapter.command_exists.return_value = True
    adapter.invoke_native_command.return_value = json.dumps(_result(), ensure_ascii=False)
    observations = NativeMorphBindingQueryGateway(adapter).query_if_available("controller", 3)
    assert observations is not None
    assert [item.plug for item in observations.destinations] == ["faceBS.weight[7]", "bodyBS.weight[2]"]
    assert observations.aliases["faceBS"] == (("smile", "faceBS.weight[7]"),)
    assert observations.raw_mappings["faceBS"]["7"] == {"name": "笑い", "index": 4}
    assert "bodyBS" not in observations.raw_mappings


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        '{"version":1,"version":1}',
        json.dumps({**_result(), "version": True}),
        json.dumps({**_result(), "ok": False, "error": {"code": "maya_query_failed", "message": "failed"}}),
        json.dumps({**_result(), "blendShapes": [{"node": "faceBS", "aliases": [], "rawNameMappingJson": '{"7":{},"7":{}}'}]}),
    ],
)
def test_registered_native_failure_or_malformed_dto_is_not_hidden(raw):
    adapter = Mock()
    adapter.command_exists.return_value = True
    adapter.invoke_native_command.return_value = raw
    with pytest.raises(NativeMorphBindingQueryError):
        NativeMorphBindingQueryGateway(adapter).query_if_available("controller", 3)


def test_transport_failure_is_not_hidden():
    adapter = Mock()
    adapter.command_exists.return_value = True
    adapter.invoke_native_command.side_effect = RuntimeError("Maya failed")
    with pytest.raises(NativeMorphBindingQueryError, match="transport"):
        NativeMorphBindingQueryGateway(adapter).query_if_available("controller", 3)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.pop("requestedController"),
        lambda value: value.update(requestedController="other"),
        lambda value: value.update(slot=8),
        lambda value: value["blendShapes"][0].pop("rawNameMappingJson"),
    ],
)
def test_success_dto_identity_and_raw_mapping_presence_are_strict(mutate):
    adapter = Mock()
    adapter.command_exists.return_value = True
    result = _result()
    mutate(result)
    adapter.invoke_native_command.return_value = json.dumps(result)
    with pytest.raises(NativeMorphBindingQueryError):
        NativeMorphBindingQueryGateway(adapter).query_if_available("controller", 3)


@pytest.mark.parametrize(
    "result",
    [
        {"version": 1, "command": COMMAND_QUERY_MORPH_BINDINGS, "ok": False, "error": {"code": "failed", "message": "no", "extra": 1}},
        {"version": 1, "command": COMMAND_QUERY_MORPH_BINDINGS, "ok": False, "error": {"code": "failed", "message": "no"}, "unexpected": 1},
    ],
)
def test_error_dto_rejects_extra_fields(result):
    adapter = Mock()
    adapter.command_exists.return_value = True
    adapter.invoke_native_command.return_value = json.dumps(result)
    with pytest.raises(NativeMorphBindingQueryError, match="invalid error"):
        NativeMorphBindingQueryGateway(adapter).query_if_available("controller", 3)
