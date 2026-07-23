"""Maya standalone probe for a restored MMD foot-IK SOURCE retarget.

Both PMX files are imported with ``setup_rig=True`` so their actual
``mmdCcdIk`` foot controllers remain live. TARGET preview must isolate only
the target's importer-owned foot writer edges for its lifetime; SOURCE remains
the active HumanIK input. The probe moves the source leg IK controller, then
restores the preview and verifies the target edges reconnect exactly.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence

import maya.cmds as cmds
import maya.standalone


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mmd_tools.core.humanik_frontend import FULL_ASSIGNMENT_PROFILE, HumanIkFrontendSession


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe restored MMD foot IK as HumanIK SOURCE.")
    parser.add_argument("--source-pmx", default="tests/data/mmt_test_model.pmx")
    parser.add_argument("--target-pmx", default=None)
    parser.add_argument("--out", default="build/reports/humanik_source_ik_retarget_probe.json")
    parser.add_argument("--offset", type=float, default=0.5)
    parser.add_argument("--tolerance", type=float, default=1.0e-4)
    return parser.parse_args()


def _load_plugin() -> None:
    plugin_path = _PROJECT_ROOT / "mmd_tools" / "plugin_main.py"
    if not cmds.pluginInfo(plugin_path.stem, query=True, loaded=True):
        cmds.loadPlugin(str(plugin_path), quiet=True)


def _import_model(path: Path, *, setup_rig: bool) -> str:
    from mmd_tools.io.mmd_importer import import_mmd_file

    root = import_mmd_file(
        str(path),
        options={
            "use_namespace": True,
            "setup_rig": bool(setup_rig),
            "import_physics": False,
            "create_mmd_shaders": False,
            "use_native_pmx_parse": False,
            "require_native_pmx_parse": False,
        },
    )
    if not root:
        raise RuntimeError(f"MMD model import failed: {path}")
    return str(root)


def _matrix(node: str) -> tuple[float, ...]:
    return tuple(float(value) for value in cmds.xform(node, query=True, worldSpace=True, matrix=True))


def _vector(value: Any) -> tuple[float, ...]:
    while isinstance(value, (tuple, list)) and len(value) == 1:
        value = value[0]
    return tuple(float(item) for item in (value or ()))


def _max_delta(before: Sequence[float], after: Sequence[float]) -> float:
    return max((abs(float(left) - float(right)) for left, right in zip(before, after)), default=0.0)


def _long_node_name(node: str) -> str:
    """Resolve a connection's short DAG node name to its long spelling."""
    matches = cmds.ls(node, long=True) or []
    return str(matches[0]) if matches else str(node)


def _source_ik_topology(source_assignments: Iterable[Any]) -> Dict[str, list[str]]:
    assignment_joints = {str(item.joint) for item in source_assignments}
    topology: Dict[str, list[str]] = {}
    for node in sorted(str(item) for item in (cmds.ls(type="mmdCcdIk", long=True) or [])):
        destinations = []
        for index in range(64):
            plug = f"{node}.outputRotate[{index}]"
            for destination in cmds.listConnections(plug, source=False, destination=True, plugs=True) or []:
                if _long_node_name(str(destination).rsplit(".", 1)[0]) in assignment_joints:
                    destinations.append(str(destination))
        if destinations:
            topology[node] = sorted(set(destinations))
    return topology


def _find_source_foot_controller(source_assignments: Iterable[Any]) -> Mapping[str, Any]:
    assignment_joints = {str(item.joint) for item in source_assignments}
    candidates = []
    for node in sorted(str(item) for item in (cmds.ls(type="mmdCcdIk", long=True) or [])):
        leaf = node.rsplit("|", 1)[-1].lower()
        if "left_leg_ik_mmdccdik" not in leaf:
            continue
        destinations = []
        for index in range(64):
            plug = f"{node}.outputRotate[{index}]"
            if cmds.listConnections(plug, source=False, destination=True, plugs=True):
                destinations.extend(
                    str(item)
                    for item in cmds.listConnections(plug, source=False, destination=True, plugs=True) or []
                )
        if not any(
            _long_node_name(str(item).rsplit(".", 1)[0]) in assignment_joints
            for item in destinations
        ):
            continue
        # The importer supplies the controller transform through the compound
        # matrix input; the scalar goalX/Y/Z attributes are computed outputs,
        # not connected plugs.
        goal_sources = (
            cmds.listConnections(
                f"{node}.goalWorldMatrix",
                source=True,
                destination=False,
                plugs=True,
            )
            or []
        )
        if not goal_sources:
            continue
        controller = str(goal_sources[0]).rsplit(".", 1)[0]
        if not cmds.objExists(controller):
            continue
        candidates.append((node, controller, sorted(set(destinations))))
    if not candidates:
        raise RuntimeError("No source left_leg_ik_mmdCcdIk controller with a goalWorldMatrix input was found")
    node, controller, destinations = candidates[0]
    return {"node": node, "controller": controller, "destinations": destinations}


def _node_edge_connected(node: str, destination: str) -> bool:
    """Return whether ``node`` still feeds the destination plug."""
    return any(
        str(source).rsplit(".", 1)[0] == str(node)
        for source in cmds.listConnections(
            destination,
            source=True,
            destination=False,
            plugs=True,
        )
        or []
    )


