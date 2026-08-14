"""Pure Group/Flip morph topology calculation and inspection.

Raw PMX Group/Flip offsets are the authority.  The controller JSON is only a
derived runtime cache and must never be used to repair those offsets.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Tuple


TOPOLOGY_VERSION = 1
_EMPTY_TOPOLOGY = MappingProxyType({})


@dataclass(frozen=True)
class MorphTopologyDiagnostic:
    """One stable topology diagnostic suitable for UI and validation."""

    code: str
    detail: str


@dataclass(frozen=True)
class MorphTopologyInspection:
    """Comparison of authoritative offsets and the derived controller cache."""

    expected: Mapping[str, Tuple[Tuple[int, float], ...]]
    stored: Mapping[str, Tuple[Tuple[int, float], ...]]
    diagnostics: Tuple[MorphTopologyDiagnostic, ...]

    @property
    def valid(self) -> bool:
        return not self.diagnostics

    @property
    def repairable(self) -> bool:
        return bool(self.diagnostics) and all(
            item.code in {"version", "malformed", "stale"}
            for item in self.diagnostics
        )


class MorphTopologyError(ValueError):
    """Raised when topology cannot be consumed without losing diagnostics."""

    def __init__(self, diagnostic: MorphTopologyDiagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(f"{diagnostic.code}: {diagnostic.detail}")


def _morph_value(morph: Any, name: str) -> Any:
    if isinstance(morph, Mapping):
        if name == "morph_type":
            return morph.get(name, morph.get("type"))
        return morph.get(name)
    return getattr(morph, name, None)


def _strict_index(value: Any, detail: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MorphTopologyError(MorphTopologyDiagnostic("malformed", detail))
    return value


def _strict_rate(value: Any, detail: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MorphTopologyError(MorphTopologyDiagnostic("malformed", detail))
    rate = float(value)
    if not math.isfinite(rate):
        raise MorphTopologyError(MorphTopologyDiagnostic("malformed", detail))
    return rate


def compute_group_topology(
    morphs: Iterable[Any],
) -> Mapping[str, Tuple[Tuple[int, float], ...]]:
    """Compute flattened target-to-source rates from raw Group/Flip offsets."""

    rows = tuple(morphs)
    indices = set()
    expanding = {}
    for morph in rows:
        index = _strict_index(_morph_value(morph, "index"), "morph index is invalid")
        if index in indices:
            raise MorphTopologyError(
                MorphTopologyDiagnostic("malformed", f"duplicate morph index {index}")
            )
        indices.add(index)
        if _morph_value(morph, "morph_type") in {"group", "flip"}:
            expanding[index] = morph

    rates = {}

    def expand(source: int, current: int, rate: float, path: Tuple[int, ...]) -> None:
        offsets = _morph_value(expanding[current], "offsets")
        if isinstance(offsets, (str, bytes, bytearray)) or not isinstance(offsets, Iterable):
            raise MorphTopologyError(
                MorphTopologyDiagnostic("malformed", f"morph {current} offsets are invalid")
            )
        for offset in offsets:
            if not isinstance(offset, Mapping):
                raise MorphTopologyError(
                    MorphTopologyDiagnostic("malformed", f"morph {current} offset is not an object")
                )
            morph_type = _morph_value(expanding[current], "morph_type")
            rate_key = "morph_rate" if morph_type == "group" else "flip_rate"
            expected_fields = {"morph_index", rate_key}
            if set(offset) != expected_fields:
                raise MorphTopologyError(
                    MorphTopologyDiagnostic(
                        "malformed",
                        f"morph {current} offset fields must be {sorted(expected_fields)!r}",
                    )
                )
            target = _strict_index(
                offset.get("morph_index"), f"morph {current} target index is invalid"
            )
            if target not in indices:
                raise MorphTopologyError(
                    MorphTopologyDiagnostic("malformed", f"morph {current} targets missing morph {target}")
                )
            next_rate = rate * _strict_rate(
                offset[rate_key], f"morph {current} rate is invalid"
            )
            if target in path:
                cycle = "->".join(str(value) for value in path + (target,))
                raise MorphTopologyError(
                    MorphTopologyDiagnostic("cycle", f"Group/Flip cycle {cycle}")
                )
            sources = rates.setdefault(target, {})
            sources[source] = sources.get(source, 0.0) + next_rate
            if target in expanding:
                expand(source, target, next_rate, path + (target,))

    for index in sorted(expanding):
        expand(index, index, 1.0, (index,))
    return MappingProxyType(
        {
            str(target): tuple((source, rate) for source, rate in sorted(sources.items()))
            for target, sources in sorted(rates.items())
        }
    )


def parse_group_topology(
    version: Any, source: Any
) -> Mapping[str, Tuple[Tuple[int, float], ...]]:
    """Strictly parse one controller topology payload."""

    if isinstance(version, bool) or version != TOPOLOGY_VERSION:
        raise MorphTopologyError(
            MorphTopologyDiagnostic("version", f"expected {TOPOLOGY_VERSION}, got {version!r}")
        )
    if not isinstance(source, str):
        raise MorphTopologyError(
            MorphTopologyDiagnostic("malformed", "groupTopology must be a JSON string")
        )
    duplicate_keys = []

    def object_from_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                duplicate_keys.append(key)
            result[key] = value
        return result

    try:
        parsed = json.loads(source, object_pairs_hook=object_from_pairs)
    except (TypeError, ValueError) as exc:
        raise MorphTopologyError(
            MorphTopologyDiagnostic("malformed", f"invalid groupTopology JSON: {exc}")
        )
    if duplicate_keys:
        raise MorphTopologyError(
            MorphTopologyDiagnostic(
                "malformed", f"duplicate target key {duplicate_keys[0]!r}"
            )
        )
    if not isinstance(parsed, dict):
        raise MorphTopologyError(
            MorphTopologyDiagnostic("malformed", "groupTopology root must be an object")
        )
    result = {}
    for raw_target, raw_sources in parsed.items():
        try:
            target = int(raw_target)
        except (TypeError, ValueError):
            raise MorphTopologyError(
                MorphTopologyDiagnostic("malformed", f"target key {raw_target!r} is invalid")
            )
        if target < 0 or str(target) != str(raw_target):
            raise MorphTopologyError(
                MorphTopologyDiagnostic("malformed", f"target key {raw_target!r} is not canonical")
            )
        if not isinstance(raw_sources, list):
            raise MorphTopologyError(
                MorphTopologyDiagnostic("malformed", f"target {target} sources must be an array")
            )
        sources = []
        seen = set()
        for row in raw_sources:
            if not isinstance(row, list) or len(row) != 2:
                raise MorphTopologyError(
                    MorphTopologyDiagnostic("malformed", f"target {target} source row is invalid")
                )
            source_index = _strict_index(row[0], f"target {target} source index is invalid")
            if source_index in seen:
                raise MorphTopologyError(
                    MorphTopologyDiagnostic("malformed", f"target {target} has duplicate source {source_index}")
                )
            seen.add(source_index)
            sources.append((source_index, _strict_rate(row[1], f"target {target} rate is invalid")))
        if sources != sorted(sources):
            raise MorphTopologyError(
                MorphTopologyDiagnostic("malformed", f"target {target} sources are not canonical")
            )
        result[str(target)] = tuple(sources)
    if list(result) != sorted(result, key=int):
        raise MorphTopologyError(
            MorphTopologyDiagnostic("malformed", "target keys are not canonical")
        )
    return MappingProxyType(result)


def parse_raw_offsets_json(source: Any) -> list:
    """Parse authoritative Group/Flip offsets and reject duplicate fields."""
    if not isinstance(source, str):
        raise MorphTopologyError(
            MorphTopologyDiagnostic("malformed", "raw offsets must be a JSON string")
        )
    duplicate_keys = []

    def object_from_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                duplicate_keys.append(key)
            result[key] = value
        return result

    try:
        parsed = json.loads(source, object_pairs_hook=object_from_pairs)
    except (TypeError, ValueError) as exc:
        raise MorphTopologyError(
            MorphTopologyDiagnostic("malformed", f"invalid raw offsets JSON: {exc}")
        )
    if duplicate_keys:
        raise MorphTopologyError(
            MorphTopologyDiagnostic(
                "malformed", f"duplicate raw offset field {duplicate_keys[0]!r}"
            )
        )
    if not isinstance(parsed, list) or any(not isinstance(item, Mapping) for item in parsed):
        raise MorphTopologyError(
            MorphTopologyDiagnostic("malformed", "raw offsets root must be an array of objects")
        )
    return parsed


def serialize_group_topology(topology: Mapping[str, Iterable[Tuple[int, float]]]) -> str:
    """Serialize canonical topology for the Maya string attribute."""

    payload = {key: [[source, rate] for source, rate in values] for key, values in topology.items()}
    return json.dumps(payload, separators=(",", ":"))


def inspect_group_topology(
    morphs: Iterable[Any], version: Any, source: Any
) -> MorphTopologyInspection:
    """Compare stored topology with raw offsets without mutating either."""

    try:
        expected = compute_group_topology(morphs)
    except MorphTopologyError as exc:
        return MorphTopologyInspection(_EMPTY_TOPOLOGY, _EMPTY_TOPOLOGY, (exc.diagnostic,))
    try:
        stored = parse_group_topology(version, source)
    except MorphTopologyError as exc:
        return MorphTopologyInspection(expected, _EMPTY_TOPOLOGY, (exc.diagnostic,))
    diagnostics = ()
    if stored != expected:
        diagnostics = (
            MorphTopologyDiagnostic("stale", "controller topology does not match raw Group/Flip offsets"),
        )
    return MorphTopologyInspection(expected, stored, diagnostics)


__all__ = [
    "TOPOLOGY_VERSION",
    "MorphTopologyDiagnostic",
    "MorphTopologyError",
    "MorphTopologyInspection",
    "compute_group_topology",
    "inspect_group_topology",
    "parse_group_topology",
    "parse_raw_offsets_json",
    "serialize_group_topology",
]
