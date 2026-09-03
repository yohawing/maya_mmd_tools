"""Measure the existing VMD replacement path on one real PMX/VMD fixture.

The host process writes a UTF-8 JSON configuration and launches one fresh
``mayapy`` process per Maya version.  Only the ASCII configuration path crosses
the mayapy command line; fixture paths are decoded from that JSON inside Maya.
The worker imports the model and a seed VMD through the production Actions,
then measures warmup and replacement imports with ``clear_existing_motion``.

This is measurement-only evidence.  Runtime wrappers record the existing route
planner, clear-route resolver, and clear function without changing product
files.  ``post_route_clear_residual_wall_ns`` is the remainder of the complete
replacement import; it includes parsing, mapping, keying, camera/light work,
and cleanup, and is deliberately not described as pure key application.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
import threading
import time
import traceback
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_PMX = ROOT / "build" / "fixtures" / "sour_black_love_ascii" / "sour_black.pmx"
DEFAULT_VMD = ROOT / "build" / "fixtures" / "sour_black_love_ascii" / "love_dance.vmd"
DEFAULT_OUT_DIR = ROOT / "build" / "reports" / "vmd-clear-performance"
SCHEMA_VERSION = 1
DEFAULT_MAYA_VERSIONS = ("2024", "2026")
DEFAULT_RUNS = 3
DEFAULT_WARMUP = 1
MIN_MEASURED_RUNS = 3
CLEAR_THRESHOLD_NS = 250_000_000
CLEAR_RATIO_THRESHOLD = 0.20
PYTHON_PLUGIN = ROOT / "mmd_tools" / "plugin_main.py"
_DIAGNOSTIC_TEXT_LIMIT = 1_024
_DIAGNOSTIC_WARNING_LIMIT = 16
_MAYAPY_OUTPUT_TAIL_LIMIT = 8_192
_MAYAPY_WARNING_LINE_LIMIT = 16
_PROFILE_STATUS_FIELDS = (
    "native_physics_bake_applied",
    "vmd_converter.runtime_registration.status",
    "vmd_converter.runtime_registration.reason",
    "vmd_converter.runtime_registration.evaluation_mode",
    "vmd_converter.runtime_registration.frame_count",
    "vmd_converter.runtime_registration.scene_metadata_stored",
    "vmd_converter.registered_sparse.status",
    "vmd_converter.registered_sparse.registration_mode",
    "vmd_converter.registered_sparse.fallback",
    "vmd_converter.registered_sparse.track_count",
    "vmd_converter.registered_sparse.vertex_track_count",
    "vmd_converter.registered_sparse.key_count",
    "vmd_converter.reduced_bake_keys.status",
    "vmd_converter.reduced_bake_keys.reason",
    "vmd_converter.reduced_bake_keys.fallback",
    "vmd_converter.reduced_bake_keys.route_count",
    "vmd_converter.reduced_bake_keys.morph_fanout_count",
    "mmd_control_rig.requested",
    "mmd_control_rig.succeeded",
    "mmd_control_rig.bound",
    "mmd_control_rig.control_count",
    "mmd_control_rig.reused",
    "physics_converter.eligible",
    "physics_converter.used",
    "physics_converter.reason",
)


class ProbeConfigurationError(ValueError):
    """Raised when a probe configuration cannot be safely executed."""


def _sha256_file(path: Path) -> str:
    """Return the SHA-256 digest for a fixture or other provenance file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _finite_number(value: Any) -> bool:
    """Return whether *value* is a finite real number."""
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _median(values: Sequence[float]) -> Optional[float]:
    """Return a median or ``None`` for an empty sample set."""
    if not values:
        return None
    return float(statistics.median(values))


