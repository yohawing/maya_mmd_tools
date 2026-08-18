"""Run a bounded Maya roundtrip against the local representative manifest.

The host process owns case and phase timeouts.  Each case is executed in a
dedicated mayapy process, so a hung Maya operation cannot stall the remaining
cases or leave the caller waiting indefinitely.  Asset paths stay in a UTF-8
JSON worker configuration; the command line only carries ASCII build paths.

The worker reuses the release probe scene oracles and
``ExportWorkflowService``.  It never writes below the manifest scan root.
"""

from __future__ import annotations

import argparse
import ctypes
import faulthandler
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import traceback
from ctypes import wintypes
from typing import Any, Callable, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
BUILD_ROOT = (ROOT / "build").resolve()
DEFAULT_MANIFEST = BUILD_ROOT / "reports" / "local_asset_roundtrip" / "representative.json"
DEFAULT_OUT_DIR = BUILD_ROOT / "reports" / "local_asset_roundtrip"
MANIFEST_SCHEMA_VERSION = 1
FLOAT_TOLERANCE = 1.0e-4

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class PhaseTimeoutError(RuntimeError):
    """Raised when a worker phase exceeds its configured wall timeout."""


def _require_build_path(value: str | Path, option_name: str) -> Path:
    """Resolve an artifact path and keep it under this repository's build tree."""

    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    resolved = path.resolve()
    if resolved != BUILD_ROOT and BUILD_ROOT not in resolved.parents:
        raise ValueError(f"{option_name} must resolve under {BUILD_ROOT}: {resolved}")
    return resolved


