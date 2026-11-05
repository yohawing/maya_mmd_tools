"""Exclusive HumanIK TARGET preview ownership transition.

S3 applies a previously reviewed S1 ownership report in the fixed order
``restore_state -> mute conflicting writers -> HIK input`` and restores NEUTRAL from
the S2 restore_state.  It does not bake animation or modify physics owners.

``begin_humanik_target_preview`` also disables the TARGET character's
``FingerSolving`` property for the preview's lifetime (HUMANIK-RETARGET-S5;
see ``HUMANIK_FINGER_SOLVING_DISABLED`` in ``humanik_builder.py``). It also
scopes the Aida-proven TARGET ``LeftElbowKillPitch`` correction to the preview
and restores both prior values on ``stop_humanik_target_preview`` or rollback,
exactly like ``input_source``/``lock_state`` are restored via the restore_state.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional

from mmd_tools.core.humanik_builder import (
    HUMANIK_FINGER_SOLVING_DISABLED,
    HUMANIK_LEFT_ELBOW_KILL_PITCH_ENABLED,
    set_humanik_left_elbow_kill_pitch_state,
    set_humanik_finger_solving_state,
)
from mmd_tools.core.humanik_constraints import (
    BLOCKING_CLASSIFICATIONS,
    classify_humanik_constraints,
    collect_humanik_constraint_facts,
    is_preisolated_mmd_ccdik_feedback_row,
    is_supported_mmd_feedback_row,
    split_ownership_rows,
)
from mmd_tools.core.humanik_retarget import connect_humanik_source
from mmd_tools.core.humanik_transaction import (
    HumanIkRestoreState,
    capture_humanik_restore_state,
    apply_humanik_restore_state,
    include_unconnected_hik_trs,
)
from mmd_tools.core.humanik_utils import maya_cmds


# Re-exported here for backward compatibility: several callers (control_rig,
# frontend, stance, and tests/viewport/humanik_roundtrip_smoke.py) import
# BLOCKING_CLASSIFICATIONS from this module. The canonical definition now
# lives in humanik_constraints.py, next to the classification strings it
# names (see classify_humanik_constraints._classify_constraint).


@dataclass
class HumanIkTargetPreview:
    """Active TARGET preview and its reversible restore_state."""

    ownership_id: str
    target_character: str
    source_character: str
    restore_state: HumanIkRestoreState
    disconnected: List[Dict[str, str]]
    retained_nodes: List[str]
    post_report: Dict[str, Any]
    isolated_feedback_rows: List[Dict[str, Any]]
    active: bool = True
    finger_solving_previous: Optional[int] = None
    left_elbow_kill_pitch_previous: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Return the JSON-safe preview diagnostic payload."""
        return {
            "ownershipId": self.ownership_id,
            "targetCharacter": self.target_character,
            "sourceCharacter": self.source_character,
            "disconnected": list(self.disconnected),
            "retainedNodes": list(self.retained_nodes),
            "postReport": self.post_report,
            "isolatedFeedbackRows": list(self.isolated_feedback_rows),
            "active": self.active,
            "fingerSolvingPreviousValue": self.finger_solving_previous,
            "leftElbowKillPitchPreviousValue": self.left_elbow_kill_pitch_previous,
        }

    def to_scene_dict(self) -> Dict[str, Any]:
        """Return the reversible subset required after a scene reload."""
        return {
            "ownershipId": self.ownership_id,
            "targetCharacter": self.target_character,
            "sourceCharacter": self.source_character,
            "restore_state": self.restore_state.to_dict(),
            "disconnected": list(self.disconnected),
            "retainedNodes": list(self.retained_nodes),
            "active": bool(self.active),
            "fingerSolvingPreviousValue": self.finger_solving_previous,
            "leftElbowKillPitchPreviousValue": self.left_elbow_kill_pitch_previous,
        }

    @classmethod
    def from_scene_dict(cls, payload: Mapping[str, Any]) -> "HumanIkTargetPreview":
        """Reconstruct and validate a persisted reversible preview handle."""
        if not isinstance(payload, Mapping):
            raise ValueError("HumanIK preview scene payload must be an object")
        ownership_id = payload.get("ownershipId")
        target_character = payload.get("targetCharacter")
        source_character = payload.get("sourceCharacter")
        if not isinstance(ownership_id, str) or not ownership_id:
            raise ValueError("HumanIK preview ownershipId must be a non-empty string")
        if not isinstance(target_character, str) or not target_character:
            raise ValueError("HumanIK preview targetCharacter must be a non-empty string")
        if not isinstance(source_character, str) or not source_character:
            raise ValueError("HumanIK preview sourceCharacter must be a non-empty string")
        restore_state = HumanIkRestoreState.from_dict(payload.get("restore_state", {}))
        if restore_state.ownership_id != ownership_id:
            raise ValueError("HumanIK preview restore_state ownership mismatch")
        if restore_state.character != target_character:
            raise ValueError("HumanIK preview restore_state character mismatch")

        disconnected = payload.get("disconnected", [])
        retained_nodes = payload.get("retainedNodes", [])
        if not isinstance(disconnected, list) or not all(
            isinstance(item, dict) for item in disconnected
        ):
            raise ValueError("HumanIK preview disconnected must be an array")
        if not isinstance(retained_nodes, list) or not all(
            isinstance(item, str) for item in retained_nodes
        ):
            raise ValueError("HumanIK preview retainedNodes must be an array of strings")
        active = payload.get("active", True)
        if not isinstance(active, bool):
            raise ValueError("HumanIK preview active must be a boolean")

        def _optional_int(key: str) -> Optional[int]:
            value = payload.get(key)
            if value is None:
                return None
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"HumanIK preview {key} must be an integer or null")
            return value

        return cls(
            ownership_id=ownership_id,
            target_character=target_character,
            source_character=source_character,
            restore_state=restore_state,
            disconnected=[dict(item) for item in disconnected],
            retained_nodes=list(retained_nodes),
            post_report={"rows": [], "sceneRecovered": True},
            isolated_feedback_rows=[],
            active=active,
            finger_solving_previous=_optional_int("fingerSolvingPreviousValue"),
            left_elbow_kill_pitch_previous=_optional_int(
                "leftElbowKillPitchPreviousValue"
            ),
        )


