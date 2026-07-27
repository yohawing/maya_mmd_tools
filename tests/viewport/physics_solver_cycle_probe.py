"""Capture the Citlali mmdPhysicsSolver DG graph and cycle state.

This is an investigation probe for ``MMD-PHYSICS-SOLVER-CYCLE-1``.  It uses
the production PMX importer, records every solver plug and its connections,
captures the relevant rigid-body/joint/skin/IK/append topology, then runs a
small reversible playback/scrub/reset/physics-toggle sequence.  The probe
does not disconnect or repair any scene connections; all evidence is written
to a JSON report under ``build/``.  Each evaluation-mode run also performs a
single-frame offscreen Playblast by default.  The Playblast trace records the
requested/captured frame, solver state and two explicit same-frame pulls
before and after capture, so a repeated pull can be distinguished from a
sequential time evaluation.  Use ``--no-playblast`` to disable this optional
observation or ``--playblast-out``/``--playblast-frame`` to control its output.

Usage (inside Maya's Python interpreter)::

    mayapy tests/viewport/physics_solver_cycle_probe.py

The command exits non-zero when Maya reports a solver cycle warning, even if
the final ``cycleCheck`` query is empty.  Maya command output is captured in
memory per isolated evaluation-mode run so transient warnings cannot be
confused with older appended session output.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Sequence, Tuple

import maya.cmds as cmds

DEFAULT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PMX = "build/fixtures/citlali_ascii_file/citlali.pmx"
DEFAULT_REPORT = "build/reports/physics_solver_cycle_probe.json"
DEFAULT_PLAYBLAST = "build/physics_solver_cycle_probe_playblast.png"
DEFAULT_PLAYBLAST_WIDTH = 320
DEFAULT_PLAYBLAST_HEIGHT = 240
_EVALUATION_MODES = {"off", "serial", "parallel"}
_SOLVER_CYCLE_WARNING_RE = re.compile(
    r"(?=.*mmdPhysicsSolver)(?=.*outSolved)"
    r"(?=.*(?:cycle|cycleCheck|サイクル|循環))",
    re.IGNORECASE,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pmx", default=DEFAULT_PMX, help="Citlali PMX fixture to import.")
    parser.add_argument("--out", default=DEFAULT_REPORT, help="JSON report path (must be under build/ when using nox).")
    parser.add_argument(
        "--frames",
        default="0,1,2,1,0",
        help="Comma-separated Maya frames used by the deterministic playback/scrub sequence.",
    )
    parser.add_argument(
        "--modes",
        default="off,serial,parallel",
        help="Comma-separated evaluationManager modes (off, serial, parallel).",
    )
    playblast_group = parser.add_mutually_exclusive_group()
    playblast_group.add_argument(
        "--playblast",
        dest="playblast",
        action="store_true",
        default=True,
        help="Capture one offscreen Playblast frame per evaluation mode (default).",
    )
    playblast_group.add_argument(
        "--no-playblast",
        dest="playblast",
        action="store_false",
        help="Skip the Playblast observation while retaining cycle probing.",
    )
    parser.add_argument(
        "--playblast-frame",
        type=int,
        default=None,
        help="Explicit frame for the one-frame Playblast (defaults to the first --frames value).",
    )
    parser.add_argument(
        "--playblast-out",
        "--playblast-output",
        "--playblast-path",
        dest="playblast_out",
        default=DEFAULT_PLAYBLAST,
        help="Playblast PNG path/base; mode is appended to keep per-mode output deterministic.",
    )
    parser.add_argument(
        "--playblast-width",
        type=int,
        default=DEFAULT_PLAYBLAST_WIDTH,
        help=f"Offscreen Playblast width in pixels (default: {DEFAULT_PLAYBLAST_WIDTH}).",
    )
    parser.add_argument(
        "--playblast-height",
        type=int,
        default=DEFAULT_PLAYBLAST_HEIGHT,
        help=f"Offscreen Playblast height in pixels (default: {DEFAULT_PLAYBLAST_HEIGHT}).",
    )
    return parser.parse_args()


def _resolve_path(value: str, root: Path = DEFAULT_ROOT) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _load_plugin(repo_root: Path) -> None:
    # Importing the shared helper keeps plugin path/loader behaviour identical
    # to the other mayapy viewport probes.
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from tests.common.maya_plugin_setup import load_mmd_tools_plugin

    load_mmd_tools_plugin(repo_root)


def _load_model(path: Path) -> str:
    from mmd_tools.io.mmd_importer import import_mmd_file

    root = import_mmd_file(
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


def _json_safe(value: Any) -> Any:
    """Convert Maya scalar/array values to bounded JSON-friendly data."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        # Matrix and output arrays can be very large.  Keep a short sample and
        # an exact element count, which is enough to identify a plug without
        # making the report megabytes in size.
        safe = [_json_safe(item) for item in value]
        if len(safe) > 32:
            return {"count": len(safe), "sample": safe[:8]}
        return safe
    return str(value)


def _safe_get_attr(plug: str) -> Any:
    try:
        return _json_safe(cmds.getAttr(plug))
    except Exception as exc:
        return {"error": str(exc)}


def _current_frame() -> Any:
    """Return Maya's current time without hiding a query failure."""
    try:
        return _json_safe(cmds.currentTime(query=True))
    except Exception as exc:
        return {"error": str(exc)}