def run_probe(args: argparse.Namespace) -> Dict[str, Any]:
    source_pmx = Path(args.source_pmx).resolve()
    target_pmx = Path(args.target_pmx or args.source_pmx).resolve()
    payload: Dict[str, Any] = {
        "status": "fail",
        "sourcePmx": str(source_pmx),
        "targetPmx": str(target_pmx),
        "checks": {},
    }
    session = None
    controller = None
    original_controller_translate = None
    try:
        if not source_pmx.is_file() or not target_pmx.is_file():
            raise FileNotFoundError(f"Fixtures not found: source={source_pmx} target={target_pmx}")
        cmds.file(new=True, force=True)
        _load_plugin()
        source_root = _import_model(source_pmx, setup_rig=True)
        session = HumanIkFrontendSession(cmds_module=cmds)
        source_binding = session.setup_and_characterize(
            source_root,
            profile=FULL_ASSIGNMENT_PROFILE,
            include_fingers=True,
        )
        source_topology_after_characterize = _source_ik_topology(source_binding.assignments)
        source_stance = dict(source_binding.stance)
        session.enter_source_mode(source_root)
        target_root = _import_model(target_pmx, setup_rig=True)
        target_binding = session.setup_and_characterize(
            target_root,
            profile=FULL_ASSIGNMENT_PROFILE,
            include_fingers=True,
        )
        target_topology_before_preview = _source_ik_topology(target_binding.assignments)
        target_ownership_preflight = session.inspect_target_ownership(target_root)
        target_preflight_clear = not bool(target_ownership_preflight.get("blockers"))
        preview = session.enter_target_mode(target_root)
        target_preview_started = bool(preview and preview.active)
        target_edges_disconnected = bool(target_topology_before_preview) and all(
            not _node_edge_connected(node, destination)
            for node, destinations in target_topology_before_preview.items()
            for destination in destinations
        )

        source_ik = _find_source_foot_controller(source_binding.assignments)
        controller = str(source_ik["controller"])
        original_controller_translate = _vector(cmds.getAttr(f"{controller}.translate"))
        target_foot = next(
            (str(item.joint) for item in target_binding.assignments if str(item.hik_bone) == "LeftFoot"),
            None,
        )
        if not target_foot:
            raise RuntimeError("Target HIK assignment LeftFoot is missing")
        source_outputs = {
            destination: _vector(cmds.getAttr(destination))
            for destination in source_ik["destinations"]
            if cmds.objExists(destination)
        }
        target_before = _matrix(target_foot)
        moved = list(original_controller_translate)
        if len(moved) != 3:
            raise RuntimeError(f"Source controller translate is not a 3-vector: {controller}")
        moved[1] += float(args.offset)
        cmds.setAttr(f"{controller}.translate", *moved, type="double3")
        cmds.dgdirty(controller)
        cmds.refresh(force=True)
        target_after = _matrix(target_foot)
        source_output_after = {
            destination: _vector(cmds.getAttr(destination))
            for destination in source_outputs
            if cmds.objExists(destination)
        }
        source_output_delta = max(
            (_max_delta(source_outputs[key], source_output_after.get(key, ())) for key in source_outputs),
            default=0.0,
        )
        target_delta = _max_delta(target_before, target_after)
        session.restore_mmd_rig()
        target_topology_after_restore = _source_ik_topology(target_binding.assignments)
        target_edges_restored = target_topology_after_restore == target_topology_before_preview
        payload.update(
            {
                "sourceRoot": source_root,
                "targetRoot": target_root,
                "sourceCharacter": source_binding.character,
                "targetCharacter": target_binding.character,
                "sourceIk": dict(source_ik),
                "sourceStance": source_stance,
                "sourceTopologyAfterCharacterize": source_topology_after_characterize,
                "targetStance": dict(target_binding.stance),
                "targetTopologyBeforePreview": target_topology_before_preview,
                "targetTopologyAfterRestore": target_topology_after_restore,
                "targetOwnershipPreflight": target_ownership_preflight,
                "targetPreviewStarted": target_preview_started,
                "targetFootIkEdgesDisconnected": target_edges_disconnected,
                "targetFoot": target_foot,
                "targetFootBefore": list(target_before),
                "targetFootAfter": list(target_after),
                "sourceOutputDelta": source_output_delta,
                "targetFootDelta": target_delta,
            }
        )
        payload["checks"] = {
            "sourceSetupRigCharacterized": bool(source_stance.get("restore", {}).get("topologyRestored")),
            "targetSetupRigCharacterized": bool(target_binding.stance.get("restore", {}).get("topologyRestored")),
            "targetOwnershipPreflightClear": target_preflight_clear,
            "targetPreviewStarted": target_preview_started,
            "targetFootIkEdgesDisconnected": target_edges_disconnected,
            "sourceMmdCcdIkOutputMoved": source_output_delta > float(args.tolerance),
            "targetFootResponded": target_delta > float(args.tolerance),
            "targetFootIkEdgesRestored": target_edges_restored,
        }
        if not all(payload["checks"].values()):
            raise RuntimeError(f"Source IK retarget acceptance failed: {payload['checks']}")
        payload["status"] = "pass"
    except Exception as error:  # pragma: no cover - executed in Maya
        payload["error"] = str(error)
        payload["errorType"] = type(error).__name__
    finally:
        if controller and original_controller_translate is not None and cmds.objExists(controller):
            try:
                cmds.setAttr(f"{controller}.translate", *original_controller_translate, type="double3")
                cmds.refresh(force=True)
            except Exception as error:  # pragma: no cover - Maya cleanup
                payload["restoreError"] = str(error)
        if session is not None:
            try:
                session.restore_mmd_rig()
            except Exception as error:  # pragma: no cover - Maya cleanup
                payload["sessionRestoreError"] = str(error)
    return payload


def main() -> int:
    args = _parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    maya.standalone.initialize(name="python")
    try:
        payload = run_probe(args)
    finally:
        maya.standalone.uninitialize()
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload.get("status"), "checks": payload.get("checks", {}), "error": payload.get("error")}))
    return 0 if payload.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
