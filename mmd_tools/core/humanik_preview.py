"""Exclusive HumanIK TARGET preview ownership transition.

S3 applies a previously reviewed S1 ownership report in the fixed order
``restore_state -> mute conflicting writers -> HIK input`` and restores NEUTRAL from
the S2 restore_state.  It does not bake animation or modify physics owners.

``begin_humanik_target_preview`` also disables the TARGET character's
``FingerSolving`` property for the preview's lifetime (HUMANIK-RETARGET-S5;
see ``HUMANIK_FINGER_SOLVING_DISABLED`` in ``humanik_builder.py``) and restores
its prior value on ``stop_humanik_target_preview`` or rollback, exactly like
``input_source``/``lock_state`` are restored via the restore_state. It is
per-preview rather than a characterize-time change because it is only
meaningful while the target is actively retargeting from a source.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from mmd_tools.core.humanik_builder import (
    HUMANIK_FINGER_SOLVING_DISABLED,
    set_humanik_finger_solving_state,
)
from mmd_tools.core.humanik_constraints import (
    BLOCKING_CLASSIFICATIONS,
    classify_humanik_constraints,
    collect_humanik_constraint_facts,
    split_ownership_rows,
)
from mmd_tools.core.humanik_retarget import connect_humanik_source
from mmd_tools.core.humanik_transaction import (
    HumanIkRestoreState,
    capture_humanik_restore_state,
    apply_humanik_restore_state,
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
    active: bool = True
    finger_solving_previous: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Return the JSON-safe preview diagnostic payload."""
        return {
            "ownershipId": self.ownership_id,
            "targetCharacter": self.target_character,
            "sourceCharacter": self.source_character,
            "disconnected": list(self.disconnected),
            "retainedNodes": list(self.retained_nodes),
            "postReport": self.post_report,
            "active": self.active,
            "fingerSolvingPreviousValue": self.finger_solving_previous,
        }


def begin_humanik_target_preview(
    ownership_id: str,
    target_character: str,
    source_character: str,
    ownership_report: Dict[str, Any],
    hik_joints: Iterable[str],
    cmds_module=None,
    mel_module=None,
) -> HumanIkTargetPreview:
    """Start TARGET preview after rejecting all unresolved ownership rows."""
    cmds = cmds_module or maya_cmds()
    blockers, mute_rows, retained_nodes = split_ownership_rows(ownership_report)
    if blockers:
        labels = ", ".join(f"{row['node']}:{row['classification']}" for row in blockers)
        raise RuntimeError(f"HumanIK TARGET preview blocked: {labels}")

    destinations = sorted({plug for row in mute_rows for plug in row.get("writes", [])})
    muted_nodes = sorted({row["node"] for row in mute_rows})
    hik_joint_set = {str(joint) for joint in hik_joints}
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
        disconnect_reviewed_writers(cmds, mute_rows, disconnected)
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
        residual_muted_writers = [
            row
            for row in post_report["rows"]
            if row["node"] in muted_nodes and row_hik_writes(row, hik_joint_set)
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
        post_blockers = [
            row for row in post_report["rows"]
            if row["node"] not in muted_nodes
            and row["classification"] in BLOCKING_CLASSIFICATIONS
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
        finger_solving_previous=finger_solving_previous,
    )


def stop_humanik_target_preview(
    preview: HumanIkTargetPreview,
    cmds_module=None,
    mel_module=None,
) -> None:
    """Restore NEUTRAL ownership; repeated stop calls are safe."""
    apply_humanik_restore_state(
        preview.restore_state,
        ownership_id=preview.ownership_id,
        cmds_module=cmds_module,
        mel_module=mel_module,
    )
    if preview.finger_solving_previous is not None:
        set_humanik_finger_solving_state(
            preview.target_character,
            preview.finger_solving_previous,
            mel_module=mel_module,
            cmds_module=cmds_module,
        )
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