def _has_unresolved_indirect_output(row: Dict[str, Any], cmds, hik_joints) -> bool:
    """Reject manual writers only when their outputs can affect this target."""
    if row["classification"] != "manual":
        return False
    pending = [row["node"]]
    visited = set()
    while pending:
        node = pending.pop()
        if node in visited:
            continue
        visited.add(node)
        outputs = cmds.listConnections(node, source=False, destination=True, plugs=True) or []
        if outputs and (row.get("readHikJoints") or row.get("writeHikJoints")):
            return True
        for plug in outputs:
            destination = plug.split(".", 1)[0]
            names = cmds.ls(destination, long=True) or []
            if hik_joints.intersection(names):
                return True
            pending.append(destination)
    return False


def _preisolated_foot_has_unexpected_output(row: Dict[str, Any], cmds, rows_by_node, isolated_nodes) -> bool:
    """Allow authored-input forwarding and the retained foot-IK append path."""
    node = row["node"]
    pairs = cmds.listConnections(
        node, source=False, destination=True, plugs=True, connections=True
    ) or []
    if len(pairs) % 2:
        return True
    for first, second in zip(pairs[::2], pairs[1::2]):
        if str(first).startswith(f"{node}."):
            source, destination = first, second
        elif str(second).startswith(f"{node}."):
            source, destination = second, first
        else:
            return True
        destination_node, _, destination_attr = destination.rpartition(".")
        # These are authored inputs, not solved outputs. Both endpoints must
        # belong to foot writers already isolated by this Control Rig owner.
        if (
            re.fullmatch(r"inputRotate\[\d+\]", source[len(node) + 1:])
            and re.fullmatch(r"preIkRotate\[\d+\]", destination_attr)
            and destination_node in isolated_nodes
            and not rows_by_node.get(destination_node, {}).get("writes")
        ):
            continue
        if source != f"{node}.outputMmdLinkQuaternions" or "." not in destination:
            return True
        append_node, attribute = destination.rsplit(".", 1)
        append_row = rows_by_node.get(append_node, {})
        if (
            attribute != "sourceMmdLinkQuaternions"
            or append_row.get("nodeType") != "mmdAppend"
            or append_row.get("classification") != "keep_post"
        ):
            return True
        for plug in cmds.listConnections(append_node, source=False, destination=True, plugs=True) or []:
            if "." not in plug:
                return True
            joint, attr = plug.rsplit(".", 1)
            long_names = cmds.ls(joint, long=True) or [joint]
            if not any(f"{name}.{attr}" in append_row.get("writes", ()) for name in long_names):
                return True
    return False


