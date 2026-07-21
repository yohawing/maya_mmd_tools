"""Capture the Citlali mmdPhysicsSolver DG graph and cycle state.

This is an investigation probe for ``MMD-PHYSICS-SOLVER-CYCLE-1``.  It uses
the production PMX importer, records every solver plug and its connections,
captures the relevant rigid-body/joint/skin/IK/append topology, then runs a
small reversible playback/scrub/reset/physics-toggle sequence.  The probe
does not disconnect or repair any scene connections; all evidence is written
to a JSON report under ``build/``.

Usage (inside Maya's Python interpreter)::

    mayapy tests/viewport/physics_solver_cycle_probe.py

The command exits zero when the clean production import completed.  A cycle
reported by Maya is evidence, not a probe failure.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Tuple

import maya.cmds as cmds

DEFAULT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PMX = "build/fixtures/citlali_ascii_file/citlali.pmx"
DEFAULT_REPORT = "build/reports/physics_solver_cycle_probe.json"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pmx", default=DEFAULT_PMX, help="Citlali PMX fixture to import.")
    parser.add_argument("--out", default=DEFAULT_REPORT, help="JSON report path (must be under build/ when using nox).")
    parser.add_argument(
        "--frames",
        default="0,1,2,1,0",
        help="Comma-separated Maya frames used by the deterministic playback/scrub sequence.",
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
        modes = cmds.evaluationManager(query=True, mode=True) or []
    except Exception as exc:
        return {"modes": [], "error": str(exc)}
    return {"modes": [str(mode) for mode in modes]}


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


def _run(args: argparse.Namespace) -> Dict[str, Any]:
    pmx = _resolve_path(args.pmx)
    report: Dict[str, Any] = {
        "status": "error",
        "probe": "MMD-PHYSICS-SOLVER-CYCLE-1",
        "mayaVersion": None,
        "pmx": str(pmx),
        "modelRoot": None,
        "solver": None,
        "import": {},
        "operations": [],
        "errors": [],
    }
    report["mayaVersion"] = str(cmds.about(version=True))
    previous_cycle_evaluation: bool | None = None
    try:
        try:
            previous_cycle_evaluation = bool(cmds.cycleCheck(query=True, evaluation=True))
            # Explicitly enable checking for this probe, restoring the prior
            # flag in finally so a command-port/session host is not modified.
            cmds.cycleCheck(evaluation=True)
        except Exception as exc:
            report["errors"].append(f"cycleCheck setup: {exc}")
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
            "cycle": _cycle_state("clean_import"),
            "solverState": _solver_state(solver),
        }
        if not solver:
            raise RuntimeError(
                "Production import completed but no mmdPhysicsSolver is connected "
                f"to model root {root}; native physics registration is required"
            )
        report["solverPlugs"] = _solver_plugs(solver) if solver else {"node": None, "reason": "mmdPhysicsSolver unavailable"}
        report["topologyBefore"] = _topology(root, solver)

        frames = _parse_frames(args.frames)
        # Standalone Maya does not advance playback asynchronously, so invoke
        # play/stop and still set explicit frames to make the sequence stable.
        def playback() -> Dict[str, Any]:
            cmds.play(state=False)
            cmds.play(state=True)
            for frame in frames[: max(1, min(3, len(frames)))]:
                cmds.currentTime(frame, edit=True)
            cmds.play(state=False)
            return {"frames": frames[: max(1, min(3, len(frames)))]}

        report["operations"].append(_operation("playback", playback, solver))

        def scrub() -> Dict[str, Any]:
            for frame in frames:
                cmds.currentTime(frame, edit=True)
                cmds.refresh(force=True)
            return {"frames": frames}

        report["operations"].append(_operation("scrub", scrub, solver))

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

        report["operations"].append(_operation("reset", reset, solver))

        def toggle_physics() -> Dict[str, Any]:
            for world in worlds:
                if not cmds.attributeQuery("enable", node=world, exists=True):
                    continue
                cmds.setAttr(f"{world}.enable", False)
                cmds.refresh(force=True)
                cmds.setAttr(f"{world}.enable", True)
                cmds.refresh(force=True)
            return {"worlds": worlds, "toggled": bool(worlds)}

        report["operations"].append(_operation("physics_toggle", toggle_physics, solver))

        # Restore world values after the reversible sequence.  The reset
        # generation is restored too; the operation evidence remains in JSON.
        for world, state in world_state.items():
            for attr, value in state.items():
                if isinstance(value, (bool, int, float)):
                    try:
                        cmds.setAttr(f"{world}.{attr}", value)
                    except Exception as exc:
                        report["errors"].append(f"restore {world}.{attr}: {exc}")

        scene_path = _resolve_path(
            str(Path(args.out).with_name(Path(args.out).stem + "_scene.ma"))
        )

        def scene_reopen() -> Dict[str, Any]:
            """Save/open the imported scene and compare solver/cycle evidence."""
            before_connections = _connection_pairs(solver)
            before_cycle = _cycle_state("scene_reopen_before_save")
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
            after_cycle = _cycle_state("scene_reopen_after_open")
            return {
                "scenePath": str(scene_path),
                "solverBefore": solver,
                "solverAfter": reopened_solver,
                "solverConnectionsStable": before_connections == after_connections,
                "cycleStable": before_cycle["cyclePlugs"] == after_cycle["cyclePlugs"],
                "cycleBefore": before_cycle,
                "cycleAfter": after_cycle,
            }

        report["operations"].append(_operation("scene_reopen", scene_reopen, solver))
        report["topologyAfter"] = _topology(root, solver)
        report["topologyStable"] = report["topologyBefore"] == report["topologyAfter"]
        reopen_result = report["operations"][-1].get("result") or {}
        if report["operations"][-1].get("outcome") == "error":
            raise RuntimeError(
                "scene reopen operation failed: " + str(report["operations"][-1].get("error"))
            )
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
