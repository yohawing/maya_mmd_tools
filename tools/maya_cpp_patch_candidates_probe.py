"""Measure whether narrow Authoring writes justify a native batch command.

This is a decision probe, not a benchmark claim.  It runs unchanged production
coordinator paths in Maya 2024 and 2026, counts calls at the ``maya.cmds``
boundary, and verifies the exact Undo/Redo contract outside the timed interval.
The combined JSON report deliberately rejects single-plug Display and Info
writes; only a multi-plug Material path can become an implementation candidate.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
import traceback
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = PROJECT_ROOT / "tests" / "data" / "yw_test_model_control_rig_bone_morph.pmx"
DEFAULT_OUT_DIR = PROJECT_ROOT / "build" / "reports" / "cpp_patch_candidates"
COMPLETION_MARKER = "//-- CPP_PATCH_CANDIDATES_DONE --//"
SCHEMA_VERSION = 2
DEFAULT_ITERATIONS = 7
COLD_ITERATIONS = 3
MIN_BATCH_TRANSACTION_PLUGS = 16
MIN_BATCH_MAYA_CALLS = 128
EXPECTED_CASES = frozenset(
    {
        "material_value_n1",
        "material_value_n4",
        "material_value_n8",
        "material_value_outline_n7",
        "display_json_n1",
        "info_string_n1",
    }
)

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

MATERIAL_FIELD_SCALES = (
    (1, ("name_english",)),
    (4, ("name_english", "memo", "edge_size", "specular_coefficient")),
    (
        8,
        (
            "name_english",
            "memo",
            "edge_size",
            "specular_coefficient",
            "diffuse",
            "ambient",
            "edge_color",
            "draw_flags",
        ),
    ),
)


def distribution(samples_ns: Sequence[int]) -> Dict[str, Any]:
    """Return nearest-rank timing statistics suitable for JSON evidence."""

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
    """Return the same nearest-rank distribution with count-valued keys."""

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


def _command_targets(name: str, args: Sequence[Any], kwargs: Mapping[str, Any]) -> tuple[set[str], set[str]]:
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


def _stable_value(value: Any) -> Any:
    """Normalize Maya tuple/list and float representation for exact oracles."""

    if isinstance(value, Mapping):
        return {str(key): _stable_value(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_stable_value(item) for item in value]
    if isinstance(value, float):
        return round(value, 7)
    return value


def _material_equal(actual: Any, expected: Any) -> bool:
    """Compare complete material semantics with Maya float round-trip normalization."""

    return _stable_value(actual.to_mapping()) == _stable_value(expected.to_mapping())


def _measure_case(
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


def _material_variant(material: Any, fields: Iterable[str], phase: int) -> Any:
    odd = bool(phase % 2)
    changes: Dict[str, Any] = {}
    for field in fields:
        if field == "name_english":
            changes[field] = "CPP candidate A" if odd else "CPP candidate B"
        elif field == "memo":
            changes[field] = "probe-a" if odd else "probe-b"
        elif field == "edge_size":
            changes[field] = 1.125 if odd else 1.375
        elif field == "specular_coefficient":
            changes[field] = 5.25 if odd else 7.75
        elif field == "diffuse":
            changes[field] = (0.31, 0.42, 0.53, 0.64) if odd else (0.61, 0.52, 0.43, 0.74)
        elif field == "ambient":
            changes[field] = (0.12, 0.23, 0.34) if odd else (0.32, 0.21, 0.14)
        elif field == "edge_color":
            changes[field] = (0.11, 0.22, 0.33, 0.44) if odd else (0.41, 0.32, 0.23, 0.14)
        elif field == "draw_flags":
            changes[field] = int(material.draw_flags) ^ 0x10
        else:
            raise KeyError(field)
    return replace(material, **changes)


def _outline_snapshot(cmds: Any, shader: str) -> Dict[str, Any]:
    attrs = ("technique", "EdgeSize", "mmd_shader_outline_enabled", "mmdDoubleSided", "mmdTransparencyMode")
    result: Dict[str, Any] = {}
    for attr in attrs:
        exists = bool(cmds.attributeQuery(attr, node=shader, exists=True))
        result[attr] = {
            "exists": exists,
            "value": _stable_value(cmds.getAttr(f"{shader}.{attr}")) if exists else None,
        }
    return result


def run_probe(
    log_path: str,
    model_path: str,
    report_path: str,
    iterations: int,
) -> None:
    """Execute unchanged production paths inside one Maya GUI process."""

    from maya import cmds

    report: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "error",
        "maya_version": str(cmds.about(version=True)),
        "fixture": str(Path(model_path).resolve()),
        "measurement": {
            "call_boundary": "raw maya.cmds Python API calls, including adapter-mediated and direct calls",
            "wall_clock": "production action with maya.cmds wrappers fully uninstalled",
            "sample_protocol": "each sample times one action, verifies its Undo/Redo, then runs an opposite-phase equivalent action for call characterization and verifies its Undo/Redo",
            "cold": "first action through a freshly built production authoring composition",
            "warm": "subsequent equivalent actions in the same Maya scene",
            "vp2_override": os.environ.get("MAYA_VP2_DEVICE_OVERRIDE"),
        },
        "cases": [],
        "errors": [],
    }
    recorder = MayaCommandRecorder(cmds)
    window = None
    try:
        if bool(cmds.about(batch=True)):
            raise RuntimeError("C++ patch candidate probe requires Maya GUI")
        if os.environ.get("MMD_CPP_PATCH_CANDIDATES_OWNED") != "1":
            raise RuntimeError("refusing to replace a scene outside a probe-owned Maya process")
        if iterations < 2:
            raise ValueError("iterations must be >= 2")
        if not Path(model_path).is_file():
            raise FileNotFoundError(model_path)
        cmds.file(new=True, force=True)
        from tests.common.maya_plugin_setup import load_mmd_tools_plugin

        load_mmd_tools_plugin(PROJECT_ROOT, cmds_module=cmds)
        cmds.loadPlugin("dx11Shader", quiet=True)
        from mmd_tools.io.mmd_importer import import_mmd_file
        from mmd_tools.core import settings

        previous_create = settings.get("import.model.create_mmd_shaders")
        previous_backend = settings.get("import.model.mmd_shader_backend")
        try:
            settings.set("import.model.create_mmd_shaders", True)
            settings.set("import.model.mmd_shader_backend", "dx11")
            root = import_mmd_file(
                str(Path(model_path).resolve()),
                options={
                    "scale": 1.0,
                    "import_physics": False,
                    "setup_rig": False,
                    "setup_bone_orientation": False,
                    "create_mmd_control_rig": False,
                    "import_morphs": True,
                    "create_mmd_shaders": True,
                    "use_cpp_fast_load": False,
                    "use_native_pmx_parse": False,
                    "require_native_pmx_parse": False,
                },
            )
        finally:
            settings.set("import.model.create_mmd_shaders", previous_create)
            settings.set("import.model.mmd_shader_backend", previous_backend)
        matches = cmds.ls(str(root), long=True) or []
        if len(matches) != 1:
            raise RuntimeError(f"imported root is not unique: {matches!r}")
        root = str(matches[0])

        from mmd_tools.core.constants import ATTR_MMD_DISPLAY_FRAMES_JSON, ATTR_MMD_MODEL_NAME_EN
        from mmd_tools.ui.main_window import MainWindow

        window = MainWindow()
        window.app_state.current_model_root = root
        coordinator = window.authoring_composition.coordinator
        from mmd_tools.adapters.maya_authoring_factory import build_maya_authoring_composition

        def prepare_cold() -> None:
            nonlocal coordinator
            coordinator = build_maya_authoring_composition(cmds).coordinator

        spec = coordinator.read_spec(root)
        if not spec.materials:
            raise RuntimeError("fixture exposes no material")
        material = spec.materials[0]
        shader = str(material.binding_identity)
        shader_type = str(cmds.nodeType(shader))
        report["model"] = {
            "root": root,
            "material_count": len(spec.materials),
            "selected_shader": shader,
            "selected_shader_type": shader_type,
        }
        if shader_type != "dx11Shader":
            raise RuntimeError(f"outline case requires dx11Shader, got {shader_type!r}")

        for semantic_count, fields in MATERIAL_FIELD_SCALES:
            state: Dict[str, Any] = {"current": coordinator.read_material_value(root, material.index, shader)}
            preimages: Dict[int, Any] = {}
            targets: Dict[int, Any] = {}

            def material_action(index: int, _fields=fields) -> None:
                current = state["current"]
                target = _material_variant(current, _fields, index)
                preimages[index] = current
                targets[index] = target
                state["current"] = coordinator.apply_material_value_patch(root, target)

            def material_target(index: int) -> None:
                actual = coordinator.read_material_value(root, material.index, shader)
                if not _material_equal(actual, targets[index]):
                    raise AssertionError("material target readback mismatch")

            def material_undo_redo(index: int) -> None:
                cmds.undo()
                if not _material_equal(
                    coordinator.read_material_value(root, material.index, shader), preimages[index]
                ):
                    raise AssertionError("material Undo preimage mismatch")
                cmds.redo()
                actual = coordinator.read_material_value(root, material.index, shader)
                if not _material_equal(actual, targets[index]):
                    raise AssertionError("material Redo target mismatch")
                state["current"] = actual

            report["cases"].append(
                _measure_case(
                    name=f"material_value_n{semantic_count}",
                    recorder=recorder,
                    action=material_action,
                    verify_target=material_target,
                    verify_undo_redo=material_undo_redo,
                    iterations=iterations,
                    semantic_field_count=semantic_count,
                    prepare_cold=prepare_cold,
                )
            )

        outline_state: Dict[str, Any] = {
            "current": coordinator.read_material_value(root, material.index, shader),
        }
        outline_preimages: Dict[int, Any] = {}
        outline_targets: Dict[int, Any] = {}
        outline_attr_preimages: Dict[int, Any] = {}
        outline_attr_targets: Dict[int, Any] = {}
        outline_attr_state = {"current": _outline_snapshot(cmds, shader)}

        def outline_action(index: int) -> None:
            current = outline_state["current"]
            target = _material_variant(current, ("name_english", "edge_size"), index)
            outline_preimages[index] = current
            outline_attr_preimages[index] = outline_attr_state["current"]
            enabled = bool(index % 2)
            outline_targets[index] = target
            outline_state["current"] = coordinator.apply_material_value_patch(
                root, target, outline_enabled=enabled
            )

        def outline_target(index: int) -> None:
            if not _material_equal(
                coordinator.read_material_value(root, material.index, shader), outline_targets[index]
            ):
                raise AssertionError("material outline semantic target mismatch")
            actual_outline = _outline_snapshot(cmds, shader)
            outline_attr_targets[index] = actual_outline
            outline_enabled = actual_outline["mmd_shader_outline_enabled"]
            if not outline_enabled["exists"] or bool(outline_enabled["value"]) != bool(index % 2):
                raise AssertionError("material outline attr target mismatch")

        def outline_undo_redo(index: int) -> None:
            cmds.undo()
            if not _material_equal(
                coordinator.read_material_value(root, material.index, shader), outline_preimages[index]
            ):
                raise AssertionError("material outline Undo semantic mismatch")
            if _outline_snapshot(cmds, shader) != outline_attr_preimages[index]:
                raise AssertionError("material outline Undo attr mismatch")
            cmds.redo()
            actual = coordinator.read_material_value(root, material.index, shader)
            if not _material_equal(actual, outline_targets[index]):
                raise AssertionError("material outline Redo semantic mismatch")
            if _outline_snapshot(cmds, shader) != outline_attr_targets[index]:
                raise AssertionError("material outline Redo attr mismatch")
            outline_state["current"] = actual
            outline_attr_state["current"] = outline_attr_targets[index]

        report["cases"].append(
            _measure_case(
                name="material_value_outline_n7",
                recorder=recorder,
                action=outline_action,
                verify_target=outline_target,
                verify_undo_redo=outline_undo_redo,
                iterations=iterations,
                semantic_field_count=2,
                prepare_cold=prepare_cold,
            )
        )

        display_plug = f"{root}.{ATTR_MMD_DISPLAY_FRAMES_JSON}"
        display_state = {"current": str(cmds.getAttr(display_plug) or "[]")}
        display_preimages: Dict[int, str] = {}
        display_targets: Dict[int, str] = {}

        def display_action(index: int) -> None:
            current = display_state["current"]
            payload = json.loads(current)
            payload[0]["name_english"] = "CPP Display A" if index % 2 else "CPP Display B"
            target = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            display_preimages[index] = current
            display_targets[index] = target
            coordinator.write_display_frames(root, target)
            display_state["current"] = target

        def display_target(index: int) -> None:
            if cmds.getAttr(display_plug) != display_targets[index]:
                raise AssertionError("Display target mismatch")

        def display_undo_redo(index: int) -> None:
            cmds.undo()
            if cmds.getAttr(display_plug) != display_preimages[index]:
                raise AssertionError("Display Undo preimage mismatch")
            cmds.redo()
            if cmds.getAttr(display_plug) != display_targets[index]:
                raise AssertionError("Display Redo target mismatch")

        report["cases"].append(
            _measure_case(
                name="display_json_n1",
                recorder=recorder,
                action=display_action,
                verify_target=display_target,
                verify_undo_redo=display_undo_redo,
                iterations=iterations,
                semantic_field_count=1,
                prepare_cold=prepare_cold,
            )
        )

        info_plug = f"{root}.{ATTR_MMD_MODEL_NAME_EN}"
        info_state = {"current": str(cmds.getAttr(info_plug) or "")}
        info_preimages: Dict[int, str] = {}
        info_targets: Dict[int, str] = {}

        def info_action(index: int) -> None:
            target = "CPP Info A" if index % 2 else "CPP Info B"
            info_preimages[index] = info_state["current"]
            info_targets[index] = target
            session = coordinator.begin_info_metadata_edit(root, ATTR_MMD_MODEL_NAME_EN)
            coordinator.update_info_metadata_edit(session, target)
            coordinator.commit_info_metadata_edit(session)
            info_state["current"] = target

        def info_target(index: int) -> None:
            if cmds.getAttr(info_plug) != info_targets[index]:
                raise AssertionError("Info target mismatch")

        def info_undo_redo(index: int) -> None:
            cmds.undo()
            if cmds.getAttr(info_plug) != info_preimages[index]:
                raise AssertionError("Info Undo preimage mismatch")
            cmds.redo()
            if cmds.getAttr(info_plug) != info_targets[index]:
                raise AssertionError("Info Redo target mismatch")

        report["cases"].append(
            _measure_case(
                name="info_string_n1",
                recorder=recorder,
                action=info_action,
                verify_target=info_target,
                verify_undo_redo=info_undo_redo,
                iterations=iterations,
                semantic_field_count=1,
                prepare_cold=prepare_cold,
            )
        )

        failed = [case for case in report["cases"] if case["status"] != "pass"]
        report["status"] = "failed" if failed else "pass"
        if failed:
            report["errors"].append("one or more candidate measurements failed")
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
        except Exception:
            pass
        report_file = Path(report_path)
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        log_file = Path(log_path)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write("RESULT_JSON: " + json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n")
            handle.write(COMPLETION_MARKER + "\n")


def _validate_decision_report(report: Any) -> List[str]:
    """Return schema errors instead of allowing malformed evidence to escape."""

    errors: List[str] = []
    if not isinstance(report, Mapping):
        return ["report must be a mapping"]
    version = str(report.get("maya_version", ""))
    prefix = f"maya{version or '?'}"
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"{prefix}: schema_version must be {SCHEMA_VERSION}")
    if version not in {"2024", "2026"}:
        errors.append(f"{prefix}: unsupported maya_version")
    if report.get("status") != "pass":
        errors.append(f"{prefix}: report status must be pass")
    top_errors = report.get("errors")
    if top_errors not in (None, []):
        errors.append(f"{prefix}: top-level errors must be empty")
    if "traceback" in report:
        errors.append(f"{prefix}: traceback must be absent")
    if not isinstance(report.get("fixture"), str) or not report.get("fixture"):
        errors.append(f"{prefix}: fixture must be a non-empty string")
    model = report.get("model")
    if not isinstance(model, Mapping) or model.get("selected_shader_type") != "dx11Shader":
        errors.append(f"{prefix}: selected shader must be dx11Shader")
    measurement = report.get("measurement")
    if not isinstance(measurement, Mapping) or "raw maya.cmds" not in str(
        measurement.get("call_boundary", "")
    ):
        errors.append(f"{prefix}: raw maya.cmds call boundary is missing")
    if not isinstance(measurement, Mapping) or measurement.get("vp2_override") != "VirtualDeviceDx11":
        errors.append(f"{prefix}: VP2 override must be VirtualDeviceDx11")

    cases = report.get("cases")
    if not isinstance(cases, list):
        errors.append(f"{prefix}: cases must be a list")
        return errors
    names: List[str] = []
    for case in cases:
        name = case.get("name") if isinstance(case, Mapping) else None
        if not isinstance(name, str) or not name:
            errors.append(f"{prefix}: every case name must be a non-empty string")
            continue
        names.append(name)
    if len(names) != len(cases) or len(names) != len(set(names)):
        errors.append(f"{prefix}: case names must be unique")
    if set(names) != EXPECTED_CASES:
        errors.append(f"{prefix}: exact required case set is missing")

    def number(
        mapping: Any, key: str, label: str, *, integer: bool = False
    ) -> Optional[float]:
        if not isinstance(mapping, Mapping):
            errors.append(f"{label}: parent must be a mapping")
            return None
        value = mapping.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{label}: {key} must be numeric")
            return None
        if integer and not isinstance(value, int):
            errors.append(f"{label}: {key} must be an integer")
            return None
        value = float(value)
        if not math.isfinite(value) or value < 0.0:
            errors.append(f"{label}: {key} must be finite and non-negative")
            return None
        return value

    for case in cases:
        if not isinstance(case, Mapping):
            continue
        label = f"{prefix}:{case.get('name', '?')}"
        if case.get("status") != "pass" or case.get("failures") != 0:
            errors.append(f"{label}: case must pass with zero failures")
        if case.get("undo_boundary") != "one_action_one_undo_redo":
            errors.append(f"{label}: Undo boundary evidence is missing")
        if case.get("semantic_parity") != "exact_preimage_and_target":
            errors.append(f"{label}: semantic parity evidence is missing")
        if case.get("cold_iterations") != COLD_ITERATIONS:
            errors.append(f"{label}: cold iteration count mismatch")
        warm_iterations = number(case, "warm_iterations", label, integer=True)
        if warm_iterations is not None and warm_iterations < 2:
            errors.append(f"{label}: at least two warm iterations are required")
        cold = case.get("cold_timing_ns")
        warm = case.get("warm_timing_ns")
        calls = case.get("warm_maya_calls")
        cold_count = number(cold, "count", f"{label}:cold", integer=True)
        warm_count = number(warm, "count", f"{label}:warm", integer=True)
        if cold_count is not None and cold_count != COLD_ITERATIONS:
            errors.append(f"{label}: cold distribution count mismatch")
        if warm_count is not None and warm_iterations is not None and warm_count != warm_iterations:
            errors.append(f"{label}: warm distribution count mismatch")
        for row, row_label in ((cold, "cold"), (warm, "warm")):
            p50 = number(row, "p50_ns", f"{label}:{row_label}", integer=True)
            p95 = number(row, "p95_ns", f"{label}:{row_label}", integer=True)
            if p50 is not None and p95 is not None and p95 < p50:
                errors.append(f"{label}:{row_label}: p95 must be >= p50")
        number(calls, "p50", f"{label}:calls", integer=True)
        transaction_plugs = number(
            case, "observed_transaction_plug_count", label, integer=True
        )
        target_plugs = number(case, "observed_target_plug_count", label, integer=True)
        write_plugs = number(case, "observed_write_plug_count", label, integer=True)
        number(case, "observed_target_node_count", label, integer=True)
        number(case, "semantic_field_count", label, integer=True)
        if (
            transaction_plugs is not None
            and write_plugs is not None
            and write_plugs > transaction_plugs
        ):
            errors.append(f"{label}: write plugs exceed transaction plugs")
        if transaction_plugs is not None and transaction_plugs != target_plugs:
            errors.append(f"{label}: transaction plugs must equal target plugs")
        samples = case.get("samples")
        if not isinstance(samples, list):
            errors.append(f"{label}: samples must be a list")
            continue
        if len(samples) != COLD_ITERATIONS + int(warm_iterations or 0):
            errors.append(f"{label}: sample count mismatch")
        cold_samples: List[Mapping[str, Any]] = []
        warm_samples: List[Mapping[str, Any]] = []
        for sample_index, sample in enumerate(samples):
            sample_label = f"{label}:sample{sample_index}"
            if not isinstance(sample, Mapping):
                errors.append(f"{sample_label}: sample must be a mapping")
                continue
            expected_temperature = "cold" if sample_index < COLD_ITERATIONS else "warm"
            if sample.get("temperature") != expected_temperature:
                errors.append(f"{sample_label}: temperature/order mismatch")
            if sample.get("status") != "pass" or sample.get("error") is not None:
                errors.append(f"{sample_label}: sample must pass without error")
            elapsed = number(sample, "elapsed_ns", sample_label, integer=True)
            maya_calls = number(sample, "maya_call_count", sample_label, integer=True)
            sample_transaction = number(
                sample, "transaction_plug_count", sample_label, integer=True
            )
            sample_writes = number(sample, "write_plug_count", sample_label, integer=True)
            sample_nodes = number(sample, "target_node_count", sample_label, integer=True)
            sample_target_plugs = number(
                sample, "target_plug_count", sample_label, integer=True
            )
            if (
                sample_transaction is not None
                and sample_writes is not None
                and sample_writes > sample_transaction
            ):
                errors.append(f"{sample_label}: write plugs exceed transaction plugs")
            if (
                sample_transaction is not None
                and sample_target_plugs is not None
                and sample_transaction != sample_target_plugs
            ):
                errors.append(f"{sample_label}: transaction plugs must equal target plugs")
            by_method = sample.get("maya_calls_by_method")
            if not isinstance(by_method, Mapping):
                errors.append(f"{sample_label}: maya_calls_by_method must be a mapping")
            else:
                method_total = 0.0
                for method, count in by_method.items():
                    value = number(
                        {"count": count},
                        "count",
                        f"{sample_label}:{method}",
                        integer=True,
                    )
                    if value is not None:
                        method_total += value
                if maya_calls is not None and method_total != maya_calls:
                    errors.append(f"{sample_label}: method counts do not sum to maya_call_count")
            if elapsed is None or maya_calls is None or sample_transaction is None or sample_nodes is None:
                continue
            if expected_temperature == "cold":
                cold_samples.append(sample)
            else:
                warm_samples.append(sample)
        if len(cold_samples) == COLD_ITERATIONS:
            expected_cold = distribution([int(sample["elapsed_ns"]) for sample in cold_samples])
            if cold != expected_cold:
                errors.append(f"{label}: cold aggregate does not match samples")
        if warm_iterations is not None and len(warm_samples) == int(warm_iterations):
            expected_warm = distribution([int(sample["elapsed_ns"]) for sample in warm_samples])
            expected_calls = count_distribution(
                [int(sample["maya_call_count"]) for sample in warm_samples]
            )
            if warm != expected_warm:
                errors.append(f"{label}: warm aggregate does not match samples")
            if calls != expected_calls:
                errors.append(f"{label}: call aggregate does not match samples")
        if samples and all(isinstance(sample, Mapping) for sample in samples):
            sample_to_case = {
                "target_node_count": "observed_target_node_count",
                "target_plug_count": "observed_target_plug_count",
                "transaction_plug_count": "observed_transaction_plug_count",
                "write_plug_count": "observed_write_plug_count",
            }
            for sample_key, case_key in sample_to_case.items():
                values = [sample.get(sample_key) for sample in samples]
                if not all(
                    not isinstance(value, bool)
                    and isinstance(value, (int, float))
                    and math.isfinite(float(value))
                    for value in values
                ):
                    continue
                if case.get(case_key) != max(values):
                    errors.append(f"{label}: {case_key} does not match samples")
    return errors


def build_decision(reports: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Turn complete cross-version measurements into fixed candidate decisions."""

    validation_errors: List[str] = []
    versions: Dict[str, Mapping[str, Any]] = {}
    for index, report in enumerate(reports):
        validation_errors.extend(_validate_decision_report(report))
        if not isinstance(report, Mapping):
            continue
        version = str(report.get("maya_version", ""))
        if version in versions:
            validation_errors.append(f"duplicate maya_version: {version}")
        else:
            versions[version] = report
    if len(reports) != 2:
        validation_errors.append("exactly two version reports are required")
    fixtures = {
        str(report.get("fixture"))
        for report in versions.values()
        if isinstance(report.get("fixture"), str)
    }
    if len(fixtures) > 1:
        validation_errors.append("cross-version fixtures differ")
    complete = set(versions) == {"2024", "2026"} and not validation_errors
    rule = {
        "min_transaction_plugs": MIN_BATCH_TRANSACTION_PLUGS,
        "min_warm_maya_calls_p50": MIN_BATCH_MAYA_CALLS,
        "adopt": "all versions and exact cases pass parity; every candidate case validates at least 16 plugs and crosses 128 raw maya.cmds calls",
        "reject": "single-plug transaction or incomplete cross-version evidence",
        "decision_policy": "crossing the Python to native language boundary is justified only when every supported Maya validates at least 16 transaction plugs and performs at least 128 raw maya.cmds calls; lower-volume writes remain Python-owned",
    }
    if not complete:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "incomplete",
            "validation_errors": validation_errors,
            "required_versions": ["2024", "2026"],
            "rule": rule,
            "candidates": [
                {
                    "id": candidate_id,
                    "decision": "do_not_adopt",
                    "reason": "evidence schema is incomplete or invalid",
                    "evidence": [],
                }
                for candidate_id in (
                    "material_value_batch_command",
                    "material_outline_batch_command",
                    "display_json_command",
                    "info_string_command",
                )
            ],
        }

    def evidence(prefixes: Sequence[str]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for version, report in sorted(versions.items()):
            selected = [
                case for case in report.get("cases", ())
                if any(str(case.get("name", "")).startswith(prefix) for prefix in prefixes)
            ]
            rows.append(
                {
                    "maya_version": version,
                    "cases": [
                        {
                            "name": case["name"],
                            "warm_p50_ns": case["warm_timing_ns"].get("p50_ns"),
                            "warm_p95_ns": case["warm_timing_ns"].get("p95_ns"),
                            "warm_maya_calls_p50": case["warm_maya_calls"].get("p50"),
                            "target_node_count": case["observed_target_node_count"],
                            "transaction_plug_count": case["observed_transaction_plug_count"],
                            "write_plug_count": case["observed_write_plug_count"],
                            "semantic_field_count": case["semantic_field_count"],
                        }
                        for case in selected
                    ],
                }
            )
        return rows

    material_rows = evidence(("material_value_n",))
    outline_rows = evidence(("material_value_outline",))
    display_rows = evidence(("display_json",))
    info_rows = evidence(("info_string",))

    def multi_plug_and_expensive(rows: Sequence[Mapping[str, Any]]) -> bool:
        cases = [case for row in rows for case in row["cases"]]
        return bool(cases) and all(
            int(case["transaction_plug_count"]) >= MIN_BATCH_TRANSACTION_PLUGS
            and int(case["warm_maya_calls_p50"] or 0) >= MIN_BATCH_MAYA_CALLS
            for case in cases
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "complete" if complete else "incomplete",
        "validation_errors": validation_errors,
        "required_versions": ["2024", "2026"],
        "rule": rule,
        "candidates": [
            {
                "id": "material_value_batch_command",
                "decision": "adopt_for_implementation" if complete and multi_plug_and_expensive(material_rows) else "do_not_adopt",
                "reason": "the selected-material transaction validates a multi-plug semantic fingerprint even when one field is written" if complete and multi_plug_and_expensive(material_rows) else "cross-version multi-plug transaction threshold not met",
                "evidence": material_rows,
            },
            {
                "id": "material_outline_batch_command",
                "decision": "adopt_for_implementation" if complete and multi_plug_and_expensive(outline_rows) else "do_not_adopt",
                "reason": "outline policy spans several shader plugs and repeated capture/readback" if complete and multi_plug_and_expensive(outline_rows) else "cross-version multi-plug threshold not met",
                "evidence": outline_rows,
            },
            {
                "id": "display_json_command",
                "decision": "do_not_adopt",
                "reason": "one root string plug; native command would duplicate an already narrow Python transaction",
                "evidence": display_rows,
            },
            {
                "id": "info_string_command",
                "decision": "do_not_adopt",
                "reason": "one focused string plug; keep the event-spanning Python transaction",
                "evidence": info_rows,
            },
        ],
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", nargs="+", default=("2024", "2026"))
    parser.add_argument("--model", default=str(DEFAULT_MODEL))
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--port", type=int, default=7770)
    return parser.parse_args()


def main() -> int:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from tests.viewport.maya_e2e_harness import run_maya_e2e

    args = _parse_args()
    versions = [str(value) for value in args.maya]
    if versions != ["2024", "2026"] and len(versions) != 1:
        raise SystemExit("--maya must be one version or the ordered pair: 2024 2026")
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    reports: List[Mapping[str, Any]] = []
    exit_code = 0
    for offset, version in enumerate(versions):
        report_path = out_dir / f"cpp_patch_candidates_maya{version}.json"
        log_path = out_dir / f"cpp_patch_candidates_maya{version}.log"
        command = (
            "import sys\n"
            "from pathlib import Path\n"
            f"project_root = Path({str(PROJECT_ROOT.as_posix())!r})\n"
            "sys.path.insert(0, str(project_root)) if str(project_root) not in sys.path else None\n"
            "from tools.maya_cpp_patch_candidates_probe import run_probe\n"
            f"run_probe({str(log_path.as_posix())!r}, {str(Path(args.model).resolve().as_posix())!r}, "
            f"{str(report_path.as_posix())!r}, {int(args.iterations)})\n"
        )
        try:
            report = run_maya_e2e(
                project_root=PROJECT_ROOT,
                version=version,
                out_dir=out_dir,
                port=int(args.port) + offset,
                timeout=float(args.timeout),
                log_path=log_path,
                report_path=report_path,
                command=command,
                marker=COMPLETION_MARKER,
                send_label=f"<cpp-patch-candidates-{version}>",
                stale_paths=(report_path, log_path),
                terminate_process=True,
                quit_delay=3.0,
                port_error=f"commandPort :{int(args.port) + offset} is already open",
                report_error=f"C++ patch candidate report missing: {report_path}",
                env_overrides={
                    "MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11",
                    "MMD_CPP_PATCH_CANDIDATES_OWNED": "1",
                },
            )
            reports.append(report)
            if report.get("status") != "pass":
                exit_code = 1
        except Exception as exc:
            exit_code = 1
            reports.append({"maya_version": version, "status": "error", "errors": [str(exc)]})
    decision = build_decision(reports)
    (out_dir / "cpp_patch_candidates_decision.json").write_text(
        json.dumps(decision, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if decision["status"] != "complete":
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
