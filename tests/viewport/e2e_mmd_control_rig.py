"""Maya GUI commandPort E2E gate for the MMD-native control rig.

The Maya-side check imports the checked-in PMX/VMD fixture, creates the
detached control rig, enters EDIT, moves representative left/right foot and toe
IK controllers, checks each owned ``mmdCcdIk`` response and cycle state, toggles ``ikEnabled``,
bakes back to MMD inputs, saves/reopens, and performs a VMD export/re-import
round-trip.  The host side always launches a fresh Maya process and refuses to
use an already-open commandPort.

Usage::

    python tests/viewport/e2e_mmd_control_rig.py --maya 2024
    python tests/viewport/e2e_mmd_control_rig.py --maya 2026 --port 7734
    python tests/viewport/e2e_mmd_control_rig.py --maya 2024 --create-on-import
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import math
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Iterable, Mapping

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.viewport.maya_e2e_harness import run_maya_e2e

COMMAND_PORT = 7734
COMPLETION_MARKER = "//-- MMD_CONTROL_RIG_E2E_DONE --//"
TEST_TIMEOUT = 600
MOVE_EPSILON = 1.0e-5
ROUNDTRIP_MATRIX_EPSILON = 5.0e-3
ROUNDTRIP_FRAMES = tuple(range(0, 6))
EVALUATION_MODE_CHOICES = ("default", "dg", "serial", "parallel")
_EVALUATION_MODE_TO_MAYA = {"dg": "off", "serial": "serial", "parallel": "parallel"}
IK_MOVE_CASES = (
    ("left_foot_ik", 0.35),
    ("right_foot_ik", 0.35),
    ("left_toe_ik", 0.35),
    ("right_toe_ik", 0.35),
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _flatten_numeric(value: Any) -> list[float]:
    """Flatten Maya numeric wrappers into a JSON-safe float list."""

    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return []
    if isinstance(value, Iterable):
        result: list[float] = []
        for item in value:
            result.extend(_flatten_numeric(item))
        return result
    try:
        return [float(value)]
    except (TypeError, ValueError):
        return []


def _distance(left: Iterable[float], right: Iterable[float]) -> float:
    values = list(zip(left, right))
    return math.sqrt(sum((a - b) ** 2 for a, b in values))


def _matrix(node: str, cmds) -> list[float]:
    return _flatten_numeric(cmds.xform(node, query=True, worldSpace=True, matrix=True))


def _world_translation(node: str, cmds) -> list[float]:
    return _flatten_numeric(
        cmds.xform(node, query=True, worldSpace=True, translation=True)
    )


def _find_joint_for_mmd_name(name: str, cmds) -> str | None:
    """Resolve a PMX bone-name metadata value to its Maya joint."""

    for joint in cmds.ls(type="joint", long=True) or []:
        try:
            if not cmds.attributeQuery("mmd_bone_name", node=joint, exists=True):
                continue
            if str(cmds.getAttr(f"{joint}.mmd_bone_name")) == str(name):
                return str(joint)
        except RuntimeError:
            continue
    return None


def _cycle_state(label: str, cmds) -> dict[str, Any]:
    """Capture Maya's cycleCheck output without mutating its enable state."""

    evaluation_on = bool(cmds.cycleCheck(query=True, evaluation=True))
    plugs = sorted(str(item) for item in (cmds.cycleCheck(all=True, list=True) or []))
    return {"label": label, "evaluationOn": evaluation_on, "cyclePlugs": plugs}


def _evaluation_mode_snapshot(requested: str, cmds) -> dict[str, str]:
    """Apply and report the requested Maya evaluation mode.

    Maya exposes DG evaluation as ``off``; the report keeps the user-facing
    ``dg`` spelling while retaining the raw Maya mode for diagnostics.
    ``default`` intentionally leaves the current Maya mode untouched.
    """

    requested = str(requested or "default").lower()
    if requested not in EVALUATION_MODE_CHOICES:
        raise ValueError(f"unsupported evaluation mode: {requested}")
    target = _EVALUATION_MODE_TO_MAYA.get(requested)
    if target is not None:
        cmds.evaluationManager(mode=target)
    raw = cmds.evaluationManager(query=True, mode=True) or []
    maya_mode = str(raw[0]) if raw else "unknown"
    active = {"off": "dg"}.get(maya_mode, maya_mode)
    if target is not None and maya_mode != target:
        raise RuntimeError(
            f"requested evaluation mode {requested!r}, Maya reported {maya_mode!r}"
        )
    return {"requested": requested, "active": active, "mayaMode": maya_mode}


def _joint_worlds(cmds, frames: Iterable[int]) -> dict[str, dict[str, list[float]]]:
    """Capture indexed PMX joint world matrices for stable round-trip comparison."""

    indexed: dict[str, str] = {}
    for joint in cmds.ls(type="joint", long=True) or []:
        try:
            if not cmds.attributeQuery("mmd_bone_index", node=joint, exists=True):
                continue
            index = cmds.getAttr(f"{joint}.mmd_bone_index")
            indexed[str(int(index))] = str(joint)
        except (TypeError, ValueError, RuntimeError):
            continue

    result: dict[str, dict[str, list[float]]] = {}
    for frame in frames:
        cmds.currentTime(frame, edit=True)
        cmds.refresh(force=True)
        result[str(frame)] = {
            index: _matrix(joint, cmds)
            for index, joint in sorted(indexed.items())
            if cmds.objExists(joint)
        }
    return result


def _ik_states(cmds, frames: Iterable[int]) -> dict[str, dict[str, bool | None]]:
    """Capture enabled state of all mmdCcdIk solvers by PMX IK name."""

    nodes = [str(node) for node in (cmds.ls(type="mmdCcdIk", long=True) or [])]
    names: dict[str, str] = {}
    for node in nodes:
        try:
            name = (
                cmds.getAttr(f"{node}.mmd_ik_bone_name")
                if cmds.attributeQuery("mmd_ik_bone_name", node=node, exists=True)
                else node
            )
        except RuntimeError:
            name = node
        names[str(name)] = node

    result: dict[str, dict[str, bool | None]] = {}
    for frame in frames:
        cmds.currentTime(frame, edit=True)
        cmds.refresh(force=True)
        result[str(frame)] = {}
        for name, node in sorted(names.items()):
            try:
                enabled = bool(cmds.getAttr(f"{node}.enabled"))
            except RuntimeError:
                enabled = None
            result[str(frame)][name] = enabled
    return result


def _solver_owned_joint_indices(cmds) -> dict[str, dict[str, Any]]:
    """Resolve joints driven by native ``mmdCcdIk`` output plugs.

    These links are expected to be re-solved when a VMD target is quantized
    to its float32 representation.  Keep them visible in the report, but do
    not treat their numerical drift as authored-channel parity failure.
    """

    owned: dict[str, dict[str, Any]] = {}
    for solver in cmds.ls(type="mmdCcdIk", long=True) or []:
        solver_name = str(solver)
        try:
            ik_name = (
                cmds.getAttr(f"{solver}.mmd_ik_bone_name")
                if cmds.attributeQuery("mmd_ik_bone_name", node=solver, exists=True)
                else solver_name
            )
        except RuntimeError:
            ik_name = solver_name
        for slot in range(64):
            destinations = cmds.listConnections(
                f"{solver}.outputRotate[{slot}]",
                source=False,
                destination=True,
                type="joint",
            ) or []
            for destination in destinations:
                joints = cmds.ls(destination, long=True) or [destination]
                joint = str(joints[0])
                try:
                    if not cmds.attributeQuery("mmd_bone_index", node=joint, exists=True):
                        continue
                    index = str(int(cmds.getAttr(f"{joint}.mmd_bone_index")))
                except (TypeError, ValueError, RuntimeError):
                    continue
                owned[index] = {
                    "joint": joint,
                    "solver": solver_name,
                    "ikBoneName": str(ik_name),
                    "outputSlot": slot,
                }
    return owned


def _expand_solver_owned_joint_indices(
    direct_owned: Mapping[str, Mapping[str, Any]],
    cmds,
) -> dict[str, dict[str, Any]]:
    """Include descendants whose world matrices inherit a solver-owned link."""

    dependency_by_index: dict[str, set[str]] = {}
    for joint in cmds.ls(type="joint", long=True) or []:
        try:
            if not cmds.attributeQuery("mmd_bone_index", node=joint, exists=True):
                continue
            index = str(int(cmds.getAttr(f"{joint}.mmd_bone_index")))
            dependencies: set[str] = set()
            # A rig may insert non-joint transforms between two PMX bones.
            # Walk the DAG until the nearest indexed joint instead of assuming
            # the immediate parent carries ``mmd_bone_index``.
            parents = cmds.listRelatives(joint, parent=True, fullPath=True) or []
            while parents:
                parent = str(parents[0])
                if cmds.attributeQuery("mmd_bone_index", node=parent, exists=True):
                    dependencies.add(
                        str(int(cmds.getAttr(f"{parent}.mmd_bone_index")))
                    )
                    break
                parents = cmds.listRelatives(
                    parent, parent=True, fullPath=True
                ) or []
            # Append/grant bones can be siblings in the DAG while still
            # inheriting a solver-owned source rotation.  The importer keeps
            # this PMX relationship as metadata on the destination joint.
            if cmds.attributeQuery("mmd_grant_parent_index", node=joint, exists=True):
                grant_parent = int(cmds.getAttr(f"{joint}.mmd_grant_parent_index"))
                if grant_parent >= 0:
                    dependencies.add(str(grant_parent))
            dependency_by_index[index] = dependencies
        except (TypeError, ValueError, RuntimeError):
            continue

    effective = {str(index): dict(value) for index, value in direct_owned.items()}
    for index in dependency_by_index:
        # Preserve direct output metadata when a direct solver link also has
        # a solver-owned ancestor.
        if index in direct_owned:
            continue
        pending = list(dependency_by_index[index])
        visited: set[str] = set()
        while pending:
            ancestor = pending.pop()
            if ancestor in visited:
                continue
            visited.add(ancestor)
            if ancestor in direct_owned:
                effective[index] = {
                    **direct_owned[ancestor],
                    "propagatedFrom": ancestor,
                }
                break
            pending.extend(dependency_by_index.get(ancestor, ()))
    return effective


def _matrix_error_summary(
    locations: Iterable[Mapping[str, Any]],
    *,
    solver_owned_indices: set[str],
) -> dict[str, Any]:
    """Summarize matrix errors while preserving the exact worst entry."""

    locations = list(locations)
    non_solver = [
        item for item in locations if str(item["jointIndex"]) not in solver_owned_indices
    ]
    solver = [
        item for item in locations if str(item["jointIndex"]) in solver_owned_indices
    ]

    def _summary(items: list[Mapping[str, Any]]) -> dict[str, Any]:
        by_frame = {
            str(frame): max(
                (
                    float(item["error"])
                    for item in items
                    if int(item["frame"]) == int(frame)
                ),
                default=0.0,
            )
            for frame in sorted({int(item["frame"]) for item in items})
        }
        return {
            "maxWorldMatrixError": max(
                (float(item["error"]) for item in items),
                default=0.0,
            ),
            "maxWorldMatrixErrorByFrame": by_frame,
            "worstEntry": max(
                items,
                key=lambda item: float(item["error"]),
                default=None,
            ),
            "jointCount": len({str(item["jointIndex"]) for item in items}),
        }

    return {"nonSolverOwned": _summary(non_solver), "solverOwned": _summary(solver)}