def _solver_status_pull(solver: str | None, requested_frame: int, label: str) -> Dict[str, Any]:
    """Pull the solver status once and preserve the exact DG evidence.

    This intentionally uses ``cmds.getAttr`` directly instead of
    :func:`_safe_get_attr`: a failed pull is evidence for the Playblast trace,
    not a value that should be silently converted into a string.
    """
    row: Dict[str, Any] = {
        "label": label,
        "requestedFrame": requested_frame,
        "observedFrame": _current_frame(),
        "plug": None,
        "status": None,
        "error": None,
    }
    if not solver or not cmds.objExists(solver):
        row["error"] = f"solver is unavailable: {solver!r}"
        return row
    try:
        attr = "outStatus" if cmds.attributeQuery("outStatus", node=solver, exists=True) else "outSolved"
        row["plug"] = f"{solver}.{attr}"
        row["status"] = _json_safe(cmds.getAttr(row["plug"]))
    except Exception as exc:
        row["error"] = str(exc)
    return row


def _resolve_playblast_output(requested: Path, frame: int, result: Any = None) -> Path | None:
    """Resolve Maya's actual PNG path for a one-frame image Playblast.

    Maya versions differ in whether ``playblast`` returns an exact path and in
    how they pad a single frame number.  The candidate directory is controlled
    by the caller, so this lookup cannot accidentally select another report.
    """
    requested = requested.resolve()
    candidates: List[Path] = [requested, requested.with_suffix(".png")]
    base = requested.with_suffix("")
    for value in (result if isinstance(result, (list, tuple)) else [result]):
        if value in (None, ""):
            continue
        try:
            returned = Path(str(value))
        except (TypeError, ValueError):
            continue
        candidates.extend((returned, returned.with_suffix(".png")))
    candidates.extend(
        base.parent / f"{base.name}.{suffix}.png"
        for suffix in (f"{frame:04d}", f"{frame:03d}", f"{frame:02d}", str(frame))
    )
    seen: set[str] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            if resolved.is_file() and resolved.stat().st_size > 0:
                return resolved
        except OSError:
            continue
    try:
        matches = sorted(
            (path for path in base.parent.glob(f"{base.name}*.png") if path.is_file()),
            key=lambda path: (path.stat().st_mtime_ns, str(path)),
            reverse=True,
        )
    except OSError:
        matches = []
    for match in matches:
        try:
            if match.stat().st_size > 0:
                return match.resolve()
        except OSError:
            continue
    return None


def _file_signature(path: Path | None) -> Dict[str, Any] | None:
    """Return a small pre/post signature for Playblast write diagnostics."""
    if path is None:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    return {"bytes": int(stat.st_size), "mtimeNs": int(stat.st_mtime_ns)}


def _playblast_observation(
    *,
    solver: str | None,
    frame: int,
    output_path: Path,
    width: int,
    height: int,
) -> Dict[str, Any]:
    """Capture and report one explicit offscreen Playblast frame.

    The operation is deliberately observation-only.  It does not change
    solver/world attributes and records both same-frame status pulls around
    the capture.  Any Maya/playblast or output-resolution error is returned in
    the JSON row so callers can fail the mode without losing diagnostics.
    """
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    requested_base = output_path.with_suffix("")
    frame_before_request = _current_frame()
    frame_request_error: str | None = None
    try:
        cmds.currentTime(frame, edit=True)
        cmds.refresh(force=True)
    except Exception as exc:
        frame_request_error = str(exc)
    before_frame = _current_frame()
    before_solver = _solver_state(solver)
    before_pulls = [
        _solver_status_pull(solver, frame, "before_same_frame_pull_1"),
        _solver_status_pull(solver, frame, "before_same_frame_pull_2"),
    ]
    preexisting = _resolve_playblast_output(output_path, frame)
    pre_signature = _file_signature(preexisting)
    result: Any = None
    call_error: str | None = None
    try:
        # These flags are supported by standalone mayapy and avoid requiring a
        # GUI modelPanel or DX11 viewport.  Singular ``frame`` is intentional:
        # it prevents Maya from evaluating an implicit frame range.
        result = cmds.playblast(
            filename=str(requested_base),
            frame=frame,
            format="image",
            compression="png",
            offScreen=True,
            offScreenViewportUpdate=True,
            viewer=False,
            width=int(width),
            height=int(height),
            percent=100,
            forceOverwrite=True,
            showOrnaments=False,
        )
    except Exception as exc:
        call_error = str(exc)
    actual = _resolve_playblast_output(output_path, frame, result)
    after_frame = _current_frame()
    after_solver = _solver_state(solver)
    after_pulls = [
        _solver_status_pull(solver, frame, "after_same_frame_pull_1"),
        _solver_status_pull(solver, frame, "after_same_frame_pull_2"),
    ]
    post_signature = _file_signature(actual)
    file_written = bool(call_error is None and post_signature and post_signature.get("bytes", 0) > 0)
    errors = []
    if frame_request_error:
        errors.append(f"set requested frame {frame}: {frame_request_error}")
    if call_error:
        errors.append(f"cmds.playblast: {call_error}")
    if actual is None:
        errors.append(f"Playblast did not produce a non-empty PNG under {output_path.parent}")
    for pull in before_pulls + after_pulls:
        if pull.get("error"):
            errors.append(f"{pull.get('label')}: {pull['error']}")
    if isinstance(after_frame, (int, float)) and after_frame != frame:
        errors.append(f"Playblast left Maya at frame {after_frame!r}; requested {frame}")
    return {
        "outcome": "pass" if not errors else "error",
        "requestedFrame": frame,
        "capturedFrame": after_frame,
        "frameBeforeRequest": frame_before_request,
        "frameBeforePlayblast": before_frame,
        "frameAfterPlayblast": after_frame,
        "sameFrame": after_frame == frame,
        "beforeSolverState": before_solver,
        "solverStateBefore": before_solver,
        "beforeSameFramePulls": before_pulls,
        "afterSolverState": after_solver,
        "solverStateAfter": after_solver,
        "afterSameFramePulls": after_pulls,
        "sameFramePulls": before_pulls + after_pulls,
        "playblast": {
            "requestedOutputPath": str(output_path),
            "requestedFilenameBase": str(requested_base),
            "returned": _json_safe(result),
            "actualOutputPath": str(actual) if actual else None,
            "fileWritten": file_written,
            "fileExists": bool(post_signature),
            "fileBytes": post_signature.get("bytes") if post_signature else 0,
            "preExistingOutputPath": str(preexisting) if preexisting else None,
            "preSignature": pre_signature,
            "postSignature": post_signature,
            "fileChanged": pre_signature != post_signature,
            "width": int(width),
            "height": int(height),
            "offScreen": True,
            "offScreenViewportUpdate": True,
            "viewer": False,
            "frame": frame,
        },
        "playblastReturned": _json_safe(result),
        "actualOutputPath": str(actual) if actual else None,
        "fileWritten": file_written,
        "errors": errors,
        "error": errors[0] if errors else None,
    }


