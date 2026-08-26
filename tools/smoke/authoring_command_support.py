"""Shared support for the native authoring-command Maya smokes.

The active material value and outline smokes need the same narrow observer and
bounded timing loop.  Keep that support independent from the retired
cross-version decision probe so the smoke scripts remain usable on their own.
"""

from __future__ import annotations

from collections import Counter
import math
import statistics
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence


COLD_ITERATIONS = 3

TRACKED_MAYA_COMMANDS = (
    "objExists",
    "referenceQuery",
    "ls",
    "attributeQuery",
    "addAttr",
    "deleteAttr",
    "getAttr",
    "setAttr",
    "undoInfo",
    "undo",
    "redo",
    "nodeType",
    "listConnections",
    "listRelatives",
    "listAttr",
    "aliasAttr",
    "listHistory",
    "polyEvaluate",
    "connectAttr",
    "disconnectAttr",
    "sets",
)

GRAPH_DISCOVERY_COMMANDS = frozenset(
    ("ls", "listConnections", "listRelatives", "listAttr", "aliasAttr", "listHistory")
)


def distribution(samples_ns: Sequence[int]) -> Dict[str, Any]:
    """Return nearest-rank timing statistics suitable for smoke evidence."""

    ordered = sorted(int(value) for value in samples_ns)
    if not ordered:
        return {"count": 0, "status": "not_observed"}

    def percentile(fraction: float) -> int:
        index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
        return ordered[index]

    return {
        "count": len(ordered),
        "min_ns": ordered[0],
        "p50_ns": percentile(0.50),
        "p95_ns": percentile(0.95),
        "max_ns": ordered[-1],
        "mean_ns": round(statistics.mean(ordered), 2),
        "status": "measured",
    }


def count_distribution(samples: Sequence[int]) -> Dict[str, Any]:
    """Return the smoke distribution with compact count-valued keys."""

    timed = distribution(samples)
    for old, new in (
        ("min_ns", "min"),
        ("p50_ns", "p50"),
        ("p95_ns", "p95"),
        ("max_ns", "max"),
        ("mean_ns", "mean"),
    ):
        if old in timed:
            timed[new] = timed.pop(old)
    return timed


def _command_targets(
    name: str, args: Sequence[Any], kwargs: Mapping[str, Any]
) -> tuple[set[str], set[str]]:
    """Extract explicit node and plug targets without issuing Maya queries."""

    nodes: set[str] = set()
    plugs: set[str] = set()

    def add(value: Any) -> None:
        if not isinstance(value, str) or not value or value.startswith(("{", "[")):
            return
        if "." in value:
            plugs.add(value)
            nodes.add(value.split(".", 1)[0])
        else:
            nodes.add(value)

    if name == "attributeQuery":
        if args and isinstance(args[0], str):
            node = kwargs.get("node")
            if isinstance(node, str):
                nodes.add(node)
                plugs.add(f"{node}.{args[0]}")
        return nodes, plugs
    indices = {
        "objExists": (0,),
        "referenceQuery": (0,),
        "ls": tuple(range(len(args))),
        "addAttr": (0,),
        "deleteAttr": (0,),
        "getAttr": (0,),
        "setAttr": (0,),
        "nodeType": (0,),
        "listConnections": (0,),
        "listRelatives": (0,),
        "listAttr": (0,),
        "aliasAttr": (0,),
        "listHistory": (0,),
        "polyEvaluate": (0,),
        "connectAttr": (0, 1),
        "disconnectAttr": (0, 1),
        "sets": (0,),
    }.get(name, ())
    for index in indices:
        if index >= len(args):
            continue
        value = args[index]
        if isinstance(value, (list, tuple, set)):
            for item in value:
                add(item)
        else:
            add(value)
    if name in {"addAttr", "deleteAttr"}:
        attr = kwargs.get(
            "longName",
            kwargs.get("ln", kwargs.get("attribute", kwargs.get("at"))),
        )
        if isinstance(attr, str):
            plugs.update(f"{node}.{attr}" for node in nodes if "." not in node)
    return nodes, plugs


