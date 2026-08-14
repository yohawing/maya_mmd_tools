"""Strict Python gateway for narrow native Authoring commands."""

from __future__ import annotations

import json
import math
from typing import Any, Dict, Iterable, Mapping


COMMAND_SET_ATTRS = "mmdAuthoringSetAttrs"
COMMAND_SET_MORPH_WEIGHTS = "mmdAuthoringSetMorphWeights"
COMMAND_SET_MATERIAL_VALUES = "mmdAuthoringSetMaterialValues"
COMMAND_SET_MATERIAL_OUTLINE = "mmdAuthoringSetMaterialOutline"
_ALLOWED_COMMANDS = frozenset(
    (
        COMMAND_SET_ATTRS,
        COMMAND_SET_MORPH_WEIGHTS,
        COMMAND_SET_MATERIAL_VALUES,
        COMMAND_SET_MATERIAL_OUTLINE,
    )
)
_PROTOCOL_VERSION = 1


class NativeAuthoringCommandError(RuntimeError):
    """Base class for native Authoring command failures."""


class NativeCommandUnavailable(NativeAuthoringCommandError):
    """The requested native command is not registered in Maya."""


class NativeCommandTransportError(NativeAuthoringCommandError):
    """Maya failed before a protocol result could be returned."""


class NativeCommandProtocolError(NativeAuthoringCommandError):
    """A registered command returned malformed or incompatible JSON."""


class NativeCommandDomainError(NativeAuthoringCommandError):
    """A registered command rejected a validated domain operation."""

    def __init__(self, code: str, message: str, phase: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.phase = phase


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _finite_number(value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _material_readback_matches(expected: Any, actual: Any) -> bool:
    """Compare native canonical storage without accepting malformed JSON values."""
    if type(expected) in (str, bool, int):
        return type(actual) is type(expected) and actual == expected
    if type(expected) is float:
        return _finite_number(actual) and math.isclose(
            float(actual), expected, rel_tol=1.0e-6, abs_tol=1.0e-7
        )
    if isinstance(expected, (list, tuple)):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(
                _material_readback_matches(expected_item, actual_item)
                for expected_item, actual_item in zip(expected, actual)
            )
        )
    return False


class NativeAuthoringCommandGateway:
    """Invoke only explicitly allowlisted native commands with JSON payloads."""

    def __init__(self, cmds_adapter: Any) -> None:
        self._cmds = cmds_adapter

    def execute(self, command: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        if command not in _ALLOWED_COMMANDS:
            raise ValueError(f"Native Authoring command is not allowlisted: {command}")
        command_exists = getattr(self._cmds, "command_exists", None)
        if not callable(command_exists) or not command_exists(command):
            raise NativeCommandUnavailable(f"Native Authoring command is unavailable: {command}")
        request = dict(payload)
        request["version"] = _PROTOCOL_VERSION
        try:
            raw_result = self._cmds.invoke_native_command(
                command,
                payload=json.dumps(request, ensure_ascii=False, separators=(",", ":")),
            )
        except Exception as error:
            raise NativeCommandTransportError(f"Native command transport failed: {command}") from error
        try:
            result = json.loads(raw_result, object_pairs_hook=_strict_object)
        except (TypeError, ValueError) as error:
            raise NativeCommandProtocolError(f"Native command returned invalid JSON: {command}") from error
        if (
            not isinstance(result, dict)
            or type(result.get("version")) is not int
            or result["version"] != _PROTOCOL_VERSION
            or result.get("command") != command
            or result.get("phase") not in ("prepare", "redo", "undo")
            or not isinstance(result.get("ok"), bool)
        ):
            raise NativeCommandProtocolError(f"Native command returned an incompatible result: {command}")
        if not result["ok"]:
            error = result.get("error")
            if not isinstance(error, dict) or not isinstance(error.get("code"), str) or not isinstance(error.get("message"), str):
                raise NativeCommandProtocolError(f"Native command returned an invalid error: {command}")
            raise NativeCommandDomainError(error["code"], error["message"], result["phase"])
        return result

    def set_attrs(self, updates: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
        """Run the fixed Authoring witness write set as one Maya Undo item."""
        return self.execute(COMMAND_SET_ATTRS, {"updates": [dict(update) for update in updates]})

    def set_morph_weights(self, updates: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
        """Write a fixed, discovered morph target set as one native command."""
        request_updates = [dict(update) for update in updates]
        result = self.execute(
            COMMAND_SET_MORPH_WEIGHTS,
            {"updates": request_updates},
        )
        plugs = result.get("plugs")
        values = result.get("values")
        if (
            not isinstance(plugs, list)
            or not plugs
            or not all(isinstance(plug, str) and plug for plug in plugs)
            or len(set(plugs)) != len(plugs)
            or not isinstance(values, list)
            or len(values) != len(plugs)
            or plugs != [update.get("plug") for update in request_updates]
            or not all(_finite_number(value) for value in values)
        ):
            raise NativeCommandProtocolError(
                "Native morph command returned invalid canonical values"
            )
        return result

    def set_material_values(
        self,
        root: str,
        shader: str,
        material_index: int,
        updates: Iterable[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        """Write one Python-expanded semantic Material value patch."""
        request_updates = [dict(update) for update in updates]
        result = self.execute(
            COMMAND_SET_MATERIAL_VALUES,
            {
                "root": root,
                "shader": shader,
                "material_index": material_index,
                "updates": request_updates,
            },
        )
        fields = result.get("fields")
        plugs = result.get("plugs")
        values = result.get("values")
        expected_fields = [update.get("field") for update in request_updates]
        if (
            not isinstance(fields, list)
            or fields != expected_fields
            or len(set(fields)) != len(fields)
            or not isinstance(plugs, list)
            or len(plugs) != len(fields)
            or len(set(plugs)) != len(plugs)
            or not all(isinstance(plug, str) and plug for plug in plugs)
            or not isinstance(values, list)
            or len(values) != len(fields)
            or not all(
                _material_readback_matches(update.get("value"), value)
                for update, value in zip(request_updates, values)
            )
        ):
            raise NativeCommandProtocolError(
                "Native material value command returned invalid canonical values"
            )
        return result

    def set_material_outline(
        self,
        root: str,
        shader: str,
        material_index: int,
        updates: Iterable[Mapping[str, Any]],
        outline_preimage: Mapping[str, Any],
        outline_target: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """Apply one Python-expanded Material value plus DX11 outline patch."""
        request_updates = [dict(update) for update in updates]
        result = self.execute(
            COMMAND_SET_MATERIAL_OUTLINE,
            {
                "root": root,
                "shader": shader,
                "material_index": material_index,
                "updates": request_updates,
                "outline_preimage": dict(outline_preimage),
                "outline_target": dict(outline_target),
            },
        )
        fields = result.get("fields")
        plugs = result.get("plugs")
        values = result.get("values")
        expected_fields = [update.get("field") for update in request_updates] + list(
            outline_target
        )
        expected_values = [update.get("value") for update in request_updates] + list(
            outline_target.values()
        )
        if (
            not isinstance(fields, list)
            or fields != expected_fields
            or len(set(fields)) != len(fields)
            or not isinstance(plugs, list)
            or len(plugs) != len(fields)
            or len(set(plugs)) != len(plugs)
            or not all(isinstance(plug, str) and plug for plug in plugs)
            or not isinstance(values, list)
            or len(values) != len(fields)
            or not all(
                _material_readback_matches(expected, actual)
                for expected, actual in zip(expected_values, values)
            )
        ):
            raise NativeCommandProtocolError(
                "Native material outline command returned invalid canonical values"
            )
        return result