def _resolve_ik_solver(
    metadata: Mapping[str, Any], role: str, cmds
) -> tuple[str, str]:
    """Return one role's solver and an output-driven effector joint."""

    binding = metadata.get("bindings", {}).get(role, {})
    solvers = [str(value) for value in binding.get("ikSolvers", []) if value]
    if not solvers:
        raise RuntimeError(f"{role} binding has no mmdCcdIk solver")
    solver = solvers[0]
    if not cmds.objExists(solver):
        matches = cmds.ls(solver, long=True) or []
        if len(matches) == 1:
            solver = str(matches[0])
    if not cmds.objExists(solver):
        raise RuntimeError(f"{role} solver is missing: {solver}")

    destinations: list[str] = []
    for index in range(32):
        for value in (
            cmds.listConnections(
                f"{solver}.outputRotate[{index}]",
                source=False,
                destination=True,
                type="joint",
            )
            or []
        ):
            long_name = cmds.ls(value, long=True) or [value]
            destinations.append(str(long_name[0]))
    if destinations:
        return solver, sorted(set(destinations))[-1]

    fallback = str(binding.get("joint", ""))
    if fallback and cmds.objExists(fallback):
        return solver, fallback
    matches = cmds.ls(fallback, long=True) or []
    if len(matches) == 1:
        return solver, str(matches[0])
    raise RuntimeError(f"{role} solver has no output-driven effector: {solver}")


def _solver_link_joints(solver: str, index: int, cmds) -> list[str]:
    """Return output-driven IK link joints for one solver output slot."""

    joints = []
    for value in (
        cmds.listConnections(
            f"{solver}.outputRotate[{index}]",
            source=False,
            destination=True,
            type="joint",
        )
        or []
    ):
        long_names = cmds.ls(value, long=True) or [value]
        joints.extend(str(item) for item in long_names if cmds.objExists(str(item)))
    return sorted(set(joints))


def _nonzero_sentinel_value(before: float, offset: float) -> float:
    """Choose a non-zero target while retaining a meaningful movement delta."""

    expected = before + offset
    if abs(expected) <= MOVE_EPSILON:
        expected = before - offset
    if abs(expected) <= MOVE_EPSILON:
        expected = offset
    return expected


def _author_control_sentinel(control: str, attribute: str, frame: int, offset: float, cmds) -> dict[str, Any]:
    """Author and verify a non-zero control value through its writable route."""

    if not cmds.objExists(control):
        raise RuntimeError(f"control is missing: {control}")
    if not cmds.attributeQuery(attribute, node=control, exists=True):
        raise RuntimeError(f"control attribute is missing: {control}.{attribute}")
    before = float(cmds.getAttr(f"{control}.{attribute}", time=frame))
    if not math.isfinite(before):
        raise RuntimeError(f"control value is not finite: {control}.{attribute}")
    expected = _nonzero_sentinel_value(before, float(offset))
    source_plugs = [
        str(value)
        for value in (
            cmds.listConnections(
                f"{control}.{attribute}",
                source=True,
                destination=False,
                plugs=True,
            )
            or []
        )
    ]
    source_node = None
    if len(source_plugs) > 1:
        raise RuntimeError(
            f"control input route is ambiguous: {control}.{attribute}: {source_plugs}"
        )
    if source_plugs:
        source_node = source_plugs[0].split(".", 1)[0]
        source_type = str(cmds.nodeType(source_node))
        if not source_type.startswith("animCurve"):
            raise RuntimeError(
                f"control input route is not writable: {source_plugs[0]} ({source_type})"
            )
        cmds.setKeyframe(source_node, time=frame, value=expected)
    else:
        source_type = None
        cmds.setKeyframe(control, attribute=attribute, time=frame, value=expected)
    cmds.dgdirty(allPlugs=True)
    cmds.dgdirty(control)
    cmds.refresh(force=True)
    after = float(cmds.getAttr(f"{control}.{attribute}", time=frame))
    if not math.isfinite(after):
        raise RuntimeError(f"authored control value is not finite: {control}.{attribute}")
    delta = abs(after - before)
    if (
        abs(after) <= MOVE_EPSILON
        or delta <= MOVE_EPSILON
        or abs(after - expected) > MOVE_EPSILON
    ):
        raise RuntimeError(
            f"control input route did not evaluate authored sentinel: "
            f"{control}.{attribute} before={before} expected={expected} after={after}"
        )
    return {
        "plug": f"{control}.{attribute}",
        "sourcePlugs": source_plugs,
        "sourceNode": source_node,
        "sourceType": source_type,
        "before": before,
        "expected": expected,
        "after": after,
        "delta": delta,
        "pass": True,
    }


def _ik_move_witness_pass(
    *,
    control_route_pass: bool,
    control_delta: float,
    target_delta: float,
    link_deltas: Mapping[str, float],
) -> bool:
    """Require a writable authored input and evaluated solver/link response."""

    return bool(
        control_route_pass
        and control_delta > MOVE_EPSILON
        and target_delta > MOVE_EPSILON
        and link_deltas
        and all(delta > MOVE_EPSILON for delta in link_deltas.values())
    )


def _focused_witnesses_pass(report: Mapping[str, Any]) -> bool:
    """Keep export parity from masking a failed focused Control Rig witness."""

    return all(
        bool(report.get(name, {}).get("pass"))
        for name in ("ikMove", "ikToggle", "autoBakeExport")
    )


def _solver_snapshot(solver: str, effector: str, cmds) -> dict[str, Any]:
    """Capture solver goal/output and the selected effector world matrix."""

    chain = {}
    try:
        raw_chain = cmds.getAttr(f"{solver}.chainJson")
        chain = json.loads(raw_chain) if raw_chain else {}
    except (TypeError, ValueError, RuntimeError):
        chain = {}
    links = chain.get("links", []) if isinstance(chain, dict) else []
    count = max(1, len(links))
    outputs = {}
    link_world_matrices = {}
    for index in range(count):
        try:
            outputs[str(index)] = _flatten_numeric(
                cmds.getAttr(f"{solver}.outputRotate[{index}]")
            )
        except RuntimeError:
            outputs[str(index)] = []
        link_world_matrices[str(index)] = {
            joint: _matrix(joint, cmds)
            for joint in _solver_link_joints(solver, index, cmds)
        }
    try:
        enabled = bool(cmds.getAttr(f"{solver}.enabled"))
    except RuntimeError:
        enabled = None
    return {
        "solver": solver,
        "enabled": enabled,
        "goalWorldMatrix": _flatten_numeric(cmds.getAttr(f"{solver}.goalWorldMatrix")),
        "outputRotate": outputs,
        "ikLinkWorldMatrices": link_world_matrices,
        "effector": effector,
        "effectorWorldMatrix": _matrix(effector, cmds),
        "effectorWorldTranslation": _world_translation(effector, cmds),
    }


def _find_rig_root(cmds) -> str:
    from mmd_tools.core.constants import ATTR_MMD_CONTROL_RIG_JSON

    roots = cmds.ls(f"*.{ATTR_MMD_CONTROL_RIG_JSON}", objectsOnly=True, long=True) or []
    if len(roots) != 1:
        raise RuntimeError(f"expected one MMD control-rig metadata root, found {roots}")
    return str(roots[0])


def _animation_layer_diagnostics(cmds) -> dict[str, Any]:
    """Capture animation-layer and blend ownership relevant to VMD import."""

    rows = []
    for layer in cmds.ls(type="animLayer") or []:
        layer_name = str(layer)
        try:
            attributes = [
                str(value)
                for value in (cmds.animLayer(layer, query=True, attribute=True) or [])
            ]
        except RuntimeError:
            attributes = []
        try:
            blend_nodes = [
                str(value)
                for value in (
                    cmds.listConnections(
                        layer,
                        source=False,
                        destination=True,
                        type="animBlendNodeBase",
                    )
                    or []
                )
            ]
        except RuntimeError:
            blend_nodes = []
        rows.append(
            {
                "name": layer_name,
                "attributes": sorted(set(attributes)),
                "animBlendNodes": sorted(set(blend_nodes)),
                "base": layer_name in {"BaseAnimation", "baseAnimation"},
            }
        )
    vmd_rows = [row for row in rows if row["name"] == "VMD_Motion"]
    populated_non_base = [
        row
        for row in rows
        if not row["base"] and (row["attributes"] or row["animBlendNodes"])
    ]
    return {
        "layers": sorted(rows, key=lambda row: row["name"]),
        "vmdMotion": vmd_rows,
        "populatedNonBase": populated_non_base,
        "vmdMotionOwnershipPass": not any(
            row["attributes"] or row["animBlendNodes"] for row in vmd_rows
        ),
        "singleWriterPass": not populated_non_base,
    }


def _vmd_role_diagnostics(vmd_data) -> dict[str, dict[str, Any]]:
    """Classify VMD bone roles by authored non-identity payload."""

    rows: dict[str, dict[str, Any]] = {}
    for frame in getattr(vmd_data, "bone_frames", []) or []:
        name = str(frame.bone_name)
        position = [float(value) for value in frame.position]
        rotation = [float(value) for value in frame.rotation]
        non_identity_position = any(abs(value) > MOVE_EPSILON for value in position)
        non_identity_rotation = (
            len(rotation) >= 4
            and (
                any(abs(value) > MOVE_EPSILON for value in rotation[:3])
                or abs(rotation[3] - 1.0) > MOVE_EPSILON
            )
        )
        row = rows.setdefault(
            name,
            {
                "frameCount": 0,
                "nonRestFrameCount": 0,
                "hasNonIdentityPosition": False,
                "hasNonIdentityRotation": False,
                "frames": [],
            },
        )
        row["frameCount"] += 1
        row["hasNonIdentityPosition"] |= non_identity_position
        row["hasNonIdentityRotation"] |= non_identity_rotation
        if non_identity_position or non_identity_rotation:
            row["nonRestFrameCount"] += 1
        row["frames"].append(int(frame.frame_number))
    for row in rows.values():
        row["frames"] = sorted(set(row["frames"]))
        row["identityOnly"] = row["nonRestFrameCount"] == 0
    return dict(sorted(rows.items()))


