"""Non-UI HumanIK source switching and S0 connection diagnostics.

The helpers in this module intentionally stop at direct source connection and
evidence collection.  They do not mute MMD constraints, bake animation, or
create proxy skeletons; those ownership operations belong to later retarget
slices.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from mmd_tools.core.humanik_builder import ensure_humanik_mel_loaded, get_humanik_definition_lock_state
from mmd_tools.core.humanik_resolver import HumanIkBoneAssignment, HumanIkResolveResult
from mmd_tools.core.humanik_utils import maya_cmds, maya_mel, mel_string
from mmd_tools.services.scene_model_service import SceneModelService


HUMANIK_DIRECT_INPUT_TYPE = 3
HIK_CHARACTER_NODE_TYPE = "HIKCharacterNode"
DEFAULT_HUMANIK_CHANNELS = (
    "translateX",
    "translateY",
    "translateZ",
    "rotateX",
    "rotateY",
    "rotateZ",
)
_TRANSLATE_CHANNELS = ("translateX", "translateY", "translateZ")


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
    mel = mel_module or maya_mel()
    ensure_humanik_mel_loaded(mel)
    command = f"hikSetCharacterInput({mel_string(target_character)}, {mel_string(source_character)});"
    mel.eval(command)

    input_type = _as_int(mel.eval(f"hikGetInputType({mel_string(target_character)})"), default=-1)
    connected_source = str(
        mel.eval(f"hikGetRetargetCharacterInput({mel_string(target_character)})") or ""
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


@dataclass(frozen=True)
class HumanIkImportLock:
    """Scene-fact snapshot of whether HumanIK currently owns a model.

    ``blocked`` names the fail-closed reason a mutating operation (such as
    VMD import) must be refused: ``"target_preview"`` when a retarget input
    is connected to the model's character, or ``"control_rig"`` when a
    Control Rig exists for it.  Any other scene-fact state -- no
    ``HIKCharacterNode`` connected to the model at all, or characterized
    with neither an input source nor a control rig (NEUTRAL/SOURCE) -- reports
    ``blocked=None`` and the caller should permit the operation.
    """

    blocked: Optional[str]
    character: Optional[str]
    input_source: str = ""
    has_control_rig: bool = False


def find_humanik_character_for_model(model_root: str, cmds_module=None) -> Optional[str]:
    """Return the ``HIKCharacterNode`` characterizing a model, from scene facts alone.

    This intentionally does not consult any in-memory session/binding state
    (``HumanIkFrontendSession`` instances are UI-layer and may not exist, for
    example after a fresh Python session or plain Maya UI use).  It walks
    ``model_root`` and its joint descendants and returns the first
    ``HIKCharacterNode`` Maya reports as connected to any of them.

    Args:
        model_root: Model root or joint path to inspect.
        cmds_module: Optional Maya ``cmds`` compatible module for tests.

    Returns:
        The character node name, or ``None`` for an uncharacterized model, a
        missing node, or any Maya query failure.  Callers building a
        fail-closed gate should treat ``None`` as "nothing to block" -- see
        :func:`describe_humanik_import_lock`.
    """
    if not model_root:
        return None
    cmds = cmds_module or maya_cmds()
    try:
        if not bool(cmds.pluginInfo("mayaHIK", query=True, loaded=True)):
            return None
    except Exception:
        return None
    try:
        nodes = _model_hierarchy_nodes(cmds, model_root)
    except Exception:
        return None
    for node in nodes:
        try:
            characters = cmds.listConnections(node, type=HIK_CHARACTER_NODE_TYPE) or []
        except Exception:
            continue
        for character in characters:
            if character:
                return str(character)
    return None


def describe_humanik_import_lock(
    model_root: str,
    cmds_module=None,
    mel_module=None,
) -> HumanIkImportLock:
    """Detect from scene facts whether a model is a HumanIK TARGET/Control Rig.

    VMD import must stay permitted while a HumanIK-characterized model is in
    NEUTRAL (no ``HIKCharacterNode``) or SOURCE (characterized, but with no
    retarget input connected and no Control Rig -- the read-only state used
    while another model previews against it).  It must be refused fail-closed
    while the model is itself a TARGET preview (``hikGetRetargetCharacterInput``
    reports a connected source) or has an active Control Rig
    (``hikHasControlRig``): both derive the model's pose from HumanIK, so a
    VMD import underneath it would silently disagree with the visible pose
    and with a later ``Restore MMD Rig``.

    Detection never raises: a missing HumanIK plugin/MEL, an uncharacterized
    model, or any Maya query failure returns an unblocked result so VMD
    import keeps working without a hard HumanIK runtime dependency.  Only an
    explicit TARGET/Control Rig scene fact returns ``blocked``.

    Args:
        model_root: Model root or joint path to inspect.
        cmds_module: Optional Maya ``cmds`` compatible module for tests.
        mel_module: Optional Maya ``mel`` compatible module for tests.

    Returns:
        A :class:`HumanIkImportLock` describing the detected state.
    """
    character = find_humanik_character_for_model(model_root, cmds_module=cmds_module)
    if not character:
        return HumanIkImportLock(blocked=None, character=None)
    try:
        mel = mel_module or maya_mel()
        ensure_humanik_mel_loaded(mel)
    except Exception:
        return HumanIkImportLock(blocked=None, character=character)
    try:
        has_control_rig = bool(mel.eval(f"hikHasControlRig({mel_string(character)})"))
    except Exception:
        has_control_rig = False
    try:
        input_source = str(
            mel.eval(f"hikGetRetargetCharacterInput({mel_string(character)})") or ""
        )
    except Exception:
        input_source = ""
    if has_control_rig:
        return HumanIkImportLock("control_rig", character, input_source, True)
    if input_source.strip():
        return HumanIkImportLock("target_preview", character, input_source, False)
    return HumanIkImportLock(None, character, input_source, False)


def list_scene_hik_characters(
    cmds_module=None,
    mel_module=None,
) -> List[Dict[str, Any]]:
    """Enumerate every ``HIKCharacterNode`` in the scene as scene facts.

    Used by ``HumanIkFrontendSession.enter_external_source_mode`` (and any UI
    that needs to offer a SOURCE picker) to distinguish MMD-characterized
    characters -- which already have a dedicated ``enter_source_mode`` path
    keyed by model root -- from external/mocap characters characterized
    outside mmd_tools, and to report whether each character's definition is
    locked (the external-source precondition).

    This intentionally never consults ``HumanIkFrontendSession`` state: it is
    a pure scene-fact scan, so it keeps working after a fresh Python session
    or plain Maya HumanIK UI use, matching ``find_humanik_character_for_model``.

    Args:
        cmds_module: Optional Maya ``cmds`` compatible module for tests.
        mel_module: Optional Maya ``mel`` compatible module for tests.

    Returns:
        A list of ``{"character", "isMmd", "modelRoot", "locked"}`` dicts,
        one per ``HIKCharacterNode`` found, sorted by character name.  Any
        Maya query failure (missing plugin, non-Maya test process) returns an
        empty list rather than raising -- fail-soft, matching every other
        scene-fact helper in this module.
    """
    cmds = cmds_module or maya_cmds()
    try:
        characters = sorted(str(item) for item in (cmds.ls(type=HIK_CHARACTER_NODE_TYPE) or []))
    except Exception:
        return []
    mmd_character_to_model: Dict[str, str] = {}
    try:
        model_roots = SceneModelService(cmds_module=cmds).list_mmd_models()
    except Exception:
        model_roots = []
    for model_root in model_roots:
        try:
            character = find_humanik_character_for_model(model_root, cmds_module=cmds)
        except Exception:
            continue
        if character:
            mmd_character_to_model[character] = model_root
    rows: List[Dict[str, Any]] = []
    for character in characters:
        try:
            locked = bool(get_humanik_definition_lock_state(character, mel_module=mel_module))
        except Exception:
            locked = False
        model_root = mmd_character_to_model.get(character)
        rows.append(
            {
                "character": character,
                "isMmd": model_root is not None,
                "modelRoot": model_root,
                "locked": locked,
            }
        )
    return rows


def _model_hierarchy_nodes(cmds, model_root: str) -> List[str]:
    """Return ``model_root`` plus its descendant joints, long-named and deduped."""
    if not cmds.objExists(model_root):
        return []
    nodes = [model_root]
    nodes.extend(
        cmds.listRelatives(model_root, allDescendents=True, fullPath=True, type="joint") or []
    )
    seen = set()
    result: List[str] = []
    for node in nodes:
        long_names = cmds.ls(node, long=True) or [node]
        name = str(long_names[0])
        if name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def collect_humanik_incoming_writer_census(
    assignments: HumanIkResolveResult | Iterable[HumanIkBoneAssignment],
    cmds_module=None,
    channels: Sequence[str] = DEFAULT_HUMANIK_CHANNELS,
) -> List[Dict[str, Any]]:
    """Collect deterministic incoming writers for every mapped HIK joint.

    The result is flat by destination channel so it can be written directly as
    JSONL and diffed between pre-HIK and post-HIK snapshots.
    """
    cmds = cmds_module or maya_cmds()
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
    source_model_root: Optional[str] = None,
) -> Dict[str, Any]:
    """Probe root locomotion through a writable scalar source channel.

    ``affected_joints`` may be a flat sequence or a mapping such as
    ``{"upperBody": [...], "lowerBody": [...], "legs": [...]}``.
    ``observed_root_joint`` defaults to ``driver_joint`` for hierarchy-only
    probes.  A retarget probe passes source Hips as the driver and target Hips
    as the observed root.  Candidate source drivers are the Hips joint and its
    ancestors through ``source_model_root`` (or the topmost parent when no
    root is supplied).  Only one writable ``translateX/Y/Z`` scalar is edited;
    the exact original scalar is restored in ``finally``.

    The returned report is deliberately structured for smoke JSON.  A locked
    or connected Hips channel is not an exception: the probe tries an ancestor
    and returns ``supported=False`` with reason
    ``"no_writable_locomotion_driver"`` when no safe scalar exists.
    """
    cmds = cmds_module or maya_cmds()
    delta = tuple(float(value) for value in translation)
    if len(delta) != 3:
        raise ValueError("translation must contain exactly three values")
    observed_root = observed_root_joint or driver_joint
    driver_joint = _normalise_locomotion_name(cmds, driver_joint)
    observed_root = _normalise_locomotion_name(cmds, observed_root)
    if source_model_root is not None:
        source_model_root = _normalise_locomotion_name(cmds, source_model_root)
    groups = _normalise_groups(observed_root, affected_joints)
    # Always sample source Hips as well as target groups.  The source world
    # delta is the locomotion reference; the authored local request is not.
    joints = [str(driver_joint), observed_root] + [
        joint for values in groups.values() for joint in values
    ]
    joints = list(dict.fromkeys(joints))

    candidates, candidate_boundary_reason = _locomotion_driver_candidates(
        cmds,
        driver_joint,
        source_model_root=source_model_root,
    )
    requested_axes = [
        index for index, value in enumerate(delta) if abs(float(value)) > float(tolerance)
    ]
    candidate_diagnostics: List[Dict[str, Any]] = []
    rejected_candidates: List[Dict[str, Any]] = []
    selected: Optional[Dict[str, Any]] = None
    if candidate_boundary_reason is None:
        for candidate in candidates:
            candidate_report = _inspect_locomotion_candidate(
                cmds,
                candidate,
                requested_axes,
            )
            candidate_diagnostics.append(candidate_report)
            if candidate_report.get("selected"):
                selected = candidate_report
                break
            rejected_candidates.append(candidate_report)

    # The repository's original hierarchy unit double only implements a
    # compound ``translate`` API.  Keep that narrow compatibility path for
    # doubles lacking scalar/lock/hierarchy APIs; real Maya always takes the
    # scalar path above.
    legacy_vector_fallback = False
    if (
        selected is None
        and candidate_boundary_reason is None
        and _supports_legacy_vector_probe(cmds)
    ):
        axis = requested_axes[0] if requested_axes else None
        if axis is not None:
            selected = {
                "candidate": str(driver_joint),
                "selected": True,
                "selectedAxis": int(axis),
                "selectedChannel": _TRANSLATE_CHANNELS[axis],
                "selectedPlug": f"{driver_joint}.{_TRANSLATE_CHANNELS[axis]}",
                "legacyVectorFallback": True,
                "reasons": [],
                "writers": [],
            }
            legacy_vector_fallback = True

    base_report: Dict[str, Any] = {
        "driverJoint": str(driver_joint),
        "rootJoint": str(observed_root),
        "translation": list(delta),
        "tolerance": float(tolerance),
        "candidates": [str(candidate) for candidate in candidates],
        "candidateBoundaryReason": candidate_boundary_reason,
        "rejectedCandidates": rejected_candidates,
        "candidateDiagnostics": candidate_diagnostics,
        "selectedPlug": selected.get("selectedPlug") if selected else None,
        "selectedDriverJoint": selected.get("candidate") if selected else None,
        "selectedAxis": selected.get("selectedChannel") if selected else None,
        "legacyVectorFallback": bool(legacy_vector_fallback),
        "supported": bool(selected),
        "reason": (
            None
            if selected
            else candidate_boundary_reason or "no_writable_locomotion_driver"
        ),
        "writeSucceeded": False,
        "writeReadbackPassed": False,
        "originalScalar": None,
        "requestedScalar": None,
        "readback": None,
        "restoreReadback": None,
        "restoreReadbackPassed": False,
        "restoreSucceeded": False,
        "restoreAttempted": False,
        "restoreError": None,
        "restore": {"attempted": False, "succeeded": False, "error": None},
        "beforeWorldMatrix": {},
        "afterWorldMatrix": {},
        "beforeWorldTranslation": {},
        "afterWorldTranslation": {},
        "deltas": {},
        "groups": {},
        "rootDelta": [0.0, 0.0, 0.0],
        "sourceHipsDelta": [0.0, 0.0, 0.0],
        "sourceRootDelta": [0.0, 0.0, 0.0],
        "targetRootDelta": [0.0, 0.0, 0.0],
        "rootMotionScale": None,
        "rootMotionResidual": None,
        "rootMotionPassed": False,
        "passed": False,
    }
    if selected is None:
        return base_report

    before_matrices = {joint: _world_matrix(cmds, joint) for joint in joints}
    before = {joint: _matrix_translation(matrix) for joint, matrix in before_matrices.items()}
    selected_joint = str(selected["candidate"])
    selected_axis = int(selected["selectedAxis"])
    selected_plug = str(selected["selectedPlug"])
    original_scalar: Optional[float] = None
    after_matrices: Dict[str, List[float]] = {}
    write_error: Optional[str] = None
    try:
        if legacy_vector_fallback:
            original_vector = _get_vector_attr(cmds, selected_joint, "translate")
            original_scalar = float(original_vector[selected_axis])
            base_report["originalScalar"] = original_scalar
            updated_vector = list(original_vector)
            updated_vector[selected_axis] += float(delta[selected_axis])
            base_report["requestedScalar"] = updated_vector[selected_axis]
            cmds.setAttr(
                f"{selected_joint}.translate",
                updated_vector[0],
                updated_vector[1],
                updated_vector[2],
                type="double3",
            )
        else:
            original_scalar = _get_scalar_attr(cmds, selected_plug)
            base_report["originalScalar"] = original_scalar
            base_report["requestedScalar"] = original_scalar + float(delta[selected_axis])
            cmds.setAttr(selected_plug, original_scalar + float(delta[selected_axis]))
        # This flag is intentionally set before the finally restoration so a
        # successful temporary write cannot be lost from the diagnostic.
        base_report["writeSucceeded"] = True
        if legacy_vector_fallback:
            readback_vector = _get_vector_attr(cmds, selected_joint, "translate")
            readback = float(readback_vector[selected_axis])
        else:
            readback = _get_scalar_attr(cmds, selected_plug)
        base_report["readback"] = readback
        expected_readback = float(original_scalar) + float(delta[selected_axis])
        base_report["writeReadbackPassed"] = abs(readback - expected_readback) <= float(tolerance)
        _force_evaluation(cmds)
        after_matrices = {joint: _world_matrix(cmds, joint) for joint in joints}
    except Exception as exc:
        write_error = str(exc)
    finally:
        if original_scalar is not None:
            base_report["restoreAttempted"] = True
            try:
                if legacy_vector_fallback:
                    restore_vector = _get_vector_attr(cmds, selected_joint, "translate")
                    restore_vector[selected_axis] = original_scalar
                    cmds.setAttr(
                        f"{selected_joint}.translate",
                        restore_vector[0],
                        restore_vector[1],
                        restore_vector[2],
                        type="double3",
                    )
                else:
                    cmds.setAttr(selected_plug, original_scalar)
                _force_evaluation(cmds)
                if legacy_vector_fallback:
                    restored_vector = _get_vector_attr(cmds, selected_joint, "translate")
                    restore_readback = float(restored_vector[selected_axis])
                else:
                    restore_readback = _get_scalar_attr(cmds, selected_plug)
                base_report["restoreReadback"] = restore_readback
                restore_matches = abs(restore_readback - float(original_scalar)) <= float(tolerance)
                base_report["restoreReadbackPassed"] = bool(restore_matches)
                if not restore_matches:
                    raise RuntimeError(
                        "restore_readback_mismatch: "
                        f"expected={float(original_scalar)!r} actual={restore_readback!r}"
                    )
                base_report["restoreSucceeded"] = True
                base_report["restore"] = {"attempted": True, "succeeded": True, "error": None}
            except Exception as exc:
                base_report["restoreError"] = str(exc)
                base_report["restore"] = {
                    "attempted": True,
                    "succeeded": False,
                    "error": str(exc),
                }

    after = {
        joint: _matrix_translation(matrix)
        for joint, matrix in after_matrices.items()
    }
    base_report["beforeWorldMatrix"] = before_matrices
    base_report["afterWorldMatrix"] = after_matrices
    base_report["beforeWorldTranslation"] = before
    base_report["afterWorldTranslation"] = after
    if write_error is not None:
        base_report["writeError"] = write_error
    if not after_matrices:
        base_report["reason"] = "locomotion_probe_write_failed"
        return base_report
    if not base_report["writeReadbackPassed"]:
        base_report["reason"] = "locomotion_probe_readback_failed"
    elif not base_report["restoreSucceeded"]:
        base_report["reason"] = "locomotion_probe_restore_failed"

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
    source_delta = deltas.get(str(driver_joint), [0.0, 0.0, 0.0])
    root_motion_passed, root_motion_scale, root_motion_residual = _root_motion_matches_scaled_source(
        root_delta,
        source_delta,
        tolerance,
    )
    passed = (
        bool(base_report["supported"])
        and bool(base_report["writeSucceeded"])
        and bool(base_report["writeReadbackPassed"])
        and bool(base_report["restoreSucceeded"])
        and root_motion_passed
        and all(
        report["passed"] for report in group_report.values()
        )
    )
    base_report.update(
        {
            "rootDelta": root_delta,
            "sourceHipsDelta": source_delta,
            "sourceRootDelta": source_delta,
            "targetRootDelta": root_delta,
            "rootMotionPassed": bool(root_motion_passed),
            "rootMotionScale": root_motion_scale,
            "rootMotionResidual": root_motion_residual,
            "deltas": deltas,
            "groups": group_report,
            "passed": bool(passed),
        }
    )
    return base_report


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


def _get_scalar_attr(cmds, plug: str) -> float:
    """Read one scalar Maya plug, normalising Maya's scalar return shape."""
    value = cmds.getAttr(plug)
    if isinstance(value, (tuple, list)):
        while isinstance(value, (tuple, list)) and value:
            value = value[0]
    return float(value)