def _safe_node_type(node: str) -> str | None:
    try:
        return str(cmds.nodeType(node))
    except Exception:
        return None


def _connection_pairs(node: str) -> List[Dict[str, str]]:
    """Return all source/destination connection pairs involving *node*."""
    try:
        raw = cmds.listConnections(
            node,
            plugs=True,
            connections=True,
            source=True,
            destination=True,
        ) or []
    except Exception:
        raw = []
    pairs: List[Dict[str, str]] = []
    seen: set[Tuple[str, str]] = set()
    for index in range(0, len(raw) - 1, 2):
        left, right = str(raw[index]), str(raw[index + 1])
        key = (left, right)
        if key in seen:
            continue
        seen.add(key)
        pairs.append({"thisPlug": left, "otherPlug": right})
    # Maya's listConnections order can vary after save/open and between DG
    # evaluators.  Canonical ordering keeps topology comparisons reproducible.
    return sorted(pairs, key=lambda pair: (pair["thisPlug"], pair["otherPlug"]))


def _cycle_state(label: str) -> Dict[str, Any]:
    """Read Maya's cycle checker and expand the reported plugs to edges."""
    try:
        evaluation = bool(cmds.cycleCheck(query=True, evaluation=True))
    except Exception as exc:
        evaluation = None
        evaluation_error = str(exc)
    else:
        evaluation_error = None
    try:
        raw_plugs = cmds.cycleCheck(all=True, list=True) or []
    except Exception as exc:
        raw_plugs = []
        list_error = str(exc)
    else:
        list_error = None
    if isinstance(raw_plugs, str):
        plugs = [raw_plugs]
    else:
        plugs = sorted({str(plug) for plug in raw_plugs})
    nodes = sorted({plug.split(".", 1)[0] for plug in plugs})
    edges: List[Dict[str, str]] = []
    seen: set[Tuple[str, str]] = set()
    for plug in plugs:
        try:
            raw_edges = cmds.listConnections(
                plug,
                plugs=True,
                connections=True,
                source=True,
                destination=True,
            ) or []
        except Exception:
            raw_edges = []
        for index in range(0, len(raw_edges) - 1, 2):
            left, right = str(raw_edges[index]), str(raw_edges[index + 1])
            key = (left, right)
            if key not in seen:
                seen.add(key)
                edges.append({"thisPlug": left, "otherPlug": right})
    return {
        "label": label,
        "evaluationEnabled": evaluation,
        "evaluationError": evaluation_error,
        "cyclePlugs": plugs,
        "cycleNodeTypes": {node: _safe_node_type(node) for node in nodes},
        "edges": sorted(edges, key=lambda pair: (pair["thisPlug"], pair["otherPlug"])),
        "listError": list_error,
        # The cycle checker returns all plugs participating in the SCC.  Keep
        # this explicit so a no-cycle report is unambiguous.
        "observed": bool(plugs),
    }


def _evaluation_state() -> Dict[str, Any]:
    try:
        raw_modes = cmds.evaluationManager(query=True, mode=True) or []
    except Exception as exc:
        return {"modes": [], "error": str(exc)}
    # Maya may append a localized informational string (for example, when it
    # has just prepared the evaluation graph).  Keep only actual mode tokens
    # so before/after restoration compares configuration rather than chatter.
    modes = [str(mode) for mode in raw_modes if str(mode).lower() in _EVALUATION_MODES]
    if not modes and raw_modes:
        modes = [str(raw_modes[0])]
    return {"modes": modes}


