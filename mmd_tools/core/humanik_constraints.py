"""Classify Maya rig writers against HumanIK channel ownership.

This S1 module is report-only.  It converts existing dependency-graph
connections into deterministic facts and never edits, mutes, or disconnects
scene nodes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from mmd_tools.core.humanik_utils import maya_cmds


PHYSICS_NODE_TYPES = frozenset({"mmdPhysicsBoneDriver"})
SUPPORTED_NODE_TYPES = ("mmdAppend", "mmdCcdIk", *sorted(PHYSICS_NODE_TYPES))

BLOCKING_CLASSIFICATIONS = frozenset({"physics_blocker", "feedback_blocker", "manual"})
"""Classifications that must fail-closed a HumanIK ownership-changing operation.

Re-exported from :mod:`mmd_tools.core.humanik_preview` for backward
compatibility -- this is where the classification strings themselves are
produced (see :func:`_classify_constraint`), so it is the canonical home.
"""


@dataclass(frozen=True)
class HumanIkConstraintFacts:
    """Read/write facts collected for one Maya constraint node."""

    node: str
    node_type: str
    reads: Tuple[str, ...] = ()
    writes: Tuple[str, ...] = ()
    complete: bool = True


def classify_humanik_constraints(
    facts: Iterable[HumanIkConstraintFacts],
    hik_joints: Iterable[str],
) -> Dict[str, Any]:
    """Classify constraint ownership using connections rather than names."""
    rows = sorted(facts, key=lambda item: (item.node_type, item.node))
    hik = {str(joint) for joint in hik_joints}
    reachable_from_hik = _reachable_joints(rows, hik)
    report_rows = [
        _classify_constraint(row, hik, reachable_from_hik)
        for row in rows
    ]
    counts: Dict[str, int] = {}
    for row in report_rows:
        classification = row["classification"]
        counts[classification] = counts.get(classification, 0) + 1
    writer_index: Dict[str, List[Dict[str, str]]] = {}
    for row in report_rows:
        for destination in row["writes"]:
            writer_index.setdefault(destination, []).append(
                {
                    "node": row["node"],
                    "nodeType": row["nodeType"],
                    "classification": row["classification"],
                }
            )
    for entries in writer_index.values():
        entries.sort(key=lambda item: (item["nodeType"], item["node"]))
    return {
        "hikJointCount": len(hik),
        "nodeCount": len(report_rows),
        "counts": dict(sorted(counts.items())),
        "writerIndex": dict(sorted(writer_index.items())),
        "rows": report_rows,
    }


def collect_hik_ownership_report(
    hik_joints: Iterable[str],
    cmds_module=None,
) -> Dict[str, Any]:
    """Collect and classify constraint facts for a set of HIK joints in one call.

    This is the ``collect_humanik_constraint_facts`` + ``classify_humanik_constraints``
    pair that ``humanik_control_rig``, ``humanik_frontend``, and
    ``humanik_stance`` each performed inline before this consolidation.

    Args:
        hik_joints: The HIK-assigned primary joints (long paths) to classify
            ownership against.
        cmds_module: Optional Maya ``cmds`` compatible module for tests.

    Returns:
        The same report shape ``classify_humanik_constraints`` returns.
    """
    cmds = cmds_module or maya_cmds()
    return classify_humanik_constraints(collect_humanik_constraint_facts(cmds_module=cmds), hik_joints)


def split_ownership_rows(
    report: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    """Split a classified ownership report into blockers, mute rows, and keep-post nodes.

    This is the exact row-selection trio ``humanik_control_rig.begin_humanik_control_rig``
    and ``humanik_preview.begin_humanik_target_preview`` each duplicated verbatim.

    Args:
        report: A report as returned by :func:`classify_humanik_constraints` or
            :func:`collect_hik_ownership_report`.

    Returns:
        A ``(blockers, mute_rows, retained_nodes)`` tuple: rows whose
        classification blocks the operation, rows classified
        ``mute_for_hik``, and the sorted node names classified ``keep_post``.
    """
    rows = report.get("rows", [])
    blockers = [row for row in rows if row.get("classification") in BLOCKING_CLASSIFICATIONS]
    mute_rows = [row for row in rows if row.get("classification") == "mute_for_hik"]
    retained_nodes = sorted(row["node"] for row in rows if row.get("classification") == "keep_post")
    return blockers, mute_rows, retained_nodes


def collect_humanik_constraint_facts(
    cmds_module=None,
    node_types: Sequence[str] = SUPPORTED_NODE_TYPES,
) -> List[HumanIkConstraintFacts]:
    """Read Append, CCDIK, and physics node connections from a Maya scene."""
    cmds = cmds_module or maya_cmds()
    facts: List[HumanIkConstraintFacts] = []
    for node_type in node_types:
        for node in sorted(cmds.ls(type=node_type) or []):
            reads = _connected_joint_plugs(cmds, node, incoming=True)
            writes = _connected_joint_plugs(cmds, node, incoming=False)
            facts.append(
                HumanIkConstraintFacts(
                    node=str(node),
                    node_type=str(node_type),
                    reads=tuple(sorted(reads)),
                    writes=tuple(sorted(writes)),
                    complete=bool(reads or writes),
                )
            )
    return facts


def snapshot_constraint_connections(
    cmds_module=None,
    node_types: Sequence[str] = SUPPORTED_NODE_TYPES,
) -> Dict[str, List[str]]:
    """Return stable raw connection snapshots for the report-only mutation gate."""
    cmds = cmds_module or maya_cmds()
    result: Dict[str, List[str]] = {}
    for node_type in node_types:
        for node in sorted(cmds.ls(type=node_type) or []):
            values = cmds.listConnections(
                node,
                source=True,
                destination=True,
                plugs=True,
                connections=True,
            ) or []
            result[str(node)] = [str(value) for value in values]
    return result


def _classify_constraint(
    facts: HumanIkConstraintFacts,
    hik: set[str],
    reachable_from_hik: set[str],
) -> Dict[str, Any]:
    read_joints = {_joint_from_plug(plug) for plug in facts.reads}
    write_joints = {_joint_from_plug(plug) for plug in facts.writes}
    read_joints.discard("")
    write_joints.discard("")
    read_hik = sorted(read_joints & hik)
    write_hik = sorted(write_joints & hik)
    read_outside = sorted(read_joints - hik)
    write_outside = sorted(write_joints - hik)

    if not facts.complete or not facts.writes:
        classification = "manual"
        reason = "incomplete_read_write_set"
    elif write_hik and facts.node_type in PHYSICS_NODE_TYPES:
        classification = "physics_blocker"
        reason = "physics_writer_owns_hik_channel"
    elif write_hik and any(joint in reachable_from_hik for joint in read_outside):
        classification = "feedback_blocker"
        reason = "outside_joint_reads_back_into_hik"
    elif write_hik:
        classification = "mute_for_hik"
        reason = "non_hik_writer_owns_hik_channel"
    elif read_hik and write_outside and not write_hik:
        classification = "keep_post"
        reason = "reads_hik_and_writes_only_outside"
    elif write_outside and not write_hik:
        classification = "keep_post"
        reason = "writes_only_outside_hik"
    else:
        classification = "manual"
        reason = "ownership_not_proven"

    return {
        "node": facts.node,
        "nodeType": facts.node_type,
        "classification": classification,
        "reason": reason,
        "reads": list(facts.reads),
        "writes": list(facts.writes),
        "readHikJoints": read_hik,
        "writeHikJoints": write_hik,
        "readOutsideJoints": read_outside,
        "writeOutsideJoints": write_outside,
    }


def _reachable_joints(
    facts: Sequence[HumanIkConstraintFacts],
    starts: set[str],
) -> set[str]:
    edges: Dict[str, set[str]] = {}
    for item in facts:
        reads = {_joint_from_plug(plug) for plug in item.reads}
        writes = {_joint_from_plug(plug) for plug in item.writes}
        for source in reads - {""}:
            edges.setdefault(source, set()).update(writes - {""})
    reached = set(starts)
    pending = list(starts)
    while pending:
        source = pending.pop()
        for target in edges.get(source, ()):
            if target not in reached:
                reached.add(target)
                pending.append(target)
    return reached


def _connected_joint_plugs(cmds, node: str, incoming: bool) -> set[str]:
    values = cmds.listConnections(
        node,
        source=incoming,
        destination=not incoming,
        plugs=True,
        connections=True,
    ) or []
    result = set()
    for index in range(0, len(values) - 1, 2):
        left, right = str(values[index]), str(values[index + 1])
        external = right if left.startswith(f"{node}.") else left
        external_node = external.rsplit(".", 1)[0] if "." in external else external
        try:
            if cmds.nodeType(external_node) != "joint":
                continue
        except Exception:
            continue
        long_names = cmds.ls(external_node, long=True) or [external_node]
        attr = external.split(".", 1)[1] if "." in external else ""
        result.add(f"{long_names[0]}.{attr}")
    return result


def _joint_from_plug(plug: str) -> str:
    return str(plug).rsplit(".", 1)[0] if "." in str(plug) else ""
