"""Exclusive HumanIK TARGET preview ownership transition.

S3 applies a previously reviewed S1 ownership report in the fixed order
``journal -> mute conflicting writers -> HIK input`` and restores NEUTRAL from
the S2 journal.  It does not bake animation or modify physics owners.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List

from mmd_tools.core.humanik_constraints import (
    classify_humanik_constraints,
    collect_humanik_constraint_facts,
)
from mmd_tools.core.humanik_retarget import connect_humanik_source
from mmd_tools.core.humanik_transaction import (
    HumanIkTransactionJournal,
    capture_humanik_journal,
    restore_humanik_journal,
)


BLOCKING_CLASSIFICATIONS = frozenset({"physics_blocker", "feedback_blocker", "manual"})


@dataclass
class HumanIkTargetPreview:
    """Active TARGET preview and its reversible journal."""

    ownership_id: str
    target_character: str
    source_character: str
    journal: HumanIkTransactionJournal
    disconnected: List[Dict[str, str]]
    retained_nodes: List[str]
    post_report: Dict[str, Any]
    active: bool = True

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
    cmds = cmds_module or _maya_cmds()
    blockers = [
        row
        for row in ownership_report.get("rows", [])
        if row.get("classification") in BLOCKING_CLASSIFICATIONS
    ]
    if blockers:
        labels = ", ".join(f"{row['node']}:{row['classification']}" for row in blockers)
        raise RuntimeError(f"HumanIK TARGET preview blocked: {labels}")

    mute_rows = [
        row for row in ownership_report.get("rows", [])
        if row.get("classification") == "mute_for_hik"
    ]
    retained_nodes = sorted(
        row["node"] for row in ownership_report.get("rows", [])
        if row.get("classification") == "keep_post"
    )
    destinations = sorted({plug for row in mute_rows for plug in row.get("writes", [])})
    muted_nodes = sorted({row["node"] for row in mute_rows})
    journal = capture_humanik_journal(
        ownership_id,
        target_character,
        destinations,
        muted_nodes,
        cmds_module=cmds,
        mel_module=mel_module,
    )
    disconnected: List[Dict[str, str]] = []
    try:
        for row in mute_rows:
            for destination in row.get("writes", []):
                for source in cmds.listConnections(
                    destination, source=True, destination=False, plugs=True
                ) or []:
                    if _plug_node(str(source)) != row["node"]:
                        continue
                    cmds.disconnectAttr(source, destination)
                    disconnected.append({"source": str(source), "destination": destination})
        connect_humanik_source(
            target_character,
            source_character,
            mel_module=mel_module,
        )
        post_report = classify_humanik_constraints(
            collect_humanik_constraint_facts(cmds_module=cmds),
            hik_joints,
        )
        post_blockers = [
            row for row in post_report["rows"]
            if row["node"] not in muted_nodes
            and row["classification"] in BLOCKING_CLASSIFICATIONS
        ]
        if post_blockers:
            raise RuntimeError("HumanIK TARGET preview post-scan found blocker")
    except Exception:
        restore_humanik_journal(
            journal,
            ownership_id=ownership_id,
            cmds_module=cmds,
            mel_module=mel_module,
        )
        raise
    return HumanIkTargetPreview(
        ownership_id=ownership_id,
        target_character=target_character,
        source_character=source_character,
        journal=journal,
        disconnected=sorted(disconnected, key=lambda row: (row["destination"], row["source"])),
        retained_nodes=retained_nodes,
        post_report=post_report,
    )


def stop_humanik_target_preview(
    preview: HumanIkTargetPreview,
    cmds_module=None,
    mel_module=None,
) -> None:
    """Restore NEUTRAL ownership; repeated stop calls are safe."""
    restore_humanik_journal(
        preview.journal,
        ownership_id=preview.ownership_id,
        cmds_module=cmds_module,
        mel_module=mel_module,
    )
    preview.active = False


def _plug_node(plug: str) -> str:
    return plug.rsplit(".", 1)[0] if "." in plug else plug


def _maya_cmds():
    from maya import cmds

    return cmds