def _vmd_applicability_candidates(vmd_data, joint_for_name) -> list[dict[str, Any]]:
    """Return mapped VMD keys whose values changed from that bone's first key.

    A VMD key can be non-zero without being motion: imported fixtures commonly
    repeat the authored initial pose at later non-zero frames.  Use each bone's
    first key as its source baseline and leave the Maya world-space comparison
    to the caller.
    """

    by_bone: dict[str, list[Any]] = {}
    for frame in getattr(vmd_data, "bone_frames", []) or []:
        by_bone.setdefault(str(frame.bone_name), []).append(frame)

    result = []
    for bone_name, frames in sorted(by_bone.items()):
        ordered = sorted(frames, key=lambda item: int(item.frame_number))
        if len(ordered) < 2:
            continue
        joint = joint_for_name(bone_name)
        if not joint:
            continue
        baseline = ordered[0]
        baseline_frame = int(baseline.frame_number)
        baseline_position = [float(value) for value in baseline.position]
        baseline_rotation = [float(value) for value in baseline.rotation]
        for candidate in ordered[1:]:
            candidate_frame = int(candidate.frame_number)
            if candidate_frame <= baseline_frame:
                continue
            candidate_position = [float(value) for value in candidate.position]
            candidate_rotation = [float(value) for value in candidate.rotation]
            position_delta = max(
                (abs(after - before) for before, after in zip(baseline_position, candidate_position)),
                default=0.0,
            )
            rotation_delta = max(
                (abs(after - before) for before, after in zip(baseline_rotation, candidate_rotation)),
                default=0.0,
            )
            source_delta = max(position_delta, rotation_delta)
            if source_delta <= MOVE_EPSILON:
                continue
            result.append(
                {
                    "bone": bone_name,
                    "joint": str(joint),
                    "baseline": baseline,
                    "candidate": candidate,
                    "baselineFrame": baseline_frame,
                    "candidateFrame": candidate_frame,
                    "baselinePosition": baseline_position,
                    "baselineRotation": baseline_rotation,
                    "candidatePosition": candidate_position,
                    "candidateRotation": candidate_rotation,
                    "sourcePositionMaxAbsDelta": position_delta,
                    "sourceRotationMaxAbsDelta": rotation_delta,
                    "sourceMaxAbsDelta": source_delta,
                }
            )
            # One real source-motion witness per bone is sufficient and keeps
            # Maya time evaluation/report size bounded for dense VMD files.
            break
    return result


def _canonical_warning_evidence(report: Any) -> list[dict[str, str]]:
    """Keep warning callback evidence compact and independent of UI wording."""

    return [
        {
            "code": str(issue.code),
            "severity": str(issue.severity),
            "path": str(issue.path),
            "message": str(issue.message),
        }
        for issue in getattr(report, "issues", ()) or ()
    ]


def _report_evidence(report: Any) -> dict[str, Any] | None:
    """Return the terminal report facts needed when one-shot publication fails."""

    if report is None:
        return None
    status = None
    to_dict = getattr(report, "to_dict", None)
    if callable(to_dict):
        status = to_dict().get("status")
    return {
        "status": status,
        "format": getattr(report, "export_format", None),
        "mode": getattr(report, "mode", None),
        "warnings": _canonical_warning_evidence(report),
    }


def _approve_one_shot_export_warnings(report: Any, auto_gate: dict[str, Any]) -> bool:
    """Record the in-call Export Anyway decision and explicitly approve it."""

    acknowledgement = auto_gate.setdefault(
        "warningAcknowledgement",
        {"invoked": False, "approved": False, "callbackCount": 0, "warnings": []},
    )
    acknowledgement["invoked"] = True
    acknowledgement["callbackCount"] = int(acknowledgement["callbackCount"]) + 1
    acknowledgement["warnings"] = _canonical_warning_evidence(report)
    if bool(getattr(report, "is_blocking", False)):
        # A fatal report is never expected here, but it must not be approved if
        # a workflow integration accidentally routes one through this callback.
        acknowledgement["fatalRejected"] = True
        return False
    acknowledgement["approved"] = True
    return True


def _record_one_shot_terminal_evidence(
    auto_gate: dict[str, Any], published: Any, output_path: Path
) -> None:
    """Record a complete one-shot terminal state before touching its output."""

    report = getattr(published, "report", None)
    error = getattr(published, "error", None)
    succeeded = bool(getattr(published, "succeeded", False))
    output_exists = output_path.is_file()
    report_evidence = _report_evidence(report)
    auto_gate.update(
        {
            "publishedState": getattr(published, "state", None),
            "publishedSucceeded": succeeded,
            "publishedError": (
                None if error is None else f"{type(error).__name__}: {error}"
            ),
            "validationReport": report_evidence,
            "phaseTimings": dict(getattr(published, "phase_timings", {}) or {}),
            "activePhase": getattr(published, "active_phase", None),
            "completedPhases": list(getattr(published, "completed_phases", ()) or ()),
            "outputExists": output_exists,
        }
    )
    if succeeded and output_exists:
        return
    reason = (
        "automatic Bake Timeline did not publish a readable output: "
        f"state={auto_gate['publishedState']!r}; "
        f"succeeded={succeeded}; outputExists={output_exists}; "
        f"error={auto_gate['publishedError']!r}; report={report_evidence!r}"
    )
    auto_gate["publishFailureReason"] = reason
    raise RuntimeError(reason)


def _record_control_rig_diagnostics(
    report: dict[str, Any],
    profile: Mapping[str, Any],
    vmd_roles: Mapping[str, Mapping[str, Any]],
) -> None:
    """Persist converter diagnostics with VMD role payload classification."""

    diagnostics = dict(profile.get("mmd_control_rig") or {})
    rows = []
    for diagnostic in diagnostics.get("diagnostics", []) or []:
        detail = diagnostic.get("detail", []) if isinstance(diagnostic, Mapping) else []
        if not isinstance(detail, list):
            continue
        for role in detail:
            rows.append(
                {
                    "role": str(role),
                    **dict(vmd_roles.get(str(role), {})),
                }
            )
    diagnostics["unsupportedRoleClassification"] = rows
    converter_profile = profile.get("vmd_converter")
    if isinstance(converter_profile, Mapping):
        diagnostics["vmdConverter"] = dict(converter_profile)
    report["createOnImport"]["diagnostics"] = diagnostics