def _normalise_locomotion_name(cmds, node: str) -> str:
    """Resolve a node to its long path when Maya can provide one."""
    name = str(node)
    ls = getattr(cmds, "ls", None)
    if ls is None:
        return name
    try:
        values = ls(name, long=True) or []
    except TypeError:
        try:
            values = ls(name, l=True) or []
        except (AttributeError, RuntimeError, TypeError):
            values = []
    except (AttributeError, RuntimeError):
        values = []
    return str(values[0]) if values else name


def _locomotion_driver_candidates(
    cmds,
    driver_joint: str,
    source_model_root: Optional[str] = None,
) -> Tuple[List[str], Optional[str]]:
    """Return Hips-to-root candidates without changing hierarchy state."""
    candidates: List[str] = []
    seen = set()
    current = _normalise_locomotion_name(cmds, driver_joint)
    root = (
        _normalise_locomotion_name(cmds, source_model_root)
        if source_model_root
        else None
    )
    while current and current not in seen:
        seen.add(current)
        candidates.append(current)
        if root is not None and current == root:
            return candidates, None
        list_relatives = getattr(cmds, "listRelatives", None)
        if list_relatives is None:
            return candidates, "source_model_root_unreachable" if root else None
        try:
            parents = list_relatives(current, parent=True, fullPath=True) or []
        except TypeError:
            try:
                parents = list_relatives(current, p=True, f=True) or []
            except (AttributeError, RuntimeError, TypeError):
                parents = []
        except (AttributeError, RuntimeError):
            parents = []
        current = _normalise_locomotion_name(cmds, parents[0]) if parents else ""
    if root is not None and (not candidates or candidates[-1] != root):
        return candidates, "source_model_root_unreachable"
    return candidates, None


