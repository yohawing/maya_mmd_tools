"""Capture the Model Authoring narrow-action performance contract in Maya.

The host launches a clean Maya GUI through the existing commandPort harness.
Inside Maya, production presenters operate on the largest deterministic
authoring fixture in ``tests/data`` while this tool temporarily records the
coordinator and :class:`MayaCmdsAdapter` calls.  Product code is not
instrumented and the report does not claim a speedup; it is a before-refactor
baseline plus a fail-closed read/write scope contract.  The dedicated scaling
gate expands only Bone and Material bindings around the fixed Morph seed.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import sys
import tempfile
import time
import traceback
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = PROJECT_ROOT / "tests" / "data" / "yw_test_model_control_rig_bone_morph.pmx"
DEFAULT_OUT_DIR = PROJECT_ROOT / "build" / "reports" / "authoring_performance_contract"
DEFAULT_TEXTURE = PROJECT_ROOT / "tests" / "data" / "tex" / "diffuse.png"
COMMAND_PORT = 7765
COMPLETION_MARKER = "//-- AUTHORING_PERFORMANCE_CONTRACT_DONE --//"
SCHEMA_VERSION = 2

# The scaling gate uses a separate, deliberately expanded scene envelope.  The
# imported PMX remains the deterministic geometry/morph seed, while the Maya
# probe adds owned Bone and Material bindings between cases.  This keeps the
# Morph population fixed and makes growth in unrelated aggregates observable.
SCALING_CASES = (
    {"name": "base", "bone_multiplier": 1, "material_multiplier": 1},
    {"name": "bone_2x", "bone_multiplier": 2, "material_multiplier": 1},
    {
        "name": "bone_2x_material_2x",
        "bone_multiplier": 2,
        "material_multiplier": 2,
    },
    {
        "name": "bone_4x_material_4x",
        "bone_multiplier": 4,
        "material_multiplier": 4,
    },
)
SCALING_OPERATIONS = ("snapshot", "refresh")
# This is intentionally an absolute, generous wall-time ceiling.  The gate
# checks structural regressions with call counts; it does not compare one cold
# case against another cold case.
SCALING_P95_ABSOLUTE_LIMIT_MS = {"snapshot": 1_000.0, "refresh": 1_000.0}
SCALING_MEASUREMENT_ORDER = tuple(case["name"] for case in reversed(SCALING_CASES))
SCALING_MIN_VERTICES = 8_000
SCALING_MIN_BONES = 100

NARROW_ACTIONS = frozenset(
    {
        "material_value_apply",
        "material_texture_apply",
        "morph_name_apply",
        "morph_slider_drag",
        "display_apply",
        "info_focus_edit",
    }
)

# Fixed after two Maya 2024 runs (7 measured samples each).  Budgets round up
# to roughly twice the noisier observed p95, so they catch order-of-magnitude
# regressions without pretending commandPort/Qt wall time is deterministic.
P95_BUDGET_MS = {
    "material_value_apply": 1_500.0,
    "material_texture_apply": 500.0,
    "morph_name_apply": 6_000.0,
    "morph_slider_drag": 1_500.0,
    "display_apply": 250.0,
    "info_focus_edit": 100.0,
    "refresh_visible_material_tab": 250.0,
}

MAX_ADAPTER_CALLS = {
    "material_value_apply": 275,
    "material_texture_apply": 510,
    "morph_name_apply": 145,
    "morph_slider_drag": 1,
    # Atomic Display apply now performs begin/preimage, mutation, exact
    # read-back, and Undo-chunk verification. Maya 2024 is stable at 14 calls;
    # retain only two calls of headroom so graph scans remain visible.
    "display_apply": 16,
    "info_focus_edit": 3,
    # Header Refresh now reloads the already-visible Material projection while
    # hidden tabs remain generation-dirty.  Keep headroom above the Maya 2024
    # post-contract baseline without permitting a full-model eager reload.
    "refresh_visible_material_tab": 60,
}

COLLECTION_METHODS = frozenset(
    {"ls", "list_relatives", "list_connections", "list_history", "list_attr", "alias_attr"}
)

TRACKED_ADAPTER_METHODS = (
    "object_exists",
    "reference_query",
    "ls",
    "attribute_exists",
    "attribute_range",
    "get_attr",
    "is_attr_settable",
    "set_attr",
    "add_attr",
    "delete_attr",
    "create_node",
    "list_relatives",
    "poly_evaluate",
    "list_connections",
    "node_type",
    "list_attr",
    "alias_attr",
    "list_history",
    "blend_shape",
    "shading_node",
    "connect_attr",
    "disconnect_attr",
    "sets",
    "delete",
    "remove_multi_instance",
    "hyper_shade",
    "workspace",
    "xform",
    "select",
    "undo_info",
)

_NODE_ARGUMENTS = {
    "object_exists": (0,),
    "reference_query": (0,),
    "attribute_exists": (1,),
    "attribute_range": (1,),
    "get_attr": (0,),
    "is_attr_settable": (0,),
    "set_attr": (0,),
    "add_attr": (0,),
    "delete_attr": (0,),
    "list_relatives": (0,),
    "poly_evaluate": (0,),
    "list_connections": (0,),
    "node_type": (0,),
    "list_attr": (0,),
    "alias_attr": (0,),
    "list_history": (0,),
    "blend_shape": (0,),
    "connect_attr": (0, 1),
    "disconnect_attr": (0, 1),
    "sets": (0,),
    "delete": (0,),
    "remove_multi_instance": (0,),
    "hyper_shade": (0,),
    "xform": (0,),
    "select": (0,),
}


def distribution(samples_ns: Sequence[int]) -> Dict[str, Any]:
    """Return deterministic nearest-rank p50/p95 timing metadata."""

    ordered = sorted(int(value) for value in samples_ns)
    if not ordered:
        return {"count": 0, "status": "not_observed"}

    def percentile(percent: float) -> int:
        index = max(0, min(len(ordered) - 1, math.ceil(percent * len(ordered)) - 1))
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
    """Return nearest-rank metadata for integer call-count observations."""

    ordered = sorted(int(value) for value in samples)
    if not ordered:
        return {"count": 0, "status": "not_observed"}

    def percentile(percent: float) -> int:
        index = max(0, min(len(ordered) - 1, math.ceil(percent * len(ordered)) - 1))
        return ordered[index]

    return {
        "count": len(ordered),
        "min": ordered[0],
        "p50": percentile(0.50),
        "p95": percentile(0.95),
        "max": ordered[-1],
        "status": "measured",
    }


def _iter_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _iter_strings(item)


def adapter_call_scope(method: str, args: Sequence[Any], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Extract target-like tokens and broad collection queries without Maya."""

    tokens: List[str] = []
    if method == "ls":
        for value in args:
            tokens.extend(_iter_strings(value))
    else:
        for index in _NODE_ARGUMENTS.get(method, ()):
            if index < len(args):
                tokens.extend(_iter_strings(args[index]))
        if method in {"attribute_exists", "attribute_range"} and "node" in kwargs:
            tokens.extend(_iter_strings(kwargs["node"]))

    broad = False
    if method == "ls":
        broad = not args or any("*" in token or "?" in token for token in tokens)
    elif method == "list_relatives":
        broad = bool(kwargs.get("allDescendents"))
    return {"tokens": tokens, "broad_collection": broad}


