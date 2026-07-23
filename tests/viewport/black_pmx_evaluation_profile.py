"""Measure Black.pmx Maya evaluation cost across deterministic conditions.

This probe is investigation infrastructure for ``BLACK-PMX-EVALUATION-PROFILE-1``.
It imports the production PMX path once per evaluation-manager mode, then
measures static, scrub, and playback-like frame sequences with physics and the
Maya Profiler sampling switch independently controlled.  It records solver and
world state in the same interval as each wall-time sample.  No production node
or connection is modified; world/solver enable values are restored before the
next condition and the original evaluation-manager mode is restored at exit.

The mayapy runtime has no guaranteed model panel, so display-related rows use
``refresh(force=True)`` and explicitly report ``unsupported`` when no model
panel exists.  Maya Profiler's event stream is retained as bounded raw data;
node-type timing is reported as unsupported because the command API exposes
event names/descriptions rather than a node-type field.

Usage::

    mayapy tests/viewport/black_pmx_evaluation_profile.py --pmx F:/MMD/.../Black.pmx
    uvx nox -s black_pmx_evaluation_profile -- --maya 2024
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Sequence


DEFAULT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PMX = r"F:\MMD\pmx\Sour式初音ミクVer.1.02\Black.pmx"
DEFAULT_REPORT = "build/reports/black_pmx_evaluation_profile.json"
_EVALUATION_MODES = ("off", "serial", "parallel")
_SEQUENCES = ("static", "scrub", "playback")
_PHYSICS_STATES = ("off", "on", "solver-off")
_PROFILER_STATES = ("off", "on")
_DISPLAY_MODES = ("headless", "viewport")
_SOLVER_ATTRS = ("enable", "outStatus", "outBoneCount", "outSolved")
_CENSUS_TYPES = (
    "joint",
    "skinCluster",
    "blendShape",
    "mmdRigidBodyShape",
    "mmdPhysicsJointShape",
    "mmdPhysicsBoneDriver",
    "mmdCcdIk",
    "mmdAppend",
    "mmdPhysicsWorldShape",
    "mmdPhysicsSolver",
)


def _parse_csv(raw: str, allowed: Iterable[str], label: str) -> List[str]:
    """Parse a stable, de-duplicated comma-separated option."""
    values = [part.strip().lower() for part in str(raw).split(",") if part.strip()]
    if not values:
        raise ValueError(f"--{label} must contain at least one value")
    invalid = sorted(set(values) - set(allowed))
    if invalid:
        raise ValueError(f"Unsupported {label}: {', '.join(invalid)}")
    return list(dict.fromkeys(values))


def _parse_frames(raw: str) -> List[int]:
    """Parse deterministic Maya frame numbers."""
    frames = [int(part.strip()) for part in str(raw).split(",") if part.strip()]
    if not frames:
        raise ValueError("--frames must contain at least one integer")
    return frames


def _resolve_path(value: str | Path, root: Path = DEFAULT_ROOT) -> Path:
    """Resolve a path relative to the repository for local probe arguments."""
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _json_safe(value: Any) -> Any:
    """Convert Maya values to bounded JSON-compatible data."""
    if isinstance(value, str):
        # Maya may return localized strings containing lone UTF-16 surrogates
        # when a PMX contains malformed/non-native text.  Keep the evidence
        # serializable without allowing one attribute to abort the report.
        return value.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        items = [_json_safe(item) for item in value]
        return items if len(items) <= 32 else {"count": len(items), "sample": items[:8]}
    return str(value)


def _safe_get_attr(cmds: Any, plug: str) -> Any:
    try:
        return _json_safe(cmds.getAttr(plug))
    except Exception as exc:
        return {"error": str(exc)}


def _safe_node_type(cmds: Any, node: str) -> str | None:
    try:
        return str(cmds.nodeType(node))
    except Exception:
        return None


def _evaluation_state(cmds: Any) -> Dict[str, Any]:
    """Capture only canonical evaluation-manager mode values."""
    try:
        raw = cmds.evaluationManager(query=True, mode=True) or []
    except Exception as exc:
        return {"modes": [], "error": str(exc)}
    modes = [str(mode).lower() for mode in raw if str(mode).lower() in _EVALUATION_MODES]
    if not modes and raw:
        modes = [str(raw[0])]
    return {"modes": modes}


def _set_evaluation_mode(cmds: Any, mode: str) -> Dict[str, Any]:
    before = _evaluation_state(cmds)
    cmds.evaluationManager(mode=mode)
    return {"requested": mode, "before": before, "after": _evaluation_state(cmds)}


def _restore_evaluation_mode(cmds: Any, state: Mapping[str, Any]) -> Dict[str, Any]:
    modes = [str(mode) for mode in state.get("modes") or []]
    if not modes:
        return {"requested": None, "after": _evaluation_state(cmds), "restored": True}
    try:
        cmds.evaluationManager(mode=modes[0])
        after = _evaluation_state(cmds)
        return {"requested": modes[0], "after": after, "restored": after.get("modes") == modes}
    except Exception as exc:
        return {"requested": modes[0], "after": _evaluation_state(cmds), "restored": False, "error": str(exc)}


def _load_plugin(repo_root: Path, cmds: Any) -> Dict[str, Any]:
    """Load the canonical Python plugin and preserve path/type evidence."""
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from tests.common.maya_plugin_setup import load_mmd_tools_plugin

    plugin_path = load_mmd_tools_plugin(repo_root, cmds_module=cmds)
    loaded_name = None
    loaded_path = None
    for name in cmds.pluginInfo(query=True, listPlugins=True) or []:
        try:
            if cmds.pluginInfo(name, query=True, loaded=True):
                candidate = Path(cmds.pluginInfo(name, query=True, path=True)).resolve()
                if candidate == plugin_path.resolve():
                    loaded_name, loaded_path = str(name), str(candidate)
                    break
        except Exception:
            continue
    return {
        "path": str(plugin_path.resolve()),
        "type": "python",
        "loaded": bool(loaded_name),
        "name": loaded_name,
        "resolvedPath": loaded_path,
    }


def _load_model(path: Path, importer: Callable[..., Any]) -> str:
    root = importer(
        str(path),
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
        raise RuntimeError(f"PMX import failed: {path}")
    return str(root)


def _find_solver(cmds: Any, root: str) -> str | None:
    try:
        candidates = cmds.listConnections(
            f"{root}.message", source=False, destination=True, type="mmdPhysicsSolver"
        ) or []
    except Exception:
        candidates = []
    if candidates:
        return str(candidates[0])
    for candidate in cmds.ls(type="mmdPhysicsSolver", long=True) or []:
        try:
            owners = cmds.listConnections(f"{candidate}.modelRoot", source=True, destination=False) or []
        except Exception:
            owners = []
        if root in owners or str(root).lstrip("|") in {str(item).lstrip("|") for item in owners}:
            return str(candidate)
    return None


def _find_worlds(cmds: Any, solver: str | None) -> List[str]:
    worlds: List[str] = []
    if solver and cmds.attributeQuery("inWorldSettings", node=solver, exists=True):
        try:
            worlds.extend(str(item) for item in cmds.listConnections(
                f"{solver}.inWorldSettings", source=True, destination=False
            ) or [])
        except Exception:
            pass
    if not worlds:
        worlds.extend(str(item) for item in cmds.ls(type="mmdPhysicsWorldShape", long=True) or [])
    # A message connection can resolve to the world transform.  Report the
    # connected shape so the measured ``mmdPhysicsWorldShape.enable`` value is
    # unambiguous across Maya versions.
    expanded: List[str] = []
    for world in worlds:
        if _safe_node_type(cmds, world) == "mmdPhysicsWorldShape":
            expanded.append(world)
            continue
        shapes = cmds.listRelatives(world, shapes=True, fullPath=True) or []
        physics_shapes = [
            str(shape) for shape in shapes
            if _safe_node_type(cmds, str(shape)) == "mmdPhysicsWorldShape"
        ]
        expanded.extend(physics_shapes or [world])
    return sorted(set(expanded))


def _census(cmds: Any, root: str, solver: str | None, worlds: Sequence[str]) -> Dict[str, Any]:
    """Return bounded imported model and physics topology counts."""
    counts = {
        node_type: len(cmds.ls(type=node_type, long=True) or [])
        for node_type in _CENSUS_TYPES
    }
    descendants = cmds.listRelatives(root, allDescendents=True, fullPath=True) or []
    root_namespace = root.split(":", 1)[0] if ":" in root else ""
    return {
        "root": root,
        "rootNamespace": root_namespace,
        "descendantCount": len(descendants),
        "nodeCounts": counts,
        "solver": solver,
        "worlds": list(worlds),
        "physicsNodeCount": sum(counts[name] for name in counts if "mmd" in name.lower()),
    }


def _flatten_numeric_array(value: Any) -> List[float]:
    """Flatten Maya array/list wrappers into numeric values only."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        values: List[float] = []
        for item in value:
            values.extend(_flatten_numeric_array(item))
        return values
    try:
        iterator = iter(value)
    except TypeError:
        return [float(value)]
    values = []
    for item in iterator:
        values.extend(_flatten_numeric_array(item))
    return values


