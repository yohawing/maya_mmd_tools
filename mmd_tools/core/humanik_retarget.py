"""Non-UI HumanIK source switching and S0 connection diagnostics.

The helpers in this module intentionally stop at direct source connection and
evidence collection.  They do not mute MMD constraints, bake animation, or
create proxy skeletons; those ownership operations belong to later retarget
slices.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from mmd_tools.core.humanik_builder import ensure_humanik_mel_loaded
from mmd_tools.core.humanik_resolver import HumanIkBoneAssignment, HumanIkResolveResult


HUMANIK_DIRECT_INPUT_TYPE = 3
DEFAULT_HUMANIK_CHANNELS = (
    "translateX",
    "translateY",
    "translateZ",
    "rotateX",
    "rotateY",
    "rotateZ",
)


def connect_humanik_source(
    target_character: str,
    source_character: str,
    mel_module=None,
    expected_input_type: int = HUMANIK_DIRECT_INPUT_TYPE,
    require_connected: bool = True,
) -> Dict[str, Any]:
    """Connect a source HIK character to a target without touching the UI.

    Args:
        target_character: Character receiving the retargeted motion.
        source_character: Character providing the source motion.
        mel_module: Optional Maya ``mel`` compatible module for tests.
        expected_input_type: Maya HIK input type expected after connecting.
        require_connected: Raise when Maya's source readback does not match.

    Returns:
        JSON-serialisable connection report including input type and source.

    Raises:
        RuntimeError: If Maya does not report the requested direct connection.
    """
    mel = mel_module or _maya_mel()
    ensure_humanik_mel_loaded(mel)
    command = f"hikSetCharacterInput({_mel_string(target_character)}, {_mel_string(source_character)});"
    mel.eval(command)

    input_type = _as_int(mel.eval(f"hikGetInputType({_mel_string(target_character)})"), default=-1)
    connected_source = str(
        mel.eval(f"hikGetRetargetCharacterInput({_mel_string(target_character)})") or ""
    )
    report = {
        "targetCharacter": str(target_character),
        "sourceCharacter": str(source_character),
        "inputType": input_type,
        "expectedInputType": int(expected_input_type),
        "inputTypeName": _input_type_name(input_type),
        "retargetCharacterInput": connected_source,
        "retargetConnected": input_type == int(expected_input_type)
        and connected_source == str(source_character),
        "command": command,
    }
    if require_connected and not report["retargetConnected"]:
        raise RuntimeError(f"HumanIK source connection failed: {report}")
    return report


def collect_humanik_incoming_writer_census(
    assignments: HumanIkResolveResult | Iterable[HumanIkBoneAssignment],
    cmds_module=None,
    channels: Sequence[str] = DEFAULT_HUMANIK_CHANNELS,
) -> List[Dict[str, Any]]:
    """Collect deterministic incoming writers for every mapped HIK joint.

    The result is flat by destination channel so it can be written directly as
    JSONL and diffed between pre-HIK and post-HIK snapshots.
    """
    cmds = cmds_module or _maya_cmds()
    rows: List[Dict[str, Any]] = []
    for assignment in _normalise_assignments(assignments):
        for channel in channels:
            destination = f"{assignment.joint}.{channel}"
            writers = _incoming_writers(cmds, destination)
            rows.append(
                {
                    "joint": str(assignment.joint),
                    "hikBone": str(assignment.hik_bone),
                    "hikIndex": int(assignment.hik_index),
                    "channel": str(channel),
                    "destination": destination,
                    "writers": sorted(set(str(writer) for writer in writers)),
                }
            )
    channel_order = {str(channel): index for index, channel in enumerate(channels)}
    rows.sort(key=lambda row: (row["hikIndex"], row["joint"], channel_order.get(row["channel"], len(channel_order))))
    return rows


def snapshot_humanik_connections(
    assignments: HumanIkResolveResult | Iterable[HumanIkBoneAssignment],
    cmds_module=None,
    channels: Sequence[str] = DEFAULT_HUMANIK_CHANNELS,
) -> Dict[str, List[str]]:
    """Return destination-to-writer connections for an HIK assignment set."""
    census = collect_humanik_incoming_writer_census(assignments, cmds_module=cmds_module, channels=channels)
    return {row["destination"]: list(row["writers"]) for row in census}


def diff_humanik_connections(
    before: Mapping[str, Sequence[str]],
    after: Mapping[str, Sequence[str]],
) -> List[Dict[str, Any]]:
    """List all HIK connection additions, removals, and replacements.

    Existing writers removed by HIK are explicitly present in ``disconnected``;
    an empty list is therefore meaningful evidence rather than an omission.
    """
    rows: List[Dict[str, Any]] = []
    for destination in sorted(set(before) | set(after)):
        old = sorted(set(str(value) for value in before.get(destination, ())))
        new = sorted(set(str(value) for value in after.get(destination, ())))
        disconnected = sorted(set(old) - set(new))
        connected = sorted(set(new) - set(old))
        if not disconnected and not connected:
            continue
        rows.append(
            {
                "destination": destination,
                "before": old,
                "after": new,
                "disconnected": disconnected,
                "connected": connected,
                "replaced": bool(disconnected and connected),
            }
        )
    return rows


def verify_root_locomotion(
    driver_joint: str,
    affected_joints: Mapping[str, Sequence[str]] | Sequence[str],
    translation: Sequence[float] = (1.0, 0.0, 0.0),
    cmds_module=None,
    tolerance: float = 1.0e-4,
    observed_root_joint: Optional[str] = None,
) -> Dict[str, Any]:
    """Drive source Hips and compare observed world-matrix motion.

    ``affected_joints`` may be a flat sequence or a mapping such as
    ``{"upperBody": [...], "lowerBody": [...], "legs": [...]}``.
    ``observed_root_joint`` defaults to ``driver_joint`` for hierarchy-only
    probes.  A retarget probe passes source Hips as the driver and target Hips
    as the observed root.  The authored driver translation is restored in a
    ``finally`` block.
    """
    cmds = cmds_module or _maya_cmds()
    delta = tuple(float(value) for value in translation)
    if len(delta) != 3:
        raise ValueError("translation must contain exactly three values")
    observed_root = observed_root_joint or driver_joint
    groups = _normalise_groups(observed_root, affected_joints)
    joints = [observed_root] + [joint for values in groups.values() for joint in values]
    joints = list(dict.fromkeys(joints))
    before_matrices = {joint: _world_matrix(cmds, joint) for joint in joints}
    before = {joint: _matrix_translation(matrix) for joint, matrix in before_matrices.items()}
    original = _get_vector_attr(cmds, driver_joint, "translate")
    try:
        cmds.setAttr(
            f"{driver_joint}.translate",
            original[0] + delta[0],
            original[1] + delta[1],
            original[2] + delta[2],
            type="double3",
        )
        _force_evaluation(cmds)
        after_matrices = {joint: _world_matrix(cmds, joint) for joint in joints}
        after = {joint: _matrix_translation(matrix) for joint, matrix in after_matrices.items()}
    finally:
        cmds.setAttr(
            f"{driver_joint}.translate",
            original[0],
            original[1],
            original[2],
            type="double3",
        )
        _force_evaluation(cmds)

    deltas = {joint: _vector_delta(before[joint], after[joint]) for joint in joints}
    root_delta = deltas[observed_root]
    group_report = {
        name: {
            "joints": list(values),
            "deltas": {joint: deltas[joint] for joint in values},
            "passed": all(_close_vector(deltas[joint], root_delta, tolerance) for joint in values),
        }
        for name, values in groups.items()
    }
    root_motion_passed = _root_motion_matches_direction(root_delta, delta, tolerance)
    passed = root_motion_passed and all(
        report["passed"] for report in group_report.values()
    )
    return {
        "driverJoint": str(driver_joint),
        "rootJoint": str(observed_root),
        "translation": list(delta),
        "rootDelta": root_delta,
        "rootMotionPassed": bool(root_motion_passed),
        "tolerance": float(tolerance),
        "passed": bool(passed),
        "beforeWorldMatrix": before_matrices,
        "afterWorldMatrix": after_matrices,
        "beforeWorldTranslation": before,
        "afterWorldTranslation": after,
        "deltas": deltas,
        "groups": group_report,
    }


def build_humanik_writer_report(
    assignments: HumanIkResolveResult | Iterable[HumanIkBoneAssignment],
    cmds_module=None,
    channels: Sequence[str] = DEFAULT_HUMANIK_CHANNELS,
) -> Dict[str, Any]:
    """Wrap the writer census in a stable JSON report object."""
    rows = collect_humanik_incoming_writer_census(assignments, cmds_module=cmds_module, channels=channels)
    return {"channels": list(channels), "rows": rows, "rowCount": len(rows)}


def _normalise_assignments(
    assignments: HumanIkResolveResult | Iterable[HumanIkBoneAssignment],
) -> List[HumanIkBoneAssignment]:
    if isinstance(assignments, HumanIkResolveResult):
        values = list(assignments.assignments)
    else:
        values = list(assignments)
    return sorted(values, key=lambda item: (int(item.hik_index), str(item.joint)))


def _normalise_groups(
    root_joint: str,
    affected_joints: Mapping[str, Sequence[str]] | Sequence[str],
) -> Dict[str, List[str]]:
    if isinstance(affected_joints, Mapping):
        return {str(name): [str(joint) for joint in values] for name, values in sorted(affected_joints.items())}
    return {"affected": [str(joint) for joint in affected_joints]}


def _incoming_writers(cmds, destination: str) -> List[str]:
    try:
        return list(cmds.listConnections(destination, source=True, destination=False, plugs=True) or [])
    except TypeError:
        return list(cmds.listConnections(destination, s=True, d=False, p=True) or [])


def _world_matrix(cmds, joint: str) -> List[float]:
    values = cmds.xform(joint, query=True, worldSpace=True, matrix=True) or ()
    if len(values) != 16:
        raise RuntimeError(f"Expected a 16-value world matrix for joint: {joint}")
    return [float(value) for value in values]


def _matrix_translation(matrix: Sequence[float]) -> List[float]:
    return [float(matrix[index]) for index in (12, 13, 14)]


def _get_vector_attr(cmds, node: str, attr: str) -> List[float]:
    value = cmds.getAttr(f"{node}.{attr}")
    if isinstance(value, (tuple, list)) and value and isinstance(value[0], (tuple, list)):
        value = value[0]
    return [float(component) for component in (value or (0.0, 0.0, 0.0))[:3]]


def _force_evaluation(cmds) -> None:
    dgdirty = getattr(cmds, "dgdirty", None)
    if dgdirty is not None:
        try:
            dgdirty(allPlugs=True)
        except (TypeError, RuntimeError):
            pass
    refresh = getattr(cmds, "refresh", None)
    if refresh is not None:
        try:
            refresh(force=True)
        except (TypeError, RuntimeError):
            pass


def _vector_delta(before: Sequence[float], after: Sequence[float]) -> List[float]:
    return [float(after[index] - before[index]) for index in range(3)]


def _close_vector(left: Sequence[float], right: Sequence[float], tolerance: float) -> bool:
    return all(abs(float(left[index]) - float(right[index])) <= tolerance for index in range(3))


def _root_motion_matches_direction(
    observed: Sequence[float],
    requested: Sequence[float],
    tolerance: float,
) -> bool:
    for observed_value, requested_value in zip(observed, requested):
        if abs(float(requested_value)) <= tolerance:
            if abs(float(observed_value)) > tolerance:
                return False
            continue
        if abs(float(observed_value)) <= tolerance:
            return False
        if float(observed_value) * float(requested_value) <= 0.0:
            return False
    return True


def _input_type_name(value: int) -> str:
    return {
        -1: "none",
        0: "stance",
        1: "rig",
        2: "layered",
        3: "direct",
        4: "live",
    }.get(value, "unknown")


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _mel_string(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _maya_cmds():
    from maya import cmds

    return cmds


def _maya_mel():
    from maya import mel

    return mel