def aggregate_scan_kind(method: str, args: Sequence[Any], kwargs: Dict[str, Any]) -> Optional[str]:
    """Classify aggregate Bone/Material enumeration calls in a sample."""

    if method == "list_relatives" and kwargs.get("allDescendents"):
        if kwargs.get("type") in (None, "joint"):
            return "bone"
    if method == "ls" and kwargs.get("type") == "joint":
        return "bone"
    for token in adapter_call_scope(method, args, kwargs)["tokens"]:
        leaf = token.rsplit(".", 1)[-1]
        if leaf == "materialMembers":
            return "material"
        if leaf == "boneMembers":
            return "bone"
    return None


class _CallRecorder:
    """Temporarily record coordinator and Maya adapter calls per sample."""

    def __init__(self) -> None:
        self._active = False
        self._calls: List[Dict[str, Any]] = []
        self._restorers: List[Callable[[], None]] = []

    def begin(self) -> None:
        if self._active:
            raise RuntimeError("call recorder sample is already active")
        self._calls = []
        self._active = True

    def end(self) -> List[Dict[str, Any]]:
        if not self._active:
            raise RuntimeError("call recorder sample is not active")
        self._active = False
        return list(self._calls)

    def wrap_object(self, owner: Any, attribute: str, category: str) -> None:
        original = getattr(owner, attribute)

        def observed(*args: Any, **kwargs: Any) -> Any:
            if self._active:
                self._calls.append(
                    {"category": category, "method": attribute, "args": args, "kwargs": dict(kwargs)}
                )
            return original(*args, **kwargs)

        setattr(owner, attribute, observed)
        self._restorers.append(lambda: setattr(owner, attribute, original))

    def wrap_adapter_class(self, adapter_type: type) -> None:
        for attribute in TRACKED_ADAPTER_METHODS:
            if not hasattr(adapter_type, attribute):
                continue
            original = getattr(adapter_type, attribute)

            def observed(instance: Any, *args: Any, _name: str = attribute, _original=original, **kwargs: Any) -> Any:
                event: Optional[Dict[str, Any]] = None
                if self._active:
                    scope = adapter_call_scope(_name, args, kwargs)
                    event = {
                        "category": "adapter",
                        "method": _name,
                        "args": args,
                        "kwargs": dict(kwargs),
                        "node_tokens": list(scope["tokens"]),
                    }
                    self._calls.append(event)
                result = _original(instance, *args, **kwargs)
                if event is not None and _name in {"create_node", "shading_node"}:
                    event["node_tokens"].extend(_iter_strings(result))
                return result

            setattr(adapter_type, attribute, observed)
            self._restorers.append(lambda name=attribute, value=original: setattr(adapter_type, name, value))

    def restore(self) -> None:
        for restore in reversed(self._restorers):
            restore()
        self._restorers = []
        self._active = False


def _canonical_snapshot_node(token: str, known_nodes: Iterable[str]) -> Optional[str]:
    """Resolve a node token against pre/post sample snapshots without Maya calls."""

    if not isinstance(token, str) or not token or token.startswith("{") or token.startswith("["):
        return None
    node = token.split(".", 1)[0]
    known = set(known_nodes)
    if node in known:
        return node
    matches = [candidate for candidate in known if candidate.rsplit("|", 1)[-1] == node]
    return matches[0] if len(matches) == 1 else None


def summarize_calls(
    calls: Sequence[Dict[str, Any]],
    *,
    allowed_nodes: Iterable[str],
    created_nodes: Iterable[str],
    expected_created_nodes: Iterable[str] = (),
    known_nodes: Iterable[str] = (),
) -> Dict[str, Any]:
    """Summarize one sample and identify calls outside its declared target set."""

    allowed = {value for value in allowed_nodes if value}
    created = {value for value in created_nodes if value}
    expected_created = {value for value in expected_created_nodes if value}
    adapter_counts: Counter[str] = Counter()
    collection_counts: Counter[str] = Counter()
    broad_collection_calls: List[str] = []
    aggregate_scan_calls: List[Dict[str, str]] = []
    touched: Set[str] = set()
    read_spec_calls = 0
    presenter_calls: Counter[str] = Counter()
    for call in calls:
        category = call["category"]
        method = call["method"]
        if category == "coordinator" and method == "read_spec":
            read_spec_calls += 1
        elif category == "presenter":
            presenter_calls[method] += 1
        elif category == "adapter":
            adapter_counts[method] += 1
            scope = adapter_call_scope(method, call.get("args", ()), call.get("kwargs", {}))
            if method in COLLECTION_METHODS:
                collection_counts[method] += 1
            if scope["broad_collection"]:
                broad_collection_calls.append(method)
            aggregate_kind = aggregate_scan_kind(method, call.get("args", ()), call.get("kwargs", {}))
            if aggregate_kind is not None:
                aggregate_scan_calls.append({"kind": aggregate_kind, "method": method})
            for token in call.get("node_tokens", ()):
                resolved = _canonical_snapshot_node(token, known_nodes)
                if resolved:
                    touched.add(resolved)
    unexpected_created = created - allowed - expected_created
    return {
        "read_spec_calls": read_spec_calls,
        "adapter_call_count": sum(adapter_counts.values()),
        "adapter_calls_by_method": dict(sorted(adapter_counts.items())),
        "collection_calls_by_method": dict(sorted(collection_counts.items())),
        "broad_collection_calls": broad_collection_calls,
        "aggregate_scan_calls": aggregate_scan_calls,
        "presenter_calls_by_method": dict(sorted(presenter_calls.items())),
        "touched_nodes": sorted(touched),
        "created_nodes": sorted(created),
        "unexpected_created_nodes": sorted(unexpected_created),
        "unexpected_nodes": sorted((touched - allowed) | unexpected_created),
    }


def narrow_contract_errors(name: str, summary: Dict[str, Any]) -> List[str]:
    """Return fail-closed target and enumeration errors for a narrow action."""

    if name not in NARROW_ACTIONS:
        return []
    errors: List[str] = []
    if summary["read_spec_calls"]:
        errors.append("narrow action called coordinator.read_spec")
    if summary["broad_collection_calls"]:
        errors.append("narrow action performed broad collection enumeration")
    if summary["unexpected_nodes"]:
        errors.append("narrow action touched or created nodes outside its declared target set")
    return errors