def _matrix_fingerprint(cmds: Any, solver: str) -> Dict[str, Any]:
    """Hash outBoneMatrices as canonical little-endian IEEE-754 doubles."""
    result: Dict[str, Any] = {
        "algorithm": "sha256(struct.pack('<d') per element)",
        "elementCount": 0,
        "sha256": None,
        "supported": True,
        "readAfterStatusCount": True,
        "evaluationNote": "Reading outBoneMatrices follows status/count reads and may trigger evaluation.",
    }
    try:
        raw = cmds.getAttr(f"{solver}.outBoneMatrices")
        values = _flatten_numeric_array(raw)
        digest = hashlib.sha256()
        for value in values:
            digest.update(struct.pack("<d", float(value)))
        result["elementCount"] = len(values)
        result["sha256"] = digest.hexdigest()
    except Exception as exc:
        result.update({"supported": False, "error": str(exc)})
    return result


def _solver_state(cmds: Any, solver: str | None, worlds: Sequence[str]) -> Dict[str, Any]:
    values: Dict[str, Any] = {}
    if solver and cmds.objExists(solver):
        for attr in _SOLVER_ATTRS:
            if cmds.attributeQuery(attr, node=solver, exists=True):
                values[attr] = _safe_get_attr(cmds, f"{solver}.{attr}")
        matrix_fingerprint = _matrix_fingerprint(cmds, solver)
    else:
        matrix_fingerprint = {
            "supported": False,
            "elementCount": 0,
            "sha256": None,
            "error": "solver node is unavailable",
        }
    world_values = []
    for world in worlds:
        world_values.append({
            "node": world,
            "nodeType": _safe_node_type(cmds, world),
            "enable": _safe_get_attr(cmds, f"{world}.enable"),
            "resetGeneration": _safe_get_attr(cmds, f"{world}.resetGeneration"),
        })
    return {
        "solver": solver,
        "values": values,
        "worlds": world_values,
        "outBoneMatricesFingerprint": matrix_fingerprint,
    }