def _inspect_locomotion_candidate(
    cmds,
    candidate: str,
    requested_axes: Sequence[int],
) -> Dict[str, Any]:
    """Classify scalar translation plugs for one candidate joint."""
    node_locked = _query_node_locked(cmds, candidate)
    axes: List[Dict[str, Any]] = []
    selected_axis = None
    reasons: List[str] = []
    requested = set(int(axis) for axis in requested_axes)
    # Classify all scalar axes for diagnostics; only requested axes may be
    # selected for the temporary edit.
    for axis in range(len(_TRANSLATE_CHANNELS)):
        channel = _TRANSLATE_CHANNELS[int(axis)]
        plug = f"{candidate}.{channel}"
        writers = _incoming_writers(cmds, plug)
        plug_locked = _query_attr_bool(cmds, plug, "lock")
        settable = _query_attr_bool(cmds, plug, "settable")
        scalar_readable = True
        scalar_error = None
        try:
            _get_scalar_attr(cmds, plug)
        except Exception as exc:
            scalar_readable = False
            scalar_error = str(exc)
        axis_reasons: List[str] = []
        if node_locked is True:
            axis_reasons.append("node_locked")
        if plug_locked is True:
            axis_reasons.append("plug_locked")
        if settable is False:
            axis_reasons.append("not_settable")
        if writers:
            axis_reasons.append("incoming_writers")
        if not scalar_readable:
            axis_reasons.append("scalar_unreadable")
        axis_report = {
            "channel": channel,
            "plug": plug,
            "requested": axis in requested,
            "writers": sorted(set(str(writer) for writer in writers)),
            "nodeLocked": node_locked,
            "plugLocked": plug_locked,
            "settable": settable,
            "scalarReadable": scalar_readable,
            "reasons": axis_reasons,
        }
        if scalar_error is not None:
            axis_report["readError"] = scalar_error
        axes.append(axis_report)
        if selected_axis is None and axis in requested and not axis_reasons:
            selected_axis = int(axis)
    if selected_axis is not None:
        selected_channel = _TRANSLATE_CHANNELS[selected_axis]
        return {
            "candidate": str(candidate),
            "selected": True,
            "selectedAxis": selected_axis,
            "selectedChannel": selected_channel,
            "selectedPlug": f"{candidate}.{selected_channel}",
            "nodeLocked": node_locked,
            "axes": axes,
            "reasons": [],
            "writers": sorted({writer for item in axes for writer in item["writers"]}),
        }
    for item in axes:
        if item["requested"]:
            reasons.extend(item["reasons"])
    return {
        "candidate": str(candidate),
        "selected": False,
        "nodeLocked": node_locked,
        "axes": axes,
        "reasons": sorted(set(reasons)) or ["no_requested_translation_axis"],
        "writers": sorted({writer for item in axes for writer in item["writers"]}),
    }