def begin_humanik_target_preview(
    ownership_id: str,
    target_character: str,
    source_character: str,
    ownership_report: Dict[str, Any],
    hik_joints: Iterable[str],
    cmds_module=None,
    mel_module=None,
    assignments: Optional[Iterable[Any]] = None,
    preisolated_feedback_nodes: Optional[Iterable[str]] = None,
) -> HumanIkTargetPreview:
    """Start TARGET preview after rejecting all unresolved ownership rows.

    ``assignments`` is intentionally optional for backward compatibility with
    direct callers.  Without it, every feedback blocker remains fail-closed;
    the narrowly supported foot IK and arm IK append rows are isolated only
    when the target binding supplies its assigned HIK slots.
    """
    cmds = cmds_module or maya_cmds()
    blockers, mute_rows, retained_nodes = split_ownership_rows(ownership_report)
    supported_feedback = [
        row
        for row in ownership_report.get("rows", [])
        if is_supported_mmd_feedback_row(row, assignments or (), ownership_report.get("rows", ()), cmds)
    ]
    preisolated_nodes = tuple(str(node) for node in (preisolated_feedback_nodes or ()))
    preisolated_feedback = [
        row
        for row in ownership_report.get("rows", [])
        if is_preisolated_mmd_ccdik_feedback_row(row, preisolated_nodes)
    ]
    blockers = [
        row
        for row in blockers
        if row not in supported_feedback and row not in preisolated_feedback
    ]
    if blockers:
        labels = ", ".join(f"{row['node']}:{row['classification']}" for row in blockers)
        raise RuntimeError(f"HumanIK TARGET preview blocked: {labels}")

    isolated_rows = [*mute_rows, *supported_feedback]
    destinations = sorted({plug for row in isolated_rows for plug in row.get("writes", [])})
    muted_nodes = sorted({row["node"] for row in mute_rows})
    feedback_nodes = sorted(
        {row["node"] for row in [*supported_feedback, *preisolated_feedback]}
    )
    hik_joint_set = {str(joint) for joint in hik_joints}
    pre_report = classify_humanik_constraints(
        collect_humanik_constraint_facts(cmds_module=cmds),
        hik_joint_set,
    )
    # The active Control Rig already isolated these foot writers. Their only
    # accepted remaining output is the known append path to non-HIK joints.
    # Output-free toe nodes are omitted from collected facts, but remain in
    # the transaction's UUID-validated isolation records.
    verified_preisolated_nodes = set(preisolated_nodes)
    pre_rows_by_node = {row["node"]: row for row in pre_report["rows"]}
    indirect_blockers = [
        row for row in pre_report["rows"]
        if (
            _preisolated_foot_has_unexpected_output(row, cmds, pre_rows_by_node, verified_preisolated_nodes)
            if row["node"] in verified_preisolated_nodes
            else _has_unresolved_indirect_output(row, cmds, hik_joint_set)
        )
    ]
    if indirect_blockers:
        labels = ", ".join(row["node"] for row in indirect_blockers)
        raise RuntimeError(f"HumanIK TARGET preview pre-scan found blocker: {labels}")

    # HIK also drives previously unconnected TRS channels. Keep their values,
    # not just the destinations of writers that we temporarily disconnect.
    destinations = include_unconnected_hik_trs(cmds, hik_joint_set, destinations)
    restore_state = capture_humanik_restore_state(
        ownership_id,
        target_character,
        destinations,
        muted_nodes,
        cmds_module=cmds,
        mel_module=mel_module,
    )
    disconnected: List[Dict[str, str]] = []
    finger_solving_previous: Optional[int] = None
    left_elbow_kill_pitch_previous: Optional[int] = None
    try:
        # Disable HumanIK's internal finger-rotation reconstruction on the
        # TARGET character for the lifetime of this preview (see
        # HUMANIK_FINGER_SOLVING_DISABLED). Scoped here rather than at
        # characterize time because it is only meaningful while the target is
        # actually being driven by a source, and it must be restored the same
        # way input_source/lock_state already are on stop/rollback.
        finger_solving_previous = set_humanik_finger_solving_state(
            target_character,
            HUMANIK_FINGER_SOLVING_DISABLED,
            mel_module=mel_module,
            cmds_module=cmds,
        )
        # Aida evidence shows that Maya's default target elbow-pitch
        # reconstruction introduces a fixed LeftForeArm offset even for
        # identical source/target characterization. Keep the proven fix
        # scoped to active TARGET preview and restore it on every exit path.
        left_elbow_kill_pitch_previous = set_humanik_left_elbow_kill_pitch_state(
            target_character,
            HUMANIK_LEFT_ELBOW_KILL_PITCH_ENABLED,
            mel_module=mel_module,
            cmds_module=cmds,
        )
        disconnect_reviewed_writers(cmds, isolated_rows, disconnected)
        connect_humanik_source(
            target_character,
            source_character,
            mel_module=mel_module,
        )
        re_isolate_reviewed_edges(cmds, disconnected)
        post_report = classify_humanik_constraints(
            collect_humanik_constraint_facts(cmds_module=cmds),
            hik_joint_set,
        )
        isolated_nodes = set(muted_nodes) | set(feedback_nodes)
        residual_muted_writers = [
            row
            for row in post_report["rows"]
            if row["node"] in isolated_nodes and row_hik_writes(row, hik_joint_set)
        ]
        if residual_muted_writers:
            labels = ", ".join(
                f"{row['node']}->{','.join(sorted(row_hik_writes(row, hik_joint_set)))}"
                for row in sorted(residual_muted_writers, key=lambda item: item["node"])
            )
            disconnect_residual_muted_writers(cmds, residual_muted_writers, hik_joint_set)
            raise RuntimeError(
                "HumanIK TARGET preview post-scan found residual muted HIK writers: "
                f"{labels}"
            )
        isolated_arm_nodes = {
            row["node"] for row in supported_feedback if row.get("nodeType") == "mmdAppend"
        }
        if any(
            not is_supported_mmd_feedback_row(
                row, assignments or (), ownership_report.get("rows", ()), cmds, isolated_arm_nodes
            )
            for row in supported_feedback if row["node"] in isolated_arm_nodes
        ):
            raise RuntimeError("HumanIK TARGET preview post-scan found blocker in arm IK output")
        post_rows_by_node = {row["node"]: row for row in post_report["rows"]}
        if any(
            _preisolated_foot_has_unexpected_output(row, cmds, post_rows_by_node, verified_preisolated_nodes)
            for row in post_report["rows"] if row["node"] in verified_preisolated_nodes
        ):
            raise RuntimeError("HumanIK TARGET preview post-scan found blocker")
        post_blockers = [
            row for row in post_report["rows"]
            if row["node"] not in muted_nodes
            and row["node"] not in feedback_nodes
            and row["classification"] in BLOCKING_CLASSIFICATIONS
            # Ignore manual writers whose downstream graph is unrelated to this target.
            and (
                row["classification"] != "manual"
                or row.get("readHikJoints")
                or row.get("writeHikJoints")
                or _has_unresolved_indirect_output(row, cmds, hik_joint_set)
            )
        ]
        if post_blockers:
            raise RuntimeError("HumanIK TARGET preview post-scan found blocker")
    except Exception as error:
        if finger_solving_previous is not None:
            set_humanik_finger_solving_state(
                target_character,
                finger_solving_previous,
                mel_module=mel_module,
                cmds_module=cmds,
            )
        if left_elbow_kill_pitch_previous is not None:
            set_humanik_left_elbow_kill_pitch_state(
                target_character,
                left_elbow_kill_pitch_previous,
                mel_module=mel_module,
                cmds_module=cmds,
            )
        try:
            apply_humanik_restore_state(
                restore_state,
                ownership_id=ownership_id,
                cmds_module=cmds,
                mel_module=mel_module,
            )
        except Exception as rollback_error:
            raise RuntimeError(
                "HumanIK TARGET preview failed and restore_state rollback failed: "
                f"failure={error}; rollback={rollback_error}"
            ) from error
        raise
    return HumanIkTargetPreview(
        ownership_id=ownership_id,
        target_character=target_character,
        source_character=source_character,
        restore_state=restore_state,
        disconnected=sorted(disconnected, key=lambda row: (row["destination"], row["source"])),
        retained_nodes=retained_nodes,
        post_report=post_report,
        isolated_feedback_rows=[
            dict(row) for row in [*supported_feedback, *preisolated_feedback]
        ],
        finger_solving_previous=finger_solving_previous,
        left_elbow_kill_pitch_previous=left_elbow_kill_pitch_previous,
    )


