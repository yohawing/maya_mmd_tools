import json

import pytest

from mmd_tools.adapters.native_authoring_command import (
    COMMAND_SET_MORPH_WEIGHTS,
    NativeAuthoringCommandGateway,
    NativeCommandDomainError,
    NativeCommandProtocolError,
    NativeCommandTransportError,
    NativeCommandUnavailable,
)


class _Cmds:
    def __init__(self, result=None, error=None, exists=True):
        self.result = result
        self.error = error
        self.exists = exists
        self.calls = []

    def command_exists(self, command):
        self.calls.append(("exists", command))
        return self.exists

    def invoke_native_command(self, command, **kwargs):
        self.calls.append(("invoke", command, kwargs))
        if self.error:
            raise self.error
        return self.result


def test_gateway_sends_utf8_versioned_payload_without_undo_chunk():
    cmds = _Cmds('{"version":1,"command":"mmdAuthoringSetAttrs","ok":true,"phase":"redo"}')
    result = NativeAuthoringCommandGateway(cmds).set_attrs(
        [{"plug": "|root|node.mmdAuthoringWitnessString", "type": "string", "value": "日本語"}]
    )
    assert result["phase"] == "redo"
    payload = json.loads(cmds.calls[1][2]["payload"])
    assert payload["version"] == 1
    assert payload["updates"][0]["value"] == "日本語"
    assert all(call[0] != "undoInfo" for call in cmds.calls)


def test_morph_weight_gateway_uses_dedicated_allowlisted_command():
    cmds = _Cmds(
        '{"version":1,"command":"mmdAuthoringSetMorphWeights","ok":true,"phase":"redo",'
        '"plugs":["controller.inputWeight[0]"],"values":[0.75]}'
    )
    result = NativeAuthoringCommandGateway(cmds).set_morph_weights(
        [{"plug": "controller.inputWeight[0]", "value": 0.75}]
    )
    assert result["command"] == COMMAND_SET_MORPH_WEIGHTS
    assert cmds.calls[0] == ("exists", COMMAND_SET_MORPH_WEIGHTS)
    payload = json.loads(cmds.calls[1][2]["payload"])
    assert payload == {
        "updates": [{"plug": "controller.inputWeight[0]", "value": 0.75}],
        "version": 1,
    }


def test_morph_weight_gateway_rejects_missing_canonical_readback_values():
    cmds = _Cmds(
        '{"version":1,"command":"mmdAuthoringSetMorphWeights","ok":true,"phase":"redo"}'
    )
    with pytest.raises(NativeCommandProtocolError, match="canonical values"):
        NativeAuthoringCommandGateway(cmds).set_morph_weights(
            [{"plug": "controller.inputWeight[0]", "value": 0.75}]
        )


def test_morph_weight_gateway_wraps_huge_response_value_as_protocol_error():
    cmds = _Cmds(
        json.dumps(
            {
                "version": 1,
                "command": "mmdAuthoringSetMorphWeights",
                "ok": True,
                "phase": "redo",
                "plugs": ["controller.inputWeight[0]"],
                "values": [10**400],
            }
        )
    )
    with pytest.raises(NativeCommandProtocolError, match="canonical values"):
        NativeAuthoringCommandGateway(cmds).set_morph_weights(
            [{"plug": "controller.inputWeight[0]", "value": 0.75}]
        )


def test_only_unregistered_command_is_unavailable():
    cmds = _Cmds(exists=False)
    with pytest.raises(NativeCommandUnavailable):
        NativeAuthoringCommandGateway(cmds).set_attrs([])
    assert [call[0] for call in cmds.calls] == ["exists"]


def test_registered_transport_failure_is_not_reported_as_unavailable():
    with pytest.raises(NativeCommandTransportError):
        NativeAuthoringCommandGateway(_Cmds(error=RuntimeError("maya failed"))).set_attrs([])


@pytest.mark.parametrize(
    "result",
    [
        "not-json",
        '{"version":2,"command":"mmdAuthoringSetAttrs","phase":"redo","ok":true}',
        '{"version":true,"command":"mmdAuthoringSetAttrs","phase":"redo","ok":true}',
        '{"version":1,"command":"wrong","phase":"redo","ok":true}',
        '{"version":1,"command":"mmdAuthoringSetAttrs","phase":"other","ok":true}',
        '{"version":1,"command":"mmdAuthoringSetAttrs","phase":"redo","ok":"yes"}',
        '{"version":1,"version":1,"command":"mmdAuthoringSetAttrs","phase":"redo","ok":true}',
    ],
)
def test_malformed_registered_result_is_protocol_error(result):
    with pytest.raises(NativeCommandProtocolError):
        NativeAuthoringCommandGateway(_Cmds(result)).set_attrs([])


def test_registered_domain_rejection_is_not_fallback_eligible():
    result = '{"version":1,"command":"mmdAuthoringSetAttrs","phase":"prepare","ok":false,"error":{"code":"plug_not_allowed","message":"bad"}}'
    with pytest.raises(NativeCommandDomainError) as caught:
        NativeAuthoringCommandGateway(_Cmds(result)).set_attrs([])
    assert caught.value.code == "plug_not_allowed"
    assert caught.value.phase == "prepare"


def test_gateway_rejects_non_allowlisted_commands_before_maya():
    cmds = _Cmds()
    with pytest.raises(ValueError):
        NativeAuthoringCommandGateway(cmds).execute("arbitraryCommand", {})
    assert cmds.calls == []