def case_limit_errors(name: str, timing: Dict[str, Any], max_adapter_calls: int) -> List[str]:
    """Return fixed wall-clock/call-count budget violations for one case."""

    errors = []
    budget_ms = P95_BUDGET_MS[name]
    actual_p95_ms = float(timing["p95_ns"]) / 1_000_000.0
    if actual_p95_ms > budget_ms:
        errors.append(f"p95 {actual_p95_ms:.3f} ms exceeds {budget_ms:.3f} ms budget")
    call_budget = MAX_ADAPTER_CALLS[name]
    if max_adapter_calls > call_budget:
        errors.append(f"adapter calls {max_adapter_calls} exceed {call_budget} budget")
    return errors


def _milliseconds(timing: Dict[str, Any], key: str = "p95_ns") -> Optional[float]:
    value = timing.get(key)
    if not isinstance(value, (int, float)):
        return None
    return float(value) / 1_000_000.0


def scaling_gate_errors(gate: Dict[str, Any]) -> List[str]:
    """Return mechanical, fail-closed errors for a scaling gate report."""

    if not isinstance(gate, dict):
        return ["scaling gate report is missing"]
    errors: List[str] = []
    status = gate.get("status")
    if status in {"not_run", "timeout", "warning"}:
        errors.append(f"scaling gate status {status!r} is not passable")
    elif status not in {"measured", "pass", "failed", "error"}:
        errors.append("scaling gate status is missing or invalid")

    expected_names = gate.get("expected_case_names")
    if not isinstance(expected_names, list) or not expected_names:
        expected_names = [case["name"] for case in SCALING_CASES]
    measurement_order = gate.get("measurement_order")
    if measurement_order != list(SCALING_MEASUREMENT_ORDER):
        errors.append(
            "scaling measurement order must be large-to-small: "
            f"expected {list(SCALING_MEASUREMENT_ORDER)!r}, got {measurement_order!r}"
        )
    warmup = gate.get("warmup")
    if (
        not isinstance(warmup, dict)
        or warmup.get("passes") != 1
        or warmup.get("case") != SCALING_MEASUREMENT_ORDER[0]
        or warmup.get("operations") != list(SCALING_OPERATIONS)
    ):
        errors.append("scaling gate requires one warmup pass before all measured cases")
    cases = gate.get("cases")
    if not isinstance(cases, list) or not cases:
        return errors + ["scaling gate has no measured cases"]
    by_name = {case.get("name"): case for case in cases if isinstance(case, dict)}
    for name in expected_names:
        if name not in by_name:
            errors.append(f"scaling case {name!r} is missing")
    unexpected_names = set(by_name) - set(expected_names)
    if unexpected_names:
        errors.append(f"unexpected scaling cases: {sorted(unexpected_names)!r}")
    if len(by_name) != len(cases):
        errors.append("scaling case names are missing or duplicated")

    baseline_morph_count = gate.get("baseline_morph_count")
    baseline_counts = gate.get("baseline_counts")
    if not isinstance(baseline_counts, dict) or not all(
        type(baseline_counts.get(key)) is int and baseline_counts[key] >= 0
        for key in ("bones", "materials", "morphs")
    ):
        errors.append("scaling baseline counts are missing or invalid")
        baseline_counts = {}
    operation_counts: Dict[str, List[int]] = {name: [] for name in SCALING_OPERATIONS}
    operation_histograms: Dict[str, List[Any]] = {name: [] for name in SCALING_OPERATIONS}
    operation_p95: Dict[str, List[tuple[str, float]]] = {name: [] for name in SCALING_OPERATIONS}
    increased_bones = False
    increased_materials = False

    for case in cases:
        if not isinstance(case, dict):
            errors.append("scaling case entry is not an object")
            continue
        case_name = case.get("name", "<unnamed>")
        if case.get("status") != "pass":
            errors.append(f"scaling case {case_name!r} did not pass")
        counts = case.get("counts")
        if not isinstance(counts, dict):
            errors.append(f"scaling case {case_name!r} counts are missing")
            counts = {}
        if not all(type(counts.get(key)) is int and counts[key] >= 0 for key in ("bones", "materials", "morphs")):
            errors.append(f"scaling case {case_name!r} counts are invalid")
        else:
            if baseline_morph_count is None:
                baseline_morph_count = counts["morphs"]
            if counts["morphs"] != baseline_morph_count:
                errors.append(f"scaling case {case_name!r} changed Morph count")
            multipliers = case.get("target_multipliers")
            if not isinstance(multipliers, dict) or not all(
                type(multipliers.get(key)) is int and multipliers[key] >= 1
                for key in ("bones", "materials")
            ):
                errors.append(f"scaling case {case_name!r} target multipliers are missing")
            elif baseline_counts:
                expected_bones = baseline_counts["bones"] * multipliers["bones"]
                expected_materials = baseline_counts["materials"] * multipliers["materials"]
                if counts["bones"] != expected_bones or counts["materials"] != expected_materials:
                    errors.append(f"scaling case {case_name!r} counts do not match target multipliers")
                increased_bones |= multipliers["bones"] > 1
                increased_materials |= multipliers["materials"] > 1

        operations = case.get("operations")
        if not isinstance(operations, dict):
            errors.append(f"scaling case {case_name!r} operations are missing")
            continue
        for operation in SCALING_OPERATIONS:
            result = operations.get(operation)
            if not isinstance(result, dict):
                errors.append(f"{case_name}/{operation} measurement is missing")
                continue
            if result.get("status") != "pass":
                errors.append(f"{case_name}/{operation} did not pass")
            if result.get("oracle_status") != "pass":
                errors.append(f"{case_name}/{operation} oracle is missing or failed")
            if result.get("warnings"):
                errors.append(f"{case_name}/{operation} contains warnings")
            timing = result.get("timing_ns")
            if not isinstance(timing, dict) or timing.get("status") != "measured":
                errors.append(f"{case_name}/{operation} timing measurement is missing")
            else:
                p95_ms = _milliseconds(timing)
                if p95_ms is None or _milliseconds(timing, "p50_ns") is None:
                    errors.append(f"{case_name}/{operation} p50/p95 measurement is missing")
                else:
                    operation_p95[operation].append((case_name, p95_ms))
            samples = result.get("samples")
            if not isinstance(samples, list) or not samples:
                errors.append(f"{case_name}/{operation} samples are missing")
                continue
            measured = [sample for sample in samples if isinstance(sample, dict) and not sample.get("warmup")]
            if not measured or any(sample.get("status") != "measured" for sample in measured):
                errors.append(f"{case_name}/{operation} has missing or failed measurements")
            if not measured or any(sample.get("oracle_status") != "pass" for sample in measured):
                errors.append(f"{case_name}/{operation} sample oracle is missing or failed")
            call_values = [sample.get("adapter_call_count") for sample in measured]
            if not call_values or any(type(value) is not int for value in call_values):
                errors.append(f"{case_name}/{operation} adapter call counts are missing")
            else:
                operation_counts[operation].extend(call_values)
                if len(set(call_values)) != 1:
                    errors.append(f"{case_name}/{operation} adapter call count is not constant")
            histograms = [sample.get("adapter_calls_by_method") for sample in measured]
            if not histograms or any(not isinstance(histogram, dict) for histogram in histograms):
                errors.append(f"{case_name}/{operation} adapter method histogram is missing")
            else:
                operation_histograms[operation].extend(histograms)
            if not isinstance(result.get("adapter_method_histogram"), dict):
                errors.append(f"{case_name}/{operation} aggregate adapter method histogram is missing")
            call_distribution = result.get("adapter_call_counts")
            if not isinstance(call_distribution, dict) or call_distribution.get("status") != "measured":
                errors.append(f"{case_name}/{operation} call-count distribution is missing")
            aggregate_scans = result.get("aggregate_scan_calls")
            if not isinstance(aggregate_scans, list):
                errors.append(f"{case_name}/{operation} aggregate scan observation is missing")
                aggregate_scans = []
            if aggregate_scans:
                errors.append(f"{case_name}/{operation} performed an aggregate Bone/Material scan")
            if type(result.get("read_spec_calls")) is not int:
                errors.append(f"{case_name}/{operation} read_spec observation is missing")
            elif result.get("read_spec_calls"):
                errors.append(f"{case_name}/{operation} called full coordinator.read_spec")

    if not increased_bones:
        errors.append("scaling cases never increase Bone count")
    if not increased_materials:
        errors.append("scaling cases never increase Material count")

    for operation, values in operation_counts.items():
        if not values:
            errors.append(f"{operation} has no adapter call measurements")
        elif len(set(values)) != 1:
            errors.append(f"{operation} adapter call count is not constant across scaling cases")
    for operation, histograms in operation_histograms.items():
        if histograms and any(histogram != histograms[0] for histogram in histograms[1:]):
            errors.append(f"{operation} adapter method histogram is not constant across scaling cases")
    limit_config = gate.get("p95_absolute_limit_ms", SCALING_P95_ABSOLUTE_LIMIT_MS)
    if not isinstance(limit_config, dict):
        errors.append("absolute p95 limit configuration is missing")
        limit_config = {}
    for operation, observations in operation_p95.items():
        if not observations:
            errors.append(f"{operation} has no p95 observations")
            continue
        limit = limit_config.get(operation)
        if not isinstance(limit, (int, float)) or limit <= 0:
            errors.append(f"{operation} absolute p95 limit is missing or invalid")
            continue
        for case_name, observed in observations:
            if observed > float(limit):
                errors.append(
                    f"{case_name}/{operation} p95 {observed:.3f} ms exceeds absolute limit "
                    f"{float(limit):.3f} ms"
                )
    return errors


