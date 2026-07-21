"""Maya 2024 S4 bake-boundary smoke using the checked-in PMX/VMD fixture.

The smoke runs an exclusive TARGET preview, samples active HIK output, stops
the preview back to NEUTRAL, keys pre-solver channels, and reports all-frame
residuals plus topology/input restoration.  The fixture also gates the native
mmdAppend grant math with a nonzero synthetic grant.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict

import maya.cmds as cmds
import maya.mel as mel
import maya.standalone
import maya.api.OpenMaya as om

from mmd_tools.core.humanik_bake import (
    CHANNELS,
    _append_base_sample,
    bake_humanik_target_preview,
)
from mmd_tools.core.humanik_builder import (
    create_humanik_definition_from_scene,
    lock_humanik_definition,
    resolve_scene_humanik_assignments,
)
from mmd_tools.core.humanik_constraints import (
    classify_humanik_constraints,
    collect_humanik_constraint_facts,
    snapshot_constraint_connections,
)
from mmd_tools.core.humanik_preview import begin_humanik_target_preview


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke HumanIK S4 bake boundary under mayapy.")
    parser.add_argument("--pmx", default="tests/data/mmt_test_model.pmx")
    parser.add_argument("--vmd", default="tests/data/mmt_test_model_test_motion.vmd")
    parser.add_argument("--out", default="build/reports/humanik_bake_smoke.json")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--end", type=int, default=10)
    parser.add_argument("--evaluation-mode", choices=("off", "serial", "parallel"), default="off")
    return parser.parse_args()


def _load_plugin() -> None:
    path = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
    if not cmds.pluginInfo(path.stem, query=True, loaded=True):
        cmds.loadPlugin(str(path), quiet=True)


def _load_model(path: Path, *, setup_rig: bool = False) -> str:
    from mmd_tools.io.mmd_importer import import_mmd_file

    root = import_mmd_file(
        str(path),
        options={
            "use_namespace": True,
            "setup_rig": setup_rig,
            "import_physics": False,
            "create_mmd_shaders": False,
            "use_native_pmx_parse": False,
            "require_native_pmx_parse": False,
        },
    )
    if not root:
        raise RuntimeError(f"PMX import failed: {path}")
    return str(root)


def _load_motion(path: Path, pmx: Path, target_model: str) -> None:
    from mmd_tools.io.mmd_importer import import_mmd_file

    if not import_mmd_file(
        str(path),
        options={
            "target_model": target_model,
            "pmx_path": str(pmx),
            "bake_mode": True,
            "clear_existing_motion": True,
            "use_native_pmx_parse": False,
            "require_native_pmx_parse": False,
        },
    ):
        raise RuntimeError(f"VMD import failed: {path}")


def _set_evaluation_mode(mode: str) -> None:
    """Set one isolated Maya evaluation mode for this fresh-scene process."""
    cmds.evaluationManager(mode=mode)


def _as_vector3(value, plug: str):
    while isinstance(value, (list, tuple)) and len(value) == 1:
        value = value[0]
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise RuntimeError(f"Expected vector3 from {plug}: {value!r}")
    return tuple(float(item) for item in value)


def _mmd_append_synthetic_gate() -> Dict[str, Any]:
    """Prove base correction preserves a real mmdAppend nonzero grant."""
    node = cmds.createNode("mmdAppend", name="MMDToolsS4_AppendSynthetic")
    try:
        cmds.setAttr(f"{node}.baseRotate", 10.0, -5.0, 20.0, type="double3")
        cmds.setAttr(f"{node}.sourceRotate", 35.0, 12.0, -8.0, type="double3")
        cmds.setAttr(f"{node}.ratio", 0.5)
        cmds.setAttr(f"{node}.affectRotation", 1)
        base = _as_vector3(cmds.getAttr(f"{node}.baseRotate"), f"{node}.baseRotate")
        output = _as_vector3(cmds.getAttr(f"{node}.outputRotate"), f"{node}.outputRotate")
        delta = max(abs(output[index] - base[index]) for index in range(3))
        desired = (25.0, -15.0, 8.0)
        corrected_base = _append_base_sample(base, output, desired, "rotate")
        cmds.setAttr(f"{node}.baseRotate", *corrected_base, type="double3")
        corrected_output = _as_vector3(
            cmds.getAttr(f"{node}.outputRotate"), f"{node}.outputRotate"
        )
        desired_quat = om.MEulerRotation(
            *(math.radians(value) for value in desired)
        ).asQuaternion()
        actual_quat = om.MEulerRotation(
            *(math.radians(value) for value in corrected_output)
        ).asQuaternion()
        dot = abs(
            desired_quat.x * actual_quat.x
            + desired_quat.y * actual_quat.y
            + desired_quat.z * actual_quat.z
            + desired_quat.w * actual_quat.w
        )
        angular_error = math.degrees(2.0 * math.acos(max(-1.0, min(1.0, dot))))
        return {
            "available": True,
            "nonzeroGrant": delta > 1.0e-4,
            "maxOutputBaseDelta": delta,
            "correctedAngularErrorDegrees": angular_error,
            "correctedOutputMatches": angular_error <= 1.0e-5,
        }
    finally:
        if cmds.objExists(node):
            cmds.delete(node)


def _snapshot_scene_connections(joints):
    """Capture solver edges plus every target joint channel destination."""
    result = snapshot_constraint_connections()
    for joint in joints:
        for channel in CHANNELS:
            plug = f"{joint}.{channel}"
            result[f"target:{plug}"] = [
                str(value)
                for value in (
                    cmds.listConnections(
                        plug,
                        source=True,
                        destination=False,
                        plugs=True,
                        connections=True,
                    )
                    or []
                )
            ]
    return result


def _snapshot_edges(snapshot: Dict[str, list[str]], cmds_module=None) -> set[tuple[str, str]]:
    """Normalize Maya connection-pair snapshots into deterministic edge sets."""
    edges = set()
    for values in snapshot.values():
        for index in range(0, len(values) - 1, 2):
            left, right = str(values[index]), str(values[index + 1])
            if cmds_module is not None:
                left = _canonical_plug(cmds_module, left)
                right = _canonical_plug(cmds_module, right)
            edges.add(tuple(sorted((left, right))))
    return edges


def _classify_connection_deltas(before, after, routes, cmds_module=cmds):
    before_edges = _snapshot_edges(before, cmds_module)
    after_edges = _snapshot_edges(after, cmds_module)
    added = sorted(after_edges - before_edges)
    removed = sorted(before_edges - after_edges)
    route_plugs = {
        _canonical_plug(cmds_module, str(route))
        for route in routes.values()
        if _route_node_type(cmds_module, route) in {"mmdCcdIk", "mmdAppend"}
        or _connection_node_type(cmds_module, str(route)) == "joint"
    }
    expected_added = [
        edge
        for edge in added
        if any(
            _canonical_plug(cmds_module, endpoint) in route_plugs
            and any(
                _connection_node_type(cmds_module, peer).startswith("animCurve")
                for peer in edge
                if peer != endpoint
            )
            for endpoint in edge
        )
    ]
    unexpected_added = [edge for edge in added if edge not in expected_added]
    expected_hik_removed = [
        edge
        for edge in removed
        if any(
            _canonical_plug(cmds_module, endpoint) in route_plugs
            and any(
                _connection_node_type(cmds_module, peer) == "HIKState2SK"
                for peer in edge
                if peer != endpoint
            )
            for endpoint in edge
        )
    ]
    unexpected_removed = [edge for edge in removed if edge not in expected_hik_removed]
    return {
        "added": added,
        "removed": removed,
        "expectedBakeEdges": expected_added,
        "unexpectedAdded": unexpected_added,
        "expectedHikWriterRemovals": expected_hik_removed,
        "unexpectedRemoved": unexpected_removed,
        "baselinePreserved": not unexpected_removed,
        "onlyExpectedBakeEdges": not unexpected_added and not unexpected_removed,
    }


def _route_node_type(cmds_module, route: str) -> str:
    """Resolve a bake route by Maya node type, independent of node naming."""
    node = str(route).split(".", 1)[0]
    try:
        return str(cmds_module.nodeType(node))
    except Exception:
        return ""


def _connection_node_type(cmds_module, plug: str) -> str:
    node = str(plug).rsplit(".", 1)[0]
    try:
        return str(cmds_module.nodeType(node))
    except Exception:
        return ""


def _canonical_plug(cmds_module, plug: str) -> str:
    """Normalize DAG endpoints so Maya short/long connection names compare."""
    value = str(plug)
    if "." not in value:
        return value
    node, attribute = value.rsplit(".", 1)
    try:
        long_names = cmds_module.ls(node, long=True) or []
    except Exception:
        long_names = []
    return f"{long_names[0] if long_names else node}.{attribute}"


def _final_writer_gate(target_joints, routes) -> Dict[str, Any]:
    """Require one non-HIK effective owner for every sampled joint channel."""
    violations = []
    counts = {
        "sampled": 0,
        "hikIncoming": 0,
        "directAnimCurve": 0,
        "solverBakedInputs": 0,
        "mmdAppendOutputs": 0,
        "mmdCcdIkOutputs": 0,
    }
    for joint in target_joints:
        for channel in CHANNELS:
            plug = f"{joint}.{channel}"
            route = str(routes[plug])
            route_type = _route_node_type(cmds, route)
            incoming = [
                str(value)
                for value in (cmds.listConnections(plug, source=True, destination=False, plugs=True) or [])
            ]
            hik_sources = [source for source in incoming if _connection_node_type(cmds, source) == "HIKState2SK"]
            counts["sampled"] += 1
            counts["hikIncoming"] += len(hik_sources)
            if hik_sources:
                violations.append({"plug": plug, "reason": "HIKState2SK incoming", "sources": incoming})
                continue
            if route_type in {"mmdAppend", "mmdCcdIk"}:
                parent = "rotate" if channel.startswith("rotate") else "translate"
                parent_sources = [
                    str(value)
                    for value in (
                        cmds.listConnections(
                            f"{joint}.{parent}",
                            source=True,
                            destination=False,
                            plugs=True,
                        )
                        or []
                    )
                ]
                effective_incoming = sorted(set(incoming + parent_sources))
                output_sources = [
                    source
                    for source in effective_incoming
                    if _connection_node_type(cmds, source) == route_type
                ]
                if len(output_sources) != 1:
                    violations.append(
                        {
                            "plug": plug,
                            "reason": f"expected one {route_type} joint output",
                            "sources": effective_incoming,
                        }
                    )
                    continue
                counts[f"{route_type}Outputs"] += 1
                author_sources = [
                    str(value)
                    for value in (
                        cmds.listConnections(route, source=True, destination=False, plugs=True) or []
                    )
                ]
                baked = [source for source in author_sources if _connection_node_type(cmds, source).startswith("animCurve")]
                if len(author_sources) != 1 or len(baked) != 1:
                    violations.append(
                        {"plug": plug, "reason": "expected one animCurve solver input", "sources": author_sources}
                    )
                    continue
                counts["solverBakedInputs"] += 1
                continue
            effective = [source for source in incoming if _connection_node_type(cmds, source).startswith("animCurve")]
            if len(incoming) != 1 or len(effective) != 1:
                violations.append(
                    {"plug": plug, "reason": "expected one direct animCurve owner", "sources": incoming}
                )
                continue
            counts["directAnimCurve"] += 1
    return {"counts": counts, "violations": violations, "passed": not violations}


def main() -> int:
    args = _parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {
        "status": "fail",
        "fixtures": {"pmx": str(args.pmx), "vmd": str(args.vmd)},
        "frameRange": {"start": args.start, "end": args.end},
    }
    maya.standalone.initialize(name="python")
    try:
        payload["mayaVersion"] = cmds.about(version=True)
        _load_plugin()
        _set_evaluation_mode(args.evaluation_mode)
        payload["evaluationMode"] = args.evaluation_mode
        payload["appendSyntheticGate"] = _mmd_append_synthetic_gate()
        if not all(
            (
                payload["appendSyntheticGate"]["nonzeroGrant"],
                payload["appendSyntheticGate"]["correctedOutputMatches"],
            )
        ):
            raise RuntimeError("mmdAppend synthetic correction gate failed")
        pmx = Path(args.pmx).resolve()
        vmd = Path(args.vmd).resolve()
        if not pmx.is_file() or not vmd.is_file():
            raise FileNotFoundError(f"S4 fixtures not found: pmx={pmx} vmd={vmd}")

        source_root = _load_model(pmx)
        _load_motion(vmd, pmx, source_root)
        target_root = _load_model(pmx, setup_rig=True)
        source_result = resolve_scene_humanik_assignments(source_root)
        target_result = resolve_scene_humanik_assignments(target_root)
        source_character = create_humanik_definition_from_scene(
            source_root, name_hint="MMDToolsS4_Source", update_ui=False
        )
        target_character = create_humanik_definition_from_scene(
            target_root, name_hint="MMDToolsS4_Target", update_ui=False
        )
        lock_humanik_definition(source_character)
        lock_humanik_definition(target_character)

        target_joints = tuple(item.joint for item in target_result.assignments)
        original_connections = _snapshot_scene_connections(target_joints)
        ownership = classify_humanik_constraints(
            collect_humanik_constraint_facts(),
            target_joints,
        )
        preview = begin_humanik_target_preview(
            "mmd-tools:s4:bake",
            target_character,
            source_character,
            ownership,
            target_joints,
        )
        active_before = _snapshot_scene_connections(target_joints)
        bake = bake_humanik_target_preview(
            preview,
            target_joints,
            args.start,
            args.end,
            mel_module=mel,
        )
        after = _snapshot_scene_connections(target_joints)
        connection_deltas = _classify_connection_deltas(original_connections, after, bake.routes, cmds_module=cmds)
        restored_source = str(mel.eval(f'hikGetRetargetCharacterInput("{target_character}")') or "")
        route_counts = {"direct": 0, "mmdAppend": 0, "mmdCcdIk": 0}
        for route in bake.routes.values():
            route_type = _route_node_type(cmds, route)
            if route_type == "mmdAppend":
                route_counts["mmdAppend"] += 1
            elif route_type == "mmdCcdIk":
                route_counts["mmdCcdIk"] += 1
            else:
                route_counts["direct"] += 1
        final_writer_gate = _final_writer_gate(target_joints, bake.routes)
        payload.update(
            {
                "sourceRoot": source_root,
                "targetRoot": target_root,
                "sourceCharacter": source_character,
                "targetCharacter": target_character,
                "sourceAssignmentCount": len(source_result.assignments),
                "targetAssignmentCount": len(target_result.assignments),
                "ownershipCounts": ownership["counts"],
                "routeCounts": route_counts,
                "keyCount": bake.key_count,
                "staleControlWarning": "mmd_ik_controls_may_be_stale" in bake.warnings,
                "warnings": list(bake.warnings),
                "hikInputAfterBake": restored_source,
                "baselineConnectionsPreserved": connection_deltas["baselinePreserved"],
                "onlyExpectedBakeConnectionsAdded": connection_deltas["onlyExpectedBakeEdges"],
                "expectedBakeEdgesPresent": bool(connection_deltas["expectedBakeEdges"]),
                "expectedBakeEdgeCount": len(connection_deltas["expectedBakeEdges"]),
                "previewMutedEdgeCount": len(
                    _snapshot_edges(original_connections, cmds) - _snapshot_edges(active_before, cmds)
                ),
                "expectedHikWriterRemovals": connection_deltas["expectedHikWriterRemovals"],
                "connectionDeltas": connection_deltas,
                "neutralInputRestored": restored_source == "",
                "preBakeRestoreStateRestored": bake.pre_bake_restore_state_restored,
                "disabledIkNodes": list(bake.disabled_ik_nodes),
                "disabledIkNodesFinal": all(
                    not bool(cmds.getAttr(f"{node}.enabled")) for node in bake.disabled_ik_nodes
                ),
                "frameErrors": dict(bake.frame_errors),
                "allFrameMaxError": bake.max_error,
                "finalWriterGate": final_writer_gate,
                "bake": bake.to_dict(),
            }
        )
        payload["status"] = "pass" if all(
            (
                payload["keyCount"] > 0,
                payload["preBakeRestoreStateRestored"],
                payload["disabledIkNodes"],
                payload["disabledIkNodesFinal"],
                payload["baselineConnectionsPreserved"],
                payload["onlyExpectedBakeConnectionsAdded"],
                payload["expectedBakeEdgesPresent"],
                payload["expectedBakeEdgeCount"] == len(set(bake.routes.values())),
                payload["neutralInputRestored"],
                payload["routeCounts"]["mmdCcdIk"] > 0,
                payload["staleControlWarning"],
                payload["allFrameMaxError"] <= 1.0e-5,
                all(error <= 1.0e-5 for error in payload["frameErrors"].values()),
                payload["finalWriterGate"]["passed"],
            )
        ) else "fail"
        if payload["status"] != "pass":
            raise RuntimeError(
                "HumanIK S4 bake acceptance failed: "
                f"error={payload['allFrameMaxError']} "
                f"baseline={payload['baselineConnectionsPreserved']} "
                f"expectedOnly={payload['onlyExpectedBakeConnectionsAdded']} "
                f"neutralInput={payload['neutralInputRestored']}"
            )
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"status": payload["status"], "keyCount": payload["keyCount"], "maxError": payload["allFrameMaxError"]}, sort_keys=True))
        return 0
    except Exception as exc:
        payload["error"] = str(exc)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        raise
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
