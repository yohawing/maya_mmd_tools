"""Measure Maya process memory across repeated physics imports.

This probe is the evidence surface for ``PHS-ISSUE-110-1``.  Each iteration
performs the production equivalent of ``Import With New Scene`` (new scene,
physics-enabled PMX import, then another new scene), recording Windows
Working Set and Private Bytes before import, after import, and after cleanup.
The process counters come from ``GetProcessMemoryInfo`` via ``ctypes``; no
``psutil`` dependency is required.  Missing counters or an incomplete cycle
fail closed in the JSON report.

Usage (inside Maya's Python interpreter)::

    mayapy tests/viewport/physics_import_memory_probe.py --maya 2024
"""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence


DEFAULT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PMX = "build/fixtures/kotora_ascii/kotora.pmx"
DEFAULT_REPORT = "build/reports/physics_import_memory_probe.json"
DEFAULT_ITERATIONS = 3
MEMORY_ABSOLUTE_THRESHOLD_BYTES = 64 * 1024 * 1024
MEMORY_RELATIVE_THRESHOLD = 0.10
HEALTHY_SOLVER_STATUSES = {"reset", "stepped", "cached", "pose-updated"}


class _ProcessMemoryCountersEx(ctypes.Structure):
    """Windows ``PROCESS_MEMORY_COUNTERS_EX`` layout."""

    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", default="2024", help="Maya version label for evidence metadata.")
    parser.add_argument("--pmx", default=DEFAULT_PMX, help="Physics-enabled PMX fixture to import.")
    parser.add_argument(
        "--iterations",
        type=int,
        default=DEFAULT_ITERATIONS,
        help=f"Number of new-scene/import cycles (default: {DEFAULT_ITERATIONS}).",
    )
    parser.add_argument("--out", default=DEFAULT_REPORT, help="JSON report path.")
    return parser.parse_args()


def _resolve_path(value: str, root: Path = DEFAULT_ROOT) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def _memory_snapshot() -> Dict[str, Any]:
    """Return current process counters, or explicit unavailable evidence."""
    result: Dict[str, Any] = {
        "available": False,
        "platform": sys.platform,
        "workingSetBytes": None,
        "privateBytes": None,
        "peakWorkingSetBytes": None,
        "error": None,
    }
    if sys.platform != "win32":
        result["error"] = "Windows GetProcessMemoryInfo is unavailable on this platform"
        return result
    try:
        psapi = ctypes.WinDLL("psapi")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_current_process = kernel32.GetCurrentProcess
        get_current_process.restype = wintypes.HANDLE
        get_process_memory_info = psapi.GetProcessMemoryInfo
        get_process_memory_info.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_ProcessMemoryCountersEx),
            wintypes.DWORD,
        ]
        get_process_memory_info.restype = wintypes.BOOL
        counters = _ProcessMemoryCountersEx()
        counters.cb = ctypes.sizeof(counters)
        handle = get_current_process()
        if not get_process_memory_info(handle, ctypes.byref(counters), counters.cb):
            error_code = ctypes.get_last_error()
            raise OSError(error_code, "GetProcessMemoryInfo failed")
    except Exception as exc:
        result["error"] = str(exc)
        return result
    result.update(
        {
            "available": True,
            "workingSetBytes": int(counters.WorkingSetSize),
            "privateBytes": int(counters.PrivateUsage),
            "peakWorkingSetBytes": int(counters.PeakWorkingSetSize),
        }
    )
    return result


def _memory_sample(label: str, cycle: int | None = None) -> Dict[str, Any]:
    sample = _memory_snapshot()
    sample["label"] = label
    if cycle is not None:
        sample["cycle"] = cycle
    return sample


def _memory_available(samples: Sequence[Mapping[str, Any]]) -> bool:
    return bool(samples) and all(
        sample.get("available") is True
        and isinstance(sample.get("workingSetBytes"), int)
        and isinstance(sample.get("privateBytes"), int)
        and sample.get("workingSetBytes", -1) >= 0
        and sample.get("privateBytes", -1) >= 0
        for sample in samples
    )