def _display_support(cmds: Any, requested: str) -> Dict[str, Any]:
    try:
        panels = sorted(str(panel) for panel in cmds.getPanel(type="modelPanel") or [])
    except Exception as exc:
        return {"requested": requested, "supported": False, "panels": [], "error": str(exc)}
    supported = requested == "headless" or bool(panels)
    return {
        "requested": requested,
        "supported": supported,
        "panels": panels,
        "reason": None if supported else "no modelPanel in mayapy standalone",
    }


def _profiler_start(cmds: Any, enabled: bool) -> Dict[str, Any]:
    if not enabled:
        return {"requested": False, "supported": False, "active": False, "events": 0}
    try:
        cmds.profiler(reset=True)
        cmds.profiler(sampling=True)
        return {"requested": True, "supported": True, "active": True, "events": 0}
    except Exception as exc:
        return {"requested": True, "supported": False, "active": False, "events": 0, "error": str(exc)}


def _profiler_stop(cmds: Any, started: Mapping[str, Any]) -> Dict[str, Any]:
    if not started.get("active"):
        return dict(started)
    result = dict(started)
    try:
        cmds.profiler(sampling=False)
        count = int(cmds.profiler(query=True, eventCount=True) or 0)
    except Exception as exc:
        result.update({"active": False, "error": str(exc), "events": 0})
        return result
    events: List[Dict[str, Any]] = []
    for index in range(min(count, 512)):
        row: Dict[str, Any] = {"index": index}
        for flag, key in (
            ("eventName", "name"),
            ("eventDescription", "description"),
            ("eventCategory", "categoryIndex"),
            ("eventCPUId", "cpuId"),
            ("eventDuration", "duration"),
            ("eventStartTime", "startTime"),
            ("eventThreadId", "threadId"),
        ):
            try:
                row[key] = _json_safe(cmds.profiler(query=True, eventIndex=index, **{flag: True}))
            except Exception as exc:
                row[key] = {"error": str(exc)}
        events.append(row)
    categories: Dict[str, str] = {}
    try:
        names = cmds.profiler(query=True, allCategories=True) or []
        categories = {str(index): str(name) for index, name in enumerate(names)}
    except Exception:
        pass
    totals: Dict[str, Dict[str, float | int]] = defaultdict(lambda: {"count": 0, "duration": 0})
    for event in events:
        category = categories.get(str(event.get("categoryIndex")), str(event.get("categoryIndex")))
        bucket = totals[category]
        bucket["count"] = int(bucket["count"]) + 1
        duration = event.get("duration")
        if isinstance(duration, (int, float)):
            bucket["duration"] = float(bucket["duration"]) + float(duration)
    mmd_events = [
        event for event in events
        if "mmd" in f"{event.get('name', '')} {event.get('description', '')}".lower()
    ]
    result.update({
        "active": False,
        "events": count,
        "eventLimit": 512,
        "categoryNames": categories,
        "categoryTotals": {key: totals[key] for key in sorted(totals)},
        "eventNameCounts": dict(sorted(Counter(str(event.get("name")) for event in events).items())),
        "mmdEvents": mmd_events[:128],
        "nodeTypeTiming": {
            "supported": False,
            "reason": "maya.cmds.profiler event records expose event name/description, not node type timing",
        },
        "rawEvents": events,
    })
    return result


