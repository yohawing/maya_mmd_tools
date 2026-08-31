"""Strict gateway for the optional native VMD animation-curve clear command.

The Python side owns route and ownership decisions.  This adapter only loads
the canonical plug-in, transports a versioned list of already-resolved plugs,
and validates the command result.  It deliberately has no Maya undo or
fallback policy: callers can fall back only for a command that did not mutate
the scene during preparation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping


COMMAND_MMD_VMD_CLEAR_CURVES = "mmdVmdClearCurves"
PROTOCOL_VERSION = 1
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
_RESULT_FIELDS = frozenset(
    {
        "version",
        "command",
        "ok",
        "phase",
        "mutated",
        "plugs",
        "curve_count",
        "removed_count",
        "reason",
    }
)
_UNSUPPORTED_REASON_MARKERS = (
    "unsupported",
    "not supported",
    "unknown command",
)


class NativeVmdClearError(RuntimeError):
    """Base class for native VMD clear failures."""


class NativeVmdClearUnavailableError(NativeVmdClearError):
    """The command is not registered and no usable plug-in could be loaded."""


class NativeVmdClearPrepareError(NativeVmdClearError):
    """The command rejected the request before mutating Maya."""


class NativeVmdClearUnsupportedError(NativeVmdClearPrepareError):
    """The loaded binary does not support this clear command/request."""


class NativeVmdClearTransportError(NativeVmdClearError):
    """Maya failed while invoking the command."""


class NativeVmdClearProtocolError(NativeVmdClearError):
    """The command returned malformed or incompatible JSON."""


class NativeVmdClearMutationError(NativeVmdClearError):
    """The native mutation did not complete; Python fallback is unsafe."""


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _normalise_plugs(plugs: Iterable[str]) -> list[str]:
    if isinstance(plugs, (str, bytes)):
        raise ValueError("native VMD clear plugs must be an iterable of strings")
    try:
        values = list(plugs)
    except TypeError as exc:
        raise ValueError("native VMD clear plugs must be an iterable of strings") from exc
    if any(type(plug) is not str or not plug or "." not in plug for plug in values):
        raise ValueError("native VMD clear plugs must be non-empty canonical plug paths")
    if len(set(values)) != len(values):
        raise ValueError("native VMD clear plugs must be unique")
    return values


class NativeVmdClearAdapter:
    """Load and invoke one strict ``mmdVmdClearCurves`` batch."""

    command_name = COMMAND_MMD_VMD_CLEAR_CURVES

    def __init__(self, cmds_module: Any = None) -> None:
        if cmds_module is None:
            from maya import cmds as cmds_module

        self._cmds = cmds_module
        self._plugin_attempted = False
        self._plugin_path = None
        self.last_diagnostics: dict[str, Any] = {
            "available": callable(getattr(cmds_module, self.command_name, None)),
            "plugin_load_status": "not_attempted",
        }

    @property
    def available(self) -> bool:
        """Return whether the command is already registered or loadable."""
        if callable(getattr(self._cmds, self.command_name, None)):
            self.last_diagnostics.update(
                {"available": True, "plugin_load_status": "already_available"}
            )
            return True
        if not self._plugin_attempted:
            self._load_plugin_once()
        available = callable(getattr(self._cmds, self.command_name, None))
        self.last_diagnostics["available"] = available
        if not available and self.last_diagnostics.get("plugin_load_status") == "loaded":
            self.last_diagnostics["plugin_load_status"] = "registration_missing"
        return available

    def _load_plugin_once(self) -> None:
        """Locate/load the versioned plug-in through the shared locator."""
        self._plugin_attempted = True
        try:
            from mmd_tools.core import cpp_plugin_locator

            maya_version = cpp_plugin_locator.running_maya_major_version(
                self._cmds,
                default="2024",
            )
            candidates = cpp_plugin_locator.plugin_candidate_paths(
                [_PLUGIN_ROOT], maya_version=maya_version
            )
            path = cpp_plugin_locator.find_plugin_path(candidates)
            if path is None:
                self._plugin_path = str(candidates[0]) if candidates else None
                self.last_diagnostics.update(
                    {
                        "plugin_path": self._plugin_path,
                        "plugin_load_status": "missing",
                    }
                )
                return
            self._plugin_path = str(path)
            self.last_diagnostics["plugin_path"] = self._plugin_path
            cpp_plugin_locator.prepare_plugin_directory(path)
            loaded = cpp_plugin_locator.load_plugin(path, self._cmds, prepare=False)
            self.last_diagnostics["plugin_load_status"] = (
                "loaded" if loaded else "already_loaded"
            )
        except Exception as exc:
            self.last_diagnostics.update(
                {
                    "plugin_path": self._plugin_path,
                    "plugin_load_status": "error",
                    "plugin_load_error": f"{type(exc).__name__}: {exc}",
                }
            )

    @staticmethod
    def _parse_result(raw_result: Any, requested_plugs: list[str]) -> dict[str, Any]:
        if not isinstance(raw_result, str):
            raise NativeVmdClearProtocolError(
                "native VMD clear command returned non-string JSON"
            )
        try:
            result = json.loads(raw_result, object_pairs_hook=_strict_object)
        except (TypeError, ValueError) as exc:
            raise NativeVmdClearProtocolError(
                "native VMD clear command returned invalid JSON"
            ) from exc
        if not isinstance(result, dict) or set(result) != _RESULT_FIELDS:
            raise NativeVmdClearProtocolError(
                "native VMD clear command returned unexpected result fields"
            )
        if (
            type(result["version"]) is not int
            or result["version"] != PROTOCOL_VERSION
            or result["command"] != COMMAND_MMD_VMD_CLEAR_CURVES
            or type(result["ok"]) is not bool
            or type(result["mutated"]) is not bool
            or type(result["phase"]) is not str
            or not result["phase"]
            or type(result["reason"]) is not str
            or type(result["curve_count"]) is not int
            or result["curve_count"] < 0
            or type(result["removed_count"]) is not int
            or result["removed_count"] < 0
            or not isinstance(result["plugs"], list)
        ):
            raise NativeVmdClearProtocolError(
                "native VMD clear command returned malformed result values"
            )
        response_plugs = []
        response_removed_counts = []
        for item in result["plugs"]:
            if (
                not isinstance(item, dict)
                or set(item) != {"plug", "removed_count"}
                or type(item["plug"]) is not str
                or not item["plug"]
                or type(item["removed_count"]) is not int
                or item["removed_count"] < 0
            ):
                raise NativeVmdClearProtocolError(
                    "native VMD clear command returned malformed plug result"
                )
            response_plugs.append(item["plug"])
            response_removed_counts.append(item["removed_count"])
        if len(set(response_plugs)) != len(response_plugs):
            raise NativeVmdClearProtocolError(
                "native VMD clear command returned duplicate plug results"
            )
        if response_plugs != requested_plugs:
            raise NativeVmdClearProtocolError(
                "native VMD clear command returned a different plug set"
            )
        if result["ok"]:
            if (
                result["phase"] != "complete"
                or result["reason"]
                or sum(response_removed_counts) != result["removed_count"]
                or result["mutated"] != (result["removed_count"] > 0)
            ):
                raise NativeVmdClearProtocolError(
                    "native VMD clear command returned an inconsistent success result"
                )
        elif (
            not result["reason"]
            or (result["mutated"] and result["phase"] != "mutation")
            or (not result["mutated"] and result["phase"] != "prepare")
        ):
            raise NativeVmdClearProtocolError(
                "native VMD clear command returned an inconsistent failure result"
            )
        # Preparation failures prove that no mutation happened and therefore
        # carry an all-zero transparent result.  A mutation failure may report
        # the partial top-level counts, but its per-plug counts stay zero so a
        # caller cannot mistake the response for a completed clear.
        if not result["ok"] and not result["mutated"] and (
            any(response_removed_counts)
            or result["curve_count"] != 0
            or result["removed_count"] != 0
        ):
            raise NativeVmdClearProtocolError(
                "native VMD prepare failure must report zero removed results"
            )
        if not result["ok"] and result["mutated"] and any(response_removed_counts):
            raise NativeVmdClearProtocolError(
                "native VMD mutation failure must report zero per-plug results"
            )
        return result

    def clear(self, plugs: Iterable[str]) -> Mapping[str, Any]:
        """Clear one validated batch and return the validated result DTO.

        An unavailable/unsupported/prepare-only failure is represented by a
        typed exception so the owner can use its pre-mutation Python fallback.
        Transport, protocol, and mutation failures remain fatal to prevent a
        second destructive pass over a possibly partial mutation.
        """
        requested_plugs = _normalise_plugs(plugs)
        if not requested_plugs:
            return {
                "version": PROTOCOL_VERSION,
                "command": COMMAND_MMD_VMD_CLEAR_CURVES,
                "ok": True,
                "phase": "prepare",
                "mutated": False,
                "plugs": [],
                "curve_count": 0,
                "removed_count": 0,
                "reason": "empty_request",
            }
        if not self.available:
            raise NativeVmdClearUnavailableError(
                "native VMD clear command is unavailable"
            )
        payload = json.dumps(
            {"version": PROTOCOL_VERSION, "plugs": requested_plugs},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            raw_result = getattr(self._cmds, self.command_name)(payload=payload)
        except Exception as exc:
            raise NativeVmdClearTransportError(
                "native VMD clear command transport failed"
            ) from exc
        result = self._parse_result(raw_result, requested_plugs)
        if not result["ok"]:
            reason = result["reason"]
            if result["mutated"]:
                raise NativeVmdClearMutationError(
                    f"native VMD clear mutation failed: {reason}"
                )
            if result["phase"] == "prepare":
                if any(marker in reason.casefold() for marker in _UNSUPPORTED_REASON_MARKERS):
                    raise NativeVmdClearUnsupportedError(reason)
                raise NativeVmdClearPrepareError(reason)
            raise NativeVmdClearMutationError(
                f"native VMD clear failed during {result['phase']}: {reason}"
            )
        return result
