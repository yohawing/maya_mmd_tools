"""Capture the Model Authoring narrow-action performance contract in Maya.

The host launches a clean Maya GUI through the existing commandPort harness.
Inside Maya, production presenters operate on the largest deterministic
authoring fixture in ``tests/data`` while this tool temporarily records the
coordinator and :class:`MayaCmdsAdapter` calls.  Product code is not
instrumented and the report does not claim a speedup; it is a before-refactor
baseline plus a fail-closed read/write scope contract.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import statistics
import sys
import time
import traceback
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = PROJECT_ROOT / "tests" / "data" / "yw_test_model_control_rig_bone_morph.pmx"
DEFAULT_OUT_DIR = PROJECT_ROOT / "build" / "reports" / "authoring_performance_contract"
DEFAULT_TEXTURE = PROJECT_ROOT / "tests" / "data" / "tex" / "diffuse.png"
COMMAND_PORT = 7765
COMPLETION_MARKER = "//-- AUTHORING_PERFORMANCE_CONTRACT_DONE --//"
SCHEMA_VERSION = 1

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
    "display_apply": 5,
    "info_focus_edit": 3,
    "refresh_visible_material_tab": 5,
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
        "morphs": len(spec.morphs),
        "display_frames": len(display_frames),
    }


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


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
        "maya_version": str(cmds.about(version=True)),
        "fixture": str(Path(model_path).resolve()),
        "cases": [],
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
            presenter.begin_edit_session()
            presenter.update_model_info(ATTR_MMD_MODEL_NAME_EN, info_state["expected"])
            presenter.end_edit_session()

        def info_oracle(_index: int) -> None:
            if cmds.getAttr(f"{root}.{ATTR_MMD_MODEL_NAME_EN}") != info_state["expected"]:
                raise AssertionError("Info metadata read-back mismatch")

        def refresh_action(_index: int) -> None:
            window.tab_widget.setCurrentWidget(material_presenter.view)
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
        failed = [case for case in report["cases"] if case["status"] != "pass"]
        report["status"] = "failed" if failed else "pass"
        if failed:
            report["errors"].append("one or more Authoring performance cases failed")
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
        "from tools.authoring_performance_contract import run_probe\n"
        f"run_probe({str(log_path.as_posix())!r}, {str(Path(args.model).resolve().as_posix())!r}, "
        f"{str(Path(args.texture).resolve().as_posix())!r}, {str(report_path.as_posix())!r}, "
        f"{int(args.iterations)}, {int(args.warmup)}, {int(args.min_vertices)}, {int(args.min_bones)})\n"
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
        send_label="<authoring-performance-contract>",
        stale_paths=(report_path, log_path),
        terminate_process=True,
        quit_delay=3.0,
        port_error=f"commandPort :{args.port} is already open",
        report_error=f"authoring performance report missing: {report_path}",
    )
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