def _set_physics(
    cmds: Any,
    solver: str | None,
    worlds: Sequence[str],
    *,
    solver_enabled: bool,
    world_enabled: bool,
) -> None:
    for world in worlds:
        if cmds.attributeQuery("enable", node=world, exists=True):
            cmds.setAttr(f"{world}.enable", bool(world_enabled))
    if solver and cmds.attributeQuery("enable", node=solver, exists=True):
        cmds.setAttr(f"{solver}.enable", bool(solver_enabled))


def _sample_state(cmds: Any, solver: str | None, worlds: Sequence[str], frame: int) -> Dict[str, Any]:
    return {"frame": int(frame), "state": _solver_state(cmds, solver, worlds)}


def _perform_sequence(
    cmds: Any,
    name: str,
    frames: Sequence[int],
    solver: str | None,
    worlds: Sequence[str],
    display: Mapping[str, Any],
) -> Dict[str, Any]:
    samples: List[Dict[str, Any]] = []
    refresh_count = 0
    playback_started = False
    if name == "static":
        frame = int(frames[0])
        cmds.currentTime(frame, edit=True)
        for _ in frames:
            cmds.refresh(force=True)
            refresh_count += 1
            samples.append(_sample_state(cmds, solver, worlds, frame))
    elif name == "scrub":
        for frame in frames:
            cmds.currentTime(int(frame), edit=True)
            cmds.refresh(force=True)
            refresh_count += 1
            samples.append(_sample_state(cmds, solver, worlds, int(frame)))
    elif name == "playback":
        cmds.play(state=False)
        try:
            cmds.play(state=True)
            playback_started = True
        except Exception:
            playback_started = False
        for frame in frames:
            cmds.currentTime(int(frame), edit=True)
            cmds.refresh(force=True)
            refresh_count += 1
            samples.append(_sample_state(cmds, solver, worlds, int(frame)))
        try:
            cmds.play(state=False)
        except Exception:
            pass
    else:
        raise ValueError(f"Unknown sequence: {name}")
    return {
        "name": name,
        "frames": [int(frame) for frame in frames],
        "refreshCount": refresh_count,
        "playbackStarted": playback_started if name == "playback" else None,
        "display": dict(display),
        "samples": samples,
    }