def _solver_for_root(root: str) -> str | None:
    try:
        candidates = cmds.listConnections(
            f"{root}.message",
            source=False,
            destination=True,
            type="mmdPhysicsSolver",
        ) or []
    except Exception:
        candidates = []
    if candidates:
        return str(candidates[0])
    # Older Maya builds can omit the type filter while a Python MPxNode is
    # still being registered.  Verify candidates by their modelRoot source.
    for candidate in cmds.ls(type="mmdPhysicsSolver") or []:
        try:
            owners = cmds.listConnections(
                f"{candidate}.modelRoot", source=True, destination=False
            ) or []
        except Exception:
            owners = []
        if root in owners or (cmds.ls(root, long=True) and cmds.ls(root, long=True)[0] in owners):
            return str(candidate)
    return None


def _solver_plugs(solver: str) -> Dict[str, Any]:
    """Capture every declared solver plug, values, array indices, and edges."""
    try:
        attrs = cmds.listAttr(solver) or []
    except Exception as exc:
        return {"error": str(exc), "attributes": {}}
    attributes: Dict[str, Any] = {}
    for attr in sorted({str(name) for name in attrs}):
        plug = f"{solver}.{attr}"
        row: Dict[str, Any] = {"plug": plug, "value": _safe_get_attr(plug)}
        try:
            row["attributeType"] = str(cmds.attributeQuery(attr, node=solver, attributeType=True))
        except Exception:
            row["attributeType"] = None
        try:
            indices = cmds.getAttr(plug, multiIndices=True) or []
            row["multiIndices"] = [int(index) for index in indices]
        except Exception:
            row["multiIndices"] = []
        row["connections"] = _connection_pairs(plug)
        attributes[attr] = row
    return {
        "node": solver,
        "nodeType": _safe_node_type(solver),
        "nodeState": _safe_get_attr(f"{solver}.nodeState"),
        "attributes": attributes,
        "connections": _connection_pairs(solver),
    }


_RELEVANT_TYPES = {
    "joint",
    "ikhandle",
    "skincluster",
    "mmdappend",
    "mmdccdik",
    "mmdphysicsbonedriver",
    "mmdphysicssolver",
    "mmdphysicsworldshape",
    "mmdphysicsjointshape",
    "mmdrigidbodyshape",
}


def _node_is_relevant(node: str) -> bool:
    node_type = (_safe_node_type(node) or "").lower()
    if node_type in _RELEVANT_TYPES:
        return True
    # Maya's type names for some legacy nodes vary by capitalization/name;
    # retain all physics nodes even when a custom plugin reports a suffix.
    return "physics" in node_type or "rigidbody" in node_type


def _topology(root: str, solver: str | None) -> Dict[str, Any]:
    """Capture relevant nodes and DG edges without dumping the whole scene."""
    root_nodes = set(cmds.listRelatives(root, allDescendents=True, fullPath=True) or [])
    root_nodes.add(root)
    relevant: set[str] = set()
    for node in root_nodes:
        if _node_is_relevant(node):
            relevant.add(node)
    if solver:
        relevant.add(solver)
        for pair in _connection_pairs(solver):
            for plug in (pair["thisPlug"], pair["otherPlug"]):
                relevant.add(plug.split(".", 1)[0])

    # Physics shape and joint parents are the transforms that matter for the
    # solver's output/display path.  Mesh skinClusters are found from history.
    for node in list(relevant):
        node_type = (_safe_node_type(node) or "").lower()
        if node_type.endswith("shape"):
            parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
            relevant.update(parents)
    meshes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
    for mesh in meshes:
        try:
            histories = cmds.listHistory(mesh, pruneDagObjects=True) or []
        except Exception:
            histories = []
        relevant.update(node for node in histories if _node_is_relevant(node))

    # Include every relevant solver/IK/append node in the scene, but only the
    # joint/transform nodes connected to those nodes.  This preserves the
    # topology needed to identify the edge that closes a cycle.
    for node_type in ("mmdCcdIk", "mmdAppend", "ikHandle", "skinCluster"):
        for node in cmds.ls(type=node_type, long=True) or []:
            relevant.add(str(node))
    for node in list(relevant):
        for pair in _connection_pairs(node):
            for plug in (pair["thisPlug"], pair["otherPlug"]):
                other = plug.split(".", 1)[0]
                other_type = (_safe_node_type(other) or "").lower()
                if other_type in {"joint", "transform", "skincluster", "ikhandle"}:
                    relevant.add(other)

    nodes = []
    edges: List[Dict[str, str]] = []
    seen_edges: set[Tuple[str, str]] = set()
    for node in sorted(relevant):
        nodes.append(
            {
                "node": node,
                "nodeType": _safe_node_type(node),
                "nodeState": _safe_get_attr(f"{node}.nodeState") if cmds.attributeQuery("nodeState", node=node, exists=True) else None,
            }
        )
        for pair in _connection_pairs(node):
            key = (pair["thisPlug"], pair["otherPlug"])
            if key in seen_edges:
                continue
            seen_edges.add(key)
            edges.append(pair)
    groups: Dict[str, List[str]] = {}
    for row in nodes:
        groups.setdefault(str(row["nodeType"]), []).append(str(row["node"]))
    return {
        "nodes": nodes,
        "edges": sorted(edges, key=lambda pair: (pair["thisPlug"], pair["otherPlug"])),
        "byType": groups,
    }