def _baseline_gate(samples: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Fail closed unless post-new-scene baselines are available and stable."""
    private_values = [sample.get("privateBytes") for sample in samples]
    working_values = [sample.get("workingSetBytes") for sample in samples]
    errors: List[str] = []
    if len(samples) < DEFAULT_ITERATIONS:
        errors.append(
            f"at least {DEFAULT_ITERATIONS} post-new-scene memory samples are required; "
            f"got {len(samples)}"
        )
    if not _memory_available(samples):
        errors.append("one or more post-new-scene memory samples are unavailable or invalid")

    def metric_gate(values: List[Any], name: str) -> Dict[str, Any]:
        if not values or any(not isinstance(value, int) for value in values):
            return {
                "metric": name,
                "valuesBytes": values,
                "baselineDeltaBytes": None,
                "allowedDeltaBytes": None,
                "monotonicNonDecreasing": None,
                "largeMonotonicIncrease": None,
                "passed": False,
            }
        start = values[0]
        end = values[-1]
        delta = end - start
        allowed = max(MEMORY_ABSOLUTE_THRESHOLD_BYTES, int(max(start, 1) * MEMORY_RELATIVE_THRESHOLD))
        monotonic = all(current <= following for current, following in zip(values, values[1:]))
        large_net_increase = delta > allowed
        large_monotonic_increase = monotonic and large_net_increase
        return {
            "metric": name,
            "valuesBytes": values,
            "baselineDeltaBytes": delta,
            "allowedDeltaBytes": allowed,
            "absoluteThresholdBytes": MEMORY_ABSOLUTE_THRESHOLD_BYTES,
            "relativeThreshold": MEMORY_RELATIVE_THRESHOLD,
            "monotonicNonDecreasing": monotonic,
            "largeNetIncrease": large_net_increase,
            "largeMonotonicIncrease": large_monotonic_increase,
            "passed": not large_net_increase,
        }

    metrics = [metric_gate(private_values, "privateBytes"), metric_gate(working_values, "workingSetBytes")]
    errors.extend(
        f"{metric['metric']}: monotonic baseline increased by {metric['baselineDeltaBytes']} bytes "
        f"(allowed {metric['allowedDeltaBytes']})"
        for metric in metrics
        if metric.get("largeNetIncrease")
    )
    return {
        "status": "pass" if not errors and all(metric.get("passed") is True for metric in metrics) else "fail",
        "samples": [_json_safe(sample) for sample in samples],
        "metrics": metrics,
        "errors": errors,
    }


def _load_plugin(repo_root: Path) -> None:
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from tests.common.maya_plugin_setup import load_mmd_tools_plugin

    load_mmd_tools_plugin(repo_root)


def _import_model(pmx: Path) -> str:
    from mmd_tools.io.mmd_importer import import_mmd_file

    root = import_mmd_file(
        str(pmx),
        options={
            "use_namespace": True,
            "setup_rig": True,
            "import_physics": True,
            "create_mmd_shaders": False,
            "use_native_pmx_parse": False,
            "require_native_pmx_parse": False,
        },
    )
    if not root:
        raise RuntimeError(f"PMX import failed: {pmx}")
    return str(root)


def _solver_state(cmds: Any, root: str) -> Dict[str, Any]:
    """Force solver output evaluation and capture status/count health."""
    try:
        solvers = [
            str(node)
            for node in (
                cmds.listConnections(
                    f"{root}.message",
                    source=False,
                    destination=True,
                    type="mmdPhysicsSolver",
                )
                or []
            )
        ]
    except Exception:
        solvers = []
    values: Dict[str, Any] = {}
    healthy = bool(solvers)
    for solver in solvers:
        row: Dict[str, Any] = {
            "outSolved": None,
            "outStatus": None,
            "outBoneCount": None,
            "error": None,
        }
        try:
            row["outSolved"] = bool(cmds.getAttr(f"{solver}.outSolved"))
            row["outStatus"] = str(cmds.getAttr(f"{solver}.outStatus"))
            row["outBoneCount"] = int(cmds.getAttr(f"{solver}.outBoneCount"))
            healthy = (
                healthy
                and row["outSolved"] is True
                and row["outStatus"] in HEALTHY_SOLVER_STATUSES
                and row["outBoneCount"] > 0
            )
        except Exception as exc:
            row["error"] = str(exc)
            healthy = False
        values[solver] = row
    return {"solverCount": len(solvers), "healthy": healthy, "solvers": values}


def _physics_state(cmds: Any, root: str, *, force_enable: bool = False) -> Dict[str, Any]:
    worlds = [str(node) for node in (cmds.ls(type="mmdPhysicsWorldShape", long=True) or [])]
    values: Dict[str, Any] = {}
    enabled = True
    for world in worlds:
        if not cmds.attributeQuery("enable", node=world, exists=True):
            enabled = False
            values[world] = {"enable": None, "error": "enable attribute is missing"}
            continue
        try:
            value = bool(cmds.getAttr(f"{world}.enable"))
            if force_enable and not value:
                cmds.setAttr(f"{world}.enable", True)
                cmds.refresh(force=True)
                value = bool(cmds.getAttr(f"{world}.enable"))
            values[world] = {"enable": value}
            enabled = enabled and value
        except Exception as exc:
            values[world] = {"enable": None, "error": str(exc)}
            enabled = False
    solver = _solver_state(cmds, root)
    return {
        "worldCount": len(worlds),
        "enabled": bool(worlds) and enabled,
        "worlds": values,
        "solver": solver,
        "initialized": bool(worlds) and enabled and solver["healthy"],
    }


def _run(args: argparse.Namespace) -> Dict[str, Any]:
    import maya.cmds as cmds

    pmx = _resolve_path(args.pmx)
    report: Dict[str, Any] = {
        "status": "error",
        "probe": "PHS-ISSUE-110-1",
        "mayaRequested": str(args.maya),
        "mayaVersion": None,
        "pmx": str(pmx),
        "iterationsRequested": int(args.iterations),
        "memoryApi": "Windows GetProcessMemoryInfo via ctypes",
        "cycles": [],
        "baselineGate": {},
        "errors": [],
    }
    try:
        report["mayaVersion"] = str(cmds.about(version=True))
    except Exception as exc:
        report["errors"].append(f"maya version query: {exc}")
    if args.iterations < DEFAULT_ITERATIONS:
        report["errors"].append(f"--iterations must be at least {DEFAULT_ITERATIONS}")
        return report
    if not pmx.is_file():
        report["errors"].append(f"PMX fixture not found: {pmx}")
        return report

    baseline_samples: List[Dict[str, Any]] = []
    try:
        report["initialMemory"] = _memory_sample("initial")
        for cycle in range(1, int(args.iterations) + 1):
            row: Dict[str, Any] = {"cycle": cycle, "status": "error", "errors": []}
            try:
                cmds.file(new=True, force=True)
                row["importBefore"] = _memory_sample("importBefore", cycle)
                root = _import_model(pmx)
                row["modelRoot"] = root
                row["physicsAfterImport"] = _physics_state(cmds, root, force_enable=True)
                row["importAfter"] = _memory_sample("importAfter", cycle)
                if not row["physicsAfterImport"].get("enabled"):
                    row["errors"].append("physics world is not enabled after import")
                if not row["physicsAfterImport"].get("initialized"):
                    row["errors"].append(
                        "physics solver did not produce a healthy outStatus/outBoneCount after import: "
                        f"{row['physicsAfterImport'].get('solver')}"
                    )
            except Exception as exc:
                row["errors"].append(str(exc))
            finally:
                try:
                    cmds.file(new=True, force=True)
                    row["afterNewScene"] = _memory_sample("afterNewScene", cycle)
                    baseline_samples.append(row["afterNewScene"])
                except Exception as exc:
                    row["errors"].append(f"new scene cleanup: {exc}")
            row["status"] = "pass" if not row["errors"] else "error"
            report["cycles"].append(row)
    except Exception as exc:
        report["errors"].append(str(exc))
    report["baselineGate"] = _baseline_gate(baseline_samples)
    if len(report["cycles"]) != int(args.iterations):
        report["errors"].append(
            f"completed {len(report['cycles'])} cycle(s), expected {args.iterations}"
        )
    if any(row.get("status") != "pass" for row in report["cycles"]):
        report["errors"].append("one or more import cycles failed")
    if report["baselineGate"].get("status") != "pass":
        report["errors"].extend(report["baselineGate"].get("errors", []))
    report["status"] = "pass" if not report["errors"] else "error"
    return report


def main() -> int:
    args = _parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    import maya.standalone

    try:
        maya.standalone.initialize(name="python")
    except RuntimeError:
        pass
    report_path = _resolve_path(args.out)
    try:
        _load_plugin(DEFAULT_ROOT)
        report = _run(args)
    except Exception as exc:
        report = {
            "status": "error",
            "probe": "PHS-ISSUE-110-1",
            "mayaRequested": str(args.maya),
            "pmx": str(_resolve_path(args.pmx)),
            "errors": [str(exc)],
        }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        maya.standalone.uninitialize()
    except Exception:
        pass
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
