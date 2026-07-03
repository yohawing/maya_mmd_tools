"""Utilities for diagnosing Maya animation-layer DG keying graphs."""

from __future__ import annotations

import json
import re
from typing import Any, Iterable


_NODE_NUMBER_RE = re.compile(r"(?P<prefix>[A-Za-z_][A-Za-z0-9_:|]*?)(?P<number>\d+)(?=\.|$)")
_HARNESS_ROUTE_RE = re.compile(r"(?:(?<=_)|^)(setkeyframe|api)(?=_compare_layer|_)")


def normalize_node_numbers(value: str) -> str:
    """Replace Maya-generated numeric node suffixes with a stable marker."""
    normalized = _NODE_NUMBER_RE.sub(lambda match: f"{match.group('prefix')}#", str(value))
    return _HARNESS_ROUTE_RE.sub("route", normalized)


def normalize_graph(value: Any) -> Any:
    """Return a copy of a graph dump with volatile Maya node numbers normalized."""
    if isinstance(value, dict):
        normalized = {normalize_node_numbers(str(key)): normalize_graph(inner) for key, inner in value.items()}
        return {key: normalized[key] for key in sorted(normalized)}
    if isinstance(value, list):
        normalized = [normalize_graph(item) for item in value]
        if all(isinstance(item, dict) and {"source", "destination"} <= set(item) for item in normalized):
            return sorted(
                normalized,
                key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
            )
        return normalized
    if isinstance(value, tuple):
        return tuple(normalize_graph(item) for item in value)
    if isinstance(value, str):
        return normalize_node_numbers(value)
    if isinstance(value, float):
        return round(value, 12)
    return value


def _maya_cmds():
    import maya.cmds as cmds

    return cmds


def _node_from_plug(plug: str) -> str:
    return plug.split(".", 1)[0]


def _as_scalar(value: Any) -> float:
    while isinstance(value, (list, tuple)) and value:
        value = value[0]
    return float(value)


def dump_plug_graph(plug: str, *, max_depth: int = 12) -> dict[str, Any]:
    """Dump upstream DG nodes and edges feeding *plug*.

    The dump is intentionally compact and JSON-serializable so mayapy harnesses
    can store it as a diagnostic artifact when animation-layer evaluation drifts.
    """
    cmds = _maya_cmds()
    seen_plugs = {plug}
    pending: list[tuple[str, int]] = [(plug, 0)]
    nodes: dict[str, str] = {}
    edges: list[dict[str, str]] = []

    while pending:
        current_plug, depth = pending.pop(0)
        node = _node_from_plug(current_plug)
        if cmds.objExists(node):
            nodes[node] = cmds.nodeType(node)
        if depth >= max_depth:
            continue
        source_plugs = cmds.listConnections(
            current_plug,
            source=True,
            destination=False,
            plugs=True,
        ) or []
        for source_plug in sorted(set(source_plugs)):
            source_node = _node_from_plug(source_plug)
            if cmds.objExists(source_node):
                nodes[source_node] = cmds.nodeType(source_node)
            edges.append({"source": source_plug, "destination": current_plug})
            if source_plug not in seen_plugs:
                seen_plugs.add(source_plug)
                pending.append((source_plug, depth + 1))
            if not cmds.objExists(source_node):
                continue
            node_inputs = cmds.listConnections(
                source_node,
                source=True,
                destination=False,
                plugs=True,
            ) or []
            for input_plug in sorted(set(node_inputs)):
                input_node = _node_from_plug(input_plug)
                if cmds.objExists(input_node):
                    nodes[input_node] = cmds.nodeType(input_node)
                edges.append({"source": input_plug, "destination": source_node})
                if input_plug not in seen_plugs:
                    seen_plugs.add(input_plug)
                    pending.append((input_plug, depth + 1))

    anim_curves: dict[str, dict[str, Any]] = {}
    for node, node_type in sorted(nodes.items()):
        if not node_type.startswith("animCurve"):
            continue
        anim_curves[node] = {
            "type": node_type,
            "times": [float(item) for item in (cmds.keyframe(node, query=True, timeChange=True) or [])],
            "values": [float(item) for item in (cmds.keyframe(node, query=True, valueChange=True) or [])],
        }

    return {
        "plug": plug,
        "nodes": dict(sorted(nodes.items())),
        "edges": sorted(edges, key=lambda item: (item["destination"], item["source"])),
        "animCurves": anim_curves,
    }


def eval_plugs_at_frames(plugs: Iterable[str], frames: Iterable[float]) -> dict[str, dict[str, float]]:
    """Evaluate target plugs at frames and return JSON-safe scalar values."""
    cmds = _maya_cmds()
    result: dict[str, dict[str, float]] = {}
    for plug in plugs:
        values: dict[str, float] = {}
        for frame in frames:
            values[str(float(frame))] = _as_scalar(cmds.getAttr(plug, time=float(frame)))
        result[plug] = values
    return result


def diff_evaluations(
    expected: dict[str, dict[str, float]],
    actual: dict[str, dict[str, float]],
    *,
    tolerance: float = 1.0e-5,
) -> dict[str, Any]:
    """Compare two evaluation maps produced by :func:`eval_plugs_at_frames`."""
    mismatches: list[dict[str, Any]] = []
    max_abs_delta = 0.0
    for plug in sorted(set(expected) | set(actual)):
        expected_frames = expected.get(plug, {})
        actual_frames = actual.get(plug, {})
        for frame in sorted(set(expected_frames) | set(actual_frames), key=float):
            if frame not in expected_frames or frame not in actual_frames:
                mismatches.append({"plug": plug, "frame": frame, "reason": "missing"})
                continue
            left = float(expected_frames[frame])
            right = float(actual_frames[frame])
            delta = abs(left - right)
            max_abs_delta = max(max_abs_delta, delta)
            if delta > tolerance:
                mismatches.append(
                    {
                        "plug": plug,
                        "frame": frame,
                        "expected": left,
                        "actual": right,
                        "absDelta": delta,
                    }
                )
    return {
        "matches": not mismatches,
        "maxAbsDelta": max_abs_delta,
        "mismatches": mismatches,
    }