def _solver_state(solver: str | None) -> Dict[str, Any]:
    if not solver or not cmds.objExists(solver):
        return {"solver": solver, "present": False}
    values: Dict[str, Any] = {}
    for attr in ("enable", "inputMode", "inTime", "inDescriptorVersion", "nodeState", "outSolved", "outBoneCount", "outStatus"):
        if cmds.attributeQuery(attr, node=solver, exists=True):
            values[attr] = _safe_get_attr(f"{solver}.{attr}")
    return {"solver": solver, "present": True, "values": values}


def _operation(
    label: str,
    action: Callable[[], Any],
    solver: str | None,
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "label": label,
        "outcome": "pass",
        "error": None,
        "evaluation": _evaluation_state(),
    }
    try:
        result = action()
        if result is not None:
            row["result"] = _json_safe(result)
    except Exception as exc:
        row["outcome"] = "error"
        row["error"] = str(exc)
    row["evaluationAfter"] = _evaluation_state()
    row["solverState"] = _solver_state(solver)
    row["cycle"] = _cycle_state(label)
    return row


def _parse_frames(raw: str) -> List[int]:
    values = [int(part.strip()) for part in str(raw).split(",") if part.strip()]
    if not values:
        raise ValueError("--frames must contain at least one integer")
    return values


def _parse_modes(raw: str) -> List[str]:
    """Parse and validate evaluationManager modes in stable caller order."""
    allowed = _EVALUATION_MODES
    values = [part.strip().lower() for part in str(raw).split(",") if part.strip()]
    if not values:
        raise ValueError("--modes must contain at least one evaluation mode")
    invalid = sorted(set(values) - allowed)
    if invalid:
        raise ValueError(f"Unsupported evaluation mode(s): {', '.join(invalid)}")
    # Preserve order while avoiding duplicate scene imports.
    return list(dict.fromkeys(values))


def _mode_playblast_path(base: Path, mode: str) -> Path:
    """Derive a deterministic per-mode PNG path from the CLI base path."""
    base = base.resolve()
    if base.suffix.lower() != ".png":
        base = base.with_suffix(".png")
    return base.with_name(f"{base.stem}_{mode}{base.suffix}")


def _set_evaluation_mode(mode: str) -> Dict[str, Any]:
    """Set one evaluationManager mode and return before/after evidence."""
    before = _evaluation_state()
    cmds.evaluationManager(mode=mode)
    after = _evaluation_state()
    return {"requested": mode, "before": before, "after": after}


def _restore_evaluation_mode(state: Mapping[str, Any]) -> Dict[str, Any]:
    """Restore the first mode returned by an evaluationManager query."""
    modes = state.get("modes") or []
    if not modes:
        return {"requested": None, "after": _evaluation_state(), "restored": True}
    requested = str(modes[0])
    cmds.evaluationManager(mode=requested)
    after = _evaluation_state()
    return {
        "requested": requested,
        "after": after,
        "restored": after.get("modes") == list(modes),
    }


def _cycle_plug_set(mode_report: Mapping[str, Any]) -> List[str]:
    """Return the union of cycle plugs observed by one mode run."""
    plugs: set[str] = set()
    imported = mode_report.get("import") or {}
    plugs.update(str(item) for item in (imported.get("cycle") or {}).get("cyclePlugs", []))
    for operation in mode_report.get("operations") or []:
        cycle = operation.get("cycle") or {}
        plugs.update(str(item) for item in cycle.get("cyclePlugs", []))
        result = operation.get("result") or {}
        for key in ("cycleBefore", "cycleAfter"):
            plugs.update(str(item) for item in (result.get(key) or {}).get("cyclePlugs", []))
    return sorted(plugs)


def _solver_connection_set(mode_report: Mapping[str, Any]) -> List[str]:
    """Return canonical solver connection edges for one mode run."""
    solver_plugs = mode_report.get("solverPlugs") or {}
    pairs = solver_plugs.get("connections") or []
    return sorted(
        f"{pair.get('thisPlug', '')} -> {pair.get('otherPlug', '')}"
        for pair in pairs
    )


def _topology_set(mode_report: Mapping[str, Any]) -> List[str]:
    """Return a compact canonical set of relevant topology nodes and edges."""
    topology = mode_report.get("topologyBefore") or {}
    values = {
        f"node:{row.get('nodeType')}:{row.get('node')}"
        for row in topology.get("nodes", [])
    }
    values.update(
        f"edge:{pair.get('thisPlug', '')} -> {pair.get('otherPlug', '')}"
        for pair in topology.get("edges", [])
    )
    return sorted(values)


def _start_command_output_capture() -> Dict[str, Any]:
    """Capture Maya command output for one isolated mode run."""
    messages: List[Dict[str, Any]] = []
    try:
        import maya.api.OpenMaya as om

        def _callback(message, message_type, _client_data):
            messages.append({"type": int(message_type), "message": str(message)})

        callback_id = om.MCommandMessage.addCommandOutputCallback(_callback)
    except Exception as exc:
        return {"enabled": False, "messages": messages, "callback": None, "error": str(exc)}
    return {"enabled": True, "messages": messages, "callback": callback_id, "error": None}


