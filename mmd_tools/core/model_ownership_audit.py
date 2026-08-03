"""Read-only audit helpers for MMD model-root ownership boundaries.

The importer currently stores ownership on several Maya node families.  This
module does not mutate a scene; it reports the root ``message`` fan-out and
legacy ``mmd_model_root`` links so a future scene registry can be introduced
without guessing about existing scenes or crossing namespaces.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from maya import cmds

from .constants import (
    ATTR_MMD_MODEL_NAME,
    ATTR_MMD_MODEL_NAME_EN,
    ATTR_MMD_MODEL_ROOT,
    SCENE_ROOT_SUFFIX,
)


AUDIT_SCHEMA_VERSION = 1


def audit_model_root(model_root: str) -> Dict[str, Any]:
    """Return a JSON-safe, read-only ownership report for one model root.

    Args:
        model_root: A short or full Maya DAG path for an MMD model root.

    Returns:
        A report containing canonical root identity, root message fan-out,
        legacy owner links found in the scene, and bounded findings.  The
        report never changes Maya state and uses ``status=invalid`` when the
        requested root cannot be resolved to exactly one DAG path.
    """
    canonical_root = _canonical_dag_root(model_root)
    if canonical_root is None:
        return {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "root": model_root,
            "status": "invalid",
            "findings": [
                {
                    "code": "INVALID_MODEL_ROOT",
                    "severity": "error",
                    "message": "model root does not resolve to one full DAG path",
                }
            ],
        }

    connections = _root_message_connections(canonical_root)
    legacy_owner_links = _legacy_owner_links(canonical_root)
    findings: List[Dict[str, str]] = []

    legacy_fanout_count = sum(
        1 for connection in connections if connection["status"] == "migration_required"
    )
    unknown_destinations = [
        connection for connection in connections if connection["status"] == "unknown"
    ]
    if legacy_fanout_count:
        findings.append(
            {
                "code": "ROOT_LEGACY_MESSAGE_FANOUT",
                "severity": "warning",
                "message": (
                    f"root.message has {legacy_fanout_count} legacy ownership "
                    "destination(s); migrate through a model registry"
                ),
            }
        )
    if unknown_destinations:
        findings.append(
            {
                "code": "ROOT_UNKNOWN_MESSAGE_DESTINATION",
                "severity": "error",
                "message": f"root.message has {len(unknown_destinations)} unknown destination(s)",
            }
        )

    ambiguous = [
        link for link in legacy_owner_links if link["status"] in {"orphaned", "ambiguous", "invalid"}
    ]
    if ambiguous:
        findings.append(
            {
                "code": "LEGACY_OWNER_AMBIGUITY",
                "severity": "error",
                "message": f"{len(ambiguous)} legacy owner link(s) are not exactly one valid root",
            }
        )

    status = "fail" if any(item["severity"] == "error" for item in findings) else "pass"
    if status == "pass" and legacy_fanout_count:
        status = "migration_required"

    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "root": canonical_root,
        "namespace": _namespace_from_root(canonical_root),
        "status": status,
        "root_message": {
            "connection_count": len(connections),
            "legacy_fanout_count": legacy_fanout_count,
            "connections": connections,
        },
        "legacy_owner_links": legacy_owner_links,
        "findings": findings,
    }


def audit_scene_model_roots() -> Dict[str, Any]:
    """Audit every discoverable MMD model root in the current Maya scene."""
    roots = discover_model_roots()
    reports = [audit_model_root(root) for root in roots]
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "model_count": len(reports),
        "status": aggregate_model_audit_status(reports),
        "models": reports,
    }


def aggregate_model_audit_status(reports: List[Dict[str, Any]]) -> str:
    """Return the fail-closed status for a collection of model reports.

    Invalid explicit roots are treated as failures just like unknown graph
    destinations.  Legacy links remain an actionable migration state, not a
    green result, while a scene with no findings is ``pass``.
    """
    if any(report.get("status") in {"fail", "invalid"} for report in reports):
        return "fail"
    if any(report.get("status") == "migration_required" for report in reports):
        return "migration_required"
    return "pass"


def discover_model_roots() -> List[str]:
    """Return namespace-safe full DAG paths for MMD model roots."""
    candidates = set()
    for pattern in (f"*{SCENE_ROOT_SUFFIX}", f"*:*{SCENE_ROOT_SUFFIX}"):
        candidates.update(cmds.ls(pattern, type="transform", long=True) or [])

    roots = []
    for candidate in sorted(candidates):
        if not (
            _has_attr(candidate, ATTR_MMD_MODEL_NAME)
            or _has_attr(candidate, ATTR_MMD_MODEL_NAME_EN)
        ):
            continue
        canonical = _canonical_dag_root(candidate)
        if canonical:
            roots.append(canonical)
    return sorted(set(roots))


def _root_message_connections(root: str) -> List[Dict[str, str]]:
    destinations = cmds.listConnections(
        f"{root}.message",
        source=False,
        destination=True,
        plugs=True,
    ) or []
    result = []
    for destination in sorted(set(str(value) for value in destinations)):
        node, attr = _split_plug(destination)
        category, status = _classify_root_message_destination(node, attr)
        result.append(
            {
                "destination": destination,
                "node": node,
                "attribute": attr,
                "category": category,
                "status": status,
            }
        )
    return result


def _legacy_owner_links(root: str) -> List[Dict[str, Any]]:
    nodes = cmds.ls(f"*.{ATTR_MMD_MODEL_ROOT}", objectsOnly=True, long=True) or []
    result = []
    for node in sorted(set(str(value) for value in nodes)):
        roots = cmds.listConnections(
            f"{node}.{ATTR_MMD_MODEL_ROOT}",
            source=True,
            destination=False,
        ) or []
        canonical_roots = [_canonical_dag_root(value) for value in roots]
        valid_roots = [value for value in canonical_roots if value is not None]
        if len(roots) == 0:
            status = "orphaned"
        elif len(roots) != 1:
            status = "ambiguous"
        elif len(valid_roots) != 1:
            status = "invalid"
        elif valid_roots[0] == root:
            status = "owned"
        else:
            status = "other_model"
        result.append(
            {
                "node": node,
                "node_type": _node_type(node),
                "connected_roots": valid_roots,
                "status": status,
            }
        )
    return result


def _classify_root_message_destination(node: str, attr: str) -> Tuple[str, str]:
    node_type = _node_type(node)
    if node_type in {"bindPose", "dagPose"} and attr.startswith("members["):
        return "maya_bind_pose", "standard"
    if attr.split("[", 1)[0] == ATTR_MMD_MODEL_ROOT:
        return "legacy_owner_link", "migration_required"
    if node_type == "mmdPhysicsSolver" and attr == "modelRoot":
        return "legacy_physics_solver", "migration_required"
    return "unknown", "unknown"


def _canonical_dag_root(node: Any) -> Optional[str]:
    if not node or not cmds.objExists(node):
        return None
    matches = cmds.ls(node, long=True) or []
    if len(matches) != 1:
        return None
    candidate = str(matches[0])
    if not candidate.startswith("|"):
        return None
    if not candidate.rsplit("|", 1)[-1].endswith(SCENE_ROOT_SUFFIX):
        return None
    if not (_has_attr(candidate, ATTR_MMD_MODEL_NAME) or _has_attr(candidate, ATTR_MMD_MODEL_NAME_EN)):
        return None
    return candidate


def _namespace_from_root(root: str) -> Optional[str]:
    leaf = root.rsplit("|", 1)[-1]
    if ":" not in leaf:
        return None
    return leaf.rsplit(":", 1)[0]


def _split_plug(plug: str) -> Tuple[str, str]:
    if "." not in plug:
        return plug, ""
    return plug.rsplit(".", 1)


def _node_type(node: str) -> str:
    try:
        return str(cmds.nodeType(node) or "")
    except Exception:
        return ""


def _has_attr(node: str, attr: str) -> bool:
    try:
        return bool(cmds.attributeQuery(attr, node=node, exists=True))
    except Exception:
        return False


__all__ = [
    "AUDIT_SCHEMA_VERSION",
    "aggregate_model_audit_status",
    "audit_model_root",
    "audit_scene_model_roots",
    "discover_model_roots",
]