def _write_maya_report(report_path: Path, report: Mapping[str, Any]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


# ===================================================================
# Maya-side: runs inside the live Maya GUI
# ===================================================================
def run_e2e_check(
    log_path: str,
    model_path: str,
    motion_path: str,
    report_path: str,
    scene_path: str,
    exported_vmd_path: str,
    evaluation_mode: str = "default",
    create_on_import: bool = False,
    auto_bake_only: bool = False,
    cpp_config: str = "Debug",
    ffi_path: str | None = None,
    auto_frame_range: tuple[int, int] | None = None,
) -> None:
    """Execute the complete control-rig workflow in a live Maya GUI.

    ``create_on_import`` is opt-in so the default invocation continues to
    exercise the legacy PMX import -> VMD import -> explicit rig-build route.
    When enabled, VMD import itself owns the transactional Control Rig create
    or reuse and direct controller keying path.

    ``auto_bake_only`` keeps the existing Control Rig edits and evidence but
    stops after the automatic Bake Timeline export gate.  It is intended for
    focused host diagnosis; normal mode retains every existing assertion and
    round-trip gate.
    """

    import maya.cmds as cmds

    log_file = Path(log_path)
    report_file = Path(report_path)
    report: dict[str, Any] = {
        "kind": "mmd-control-rig-gui-e2e",
        "roundtripOracle": "internal_maya_vmd_export_reimport",
        "internalOracle": {
            "identity": "maya_vmd_export_reimport_authored_parity",
            "status": "pending",
        },
        "externalOracle": {
            "identity": "mmd_anim_mesh_oracle_compare_rig_pmx_bind",
            "status": "pending",
        },
        "status": "error",
        "mayaVersion": None,
        "evaluationMode": {
            "requested": str(evaluation_mode or "default"),
            "active": None,
            "mayaMode": None,
        },
        "focusedMode": {
            "autoBakeOnly": bool(auto_bake_only),
            "scope": "auto_bake_export" if auto_bake_only else "full_control_rig_roundtrip",
        },
        "cppConfig": str(cpp_config),
        "autoFrameRange": {
            "requested": list(auto_frame_range) if auto_frame_range else None,
            "actual": None,
        },
        "ffiRuntime": {
            "requestedPath": str(ffi_path) if ffi_path else None,
            "configuredPath": None,
            "resolvedPath": None,
            "symbolAvailability": {},
            "status": "not_requested" if not ffi_path else "pending",
        },
        "model": str(model_path),
        "motion": str(motion_path),
        "createOnImport": {
            "requested": bool(create_on_import),
            "options": {
                "create_mmd_control_rig": bool(create_on_import),
                "bake_mode": False,
                "clear_existing_motion": bool(create_on_import),
            },
            "route": "vmd_import_control_rig"
            if create_on_import
            else "explicit_control_rig_build",
            "owner": None,
            "state": None,
            "rig": {},
            "diagnostics": {},
            "clearExistingMotion": {},
            "animationLayers": {},
        },
        "states": {},
        "roles": [],
        "vmdApplicability": {},
        "ikMove": {},
        "ikToggle": {},
        "autoBakeExport": {},
        "cycles": [],
        "roundtrip": {},
        "errors": [],
    }

    def _log(message: str) -> None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(str(message) + "\n")
        try:
            print(message)
        except Exception:
            pass

    dll_directory_handle = None
    try:
        report["mayaVersion"] = str(cmds.about(version=True))
        _log("=== MMD Control Rig GUI E2E begin ===")

        plugin_path = _PROJECT_ROOT / "mmd_tools" / "plugin_main.py"
        plugin_name = plugin_path.stem
        maya_major = str(cmds.about(version=True)).split(".", 1)[0]
        cpp_plugin = (
            _PROJECT_ROOT
            / "plug-ins"
            / maya_major
            / str(cpp_config)
            / "mmd_tools_cpp.mll"
        )
        if not cpp_plugin.is_file():
            raise RuntimeError(
                f"Maya {maya_major} {cpp_config} C++ plugin is required for mmdCcdIk E2E: {cpp_plugin}"
            )
        if ffi_path:
            configured_ffi_path = Path(ffi_path).expanduser()
            if not configured_ffi_path.is_absolute():
                configured_ffi_path = _PROJECT_ROOT / configured_ffi_path
            configured_ffi_path = configured_ffi_path.resolve()
            report["ffiRuntime"]["configuredPath"] = str(configured_ffi_path)
            if not configured_ffi_path.is_file():
                raise RuntimeError(f"requested mmd-anim FFI library does not exist: {configured_ffi_path}")
            os.environ["MMD_ANIM_FFI_PATH"] = str(configured_ffi_path)
        plugin_dir = str(cpp_plugin.parent)
        if plugin_dir not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = plugin_dir + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            dll_directory_handle = os.add_dll_directory(plugin_dir)
        if not cmds.pluginInfo(str(cpp_plugin), query=True, loaded=True):
            cmds.loadPlugin(str(cpp_plugin), quiet=True)
            _log(f"loaded C++ plugin: {cpp_plugin}")
        if not cmds.pluginInfo(plugin_name, query=True, loaded=True):
            cmds.loadPlugin(str(plugin_path), quiet=True)
            _log(f"loaded plugin: {plugin_path}")

        if ffi_path:
            from mmd_tools.core.native.mmd_anim_runtime import (
                get_mmd_runtime_library,
                get_runtime_library_path,
            )

            runtime_library = get_mmd_runtime_library()
            runtime_path = get_runtime_library_path()
            resolved_runtime_path = runtime_path.resolve() if runtime_path else None
            symbol_availability = {
                "mmd_runtime_export_vmd_from_parts": bool(
                    runtime_library is not None
                    and hasattr(runtime_library, "mmd_runtime_export_vmd_from_parts")
                )
            }
            report["ffiRuntime"].update(
                {
                    "resolvedPath": str(resolved_runtime_path) if resolved_runtime_path else None,
                    "symbolAvailability": symbol_availability,
                }
            )
            configured_path = Path(report["ffiRuntime"]["configuredPath"])
            path_matches = bool(
                resolved_runtime_path
                and os.path.normcase(str(resolved_runtime_path))
                == os.path.normcase(str(configured_path))
            )
            if runtime_library is None or not path_matches or not all(symbol_availability.values()):
                report["ffiRuntime"]["status"] = "fail"
                raise RuntimeError(
                    "requested mmd-anim FFI library was not loaded with the required export: "
                    f"requested={configured_path}, loaded={resolved_runtime_path}, "
                    f"symbols={symbol_availability}"
                )
            report["ffiRuntime"]["status"] = "pass"
            _log(
                "mmd-anim FFI runtime: "
                f"path={resolved_runtime_path} symbols={symbol_availability}"
            )

        if create_on_import:
            # Preserve Maya's script-editor diagnostics alongside the JSON
            # report so a fail-closed import exception retains its exact Maya
            # API or route error alongside the structured summary.
            history_path = log_file.with_suffix(".maya_history.log")
            try:
                cmds.scriptEditorInfo(
                    historyFilename=str(history_path),
                    writeHistory=True,
                    suppressInfo=False,
                    suppressWarnings=False,
                    suppressErrors=False,
                )
                report["createOnImport"]["mayaScriptEditorHistory"] = str(history_path)
            except Exception:
                report["createOnImport"]["mayaScriptEditorHistory"] = None

        report["evaluationMode"] = _evaluation_mode_snapshot(evaluation_mode, cmds)
        _log(
            "evaluation mode: requested=%s active=%s maya=%s"
            % (
                report["evaluationMode"]["requested"],
                report["evaluationMode"]["active"],
                report["evaluationMode"]["mayaMode"],
            )
        )

        from mmd_tools.core.mmd_control_rig_builder import (
            CONTROL_RIG_ATTACHED,
            CONTROL_RIG_BAKED,
            CONTROL_RIG_CONTROL_OWNED,
            CONTROL_RIG_EDIT,
            build_mmd_control_rig,
            read_mmd_control_rig_metadata,
        )
        from mmd_tools.core.mmd_control_rig_motion import (
            bake_mmd_control_rig,
            enter_mmd_control_rig_edit,
        )
        from mmd_tools.core.vmd_data import VmdData
        from mmd_tools.converters.vmd_scene_collector import VmdSceneCollector
        from mmd_tools.io.mmd_importer import import_mmd_file
        from mmd_tools.io.vmd_exporter import VmdExporter

        source_vmd = VmdData().parse_file(str(motion_path))
        report["vmdApplicability"]["boneFrameCount"] = len(source_vmd.bone_frames)
        if not source_vmd.bone_frames:
            raise RuntimeError("fixture VMD contains no bone frames")
        vmd_role_diagnostics = _vmd_role_diagnostics(source_vmd)
        if create_on_import:
            report["createOnImport"]["vmdRoles"] = vmd_role_diagnostics

        cmds.file(new=True, force=True)
        root = import_mmd_file(
            str(model_path),
            options={
                "setup_rig": True,
                "setup_bone_orientation": True,
                "import_physics": False,
            },
        )
        if not root:
            raise RuntimeError(f"PMX import returned no model root: {model_path}")
        root = str(root)
        _log(f"imported PMX root: {root}")

        # Seed one target joint with an out-of-band key only for the opt-in
        # route.  The VMD import's clear_existing_motion=True contract must
        # remove it before authored Control Rig keys are created.
        clear_seed: dict[str, Any] = {}
        if create_on_import:
            seed_joint = None
            for candidate in sorted(
                source_vmd.bone_frames,
                key=lambda item: (int(item.frame_number), str(item.bone_name)),
            ):
                seed_joint = _find_joint_for_mmd_name(candidate.bone_name, cmds)
                if seed_joint:
                    break
            if not seed_joint:
                raise RuntimeError(
                    "fixture VMD has no PMX joint available for clear-existing-motion seed"
                )
            seed_frame = 999
            seed_attr = "rotateX"
            cmds.setKeyframe(seed_joint, attribute=seed_attr, time=seed_frame, value=17.0)
            clear_seed = {
                "node": seed_joint,
                "attribute": seed_attr,
                "frame": seed_frame,
                "seeded": True,
            }

        vmd_profile: dict[str, Any] = {}
        vmd_options = {"target_model": root, "pmx_path": str(model_path)}
        if create_on_import:
            vmd_options.update(
                {
                    "create_mmd_control_rig": True,
                    "bake_mode": False,
                    "clear_existing_motion": True,
                    "profile": vmd_profile,
                }
            )
        try:
            imported_motion = import_mmd_file(
                str(motion_path),
                options=vmd_options,
            )
        except Exception:
            if create_on_import:
                _record_control_rig_diagnostics(
                    report,
                    vmd_profile,
                    vmd_role_diagnostics,
                )
                report["createOnImport"]["animationLayers"] = (
                    _animation_layer_diagnostics(cmds)
                )
            raise
        if not imported_motion:
            if create_on_import:
                _record_control_rig_diagnostics(
                    report,
                    vmd_profile,
                    vmd_role_diagnostics,
                )
                report["createOnImport"]["animationLayers"] = (
                    _animation_layer_diagnostics(cmds)
                )
            raise RuntimeError(f"VMD import returned no result: {motion_path}")
        _log(f"imported VMD: {motion_path}")

        if create_on_import:
            remaining_seed_frames = [
                int(value)
                for value in (
                    cmds.keyframe(
                        clear_seed["node"],
                        attribute=clear_seed["attribute"],
                        query=True,
                        timeChange=True,
                    )
                    or []
                )
            ]
            clear_seed["remainingFrames"] = remaining_seed_frames
            clear_seed["pass"] = clear_seed["frame"] not in remaining_seed_frames
            report["createOnImport"]["clearExistingMotion"] = clear_seed
            if not clear_seed["pass"]:
                raise RuntimeError(
                    "clear_existing_motion=True left the seeded target-joint key"
                )
            _record_control_rig_diagnostics(
                report,
                vmd_profile,
                vmd_role_diagnostics,
            )

        sample = None
        checked_candidates = []
        for candidate in _vmd_applicability_candidates(
            source_vmd, lambda name: _find_joint_for_mmd_name(name, cmds)
        ):
            cmds.currentTime(candidate["baselineFrame"], edit=True)
            cmds.refresh(force=True)
            sample_before = _matrix(candidate["joint"], cmds)
            cmds.currentTime(candidate["candidateFrame"], edit=True)
            cmds.refresh(force=True)
            sample_after = _matrix(candidate["joint"], cmds)
            world_delta = max(
                (abs(actual - expected) for actual, expected in zip(sample_before, sample_after)),
                default=0.0,
            )
            checked_candidates.append(
                {
                    "bone": candidate["bone"],
                    "baselineFrame": candidate["baselineFrame"],
                    "candidateFrame": candidate["candidateFrame"],
                    "sourceMaxAbsDelta": candidate["sourceMaxAbsDelta"],
                    "worldMatrixMaxAbsDelta": world_delta,
                }
            )
            if world_delta > MOVE_EPSILON:
                sample = {**candidate, "worldMatrixMaxAbsDelta": world_delta}
                break
        if sample is None:
            report["vmdApplicability"]["checkedCandidates"] = checked_candidates
            raise RuntimeError(
                "fixture VMD has no mapped key with both source and world-space motion"
            )
        report["vmdApplicability"].update(
            {
                "sampleBone": sample["bone"],
                "sampleJoint": sample["joint"],
                "sampleFrame": sample["candidateFrame"],
                "samplePosition": sample["candidatePosition"],
                "sampleRotation": sample["candidateRotation"],
                "baselineFrame": sample["baselineFrame"],
                "baselinePosition": sample["baselinePosition"],
                "baselineRotation": sample["baselineRotation"],
                "candidateFrame": sample["candidateFrame"],
                "candidatePosition": sample["candidatePosition"],
                "candidateRotation": sample["candidateRotation"],
                "sourcePositionMaxAbsDelta": sample["sourcePositionMaxAbsDelta"],
                "sourceRotationMaxAbsDelta": sample["sourceRotationMaxAbsDelta"],
                "sourceMaxAbsDelta": sample["sourceMaxAbsDelta"],
                "sampleWorldMatrixMaxAbsDelta": sample["worldMatrixMaxAbsDelta"],
                "checkedCandidates": checked_candidates,
                "pass": (
                    sample["sourceMaxAbsDelta"] > MOVE_EPSILON
                    and sample["worldMatrixMaxAbsDelta"] > MOVE_EPSILON
                ),
            }
        )
        _log(
            "VMD applicability: boneFrames=%d sample=%s baseline=%d candidate=%d "
            "sourceMaxAbsDelta=%.8f worldMatrixMaxAbsDelta=%.8f"
            % (
                len(source_vmd.bone_frames),
                sample["bone"],
                sample["baselineFrame"],
                sample["candidateFrame"],
                sample["sourceMaxAbsDelta"],
                sample["worldMatrixMaxAbsDelta"],
            )
        )

        baseline_cycle = _cycle_state("after_vmd_import", cmds)
        report["cycles"].append(baseline_cycle)

        metadata_before_build = read_mmd_control_rig_metadata(root)
        rig = build_mmd_control_rig(root)
        report["states"]["afterBuild"] = rig.state
        report["roles"] = sorted(str(role) for role in rig.controls)
        if create_on_import:
            metadata = read_mmd_control_rig_metadata(root)
            if not metadata:
                raise RuntimeError("VMD create-on-import did not persist control-rig metadata")
            report["createOnImport"].update(
                {
                    "owner": metadata.get("owner"),
                    "state": metadata.get("state"),
                    "rig": {
                        "metadataPresentBeforeImport": metadata_before_build is not None,
                        "createdByImport": metadata_before_build is None,
                        "reusedLookup": not bool(rig.created),
                        "buildResultCreated": bool(rig.created),
                        "controlCount": len(rig.controls),
                    },
                }
            )
            if metadata.get("owner") != CONTROL_RIG_CONTROL_OWNED:
                raise RuntimeError(
                    "create-on-import did not make Control Rig the motion owner: "
                    f"{metadata.get('owner')}"
                )
            if metadata.get("state") != CONTROL_RIG_EDIT:
                raise RuntimeError(
                    "create-on-import did not enter EDIT state: "
                    f"{metadata.get('state')}"
                )
            if rig.owner != CONTROL_RIG_CONTROL_OWNED or rig.state != CONTROL_RIG_EDIT:
                raise RuntimeError(
                    "build lookup disagrees with create-on-import ownership/state: "
                    f"owner={rig.owner} state={rig.state}"
                )
            animation_layers = _animation_layer_diagnostics(cmds)
            report["createOnImport"]["animationLayers"] = animation_layers
            if not animation_layers["vmdMotionOwnershipPass"]:
                raise RuntimeError(
                    "create-on-import created VMD_Motion animLayer/animBlend ownership"
                )
            if not animation_layers["singleWriterPass"]:
                raise RuntimeError(
                    "create-on-import left populated non-base animation-layer ownership"
                )
        elif rig.state != CONTROL_RIG_ATTACHED:
            raise RuntimeError(f"build did not produce ATTACHED state: {rig.state}")
        missing_ik_roles = [
            role for role, _offset in IK_MOVE_CASES if role not in rig.controls
        ]
        if missing_ik_roles:
            raise RuntimeError(
                "fixture has no required IK controls: "
                f"{', '.join(missing_ik_roles)}"
            )
        _log(f"built control rig ({len(rig.controls)} controls)")

        metadata = read_mmd_control_rig_metadata(root)
        if not metadata:
            raise RuntimeError("control-rig metadata missing after build")
        if create_on_import:
            report["createOnImport"]["owner"] = metadata.get("owner")
            report["createOnImport"]["state"] = metadata.get("state")
        solver, effector = _resolve_ik_solver(metadata, "left_foot_ik", cmds)
        control = str(rig.controls["left_foot_ik"])
        _log(f"left foot control={control}, solver={solver}, effector={effector}")

        edit_metadata = (
            metadata if create_on_import else enter_mmd_control_rig_edit(root)
        )
        report["states"]["afterEdit"] = edit_metadata.get("state")
        if edit_metadata.get("state") != CONTROL_RIG_EDIT:
            raise RuntimeError(f"EDIT transition failed: {edit_metadata.get('state')}")

        frame = 3
        cmds.currentTime(frame, edit=True)
        cmds.refresh(force=True)
        before_cycle = _cycle_state("before_ik_move", cmds)
        report["cycles"].append(before_cycle)

        ik_cases: dict[str, dict[str, Any]] = {}
        for role, offset in IK_MOVE_CASES:
            case: dict[str, Any] = {
                "role": role,
                "frame": frame,
                "offset": float(offset),
                "control": str(rig.controls.get(role, "")),
                "pass": False,
            }
            try:
                case_solver, case_effector = _resolve_ik_solver(metadata, role, cmds)
                case_control = str(rig.controls[role])
                before_solver = _solver_snapshot(case_solver, case_effector, cmds)
                before_control_world = _matrix(case_control, cmds)
                authored = _author_control_sentinel(
                    case_control,
                    "translateX",
                    frame,
                    float(offset),
                    cmds,
                )
                after_solver = _solver_snapshot(case_solver, case_effector, cmds)
                after_control_world = _matrix(case_control, cmds)
                target_delta = _distance(
                    before_solver["goalWorldMatrix"], after_solver["goalWorldMatrix"]
                )
                output_delta = _distance(
                    [
                        item
                        for values in before_solver["outputRotate"].values()
                        for item in values
                    ],
                    [
                        item
                        for values in after_solver["outputRotate"].values()
                        for item in values
                    ],
                )
                link_deltas = {}
                before_links = before_solver["ikLinkWorldMatrices"]
                after_links = after_solver["ikLinkWorldMatrices"]
                for index in sorted(set(before_links) & set(after_links)):
                    for joint in sorted(
                        set(before_links[index]) & set(after_links[index])
                    ):
                        link_deltas[f"{index}:{joint}"] = _distance(
                            before_links[index][joint], after_links[index][joint]
                        )
                case.update(
                    {
                        "solver": case_solver,
                        "effector": case_effector,
                        "controlAuthored": authored,
                        "controlWorldMatrixBefore": before_control_world,
                        "controlWorldMatrixAfter": after_control_world,
                        "controlWorldMatrixDelta": _distance(
                            before_control_world, after_control_world
                        ),
                        "before": before_solver,
                        "after": after_solver,
                        "solverTargetDelta": target_delta,
                        "outputRotateDelta": output_delta,
                        "ikLinkWorldMatrixDeltas": link_deltas,
                        "pass": _ik_move_witness_pass(
                            control_route_pass=bool(authored["pass"]),
                            control_delta=float(authored["delta"]),
                            target_delta=target_delta,
                            link_deltas=link_deltas,
                        ),
                    }
                )
            except Exception as exc:
                case["error"] = f"{type(exc).__name__}: {exc}"
            ik_cases[role] = case
            _log(
                "IK move %s: pass=%s targetDelta=%s linkDeltas=%s"
                % (
                    role,
                    case["pass"],
                    case.get("solverTargetDelta"),
                    json.dumps(case.get("ikLinkWorldMatrixDeltas", {}), sort_keys=True),
                )
            )

        left_case = ik_cases["left_foot_ik"]
        after_cycle = _cycle_state("after_ik_move", cmds)
        report["cycles"].append(after_cycle)
        report["ikMove"] = {
            "frame": frame,
            "requiredCases": [role for role, _offset in IK_MOVE_CASES],
            "cases": ik_cases,
            "control": left_case.get("control", control),
            "solver": left_case.get("solver", solver),
            "effector": left_case.get("effector", effector),
            "before": left_case.get("before"),
            "after": left_case.get("after"),
            "goalWorldMatrixDelta": left_case.get("solverTargetDelta", 0.0),
            "outputRotateDelta": left_case.get("outputRotateDelta", 0.0),
            "effectorWorldMatrixDelta": (
                _distance(
                    (left_case.get("before") or {}).get("effectorWorldMatrix", []),
                    (left_case.get("after") or {}).get("effectorWorldMatrix", []),
                )
                if left_case.get("before") and left_case.get("after")
                else 0.0
            ),
            "pass": all(case.get("pass") is True for case in ik_cases.values()),
        }
        if not report["ikMove"]["pass"] and not auto_bake_only:
            raise RuntimeError("required IK move witnesses did not pass")
        if not report["ikMove"]["pass"] and auto_bake_only:
            _log("focused auto-bake mode: retaining failed IK move evidence")

        enabled_before = bool(cmds.getAttr(f"{solver}.enabled"))
        enabled_after_expected = not enabled_before
        enabled_sources = cmds.listConnections(
            f"{control}.ikEnabled",
            source=True,
            destination=False,
            plugs=True,
        ) or []
        enabled_source_keys = None
        if enabled_sources:
            # EDIT preserves an existing animation source on the controller.
            # Key that source directly; setKeyframe on a destination with an
            # incoming animCurve can be accepted by Maya but leave its value
            # unchanged.
            source_node = str(enabled_sources[0]).split(".", 1)[0]
            source_type = str(cmds.nodeType(source_node))
            if not source_type.startswith("animCurve"):
                raise RuntimeError(
                    f"ikEnabled source is not an animCurve: {enabled_sources[0]} ({source_type})"
                )
            cmds.setKeyframe(
                source_node,
                time=frame,
                value=int(enabled_after_expected),
            )
            try:
                enabled_source_keys = {
                    "node": source_node,
                    "type": source_type,
                    "times": _flatten_numeric(
                        cmds.keyframe(source_node, query=True, timeChange=True)
                    ),
                    "values": _flatten_numeric(
                        cmds.keyframe(source_node, query=True, valueChange=True)
                    ),
                }
            except RuntimeError:
                enabled_source_keys = {"node": source_node, "type": source_type}
        else:
            cmds.setKeyframe(
                control,
                attribute="ikEnabled",
                time=frame,
                value=int(enabled_after_expected),
            )
        # Keying a controller attribute does not always dirty a custom bool
        # input in a GUI evaluation context.  Explicitly dirty the owned solver
        # before reading its evaluated enabled state.
        cmds.dgdirty(allPlugs=True)
        cmds.dgdirty(control)
        cmds.dgdirty(solver)
        cmds.refresh(force=True)
        enabled_after = bool(cmds.getAttr(f"{solver}.enabled"))
        try:
            control_enabled_after = bool(cmds.getAttr(f"{control}.ikEnabled"))
        except RuntimeError:
            control_enabled_after = None
        control_enabled_sources = [
            str(value)
            for value in (
                cmds.listConnections(
                    f"{control}.ikEnabled",
                    source=True,
                    destination=False,
                    plugs=True,
                )
                or []
            )
        ]
        solver_enabled_sources = [
            str(value)
            for value in (
                cmds.listConnections(
                    f"{solver}.enabled",
                    source=True,
                    destination=False,
                    plugs=True,
                )
                or []
            )
        ]
        report["ikToggle"] = {
            "frame": frame,
            "control": control,
            "solver": solver,
            "before": enabled_before,
            "after": enabled_after,
            "expectedAfter": enabled_after_expected,
            "controlAfter": control_enabled_after,
            "controlSources": control_enabled_sources,
            "solverSources": solver_enabled_sources,
            "sourceKeys": enabled_source_keys,
            "pass": enabled_after == enabled_after_expected,
        }
        _log(f"IK enabled toggle: {enabled_before} -> {enabled_after}")
        if not report["ikToggle"]["pass"] and not auto_bake_only:
            raise RuntimeError("ikEnabled toggle did not reach mmdCcdIk.enabled")
        if not report["ikToggle"]["pass"] and auto_bake_only:
            _log("focused auto-bake mode: retaining failed IK toggle evidence")

        # Exercise the production one-shot export while the Control Rig owns
        # the authoring motion.  Collection may temporarily bake MMD inputs,
        # but its watch and sibling output do not outlive this call.
        from mmd_tools.adapters.maya_vmd_prepare_backend import (
            create_maya_bake_timeline_vmd_action,
        )
        from mmd_tools.services.export_workflow_service import (
            ExportWorkflowRequest,
            ExportWorkflowService,
        )

        auto_output = Path(exported_vmd_path).with_suffix(".auto_bake.vmd")
        timeline_range = (
            float(cmds.playbackOptions(query=True, minTime=True)),
            float(cmds.playbackOptions(query=True, maxTime=True)),
        )
        if auto_bake_only and auto_frame_range is not None:
            requested_start, requested_end = auto_frame_range
            if requested_start < 0 or requested_end < requested_start:
                raise ValueError(
                    "automatic Bake Timeline frame range must be ordered and non-negative"
                )
            cmds.playbackOptions(
                minTime=int(requested_start),
                maxTime=int(requested_end),
                animationStartTime=int(requested_start),
                animationEndTime=int(requested_end),
            )
            timeline_range = (float(requested_start), float(requested_end))
        auto_start = int(round(timeline_range[0]))
        auto_end = int(round(timeline_range[1]))
        auto_compare_frames = (
            tuple(
                sorted(
                    {
                        auto_start,
                        auto_end,
                        auto_start + (auto_end - auto_start) // 4,
                        auto_start + (auto_end - auto_start) // 2,
                        auto_start + (auto_end - auto_start) * 3 // 4,
                    }
                )
            )
            if auto_bake_only
            else ROUNDTRIP_FRAMES
        )
        report["autoFrameRange"]["actual"] = list(timeline_range)
        auto_source_world = {}
        auto_source_ik = {}
        auto_sentinel_indices = {}
        auto_sentinel_names = {}
        auto_curve_snapshot_before = {}
        auto_curve_snapshot_after = {}
        auto_export_payload = {}
        if auto_bake_only:
            authored_controls = {
                "center": str(rig.controls["center"]),
                "left_arm": str(rig.controls["left_arm"]),
            }
            for key_time, center_x, arm_rotation in (
                (auto_start, 0.0, (0.0, 0.0, 0.0)),
                (auto_end, 1.25, (18.0, -7.0, 11.0)),
                (auto_end + 5, 9.0, (65.0, -25.0, 37.0)),
            ):
                cmds.setKeyframe(
                    authored_controls["center"],
                    attribute="translateX",
                    time=key_time,
                    value=center_x,
                )
                for axis, value in zip("XYZ", arm_rotation):
                    cmds.setKeyframe(
                        authored_controls["left_arm"],
                        attribute=f"rotate{axis}",
                        time=key_time,
                        value=value,
                    )
            from mmd_tools.core.mmd_control_rig_builder import (
                resolve_mmd_control_rig_binding_joint,
            )

            for role in authored_controls:
                joint = resolve_mmd_control_rig_binding_joint(
                    cmds,
                    edit_metadata["bindings"][role],
                )
                auto_sentinel_indices[role] = str(
                    int(cmds.getAttr(f"{joint}.mmd_bone_index"))
                )
                auto_sentinel_names[role] = str(
                    cmds.getAttr(f"{joint}.mmd_bone_name")
                )

            def _control_curve_snapshot() -> dict[str, Any]:
                snapshot: dict[str, Any] = {}
                for role, control in authored_controls.items():
                    curves = sorted(
                        {
                            str(curve)
                            for curve in (
                                cmds.keyframe(
                                    control,
                                    query=True,
                                    name=True,
                                )
                                or []
                            )
                        }
                    )
                    snapshot[role] = {
                        str(curve): {
                            "times": [
                                float(value)
                                for value in (
                                    cmds.keyframe(
                                        curve,
                                        query=True,
                                        timeChange=True,
                                    )
                                    or []
                                )
                            ],
                            "values": [
                                float(value)
                                for value in (
                                    cmds.keyframe(
                                        curve,
                                        query=True,
                                        valueChange=True,
                                    )
                                    or []
                                )
                            ],
                        }
                        for curve in curves
                    }
                return snapshot

            def _vmd_sentinel_payload(vmd_data: Any) -> dict[str, Any]:
                payload = {}
                for role, bone_name in auto_sentinel_names.items():
                    matches = [
                        frame
                        for frame in getattr(vmd_data, "bone_frames", []) or []
                        if str(getattr(frame, "bone_name", "")) == bone_name
                        and int(frame.frame_number) == auto_end
                    ]
                    payload[role] = [
                        {
                            "boneName": str(frame.bone_name),
                            "frame": int(frame.frame_number),
                            "position": [float(value) for value in frame.position],
                            "rotation": [float(value) for value in frame.rotation],
                        }
                        for frame in matches
                    ]
                return payload

            def _compare_control_curve_snapshots(
                before: Mapping[str, Any], after: Mapping[str, Any]
            ) -> dict[str, Any]:
                mismatches = []
                max_value_error = 0.0
                if set(before) != set(after):
                    mismatches.append("control roles changed")
                for role in sorted(set(before) | set(after)):
                    before_curves = before.get(role, {})
                    after_curves = after.get(role, {})
                    if set(before_curves) != set(after_curves):
                        mismatches.append(f"{role}: animCurve set changed")
                    for curve in sorted(set(before_curves) | set(after_curves)):
                        before_curve = before_curves.get(curve, {})
                        after_curve = after_curves.get(curve, {})
                        if before_curve.get("times") != after_curve.get("times"):
                            mismatches.append(f"{curve}: key times changed")
                        before_values = before_curve.get("values", [])
                        after_values = after_curve.get("values", [])
                        if len(before_values) != len(after_values):
                            mismatches.append(f"{curve}: key count changed")
                            continue
                        for before_value, after_value in zip(before_values, after_values):
                            max_value_error = max(
                                max_value_error,
                                abs(float(before_value) - float(after_value)),
                            )
                return {
                    "pass": not mismatches and max_value_error <= MOVE_EPSILON,
                    "maxValueError": max_value_error,
                    "mismatches": mismatches,
                }

            auto_curve_snapshot_before = _control_curve_snapshot()
            if not any(auto_curve_snapshot_before.values()):
                raise RuntimeError("automatic export sentinel controls have no animCurves")
            auto_source_world = _joint_worlds(cmds, auto_compare_frames)
            auto_source_ik = _ik_states(cmds, auto_compare_frames)
            sentinel_effect = {
                role: max(
                    abs(last - first)
                    for first, last in zip(
                        auto_source_world[str(auto_start)][index],
                        auto_source_world[str(auto_end)][index],
                    )
                )
                for role, index in auto_sentinel_indices.items()
            }
            if any(value <= MOVE_EPSILON for value in sentinel_effect.values()):
                raise RuntimeError(
                    f"automatic export sentinel edits had no world-space effect: {sentinel_effect}"
                )
        else:
            # Keep the legacy full E2E route's diagnostics shape without
            # enabling focused sentinel assertions or frame-range edits.
            authored_controls = {
                "center": str(rig.controls["center"]),
                "left_arm": str(rig.controls["left_arm"]),
            }

            def _control_curve_snapshot() -> dict[str, Any]:
                return {}

            def _vmd_sentinel_payload(_vmd_data: Any) -> dict[str, Any]:
                return {}

            def _compare_control_curve_snapshots(
                _before: Mapping[str, Any], _after: Mapping[str, Any]
            ) -> dict[str, Any]:
                return {"pass": True, "maxValueError": 0.0, "mismatches": []}
        auto_options = {
            "export_format": "vmd",
            "export_strategy": "bake_timeline",
            "current_model_root": root,
            "target_model": root,
            "require_current_model": True,
            "require_target": True,
            "frame_range": timeline_range,
            "frame_step": 1.0,
        }
        auto_action = None
        auto_gate = {
            "status": "running",
            "outputPath": str(auto_output),
            "frameRange": list(timeline_range),
            "requestedFrameRange": list(auto_frame_range) if auto_frame_range else list(timeline_range),
            "actualFrameRange": list(timeline_range),
            "representativeFrames": list(auto_compare_frames),
            "uiHeartbeat": [],
            "warningAcknowledgement": {
                "invoked": False,
                "approved": False,
                "callbackCount": 0,
                "warnings": [],
            },
        }
        report["autoBakeExport"] = auto_gate
        if auto_bake_only:
            auto_gate["authoredSentinels"] = {
                "jointIndices": dict(auto_sentinel_indices),
                "worldMatrixEffect": sentinel_effect,
            }
        unrelated_layer = None
        unrelated_node = None
        if auto_bake_only:
            # Reproduce a production scene that retains a muted VMD_Motion-like
            # layer for another model/object. It must neither block restoration
            # nor be absorbed into this model's ownership journal.
            unrelated_node = cmds.createNode(
                "transform",
                name="MMT_AutoBake_Unrelated",
            )
            unrelated_layer = cmds.animLayer(
                "MMT_AutoBake_Unrelated_VMD_Motion",
                override=False,
                weight=0.0,
            )
            cmds.animLayer(
                unrelated_layer,
                edit=True,
                attribute=f"{unrelated_node}.translateX",
            )
            cmds.setKeyframe(
                unrelated_node,
                attribute="translateX",
                time=2.0,
                value=1.0,
                animLayer=unrelated_layer,
            )
            auto_gate["unrelatedLayer"] = {
                "name": unrelated_layer,
                "weight": float(
                    cmds.animLayer(unrelated_layer, query=True, weight=True)
                ),
            }
        try:
            auto_action = create_maya_bake_timeline_vmd_action()
            auto_service = ExportWorkflowService(vmd_action=auto_action)
            auto_gate["exportOperation"] = {"oneShot": True}

            post_preview_center_value = (
                2.5
                if auto_bake_only
                else float(
                    cmds.getAttr(
                        f"{authored_controls['center']}.translateX",
                        time=auto_end,
                    )
                )
            )
            post_preview_arm_values = (
                (30.0, -11.0, 15.0)
                if auto_bake_only
                else tuple(
                    float(
                        cmds.getAttr(
                            f"{authored_controls['left_arm']}.rotate{axis}",
                            time=auto_end,
                        )
                    )
                    for axis in "XYZ"
                )
            )
            cmds.setKeyframe(
                authored_controls["center"],
                attribute="translateX",
                time=auto_end,
                value=post_preview_center_value,
            )
            for axis, value in zip("XYZ", post_preview_arm_values):
                cmds.setKeyframe(
                    authored_controls["left_arm"],
                    attribute=f"rotate{axis}",
                    time=auto_end,
                    value=value,
                )
            cmds.dgdirty(allPlugs=True)
            cmds.refresh(force=True)
            auto_curve_snapshot_before = _control_curve_snapshot()
            auto_source_world = _joint_worlds(cmds, auto_compare_frames)
            auto_source_ik = _ik_states(cmds, auto_compare_frames)
            auto_export_payload = {}
            if unrelated_layer is not None and unrelated_node is not None:
                unrelated_attributes = cmds.animLayer(
                    unrelated_layer,
                    query=True,
                    attribute=True,
                ) or []
                unrelated_pass = any(
                    str(attribute).endswith(f"{unrelated_node}.translateX")
                    for attribute in unrelated_attributes
                )
                auto_gate["unrelatedLayer"]["preserved"] = unrelated_pass
                if not unrelated_pass:
                    raise RuntimeError(
                        "automatic Bake Timeline changed an unrelated animation layer"
                    )

            published = auto_service.execute(
                ExportWorkflowRequest(
                    str(auto_output),
                    dict(auto_options),
                ),
                warning_callback=lambda warning_report: _approve_one_shot_export_warnings(
                    warning_report, auto_gate
                ),
                progress_callback=lambda stage: auto_gate["uiHeartbeat"].append(str(stage)),
            )
            _record_one_shot_terminal_evidence(auto_gate, published, auto_output)
            published_vmd = VmdData().parse_file(str(auto_output))
            curve_restoration_pass = True
            sentinel_payload_changed = {}
            if auto_bake_only:
                auto_export_payload = _vmd_sentinel_payload(published_vmd)
                for role in auto_sentinel_names:
                    sentinel_payload_changed[role] = bool(auto_export_payload.get(role))
                auto_curve_snapshot_after = _control_curve_snapshot()
                curve_restoration = _compare_control_curve_snapshots(
                    auto_curve_snapshot_before,
                    auto_curve_snapshot_after,
                )
                curve_restoration_pass = bool(curve_restoration["pass"])
                auto_gate["controlCurves"] = {
                    "before": auto_curve_snapshot_before,
                    "after": auto_curve_snapshot_after,
                    **curve_restoration,
                    "restorationPass": curve_restoration_pass,
                }
                auto_gate["exportedSentinels"] = {
                    "payload": auto_export_payload,
                    "present": sentinel_payload_changed,
                }
            published_pass = bool(
                published.succeeded
                and auto_output.is_file()
                and published_vmd.bone_frames
                and (
                    not auto_bake_only
                    or (all(sentinel_payload_changed.values()) and curve_restoration_pass)
                )
            )
            auto_gate.update(
                {
                    "outputSha256": hashlib.sha256(auto_output.read_bytes()).hexdigest(),
                    "sectionCounts": {
                        "bones": len(published_vmd.bone_frames),
                        "morphs": len(published_vmd.morph_frames),
                        "cameras": len(published_vmd.camera_frames),
                        "lights": len(published_vmd.light_frames),
                        "shadows": len(published_vmd.shadow_frames),
                        "ik": len(published_vmd.ik_show_hide_frames),
                    },
                    "publishedBoneFrames": len(published_vmd.bone_frames),
                    "publishedParsePass": bool(published_vmd.bone_frames),
                    "pass": published_pass,
                    "ikMovePass": bool(report["ikMove"].get("pass")),
                }
            )
            if not published_pass:
                raise RuntimeError(
                    f"automatic Bake Timeline publish/parse failed: {published.error}"
                )
            if not report["ikMove"].get("pass"):
                raise RuntimeError("automatic export parity passed but IK move witnesses failed")
            if not report["ikToggle"].get("pass"):
                raise RuntimeError(
                    "automatic export parity passed but the IK toggle witness failed"
                )
            auto_gate["status"] = "pass"
        except Exception as exc:
            auto_gate["status"] = "fail"
            auto_gate["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            auto_gate["cleanupPass"] = True

        if auto_bake_only:
            cmds.file(new=True, force=True)
            fresh_root = import_mmd_file(
                str(model_path),
                options={
                    "setup_rig": True,
                    "setup_bone_orientation": True,
                    "import_physics": False,
                },
            )
            if not fresh_root:
                raise RuntimeError("fresh PMX import failed for automatic export parity")
            if not import_mmd_file(
                str(auto_output),
                options={"target_model": str(fresh_root), "pmx_path": str(model_path)},
            ):
                raise RuntimeError("fresh VMD import failed for automatic export parity")
            fresh_world = _joint_worlds(cmds, auto_compare_frames)
            fresh_ik = _ik_states(cmds, auto_compare_frames)
            if set(auto_source_world) != set(fresh_world):
                raise RuntimeError(
                    "automatic export parity frame set changed after fresh import"
                )
            for frame in auto_source_world:
                if set(auto_source_world[frame]) != set(fresh_world[frame]):
                    raise RuntimeError(
                        f"automatic export parity joint set changed at frame {frame}"
                    )
            source_solver_owned = set(
                _expand_solver_owned_joint_indices(
                    _solver_owned_joint_indices(cmds),
                    cmds,
                )
            )
            matrix_errors = [
                {
                    "error": abs(actual - expected),
                    "frame": int(frame),
                    "jointIndex": str(index),
                    "element": int(element),
                }
                for frame in sorted(auto_source_world)
                for index in sorted(auto_source_world[frame])
                if index in fresh_world.get(frame, {})
                for element, (actual, expected) in enumerate(
                    zip(auto_source_world[frame][index], fresh_world[frame][index])
                )
            ]
            error_summary = _matrix_error_summary(
                matrix_errors,
                solver_owned_indices=source_solver_owned,
            )
            non_solver = error_summary["nonSolverOwned"]
            sentinel_errors = {
                role: max(
                    (
                        item["error"]
                        for item in matrix_errors
                        if item["jointIndex"] == index
                    ),
                    default=float("inf"),
                )
                for role, index in auto_sentinel_indices.items()
            }
            parity_pass = bool(
                matrix_errors
                and non_solver["jointCount"] > 0
                and non_solver["maxWorldMatrixError"] < ROUNDTRIP_MATRIX_EPSILON
                and error_summary["solverOwned"]["maxWorldMatrixError"]
                < ROUNDTRIP_MATRIX_EPSILON
                and all(
                    error < ROUNDTRIP_MATRIX_EPSILON
                    for error in sentinel_errors.values()
                )
                and auto_source_ik == fresh_ik
            )
            auto_gate["authoredRoundtrip"] = {
                "exportOutputSha256": auto_gate["outputSha256"],
                "frames": list(auto_compare_frames),
                "matrixErrorMetric": "max_abs_element",
                "nonSolverOwned": non_solver,
                "solverOwned": error_summary["solverOwned"],
                "sentinelErrors": sentinel_errors,
                "ikStatesEqual": auto_source_ik == fresh_ik,
                "pass": parity_pass,
            }
            if not parity_pass:
                raise RuntimeError(
                    "automatic Control Rig VMD authored-motion parity exceeded the numeric gate"
                )
            if not _focused_witnesses_pass(report):
                raise RuntimeError(
                    "required Control Rig witnesses did not all pass before final status"
                )
            report["internalOracle"] = {
                "identity": "maya_vmd_export_reimport_authored_parity",
                "status": "pass",
                "solverOwnedDriftDelegatedToExternalOracle": False,
            }
            report["status"] = "pass"
            _log("PASS: focused automatic Bake Timeline authored-motion parity passed")
            return

        baked_metadata = bake_mmd_control_rig(root)
        report["states"]["afterBake"] = baked_metadata.get("state")
        if baked_metadata.get("state") != CONTROL_RIG_BAKED:
            raise RuntimeError(f"Bake did not produce BAKED state: {baked_metadata.get('state')}")
        baked_cycle = _cycle_state("after_bake", cmds)
        report["cycles"].append(baked_cycle)

        scene_file = Path(scene_path)
        scene_file.parent.mkdir(parents=True, exist_ok=True)
        cmds.file(rename=str(scene_file))
        cmds.file(save=True, force=True, type="mayaAscii")
        cmds.file(str(scene_file), open=True, force=True)
        reopened_root = _find_rig_root(cmds)
        reopened_metadata = read_mmd_control_rig_metadata(reopened_root)
        report["states"]["afterReopen"] = reopened_metadata.get("state") if reopened_metadata else None
        if not reopened_metadata or reopened_metadata.get("state") != CONTROL_RIG_BAKED:
            raise RuntimeError("save/reopen did not preserve BAKED control-rig metadata")
        reopened_cycle = _cycle_state("after_reopen", cmds)
        report["cycles"].append(reopened_cycle)

        source_world = _joint_worlds(cmds, ROUNDTRIP_FRAMES)
        source_ik = _ik_states(cmds, ROUNDTRIP_FRAMES)
        source_solver_owned_direct = _solver_owned_joint_indices(cmds)
        source_solver_owned = _expand_solver_owned_joint_indices(
            source_solver_owned_direct,
            cmds,
        )
        collected = VmdSceneCollector().collect({"target_model": reopened_root})
        output_vmd = Path(exported_vmd_path)
        output_vmd.parent.mkdir(parents=True, exist_ok=True)
        VmdExporter().export_vmd_animation(str(output_vmd), collected)
        parsed_vmd = VmdData().parse_file(str(output_vmd))
        report["roundtrip"]["exportedBoneFrames"] = len(parsed_vmd.bone_frames)
        report["roundtrip"]["exportedIkFrames"] = len(parsed_vmd.ik_show_hide_frames)
        if not output_vmd.is_file() or not parsed_vmd.bone_frames:
            raise RuntimeError("VMD export produced no bone frames")

        cmds.file(new=True, force=True)
        fresh_root = import_mmd_file(
            str(model_path),
            options={
                "setup_rig": True,
                "setup_bone_orientation": True,
                "import_physics": False,
            },
        )
        if not fresh_root:
            raise RuntimeError("fresh PMX import failed for VMD round-trip")
        if not import_mmd_file(
            str(output_vmd),
            options={"target_model": str(fresh_root), "pmx_path": str(model_path)},
        ):
            raise RuntimeError("fresh VMD import failed for VMD round-trip")
        fresh_world = _joint_worlds(cmds, ROUNDTRIP_FRAMES)
        fresh_ik = _ik_states(cmds, ROUNDTRIP_FRAMES)
        fresh_solver_owned_direct = _solver_owned_joint_indices(cmds)
        fresh_solver_owned = _expand_solver_owned_joint_indices(
            fresh_solver_owned_direct,
            cmds,
        )
        source_frame_keys = set(source_world)
        fresh_frame_keys = set(fresh_world)
        if source_frame_keys != fresh_frame_keys:
            raise RuntimeError(
                "round-trip frame key set mismatch: "
                f"source={sorted(source_frame_keys)} fresh={sorted(fresh_frame_keys)}"
            )
        for frame in sorted(source_frame_keys):
            source_indices = set(source_world[frame])
            fresh_indices = set(fresh_world[frame])
            if source_indices != fresh_indices:
                raise RuntimeError(
                    f"round-trip joint-index set mismatch at frame {frame}: "
                    f"source={sorted(source_indices)} fresh={sorted(fresh_indices)}"
                )
            for index in sorted(source_indices):
                if len(source_world[frame][index]) != len(fresh_world[frame][index]):
                    raise RuntimeError(
                        f"round-trip matrix length mismatch at frame={frame} index={index}"
                    )
        if set(source_solver_owned) != set(fresh_solver_owned):
            raise RuntimeError(
                "round-trip solver-owned joint set mismatch: "
                f"source={sorted(source_solver_owned)} fresh={sorted(fresh_solver_owned)}"
            )
        if set(source_solver_owned_direct) != set(fresh_solver_owned_direct):
            raise RuntimeError(
                "round-trip direct solver-owned joint set mismatch: "
                f"source={sorted(source_solver_owned_direct)} "
                f"fresh={sorted(fresh_solver_owned_direct)}"
            )
        matrix_error_locations = [
            {
                "error": abs(actual - expected),
                "frame": int(frame),
                "jointIndex": str(index),
                "element": int(element),
                "source": actual,
                "fresh": expected,
            }
            for frame in sorted(source_frame_keys)
            for index in sorted(source_world[frame])
            for element, (actual, expected) in enumerate(
                zip(source_world[frame][index], fresh_world[frame][index])
            )
        ]
        matrix_errors = [item["error"] for item in matrix_error_locations]
        max_matrix_error = max(matrix_errors, default=0.0)
        error_summary = _matrix_error_summary(
            matrix_error_locations,
            solver_owned_indices=set(source_solver_owned),
        )
        non_solver_summary = error_summary["nonSolverOwned"]
        solver_summary = error_summary["solverOwned"]
        authored_pass = bool(
            matrix_errors
            and non_solver_summary["jointCount"] > 0
            and non_solver_summary["maxWorldMatrixError"] < ROUNDTRIP_MATRIX_EPSILON
            and source_ik == fresh_ik
        )
        report["roundtrip"].update(
            {
                "frames": list(ROUNDTRIP_FRAMES),
                "maxWorldMatrixError": max_matrix_error,
                "matrixErrorMetric": "max_abs_element",
                "maxWorldMatrixErrorLocation": max(
                    matrix_error_locations,
                    key=lambda item: item["error"],
                    default=None,
                ),
                "maxWorldMatrixErrorByFrame": {
                    str(frame): max(
                        (
                            item["error"]
                            for item in matrix_error_locations
                            if item["frame"] == int(frame)
                        ),
                        default=0.0,
                    )
                    for frame in sorted(source_frame_keys)
                },
                "solverOwnedJointIndices": sorted(source_solver_owned),
                "directSolverOwnedJointIndices": sorted(source_solver_owned_direct),
                "solverOwnedJoints": source_solver_owned,
                "nonSolverOwned": non_solver_summary,
                "solverOwned": solver_summary,
                "ikStatesEqual": source_ik == fresh_ik,
                "sourceIkStates": source_ik,
                "freshIkStates": fresh_ik,
                "authoredParityPass": authored_pass,
                "solverDriftDelegatedToExternalOracle": bool(
                    solver_summary["maxWorldMatrixError"] >= ROUNDTRIP_MATRIX_EPSILON
                ),
                "pass": authored_pass,
            }
        )
        report["internalOracle"] = {
            "identity": "maya_vmd_export_reimport_authored_parity",
            "status": "pass" if authored_pass else "fail",
            "solverOwnedDriftDelegatedToExternalOracle": report["roundtrip"][
                "solverDriftDelegatedToExternalOracle"
            ],
        }
        _log(
            "round-trip: boneFrames=%d ikFrames=%d nonSolverMax=%.8f solverMax=%.8f ikEqual=%s"
            % (
                report["roundtrip"]["exportedBoneFrames"],
                report["roundtrip"]["exportedIkFrames"],
                non_solver_summary["maxWorldMatrixError"],
                solver_summary["maxWorldMatrixError"],
                source_ik == fresh_ik,
            )
        )
        if not authored_pass:
            raise RuntimeError("VMD authored-channel parity exceeded the numeric gate")

        final_cycle = _cycle_state("after_roundtrip", cmds)
        report["cycles"].append(final_cycle)
        if any(not bool(state.get("evaluationOn")) for state in report["cycles"]):
            raise RuntimeError("cycleCheck evaluation must remain enabled for every gate")
        baseline_plugs = set(baseline_cycle["cyclePlugs"])
        new_cycles = sorted(
            plug
            for state in report["cycles"]
            for plug in set(state["cyclePlugs"]) - baseline_plugs
        )
        report["newCyclePlugs"] = new_cycles
        if new_cycles:
            raise RuntimeError(f"new DG cycles detected: {new_cycles}")

        report["status"] = "pass"
        _log("PASS: MMD control-rig GUI E2E numeric gates passed")
    except Exception:
        report["errors"].append(traceback.format_exc())
        _log(f"EXCEPTION:\n{traceback.format_exc()}")
    finally:
        _write_maya_report(report_file, report)
        _log(f"RESULT_JSON: {json.dumps(report, ensure_ascii=False, sort_keys=True)}")
        _log(COMPLETION_MARKER)
        if dll_directory_handle is not None:
            try:
                dll_directory_handle.close()
            except Exception:
                pass


def _repo_path(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if _PROJECT_ROOT not in path.parents and path != _PROJECT_ROOT:
        raise ValueError(f"path must stay inside repository: {path}")
    return path


def _input_path(value: str) -> Path:
    """Resolve a PMX/VMD input, including explicitly supplied local assets."""

    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"input file does not exist: {path}")
    return path


# ===================================================================
# Host-side: launch a fresh GUI process and drive commandPort
# ===================================================================
def main() -> int:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    parser = argparse.ArgumentParser(description="MMD-native control-rig Maya GUI E2E")
    parser.add_argument("--maya", default="2026")
    parser.add_argument(
        "--model",
        default=str(_PROJECT_ROOT / "tests" / "data" / "mmt_test_model.pmx"),
    )
    parser.add_argument(
        "--motion",
        default=str(_PROJECT_ROOT / "tests" / "data" / "mmt_test_model_test_motion.vmd"),
    )
    parser.add_argument("--port", type=int, default=COMMAND_PORT)
    parser.add_argument("--timeout", type=float, default=TEST_TIMEOUT)
    parser.add_argument(
        "--evaluation-mode",
        choices=EVALUATION_MODE_CHOICES,
        default="default",
        help="Maya evaluation mode (default preserves the current Maya setting)",
    )
    parser.add_argument(
        "--create-on-import",
        action="store_true",
        help=(
            "Create or reuse the MMD Control Rig during VMD import, key "
            "controllers directly, and clear existing motion"
        ),
    )
    parser.add_argument(
        "--auto-bake-only",
        action="store_true",
        help=(
            "Run the Control Rig edits and automatic Bake Timeline export gate, "
            "then stop before manual bake and round-trip gates"
        ),
    )
    parser.add_argument(
        "--cpp-config",
        choices=("Debug", "Release"),
        default="Debug",
        help="C++ plugin configuration used by the Maya E2E process",
    )
    parser.add_argument(
        "--ffi-path",
        default=None,
        help="Explicit mmd_runtime_ffi.dll path used inside the Maya process",
    )
    parser.add_argument(
        "--auto-frame-range",
        nargs=2,
        type=int,
        metavar=("START", "END"),
        default=None,
        help="Inclusive Control Rig Bake Timeline range (requires --auto-bake-only)",
    )
    parser.add_argument("--out-dir", default=str(_PROJECT_ROOT / "build" / "e2e"))
    args = parser.parse_args()

    out_dir = _repo_path(args.out_dir)
    ffi_path = _repo_path(args.ffi_path) if args.ffi_path else None
    if args.auto_frame_range is not None and not args.auto_bake_only:
        parser.error("--auto-frame-range requires --auto-bake-only")
    auto_frame_range = (
        (int(args.auto_frame_range[0]), int(args.auto_frame_range[1]))
        if args.auto_frame_range is not None
        else None
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    mode_suffix = "" if args.evaluation_mode == "default" else f"_{args.evaluation_mode}"
    route_suffix = "_create_on_import" if args.create_on_import else ""
    focused_suffix = "_auto_bake_only" if args.auto_bake_only else ""
    frame_suffix = (
        f"_frames_{auto_frame_range[0]}_{auto_frame_range[1]}"
        if auto_frame_range
        else ""
    )
    config_suffix = "" if args.cpp_config == "Debug" else f"_{args.cpp_config.lower()}"
    output_suffix = f"{mode_suffix}{route_suffix}{focused_suffix}{frame_suffix}{config_suffix}"
    report_path = out_dir / f"mmd_control_rig_e2e_maya{args.maya}{output_suffix}.json"
    log_path = out_dir / f"mmd_control_rig_e2e_maya{args.maya}{output_suffix}.log"
    scene_path = out_dir / f"mmd_control_rig_e2e_maya{args.maya}{output_suffix}.ma"
    exported_vmd_path = out_dir / f"mmd_control_rig_e2e_maya{args.maya}{output_suffix}.vmd"
    auto_exported_vmd_path = exported_vmd_path.with_suffix(".auto_bake.vmd")
    model_path = _input_path(args.model)
    motion_path = _input_path(args.motion)
    try:
        model_posix = model_path.as_posix()
        motion_posix = motion_path.as_posix()
        ffi_command_arg = repr(ffi_path.as_posix() if ffi_path else None)
        auto_frame_command_arg = repr(auto_frame_range)
        command = (
            "import sys\n"
            "from pathlib import Path\n"
            f"project_root = Path(r'{_PROJECT_ROOT.as_posix()}')\n"
            "if str(project_root) not in sys.path:\n"
            "    sys.path.insert(0, str(project_root))\n"
            "from tests.viewport.e2e_mmd_control_rig import run_e2e_check\n"
            f"run_e2e_check(r'{log_path.as_posix()}', r'{model_posix}', r'{motion_posix}', r'{report_path.as_posix()}', r'{scene_path.as_posix()}', r'{exported_vmd_path.as_posix()}', r'{args.evaluation_mode}', {bool(args.create_on_import)!r}, {bool(args.auto_bake_only)!r}, r'{args.cpp_config}', {ffi_command_arg}, {auto_frame_command_arg})\n"
        )
        report = run_maya_e2e(
            project_root=_PROJECT_ROOT,
            version=args.maya,
            out_dir=out_dir,
            port=args.port,
            timeout=args.timeout,
            log_path=log_path,
            report_path=report_path,
            command=command,
            marker=COMPLETION_MARKER,
            send_label="<mmd-control-rig-e2e>",
            stale_paths=[
                log_path,
                report_path,
                scene_path,
                exported_vmd_path,
                auto_exported_vmd_path,
            ],
            port_error=(
                f"commandPort :{args.port} is already open; refusing to attach; choose a free port"
            ),
            report_error=f"timed out waiting for file: {report_path}",
            log_ready=logger,
            warn_detached=True,
        )
        logger.info("MMD control-rig E2E status: %s", report.get("status"))
        logger.info("report: %s", report_path)
        if report.get("errors"):
            for error in report["errors"]:
                logger.error("%s", str(error)[-1000:])
        return 0 if report.get("status") == "pass" else 1
    except (FileNotFoundError, TimeoutError, RuntimeError, ValueError) as exc:
        blocked = {
            "kind": "mmd-control-rig-gui-e2e",
            "status": "blocked",
            "maya": args.maya,
            "port": args.port,
            "evaluationMode": args.evaluation_mode,
            "autoBakeOnly": bool(args.auto_bake_only),
            "cppConfig": args.cpp_config,
            "ffiPath": str(ffi_path) if ffi_path else None,
            "autoFrameRange": list(auto_frame_range) if auto_frame_range else None,
            "error": str(exc),
        }
        _write_maya_report(report_path, blocked)
        logger.error("MMD control-rig GUI E2E blocked: %s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