def _stop_command_output_capture(state: Mapping[str, Any]) -> Dict[str, Any]:
    """Remove the callback and return solver-cycle messages seen this run."""
    callback_id = state.get("callback")
    remove_error = None
    if callback_id is not None:
        try:
            import maya.api.OpenMaya as om

            om.MMessage.removeCallback(callback_id)
        except Exception as exc:
            remove_error = str(exc)
    messages = list(state.get("messages") or [])
    warnings = [
        str(item.get("message", ""))
        for item in messages
        if _SOLVER_CYCLE_WARNING_RE.search(str(item.get("message", "")))
    ]
    return {
        "enabled": bool(state.get("enabled")),
        "messageCount": len(messages),
        "warnings": warnings[-200:],
        "warningCount": len(warnings),
        "error": state.get("error"),
        "removeError": remove_error,
    }


def _run_mode(
    *,
    pmx: Path,
    mode: str,
    scene_path: Path,
    frames: Sequence[int],
    playblast_enabled: bool,
    playblast_frame: int,
    playblast_path: Path,
    playblast_width: int,
    playblast_height: int,
) -> Dict[str, Any]:
    """Run one fully isolated import/operation/reopen pass."""
    report: Dict[str, Any] = {
        "status": "error",
        "mode": mode,
        "modelRoot": None,
        "solver": None,
        "import": {},
        "operations": [],
        "playblast": {},
        "errors": [],
        "evaluationMode": {},
        "mayaCommandOutput": {},
    }
    mode_before = _evaluation_state()
    command_capture = _start_command_output_capture()
    report["_commandCapture"] = command_capture
    try:
        report["evaluationMode"]["set"] = _set_evaluation_mode(mode)
        cmds.file(new=True, force=True)
        if not pmx.is_file():
            raise FileNotFoundError(f"Citlali PMX fixture not found: {pmx}")
        root = _load_model(pmx)
        solver = _solver_for_root(root)
        report["modelRoot"] = root
        report["solver"] = solver
        report["import"] = {
            "outcome": "pass",
            "solverPresent": bool(solver),
            "evaluation": _evaluation_state(),
            "cycle": _cycle_state(f"{mode}:clean_import"),
            "solverState": _solver_state(solver),
        }
        if not solver:
            raise RuntimeError(
                "Production import completed but no mmdPhysicsSolver is connected "
                f"to model root {root}; native physics registration is required"
            )
        report["solverPlugs"] = _solver_plugs(solver)
        report["topologyBefore"] = _topology(root, solver)

        # Standalone Maya does not advance playback asynchronously, so invoke
        # play/stop and still set explicit frames to make the sequence stable.
        def playback() -> Dict[str, Any]:
            playback_frames = list(frames[: max(1, min(3, len(frames)))])
            cmds.play(state=False)
            cmds.play(state=True)
            for frame in playback_frames:
                cmds.currentTime(frame, edit=True)
            cmds.play(state=False)
            return {"frames": playback_frames}

        report["operations"].append(_operation(f"{mode}:playback", playback, solver))

        def scrub() -> Dict[str, Any]:
            for frame in frames:
                cmds.currentTime(frame, edit=True)
                cmds.refresh(force=True)
            return {"frames": list(frames)}

        report["operations"].append(_operation(f"{mode}:scrub", scrub, solver))

        worlds = [
            str(shape)
            for shape in (cmds.ls(type="mmdPhysicsWorldShape", long=True) or [])
            if cmds.objExists(shape)
        ]
        world_state = {
            world: {
                "enable": _safe_get_attr(f"{world}.enable"),
                "resetGeneration": _safe_get_attr(f"{world}.resetGeneration"),
            }
            for world in worlds
        }

        def reset() -> Dict[str, Any]:
            for world in worlds:
                if cmds.attributeQuery("resetGeneration", node=world, exists=True):
                    current = int(cmds.getAttr(f"{world}.resetGeneration"))
                    cmds.setAttr(f"{world}.resetGeneration", current + 1)
            cmds.currentTime(0, edit=True)
            cmds.refresh(force=True)
            return {"worlds": worlds}

        report["operations"].append(_operation(f"{mode}:reset", reset, solver))

        def toggle_physics() -> Dict[str, Any]:
            for world in worlds:
                if not cmds.attributeQuery("enable", node=world, exists=True):
                    continue
                cmds.setAttr(f"{world}.enable", False)
                cmds.refresh(force=True)
                cmds.setAttr(f"{world}.enable", True)
                cmds.refresh(force=True)
            return {"worlds": worlds, "toggled": bool(worlds)}

        report["operations"].append(_operation(f"{mode}:physics_toggle", toggle_physics, solver))

        if playblast_enabled:
            def playblast_observation() -> Dict[str, Any]:
                return _playblast_observation(
                    solver=solver,
                    frame=playblast_frame,
                    output_path=playblast_path,
                    width=playblast_width,
                    height=playblast_height,
                )

            playblast_operation = _operation(
                f"{mode}:playblast_trace",
                playblast_observation,
                solver,
            )
            playblast_result = playblast_operation.get("result") or {}
            if playblast_result.get("outcome") == "error":
                playblast_operation["outcome"] = "error"
                playblast_operation["error"] = playblast_result.get("error") or "Playblast observation failed"
            report["operations"].append(playblast_operation)
            report["playblast"] = playblast_result
        else:
            skipped_playblast = {
                "label": f"{mode}:playblast_trace",
                "outcome": "skipped",
                "error": None,
                "result": {
                    "outcome": "skipped",
                    "requestedFrame": playblast_frame,
                    "reason": "disabled by --no-playblast",
                },
                "evaluation": _evaluation_state(),
                "evaluationAfter": _evaluation_state(),
                "solverState": _solver_state(solver),
                "cycle": _cycle_state(f"{mode}:playblast_trace_skipped"),
            }
            report["operations"].append(skipped_playblast)
            report["playblast"] = skipped_playblast["result"]

        # Restore world values after the reversible sequence.  The reset
        # generation is restored too; the operation evidence remains in JSON.
        for world, state in world_state.items():
            for attr, value in state.items():
                if isinstance(value, (bool, int, float)):
                    try:
                        cmds.setAttr(f"{world}.{attr}", value)
                    except Exception as exc:
                        report["errors"].append(f"restore {world}.{attr}: {exc}")

        def scene_reopen() -> Dict[str, Any]:
            """Save/open the imported scene and compare solver/cycle evidence."""
            before_connections = _connection_pairs(solver)
            before_cycle = _cycle_state(f"{mode}:scene_reopen_before_save")
            scene_path.parent.mkdir(parents=True, exist_ok=True)
            cmds.file(rename=str(scene_path))
            cmds.file(save=True, type="mayaAscii", force=True)
            cmds.file(str(scene_path), open=True, force=True)
            reopened_solver = solver if cmds.objExists(solver) else None
            if reopened_solver is None:
                reopened_solver = _solver_for_root(root)
            if not reopened_solver:
                raise RuntimeError("scene reopen lost mmdPhysicsSolver")
            after_connections = _connection_pairs(reopened_solver)
            after_cycle = _cycle_state(f"{mode}:scene_reopen_after_open")
            return {
                "scenePath": str(scene_path),
                "solverBefore": solver,
                "solverAfter": reopened_solver,
                "solverConnectionsStable": before_connections == after_connections,
                "cycleStable": before_cycle["cyclePlugs"] == after_cycle["cyclePlugs"],
                "cycleBefore": before_cycle,
                "cycleAfter": after_cycle,
            }

        report["operations"].append(_operation(f"{mode}:scene_reopen", scene_reopen, solver))
        report["topologyAfter"] = _topology(root, solver)
        report["topologyStable"] = report["topologyBefore"] == report["topologyAfter"]
        operation_errors = [
            row for row in report["operations"] if row.get("outcome") == "error"
        ]
        if operation_errors:
            raise RuntimeError(f"mode {mode} operation failure: {operation_errors}")
        reopen_result = report["operations"][-1].get("result") or {}
        if not reopen_result.get("solverConnectionsStable", False):
            raise RuntimeError("scene reopen changed mmdPhysicsSolver connections")
        if not reopen_result.get("cycleStable", False):
            raise RuntimeError("scene reopen changed cycleCheck state")
        if not report["topologyStable"]:
            raise RuntimeError("scene reopen changed relevant solver topology")
        report["status"] = "pass"
    except Exception as exc:
        report["errors"].append(str(exc))
    finally:
        try:
            restore = _restore_evaluation_mode(mode_before)
        except Exception as exc:
            restore = {"requested": None, "after": _evaluation_state(), "restored": False, "error": str(exc)}
            report["errors"].append(f"evaluationManager restore: {exc}")
        report["evaluationMode"]["restore"] = restore
        report["evaluationMode"]["restored"] = bool(restore.get("restored"))
        if report["status"] == "pass" and not report["evaluationMode"]["restored"]:
            report["status"] = "error"
            report["errors"].append("evaluationManager mode was not restored")
        command_state = report.pop("_commandCapture", None)
        if command_state:
            command_report = _stop_command_output_capture(command_state)
            report["mayaCommandOutput"] = command_report
            if not command_report.get("enabled"):
                report["status"] = "error"
                report["errors"].append(
                    "Maya command output capture could not be registered: "
                    f"{command_report.get('error') or 'unknown error'}"
                )
            elif command_report.get("removeError"):
                report["status"] = "error"
                report["errors"].append(
                    "Maya command output callback could not be removed: "
                    f"{command_report['removeError']}"
                )
            elif command_report.get("warningCount", 0):
                report["status"] = "error"
                report["errors"].append(
                    "Maya command output reported an mmdPhysicsSolver/outSolved "
                    f"cycle warning ({command_report['warningCount']} line(s))"
                )
    return report