def stop_humanik_target_preview(
    preview: HumanIkTargetPreview,
    cmds_module=None,
    mel_module=None,
) -> None:
    """Restore NEUTRAL ownership; repeated stop calls are safe."""
    restore_error = None
    try:
        apply_humanik_restore_state(
            preview.restore_state,
            ownership_id=preview.ownership_id,
            cmds_module=cmds_module,
            mel_module=mel_module,
        )
    except Exception as error:  # Preserve the restore_state failure verbatim.
        restore_error = error

    property_error = None
    try:
        if preview.finger_solving_previous is not None:
            set_humanik_finger_solving_state(
                preview.target_character,
                preview.finger_solving_previous,
                mel_module=mel_module,
                cmds_module=cmds_module,
            )
    except Exception as error:
        property_error = error
    try:
        if preview.left_elbow_kill_pitch_previous is not None:
            set_humanik_left_elbow_kill_pitch_state(
                preview.target_character,
                preview.left_elbow_kill_pitch_previous,
                mel_module=mel_module,
                cmds_module=cmds_module,
            )
    except Exception as error:
        if property_error is None:
            property_error = error

    if restore_error is not None:
        raise restore_error
    if property_error is not None:
        raise property_error
    preview.active = False


def plug_node(plug: str) -> str:
    """Return the node name portion of a ``node.attr`` plug string."""
    return plug.rsplit(".", 1)[0] if "." in plug else plug