def evaluate_scaling_gate(gate: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate and return a structured status for one scaling gate report."""

    errors = scaling_gate_errors(gate)
    result = dict(gate)
    result["errors"] = list(errors)
    result["status"] = "pass" if not errors else "failed"
    return result


def _safe_process_events() -> None:
    from mmd_tools.ui.qt_compat import QApplication
    import maya.utils as maya_utils

    application = QApplication.instance()
    for _index in range(2):
        try:
            maya_utils.processIdleEvents()
        except Exception:
            pass
        if application is not None:
            application.processEvents()


def _materialize_standard_ik_solvers(cmds: Any) -> None:
    """Create Maya's deferred default IK solvers before timed samples."""

    for node_type in ("ikSCsolver", "ikRPsolver", "ikSplineSolver", "hikSolver"):
        if not cmds.objExists(node_type):
            cmds.createNode(node_type, name=node_type, shared=True, skipSelect=True)
    _safe_process_events()


def _full_node(cmds: Any, node: str) -> str:
    matches = cmds.ls(node, long=True) or []
    if len(matches) != 1:
        raise RuntimeError(f"node is not uniquely resolvable: {node!r} -> {matches!r}")
    return str(matches[0])


def _model_statistics(cmds: Any, root: str, spec: Any) -> Dict[str, int]:
    from mmd_tools.core.constants import ATTR_MMD_DISPLAY_FRAMES_JSON

    shapes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
    raw_display = cmds.getAttr(f"{root}.{ATTR_MMD_DISPLAY_FRAMES_JSON}") or "[]"
    display_frames = json.loads(raw_display)
    return {
        "vertices": sum(int(cmds.polyEvaluate(shape, vertex=True) or 0) for shape in shapes),
        "materials": len(spec.materials),
        "bones": len(spec.bones),
        "bone_index_limit": max((bone.index for bone in spec.bones), default=-1) + 1,
        "morphs": len(spec.morphs),
        "display_frames": len(display_frames),
    }


def _begin_info_edit_session(presenter: Any) -> None:
    """Start the Info probe on the specific model-name field widget."""

    presenter.begin_edit_session(presenter.view.model_name_en_edit)


def _measure_action(
    name: str,
    callback: Callable[[int], None],
    oracle: Callable[[int], None],
    recorder: _CallRecorder,
    cmds: Any,
    allowed_nodes: Set[str],
    iterations: int,
    warmup: int,
    expected_created_nodes: Iterable[str] = (),
) -> Dict[str, Any]:
    expected_created = {value for value in expected_created_nodes if value}
    rows: List[Dict[str, Any]] = []
    for index in range(warmup + iterations):
        before_nodes = set(str(item) for item in (cmds.ls(long=True) or []))
        recorder.begin()
        started = time.perf_counter_ns()
        error: Optional[str] = None
        try:
            callback(index)
            _safe_process_events()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        elapsed = time.perf_counter_ns() - started
        calls = recorder.end()
        try:
            if error is None:
                oracle(index)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        after_nodes = set(str(item) for item in (cmds.ls(long=True) or []))
        summary = summarize_calls(
            calls,
            allowed_nodes=allowed_nodes,
            created_nodes=after_nodes - before_nodes,
            expected_created_nodes=expected_created,
            known_nodes=before_nodes | after_nodes,
        )
        contract_errors = narrow_contract_errors(name, summary)
        rows.append(
            {
                "index": index,
                "warmup": index < warmup,
                "elapsed_ns": elapsed,
                "status": "failed" if error or contract_errors else "measured",
                "error": error,
                "contract_errors": contract_errors,
                **summary,
            }
        )
    measured = [row for row in rows if not row["warmup"]]
    failures = [row for row in measured if row["status"] != "measured"]
    timing = distribution([row["elapsed_ns"] for row in measured])
    max_adapter_calls = max((row["adapter_call_count"] for row in measured), default=0)
    limit_errors = case_limit_errors(name, timing, max_adapter_calls)
    return {
        "name": name,
        "status": "failed" if failures or limit_errors else "pass",
        "iterations": iterations,
        "warmup": warmup,
        "target_nodes": sorted(allowed_nodes),
        "expected_created_nodes": sorted(expected_created),
        "timing_ns": timing,
        "limits": {
            "p95_budget_ms": P95_BUDGET_MS[name],
            "max_adapter_calls": MAX_ADAPTER_CALLS[name],
            "observed_max_adapter_calls": max_adapter_calls,
        },
        "contract": {
            "read_spec_calls": 0 if name in NARROW_ACTIONS else "baseline_only",
            "broad_collection_calls": 0 if name in NARROW_ACTIONS else "baseline_only",
            "unexpected_nodes": 0 if name in NARROW_ACTIONS else "baseline_only",
        },
        "failures": len(failures),
        "limit_errors": limit_errors,
        "samples": rows,
    }


def _measure_scaling_operation(
    name: str,
    callback: Callable[[int], None],
    oracle: Optional[Callable[[int], None]],
    recorder: _CallRecorder,
    cmds: Any,
    iterations: int,
    warmup: int,
) -> Dict[str, Any]:
    """Measure one Morph snapshot/refresh operation for a scaling case."""

    rows: List[Dict[str, Any]] = []
    for index in range(warmup + iterations):
        before_nodes = set(str(item) for item in (cmds.ls(long=True) or []))
        recorder.begin()
        started = time.perf_counter_ns()
        error: Optional[str] = None
        oracle_status = "missing"
        try:
            callback(index)
            _safe_process_events()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        elapsed = time.perf_counter_ns() - started
        calls = recorder.end()
        if error is None and oracle is not None:
            try:
                oracle(index)
                oracle_status = "pass"
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                oracle_status = "failed"
        after_nodes = set(str(item) for item in (cmds.ls(long=True) or []))
        summary = summarize_calls(
            calls,
            allowed_nodes=set(),
            created_nodes=after_nodes - before_nodes,
            known_nodes=before_nodes | after_nodes,
        )
        contract_errors = []
        if summary["aggregate_scan_calls"]:
            contract_errors.append("Morph operation performed an aggregate Bone/Material scan")
        if summary["read_spec_calls"]:
            contract_errors.append("Morph operation called coordinator.read_spec")
        rows.append(
            {
                "index": index,
                "warmup": index < warmup,
                "elapsed_ns": elapsed,
                "status": "failed" if error or contract_errors else "measured",
                "oracle_status": oracle_status,
                "error": error,
                "contract_errors": contract_errors,
                **summary,
            }
        )

    measured = [row for row in rows if not row["warmup"]]
    timing = distribution([row["elapsed_ns"] for row in measured])
    call_values = [row["adapter_call_count"] for row in measured]
    method_histogram: Counter[str] = Counter()
    for row in measured:
        method_histogram.update(row["adapter_calls_by_method"])
    failures = [row for row in measured if row["status"] != "measured"]
    if len(set(call_values)) > 1:
        failures.append({"error": "adapter call count is not constant"})
    if not measured:
        failures.append({"error": "no measured samples"})
    if oracle is None:
        failures.append({"error": "oracle is missing"})
    return {
        "name": name,
        "status": "pass" if not failures else "failed",
        "iterations": iterations,
        "warmup": warmup,
        "oracle_status": "pass" if measured and all(row["oracle_status"] == "pass" for row in measured) else "failed",
        "warnings": [],
        "timing_ns": timing,
        "p50_ms": None if timing["status"] != "measured" else round(float(timing["p50_ns"]) / 1_000_000.0, 3),
        "p95_ms": None if timing["status"] != "measured" else round(float(timing["p95_ns"]) / 1_000_000.0, 3),
        "adapter_call_counts": count_distribution(call_values),
        "adapter_method_histogram": dict(sorted(method_histogram.items())),
        "aggregate_scan_calls": [
            scan
            for row in measured
            for scan in row["aggregate_scan_calls"]
        ],
        "read_spec_calls": sum(row["read_spec_calls"] for row in measured),
        "failures": len(failures),
        "samples": rows,
    }


def _add_scaling_bones(
    cmds: Any,
    root: str,
    adapter: Any,
    first_index: int,
    count: int,
) -> None:
    """Add simple owned joints without changing the imported Morph graph."""

    if count <= 0:
        return
    from mmd_tools.adapters.maya_bone_authoring import register_existing_joints
    from mmd_tools.core.model_authoring_spec import MmdBoneSpec

    bones = []
    for index in range(first_index, first_index + count):
        joint = cmds.createNode(
            "joint",
            name=f"mmdAuthoringPerfBone_{index}",
            parent=root,
            skipSelect=True,
        )
        identity = _full_node(cmds, str(joint))
        bones.append(
            MmdBoneSpec(
                name=f"Authoring Performance Bone {index}",
                name_english=f"Authoring Performance Bone {index}",
                index=index,
                binding_identity=identity,
            )
        )
    register_existing_joints(root, bones, adapter)


def _materialize_scaling_case(
    cmds: Any,
    root: str,
    coordinator: Any,
    adapter: Any,
    *,
    current_bones: int,
    current_materials: int,
    base_bones: int,
    base_bone_index: int,
    base_materials: int,
    bone_multiplier: int,
    material_multiplier: int,
) -> Dict[str, int]:
    """Grow only Bone/Material aggregates to one controlled target size."""

    target_bones = base_bones * bone_multiplier
    target_materials = base_materials * material_multiplier
    if target_materials < current_materials or target_bones < current_bones:
        raise ValueError("scaling cases must be monotonically increasing")
    for _index in range(current_materials, target_materials):
        coordinator.create_material(root)
    _add_scaling_bones(
        cmds,
        root,
        adapter,
        first_index=base_bone_index + (current_bones - base_bones),
        count=target_bones - current_bones,
    )
    return {"bones": target_bones, "materials": target_materials}


def _scaling_operation_callbacks(
    coordinator: Any,
    morph_presenter: Any,
    root: str,
    morph_count: int,
) -> Dict[str, tuple[Callable[[int], None], Callable[[int], None]]]:
    """Build the two fixed Morph operations for one isolated scene state."""

    snapshot_holder: Dict[str, Any] = {"value": None}

    def snapshot_action(_index: int) -> None:
        snapshot_holder["value"] = coordinator.read_morph_authoring_snapshot(root)

    def snapshot_oracle(_index: int) -> None:
        snapshot = snapshot_holder.get("value")
        if snapshot is None or snapshot.spec is None:
            raise AssertionError("Morph snapshot oracle was not produced")
        if len(snapshot.spec.morphs) != morph_count:
            raise AssertionError("Morph snapshot changed the fixed Morph count")
        if len(snapshot.projection.morphs) != morph_count:
            raise AssertionError("Morph projection changed the fixed Morph count")

    def refresh_action(_index: int) -> None:
        morph_presenter.load_morphs()

    def refresh_oracle(_index: int) -> None:
        authoring_spec = getattr(morph_presenter, "_authoring_spec", None)
        if authoring_spec is None or len(authoring_spec.morphs) != morph_count:
            raise AssertionError("Morph refresh did not publish the fixed Morph count")

    return {
        "snapshot": (snapshot_action, snapshot_oracle),
        "refresh": (refresh_action, refresh_oracle),
    }


def _run_scaling_warmup(
    operations: Dict[str, tuple[Callable[[int], None], Callable[[int], None]]],
    recorder: _CallRecorder,
) -> None:
    """Run one unmeasured warmup pass covering both scaling operations."""

    for name in SCALING_OPERATIONS:
        callback, oracle = operations[name]
        recorder.begin()
        error: Optional[Exception] = None
        try:
            callback(0)
            _safe_process_events()
            oracle(0)
        except Exception as exc:
            error = exc
        finally:
            recorder.end()
        if error is not None:
            raise RuntimeError(f"{name} warmup failed: {type(error).__name__}: {error}") from error


def _run_scaling_gate(
    *,
    cmds: Any,
    root: str,
    coordinator: Any,
    morph_presenter: Any,
    adapter: Any,
    base_statistics: Dict[str, int],
    recorder: _CallRecorder,
    iterations: int,
    warmup: int,
) -> Dict[str, Any]:
    """Run the expanded-scene Morph gate with one global warmup pass."""

    del warmup

    gate: Dict[str, Any] = {
        "status": "not_run",
        "fixture_role": "expanded_scaling_scene",
        "expected_case_names": [case["name"] for case in SCALING_CASES],
        "measurement_order": list(SCALING_MEASUREMENT_ORDER),
        "operations": list(SCALING_OPERATIONS),
        "p95_absolute_limit_ms": dict(SCALING_P95_ABSOLUTE_LIMIT_MS),
        "warmup": {
            "passes": 0,
            "case": SCALING_MEASUREMENT_ORDER[0],
            "operations": list(SCALING_OPERATIONS),
        },
        "baseline_counts": {
            "bones": base_statistics["bones"],
            "materials": base_statistics["materials"],
            "morphs": base_statistics["morphs"],
        },
        "baseline_morph_count": base_statistics["morphs"],
        "cases": [],
        "errors": [],
    }
    try:
        if base_statistics["vertices"] < SCALING_MIN_VERTICES or base_statistics["bones"] < SCALING_MIN_BONES:
            raise RuntimeError(
                "scaling fixture is below the dedicated performance envelope: "
                f"vertices={base_statistics['vertices']} bones={base_statistics['bones']}"
            )
        with tempfile.TemporaryDirectory(prefix="mmd_authoring_scaling_") as temp_dir:
            base_scene = Path(temp_dir) / "base.ma"
            cmds.file(rename=str(base_scene))
            cmds.file(save=True, force=True, type="mayaAscii")
            for configuration in reversed(SCALING_CASES):
                cmds.file(str(base_scene), open=True, force=True)
                current_root = _full_node(cmds, root)
                morph_presenter.app_state.current_model_root = current_root
                _materialize_scaling_case(
                    cmds,
                    current_root,
                    coordinator,
                    adapter,
                    current_bones=base_statistics["bones"],
                    current_materials=base_statistics["materials"],
                    base_bones=base_statistics["bones"],
                    base_bone_index=base_statistics["bone_index_limit"],
                    base_materials=base_statistics["materials"],
                    bone_multiplier=configuration["bone_multiplier"],
                    material_multiplier=configuration["material_multiplier"],
                )
                actual_spec = coordinator.read_spec(current_root)
                counts = {
                    "bones": len(actual_spec.bones),
                    "materials": len(actual_spec.materials),
                    "morphs": len(actual_spec.morphs),
                }
                morph_presenter.load_morphs()
                operations = _scaling_operation_callbacks(
                    coordinator,
                    morph_presenter,
                    current_root,
                    base_statistics["morphs"],
                )
                if gate["warmup"]["passes"] == 0:
                    _run_scaling_warmup(operations, recorder)
                    gate["warmup"]["passes"] = 1
                measurements = {
                    name: _measure_scaling_operation(
                        name,
                        *operations[name],
                        recorder,
                        cmds,
                        iterations,
                        0,
                    )
                    for name in SCALING_OPERATIONS
                }
                case = {
                    "name": configuration["name"],
                    "target_multipliers": {
                        "bones": configuration["bone_multiplier"],
                        "materials": configuration["material_multiplier"],
                    },
                    "counts": counts,
                    "operations": measurements,
                    "status": "pass",
                }
                if counts["morphs"] != base_statistics["morphs"]:
                    case["status"] = "failed"
                if any(operation["status"] != "pass" for operation in measurements.values()):
                    case["status"] = "failed"
                gate["cases"].append(case)
        gate["status"] = "measured"
        return evaluate_scaling_gate(gate)
    except Exception as exc:
        gate["status"] = "error"
        gate["errors"] = [f"{type(exc).__name__}: {exc}"]
        gate["unrun_cases"] = [case["name"] for case in SCALING_CASES if case["name"] not in {item.get("name") for item in gate["cases"]}]
        return gate


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_probe(
    log_path: str,
    model_path: str,
    texture_path: str,
    report_path: str,
    iterations: int,
    warmup: int,
    min_vertices: int,
    min_bones: int,
) -> None:
    """Run production Authoring actions and write one fail-closed report."""

    from maya import cmds

    log_file = Path(log_path)
    report_file = Path(report_path)
    report: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "error",
        "run_id": str(time.time_ns()),
        "started_at_utc": _utc_timestamp(),
        "maya_version": str(cmds.about(version=True)),
        "fixture": str(Path(model_path).resolve()),
        "cases": [],
        "scaling_gate": {
            "status": "not_run",
            "fixture_role": "expanded_scaling_scene",
            "expected_case_names": [case["name"] for case in SCALING_CASES],
            "measurement_order": list(SCALING_MEASUREMENT_ORDER),
            "operations": list(SCALING_OPERATIONS),
            "p95_absolute_limit_ms": dict(SCALING_P95_ABSOLUTE_LIMIT_MS),
            "warmup": {
                "passes": 0,
                "case": SCALING_MEASUREMENT_ORDER[0],
                "operations": list(SCALING_OPERATIONS),
            },
            "errors": ["scaling gate has not run yet"],
            "cases": [],
        },
        "errors": [],
    }
    recorder = _CallRecorder()
    window = None
    try:
        if bool(cmds.about(batch=True)):
            raise RuntimeError("authoring performance contract requires Maya GUI")
        if iterations < 1 or warmup < 0:
            raise ValueError("iterations must be >= 1 and warmup must be >= 0")
        for path in (model_path, texture_path):
            if not Path(path).is_file():
                raise FileNotFoundError(path)

        cmds.file(new=True, force=True)
        from tests.common.maya_plugin_setup import load_mmd_tools_plugin

        load_mmd_tools_plugin(PROJECT_ROOT, cmds_module=cmds)
        from mmd_tools.io.mmd_importer import import_mmd_file

        root = import_mmd_file(
            str(Path(model_path).resolve()),
            options={
                "scale": 1.0,
                "import_physics": False,
                "setup_rig": True,
                "setup_bone_orientation": False,
                "create_mmd_control_rig": False,
                "import_morphs": True,
                "create_mmd_shaders": False,
                "use_cpp_fast_load": False,
                "use_native_pmx_parse": False,
                "require_native_pmx_parse": False,
            },
        )
        root = _full_node(cmds, str(root))

        from mmd_tools.core import model_registry
        from mmd_tools.core.constants import ATTR_MMD_DISPLAY_FRAMES_JSON, ATTR_MMD_MODEL_NAME_EN
        from mmd_tools.ui.main_window import MainWindow
        from mmd_tools.adapters.maya_cmds_adapter import MayaCmdsAdapter

        window = MainWindow()
        window.show()
        window.app_state.current_model_root = root
        _safe_process_events()
        _materialize_standard_ik_solvers(cmds)
        coordinator = window.authoring_composition.coordinator
        spec = coordinator.read_spec(root)
        statistics_row = _model_statistics(cmds, root, spec)
        report["model"] = {"root": root, **statistics_row}
        if statistics_row["vertices"] < min_vertices or statistics_row["bones"] < min_bones:
            raise RuntimeError(
                "representative model is too small: "
                f"vertices={statistics_row['vertices']} bones={statistics_row['bones']}"
            )
        if not spec.materials or not spec.morphs or not statistics_row["display_frames"]:
            raise RuntimeError("representative model lacks material, morph, or display-frame data")

        registry = _full_node(cmds, model_registry.get_model_registry(root))
        common_targets = {root, registry}
        material = spec.materials[0]
        material_targets = common_targets | {_full_node(cmds, material.binding_identity)}
        material_created_targets = {f"mmdMaterial_{material.index}_File"}
        material_targets.update(material_created_targets)
        for node_type in ("file", "place2dTexture", "shadingEngine"):
            for connected in cmds.listConnections(material.binding_identity, type=node_type) or []:
                material_targets.add(_full_node(cmds, str(connected)))
        morph = spec.morphs[0]
        morph_targets = common_targets | {_full_node(cmds, morph.binding_identity)}

        material_presenter = window.material_presenter
        material_presenter.view.material_list.setCurrentRow(0)
        morph_presenter = window.morph_presenter
        window.tab_widget.setCurrentWidget(window.morph_tab)
        morph_presenter.ensure_morphs_loaded()
        morph_presenter.view.morph_list.setCurrentRow(0)
        display_presenter = window.display_pane_presenter
        window.tab_widget.setCurrentWidget(window.display_pane_tab)
        display_presenter.refresh()
        display_presenter.view.frame_list.setCurrentRow(0)
        _safe_process_events()

        morph_data = morph_presenter.morph_data[morph_presenter.current_morph]
        morph_plugs = list(morph_presenter._iter_morph_weight_plugs(morph_data, morph_presenter.current_morph))
        for key in ("morph_node", "blend_shape_node"):
            node = morph_data.get(key)
            if node:
                morph_targets.add(_full_node(cmds, str(node)))
        for plug in morph_plugs:
            try:
                morph_targets.add(_full_node(cmds, plug.split(".", 1)[0]))
            except RuntimeError:
                pass

        recorder.wrap_object(coordinator, "read_spec", "coordinator")
        for presenter, method in (
            (material_presenter, "load_materials"),
            (morph_presenter, "load_morphs"),
            (display_presenter, "refresh"),
            (window.info_presenter, "load_model_info"),
            (window.bone_presenter, "load_bones"),
        ):
            recorder.wrap_object(presenter, method, "presenter")
        recorder.wrap_object(coordinator, "read_morph_authoring_snapshot", "coordinator")
        recorder.wrap_adapter_class(MayaCmdsAdapter)

        material_value = {"expected": ""}

        def material_value_action(index: int) -> None:
            material_value["expected"] = f"Perf Material {index % 2}"
            material_presenter.view.material_en_name_edit.setText(material_value["expected"])
            if material_presenter.apply_changes() is None:
                raise RuntimeError("Material value Apply failed")

        def material_value_oracle(_index: int) -> None:
            observed = coordinator.read_material_value(root, material.index, material.binding_identity)
            if observed.name_english != material_value["expected"]:
                raise AssertionError("Material value read-back mismatch")

        texture_state: Dict[str, Optional[str]] = {"expected": None}

        def material_texture_action(index: int) -> None:
            texture_state["expected"] = None if index % 2 == 0 else str(Path(texture_path).resolve())
            material_presenter.view.texture_path_edit.setText(texture_state["expected"] or "")
            if material_presenter.apply_changes() is None:
                raise RuntimeError("Material texture Apply failed")

        def material_texture_oracle(_index: int) -> None:
            observed = coordinator.read_material_value(root, material.index, material.binding_identity)
            actual = observed.resolved_texture_path
            if (str(Path(actual).resolve()) if actual else None) != texture_state["expected"]:
                raise AssertionError(f"Material texture read-back mismatch: {actual!r}")

        morph_name = {"expected": ""}

        def morph_name_action(index: int) -> None:
            morph_name["expected"] = f"Perf Morph {index % 2}"
            morph_presenter.view.morph_name_en_edit.setText(morph_name["expected"])
            morph_presenter.apply_changes()

        def morph_name_oracle(_index: int) -> None:
            observed = coordinator.read_morph_value(root, morph.index, morph.binding_identity)
            if observed.name_english != morph_name["expected"]:
                raise AssertionError("Morph name read-back mismatch")

        slider_state = {"expected": 0.0}

        def morph_slider_action(index: int) -> None:
            value = 25 if index % 2 == 0 else 75
            slider_state["expected"] = value / 100.0
            morph_presenter.view.morph_slider.setValue(value)

        def morph_slider_oracle(_index: int) -> None:
            if not morph_plugs:
                raise AssertionError("Morph preview exposes no target plugs")
            actual = float(cmds.getAttr(morph_plugs[0]))
            if abs(actual - slider_state["expected"]) > 1e-6:
                raise AssertionError(f"Morph slider read-back mismatch: {actual}")

        display_state = {"expected": ""}

        def display_action(index: int) -> None:
            display_state["expected"] = f"Perf Frame {index % 2}"
            display_presenter.view.name_en_edit.setText(display_state["expected"])
            if not display_presenter.apply():
                raise RuntimeError("Display Apply failed")

        def display_oracle(_index: int) -> None:
            raw = cmds.getAttr(f"{root}.{ATTR_MMD_DISPLAY_FRAMES_JSON}") or ""
            payload = json.loads(raw)
            if payload[0]["name_english"] != display_state["expected"]:
                raise AssertionError("Display metadata read-back mismatch")

        info_state = {"expected": ""}

        def info_action(index: int) -> None:
            info_state["expected"] = f"Perf Model {index % 2}"
            presenter = window.info_presenter
            _begin_info_edit_session(presenter)
            presenter.update_model_info(ATTR_MMD_MODEL_NAME_EN, info_state["expected"])
            presenter.end_edit_session()

        def info_oracle(_index: int) -> None:
            if cmds.getAttr(f"{root}.{ATTR_MMD_MODEL_NAME_EN}") != info_state["expected"]:
                raise AssertionError("Info metadata read-back mismatch")

        def refresh_action(_index: int) -> None:
            window.header_widget.refresh_btn.click()

        def refresh_oracle(_index: int) -> None:
            if window.app_state.current_model_root != root:
                raise AssertionError("Refresh changed the current model")
            if window.tab_widget.currentWidget() is not material_presenter.view:
                raise AssertionError("Refresh changed the visible tab")

        actions = (
            ("material_value_apply", material_value_action, material_value_oracle, material_targets, set()),
            (
                "material_texture_apply",
                material_texture_action,
                material_texture_oracle,
                material_targets,
                material_created_targets,
            ),
            ("morph_name_apply", morph_name_action, morph_name_oracle, morph_targets, set()),
            ("morph_slider_drag", morph_slider_action, morph_slider_oracle, morph_targets, set()),
            ("display_apply", display_action, display_oracle, common_targets, set()),
            ("info_focus_edit", info_action, info_oracle, common_targets, set()),
            ("refresh_visible_material_tab", refresh_action, refresh_oracle, common_targets, set()),
        )
        for name, callback, oracle, targets, expected_created in actions:
            if name == "refresh_visible_material_tab":
                window.tab_widget.setCurrentWidget(material_presenter.view)
                _safe_process_events()
            report["cases"].append(
                _measure_action(
                    name,
                    callback,
                    oracle,
                    recorder,
                    cmds,
                    set(targets),
                    iterations,
                    warmup,
                    expected_created,
                )
            )
        report["scaling_gate"] = _run_scaling_gate(
            cmds=cmds,
            root=root,
            coordinator=coordinator,
            morph_presenter=morph_presenter,
            adapter=window.authoring_composition.cmds_adapter,
            base_statistics=statistics_row,
            recorder=recorder,
            iterations=iterations,
            warmup=warmup,
        )
        failed = [case for case in report["cases"] if case["status"] != "pass"]
        if report["scaling_gate"].get("status") != "pass":
            failed.append(report["scaling_gate"])
        report["status"] = "failed" if failed else "pass"
        if failed:
            report["errors"].append("one or more Authoring performance or scaling cases failed")
    except Exception as exc:
        report["status"] = "error"
        report["errors"].append(f"{type(exc).__name__}: {exc}")
        report["traceback"] = traceback.format_exc()
    finally:
        recorder.restore()
        try:
            if window is not None:
                window.close()
                window.deleteLater()
                _safe_process_events()
        except Exception:
            pass
        report["finished_at_utc"] = _utc_timestamp()
        _write_json(report_file, report)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write("RESULT_JSON: " + json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n")
            handle.write(COMPLETION_MARKER + "\n")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", default="2024")
    parser.add_argument("--model", default=str(DEFAULT_MODEL))
    parser.add_argument("--texture", default=str(DEFAULT_TEXTURE))
    parser.add_argument("--iterations", type=int, default=7)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--min-vertices", type=int, default=8_000)
    parser.add_argument("--min-bones", type=int, default=100)
    parser.add_argument("--port", type=int, default=COMMAND_PORT)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    return parser.parse_args()


def main() -> int:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from tests.viewport.maya_e2e_harness import run_maya_e2e

    args = _parse_args()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"maya{args.maya}"
    report_path = out_dir / f"authoring_performance_contract_{suffix}.json"
    log_path = out_dir / f"authoring_performance_contract_{suffix}.log"
    command = (
        "import sys\n"
        "from pathlib import Path\n"
        f"project_root = Path({str(PROJECT_ROOT.as_posix())!r})\n"
        "sys.path.insert(0, str(project_root)) if str(project_root) not in sys.path else None\n"
        "from tools.probes.authoring_performance_contract import run_probe\n"
        f"run_probe({str(log_path.as_posix())!r}, {str(Path(args.model).resolve().as_posix())!r}, "
        f"{str(Path(args.texture).resolve().as_posix())!r}, {str(report_path.as_posix())!r}, "
        f"{int(args.iterations)}, {int(args.warmup)}, {int(args.min_vertices)}, {int(args.min_bones)})\n"
    )
    try:
        report = run_maya_e2e(
            project_root=PROJECT_ROOT,
            version=args.maya,
            out_dir=out_dir,
            port=args.port,
            timeout=args.timeout,
            log_path=log_path,
            report_path=report_path,
            command=command,
            marker=COMPLETION_MARKER,
            send_label="<authoring-performance-contract>",
            stale_paths=(report_path, log_path),
            terminate_process=True,
            quit_delay=3.0,
            port_error=f"commandPort :{args.port} is already open",
            report_error=f"authoring performance report missing: {report_path}",
        )
    except Exception as exc:
        gate_status = "timeout" if isinstance(exc, TimeoutError) else "not_run"
        report = {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "run_id": str(time.time_ns()),
            "started_at_utc": _utc_timestamp(),
            "finished_at_utc": _utc_timestamp(),
            "maya_version": str(args.maya),
            "fixture": str(Path(args.model).resolve()),
            "cases": [],
            "scaling_gate": {
                "status": gate_status,
                "fixture_role": "expanded_scaling_scene",
                "expected_case_names": [case["name"] for case in SCALING_CASES],
                "measurement_order": list(SCALING_MEASUREMENT_ORDER),
                "operations": list(SCALING_OPERATIONS),
                "p95_absolute_limit_ms": dict(SCALING_P95_ABSOLUTE_LIMIT_MS),
                "warmup": {
                    "passes": 0,
                    "case": SCALING_MEASUREMENT_ORDER[0],
                    "operations": list(SCALING_OPERATIONS),
                },
                "cases": [],
                "errors": [f"real-Maya scaling gate {gate_status}: {type(exc).__name__}: {exc}"],
            },
            "errors": [f"real-Maya performance runner did not complete: {type(exc).__name__}: {exc}"],
        }
        _write_json(report_path, report)
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
