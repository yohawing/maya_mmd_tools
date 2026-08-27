"""Profile real two-model selection synchronization in the Maya UI.

This diagnostic runner opens the real Animator Toolset and HumanIK windows,
imports two namespaced PMX models, and measures the existing SelectionChanged
callbacks while Maya's Qt event loop is running.  It deliberately wraps
presenter/service methods at runtime; product code is not instrumented or
modified by this tool.

Run from a normal Python interpreter because the host side launches a fresh
Maya GUI process through the repository E2E harness::

    python tools/probes/model_selection_sync_benchmark.py --maya 2024

The generated JSON is evidence only.  A report is green only when all three
window-state cases observe real selection callbacks and both model identities
remain correct after every sample.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_A = PROJECT_ROOT / "tests" / "data" / "mmt_test_model.pmx"
DEFAULT_MODEL_B = PROJECT_ROOT / "tests" / "data" / "test_morph_model.pmx"
DEFAULT_OUT_DIR = PROJECT_ROOT / "build" / "reports" / "model_selection_sync"
COMMAND_PORT = 7764
COMPLETION_MARKER = "//-- MODEL_SELECTION_SYNC_BENCHMARK_DONE --//"
SCHEMA_VERSION = 1


def distribution(samples_ns: Sequence[int]) -> Dict[str, Any]:
    """Summarize nanosecond samples without inventing values for an empty set."""

    ordered = sorted(int(value) for value in samples_ns)
    if not ordered:
        return {"count": 0, "status": "not_observed"}
    quantiles = statistics.quantiles(ordered, n=100, method="inclusive") if len(ordered) > 1 else []
    return {
        "count": len(ordered),
        "min_ns": ordered[0],
        "median_ns": int(statistics.median(ordered)),
        # Round up so a fractional percentile never reports a lower latency
        # than the measured sample distribution.
        "p95_ns": math.ceil(quantiles[94]) if quantiles else ordered[0],
        "p99_ns": math.ceil(quantiles[98]) if quantiles else ordered[0],
        "max_ns": ordered[-1],
        "mean_ns": round(statistics.mean(ordered), 2),
        "status": "measured",
    }


class _TimingRecorder:
    """Collect per-sample timings from runtime-wrapped bound methods."""

    def __init__(self) -> None:
        self._sample_id: Optional[str] = None
        self.samples: Dict[str, Dict[str, List[int]]] = {}
        self.errors: Dict[str, List[str]] = {}
        self._restorers: List[Callable[[], None]] = []

    def begin(self, sample_id: str) -> None:
        """Start a sample; callbacks executed during this turn are attributed to it."""

        if self._sample_id is not None:
            raise RuntimeError("timing sample already active")
        self._sample_id = sample_id
        self.samples[sample_id] = {}
        self.errors[sample_id] = []

    def end(self) -> Dict[str, List[int]]:
        """Finish and return one sample's timing rows."""

        sample_id = self._sample_id
        self._sample_id = None
        if sample_id is None:
            raise RuntimeError("timing sample is not active")
        return self.samples[sample_id]

    def record(self, key: str, duration_ns: int) -> None:
        """Record one wrapped call when it belongs to the active sample."""

        if self._sample_id is None:
            return
        self.samples[self._sample_id].setdefault(key, []).append(int(duration_ns))

    def record_error(self, error: BaseException) -> None:
        """Attach a wrapped-call error to the active sample."""

        if self._sample_id is not None:
            self.errors[self._sample_id].append(f"{type(error).__name__}: {error}")

    def wrap(self, owner: Any, attribute: str, key: str) -> None:
        """Wrap one bound method and remember how to restore it."""

        original = getattr(owner, attribute)

        def timed(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter_ns()
            try:
                return original(*args, **kwargs)
            except Exception as error:
                self.record_error(error)
                raise
            finally:
                self.record(key, time.perf_counter_ns() - started)

        setattr(owner, attribute, timed)

        def restore() -> None:
            setattr(owner, attribute, original)

        self._restorers.append(restore)

    def wrap_class_method(self, owner_type: type, attribute: str, key: str) -> None:
        """Wrap a service method so presenter-created service instances are observed."""

        original = getattr(owner_type, attribute)

        def timed(instance: Any, *args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter_ns()
            try:
                return original(instance, *args, **kwargs)
            except Exception as error:
                self.record_error(error)
                raise
            finally:
                self.record(key, time.perf_counter_ns() - started)

        setattr(owner_type, attribute, timed)

        def restore() -> None:
            setattr(owner_type, attribute, original)

        self._restorers.append(restore)

    def restore(self) -> None:
        """Restore all wrapped methods in reverse order."""

        for restore in reversed(self._restorers):
            restore()
        self._restorers = []


def _parse_args() -> argparse.Namespace:
    """Parse host-side runner options."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", default="2024", help="Maya version used by the GUI harness.")
    parser.add_argument("--model-a", default=str(DEFAULT_MODEL_A), help="First PMX fixture.")
    parser.add_argument("--model-b", default=str(DEFAULT_MODEL_B), help="Second PMX fixture.")
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--port", type=int, default=COMMAND_PORT)
    parser.add_argument("--timeout", type=float, default=420.0)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    return parser.parse_args()


def _log(log_file: Path, message: str) -> None:
    """Append one UTF-8 diagnostic line and mirror it to stdout."""

    encoded = str(message)
    print(encoded)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(encoded + "\n")


def _write_report(path: Path, report: Dict[str, Any]) -> None:
    """Write a deterministic UTF-8 report."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _safe_process_events() -> None:
    """Drain Maya idle callbacks and Qt events inside the Maya probe."""

    from mmd_tools.ui.qt_compat import QApplication
    import maya.cmds as cmds
    import maya.utils as maya_utils

    application = QApplication.instance()
    if application is not None:
        # Maya scriptJobs run at idle boundaries, while presenter readback is
        # deferred through Qt. Drain both queues so each sample observes the
        # same event turn as the real UI without calling presenters directly.
        for _index in range(3):
            try:
                cmds.refresh()
            except Exception:
                pass
            try:
                maya_utils.processIdleEvents()
            except Exception:
                pass
            try:
                cmds.flushIdleQueue()
            except Exception:
                pass
            application.processEvents()
            time.sleep(0.05)


def _full_root(cmds: Any, root: str) -> str:
    """Resolve one model root to exactly one full DAG path."""

    paths = cmds.ls(root, long=True) or []
    if len(paths) != 1:
        raise RuntimeError(f"model root is not uniquely resolvable: {root!r} -> {paths!r}")
    return str(paths[0])


def _selection_root(cmds: Any, service: Any, available: Iterable[str]) -> Optional[str]:
    """Resolve the current selection through the existing model service."""

    resolved = service.resolve_model_from_selection(list(available))
    if not resolved:
        return None
    try:
        return _full_root(cmds, str(resolved))
    except RuntimeError:
        return None


def _set_animator_active(presenter: Any, active: bool) -> None:
    """Install/remove the presenter's existing selection job for a case."""

    if active:
        presenter._install_selection_sync_job()
    else:
        presenter._remove_selection_sync_jobs()


def _activate_case(animator_window: Any, humanik_window: Any, name: str) -> None:
    """Set visibility and callback ownership for one measured case."""

    animator = name in {"animator_only", "both"}
    humanik = name in {"humanik_only", "both"}
    if animator:
        animator_window.show()
        _set_animator_active(animator_window.animation_presenter, True)
    else:
        _set_animator_active(animator_window.animation_presenter, False)
        animator_window.hide()
    if humanik:
        humanik_window.show()
    else:
        humanik_window.hide()
    _safe_process_events()
    if bool(getattr(humanik_window.humanik_presenter, "_active", False)) != humanik:
        raise RuntimeError(f"HumanIK lifecycle did not reach requested state: {name}")


def _case_required_keys(name: str) -> Tuple[str, ...]:
    """Return callback keys required for a case to be a valid measurement."""

    common = ("model_list", "model_resolution", "model_summary")
    if name == "animator_only":
        return common + ("animator_selection_callback", "picker_sync")
    if name == "humanik_only":
        return common + ("humanik_selection_callback", "humanik_refresh")
    return common + (
        "animator_selection_callback",
        "picker_sync",
        "humanik_selection_callback",
        "humanik_refresh",
    )


def _measure_case(
    name: str,
    cmds: Any,
    service: Any,
    animator_window: Any,
    roots: Sequence[str],
    recorder: _TimingRecorder,
    iterations: int,
    warmup: int,
) -> Dict[str, Any]:
    """Measure alternating root selections and validate every event turn."""

    required_keys = _case_required_keys(name)
    sample_rows: List[Dict[str, Any]] = []
    total = warmup + iterations
    for index in range(total):
        target = roots[index % len(roots)]
        sample_id = f"{name}:{index}"
        recorder.begin(sample_id)
        sample_error: Optional[str] = None
        try:
            cmds.select(target, replace=True)
            _safe_process_events()

            # This is an explicit readback of the existing selection-to-model
            # API.  It is kept in the sample so model-resolution and model-
            # summary cost is not confused with the presenter callbacks.
            available = service.list_mmd_models()
            resolved = _selection_root(cmds, service, available)
            info = service.get_model_info(resolved or target)
            _safe_process_events()

            selected = cmds.ls(selection=True, long=True) or []
            if list(selected) != [target]:
                raise AssertionError(f"active selection mismatch: expected={[target]!r} actual={selected!r}")
            if resolved != target:
                raise AssertionError(f"model resolution mismatch: expected={target!r} actual={resolved!r}")
            if not info or str(info.get("root")) not in {target, target.lstrip("|")}:
                raise AssertionError(f"model summary mismatch: expected={target!r} actual={info!r}")
        except Exception as error:
            sample_error = f"{type(error).__name__}: {error}"
        events = recorder.end()
        errors = list(recorder.errors.get(sample_id, []))
        if errors and sample_error is None:
            sample_error = "; ".join(errors)
        sample_rows.append(
            {
                "index": index,
                "target_root": target,
                "warmup": index < warmup,
                "timings_ns": {key: list(values) for key, values in events.items()},
                "errors": errors,
                "status": "failed" if sample_error else "measured",
                "error": sample_error,
            }
        )

    measured_rows = [row for row in sample_rows if not row["warmup"]]
    failures = [row for row in measured_rows if row["status"] != "measured"]
    distributions: Dict[str, Any] = {}
    missing: List[str] = []
    for key in required_keys:
        values = [duration for row in measured_rows for duration in row["timings_ns"].get(key, [])]
        distributions[key] = distribution(values)
        if not values:
            missing.append(key)
    status = "pass"
    errors: List[str] = []
    if failures:
        status = "failed"
        errors.append(f"{len(failures)} measured samples failed correctness or callback execution")
    if missing:
        status = "failed"
        errors.append("required timing keys were not observed: " + ", ".join(missing))
    return {
        "name": name,
        "status": status,
        "iterations": iterations,
        "warmup": warmup,
        "required_timing_keys": list(required_keys),
        "distributions_ns": distributions,
        "sample_count": len(measured_rows),
        "correctness_checked_after_every_sample": True,
        "selection_reconciliation": "explicit SceneModelService readback after actual Qt event processing",
        "errors": errors,
        "samples": sample_rows,
    }


def run_probe(
    log_path: str,
    model_a_path: str,
    model_b_path: str,
    report_path: str,
    iterations: int,
    warmup: int,
) -> None:
    """Run the real Maya GUI probe and write a fail-closed report."""

    from maya import cmds

    report_file = Path(report_path)
    log_file = Path(log_path)
    report: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "error",
        "maya_version": str(cmds.about(version=True)),
        "models": {},
        "cases": [],
        "errors": [],
    }
    animator_window = None
    humanik_window = None
    recorder = _TimingRecorder()
    try:
        if bool(cmds.about(batch=True)):
            raise RuntimeError("selection sync benchmark requires Maya GUI, not batch mayapy")
        for path in (model_a_path, model_b_path):
            if not Path(path).is_file():
                raise FileNotFoundError(path)
        if iterations < 1 or warmup < 0:
            raise ValueError("iterations must be >= 1 and warmup must be >= 0")

        cmds.file(new=True, force=True)
        from mmd_tools.io.mmd_importer import import_mmd_file

        imported = []
        for path, namespace in ((model_a_path, "__selection_sync_a__"), (model_b_path, "__selection_sync_b__")):
            imported_root = import_mmd_file(
                str(Path(path).resolve()),
                options={
                    "custom_namespace": namespace,
                    "use_namespace": True,
                    "setup_rig": True,
                    "setup_bone_orientation": True,
                    "import_physics": False,
                    "import_morphs": False,
                },
            )
            imported.append(_full_root(cmds, str(imported_root)))
        if len(set(imported)) != 2 or any(":" not in root for root in imported):
            raise RuntimeError(f"expected two distinct namespaced roots, got {imported!r}")

        from mmd_tools.services.scene_model_service import SceneModelService
        from mmd_tools.plugin_main import close_animator_toolset, open_animator_toolset
        from mmd_tools.ui.humanik_window import close_humanik_window, show_humanik_window

        service = SceneModelService(cmds_module=cmds)
        discovered = [_full_root(cmds, str(root)) for root in service.list_mmd_models()]
        if set(discovered) != set(imported) or len(discovered) != 2:
            raise RuntimeError(f"scene model discovery mismatch: imported={imported!r} discovered={discovered!r}")
        imported = sorted(imported)
        report["models"] = {
            "fixtures": [str(Path(model_a_path).resolve()), str(Path(model_b_path).resolve())],
            "roots": imported,
            "namespaced": True,
            "distinct_full_dag_identities": True,
        }

        animator_window = open_animator_toolset(dockable=False)
        humanik_window = show_humanik_window(dockable=False)
        _safe_process_events()
        humanik_window.hide()
        _safe_process_events()

        animation_presenter = animator_window.animation_presenter
        humanik_presenter = humanik_window.humanik_presenter
        animation_presenter._remove_selection_sync_jobs()
        humanik_presenter.on_tab_deactivated()

        recorder.wrap(animation_presenter, "_schedule_selection_sync", "animator_selection_callback")
        recorder.wrap(animation_presenter, "_sync_picker_to_actual_selection", "picker_sync")
        recorder.wrap(animation_presenter, "_reload_for_model", "animator_model_reload")
        recorder.wrap(humanik_presenter, "_on_selection_changed", "humanik_selection_callback")
        recorder.wrap(humanik_presenter, "refresh", "humanik_refresh")
        recorder.wrap(humanik_presenter, "_resolve_display_model_root", "humanik_display_model_resolution")
        service_type = type(service)
        recorder.wrap_class_method(service_type, "list_mmd_models", "model_list")
        recorder.wrap_class_method(service_type, "resolve_model_from_selection", "model_resolution")
        recorder.wrap_class_method(service_type, "get_model_info", "model_summary")

        # Re-register the Animator callback immediately.  HumanIK is kept
        # inactive while its window is hidden; its next real showEvent will
        # install the wrapped callback through the normal lifecycle path.
        animation_presenter._install_selection_sync_job()
        humanik_presenter.on_tab_deactivated()
        _safe_process_events()

        for case_name in ("animator_only", "humanik_only", "both"):
            _activate_case(animator_window, humanik_window, case_name)
            recorder.samples.clear()
            recorder.errors.clear()
            report["cases"].append(
                _measure_case(
                    case_name,
                    cmds,
                    service,
                    animator_window,
                    imported,
                    recorder,
                    iterations,
                    warmup,
                )
            )

        failed = [case for case in report["cases"] if case.get("status") != "pass"]
        report["status"] = "failed" if failed else "pass"
        if failed:
            report["errors"].append("one or more required tab-state cases did not produce a valid measurement")
    except Exception as error:
        report["status"] = "error"
        report["errors"].append(f"{type(error).__name__}: {error}")
        report["traceback"] = traceback.format_exc()
        _log(log_file, "ERROR: " + report["errors"][-1])
    finally:
        recorder.restore()
        try:
            if humanik_window is not None:
                close_humanik_window()
        except Exception:
            pass
        try:
            if animator_window is not None:
                close_animator_toolset()
        except Exception:
            pass
        _write_report(report_file, report)
        _log(log_file, "RESULT_JSON: " + json.dumps(report, ensure_ascii=False, sort_keys=True))
        _log(log_file, COMPLETION_MARKER)


def main() -> int:
    """Launch the isolated Maya GUI host and return its report status."""

    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from tests.viewport.maya_e2e_harness import run_maya_e2e

    args = _parse_args()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"maya{args.maya}"
    report_path = out_dir / f"model_selection_sync_{suffix}.json"
    log_path = out_dir / f"model_selection_sync_{suffix}.log"
    command = (
        "import sys\n"
        "from pathlib import Path\n"
        f"project_root = Path({str(PROJECT_ROOT.as_posix())!r})\n"
        "sys.path.insert(0, str(project_root)) if str(project_root) not in sys.path else None\n"
        "from tools.probes.model_selection_sync_benchmark import run_probe\n"
        f"run_probe({str(log_path.as_posix())!r}, {str(Path(args.model_a).resolve().as_posix())!r}, "
        f"{str(Path(args.model_b).resolve().as_posix())!r}, {str(report_path.as_posix())!r}, "
        f"{int(args.iterations)}, {int(args.warmup)})\n"
    )
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
        send_label="<model-selection-sync-benchmark>",
        stale_paths=(report_path, log_path),
        terminate_process=True,
        quit_delay=3.0,
        port_error=f"commandPort :{args.port} is already open",
        report_error=f"model selection sync report missing: {report_path}",
    )
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
