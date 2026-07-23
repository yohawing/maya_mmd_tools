"""Capture root-motion deltas for Citlali feet, IK targets, and controllers.

This is an evidence-only diagnostic for the emergency root/IK drift report.  It
performs one production PMX import, records the relevant DAG/DG topology, moves
the imported model root by a known non-zero world-space translation, and records
the same nodes again.  Parent paths and ``inheritsTransform`` values are kept in
the report so a double transform can be attributed to a concrete parent.  No
root reset, animation bake, PMX write, or production-source change is performed.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import maya.api.OpenMaya as om
import maya.cmds as cmds

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PMX = "build/fixtures/citlali_ascii_file/citlali.pmx"
DEFAULT_OUT = "build/reports/root_move_ik_target_probe.json"
DEFAULT_DELTA = (17.5, -8.25, 11.0)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pmx", default=DEFAULT_PMX, help="ASCII-path Citlali PMX fixture.")
    parser.add_argument("--out", default=DEFAULT_OUT, help="JSON report path under build/.")
    parser.add_argument(
        "--delta",
        default=",".join(str(value) for value in DEFAULT_DELTA),
        help="Non-zero world-space root translation delta as X,Y,Z.",
    )
    parser.add_argument(
        "--expect-root-parity",
        action="store_true",
        help="Fail unless tracked foot/IK nodes follow the root delta within tolerance.",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=1.0e-4,
        help="Absolute translation residual tolerance used by --expect-root-parity.",
    )
    return parser.parse_args()


def _resolve(value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def _parse_delta(raw: str) -> List[float]:
    values = [float(part.strip()) for part in str(raw).split(",") if part.strip()]
    if len(values) != 3:
        raise ValueError("--delta must contain exactly three comma-separated numbers")
    if math.sqrt(sum(value * value for value in values)) <= 1.0e-9:
        raise ValueError("--delta must be non-zero; root zeroing is intentionally prohibited")
    return values


def _load_plugin() -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from tests.common.maya_plugin_setup import load_mmd_tools_plugin

    load_mmd_tools_plugin(ROOT)


def _import_model(path: Path) -> str:
    from mmd_tools.io.mmd_importer import import_mmd_file

    root = import_mmd_file(
        str(path),
        options={
            "use_namespace": True,
            "setup_rig": True,
            "import_physics": False,
            "create_mmd_shaders": False,
            "use_native_pmx_parse": False,
            "require_native_pmx_parse": False,
        },
    )
    if not root:
        raise RuntimeError(f"PMX import failed: {path}")
    return str(root)


def _safe_attr(node: str, attr: str, default: Any = None) -> Any:
    try:
        if not cmds.attributeQuery(attr, node=node, exists=True):
            return default
        value = cmds.getAttr(f"{node}.{attr}")
    except Exception:
        return default
    return default if value is None else value


def _node_type(node: str) -> str:
    try:
        return str(cmds.nodeType(node))
    except Exception:
        return "unknown"


def _leaf(node: str) -> str:
    return str(node).rsplit("|", 1)[-1]


def _long_node(node: str) -> str:
    matches = cmds.ls(node, long=True) or []
    return str(matches[0]) if matches else str(node)


def _matrix(node: str, plug: str = "worldMatrix[0]") -> Optional[om.MMatrix]:
    try:
        return om.MMatrix(cmds.getAttr(f"{node}.{plug}"))
    except Exception:
        return None


def _matrix_values(value: Optional[om.MMatrix]) -> Optional[List[float]]:
    if value is None:
        return None
    return [float(value[index]) for index in range(16)]


def _translation(value: Optional[om.MMatrix]) -> Optional[List[float]]:
    if value is None:
        return None
    return [float(value[index]) for index in (12, 13, 14)]


def _local_translation(node: str) -> Optional[List[float]]:
    value = _safe_attr(node, "translate")
    if isinstance(value, (tuple, list)) and value and isinstance(value[0], (tuple, list)):
        value = value[0]
    if not isinstance(value, (tuple, list)) or len(value) < 3:
        return None
    return [float(value[index]) for index in range(3)]


def _parent_chain(node: str) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    current = str(node)
    while True:
        parents = cmds.listRelatives(current, parent=True, fullPath=True) or []
        if not parents:
            break
        parent = str(parents[0])
        result.append(
            {
                "node": parent,
                "leaf": _leaf(parent),
                "type": _node_type(parent),
                "inheritsTransform": _safe_attr(parent, "inheritsTransform"),
                "localTranslation": _local_translation(parent),
                "worldTranslation": _translation(_matrix(parent)),
            }
        )
        current = parent
    return result


def _connections(node: str) -> List[str]:
    try:
        values = cmds.listConnections(
            node, plugs=True, connections=True, source=True, destination=True
        ) or []
    except Exception:
        values = []
    return sorted(set(str(value) for value in values))


def _plug_sources(node: str, attr: str) -> List[str]:
    try:
        values = cmds.listConnections(
            f"{node}.{attr}", plugs=True, source=True, destination=False
        ) or []
    except Exception:
        values = []
    return sorted(set(str(value) for value in values))


def _plug_destinations(node: str, attr: str) -> List[str]:
    try:
        values = cmds.listConnections(
            f"{node}.{attr}", plugs=True, source=False, destination=True
        ) or []
    except Exception:
        values = []
    return sorted(set(str(value) for value in values))


def _transform_row(node: str, role: str) -> Dict[str, Any]:
    full = _long_node(node)
    matrix = _matrix(full)
    return {
        "node": full,
        "leaf": _leaf(full),
        "type": _node_type(full),
        "role": role,
        "worldMatrix": _matrix_values(matrix),
        "worldTranslation": _translation(matrix),
        "localTranslation": _local_translation(full),
        "inheritsTransform": _safe_attr(full, "inheritsTransform"),
        "parent": (
            str((cmds.listRelatives(full, parent=True, fullPath=True) or [None])[0])
            if cmds.listRelatives(full, parent=True, fullPath=True)
            else None
        ),
        "incomingTranslate": _plug_sources(full, "translate"),
        "incomingRotate": _plug_sources(full, "rotate"),
        "parentChain": _parent_chain(full),
        "connections": _connections(full),
    }


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _metadata(node: str) -> Dict[str, Any]:
    names = [_leaf(node)]
    for attr in ("mmd_bone_name_en", "mmd_bone_name"):
        value = _safe_attr(node, attr)
        if value:
            names.append(str(value))
    return {
        "node": _long_node(node),
        "names": names,
        "normalized": _norm(" ".join(names)),
        "boneIndex": _safe_attr(node, "mmd_bone_index"),
    }


def _side(normalized: str) -> Optional[str]:
    if "left" in normalized or normalized.startswith("lleg") or normalized.startswith("lfoot"):
        return "left"
    if "right" in normalized or normalized.startswith("rleg") or normalized.startswith("rfoot"):
        return "right"
    return None


def _joint_catalog(root: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    joints = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
    for joint in joints:
        row = _metadata(str(joint))
        row["side"] = _side(row["normalized"])
        rows.append(row)
    return rows


def _select_foot(catalog: Sequence[Mapping[str, Any]], side: str) -> Optional[str]:
    candidates: List[Tuple[int, str]] = []
    for row in catalog:
        if row.get("side") != side:
            continue
        normalized = str(row.get("normalized") or "")
        if "ik" in normalized and not any(token in normalized for token in ("ankle", "foot")):
            continue
        score = 0
        if "foot" in normalized:
            score += 100
        if "ankle" in normalized:
            score += 90
        if "toe" in normalized:
            score += 70
        if "leg" in normalized:
            score += 20
        if score:
            candidates.append((score, str(row["node"])))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (-item[0], item[1]))[0][1]


def _parse_chain(node: str) -> Dict[str, Any]:
    raw = _safe_attr(node, "chainJson", "")
    if not raw:
        return {}
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError):
        return {"_parseError": True, "raw": str(raw)}
    return value if isinstance(value, dict) else {"value": value}


def _resolve_transform(root: str, names: Iterable[str]) -> Optional[str]:
    wanted = {_norm(name) for name in names if name}
    if not wanted:
        return None
    descendants = cmds.listRelatives(root, allDescendents=True, type="transform", fullPath=True) or []
    for node in descendants:
        if _norm(_leaf(str(node))) in wanted:
            return str(node)
    return None


def _resolve_bone_index(catalog: Sequence[Mapping[str, Any]], value: Any) -> Optional[str]:
    try:
        index = int(value)
    except (TypeError, ValueError):
        return None
    for row in catalog:
        if row.get("boneIndex") == index:
            return str(row["node"])
    return None


def _ik_context(root: str, catalog: Sequence[Mapping[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, str], Dict[str, str]]:
    nodes = cmds.ls(type="mmdCcdIk", long=True) or []
    rows: List[Dict[str, Any]] = []
    controllers: Dict[str, str] = {}
    targets: Dict[str, str] = {}
    for raw_node in nodes:
        node = str(raw_node)
        chain = _parse_chain(node)
        leaf = _leaf(node)
        prefix = leaf.rsplit("_mmdCcdIk", 1)[0] if "_mmdCcdIk" in leaf else leaf
        goal_sources: Dict[str, List[str]] = {}
        for attr in ("goalWorldMatrix", "goalX", "goalY", "goalZ"):
            goal_sources[attr] = _plug_sources(node, attr)
        controller = None
        for source in goal_sources.get("goalWorldMatrix", []) + goal_sources.get("goalX", []):
            source_node = source.split(".", 1)[0]
            if _node_type(source_node) in {"transform", "joint"}:
                controller = _long_node(source_node)
                break
        if controller is None:
            controller = _resolve_transform(root, (prefix, leaf))
        if controller:
            controllers[f"{node}:controller"] = controller

        target = None
        target_slot = chain.get("targetBoneSlot") if isinstance(chain, dict) else None
        bones = chain.get("bones") if isinstance(chain, dict) else None
        if isinstance(bones, list) and isinstance(target_slot, int) and 0 <= target_slot < len(bones):
            target_data = bones[target_slot]
            if isinstance(target_data, dict):
                target = _resolve_bone_index(catalog, target_data.get("bone_index", target_data.get("boneIndex")))
                if target is None:
                    target = _resolve_transform(root, (target_data.get("name_en"), target_data.get("name")))
        if target is None and controller:
            target_index = _safe_attr(controller, "mmd_ik_target_index")
            target = _resolve_bone_index(catalog, target_index)
        if target:
            targets[f"{node}:target"] = target

        input_sources = []
        for attr in ("inputTranslate", "inputRotate"):
            input_sources.extend(_plug_sources(node, attr))
        output_destinations = []
        for attr in ("outputRotate", "outputTranslate"):
            output_destinations.extend(_plug_destinations(node, attr))
        rows.append(
            {
                "node": _long_node(node),
                "leaf": leaf,
                "type": _node_type(node),
                "enabled": _safe_attr(node, "enabled"),
                "mmdIkBoneName": _safe_attr(node, "mmd_ik_bone_name"),
                "chainJson": chain,
                "goalSources": goal_sources,
                "goalDestinations": {
                    attr: _plug_destinations(node, attr)
                    for attr in ("goalWorldMatrix", "goalX", "goalY", "goalZ")
                },
                "inputSources": sorted(set(input_sources)),
                "outputDestinations": sorted(set(output_destinations)),
                "controller": controller,
                "target": target,
                "connections": _connections(node),
            }
        )
    return rows, controllers, targets


def _handle_context(root: str, controllers: Mapping[str, str]) -> List[str]:
    handles = [str(value) for value in (cmds.ls(type="ikHandle", long=True) or [])]
    controller_values = {_norm(value) for value in controllers.values()}
    related: List[str] = []
    for handle in handles:
        attrs = [_safe_attr(handle, attr, "") for attr in ("mmd_ik_controller", "mmd_ik_native_handle")]
        attr_text = " ".join(str(value) for value in attrs if value)
        normalized = _norm(f"{_leaf(handle)} {attr_text}")
        if any(_norm(value) in normalized or normalized in _norm(value) for value in controllers.values()):
            related.append(handle)
            continue
        if any(token in normalized for token in ("left", "right", "leg", "foot", "ankle")):
            related.append(handle)
            continue
        if controller_values and any(value in normalized for value in controller_values):
            related.append(handle)
    return sorted(set(related))


def _ancestor_transforms(nodes: Iterable[str]) -> List[str]:
    result = set()
    for node in nodes:
        for row in _parent_chain(node):
            if row["type"] in {"transform", "joint"}:
                result.add(str(row["node"]))
    return sorted(result)


def _build_context(root: str) -> Dict[str, Any]:
    catalog = _joint_catalog(root)
    foot_joints = {
        "leftFootJoint": _select_foot(catalog, "left"),
        "rightFootJoint": _select_foot(catalog, "right"),
    }
    ik_nodes, controllers, targets = _ik_context(root, catalog)
    handles = _handle_context(root, controllers)

    tracked: Dict[str, str] = {"root": _long_node(root)}
    for label, node in foot_joints.items():
        if node:
            tracked[label] = node
    for key, node in controllers.items():
        tracked[key] = node
    for key, node in targets.items():
        tracked[key] = node
    for handle in handles:
        tracked[f"ikHandle:{handle}"] = handle
    related = _ancestor_transforms(tracked.values())
    for node in related:
        tracked.setdefault(f"parent:{node}", node)
    return {
        "root": _long_node(root),
        "jointCatalog": catalog,
        "footJoints": foot_joints,
        "ikNodes": ik_nodes,
        "controllers": controllers,
        "targets": targets,
        "handles": handles,
        "tracked": tracked,
    }


def _capture(context: Mapping[str, Any]) -> Dict[str, Any]:
    tracked = context["tracked"]
    rows = {
        str(role): _transform_row(str(node), str(role))
        for role, node in tracked.items()
        if cmds.objExists(str(node))
    }
    controller_rows = {
        str(key): rows.get(str(key))
        for key in context["controllers"]
        if rows.get(str(key)) is not None
    }
    target_rows = {
        str(key): rows.get(str(key))
        for key in context["targets"]
        if rows.get(str(key)) is not None
    }
    handle_rows = {
        str(handle): rows.get(f"ikHandle:{handle}")
        for handle in context["handles"]
        if rows.get(f"ikHandle:{handle}") is not None
    }
    parent_rows = {
        key.split(":", 1)[1]: row
        for key, row in rows.items()
        if key.startswith("parent:")
    }
    return {
        "root": rows.get("root"),
        "footJoints": {
            str(label): rows.get(str(label))
            for label in context["footJoints"]
            if context["footJoints"].get(label) and rows.get(str(label)) is not None
        },
        "controllers": controller_rows,
        "targets": target_rows,
        "ikHandles": handle_rows,
        "relatedGroups": parent_rows,
        "tracked": rows,
    }


def _translation_delta(before: Optional[Sequence[float]], after: Optional[Sequence[float]]) -> Optional[List[float]]:
    if before is None or after is None:
        return None
    return [float(after[index]) - float(before[index]) for index in range(3)]


def _diff(context: Mapping[str, Any], before: Mapping[str, Any], after: Mapping[str, Any]) -> Dict[str, Any]:
    root_before = before.get("root", {}).get("worldTranslation")
    root_after = after.get("root", {}).get("worldTranslation")
    actual_root_delta = _translation_delta(root_before, root_after)
    rows: List[Dict[str, Any]] = []
    before_rows = before.get("tracked", {})
    after_rows = after.get("tracked", {})
    for role in sorted(set(before_rows) | set(after_rows)):
        left = before_rows.get(role) or {}
        right = after_rows.get(role) or {}
        delta = _translation_delta(left.get("worldTranslation"), right.get("worldTranslation"))
        residual = _translation_delta(actual_root_delta, delta)
        rows.append(
            {
                "role": role,
                "node": right.get("node", left.get("node")),
                "type": right.get("type", left.get("type")),
                "before": left.get("worldTranslation"),
                "after": right.get("worldTranslation"),
                "translationDelta": delta,
                "rootDeltaResidual": residual,
                "parentBefore": left.get("parent"),
                "parentAfter": right.get("parent"),
                "inheritsTransformBefore": left.get("inheritsTransform"),
                "inheritsTransformAfter": right.get("inheritsTransform"),
                "incomingTranslateBefore": left.get("incomingTranslate", []),
                "incomingTranslateAfter": right.get("incomingTranslate", []),
                "incomingRotateBefore": left.get("incomingRotate", []),
                "incomingRotateAfter": right.get("incomingRotate", []),
                "parentChainBefore": left.get("parentChain", []),
                "parentChainAfter": right.get("parentChain", []),
            }
        )
    ranked = sorted(
        (row for row in rows if row.get("rootDeltaResidual") is not None),
        key=lambda row: -max(abs(float(value)) for value in row["rootDeltaResidual"]),
    )
    return {
        "requestedRootDelta": actual_root_delta,
        "rows": rows,
        "largestResiduals": ranked[:20],
        "changedParentChains": [
            row["role"]
            for row in rows
            if row.get("parentChainBefore") != row.get("parentChainAfter")
        ],
    }


def _run(args: argparse.Namespace) -> Dict[str, Any]:
    delta = _parse_delta(args.delta)
    pmx_path = _resolve(args.pmx)
    report: Dict[str, Any] = {
        "status": "error",
        "probe": "ROOT-MOVE-IK-TARGET-DIAGNOSTIC-1",
        "pmx": str(pmx_path),
        "requestedDelta": delta,
        "errors": [],
    }
    try:
        cmds.file(new=True, force=True)
        root = _import_model(pmx_path)
        context = _build_context(root)
        report["root"] = context["root"]
        report["jointCatalog"] = context["jointCatalog"]
        report["selectedFootJoints"] = context["footJoints"]
        report["ikNodes"] = context["ikNodes"]
        report["controllerNodes"] = context["controllers"]
        report["targetNodes"] = context["targets"]
        report["ikHandleNodes"] = context["handles"]
        report["before"] = _capture(context)
        if not all(context["footJoints"].values()):
            missing = [label for label, node in context["footJoints"].items() if not node]
            raise RuntimeError(f"Unable to resolve required foot joints: {missing}")
        cmds.xform(context["root"], relative=True, worldSpace=True, translation=delta)
        report["after"] = _capture(context)
        report["translationDeltas"] = _diff(context, report["before"], report["after"])
        parity_failures = []
        tolerance = float(args.tolerance)
        if tolerance < 0.0:
            raise ValueError("--tolerance must be non-negative")
        for row in report["translationDeltas"]["rows"]:
            role = str(row.get("role", ""))
            if not any(token in role.lower() for token in ("footjoint", ":target", ":controller")):
                continue
            residual = row.get("rootDeltaResidual")
            if residual is None:
                parity_failures.append(f"{role}: missing translation delta")
                continue
            maximum = max(abs(float(value)) for value in residual)
            if maximum > tolerance:
                parity_failures.append(f"{role}: residual {maximum} > {tolerance}")
        report["parityGate"] = {
            "enabled": bool(args.expect_root_parity),
            "passed": (not parity_failures) if args.expect_root_parity else None,
            "tolerance": tolerance,
            "failures": parity_failures,
        }
        report["diagnostic"] = {
            "rootMoveApplied": True,
            "sourceFixAttempted": False,
            "interpretation": (
                "Inspect largestResiduals and each row's parentChain/inheritsTransform. "
                "A residual near the requested root delta identifies a second transform; "
                "a residual near zero indicates expected root-following behavior."
            ),
        }
        if args.expect_root_parity and parity_failures:
            raise RuntimeError(f"root/IK parity gate failed: {parity_failures}")
        report["status"] = "pass"
    except Exception as exc:
        report["errors"].append(str(exc))
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
    report_path = _resolve(args.out)
    try:
        _load_plugin()
        report = _run(args)
    except Exception as exc:
        report = {
            "status": "error",
            "probe": "ROOT-MOVE-IK-TARGET-DIAGNOSTIC-1",
            "pmx": str(_resolve(args.pmx)),
            "errors": [str(exc)],
        }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    try:
        maya.standalone.uninitialize()
    except Exception:
        pass
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