class MayaCommandRecorder:
    """Temporarily observe calls crossing from Python into ``maya.cmds``."""

    def __init__(self, cmds: Any) -> None:
        self.cmds = cmds
        self.active = False
        self.events: List[Dict[str, Any]] = []
        self.restorers: List[Callable[[], None]] = []

    def install(self) -> None:
        for name in TRACKED_MAYA_COMMANDS:
            original = getattr(self.cmds, name, None)
            if not callable(original):
                continue

            def observed(*args: Any, _name: str = name, _original=original, **kwargs: Any) -> Any:
                if self.active:
                    nodes, plugs = _command_targets(_name, args, kwargs)
                    self.events.append(
                        {"method": _name, "nodes": sorted(nodes), "plugs": sorted(plugs)}
                    )
                return _original(*args, **kwargs)

            setattr(self.cmds, name, observed)
            self.restorers.append(lambda key=name, value=original: setattr(self.cmds, key, value))

    def begin(self) -> None:
        if self.active:
            raise RuntimeError("Maya command recorder is already active")
        self.events = []
        self.active = True

    def end(self) -> Dict[str, Any]:
        if not self.active:
            raise RuntimeError("Maya command recorder is not active")
        self.active = False
        methods = Counter(event["method"] for event in self.events)
        nodes = sorted({node for event in self.events for node in event["nodes"]})
        plugs = sorted({plug for event in self.events for plug in event["plugs"]})
        write_methods = {"setAttr", "addAttr", "deleteAttr", "connectAttr", "disconnectAttr"}
        write_plugs = sorted(
            {
                plug
                for event in self.events
                if event["method"] in write_methods
                for plug in event["plugs"]
            }
        )
        graph = sum(methods[name] for name in GRAPH_DISCOVERY_COMMANDS)
        return {
            "maya_call_count": len(self.events),
            "maya_calls_by_method": dict(sorted(methods.items())),
            "graph_discovery_call_count": graph,
            "target_node_count": len(nodes),
            "target_plug_count": len(plugs),
            "target_nodes": nodes,
            "target_plugs": plugs,
            "transaction_plug_count": len(plugs),
            "write_plug_count": len(write_plugs),
            "write_plugs": write_plugs,
        }

    def restore(self) -> None:
        self.active = False
        for restore in reversed(self.restorers):
            restore()
        self.restorers = []


def measure_case(
    *,
    name: str,
    recorder: MayaCommandRecorder,
    action: Callable[[int], None],
    verify_target: Callable[[int], None],
    verify_undo_redo: Callable[[int], None],
    iterations: int,
    semantic_field_count: int,
    prepare_cold: Callable[[], None],
) -> Dict[str, Any]:
    """Measure one cold call and a warm distribution with parity checks."""

    rows: List[Dict[str, Any]] = []
    for index in range(COLD_ITERATIONS + iterations):
        is_cold = index < COLD_ITERATIONS
        if is_cold:
            prepare_cold()
        timed_index = index * 2
        characterization_index = timed_index + 1
        started = time.perf_counter_ns()
        error: Optional[str] = None
        try:
            # Timing is deliberately free of recorder bookkeeping.  The
            # equivalent opposite-phase action below characterizes calls.
            action(timed_index)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        elapsed = time.perf_counter_ns() - started
        if error is None:
            try:
                verify_target(timed_index)
                verify_undo_redo(timed_index)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
        recorder.install()
        recorder.begin()
        try:
            if error is None:
                try:
                    action(characterization_index)
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
            calls = recorder.end()
        finally:
            recorder.restore()
        if error is None:
            try:
                verify_target(characterization_index)
                verify_undo_redo(characterization_index)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
        rows.append(
            {
                "index": index,
                "timed_action_index": timed_index,
                "call_characterization_index": characterization_index,
                "temperature": "cold" if is_cold else "warm",
                "elapsed_ns": elapsed,
                "status": "pass" if error is None else "failed",
                "error": error,
                **calls,
            }
        )
    cold = rows[:COLD_ITERATIONS]
    warm = rows[COLD_ITERATIONS:]
    failures = [row for row in rows if row["status"] != "pass"]
    return {
        "name": name,
        "status": "pass" if not failures else "failed",
        "cold_iterations": COLD_ITERATIONS,
        "warm_iterations": iterations,
        "semantic_field_count": semantic_field_count,
        "cold_timing_ns": distribution([row["elapsed_ns"] for row in cold]),
        "cold_definition": "first action through a freshly built production authoring composition",
        "warm_timing_ns": distribution([row["elapsed_ns"] for row in warm]),
        "warm_maya_calls": count_distribution([row["maya_call_count"] for row in warm]),
        "observed_target_node_count": max(row["target_node_count"] for row in rows),
        "observed_target_plug_count": max(row["target_plug_count"] for row in rows),
        "observed_transaction_plug_count": max(row["transaction_plug_count"] for row in rows),
        "observed_write_plug_count": max(row["write_plug_count"] for row in rows),
        "undo_boundary": "one_action_one_undo_redo",
        "semantic_parity": "exact_preimage_and_target",
        "failures": len(failures),
        "samples": rows,
    }