def _query_attr_bool(cmds, plug: str, flag: str) -> Optional[bool]:
    try:
        value = cmds.getAttr(plug, **{flag: True})
    except (AttributeError, RuntimeError, TypeError):
        return None
    if isinstance(value, (tuple, list)) and value:
        value = value[0]
    return None if value is None else bool(value)


def _query_node_locked(cmds, node: str) -> Optional[bool]:
    lock_node = getattr(cmds, "lockNode", None)
    if lock_node is None:
        return None
    try:
        value = lock_node(node, query=True, lock=True)
    except TypeError:
        try:
            value = lock_node(node, q=True, l=True)
        except (AttributeError, RuntimeError, TypeError):
            return None
    except (AttributeError, RuntimeError):
        return None
    if isinstance(value, (tuple, list)) and value:
        value = value[0]
    return None if value is None else bool(value)


def _supports_legacy_vector_probe(cmds) -> bool:
    """Recognise only the old minimal hierarchy test double.

    Maya exposes ``listRelatives`` and ``lockNode``; requiring both to be
    absent prevents a real/realistic command double from bypassing scalar
    safety checks merely because a scalar plug is unavailable.
    """
    return not hasattr(cmds, "listRelatives") and not hasattr(cmds, "lockNode")


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


def _root_motion_matches_scaled_source(
    target_delta: Sequence[float],
    source_delta: Sequence[float],
    tolerance: float,
) -> Tuple[bool, Optional[float], Optional[List[float]]]:
    """Compare world deltas using one positive uniform retarget scale.

    The source delta is measured from source Hips, not inferred from the
    authored local probe vector.  This is important when Hips has a rotated
    parent or when the HIK target uses a different scale.
    """
    source = [float(value) for value in source_delta]
    target = [float(value) for value in target_delta]
    source_norm_sq = sum(value * value for value in source)
    target_norm_sq = sum(value * value for value in target)
    tolerance_sq = float(tolerance) * float(tolerance)
    if source_norm_sq <= tolerance_sq or target_norm_sq <= tolerance_sq:
        return False, None, None
    scale = sum(target[index] * source[index] for index in range(3)) / source_norm_sq
    residual = [target[index] - source[index] * scale for index in range(3)]
    passed = scale > 0.0 and all(abs(value) <= float(tolerance) for value in residual)
    return bool(passed), float(scale), residual


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