def _safe_name(value: str) -> str:
    """Return a stable ASCII case directory name."""

    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return safe[:80] or "case"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write one UTF-8 JSON artifact, creating only build-owned parents."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any] | None:
    """Read a possibly in-progress JSON checkpoint."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _resolve_asset_path(value: str, manifest_path: Path) -> Path:
    """Resolve a manifest asset path without rewriting or copying the asset."""

    path = Path(value)
    if not path.is_absolute():
        path = manifest_path.parent / path
    return path.resolve()


def _load_manifest(path_value: str | Path) -> tuple[Path, dict[str, Any]]:
    """Load and validate the selector manifest and all selected source paths."""

    path = Path(path_value)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported manifest schema: {document.get('schema_version')!r}"
        )
    cases = document.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"manifest has no cases: {path}")
    normalized: list[dict[str, Any]] = []
    for index, raw_case in enumerate(cases):
        if not isinstance(raw_case, dict):
            raise ValueError(f"manifest case {index} is not an object")
        name = str(raw_case.get("name") or f"case_{index:03d}")
        pmx_value = raw_case.get("pmx")
        vmd_value = raw_case.get("vmd")
        if not isinstance(pmx_value, str) or not pmx_value:
            raise ValueError(f"case {name!r} has no PMX path")
        pmx_path = _resolve_asset_path(pmx_value, path)
        if not pmx_path.is_file():
            raise FileNotFoundError(f"case {name!r} PMX not found: {pmx_path}")
        case = dict(raw_case)
        case["name"] = name
        case["pmx"] = str(pmx_path)
        if vmd_value is not None:
            if not isinstance(vmd_value, str) or not vmd_value:
                raise ValueError(f"case {name!r} has malformed VMD path")
            vmd_path = _resolve_asset_path(vmd_value, path)
            if not vmd_path.is_file():
                raise FileNotFoundError(f"case {name!r} VMD not found: {vmd_path}")
            case["vmd"] = str(vmd_path)
        normalized.append(case)
    return path, {"manifest": document, "cases": normalized}


def _select_cases(
    cases: Iterable[Mapping[str, Any]],
    case_filter: str | None = None,
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """Select manifest cases without expanding PMX/VMD pairs cartesianly."""

    available = [dict(case) for case in cases]
    if case_filter:
        needle = case_filter.casefold()
        available = [case for case in available if needle in str(case["name"]).casefold()]
    if profile is None:
        return available
    if profile != "dense-hang-and-sparse-interpolation":
        raise ValueError(f"unsupported profile: {profile}")
    selected: list[dict[str, Any]] = []
    for classification in ("dense", "sparse"):
        selected_case = next(
            (
                case
                for case in available
                if str(case.get("classification")) == classification
            ),
            None,
        )
        if selected_case is not None:
            selected.append(selected_case)
    if len(selected) != 2:
        raise ValueError(
            "profile requires one dense and one sparse PMX/VMD case after filtering"
        )
    return selected


def _vmd_payload(data: Any) -> dict[str, Any]:
    """Normalize every VMD section for structural and raw interpolation checks."""

    def vector(value: Any) -> list[float]:
        return [round(float(item), 7) for item in (value or ())]

    bones = [
        {
            "name": str(frame.bone_name),
            "frame": int(frame.frame_number),
            "position": vector(frame.position),
            "rotation": vector(frame.rotation),
            "interpolation": list(bytes(frame.interpolation)),
        }
        for frame in data.bone_frames
    ]
    bones.sort(key=lambda item: (item["name"], item["frame"]))
    morphs = [
        {
            "name": str(frame.morph_name),
            "frame": int(frame.frame_number),
            "value": round(float(frame.value), 7),
        }
        for frame in data.morph_frames
    ]
    morphs.sort(key=lambda item: (item["name"], item["frame"]))
    cameras = [
        {
            "frame": int(frame.frame_number),
            "distance": round(float(frame.distance), 7),
            "position": vector(frame.position),
            "rotation": vector(frame.rotation),
            "interpolation": list(bytes(frame.interpolation)),
            "viewing_angle": int(frame.viewing_angle),
            "perspective": int(frame.perspective),
        }
        for frame in data.camera_frames
    ]
    cameras.sort(key=lambda item: item["frame"])
    lights = [
        {
            "frame": int(frame.frame_number),
            "color": vector(frame.color),
            "position": vector(frame.position),
        }
        for frame in data.light_frames
    ]
    lights.sort(key=lambda item: item["frame"])
    shadows = [
        {
            "frame": int(frame.frame_number),
            "mode": int(frame.mode),
            "distance": round(float(frame.distance), 7),
        }
        for frame in data.shadow_frames
    ]
    shadows.sort(key=lambda item: item["frame"])
    ik_frames = [
        {
            "frame": int(frame.frame_number),
            "visible": int(frame.visible),
            "states": [[str(name), int(state)] for name, state in frame.ik_states],
        }
        for frame in data.ik_show_hide_frames
    ]
    ik_frames.sort(key=lambda item: item["frame"])
    return {
        "model_name": str(getattr(data.header, "model_name", "") or ""),
        "bone": bones,
        "morph": morphs,
        "camera": cameras,
        "light": lights,
        "shadow": shadows,
        "ik": ik_frames,
    }


def _vmd_payload_diff(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> list[str]:
    """Compare VMD names, key times, values, interpolation and IK states."""

    failures: list[str] = []
    for section in ("bone", "morph", "camera", "light", "shadow", "ik"):
        expected_items = list(expected.get(section, ()))
        actual_items = list(actual.get(section, ()))
        if len(expected_items) != len(actual_items):
            failures.append(
                f"{section}.count expected={len(expected_items)} actual={len(actual_items)}"
            )
            continue
        if section in {"bone", "morph", "ik"}:
            expected_names = [item.get("name") for item in expected_items]
            actual_names = [item.get("name") for item in actual_items]
            if expected_names != actual_names:
                failures.append(f"{section}.track_names differ")
        for index, (source, result) in enumerate(zip(expected_items, actual_items)):
            if source.get("frame") != result.get("frame"):
                failures.append(f"{section}[{index}].frame differs")
            if section == "bone":
                if source.get("interpolation") != result.get("interpolation"):
                    failures.append(f"bone[{index}].interpolation differs")
                for field in ("position", "rotation"):
                    if _max_float_difference(source.get(field, ()), result.get(field, ())) > FLOAT_TOLERANCE:
                        failures.append(f"bone[{index}].{field} differs")
            elif section == "morph":
                if abs(float(source.get("value", 0.0)) - float(result.get("value", 0.0))) > FLOAT_TOLERANCE:
                    failures.append(f"morph[{index}].value differs")
            elif section == "camera":
                if source.get("interpolation") != result.get("interpolation"):
                    failures.append(f"camera[{index}].interpolation differs")
                for field in ("distance", "viewing_angle", "perspective"):
                    if source.get(field) != result.get(field):
                        failures.append(f"camera[{index}].{field} differs")
                for field in ("position", "rotation"):
                    if _max_float_difference(source.get(field, ()), result.get(field, ())) > FLOAT_TOLERANCE:
                        failures.append(f"camera[{index}].{field} differs")
            elif section == "light":
                for field in ("color", "position"):
                    if _max_float_difference(source.get(field, ()), result.get(field, ())) > FLOAT_TOLERANCE:
                        failures.append(f"light[{index}].{field} differs")
            elif section == "shadow":
                if source != result:
                    failures.append(f"shadow[{index}] differs")
            elif section == "ik" and source != result:
                failures.append(f"ik[{index}] differs")
    return failures


def _max_float_difference(expected: Iterable[Any], actual: Iterable[Any]) -> float:
    """Return the largest absolute difference between two numeric vectors."""

    expected_values = list(expected)
    actual_values = list(actual)
    if len(expected_values) != len(actual_values):
        return float("inf")
    return max(
        (abs(float(left) - float(right)) for left, right in zip(expected_values, actual_values)),
        default=0.0,
    )


def _metric_snapshot() -> dict[str, int | None]:
    """Return current process RSS in bytes where the host exposes it."""

    if os.name == "nt":
        class ProcessMemoryCounters(ctypes.Structure):
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
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(ProcessMemoryCounters)
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            get_current_process = kernel32.GetCurrentProcess
            get_current_process.restype = wintypes.HANDLE
            get_process_memory_info = psapi.GetProcessMemoryInfo
            get_process_memory_info.argtypes = (
                wintypes.HANDLE,
                ctypes.POINTER(ProcessMemoryCounters),
                wintypes.DWORD,
            )
            get_process_memory_info.restype = wintypes.BOOL
            result = get_process_memory_info(
                get_current_process(),
                ctypes.byref(counters),
                counters.cb,
            )
        except (AttributeError, OSError, TypeError):
            result = 0
        if result:
            return {
                "rss_bytes": int(counters.WorkingSetSize),
                "peak_rss_bytes": int(counters.PeakWorkingSetSize),
            }
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        pages = int(Path("/proc/self/statm").read_text(encoding="ascii").split()[1])
    except (AttributeError, FileNotFoundError, IndexError, OSError, ValueError):
        return {"rss_bytes": None, "peak_rss_bytes": None}
    return {"rss_bytes": pages * page_size, "peak_rss_bytes": None}


class _PhaseRecorder:
    """Record one worker phase and asynchronously emit timeout stack samples."""

    def __init__(
        self,
        context: "_WorkerContext",
        name: str,
    ) -> None:
        self.context = context
        self.name = name
        self.started = time.perf_counter()
        self.cpu_started = time.process_time()
        self.rss_started = _metric_snapshot()
        self.rss_peak = self.rss_started.get("peak_rss_bytes") or self.rss_started.get("rss_bytes")
        self.stop = threading.Event()
        self.timed_out = threading.Event()
        self.thread: threading.Thread | None = None
        self.stack_samples: list[str] = []

    def __enter__(self) -> "_PhaseRecorder":
        self.context.write_checkpoint(self.name, "running")
        self.thread = threading.Thread(target=self._watchdog, name=f"phase-timeout-{self.name}", daemon=True)
        self.thread.start()
        return self

    def _watchdog(self) -> None:
        timeout = self.context.phase_timeout_sec
        if timeout <= 0:
            return
        if self.stop.wait(timeout):
            return
        self.timed_out.set()
        for sample_index in range(1, 4):
            stack_path = self.context.stack_dir / f"{_safe_name(self.name)}-{sample_index}.log"
            try:
                stack_path.parent.mkdir(parents=True, exist_ok=True)
                with stack_path.open("w", encoding="utf-8") as handle:
                    faulthandler.dump_traceback(file=handle, all_threads=True)
                self.stack_samples.append(str(stack_path))
            except (OSError, RuntimeError):
                pass
            self.context.write_checkpoint(
                self.name,
                "timed_out",
                timed_out=True,
                stack_samples=self.stack_samples,
            )
            if sample_index < 3:
                time.sleep(0.5)

    def __exit__(self, exc_type: Any, exc: Any, _tb: Any) -> bool:
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=0.2)
        ended = time.perf_counter()
        cpu_ended = time.process_time()
        rss_ended = _metric_snapshot()
        self.rss_peak = max(
            value
            for value in (
                self.rss_peak,
                rss_ended.get("rss_bytes"),
                rss_ended.get("peak_rss_bytes"),
            )
            if value is not None
        ) if any(
            value is not None
            for value in (
                self.rss_peak,
                rss_ended.get("rss_bytes"),
                rss_ended.get("peak_rss_bytes"),
            )
        ) else None
        entry = {
            "name": self.name,
            "wall_sec": round(ended - self.started, 6),
            "cpu_sec": round(cpu_ended - self.cpu_started, 6),
            "rss_start_bytes": self.rss_started.get("rss_bytes"),
            "rss_end_bytes": rss_ended.get("rss_bytes"),
            "rss_peak_bytes": self.rss_peak,
            "timeout_sec": self.context.phase_timeout_sec,
            "status": "timed_out" if self.timed_out.is_set() else ("failed" if exc else "passed"),
            "stack_samples": list(self.stack_samples),
        }
        self.context.phases.append(entry)
        self.context.write_checkpoint(
            self.name,
            entry["status"],
            timed_out=self.timed_out.is_set(),
            stack_samples=self.stack_samples,
        )
        if self.timed_out.is_set() and exc is None:
            raise PhaseTimeoutError(
                f"phase {self.name!r} exceeded {self.context.phase_timeout_sec:g}s"
            )
        return False


class _WorkerContext:
    """Mutable per-worker evidence state."""

    def __init__(self, checkpoint: Path, stack_dir: Path, phase_timeout_sec: float) -> None:
        self.checkpoint = checkpoint
        self.stack_dir = stack_dir
        self.phase_timeout_sec = phase_timeout_sec
        self.phases: list[dict[str, Any]] = []

    def write_checkpoint(
        self,
        phase: str,
        status: str,
        *,
        timed_out: bool = False,
        stack_samples: Iterable[str] = (),
    ) -> None:
        """Publish the last known phase for the host watchdog."""

        _write_json(
            self.checkpoint,
            {
                "phase": phase,
                "status": status,
                "timed_out": bool(timed_out),
                "stack_samples": list(stack_samples),
                "updated_at": time.time(),
            },
        )


def _phase(context: _WorkerContext, name: str, function: Callable[[], Any]) -> Any:
    """Run a callable under the worker phase recorder."""

    with _PhaseRecorder(context, name):
        return function()


def _report_summary(validation: Any) -> dict[str, Any]:
    """Normalize ExportWorkflow validation evidence for JSON."""

    report = getattr(validation, "report", None)
    issues = list(getattr(report, "issues", ()) or ())
    issue_details = [
        {
            "code": str(getattr(issue, "code", "")),
            "severity": str(getattr(issue, "severity", "")),
            "blocking": bool(getattr(issue, "blocking", False)),
            "category": str(getattr(issue, "category", "")),
            "message": str(getattr(issue, "message", "")),
            "remediation": str(getattr(issue, "remediation", "")),
        }
        for issue in issues
    ]
    return {
        "state": getattr(validation, "state", None),
        "blocking": bool(getattr(report, "is_blocking", False)),
        "issue_count": len(issues),
        "issue_codes": [str(getattr(issue, "code", "")) for issue in issues],
        "issues": issue_details,
        "severity_counts": {
            severity: sum(1 for issue in issues if getattr(issue, "severity", None) == severity)
            for severity in ("fatal", "error", "warning", "info")
        },
    }


def _compare_morph_structure(
    expected: Mapping[str, Any], actual: Mapping[str, Any]
) -> list[str]:
    """Compare general-model morph identity without fixture-only runtime probes."""

    failures: list[str] = []
    expected_morphs = list(expected.get("morphs", ()) or ())
    actual_morphs = list(actual.get("morphs", ()) or ())
    if len(expected_morphs) != len(actual_morphs):
        failures.append(
            f"morph count expected={len(expected_morphs)} actual={len(actual_morphs)}"
        )
    for index, (source, result) in enumerate(zip(expected_morphs, actual_morphs)):
        for field in ("index", "name", "name_en", "type", "panel", "offsets"):
            if field not in source and field not in result:
                continue
            source_value = json.dumps(
                source.get(field), ensure_ascii=False, sort_keys=True, default=str
            )
            result_value = json.dumps(
                result.get(field), ensure_ascii=False, sort_keys=True, default=str
            )
            if source_value != result_value:
                failures.append(f"morphs[{index}].{field} differs")
    if expected.get("unsupported_types") != actual.get("unsupported_types"):
        failures.append("morph unsupported type set differs")
    return failures


def _export_request(
    output: Path,
    report_dir: Path,
    *,
    export_format: str,
    target_model: str | None,
    start_frame: int | None = None,
    end_frame: int | None = None,
    model_name: str | None = None,
    case: Mapping[str, Any],
) -> Any:
    """Build a release-style ExportWorkflow request for one local case."""

    from mmd_tools.services.export_workflow_service import ExportWorkflowRequest

    options: dict[str, Any] = {
        "export_format": export_format,
        "authoring_semantics": "legacy",
        "require_target": target_model is not None,
        "target_model": target_model,
        "target_identity": target_model,
        "validation_report_dir": str(report_dir),
        "validation_report_evidence": {
            "gate": "LOCAL-ASSET-REPRESENTATIVE-ROUNDTRIP-HANG-1",
            "case": str(case["name"]),
            "fresh_import": True,
            "authoring_semantics": "legacy",
            "metrics": case.get("metrics", {}),
            "oracles": ["parser", "scene", "fresh_scene"],
        },
    }
    if start_frame is not None:
        options["start_frame"] = int(start_frame)
    if end_frame is not None:
        options["end_frame"] = int(end_frame)
    if model_name is not None:
        options["model_name"] = model_name
    if export_format == "vmd":
        options["vmd_mode"] = "C"
        # The runner exercises an unchanged imported clip. Complete raw VMD
        # provenance is therefore authoritative for transform values; normal
        # edited-scene exports keep the collector's conservative default.
        options["preserve_raw_bone_transforms"] = True
    return ExportWorkflowRequest(str(output), options)


def _run_pmx_case(case: Mapping[str, Any], out_dir: Path, context: _WorkerContext) -> dict[str, Any]:
    """Run PMX parser/import/export/fresh-import semantic parity."""

    from mmd_tools.core.pmx_data import PmxData
    from mmd_tools.services.export_workflow_service import ExportWorkflowService
    from tests.roundtrip.pmx_roundtrip_runner import _compare_pmx_supported_content
    from tools.export_release_maya_probe import (
        _build_source_bone_semantics_oracle,
        _capture_bone_semantics_oracle,
        _capture_scene_oracle,
        _compare_bone_semantics,
        _compare_scene_oracles,
        _fresh_import,
    )

    source = Path(str(case["pmx"]))
    output = out_dir / "model.pmx"
    report_dir = out_dir / "report"
    source_data = _phase(context, "source_parse", lambda: PmxData().parse_file(str(source)))
    source_root, source_oracle, source_bones, source_import_bones = _phase(
        context,
        "source_import_oracle",
        lambda: _pmx_source_import(
            source,
            _fresh_import,
            _capture_scene_oracle,
            _build_source_bone_semantics_oracle,
            _capture_bone_semantics_oracle,
        ),
    )
    source_failures = []
    source_failures.extend(_compare_bone_semantics(source_bones, source_import_bones, "source_import"))
    if source_failures:
        raise AssertionError("source import oracle failed: " + "; ".join(source_failures[:20]))

    request = _export_request(
        output,
        report_dir,
        export_format="pmx",
        target_model=source_root,
        case=case,
    )
    workflow = ExportWorkflowService()
    validation = _phase(context, "export_validation", lambda: workflow.validate(request))
    validation_evidence = _report_summary(validation)
    if validation.error is not None or validation_evidence["blocking"]:
        raise RuntimeError(
            f"PMX validation blocked: state={validation_evidence['state']} "
            f"issues={validation_evidence['issues']}"
        )
    result = _phase(
        context,
        "export_write",
        lambda: workflow.execute(request, acknowledge_warnings=True),
    )
    if not result.succeeded:
        raise RuntimeError(f"PMX export failed: {result.error or result.report}")
    exported_data = _phase(context, "exported_parse", lambda: PmxData().parse_file(str(output)))
    parser_diffs, parser_warnings = _compare_pmx_supported_content(
        source_data,
        exported_data,
        str(case["name"]),
    )
    fresh_root, fresh_oracle, _fresh_bones, fresh_import_bones = _phase(
        context,
        "fresh_import_oracle",
        lambda: _pmx_source_import(
            output,
            _fresh_import,
            _capture_scene_oracle,
            _build_source_bone_semantics_oracle,
            _capture_bone_semantics_oracle,
        ),
    )
    failures: list[str] = []
    failures.extend(
        _compare_scene_oracles(
            source_oracle,
            fresh_oracle,
            pose=True,
            physics=True,
            morphs=False,
        )
    )
    failures.extend(_compare_morph_structure(source_oracle["morphs"], fresh_oracle["morphs"]))
    failures.extend(_compare_bone_semantics(source_bones, fresh_import_bones, "fresh_import"))
    if source_oracle.get("metadata", {}).get("mmd_display_frames_json") != fresh_oracle.get("metadata", {}).get("mmd_display_frames_json"):
        failures.append("metadata.mmd_display_frames_json differs")
    if failures:
        raise AssertionError("PMX semantic mismatch: " + "; ".join(failures[:30]))
    return {
        "status": "pass",
        "kind": "pmx",
        "source": str(source),
        "output": str(output),
        "validation": validation_evidence,
        "parser_warnings": parser_warnings,
        "parser_normalization_diagnostics": {
            "status": "recorded",
            "diff_count": len(parser_diffs),
            "samples": parser_diffs[:20],
        },
        "semantic": {
            "model": True,
            "geometry": True,
            "materials": True,
            "bones_ik": True,
            "morphs": True,
            "display_frames": True,
            "physics": True,
            "index_references": True,
        },
        "parsed_counts": {
            "vertices": len(exported_data.vertices),
            "faces": len(exported_data.faces),
            "materials": len(exported_data.materials),
            "bones": len(exported_data.bones),
            "morphs": len(exported_data.morphs),
            "rigid_bodies": len(exported_data.rigid_bodies),
            "joints": len(exported_data.joints),
        },
        "fresh_root": fresh_root,
    }


def _pmx_source_import(
    source: Path,
    fresh_import: Callable[..., str],
    capture_scene_oracle: Callable[..., dict[str, Any]],
    build_bone_oracle: Callable[..., dict[str, Any]],
    capture_bone_oracle: Callable[..., dict[str, Any]],
) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Import a PMX and capture parser-backed scene, bone, and morph oracles."""

    root = fresh_import(source)
    scene = capture_scene_oracle(root, (0,))
    return (
        root,
        scene,
        build_bone_oracle(source),
        capture_bone_oracle(root),
    )


