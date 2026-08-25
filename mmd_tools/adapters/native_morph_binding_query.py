"""Strict gateway for the optional native morph-binding observation query."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from mmd_tools.core.morph_binding_resolver import MorphDestinationObservation


COMMAND_QUERY_MORPH_BINDINGS = "mmdAuthoringQueryMorphBindings"
_PROTOCOL_VERSION = 1


class NativeMorphBindingQueryError(RuntimeError):
    """A registered native query failed or returned an incompatible DTO."""


@dataclass(frozen=True)
class NativeMorphBindingObservations:
    """Maya observations consumed by the pure Python binding policy."""

    destinations: Tuple[MorphDestinationObservation, ...]
    aliases: Mapping[str, Tuple[Tuple[str, str], ...]]
    raw_mappings: Mapping[str, Mapping[object, object]]


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: {}".format(key))
        result[key] = value
    return result


class NativeMorphBindingQueryGateway:
    """Use the fixed native query when registered; never mask its failures."""

    def __init__(self, cmds_adapter: Any) -> None:
        self._cmds = cmds_adapter

    def query_if_available(
        self, controller: str, slot: int
    ) -> Optional[NativeMorphBindingObservations]:
        if not self._cmds.command_exists(COMMAND_QUERY_MORPH_BINDINGS):
            return None
        request = {"version": _PROTOCOL_VERSION, "controller": controller, "slot": slot}
        try:
            raw_result = self._cmds.invoke_native_command(
                COMMAND_QUERY_MORPH_BINDINGS,
                payload=json.dumps(request, ensure_ascii=False, separators=(",", ":")),
            )
        except Exception as exc:
            raise NativeMorphBindingQueryError("native morph binding query transport failed") from exc
        try:
            result = json.loads(raw_result, object_pairs_hook=_strict_object)
            return self._parse_result(result, controller, slot)
        except NativeMorphBindingQueryError:
            raise
        except (TypeError, ValueError, KeyError) as exc:
            raise NativeMorphBindingQueryError("native morph binding query returned invalid JSON") from exc

    @staticmethod
    def _parse_result(
        result: object,
        expected_controller: str,
        expected_slot: int,
    ) -> NativeMorphBindingObservations:
        if (
            not isinstance(result, dict)
            or type(result.get("version")) is not int
            or result.get("version") != _PROTOCOL_VERSION
            or result.get("command") != COMMAND_QUERY_MORPH_BINDINGS
            or not isinstance(result.get("ok"), bool)
        ):
            raise NativeMorphBindingQueryError("native morph binding query returned an incompatible result")
        if not result["ok"]:
            error = result.get("error")
            if (
                set(result) != {"version", "command", "ok", "error"}
                or not isinstance(error, dict)
                or set(error) != {"code", "message"}
                or not isinstance(error.get("code"), str)
                or not error["code"]
                or not isinstance(error.get("message"), str)
                or not error["message"]
            ):
                raise NativeMorphBindingQueryError("native morph binding query returned an invalid error")
            raise NativeMorphBindingQueryError("{}: {}".format(error["code"], error["message"]))
        if set(result) != {
            "version",
            "command",
            "ok",
            "requestedController",
            "controller",
            "slot",
            "destinations",
            "blendShapes",
        }:
            raise NativeMorphBindingQueryError("native morph binding query returned unexpected result fields")
        if (
            result.get("requestedController") != expected_controller
            or not isinstance(result.get("controller"), str)
            or not result["controller"]
            or type(result.get("slot")) is not int
            or result["slot"] != expected_slot
        ):
            raise NativeMorphBindingQueryError("native morph binding query response identity does not match request")
        if not isinstance(result.get("destinations"), list) or not isinstance(result.get("blendShapes"), list):
            raise NativeMorphBindingQueryError("native morph binding query omitted observation arrays")

        destinations = []
        for item in result["destinations"]:
            if not isinstance(item, dict) or set(item) != {"node", "nodeType", "plug"} or not all(isinstance(item.get(key), str) and item[key] for key in ("node", "nodeType", "plug")):
                raise NativeMorphBindingQueryError("native destination observation is malformed")
            destinations.append(MorphDestinationObservation(item["node"], item["nodeType"], item["plug"]))

        aliases: Dict[str, Tuple[Tuple[str, str], ...]] = {}
        raw_mappings: Dict[str, Mapping[object, object]] = {}
        for item in result["blendShapes"]:
            if not isinstance(item, dict) or set(item) != {"node", "aliases", "rawNameMappingJson"} or not isinstance(item.get("node"), str) or not item["node"] or not isinstance(item.get("aliases"), list):
                raise NativeMorphBindingQueryError("native blendShape observation is malformed")
            node = item["node"]
            if node in aliases:
                raise NativeMorphBindingQueryError("native blendShape observation is duplicated")
            pairs = []
            for pair in item["aliases"]:
                if not isinstance(pair, dict) or set(pair) != {"alias", "plug"} or not isinstance(pair.get("alias"), str) or not pair["alias"] or not isinstance(pair.get("plug"), str) or not pair["plug"]:
                    raise NativeMorphBindingQueryError("native alias observation is malformed")
                pairs.append((pair["alias"], pair["plug"]))
            aliases[node] = tuple(pairs)
            raw = item["rawNameMappingJson"]
            if raw is not None:
                if not isinstance(raw, str):
                    raise NativeMorphBindingQueryError("native raw-name mapping is not JSON text")
                try:
                    mapping = json.loads(raw, object_pairs_hook=_strict_object)
                except (TypeError, ValueError) as exc:
                    raise NativeMorphBindingQueryError("native raw-name mapping is invalid JSON") from exc
                if not isinstance(mapping, dict):
                    raise NativeMorphBindingQueryError("native raw-name mapping is not an object")
                raw_mappings[node] = mapping
        return NativeMorphBindingObservations(tuple(destinations), aliases, raw_mappings)