def summarize_timings(runs: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Summarize valid measured replacement timings without hiding omissions."""
    measured = [
        run
        for run in runs
        if not bool(run.get("warmup", False)) and run.get("status") == "measured"
    ]
    clear_values: List[float] = []
    total_values: List[float] = []
    route_values: List[float] = []
    clear_route_values: List[float] = []
    residual_values: List[float] = []
    ratios: List[float] = []
    missing: List[str] = []
    for index, run in enumerate(measured):
        timings = run.get("timings")
        if not isinstance(timings, Mapping):
            missing.append(f"run[{index}].timings")
            continue
        required = (
            "route_resolution_wall_ns",
            "clear_wall_ns",
            "replacement_total_wall_ns",
            "post_route_clear_residual_wall_ns",
        )
        if any(name not in timings or not _finite_number(timings[name]) for name in required):
            missing.append(f"run[{index}].timings")
            continue
        clear = float(timings["clear_wall_ns"])
        total = float(timings["replacement_total_wall_ns"])
        if total <= 0.0 or clear < 0.0:
            missing.append(f"run[{index}].timings")
            continue
        clear_values.append(clear)
        total_values.append(total)
        route_values.append(float(timings["route_resolution_wall_ns"]))
        clear_route_values.append(float(timings.get("clear_route_resolution_wall_ns", 0.0)))
        residual_values.append(float(timings["post_route_clear_residual_wall_ns"]))
        ratios.append(clear / total)
    return {
        "measured_run_count": len(measured),
        "valid_timing_count": len(clear_values),
        "missing_metrics": missing,
        "median_route_resolution_wall_ns": _median(route_values),
        "median_clear_route_resolution_wall_ns": _median(clear_route_values),
        "median_clear_wall_ns": _median(clear_values),
        "median_replacement_total_wall_ns": _median(total_values),
        "median_post_route_clear_residual_wall_ns": _median(residual_values),
        "median_clear_total_ratio": _median(ratios),
    }


def threshold_decision(
    runs: Sequence[Mapping[str, Any]],
    *,
    warnings: Sequence[Any] = (),
    errors: Sequence[Any] = (),
    not_run: Sequence[Any] = (),
    minimum_runs: int = MIN_MEASURED_RUNS,
) -> Dict[str, Any]:
    """Return the fail-closed threshold decision for measured replacement runs.

    A proceed decision requires at least ``minimum_runs`` valid measured rows,
    no warnings/errors/not-run entries, complete timing metrics, and either a
    250 ms median clear or a 20% median clear/total ratio.  Invalid or partial
    rows never contribute to the medians.
    """
    summary = summarize_timings(runs)
    reasons: List[str] = []
    measured = [
        run
        for run in runs
        if not bool(run.get("warmup", False)) and run.get("status") == "measured"
    ]
    invalid = [run for run in runs if not bool(run.get("warmup", False)) and run.get("status") != "measured"]
    if len(measured) < int(minimum_runs):
        reasons.append(f"fewer than {int(minimum_runs)} measured replacement runs")
    if invalid:
        reasons.append("a measured replacement run was partial, failed, or otherwise invalid")
    if summary["missing_metrics"]:
        reasons.append("one or more measured runs are missing timing metrics")
    if warnings:
        reasons.append("warnings were reported")
    if errors:
        reasons.append("errors were reported")
    if not_run:
        reasons.append("not_run blockers were reported")

    median_clear = summary["median_clear_wall_ns"]
    median_ratio = summary["median_clear_total_ratio"]
    threshold_met = bool(
        _finite_number(median_clear)
        and _finite_number(median_ratio)
        and (float(median_clear) >= CLEAR_THRESHOLD_NS or float(median_ratio) >= CLEAR_RATIO_THRESHOLD)
    )
    if not threshold_met:
        reasons.append("clear threshold was not met")

    if reasons:
        status = "not_run" if len(measured) < int(minimum_runs) or invalid or summary["missing_metrics"] else "fail"
        return {
            "status": status,
            "decision": "no_proceed",
            "threshold_met": threshold_met,
            "reason": "; ".join(dict.fromkeys(reasons)),
            "minimum_measured_runs": int(minimum_runs),
            "clear_threshold_ms": CLEAR_THRESHOLD_NS / 1_000_000.0,
            "clear_ratio_threshold": CLEAR_RATIO_THRESHOLD,
            **summary,
        }
    return {
        "status": "pass",
        "decision": "proceed",
        "threshold_met": True,
        "reason": "median clear wall time or clear/total ratio met the accelerator threshold",
        "minimum_measured_runs": int(minimum_runs),
        "clear_threshold_ms": CLEAR_THRESHOLD_NS / 1_000_000.0,
        "clear_ratio_threshold": CLEAR_RATIO_THRESHOLD,
        **summary,
    }


def load_config(path: Path, *, require_assets: bool = True) -> Dict[str, Any]:
    """Read and normalize a UTF-8 worker configuration."""
    config_path = Path(path)
    if not str(config_path).isascii():
        raise ProbeConfigurationError("worker config path must be ASCII-safe for mayapy argv")
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProbeConfigurationError(f"could not read worker config: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ProbeConfigurationError("worker config root must be an object")
    allowed = {
        "schema_version",
        "maya_version",
        "pmx_path",
        "vmd_path",
        "out_path",
        "maya_app_dir",
        "runs",
        "warmup",
        "candidate_sha",
        "git_provenance_start",
        "options",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ProbeConfigurationError("unknown worker config fields: " + ", ".join(unknown))
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ProbeConfigurationError(f"schema_version must be {SCHEMA_VERSION}")
    maya_version = str(raw.get("maya_version", "")).strip()
    if not maya_version:
        raise ProbeConfigurationError("maya_version must not be empty")
    try:
        runs = int(raw.get("runs", DEFAULT_RUNS))
        warmup = int(raw.get("warmup", DEFAULT_WARMUP))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProbeConfigurationError("runs and warmup must be integers") from exc
    if runs < MIN_MEASURED_RUNS:
        raise ProbeConfigurationError(f"runs must be at least {MIN_MEASURED_RUNS}")
    if warmup < 1:
        raise ProbeConfigurationError("warmup must be at least 1")
    try:
        pmx_path = Path(str(raw["pmx_path"])).expanduser()
        vmd_path = Path(str(raw["vmd_path"])).expanduser()
        out_path = Path(str(raw["out_path"])).expanduser()
        maya_app_dir = Path(str(raw["maya_app_dir"])).expanduser()
    except KeyError as exc:
        raise ProbeConfigurationError(f"missing worker config field: {exc.args[0]}") from exc
    if pmx_path.suffix.casefold() not in {".pmx", ".pmd"}:
        raise ProbeConfigurationError("pmx_path must name a PMX or PMD file")
    if vmd_path.suffix.casefold() != ".vmd":
        raise ProbeConfigurationError("vmd_path must name a VMD file")
    if require_assets:
        for label, asset in (("pmx_path", pmx_path), ("vmd_path", vmd_path)):
            if not asset.is_file():
                raise ProbeConfigurationError(f"{label} does not exist: {asset}")
    options = raw.get("options", {})
    if not isinstance(options, Mapping):
        raise ProbeConfigurationError("options must be an object")
    return {
        "schema_version": SCHEMA_VERSION,
        "maya_version": maya_version,
        "pmx_path": pmx_path,
        "vmd_path": vmd_path,
        "out_path": out_path,
        "maya_app_dir": maya_app_dir,
        "runs": runs,
        "warmup": warmup,
        "candidate_sha": str(raw.get("candidate_sha", "unknown")),
        "git_provenance_start": raw.get("git_provenance_start"),
        "options": dict(options),
    }


def _fixture_provenance(config: Mapping[str, Any]) -> Dict[str, Any]:
    """Build the fixture identity recorded in every version report."""
    result: Dict[str, Any] = {}
    for label in ("pmx_path", "vmd_path"):
        path = Path(config[label]).resolve()
        entry: Dict[str, Any] = {"path": str(path), "exists": path.is_file()}
        if path.is_file():
            entry["size_bytes"] = path.stat().st_size
            entry["sha256"] = _sha256_file(path)
        result[label] = entry
    return result


def _base_options() -> Dict[str, Any]:
    """Return fixed production options shared by seed and replacement imports."""
    return {
        "scale": 1.0,
        "use_namespace": True,
        "setup_rig": True,
        "setup_bone_orientation": True,
        "import_physics": False,
        "import_morphs": True,
        "create_mmd_shaders": False,
        "use_cpp_fast_load": False,
        "use_native_pmx_parse": False,
        "require_native_pmx_parse": False,
        "bake_mode": False,
        "create_mmd_control_rig": False,
        "import_camera_animation": True,
        "import_light_animation": True,
    }


def _canonical_root(cmds: Any, value: Any) -> str:
    """Resolve an Action root to one full DAG path."""
    matches = cmds.ls(value, long=True) or []
    if isinstance(matches, (str, bytes)) or len(matches) != 1:
        raise RuntimeError(f"model root is not uniquely resolvable: {value!r} -> {matches!r}")
    root = str(matches[0])
    if not root.startswith("|"):
        raise RuntimeError(f"model root is not a canonical DAG path: {root!r}")
    return root


def _key_count(cmds: Any, plug: str) -> int:
    """Read one plug's key count, treating unsupported queries as missing."""
    try:
        value = cmds.keyframe(plug, query=True, keyframeCount=True)
    except Exception:
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _anim_curves_for_plug(cmds: Any, plug: str) -> List[str]:
    """Find direct and animBlend-backed source curves for one target plug."""
    curves = set()
    try:
        upstream = cmds.listConnections(plug, source=True, destination=False) or []
    except Exception:
        upstream = []
    for node in upstream:
        node = str(node)
        try:
            node_type = str(cmds.nodeType(node))
        except Exception:
            continue
        if node_type.startswith("animCurve"):
            curves.add(node)
            continue
        if not node_type.startswith("animBlend"):
            continue
        try:
            input_curves = cmds.listConnections(
                node,
                source=True,
                destination=False,
                type="animCurve",
            ) or []
        except Exception:
            input_curves = []
        curves.update(str(curve) for curve in input_curves)
    return sorted(curves)


class _Instrumentation:
    """Runtime wrappers for the production route and clear function."""

    def __init__(self, cmds: Any) -> None:
        self.cmds = cmds
        self.active = False
        self.route_plan_wall_ns = 0
        self.clear_route_wall_ns = 0
        self.clear_wall_ns = 0
        self.clear_wall_raw_ns = 0
        self.clear_count = 0
        self.before_clear: Optional[Dict[str, Any]] = None
        self._scope_plugs: Dict[str, int] = {}
        self._scope_plug_curves: Dict[str, Tuple[str, ...]] = {}
        self._scope_curves: Dict[str, int] = {}
        self._pending_layer_curves: Dict[str, int] = {}
        self._pre_clear_inventory_overhead_ns = 0
        self._in_clear_inventory_overhead_ns = 0
        self._originals: List[Tuple[Any, str, Any]] = []

    def _record(self, attribute: str, duration_ns: int) -> None:
        if self.active:
            setattr(self, attribute, int(getattr(self, attribute)) + int(duration_ns))

    def install(self) -> None:
        """Install wrappers and retain exact restoration data."""
        from mmd_tools.converters import vmd_converter
        from mmd_tools.converters.vmd_converter import VmdConverter

        original_plan = vmd_converter.plan_vmd_import_route

        def timed_plan(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter_ns()
            try:
                return original_plan(*args, **kwargs)
            finally:
                self._record("route_plan_wall_ns", time.perf_counter_ns() - started)

        original_route = VmdConverter._build_legacy_bone_key_routes

        def timed_route(instance: Any, *args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter_ns()
            try:
                return original_route(instance, *args, **kwargs)
            finally:
                self._record("clear_route_wall_ns", time.perf_counter_ns() - started)

        original_clear = vmd_converter.clear_existing_motion

        def timed_clear(*args: Any, **kwargs: Any) -> Any:
            if self.active:
                target_model = kwargs.get("target_model")
                if target_model is None and len(args) >= 4:
                    target_model = args[3]
                layer_name = kwargs.get("layer_name")
                if layer_name is None and len(args) >= 2:
                    layer_name = args[1]
                if target_model:
                    self._scope_target_model = str(target_model)
                self._record_layer_curves(layer_name)
            in_clear_overhead_before = self._in_clear_inventory_overhead_ns
            started = time.perf_counter_ns()
            try:
                return original_clear(*args, **kwargs)
            finally:
                if self.active:
                    self.clear_count += 1
                    elapsed = time.perf_counter_ns() - started
                    self.clear_wall_raw_ns += elapsed
                    in_clear_overhead = self._in_clear_inventory_overhead_ns - in_clear_overhead_before
                    self.clear_wall_ns += max(0, elapsed - in_clear_overhead)
                    if layer_name and not self.cmds.objExists(str(layer_name)):
                        self._scope_curves.update(self._pending_layer_curves)
                    self.before_clear = self._scope_inventory()

        from mmd_tools.converters import vmd_import_state

        original_key_cut = vmd_import_state._key_cut_attrs

        def timed_key_cut(node: str, attribute: str) -> Any:
            result = original_key_cut(node, attribute)
            if self.active:
                for target_attr in result:
                    self._record_scope_plug(node, target_attr)
            return result

        self._originals = [
            (vmd_converter, "plan_vmd_import_route", original_plan),
            (VmdConverter, "_build_legacy_bone_key_routes", original_route),
            (vmd_converter, "clear_existing_motion", original_clear),
            (vmd_import_state, "_key_cut_attrs", original_key_cut),
        ]
        vmd_converter.plan_vmd_import_route = timed_plan
        VmdConverter._build_legacy_bone_key_routes = timed_route
        vmd_converter.clear_existing_motion = timed_clear
        vmd_import_state._key_cut_attrs = timed_key_cut

    def restore(self) -> None:
        """Restore production symbols after the worker finishes."""
        for owner, attribute, original in reversed(self._originals):
            setattr(owner, attribute, original)
        self._originals = []

    @contextmanager
    def measure(self, target_model: str) -> Iterator[None]:
        """Measure one replacement import, excluding inventory capture."""
        self.route_plan_wall_ns = 0
        self.clear_route_wall_ns = 0
        self.clear_wall_ns = 0
        self.clear_wall_raw_ns = 0
        self.clear_count = 0
        self.before_clear = None
        self._scope_plugs = {}
        self._scope_plug_curves = {}
        self._scope_curves = {}
        self._pending_layer_curves = {}
        self._scope_target_model = str(target_model)
        self._pre_clear_inventory_overhead_ns = 0
        self._in_clear_inventory_overhead_ns = 0
        self.active = True
        try:
            yield
        finally:
            self.active = False

    def timings(self, replacement_total_raw_wall_ns: int) -> Dict[str, Any]:
        """Return the timing fields for the current replacement import."""
        route_resolution = self.route_plan_wall_ns + self.clear_route_wall_ns
        instrumentation_overhead = (
            self._pre_clear_inventory_overhead_ns + self._in_clear_inventory_overhead_ns
        )
        replacement_total_wall_ns = max(0, int(replacement_total_raw_wall_ns) - instrumentation_overhead)
        residual = replacement_total_wall_ns - route_resolution - self.clear_wall_ns
        return {
            "route_resolution_wall_ns": int(route_resolution),
            "route_plan_wall_ns": int(self.route_plan_wall_ns),
            "clear_route_resolution_wall_ns": int(self.clear_route_wall_ns),
            "clear_wall_ns": int(self.clear_wall_ns),
            "clear_wall_raw_ns": int(self.clear_wall_raw_ns),
            "replacement_total_wall_ns": int(replacement_total_wall_ns),
            "replacement_total_raw_wall_ns": int(replacement_total_raw_wall_ns),
            "instrumentation_overhead_wall_ns": int(instrumentation_overhead),
            "pre_clear_inventory_overhead_wall_ns": int(self._pre_clear_inventory_overhead_ns),
            "in_clear_inventory_overhead_wall_ns": int(self._in_clear_inventory_overhead_ns),
            "post_route_clear_residual_wall_ns": int(residual),
            "clear_call_count": int(self.clear_count),
        }

    def _record_scope_plug(self, node: str, attribute: str) -> None:
        """Record one final plug immediately before production clears it."""
        plug = f"{node}.{attribute}"
        if plug in self._scope_plugs:
            return
        started = time.perf_counter_ns()
        try:
            count = _key_count(self.cmds, plug)
            curves = _anim_curves_for_plug(self.cmds, plug)
            for curve in curves:
                if curve not in self._scope_curves:
                    self._scope_curves[curve] = _key_count(self.cmds, curve)
        finally:
            self._in_clear_inventory_overhead_ns += time.perf_counter_ns() - started
        self._scope_plugs[plug] = count
        self._scope_plug_curves[plug] = tuple(curves)

    def _record_layer_curves(self, layer_name: Any) -> None:
        """Record curves that production may remove by deleting its VMD layer."""
        if not layer_name:
            return
        started = time.perf_counter_ns()
        try:
            curves = self.cmds.animLayer(str(layer_name), query=True, animCurves=True) or []
        except Exception:
            curves = []
        for curve in curves:
            curve_name = str(curve)
            if curve_name not in self._pending_layer_curves:
                self._pending_layer_curves[curve_name] = _key_count(self.cmds, curve_name)
        self._pre_clear_inventory_overhead_ns += time.perf_counter_ns() - started

    def _scope_inventory(self) -> Dict[str, Any]:
        """Return exact clear-scope counts gathered at production boundaries."""
        curve_keys = sum(self._scope_curves.values())
        plug_keys_without_curves = sum(
            count
            for plug, count in self._scope_plugs.items()
            if not self._scope_plug_curves.get(plug)
        )
        return {
            "scope_kind": "production_cut_keyable_attrs_and_vmd_layer_curves",
            "target_model": getattr(self, "_scope_target_model", None),
            "plug_count": len(self._scope_plugs),
            "curve_count": len(self._scope_curves),
            "key_count": int(curve_keys + plug_keys_without_curves),
        }


def _action_error(result: Any, label: str) -> Optional[str]:
    """Convert an Action result into a fail-closed error string."""
    error = getattr(result, "error", None)
    warnings = list(getattr(result, "warnings", ()) or ())
    outcome = str(getattr(result, "outcome", "") or "").casefold()
    if error is not None:
        return f"{label} error: {type(error).__name__}: {error}"
    if not getattr(result, "succeeded", False) or outcome not in {"", "success"}:
        return f"{label} did not succeed: outcome={outcome!r}"
    if warnings:
        return f"{label} emitted warnings: {warnings!r}"
    return None


def _bounded_text(value: Any, *, limit: int = _DIAGNOSTIC_TEXT_LIMIT) -> str:
    """Return one diagnostic string without allowing report-sized payloads."""
    text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "... [truncated]"


def _bounded_warnings(value: Any) -> Dict[str, Any]:
    """Keep warnings useful while preventing arbitrary nested profile payloads."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        warnings = () if value is None else (value,)
    else:
        warnings = value
    serialized = [_bounded_text(warning) for warning in warnings[:_DIAGNOSTIC_WARNING_LIMIT]]
    return {
        "warning_count": len(warnings),
        "warnings": serialized,
        "warnings_truncated": len(warnings) > len(serialized),
    }


def _profile_status_summary(profile: Any) -> Dict[str, Any]:
    """Extract a fixed flat set of scalar profile status fields only.

    Import profiles can contain per-frame data, node inventories, texture rows,
    and other unbounded nested payloads.  The performance report intentionally
    retains none of those diagnostic internals.
    """
    if not isinstance(profile, Mapping):
        return {}
    summary: Dict[str, Any] = {}
    for field in _PROFILE_STATUS_FIELDS:
        current: Any = profile
        for key in field.split("."):
            if not isinstance(current, Mapping) or key not in current:
                break
            current = current[key]
        else:
            if isinstance(current, bool) or current is None:
                summary[field] = current
            elif isinstance(current, int):
                summary[field] = current
            elif isinstance(current, float) and math.isfinite(current):
                summary[field] = current
            elif isinstance(current, str):
                summary[field] = _bounded_text(current)
    return summary


def _action_diagnostics(result: Any, options: Mapping[str, Any]) -> Dict[str, Any]:
    """Return bounded Action outcome data for a failed or partial import."""
    error = getattr(result, "error", None)
    profile = options.get("profile")
    diagnostics = {
        "succeeded": bool(getattr(result, "succeeded", False)),
        "outcome": _bounded_text(getattr(result, "outcome", "") or ""),
        "root_node": _bounded_text(getattr(result, "root_node", "") or ""),
        "error": _bounded_text(repr(error)) if error is not None else None,
        "profile_status": _profile_status_summary(profile),
    }
    diagnostics.update(_bounded_warnings(getattr(result, "warnings", ()) or ()))
    return diagnostics


def _mayapy_warning_entry(line: str, source: str) -> Optional[Dict[str, str]]:
    """Return one explicit warning line, excluding normal Maya announcements."""
    text = str(line).strip()
    if not text:
        return None
    upper = text.upper()
    if "WARNING" not in upper and "警告" not in text:
        return None
    if (
        "WARNING:" not in upper
        and "WARNING :" not in upper
        and "WARNING：" not in upper
        and "警告:" not in text
        and "警告：" not in text
    ):
        return None
    return {"source": source, "warning": _bounded_text(text)}


def _capture_mayapy_output(process: Any) -> Dict[str, Any]:
    """Drain worker pipes while retaining only bounded warning evidence and tails."""
    captured: Dict[str, Any] = {
        "warnings": [],
        "warning_count": 0,
        "warnings_truncated": False,
        "stdout_tail": "",
        "stderr_tail": "",
    }
    lock = threading.Lock()

    def pump(stream: Any, stream_name: str) -> None:
        tail_key = f"{stream_name}_tail"
        source = f"mayapy_{stream_name}"
        while True:
            line = stream.readline(4_096)
            if not line:
                break
            entry = _mayapy_warning_entry(line, source)
            with lock:
                captured[tail_key] = (captured[tail_key] + line)[-_MAYAPY_OUTPUT_TAIL_LIMIT:]
                if entry is not None:
                    captured["warning_count"] += 1
                    if len(captured["warnings"]) < _MAYAPY_WARNING_LINE_LIMIT:
                        captured["warnings"].append(entry)
                    else:
                        captured["warnings_truncated"] = True

    threads = [
        threading.Thread(target=pump, args=(process.stdout, "stdout"), daemon=True),
        threading.Thread(target=pump, args=(process.stderr, "stderr"), daemon=True),
    ]
    for thread in threads:
        thread.start()
    captured["threads"] = threads
    return captured


def _finish_mayapy_output_capture(captured: Mapping[str, Any]) -> None:
    """Join bounded pipe drainers after the Maya worker exits."""
    for thread in captured.get("threads", ()):
        thread.join()


def _run_mayapy_worker(command: Sequence[str], **kwargs: Any) -> Tuple[int, Dict[str, Any]]:
    """Run mayapy with bounded output retention while draining both pipes."""
    timeout = float(kwargs.pop("timeout"))
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **kwargs,
    )
    captured = _capture_mayapy_output(process)
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise
    finally:
        _finish_mayapy_output_capture(captured)
    captured.pop("threads", None)
    return int(returncode), captured


def _apply_mayapy_warnings(report: Dict[str, Any], captured: Mapping[str, Any]) -> None:
    """Make captured worker warnings fail the threshold decision closed."""
    warnings = list(captured.get("warnings") or ())
    if not warnings:
        return
    report["mayapy_output"] = {
        "warning_count": int(captured.get("warning_count", len(warnings))),
        "warnings_truncated": bool(captured.get("warnings_truncated")),
    }
    report.setdefault("warnings", []).extend(warnings)
    if captured.get("warnings_truncated"):
        report["warnings"].append(
            {
                "source": "mayapy_output",
                "warning": "additional mayapy warning lines were truncated",
            }
        )
    _recompute_report_decision(report)


def _safe_console_text(value: Any, stream: Any) -> str:
    """Make bounded diagnostic text encodable by the destination console."""
    text = _bounded_text(value, limit=_MAYAPY_OUTPUT_TAIL_LIMIT)
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
    except (LookupError, UnicodeError):
        try:
            return text.encode(encoding, errors="backslashreplace").decode(encoding, errors="replace")
        except (LookupError, UnicodeError):
            return text.encode("ascii", errors="backslashreplace").decode("ascii")
    return text


def _safe_console_print(value: Any, stream: Any) -> None:
    """Best-effort bounded console output that never affects the probe gate."""
    try:
        print(_safe_console_text(value, stream), file=stream)
        return
    except Exception:
        pass
    try:
        buffer = getattr(stream, "buffer", None)
        if buffer is not None:
            payload = str(value).encode("ascii", errors="backslashreplace")
            buffer.write(payload + b"\n")
            buffer.flush()
    except Exception:
        pass


def _replay_mayapy_output(version: str, returncode: int, captured: Mapping[str, Any]) -> None:
    """Print captured diagnostics without allowing console encoding to abort the gate."""
    try:
        warnings = list(captured.get("warnings") or ())
    except Exception:
        warnings = []
    if warnings:
        _safe_console_print(
            f"[vmd-clear-performance] Maya {version} captured warning lines:", sys.stdout
        )
        for entry in warnings:
            try:
                source = entry["source"]
                warning = entry["warning"]
            except Exception:
                source = "mayapy_output"
                warning = entry
            _safe_console_print(f"  [{source}] {warning}", sys.stdout)
        if captured.get("warnings_truncated"):
            _safe_console_print("  [mayapy_output] additional warning lines truncated", sys.stdout)
    if returncode:
        tail = str(captured.get("stderr_tail") or captured.get("stdout_tail") or "").strip()
        if tail:
            _safe_console_print(
                f"[vmd-clear-performance] Maya {version} output tail:\n{tail}", sys.stderr
            )


def _worker_options(config: Mapping[str, Any], root: str, clear: bool) -> Dict[str, Any]:
    options = dict(config.get("options") or {})
    options.update(
        {
            "target_model": root,
            "pmx_path": str(Path(config["pmx_path"]).resolve()),
            "clear_existing_motion": bool(clear),
            "profile": {},
        }
    )
    return options


def _configure_disposable_workspace(cmds: Any, config: Mapping[str, Any]) -> None:
    """Create the Maya project used by this worker inside its disposable app dir."""
    workspace = Path(config["maya_app_dir"]) / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    cmds.workspace(str(workspace), newWorkspace=True)


def _run_worker(config: Mapping[str, Any]) -> Dict[str, Any]:
    """Run one version's Maya-side seed and replacement measurements."""
    import maya.cmds as cmds
    from mmd_tools.actions.import_model_action import ImportModelAction, ImportModelRequest
    from mmd_tools.actions.import_vmd_action import ImportVmdAction, ImportVmdRequest

    report: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "not_run",
        "maya_version": str(cmds.about(version=True)),
        "requested_maya_version": str(config["maya_version"]),
        "candidate_sha": str(config.get("candidate_sha", "unknown")),
        "git_provenance_start": config.get("git_provenance_start"),
        "fixture": _fixture_provenance(config),
        "options": dict(config.get("options") or {}),
        "warmup_runs": int(config["warmup"]),
        "requested_measured_runs": int(config["runs"]),
        "runs": [],
        "warnings": [],
        "errors": [],
        "not_run": [],
    }
    instrumentation = _Instrumentation(cmds)
    try:
        cmds.file(new=True, force=True)
        _configure_disposable_workspace(cmds, config)
        if not PYTHON_PLUGIN.is_file():
            raise FileNotFoundError(f"Python plugin does not exist: {PYTHON_PLUGIN}")
        cmds.loadPlugin(str(PYTHON_PLUGIN), quiet=True)

        model_options = _base_options()
        model_options["profile"] = {}
        model_result = ImportModelAction().execute(
            ImportModelRequest(
                file_path=str(Path(config["pmx_path"]).resolve()),
                options=model_options,
                create_new_scene=False,
            )
        )
        report["model_import"] = _action_diagnostics(model_result, model_options)
        model_warnings = list(getattr(model_result, "warnings", ()) or ())
        if not getattr(model_result, "succeeded", False) or not getattr(model_result, "root_node", None):
            model_error = _action_error(model_result, "ImportModelAction")
            raise RuntimeError(model_error or "ImportModelAction returned no model root")
        if model_warnings:
            # A partial model import can still provide a usable animation target
            # (the sour_black fixture has missing texture files).  Preserve the
            # warning and keep the threshold gate fail-closed below.
            report["warnings"].extend(
                {"source": "model_import", "warning": warning} for warning in model_warnings
            )
        root = _canonical_root(cmds, getattr(model_result, "root_node", None))
        report["target_model"] = root

        vmd_path = str(Path(config["vmd_path"]).resolve())
        action = ImportVmdAction()
        seed_options = _worker_options(config, root, False)
        seed_result = action.execute(
            ImportVmdRequest(file_path=vmd_path, options=seed_options, create_new_scene=False)
        )
        report["seed_import"] = _action_diagnostics(seed_result, seed_options)
        seed_error = _action_error(seed_result, "seed ImportVmdAction")
        if seed_error:
            if getattr(seed_result, "succeeded", False) and list(getattr(seed_result, "warnings", ()) or ()):
                report["warnings"].extend(
                    {"source": "seed_import", "warning": warning}
                    for warning in list(getattr(seed_result, "warnings", ()) or ())
                )
            else:
                raise RuntimeError(seed_error)
        report["seed_import"].update(
            {
                "status": "partial" if getattr(seed_result, "warnings", ()) else "pass",
                "clear_existing_motion": False,
            }
        )

        instrumentation.install()
        try:
            for index in range(int(config["warmup"]) + int(config["runs"])):
                is_warmup = index < int(config["warmup"])
                row: Dict[str, Any] = {
                    "index": index,
                    "warmup": is_warmup,
                    "clear_existing_motion": True,
                    "status": "not_run",
                    "warnings": [],
                    "errors": [],
                }
                started = time.perf_counter_ns()
                try:
                    replacement_options = _worker_options(config, root, True)
                    with instrumentation.measure(root):
                        result = action.execute(
                            ImportVmdRequest(
                                file_path=vmd_path,
                                options=replacement_options,
                                create_new_scene=False,
                            )
                        )
                    total = time.perf_counter_ns() - started
                    row["timings"] = instrumentation.timings(total)
                    row["before_clear"] = instrumentation.before_clear
                    row["action"] = _action_diagnostics(result, replacement_options)
                    row["warnings"] = list(getattr(result, "warnings", ()) or ())
                    if row["warnings"]:
                        report["warnings"].extend(
                            {"source": f"replacement_run_{index}", "warning": warning}
                            for warning in row["warnings"]
                        )
                    error = _action_error(result, "replacement ImportVmdAction")
                    if error:
                        row["errors"].append(error)
                        row["status"] = "failed"
                    elif instrumentation.clear_count != 1:
                        row["errors"].append(
                            f"expected exactly one clear_existing_motion call, got {instrumentation.clear_count}"
                        )
                        row["status"] = "failed"
                    elif instrumentation.before_clear is None:
                        row["errors"].append("clear preimage metrics were not observed")
                        row["status"] = "failed"
                    else:
                        row["status"] = "measured"
                except Exception as exc:
                    row["errors"].append(f"{type(exc).__name__}: {exc}")
                    row["traceback"] = traceback.format_exc()
                    row["status"] = "failed"
                report["runs"].append(row)
                if row["status"] != "measured":
                    report["errors"].extend(row["errors"])
                    if not is_warmup:
                        report["not_run"].append(
                            f"stopped after replacement run {index} failed; later runs were not attempted"
                        )
                        break
        finally:
            instrumentation.restore()
        decision = threshold_decision(
            report["runs"],
            warnings=report["warnings"],
            errors=report["errors"],
            not_run=report["not_run"],
        )
        report["summary"] = summarize_timings(report["runs"])
        report["threshold_decision"] = decision
        report["status"] = "pass" if decision["decision"] == "proceed" else decision["status"]
        return report
    except Exception as exc:
        report["errors"].append(f"{type(exc).__name__}: {exc}")
        report["traceback"] = traceback.format_exc()
        report["summary"] = summarize_timings(report["runs"])
        report["threshold_decision"] = threshold_decision(
            report["runs"], errors=report["errors"], not_run=report["not_run"]
        )
        report["status"] = "not_run"
        return report


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write one deterministic UTF-8 JSON artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_worker(config_path: Path) -> int:
    """Initialize Maya, execute the worker, and always leave a report."""
    config = load_config(config_path)
    os.environ.setdefault("MAYA_APP_DIR", str(config["maya_app_dir"]))
    output = Path(config["out_path"])
    report: Optional[Dict[str, Any]] = None
    import maya.standalone

    maya.standalone.initialize(name="python")
    try:
        report = _run_worker(config)
    except Exception as exc:
        report = {
            "schema_version": SCHEMA_VERSION,
            "status": "not_run",
            "maya_version": str(config["maya_version"]),
            "requested_maya_version": str(config["maya_version"]),
            "candidate_sha": str(config.get("candidate_sha", "unknown")),
            "fixture": _fixture_provenance(config),
            "runs": [],
            "warnings": [],
            "errors": [f"{type(exc).__name__}: {exc}"],
            "not_run": ["Maya worker initialization failed"],
            "traceback": traceback.format_exc(),
        }
    finally:
        try:
            maya.standalone.uninitialize()
        except Exception:
            pass
    _write_json(output, report or {"status": "not_run", "errors": ["worker did not produce a report"]})
    return 0


_GIT_PROVENANCE_ENTRY_LIMIT = 128
_GIT_STREAM_CHUNK_SIZE = 64 * 1024
_GIT_STATUS_RECORD_LIMIT = 4 * 1024


def _stream_git_stdout(
    command: Sequence[str], consume: Optional[Callable[[bytes], None]] = None
) -> str:
    """Hash git stdout incrementally without retaining command output."""
    process = subprocess.Popen(
        command,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    digest = hashlib.sha256()
    try:
        if process.stdout is None:
            raise OSError("git stdout pipe was not created")
        while True:
            chunk = process.stdout.read(_GIT_STREAM_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            if consume is not None:
                consume(chunk)
        returncode = process.wait()
    except BaseException:
        try:
            process.kill()
        except OSError:
            pass
        process.wait()
        raise
    if returncode:
        raise subprocess.CalledProcessError(returncode, command)
    return digest.hexdigest()


def _stream_git_status(command: Sequence[str]) -> Dict[str, Any]:
    """Hash NUL-delimited status output while retaining only 128 entries."""
    status_entries: List[str] = []
    target_paths: List[str] = []
    untracked_digest = hashlib.sha256()
    pending = bytearray()
    skip_until_nul = False
    entry_count = 0

    def record(raw_record: bytes) -> None:
        nonlocal entry_count
        entry_count += 1
        if len(status_entries) < _GIT_PROVENANCE_ENTRY_LIMIT:
            status_entries.append(
                _bounded_text(
                    raw_record.decode("utf-8", errors="replace"), limit=_GIT_STATUS_RECORD_LIMIT
                )
            )
        path_bytes = raw_record[3:] if len(raw_record) >= 3 and raw_record[2:3] == b" " else raw_record
        path_text = path_bytes.decode("utf-8", errors="replace")
        if path_text and len(target_paths) < _GIT_PROVENANCE_ENTRY_LIMIT:
            target_paths.append(_bounded_text(path_text, limit=_GIT_STATUS_RECORD_LIMIT))
        if raw_record.startswith(b"?? "):
            untracked_digest.update(path_bytes)
            untracked_digest.update(b"\0")
            path = ROOT / Path(path_text.replace("/", os.sep))
            if path.is_file():
                untracked_digest.update(_sha256_file(path).encode("ascii"))
            else:
                untracked_digest.update(b"missing")

    def consume(chunk: bytes) -> None:
        nonlocal pending, skip_until_nul
        if skip_until_nul:
            marker = chunk.find(b"\0")
            if marker < 0:
                return
            skip_until_nul = False
            chunk = chunk[marker + 1 :]
        if not chunk:
            return
        pending.extend(chunk)
        while True:
            marker = pending.find(0)
            if marker < 0:
                if len(pending) > _GIT_STATUS_RECORD_LIMIT:
                    record(b"<oversized status record>")
                    pending.clear()
                    skip_until_nul = True
                return
            record(bytes(pending[:marker]))
            del pending[: marker + 1]

    status_sha256 = _stream_git_stdout(command, consume)
    if pending and not skip_until_nul:
        record(bytes(pending))
    return {
        "sha256": status_sha256,
        "status_entries": status_entries,
        "status_entries_truncated": entry_count > _GIT_PROVENANCE_ENTRY_LIMIT,
        "target_paths": target_paths,
        "target_paths_truncated": entry_count > _GIT_PROVENANCE_ENTRY_LIMIT,
        "status_entry_count": entry_count,
        "untracked_sha256": untracked_digest.hexdigest(),
    }


def _git_worktree_provenance() -> Dict[str, Any]:
    """Return a bounded fingerprint for the current candidate worktree."""
    try:
        status = _stream_git_status(
            ["git", "-c", "core.quotePath=false", "status", "--porcelain=v1", "-z", "--untracked-files=all"]
        )
        working_tree_diff_sha256 = _stream_git_stdout(
            ["git", "diff", "--no-ext-diff", "--binary", "HEAD", "--"]
        )
        head_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        diff_digest = hashlib.sha256()
        diff_digest.update(str(status["sha256"]).encode("ascii"))
        diff_digest.update(b"\0")
        diff_digest.update(working_tree_diff_sha256.encode("ascii"))
        diff_digest.update(b"\0")
        diff_digest.update(str(status["untracked_sha256"]).encode("ascii"))
        return {
            "available": True,
            "status": "dirty" if status["status_entry_count"] else "clean",
            "status_entry_count": int(status["status_entry_count"]),
            "status_entries": status["status_entries"],
            "status_entries_truncated": bool(status["status_entries_truncated"]),
            "target_paths": status["target_paths"],
            "target_paths_truncated": bool(status["target_paths_truncated"]),
            "status_sha256": status["sha256"],
            "working_tree_diff_sha256": working_tree_diff_sha256,
            "diff_sha256": diff_digest.hexdigest(),
            "head_sha": str(head_result.stdout or "").strip(),
        }
    except (OSError, subprocess.CalledProcessError) as exc:
        return {
            "available": False,
            "status": "unavailable",
            "status_entry_count": 0,
            "status_entries": [],
            "target_paths": [],
            "diff_sha256": None,
            "head_sha": None,
            "error": _bounded_text(f"{type(exc).__name__}: {exc}"),
        }


def _git_provenance_identity(provenance: Mapping[str, Any]) -> Tuple[Any, ...]:
    """Select stable fields used to detect a candidate change."""
    return (
        bool(provenance.get("available")),
        str(provenance.get("status", "")),
        int(provenance.get("status_entry_count", 0)),
        tuple(provenance.get("target_paths") or ()),
        str(provenance.get("diff_sha256", "")),
        str(provenance.get("head_sha", "")),
    )


def _recompute_report_decision(report: Dict[str, Any]) -> None:
    """Refresh summary/status after a host-side fail-closed finding."""
    report["summary"] = summarize_timings(report.get("runs") or ())
    decision = threshold_decision(
        report.get("runs") or (),
        warnings=report.get("warnings") or (),
        errors=report.get("errors") or (),
        not_run=report.get("not_run") or (),
    )
    report["threshold_decision"] = decision
    report["status"] = "pass" if decision["decision"] == "proceed" else decision["status"]


def _apply_mayapy_returncode(report: Dict[str, Any], version: str, returncode: int) -> None:
    """Make a nonzero worker exit an explicit report blocker."""
    if int(returncode) == 0:
        return
    report.setdefault("errors", []).append(
        f"Maya {version} mayapy worker exited with code {int(returncode)}"
    )
    report.setdefault("not_run", []).append("mayapy worker exited nonzero")
    _recompute_report_decision(report)


def _apply_git_provenance(
    report: Dict[str, Any], start: Mapping[str, Any], end: Mapping[str, Any]
) -> None:
    """Record start/end candidate identity and block changed/unavailable runs."""
    changed = _git_provenance_identity(start) != _git_provenance_identity(end)
    report["git_provenance"] = {"start": dict(start), "end": dict(end), "changed": changed}
    unavailable = not start.get("available") or not end.get("available")
    if unavailable:
        report.setdefault("errors", []).append("git worktree provenance was unavailable")
        report.setdefault("not_run", []).append("candidate provenance unavailable")
    elif changed:
        report.setdefault("errors", []).append("git worktree provenance changed during probe")
        report.setdefault("not_run", []).append("candidate changed during probe")
    if changed or unavailable:
        _recompute_report_decision(report)


def _candidate_sha() -> str:
    """Read the current candidate SHA without mutating repository state."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def _host_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--config", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--maya", action="append", dest="maya_versions", default=None)
    parser.add_argument("--pmx", type=Path, default=DEFAULT_PMX)
    parser.add_argument("--vmd", type=Path, default=DEFAULT_VMD)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--warmup", type=int, default=DEFAULT_WARMUP)
    parser.add_argument("--timeout", type=float, default=900.0)
    return parser.parse_args(argv)


def _host_config(
    args: argparse.Namespace,
    version: str,
    output: Path,
    git_provenance_start: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Build one version configuration and its provenance metadata."""
    pmx = args.pmx.expanduser().resolve()
    vmd = args.vmd.expanduser().resolve()
    provenance = dict(git_provenance_start or _git_worktree_provenance())
    candidate_sha = str(provenance.get("head_sha") or "unknown")
    return {
        "schema_version": SCHEMA_VERSION,
        "maya_version": str(version),
        "pmx_path": str(pmx),
        "vmd_path": str(vmd),
        "out_path": str(output.resolve()),
        "maya_app_dir": str((args.out_dir / f"maya-app-{version}").resolve()),
        "runs": int(args.runs),
        "warmup": int(args.warmup),
        "candidate_sha": candidate_sha,
        "git_provenance_start": provenance,
        "options": _base_options(),
    }


def _maya_executable(version: str) -> Path:
    """Resolve mayapy using the repository's shared Maya location helper."""
    from tests.common.maya_location import mayapy

    return mayapy(version)


def run_host(args: argparse.Namespace) -> int:
    """Launch one worker per requested Maya version and write a summary."""
    versions = tuple(args.maya_versions or DEFAULT_MAYA_VERSIONS)
    if args.runs < MIN_MEASURED_RUNS:
        raise ProbeConfigurationError(f"runs must be at least {MIN_MEASURED_RUNS}")
    if args.warmup < 1:
        raise ProbeConfigurationError("warmup must be at least 1")
    if args.timeout <= 0.0 or not math.isfinite(float(args.timeout)):
        raise ProbeConfigurationError("timeout must be positive and finite")
    if not args.pmx.is_file() or not args.vmd.is_file():
        raise ProbeConfigurationError(f"fixture does not exist: pmx={args.pmx} vmd={args.vmd}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    reports: Dict[str, Any] = {}
    git_provenance_start = _git_worktree_provenance()
    for version in versions:
        report_path = args.out_dir / f"maya-{version}.json"
        config_path = args.out_dir / f"config-{version}.json"
        config = _host_config(args, str(version), report_path, git_provenance_start)
        _write_json(config_path, config)
        try:
            report_path.unlink()
        except FileNotFoundError:
            pass
        env = dict(os.environ)
        env.update(
            {
                "MAYA_APP_DIR": str(args.out_dir / f"maya-app-{version}"),
                "MAYA_SKIP_USERSETUP_PY": "1",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONPATH": str(ROOT),
            }
        )
        returncode: Optional[int] = None
        captured_output: Dict[str, Any] = {"warnings": []}
        try:
            executable = _maya_executable(str(version))
            returncode, captured_output = _run_mayapy_worker(
                [str(executable), str(Path(__file__).resolve()), "--worker", "--config", str(config_path.resolve())],
                cwd=str(ROOT),
                env=env,
                timeout=float(args.timeout),
            )
            if not report_path.is_file():
                _write_json(
                    report_path,
                    {
                        "schema_version": SCHEMA_VERSION,
                        "status": "not_run",
                        "requested_maya_version": str(version),
                        "candidate_sha": config["candidate_sha"],
                        "fixture": {
                            "pmx_path": str(args.pmx.resolve()),
                            "vmd_path": str(args.vmd.resolve()),
                        },
                        "runs": [],
                        "warnings": [],
                        "errors": ["mayapy worker report missing"],
                        "not_run": ["worker report missing"],
                    },
                )
        except subprocess.TimeoutExpired as exc:
            _write_json(
                report_path,
                {
                    "schema_version": SCHEMA_VERSION,
                    "status": "not_run",
                    "requested_maya_version": str(version),
                    "candidate_sha": config["candidate_sha"],
                    "fixture": {
                        "pmx_path": str(args.pmx.resolve()),
                        "vmd_path": str(args.vmd.resolve()),
                    },
                    "runs": [],
                    "warnings": [],
                    "errors": [f"mayapy worker timed out after {args.timeout} seconds: {exc}"],
                    "not_run": ["worker timeout"],
                },
            )
        except (OSError, ProbeConfigurationError) as exc:
            _write_json(
                report_path,
                {
                    "schema_version": SCHEMA_VERSION,
                    "status": "not_run",
                    "requested_maya_version": str(version),
                    "candidate_sha": config["candidate_sha"],
                    "fixture": {
                        "pmx_path": str(args.pmx.resolve()),
                        "vmd_path": str(args.vmd.resolve()),
                    },
                    "runs": [],
                    "warnings": [],
                    "errors": [f"could not launch Maya {version}: {type(exc).__name__}: {exc}"],
                    "not_run": ["Maya executable unavailable"],
                },
            )
        try:
            worker_report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            reports[str(version)] = {
                "status": "not_run",
                "errors": [f"invalid worker report: {type(exc).__name__}: {exc}"],
                "not_run": ["worker report could not be read"],
            }
        else:
            _apply_mayapy_warnings(worker_report, captured_output)
            if returncode is not None:
                _apply_mayapy_returncode(worker_report, str(version), returncode)
            _apply_git_provenance(
                worker_report,
                config.get("git_provenance_start") or git_provenance_start,
                _git_worktree_provenance(),
            )
            _write_json(report_path, worker_report)
            reports[str(version)] = worker_report
        if returncode is not None:
            _replay_mayapy_output(str(version), returncode, captured_output)
    git_provenance_end = _git_worktree_provenance()
    final_provenance_changed = _git_provenance_identity(git_provenance_start) != _git_provenance_identity(
        git_provenance_end
    )
    final_provenance_unavailable = not git_provenance_start.get("available") or not git_provenance_end.get(
        "available"
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "status": "pass" if reports and all(report.get("status") == "pass" for report in reports.values()) else "not_run",
        "candidate_sha": str(git_provenance_start.get("head_sha") or "unknown"),
        "git_provenance": {
            "start": git_provenance_start,
            "end": git_provenance_end,
            "changed": final_provenance_changed,
        },
        "fixture": {
            "pmx_path": str(args.pmx.resolve()),
            "pmx_sha256": _sha256_file(args.pmx.resolve()),
            "vmd_path": str(args.vmd.resolve()),
            "vmd_sha256": _sha256_file(args.vmd.resolve()),
        },
        "maya_versions": list(versions),
        "mayapy_warnings": {
            version: [
                warning
                for warning in report.get("warnings") or ()
                if isinstance(warning, Mapping)
                and str(warning.get("source", "")).startswith("mayapy_")
            ]
            for version, report in reports.items()
        },
        "reports": reports,
    }
    if final_provenance_unavailable:
        summary["status"] = "not_run"
        summary["errors"] = ["git worktree provenance was unavailable at probe boundary"]
        summary["not_run"] = ["candidate provenance unavailable"]
    elif final_provenance_changed:
        summary["status"] = "not_run"
        summary["errors"] = ["git worktree provenance changed after per-report validation"]
        summary["not_run"] = ["candidate changed before summary finalization"]
    _write_json(args.out_dir / "summary.json", summary)
    return 0 if summary["status"] == "pass" else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run either the host controller or one Maya worker."""
    args = _host_args(argv)
    if args.worker:
        if args.config is None:
            raise ProbeConfigurationError("--worker requires --config")
        return run_worker(args.config)
    return run_host(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProbeConfigurationError as exc:
        print(f"vmd-clear-performance-probe: {exc}", file=sys.stderr)
        raise SystemExit(2)