def _run_vmd_case(case: Mapping[str, Any], out_dir: Path, context: _WorkerContext) -> dict[str, Any]:
    """Run PMX+VMD import, validation/export, parser parity and fresh pose parity."""

    from mmd_tools.core.vmd_data import VmdData
    from mmd_tools.services.export_workflow_service import ExportWorkflowService
    from tools.export_release_maya_probe import (
        _capture_camera_light_scene_oracle,
        _capture_scene_oracle,
        _compare_camera_light_semantics,
        _compare_scene_oracles,
        _fresh_import,
        _import_vmd_into_current_scene,
    )

    source_model = Path(str(case["pmx"]))
    source_vmd = Path(str(case["vmd"]))
    metrics = case.get("metrics", {})
    oracle_frames = [int(frame) for frame in case.get("oracle_frames", ())]
    if len(oracle_frames) < 2:
        raise ValueError(f"case {case['name']!r} has fewer than two oracle frames")
    output = out_dir / "motion.vmd"
    report_dir = out_dir / "report"
    source_data = _phase(context, "source_parse", lambda: VmdData().parse_file(str(source_vmd)))
    source_payload = _vmd_payload(source_data)

    def import_source() -> tuple[str, dict[str, Any], dict[str, Any] | None]:
        root = _fresh_import(source_model)
        _import_vmd_into_current_scene(root, source_model, source_vmd)
        scene = _capture_scene_oracle(root, oracle_frames)
        camera_scene = None
        if source_data.camera_frames and source_data.light_frames:
            camera_scene = _capture_camera_light_scene_oracle(root, oracle_frames)
        return root, scene, camera_scene

    source_root, source_oracle, source_camera_oracle = _phase(
        context,
        "source_import_oracle",
        import_source,
    )
    start_frame = int(metrics.get("frame_start", oracle_frames[0]))
    end_frame = int(metrics.get("frame_end", oracle_frames[-1]))
    request = _export_request(
        output,
        report_dir,
        export_format="vmd",
        target_model=source_root,
        start_frame=start_frame,
        end_frame=end_frame,
        model_name=str(getattr(source_data.header, "model_name", "") or ""),
        case=case,
    )
    workflow = ExportWorkflowService()
    validation = _phase(context, "export_validation", lambda: workflow.validate(request))
    validation_evidence = _report_summary(validation)
    if validation.error is not None or validation_evidence["blocking"]:
        raise RuntimeError(
            f"VMD validation blocked: state={validation_evidence['state']} "
            f"issues={validation_evidence['issues']}"
        )
    result = _phase(
        context,
        "export_write",
        lambda: workflow.execute(request, acknowledge_warnings=True),
    )
    if not result.succeeded:
        raise RuntimeError(f"VMD export failed: {result.error or result.report}")
    exported_data = _phase(context, "exported_parse", lambda: VmdData().parse_file(str(output)))
    exported_payload = _vmd_payload(exported_data)
    parser_failures = _vmd_payload_diff(source_payload, exported_payload)
    source_total_keys = sum(len(source_payload[section]) for section in ("bone", "morph", "camera", "light", "shadow", "ik"))
    exported_total_keys = sum(len(exported_payload[section]) for section in ("bone", "morph", "camera", "light", "shadow", "ik"))
    key_inflation = exported_total_keys - source_total_keys
    if str(case.get("classification")) == "sparse" and key_inflation > 0:
        parser_failures.append(
            f"sparse key inflation source={source_total_keys} exported={exported_total_keys}"
        )

    def import_fresh() -> tuple[dict[str, Any], dict[str, Any] | None]:
        fresh_root = _fresh_import(source_model)
        _import_vmd_into_current_scene(fresh_root, source_model, output)
        scene = _capture_scene_oracle(fresh_root, oracle_frames)
        camera_scene = None
        if exported_data.camera_frames and exported_data.light_frames:
            camera_scene = _capture_camera_light_scene_oracle(fresh_root, oracle_frames)
        return scene, camera_scene

    fresh_oracle, fresh_camera_oracle = _phase(context, "fresh_import_oracle", import_fresh)
    failures = list(parser_failures)
    failures.extend(
        _compare_scene_oracles(
            source_oracle,
            fresh_oracle,
            pose=True,
            mesh=False,
            materials=False,
            morphs=False,
        )
    )
    failures.extend(_compare_morph_structure(source_oracle["morphs"], fresh_oracle["morphs"]))
    if source_camera_oracle is not None and fresh_camera_oracle is not None:
        failures.extend(
            _compare_camera_light_semantics(
                source_camera_oracle,
                fresh_camera_oracle,
                "fresh_import",
            )
        )
    if failures:
        raise AssertionError("VMD semantic mismatch: " + "; ".join(failures[:30]))
    return {
        "status": "pass",
        "kind": "pmx_vmd",
        "classification": case.get("classification"),
        "source_model": str(source_model),
        "source": str(source_vmd),
        "output": str(output),
        "validation": validation_evidence,
        "semantic": {
            "sections": True,
            "track_names": True,
            "key_frames": True,
            "ik_states": True,
            "raw_interpolation": True,
            "fresh_pose": True,
            "fresh_camera_light": source_camera_oracle is not None,
        },
        "key_counts": {
            "source": source_total_keys,
            "exported": exported_total_keys,
            "inflation": key_inflation,
        },
        "track_counts": {
            section: {"source": len(source_payload[section]), "exported": len(exported_payload[section])}
            for section in ("bone", "morph", "camera", "light", "shadow", "ik")
        },
        "source_metrics": metrics,
    }


