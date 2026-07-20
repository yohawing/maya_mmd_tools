"""Small reversible canonical-world arm stance transaction for HumanIK setup.

The implementation is the productionized, single-model form of the stance
helpers used by ``tests/viewport/humanik_roundtrip_smoke.py``.  It snapshots
the two arm joints, isolates only reviewed ``mute_for_hik`` writer edges, uses
each arm's current horizontal world direction as its target, and restores the
snapshot before reconnecting the exact original topology.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from mmd_tools.core.humanik_constraints import (
    classify_humanik_constraints,
    collect_humanik_constraint_facts,
)
from mmd_tools.core.humanik_preview import BLOCKING_CLASSIFICATIONS
from mmd_tools.core.humanik_resolver import HumanIkBoneAssignment


STANCE_ELEVATION_TOLERANCE = 1.0e-4
STANCE_DIRECTION_TOLERANCE = 1.0e-8
STANCE_RESTORE_TOLERANCE = 1.0e-6
STANCE_MAX_DIRECTION_ATTEMPTS = 3
REQUIRED_ARM_SLOTS = {"LeftArm": "LeftForeArm", "RightArm": "RightForeArm"}


def _maya_cmds():
    from maya import cmds

    return cmds


def _maya_open_maya():
    import maya.api.OpenMaya as om

    return om


def _length(vector: Sequence[float]) -> float:
    return math.sqrt(sum(float(value) ** 2 for value in vector))


def _unit(vector: Sequence[float]) -> Tuple[float, float, float]:
    length = _length(vector)
    if length <= 1.0e-12:
        raise RuntimeError("Cannot normalize a zero-length T-pose arm segment")
    return tuple(float(value) / length for value in vector)


def _subtract(left: Sequence[float], right: Sequence[float]) -> Tuple[float, float, float]:
    return tuple(float(a) - float(b) for a, b in zip(left, right))


def _matrix_values(value: Any) -> List[float]:
    if hasattr(value, "__len__") and len(value) == 1 and isinstance(value[0], (tuple, list)):
        value = value[0]
    return [float(value[index]) for index in range(16)]


def _matrix(cmds, plug: str) -> List[float]:
    return _matrix_values(cmds.getAttr(plug))


def joint_world_direction(cmds_module, joint: str, child: str) -> Tuple[float, float, float]:
    """Return the world-space vector from an arm joint to its child."""
    cmds = cmds_module or _maya_cmds()
    parent = _matrix(cmds, f"{joint}.worldMatrix[0]")
    child_matrix = _matrix(cmds, f"{child}.worldMatrix[0]")
    return (
        child_matrix[12] - parent[12],
        child_matrix[13] - parent[13],
        child_matrix[14] - parent[14],
    )


def direction_evidence(cmds_module, joint: str, child: str) -> Dict[str, Any]:
    """Return JSON-safe world direction and elevation evidence."""
    direction = joint_world_direction(cmds_module, joint, child)
    horizontal = math.sqrt(direction[0] ** 2 + direction[2] ** 2)
    elevation = math.atan2(direction[1], horizontal)
    return {
        "joint": str(joint),
        "child": str(child),
        "worldDirection": list(direction),
        "length": _length(direction),
        "elevationRadians": elevation,
        "elevationDegrees": math.degrees(elevation),
        "absoluteElevationRadians": abs(elevation),
        "absoluteElevationDegrees": abs(math.degrees(elevation)),
    }


def _shortest_arc(source: Sequence[float], target: Sequence[float]):
    """Return the Maya shortest-arc quaternion, including anti-parallel input."""
    om = _maya_open_maya()
    source_vector = om.MVector(*_unit(source))
    target_vector = om.MVector(*_unit(target))
    dot = max(-1.0, min(1.0, float(source_vector * target_vector)))
    axis = source_vector ^ target_vector
    if float(axis.length()) <= 1.0e-12:
        if dot >= 0.0:
            return om.MQuaternion()
        fallback = source_vector ^ om.MVector(0.0, 1.0, 0.0)
        if float(fallback.length()) <= 1.0e-12:
            fallback = source_vector ^ om.MVector(1.0, 0.0, 0.0)
        return om.MQuaternion(math.pi, fallback.normal())
    return om.MQuaternion(math.acos(dot), axis.normal())


def set_joint_world_direction(
    cmds_module,
    joint: str,
    child: str,
    target_direction: Sequence[float],
) -> Dict[str, Any]:
    """Set a joint with the S5c world-matrix shortest-arc setter."""
    cmds = cmds_module or _maya_cmds()
    om = _maya_open_maya()
    current_direction = joint_world_direction(cmds, joint, child)
    delta = _shortest_arc(current_direction, target_direction)
    current_world = om.MMatrix(cmds.getAttr(f"{joint}.worldMatrix[0]"))
    current_rotation = om.MTransformationMatrix(current_world).rotation(asQuaternion=True)
    desired_world = om.MTransformationMatrix(current_world)
    desired_world.setRotation(current_rotation * delta)
    desired_matrix = desired_world.asMatrix()
    cmds.xform(joint, worldSpace=True, matrix=_matrix_values(desired_matrix), preserve=False)
    try:
        cmds.refresh(force=True)
    except Exception:
        pass
    actual = _matrix(cmds, f"{joint}.worldMatrix[0]")
    desired_values = _matrix_values(desired_matrix)
    return {
        "method": "world-shortest-arc-with-world-matrix-jointOrient-preservation",
        "targetDirection": list(_unit(target_direction)),
        "deltaQuaternion": [float(delta.x), float(delta.y), float(delta.z), float(delta.w)],
        "resultingRotateDegrees": [float(value) for value in cmds.getAttr(f"{joint}.rotate")[0]],
        "worldMatrixResidual": max(
            (abs(left - right) for left, right in zip(desired_values, actual)),
            default=0.0,
        ),
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


def _long_name(cmds, node: str) -> str:
    values = cmds.ls(node, long=True) or []
    return str(values[0] if values else node)


def _skin_clusters(cmds, root: str) -> List[str]:
    clusters: List[str] = []
    shapes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
    for shape in shapes:
        for node in cmds.listHistory(shape, pruneDagObjects=True) or []:
            if cmds.nodeType(node) == "skinCluster" and node not in clusters:
                clusters.append(str(node))
    return sorted(clusters)


def _skin_influence(cmds, root: str, joint: str) -> Optional[Tuple[str, int]]:
    """Return the first S5c-style skinCluster/index pair for one joint."""
    for skin in _skin_clusters(cmds, root):
        for logical_index in cmds.getAttr(f"{skin}.matrix", multiIndices=True) or []:
            sources = cmds.listConnections(
                f"{skin}.matrix[{logical_index}]",
                source=True,
                destination=False,
                plugs=True,
            ) or []
            if sources and _long_name(cmds, str(sources[0]).rsplit(".", 1)[0]) == str(joint):
                return str(skin), int(logical_index)
    return None


def _skin_product(cmds, joint: str, skin: str, logical_index: int) -> List[float]:
    bind_pre = _matrix(cmds, f"{skin}.bindPreMatrix[{logical_index}]")
    world = _matrix(cmds, f"{joint}.worldMatrix[0]")
    om = _maya_open_maya()
    return _matrix_values(om.MMatrix(bind_pre) * om.MMatrix(world))


def _matrix_residual(left: Sequence[float], right: Sequence[float]) -> float:
    return max((abs(float(a) - float(b)) for a, b in zip(left, right)), default=0.0)


def _attribute_vector(cmds, plug: str) -> List[float]:
    value = cmds.getAttr(plug)
    while isinstance(value, (tuple, list)) and len(value) == 1 and isinstance(value[0], (tuple, list)):
        value = value[0]
    return [float(item) for item in (value or ())]


def canonical_stance_targets(assignments: Iterable[HumanIkBoneAssignment]) -> Dict[str, Any]:
    """Describe required arm slots and the source-derived horizontal strategy."""
    by_hik = {str(item.hik_bone): str(item.joint) for item in assignments}
    missing = [
        slot
        for slot, child in REQUIRED_ARM_SLOTS.items()
        if slot not in by_hik or child not in by_hik
    ]
    return {
        "mode": "automatic-horizontal-world-t-pose",
        "upAxis": "Y",
        "directionStrategy": "current-world-direction-horizontal-projection",
        "requiredSlots": [item for slot, child in REQUIRED_ARM_SLOTS.items() for item in (slot, child)],
        "targets": {
            slot: {"joint": by_hik[slot], "child": by_hik[child]}
            for slot, child in REQUIRED_ARM_SLOTS.items()
            if slot in by_hik and child in by_hik
        },
        "missingSlots": missing,
        "ready": not missing,
    }


def _stance_joint_map(cmds, assignments: Iterable[HumanIkBoneAssignment]) -> Dict[str, Tuple[str, str]]:
    by_hik = {str(item.hik_bone): _long_name(cmds, str(item.joint)) for item in assignments}
    missing = [slot for slot, child in REQUIRED_ARM_SLOTS.items() if slot not in by_hik or child not in by_hik]
    if missing:
        raise RuntimeError(f"T-pose requires characterized HIK slots: {', '.join(missing)}")
    return {slot: (by_hik[slot], by_hik[child]) for slot, child in REQUIRED_ARM_SLOTS.items()}


def _incoming_sources(cmds, destination: str) -> List[str]:
    return sorted({str(value) for value in (cmds.listConnections(destination, source=True, destination=False, plugs=True) or [])})


def _edge_connected(cmds, source: str, destination: str) -> bool:
    try:
        return bool(cmds.isConnected(source, destination))
    except Exception:
        return source in _incoming_sources(cmds, destination)


@dataclass
class HumanIkStanceTransaction:
    """One model-scoped automatic horizontal arm stance transaction."""

    model_root: str
    assignments: Tuple[HumanIkBoneAssignment, ...] = ()
    ownership_report: Optional[Dict[str, Any]] = None
    cmds_module: Any = field(default=None, repr=False, compare=False)
    mel_module: Any = field(default=None, repr=False, compare=False)
    ownership_id: str = "mmd-tools:automatic-stance"
    world_matrix_setter: Optional[Callable[..., Dict[str, Any]]] = field(default=None, repr=False, compare=False)
    modified_joints: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    skin_evidence: Dict[str, Any] = field(default_factory=dict)
    ownership_journal: Dict[str, Any] = field(default_factory=dict)
    stance_evidence: Dict[str, Any] = field(default_factory=dict)
    active: bool = False
    prepared: bool = False
    character: Optional[str] = None

    def __post_init__(self):
        self.model_root = str(self.model_root)
        self.assignments = tuple(self.assignments)

    @property
    def cmds(self):
        return self.cmds_module or _maya_cmds()

    def prepare(self) -> "HumanIkStanceTransaction":
        """Capture stance, skin, and exact ownership state without mutation."""
        if self.prepared:
            return self
        cmds = self.cmds
        report = self.ownership_report or classify_humanik_constraints(
            collect_humanik_constraint_facts(cmds_module=cmds),
            tuple(str(item.joint) for item in self.assignments),
        )
        blockers = [row for row in report.get("rows", []) if row.get("classification") in BLOCKING_CLASSIFICATIONS]
        if blockers:
            labels = ", ".join(f"{row.get('node')}:{row.get('classification')}" for row in blockers)
            raise RuntimeError(f"Canonical T-pose ownership blocked: {labels}")
        chains = _stance_joint_map(cmds, self.assignments)
        modified = {}
        for slot, (joint, child) in chains.items():
            skin_info = _skin_influence(cmds, self.model_root, joint)
            skin = str(skin_info[0]) if skin_info else None
            logical_index = int(skin_info[1]) if skin_info else None
            modified[joint] = {
                "hikBone": slot,
                "child": child,
                "rotate": _attribute_vector(cmds, f"{joint}.rotate"),
                "jointOrient": _attribute_vector(cmds, f"{joint}.jointOrient"),
                "skin": skin,
                "logicalIndex": logical_index,
                "skinProduct": _skin_product(cmds, joint, skin, logical_index) if skin else None,
            }
        self.modified_joints = modified
        skin_rows = []
        for assignment in self.assignments:
            joint = _long_name(cmds, str(assignment.joint))
            info = _skin_influence(cmds, self.model_root, joint)
            if info is None:
                continue
            skin, logical_index = info
            skin_rows.append(
                {
                    "hikBone": str(assignment.hik_bone),
                    "joint": joint,
                    "skin": skin,
                    "logicalIndex": int(logical_index),
                    "skinProduct": _skin_product(cmds, joint, skin, logical_index),
                }
            )
        self.skin_evidence = {
            "matrixOrder": "bindPreMatrix[index] * joint.worldMatrix[0]",
            "coverageAvailable": bool(skin_rows),
            "skinVerificationStatus": "available" if skin_rows else "not_available",
            "verifiedCount": 0,
            "rows": skin_rows,
        }
        self.ownership_report = report
        self.ownership_journal = self._capture_ownership_journal(report)
        self.stance_evidence = {
            "mode": "automatic-horizontal-world-t-pose",
            "upAxis": "Y",
            "directionStrategy": "current-world-direction-horizontal-projection",
            "elevationToleranceRadians": STANCE_ELEVATION_TOLERANCE,
            "directionTolerance": STANCE_DIRECTION_TOLERANCE,
            "restoreTolerance": STANCE_RESTORE_TOLERANCE,
            "targets": {
                slot: {"joint": joint, "child": child, "targetDirection": None}
                for slot, (joint, child) in chains.items()
            },
            "pose": None,
            "restore": None,
        }
        for slot, (joint, child) in chains.items():
            direction = joint_world_direction(cmds, joint, child)
            horizontal = (direction[0], 0.0, direction[2])
            self.stance_evidence["targets"][slot]["targetDirection"] = list(_unit(horizontal))
        self.prepared = True
        return self

    def enter(self) -> "HumanIkStanceTransaction":
        """Disconnect reviewed edges and apply each arm's horizontal target."""
        self.prepare()
        if self.active:
            return self
        self.active = True
        disconnected = []
        try:
            for edge in self.ownership_journal.get("edges", []):
                if not _edge_connected(self.cmds, edge["source"], edge["destination"]):
                    raise RuntimeError(f"Writer edge disappeared before isolation: {edge['source']} -> {edge['destination']}")
                self.cmds.disconnectAttr(edge["source"], edge["destination"])
                disconnected.append(edge)
            self._verify_isolated_topology()
            rows = []
            for slot, target in self.stance_evidence["targets"].items():
                joint, child = target["joint"], target["child"]
                target_direction = target["targetDirection"]
                attempts = []
                for attempt in range(1, STANCE_MAX_DIRECTION_ATTEMPTS + 1):
                    apply_row = (
                        self.world_matrix_setter(joint, child, target_direction)
                        if self.world_matrix_setter is not None
                        else set_joint_world_direction(self.cmds, joint, child, target_direction)
                    )
                    direction = direction_evidence(self.cmds, joint, child)
                    residual = _length(_subtract(_unit(direction["worldDirection"]), _unit(target_direction)))
                    passed = (
                        direction["absoluteElevationRadians"] <= STANCE_ELEVATION_TOLERANCE
                        and residual <= STANCE_DIRECTION_TOLERANCE
                    )
                    attempts.append(
                        {
                            "attempt": attempt,
                            "apply": apply_row,
                            "direction": direction,
                            "directionResidual": residual,
                            "elevationRadians": direction["absoluteElevationRadians"],
                            "passed": passed,
                        }
                    )
                    if passed:
                        break
                final = attempts[-1]
                rows.append(
                    {
                        "hikBone": slot,
                        "joint": joint,
                        "child": child,
                        "targetDirection": list(target_direction),
                        "apply": final["apply"],
                        "direction": final["direction"],
                        "directionResidual": final["directionResidual"],
                        "passed": final["passed"],
                        "attempts": attempts,
                        "attemptCount": len(attempts),
                        "finalApply": final["apply"],
                        "finalDirection": final["direction"],
                        "finalDirectionResidual": final["directionResidual"],
                        "finalElevationRadians": final["elevationRadians"],
                        "tolerances": {
                            "direction": STANCE_DIRECTION_TOLERANCE,
                            "elevation": STANCE_ELEVATION_TOLERANCE,
                        },
                    }
                )
            self.stance_evidence["pose"] = {"rows": rows, "passed": all(row["passed"] for row in rows)}
            if not self.stance_evidence["pose"]["passed"]:
                failing = next(row for row in rows if not row["passed"])
                raise RuntimeError(
                    "Canonical T-pose direction residual exceeds tolerance: "
                    f"hikBone={failing['hikBone']}, "
                    f"directionResidual={failing['finalDirectionResidual']} "
                    f"(tolerance={STANCE_DIRECTION_TOLERANCE}), "
                    f"elevationRadians={failing['finalElevationRadians']} "
                    f"(tolerance={STANCE_ELEVATION_TOLERANCE}), "
                    f"attempts={failing['attemptCount']}"
                )
            self.ownership_journal["topologyIsolated"] = True
            self.ownership_journal["disconnectedEdges"] = disconnected
            return self
        except Exception as error:
            try:
                self.restore()
            except Exception as rollback_error:
                raise RuntimeError(f"Canonical T-pose enter failed and rollback failed: {rollback_error}") from error
            raise

    def attach_character(self, character: str) -> None:
        """Record the created character without changing its post-lock state."""
        self.character = str(character)

    def restore(self) -> Dict[str, Any]:
        """Restore pose/JO/skin while isolated, then reconnect exact topology."""
        if not self.active:
            result = dict(self.stance_evidence.get("restore") or {"passed": True})
            result.setdefault("idempotent", True)
            return result
        try:
            for joint, info in self.modified_joints.items():
                self.cmds.setAttr(f"{joint}.rotate", *info["rotate"], type="double3")
            try:
                self.cmds.refresh(force=True)
            except Exception:
                pass
            rows = []
            for joint, info in self.modified_joints.items():
                rotate_residual = _matrix_residual(_attribute_vector(self.cmds, f"{joint}.rotate"), info["rotate"])
                joint_orient_residual = _matrix_residual(
                    _attribute_vector(self.cmds, f"{joint}.jointOrient"), info["jointOrient"]
                )
                skin_residual = 0.0
                if info["skinProduct"] is not None:
                    skin_residual = _matrix_residual(
                        info["skinProduct"],
                        _skin_product(self.cmds, joint, info["skin"], int(info["logicalIndex"])),
                    )
                rows.append(
                    {
                        "joint": joint,
                        "rotateResidual": rotate_residual,
                        "jointOrientResidual": joint_orient_residual,
                        "skinMatrixResidual": skin_residual,
                        "passed": max(rotate_residual, joint_orient_residual, skin_residual)
                        <= STANCE_RESTORE_TOLERANCE,
                    }
                )
            all_skin_rows = []
            for evidence in self.skin_evidence.get("rows", []):
                residual = _matrix_residual(
                    evidence["skinProduct"],
                    _skin_product(self.cmds, evidence["joint"], evidence["skin"], int(evidence["logicalIndex"])),
                )
                all_skin_rows.append({**evidence, "skinMatrixResidual": residual})
            self.skin_evidence["verifiedCount"] = len(all_skin_rows)
            self.skin_evidence["skinVerificationStatus"] = "verified" if all_skin_rows else "not_available"
            restore = {
                "rows": rows,
                "allSkinRows": all_skin_rows,
                "maxRotateResidual": max((row["rotateResidual"] for row in rows), default=0.0),
                "maxJointOrientResidual": max((row["jointOrientResidual"] for row in rows), default=0.0),
                "maxSkinMatrixResidual": max((row["skinMatrixResidual"] for row in rows), default=0.0),
                "maxAllSkinMatrixResidual": max((row["skinMatrixResidual"] for row in all_skin_rows), default=0.0),
                "allSkinInfluenceCount": len(all_skin_rows),
                "topologyRestored": False,
                "skinVerified": bool(all_skin_rows),
                "passed": all(row["passed"] for row in rows)
                and all(row["skinMatrixResidual"] <= STANCE_RESTORE_TOLERANCE for row in all_skin_rows),
                "tolerance": STANCE_RESTORE_TOLERANCE,
            }
            self.stance_evidence["restore"] = restore
            if not restore["passed"]:
                # Even a residual failure must not strand reviewed writer
                # edges disconnected.  Reconnect is attempted before the
                # failure is surfaced; a topology failure remains retryable.
                try:
                    self._restore_topology()
                    restore["topologyRestored"] = True
                except Exception as reconnect_error:
                    restore["reconnectError"] = str(reconnect_error)
                raise RuntimeError("Canonical T-pose restore residual exceeds tolerance")
            self._restore_topology()
            restore["topologyRestored"] = True
            self.active = False
            return restore
        except Exception as error:
            failure = dict(self.stance_evidence.get("restore") or {})
            failure.update({"passed": False, "error": str(error), "topologyRestored": bool(self.ownership_journal.get("topologyRestored", False))})
            self.stance_evidence["restore"] = failure
            self.active = True
            raise

    def to_dict(self) -> Dict[str, Any]:
        """Return JSON-safe transaction diagnostics."""
        return {
            "modelRoot": self.model_root,
            "character": self.character,
            "modifiedJoints": _json_value(self.modified_joints),
            "skinEvidence": _json_value(self.skin_evidence),
            "ownershipJournal": _json_value(self.ownership_journal),
            "stanceEvidence": _json_value(self.stance_evidence),
            "active": bool(self.active),
            "prepared": bool(self.prepared),
        }

    def _capture_ownership_journal(self, report: Mapping[str, Any]) -> Dict[str, Any]:
        edges = []
        seen = set()
        for row in report.get("rows", []):
            if row.get("classification") != "mute_for_hik":
                continue
            for destination in row.get("writes", []):
                baseline = _incoming_sources(self.cmds, str(destination))
                matched = [source for source in baseline if source.rsplit(".", 1)[0] == str(row.get("node", ""))]
                if len(matched) > 1:
                    raise RuntimeError(f"Ambiguous HIK writer edges for {row.get('node')}: {destination}")
                if not matched:
                    raise RuntimeError(f"Missing HIK writer edge for {row.get('node')}: {destination}")
                edge = (matched[0], str(destination))
                if edge in seen:
                    continue
                seen.add(edge)
                edges.append(
                    {
                        "source": edge[0],
                        "destination": edge[1],
                        "node": str(row.get("node", "")),
                        "nodeType": str(row.get("nodeType", "")),
                        "baselineIncomingSources": baseline,
                    }
                )
        return {
            "classificationCounts": dict(report.get("counts", {})),
            "rows": list(report.get("rows", [])),
            "edges": sorted(edges, key=lambda item: (item["destination"], item["source"])),
            "topologyIsolated": False,
            "topologyRestored": False,
            "topologyMismatches": [],
        }

    def _verify_isolated_topology(self):
        mismatches = []
        for destination in sorted({edge["destination"] for edge in self.ownership_journal.get("edges", [])}):
            destination_edges = [edge for edge in self.ownership_journal["edges"] if edge["destination"] == destination]
            baseline = list(destination_edges[0]["baselineIncomingSources"])
            isolated = {edge["source"] for edge in destination_edges}
            actual = _incoming_sources(self.cmds, destination)
            expected = sorted(source for source in baseline if source not in isolated)
            if actual != expected:
                mismatches.append({"destination": destination, "expected": expected, "actual": actual})
        self.ownership_journal["topologyMismatches"] = mismatches
        if mismatches:
            raise RuntimeError("Canonical T-pose writer isolation topology verification failed")

    def _restore_topology(self):
        for edge in self.ownership_journal.get("edges", []):
            destination = edge["destination"]
            baseline = list(edge["baselineIncomingSources"])
            actual = _incoming_sources(self.cmds, destination)
            unexpected = [source for source in actual if source not in baseline]
            if unexpected:
                raise RuntimeError(f"Canonical T-pose topology has third-party writers: {destination}: {unexpected}")
            if not _edge_connected(self.cmds, edge["source"], destination):
                self.cmds.connectAttr(edge["source"], destination, force=False)
            if _incoming_sources(self.cmds, destination) != sorted(baseline):
                raise RuntimeError(f"Canonical T-pose topology restore failed: {destination}")
        self.ownership_journal["topologyRestored"] = True


__all__ = [
    "REQUIRED_ARM_SLOTS",
    "STANCE_DIRECTION_TOLERANCE",
    "STANCE_ELEVATION_TOLERANCE",
    "STANCE_MAX_DIRECTION_ATTEMPTS",
    "STANCE_RESTORE_TOLERANCE",
    "HumanIkStanceTransaction",
    "canonical_stance_targets",
    "direction_evidence",
    "joint_world_direction",
    "set_joint_world_direction",
]