def _status_summary(repeats: Sequence[Mapping[str, Any]], enabled: bool) -> Dict[str, Any]:
    statuses: List[str] = []
    transitions: List[str] = []
    for repeat in repeats:
        previous = None
        for sample in repeat.get("sequence", {}).get("samples", []):
            value = ((sample.get("state") or {}).get("values") or {}).get("outStatus")
            text = str(value)
            statuses.append(text)
            if previous is not None and text != previous:
                transitions.append(f"{previous}->{text}")
            previous = text
    counts = Counter(statuses)
    candidate = bool(enabled and statuses and all(status == "no physics data" for status in statuses))
    return {
        "statusCounts": dict(sorted(counts.items())),
        "statusTransitions": transitions,
        "initializationRetryCandidate": candidate,
        "nativeSteppingObserved": "stepped" in counts,
        "disabledObserved": "disabled" in counts,
    }


def _run_condition(
    cmds: Any,
    sequence: str,
    physics: str,
    profiler: str,
    display: str,
    frames: Sequence[int],
    repeats: int,
    solver: str | None,
    worlds: Sequence[str],
) -> Dict[str, Any]:
    # ``off`` mirrors the production UI's world toggle: the solver remains
    # enabled while the connected world is disabled.  ``solver-off`` is an
    # optional cheap control group for isolating the solver node itself.
    solver_enabled = physics in {"off", "on"}
    world_enabled = physics == "on"
    display_evidence = _display_support(cmds, display)
    row: Dict[str, Any] = {
        "key": f"{sequence}|physics={physics}|display={display}|profiler={profiler}",
        "sequence": sequence,
        "physics": physics,
        "display": display_evidence,
        "profiler": profiler,
        "repeatCount": repeats,
        "stateBefore": None,
        "stateAfter": None,
        "repeats": [],
        "errors": [],
    }
    _set_physics(
        cmds,
        solver,
        worlds,
        solver_enabled=solver_enabled,
        world_enabled=world_enabled,
    )
    row["stateBefore"] = _solver_state(cmds, solver, worlds)
    for repeat_index in range(repeats):
        profiler_state = _profiler_start(cmds, profiler == "on")
        started = time.perf_counter()
        try:
            sequence_result = _perform_sequence(cmds, sequence, frames, solver, worlds, display_evidence)
        except Exception as exc:
            sequence_result = {"name": sequence, "frames": list(frames), "samples": [], "error": str(exc)}
            row["errors"].append(str(exc))
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        profiler_result = _profiler_stop(cmds, profiler_state)
        row["repeats"].append({
            "index": repeat_index,
            "wallTimeMs": round(elapsed_ms, 6),
            "sequence": sequence_result,
            "profiler": profiler_result,
        })
    row["stateAfter"] = _solver_state(cmds, solver, worlds)
    values = [float(item["wallTimeMs"]) for item in row["repeats"]]
    row["timing"] = {
        "wallTimeMs": round(sum(values), 6),
        "meanMs": round(sum(values) / len(values), 6) if values else None,
        "minMs": round(min(values), 6) if values else None,
        "maxMs": round(max(values), 6) if values else None,
    }
    row["observations"] = _status_summary(row["repeats"], solver_enabled)
    row["status"] = "unsupported" if not display_evidence.get("supported") else ("error" if row["errors"] else "pass")
    return row


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pmx", default=DEFAULT_PMX)
    parser.add_argument(
        "--pmx-path-file",
        default=None,
        help="UTF-8 text file containing the PMX path (avoids non-ASCII Windows argv loss).",
    )
    parser.add_argument("--out", default=DEFAULT_REPORT)
    parser.add_argument("--modes", default=",".join(_EVALUATION_MODES))
    parser.add_argument("--physics", default=",".join(_PHYSICS_STATES))
    parser.add_argument("--sequences", default=",".join(_SEQUENCES))
    parser.add_argument("--profiler", default=",".join(_PROFILER_STATES))
    parser.add_argument("--display", default=",".join(_DISPLAY_MODES))
    parser.add_argument("--frames", default="0,1,2,1,0")
    parser.add_argument("--repeats", type=int, default=2)
    return parser.parse_args()