def _initialize_maya() -> None:
    """Initialize Maya standalone and register the project plugin."""

    import maya.standalone
    from tests.common.maya_plugin_setup import load_mmd_tools_plugin

    try:
        maya.standalone.initialize(name="python")
    except RuntimeError:
        pass
    load_mmd_tools_plugin(ROOT)


def _run_worker(config_path: Path, result_path: Path, checkpoint: Path, phase_timeout_sec: float) -> int:
    """Run one case repeatedly in a single warmable mayapy process."""

    config = json.loads(config_path.read_text(encoding="utf-8"))
    case = config["case"]
    repetitions = int(config.get("repetitions", 1))
    out_dir = Path(config["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    context = _WorkerContext(checkpoint, out_dir / "stacks", phase_timeout_sec)
    document: dict[str, Any] = {
        "status": "fail",
        "case": case,
        "out_dir": str(out_dir),
        "repetitions": repetitions,
        "runs": [],
        "worker_pid": os.getpid(),
    }
    try:
        _initialize_maya()
        for index in range(repetitions):
            run_dir = out_dir / f"run-{index:02d}"
            run_dir.mkdir(parents=True, exist_ok=True)
            context.phases = []
            started = time.perf_counter()
            try:
                if case.get("vmd"):
                    result = _run_vmd_case(case, run_dir, context)
                else:
                    result = _run_pmx_case(case, run_dir, context)
                run_status = "pass"
                error = None
                traceback_text = None
            except PhaseTimeoutError as exc:
                run_status = "timeout"
                result = None
                error = f"{type(exc).__name__}: {exc}"
                traceback_text = traceback.format_exc(limit=20)
            except Exception as exc:  # noqa: BLE001 - worker serializes evidence.
                run_status = "fail"
                result = None
                error = f"{type(exc).__name__}: {exc}"
                traceback_text = traceback.format_exc(limit=30)
            document["runs"].append(
                {
                    "index": index,
                    "temperature": "cold" if index == 0 else "warm",
                    "status": run_status,
                    "elapsed_sec": round(time.perf_counter() - started, 3),
                    "phase_timing": list(context.phases),
                    "result": result,
                    "error": error,
                    "traceback": traceback_text,
                }
            )
            if run_status != "pass":
                break
        document["status"] = "pass" if len(document["runs"]) == repetitions and all(
            run["status"] == "pass" for run in document["runs"]
        ) else "fail"
    except Exception as exc:  # noqa: BLE001 - include initialization failures.
        document["status"] = "fail"
        document["error"] = f"{type(exc).__name__}: {exc}"
        document["traceback"] = traceback.format_exc(limit=30)
    _write_json(result_path, document)
    return 0 if document["status"] == "pass" else 1


def _run_child(
    mayapy: Path,
    config_path: Path,
    result_path: Path,
    checkpoint: Path,
    *,
    phase_timeout_sec: float,
    case_timeout_sec: float,
) -> dict[str, Any]:
    """Run and watchdog one mayapy child, returning host-owned case evidence."""

    log_dir = config_path.parent
    stdout_path = log_dir / "mayapy.stdout.log"
    stderr_path = log_dir / "mayapy.stderr.log"
    command = [
        str(mayapy),
        str(Path(__file__).resolve()),
        "--worker-config",
        str(config_path),
        "--worker-result",
        str(result_path),
        "--worker-checkpoint",
        str(checkpoint),
        "--phase-timeout-sec",
        str(phase_timeout_sec),
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        entry for entry in (str(ROOT), env.get("PYTHONPATH", "")) if entry
    )
    env["MAYA_SKIP_USERSETUP_PY"] = "1"
    env["MMD_TOOLS_SKIP_SHADER_OVERRIDE"] = "1"
    started = time.perf_counter()
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen(
            command,
            cwd=str(ROOT),
            env=env,
            stdout=stdout,
            stderr=stderr,
        )
        timeout_kind = None
        timeout_checkpoint: dict[str, Any] | None = None
        while process.poll() is None:
            elapsed = time.perf_counter() - started
            current_checkpoint = _read_json(checkpoint)
            if current_checkpoint and current_checkpoint.get("timed_out"):
                timeout_kind = "phase_timeout"
                grace_deadline = time.perf_counter() + 3.0
                timeout_checkpoint = current_checkpoint
                while time.perf_counter() < grace_deadline and process.poll() is None:
                    time.sleep(0.25)
                    timeout_checkpoint = _read_json(checkpoint) or timeout_checkpoint
                process.kill()
                break
            if elapsed >= case_timeout_sec:
                timeout_kind = "case_timeout"
                timeout_checkpoint = current_checkpoint
                process.kill()
                break
            time.sleep(0.25)
        return_code = process.wait()
    child_result = _read_json(result_path)
    if timeout_kind is not None:
        return {
            "status": "timeout",
            "timeout_kind": timeout_kind,
            "return_code": return_code,
            "elapsed_sec": round(time.perf_counter() - started, 3),
            "last_phase": timeout_checkpoint,
            "result": child_result,
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
        }
    if child_result is None:
        return {
            "status": "crash",
            "return_code": return_code,
            "elapsed_sec": round(time.perf_counter() - started, 3),
            "error": "mayapy exited without a worker result",
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
        }
    child_result["return_code"] = return_code
    child_result["stdout"] = str(stdout_path)
    child_result["stderr"] = str(stderr_path)
    return child_result


def _repetitions(case: Mapping[str, Any], cold_runs: int, warm_runs: int) -> int:
    """Return cold+warm count for Dense cases and one run for other cases."""

    if str(case.get("classification")) == "dense":
        return cold_runs + warm_runs
    return 1


def _summary_markdown(document: Mapping[str, Any]) -> str:
    """Render a compact human-readable summary without hiding failures."""

    lines = [
        "# Local asset roundtrip",
        "",
        f"- status: `{document.get('status')}`",
        f"- Maya: `{document.get('maya')}`",
        f"- profile: `{document.get('profile') or 'all'}`",
        f"- manifest: `{document.get('manifest')}`",
        "",
        "| Case | Classification | Status | Runs | Last phase |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for case in document.get("cases", ()):
        runs = case.get("runs", [])
        last_phase = ""
        if runs:
            phase_timing = runs[-1].get("phase_timing", [])
            if phase_timing:
                last_phase = str(phase_timing[-1].get("name", ""))
        if case.get("last_phase"):
            last_phase = str(case["last_phase"].get("phase", last_phase))
        lines.append(
            f"| {case.get('name')} | {case.get('classification')} | {case.get('status')} | "
            f"{len(runs)} | {last_phase} |"
        )
    lines.extend(["", "## Artifacts", ""])
    for case in document.get("cases", ()):
        lines.append(f"- `{case.get('name')}`: `{case.get('out_dir')}`")
    return "\n".join(lines) + "\n"


def _run_host(args: argparse.Namespace) -> int:
    """Run selected cases in isolated mayapy workers and write summary artifacts."""

    manifest_path, loaded = _load_manifest(args.manifest)
    cases = _select_cases(loaded["cases"], args.case, args.profile)
    if not cases:
        raise ValueError("no cases selected")
    if args.cold_runs < 1 or args.warm_runs < 0:
        raise ValueError("cold runs must be positive and warm runs must be non-negative")
    if args.phase_timeout_sec <= 0 or args.case_timeout_sec <= 0:
        raise ValueError("timeouts must be positive")
    out_dir = _require_build_path(args.out_dir, "--out-dir")
    out_dir.mkdir(parents=True, exist_ok=True)
    from tests.common.maya_location import mayapy as mayapy_for_version

    mayapy = mayapy_for_version(args.maya)
    if not mayapy.is_file():
        raise FileNotFoundError(f"mayapy not found for Maya {args.maya}: {mayapy}")
    summary: dict[str, Any] = {
        "status": "fail",
        "maya": str(args.maya),
        "profile": args.profile,
        "manifest": str(manifest_path),
        "case_timeout_sec": args.case_timeout_sec,
        "phase_timeout_sec": args.phase_timeout_sec,
        "cases": [],
    }
    for case in cases:
        case_dir = out_dir / "cases" / _safe_name(str(case["name"]))
        case_dir.mkdir(parents=True, exist_ok=True)
        repetitions = _repetitions(case, args.cold_runs, args.warm_runs)
        config_path = case_dir / "worker-config.json"
        result_path = case_dir / "worker-result.json"
        checkpoint = case_dir / "phase-status.json"
        _write_json(
            config_path,
            {
                "schema_version": 1,
                "case": case,
                "out_dir": str(case_dir),
                "repetitions": repetitions,
            },
        )
        result = _run_child(
            mayapy,
            config_path,
            result_path,
            checkpoint,
            phase_timeout_sec=args.phase_timeout_sec,
            case_timeout_sec=args.case_timeout_sec,
        )
        result["name"] = case["name"]
        result["classification"] = case.get("classification")
        result["out_dir"] = str(case_dir)
        summary["cases"].append(result)
        _write_json(out_dir / "summary.json", summary)
    summary["status"] = "pass" if all(case.get("status") == "pass" for case in summary["cases"]) else "fail"
    _write_json(out_dir / "summary.json", summary)
    (out_dir / "summary.md").write_text(_summary_markdown(summary), encoding="utf-8")
    print(f"summary: {out_dir / 'summary.json'}")
    print(f"status: {summary['status']}")
    return 0 if summary["status"] == "pass" else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse host and hidden mayapy worker options."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--maya", default="2024")
    parser.add_argument("--case", default=None)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--phase-timeout-sec", type=float, default=300.0)
    parser.add_argument("--case-timeout-sec", type=float, default=1800.0)
    parser.add_argument("--cold-runs", type=int, default=1)
    parser.add_argument("--warm-runs", type=int, default=3)
    parser.add_argument("--worker-config", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--worker-result", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--worker-checkpoint", default=None, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for host orchestration or one mayapy worker."""

    args = parse_args(argv)
    if args.worker_config:
        if not args.worker_result or not args.worker_checkpoint:
            raise SystemExit("worker config, result, and checkpoint are required together")
        return _run_worker(
            Path(args.worker_config),
            Path(args.worker_result),
            Path(args.worker_checkpoint),
            args.phase_timeout_sec,
        )
    return _run_host(args)


if __name__ == "__main__":
    raise SystemExit(main())