def row_hik_writes(row: Dict[str, Any], hik_joints: set[str]) -> List[str]:
    """Return post-scan HIK writes, with a raw-write fallback for test hosts."""
    reported = row.get("writeHikJoints")
    if reported:
        return [str(joint) for joint in reported]
    return sorted({plug_node(str(plug)) for plug in row.get("writes", [])} & hik_joints)


def disconnect_residual_muted_writers(cmds, rows, hik_joints: set[str]) -> None:
    """Remove residual muted-node edges before restoring the scoped restore_state."""
    for row in sorted(rows, key=lambda item: str(item["node"])):
        node = str(row["node"])
        allowed_joints = set(row_hik_writes(row, hik_joints))
        for destination in sorted(str(value) for value in row.get("writes", [])):
            if plug_node(destination) not in allowed_joints:
                continue
            for source in cmds.listConnections(
                destination, source=True, destination=False, plugs=True
            ) or []:
                source = str(source)
                if plug_node(source) == node:
                    cmds.disconnectAttr(source, destination)


def disconnect_reviewed_writers(cmds, mute_rows, disconnected: List[Dict[str, str]]) -> None:
    """Disconnect only the reviewed node-to-destination writer edges."""
    known = {(row["source"], row["destination"]) for row in disconnected}
    for row in mute_rows:
        node = str(row["node"])
        for destination in sorted(str(value) for value in row.get("writes", [])):
            for source in cmds.listConnections(
                destination, source=True, destination=False, plugs=True
            ) or []:
                source = str(source)
                edge = (source, destination)
                if plug_node(source) != node or edge in known:
                    continue
                cmds.disconnectAttr(source, destination)
                disconnected.append({"source": source, "destination": destination})
                known.add(edge)


def re_isolate_reviewed_edges(cmds, disconnected: List[Dict[str, str]]) -> None:
    """Remove reviewed writer edges that reappeared while connecting HIK source."""
    for edge in sorted(disconnected, key=lambda row: (row["destination"], row["source"])):
        source = edge["source"]
        destination = edge["destination"]
        if source in (cmds.listConnections(
            destination, source=True, destination=False, plugs=True
        ) or []):
            cmds.disconnectAttr(source, destination)