def _run(args: argparse.Namespace, cmds: Any, importer: Callable[..., Any]) -> Dict[str, Any]:
    if args.pmx_path_file:
        path_file = _resolve_path(args.pmx_path_file)
        pmx = Path(path_file.read_text(encoding="utf-8").strip()).resolve()
    else:
        pmx = _resolve_path(args.pmx)
    modes = _parse_csv(args.modes, _EVALUATION_MODES, "modes")
    physics = _parse_csv(args.physics, _PHYSICS_STATES, "physics")
    sequences = _parse_csv(args.sequences, _SEQUENCES, "sequences")
    profiler = _parse_csv(args.profiler, _PROFILER_STATES, "profiler")
    displays = _parse_csv(args.display, _DISPLAY_MODES, "display")
    frames = _parse_frames(args.frames)
    if int(args.repeats) < 1:
        raise ValueError("--repeats must be >= 1")
    initial_eval = _evaluation_state(cmds)
    report: Dict[str, Any] = {
        "status": "error",
        "probe": "BLACK-PMX-EVALUATION-PROFILE-1",
        "mayaVersion": str(cmds.about(version=True)),
        "pmx": str(pmx),
        "configuration": {
            "modes": modes,
            "physics": physics,
            "sequences": sequences,
            "profiler": profiler,
            "display": displays,
            "frames": frames,
            "repeatCount": int(args.repeats),
        },
        "plugin": {},
        "modes": [],
        "errors": [],
        "unsupported": [],
        "evaluationManagerBefore": initial_eval,
    }
    try:
        if not pmx.is_file():
            raise FileNotFoundError(f"Black PMX fixture not found: {pmx}")
        report["plugin"] = _load_plugin(DEFAULT_ROOT, cmds)
        for mode in modes:
            mode_report: Dict[str, Any] = {
                "mode": mode,
                "status": "error",
                "evaluation": {},
                "root": None,
                "solver": None,
                "worlds": [],
                "census": {},
                "importState": {},
                "conditions": [],
                "errors": [],
            }
            try:
                mode_report["evaluation"]["set"] = _set_evaluation_mode(cmds, mode)
                cmds.file(new=True, force=True)
                root = _load_model(pmx, importer)
                solver = _find_solver(cmds, root)
                worlds = _find_worlds(cmds, solver)
                mode_report.update({
                    "root": root,
                    "solver": solver,
                    "worlds": worlds,
                    "census": _census(cmds, root, solver, worlds),
                    "importState": _solver_state(cmds, solver, worlds),
                })
                if not solver:
                    raise RuntimeError(f"No mmdPhysicsSolver connected to imported root {root}")
                for sequence in sequences:
                    for physics_state in physics:
                        for display in displays:
                            for profiler_state in profiler:
                                condition = _run_condition(
                                    cmds, sequence, physics_state, profiler_state,
                                    display, frames, int(args.repeats), solver, worlds,
                                )
                                mode_report["conditions"].append(condition)
                                if condition["status"] == "unsupported":
                                    report["unsupported"].append({"mode": mode, "condition": condition["key"], "display": condition["display"]})
                                if condition["status"] == "error":
                                    mode_report["errors"].extend(condition["errors"])
                mode_report["status"] = "pass" if not mode_report["errors"] else "error"
            except Exception as exc:
                mode_report["errors"].append(str(exc))
            finally:
                try:
                    mode_report["evaluation"]["restore"] = _restore_evaluation_mode(cmds, initial_eval)
                except Exception as exc:
                    mode_report["evaluation"]["restore"] = {"restored": False, "error": str(exc)}
                    mode_report["errors"].append(f"evaluation restore: {exc}")
                report["modes"].append(mode_report)
        if report["modes"] and all(mode.get("status") == "pass" for mode in report["modes"]):
            report["status"] = "pass"
        else:
            report["errors"].append("one or more evaluation modes failed")
    except Exception as exc:
        report["errors"].append(str(exc))
    finally:
        report["evaluationManagerRestore"] = _restore_evaluation_mode(cmds, initial_eval)
        report["evaluationManagerAfter"] = _evaluation_state(cmds)
        if not report["evaluationManagerRestore"].get("restored", False):
            report["status"] = "error"
            report["errors"].append("evaluationManager mode was not restored")
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
    import maya.cmds as cmds
    try:
        from mmd_tools.io.mmd_importer import import_mmd_file

        report = _run(args, cmds, import_mmd_file)
    except Exception as exc:
        report = {"status": "error", "probe": "BLACK-PMX-EVALUATION-PROFILE-1", "errors": [str(exc)]}
    report_path = _resolve_path(args.out)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(_json_safe(report), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    try:
        maya.standalone.uninitialize()
    except Exception:
        pass
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