def _run(args: argparse.Namespace) -> Dict[str, Any]:
    pmx = _resolve_path(args.pmx)
    modes = _parse_modes(args.modes)
    frames = _parse_frames(args.frames)
    playblast_enabled = bool(getattr(args, "playblast", True))
    requested_playblast_frame = getattr(args, "playblast_frame", None)
    playblast_frame = requested_playblast_frame if requested_playblast_frame is not None else frames[0]
    playblast_width = int(getattr(args, "playblast_width", DEFAULT_PLAYBLAST_WIDTH))
    playblast_height = int(getattr(args, "playblast_height", DEFAULT_PLAYBLAST_HEIGHT))
    if playblast_width <= 0 or playblast_height <= 0:
        raise ValueError("--playblast-width and --playblast-height must be positive")
    playblast_base = _resolve_path(getattr(args, "playblast_out", DEFAULT_PLAYBLAST))
    report: Dict[str, Any] = {
        "status": "error",
        "probe": "MMD-PHYSICS-SOLVER-CYCLE-1",
        "mayaVersion": str(cmds.about(version=True)),
        "pmx": str(pmx),
        "modesRequested": modes,
        "playblastObservation": {
            "enabled": playblast_enabled,
            "requestedFrame": playblast_frame,
            "outputBase": str(playblast_base),
            "width": playblast_width,
            "height": playblast_height,
            "offScreen": True,
        },
        "modes": [],
        "modeComparison": {},
        "errors": [],
    }
    initial_eval_mode = _evaluation_state()
    previous_cycle_evaluation: bool | None = None
    try:
        try:
            previous_cycle_evaluation = bool(cmds.cycleCheck(query=True, evaluation=True))
            # Explicitly enable checking for this probe, restoring the prior
            # flag in finally so a command-port/session host is not modified.
            cmds.cycleCheck(evaluation=True)
        except Exception as exc:
            report["errors"].append(f"cycleCheck setup: {exc}")
        if not pmx.is_file():
            raise FileNotFoundError(f"Citlali PMX fixture not found: {pmx}")

        for mode in modes:
            scene_path = _resolve_path(
                str(Path(args.out).with_name(Path(args.out).stem + f"_{mode}_scene.ma"))
            )
            mode_report = _run_mode(
                pmx=pmx,
                mode=mode,
                scene_path=scene_path,
                frames=frames,
                playblast_enabled=playblast_enabled,
                playblast_frame=int(playblast_frame),
                playblast_path=_mode_playblast_path(playblast_base, mode),
                playblast_width=playblast_width,
                playblast_height=playblast_height,
            )
            report["modes"].append(mode_report)

        facts: Dict[str, Any] = {}
        for mode_report in report["modes"]:
            mode = str(mode_report.get("mode"))
            cycle_plugs = _cycle_plug_set(mode_report)
            solver_connections = _solver_connection_set(mode_report)
            topology = _topology_set(mode_report)
            reopen = (mode_report.get("operations") or [])[-1].get("result", {}) if mode_report.get("operations") else {}
            playblast = mode_report.get("playblast") or {}
            playblast_file = playblast.get("playblast") or {}
            facts[mode] = {
                "status": mode_report.get("status"),
                "cycleCount": len(cycle_plugs),
                "cycleObservationCount": sum(
                    len((row.get("cycle") or {}).get("cyclePlugs", []))
                    for row in mode_report.get("operations", [])
                ),
                "cyclePlugs": cycle_plugs,
                "solverConnectionCount": len(solver_connections),
                "solverConnections": solver_connections,
                "solverConnectionsStable": reopen.get("solverConnectionsStable"),
                "topologyCount": len(topology),
                "topology": topology,
                "topologyStable": mode_report.get("topologyStable"),
                "evaluationModeRestored": (mode_report.get("evaluationMode") or {}).get("restored"),
                "playblastOutcome": playblast.get("outcome"),
                "playblastRequestedFrame": playblast.get("requestedFrame"),
                "playblastCapturedFrame": playblast.get("capturedFrame"),
                "playblastFileWritten": playblast_file.get("fileWritten"),
                "playblastActualOutputPath": playblast_file.get("actualOutputPath"),
            }
        report["modeComparison"] = {
            "modes": modes,
            "facts": facts,
            "cyclePlugSetsEqual": len({tuple(item["cyclePlugs"]) for item in facts.values()}) <= 1,
            "solverConnectionSetsEqual": len({tuple(item["solverConnections"]) for item in facts.values()}) <= 1,
            "topologySetsEqual": len({tuple(item["topology"]) for item in facts.values()}) <= 1,
        }
        if not report["modes"]:
            raise RuntimeError("No evaluation modes were executed")
        if any(mode_report.get("status") != "pass" for mode_report in report["modes"]):
            raise RuntimeError("One or more evaluation mode runs failed")
        report["status"] = "pass"
    except Exception as exc:
        report["errors"].append(str(exc))
    finally:
        # Each mode restores this state; repeat once at the outer boundary so
        # a partially initialized mode cannot leak evaluation settings.
        try:
            report["evaluationManagerBefore"] = initial_eval_mode
            evaluation_restore = _restore_evaluation_mode(initial_eval_mode)
            report["evaluationManagerRestore"] = evaluation_restore
            report["evaluationManagerAfter"] = _evaluation_state()
            if not evaluation_restore.get("restored", False):
                report["status"] = "error"
                report["errors"].append(
                    "evaluationManager outer restore did not restore the initial mode: "
                    f"{evaluation_restore}"
                )
        except Exception as exc:
            report["status"] = "error"
            report["errors"].append(f"evaluationManager outer restore: {exc}")
        if previous_cycle_evaluation is not None:
            try:
                cmds.cycleCheck(evaluation=previous_cycle_evaluation)
            except Exception:
                pass
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
        # A command-port harness may already have initialized standalone Maya.
        pass
    report_path = _resolve_path(args.out)
    try:
        _load_plugin(DEFAULT_ROOT)
        report = _run(args)
    except Exception as exc:
        report = {
            "status": "error",
            "probe": "MMD-PHYSICS-SOLVER-CYCLE-1",
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
