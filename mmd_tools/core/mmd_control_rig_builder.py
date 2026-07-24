"""Create and remove the detached NURBS controls for an MMD-native rig.

This first builder slice creates an ATTACHED, display-only control hierarchy.
It never reparents the imported skeleton and never connects controller outputs
to MMD joints.  Model-root metadata records exact node UUID ownership so later
state transitions and removal can fail closed instead of deleting user nodes.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import re
from typing import Any, Dict, List, Mapping, Optional, Tuple

from mmd_tools.core.constants import ATTR_MMD_CONTROL_RIG_JSON
from mmd_tools.core.humanik_utils import maya_cmds
from mmd_tools.core.mmd_control_rig_analyzer import (
    MmdControlRigSpec,
    analyze_mmd_control_rig,
)


CONTROL_RIG_METADATA_SCHEMA = "mmd_tools.mmd_control_rig"
CONTROL_RIG_METADATA_VERSION = 1
CONTROL_RIG_ATTACHED = "ATTACHED"
CONTROL_RIG_EDIT = "EDIT"
CONTROL_RIG_BAKED = "BAKED"
CONTROL_RIG_STATES = frozenset({CONTROL_RIG_ATTACHED, CONTROL_RIG_EDIT, CONTROL_RIG_BAKED})


class MmdControlRigBuildError(RuntimeError):
    """Raised when safe creation, recovery, or removal cannot be proven."""


@dataclass(frozen=True)
class MmdControlRigBuildResult:
    """Stable scene nodes returned by a successful control-rig build."""

    model_root: str
    control_group: str
    selection_set: str
    controls: Mapping[str, str]
    zero_groups: Mapping[str, str]
    state: str = CONTROL_RIG_ATTACHED
    created: bool = True


_ROLE_SHAPES = {
    "master": "circle",
    "center": "square",
    "groove": "diamond",
    "left_foot_ik": "foot",
    "right_foot_ik": "foot",
    "lower_body": "square",
    "upper_body": "circle",
    "upper_body2": "circle",
    "neck": "circle",
    "head": "circle",
    "left_shoulder": "circle",
    "left_arm": "circle",
    "left_elbow": "circle",
    "left_wrist": "circle",
    "right_shoulder": "circle",
    "right_arm": "circle",
    "right_elbow": "circle",
    "right_wrist": "circle",
}
_ROLE_COLORS = {
    "master": 17,
    "center": 17,
    "groove": 14,
    "left_foot_ik": 6,
    "right_foot_ik": 13,
    "lower_body": 14,
    "upper_body": 17,
    "upper_body2": 17,
    "neck": 17,
    "head": 17,
    "left_shoulder": 6,
    "left_arm": 6,
    "left_elbow": 6,
    "left_wrist": 6,
    "right_shoulder": 13,
    "right_arm": 13,
    "right_elbow": 13,
    "right_wrist": 13,
}

_ROLE_PARENTS = {
    "center": "master",
    "groove": "center",
    "left_foot_ik": "master",
    "right_foot_ik": "master",
    "lower_body": "groove",
    "upper_body": "groove",
    "upper_body2": "upper_body",
    "neck": "upper_body2",
    "head": "neck",
    "left_shoulder": "upper_body2",
    "left_arm": "left_shoulder",
    "left_elbow": "left_arm",
    "left_wrist": "left_elbow",
    "right_shoulder": "upper_body2",
    "right_arm": "right_shoulder",
    "right_elbow": "right_arm",
    "right_wrist": "right_elbow",
}


def build_mmd_control_rig(
    model_root: str,
    *,
    cmds_module=None,
    spec: Optional[MmdControlRigSpec] = None,
) -> MmdControlRigBuildResult:
    """Create an idempotent detached MVP control hierarchy for one MMD model."""
    cmds = cmds_module or maya_cmds()
    root = _canonical_node(cmds, model_root)
    existing = _read_metadata(cmds, root)
    if existing is not None:
        return _result_from_metadata(cmds, root, existing, created=False)

    rig_spec = spec or analyze_mmd_control_rig(root, cmds_module=cmds)
    if rig_spec.model_root != root:
        raise MmdControlRigBuildError("control-rig spec belongs to a different model root")
    if not rig_spec.can_build_mvp:
        detail = "; ".join(rig_spec.blockers) or "MVP role binding is incomplete"
        raise MmdControlRigBuildError(f"MMD control rig is not buildable: {detail}")

    metadata_before = _raw_metadata(cmds, root)
    created_roots: List[str] = []
    with _undo_chunk(cmds, "Build MMD Control Rig"):
        try:
            prefix = _safe_prefix(root)
            control_group = cmds.group(empty=True, name=f"{prefix}_MMD_CONTROLS_GRP")
            created_roots.append(control_group)
            selection_set = cmds.sets(empty=True, name=f"{prefix}_MMD_CONTROLS_SET")
            created_roots.append(selection_set)
            scale = _controller_scale(cmds, root)
            controls: Dict[str, str] = {}
            zero_groups: Dict[str, str] = {}
            bindings: Dict[str, Dict[str, Any]] = {}
            for role_binding in rig_spec.roles:
                binding = role_binding.binding
                if binding is None or binding.blocked:
                    continue
                role = role_binding.role
                zero = cmds.createNode(
                    "transform",
                    name=f"{prefix}_{role}_ZERO",
                    parent=control_group,
                )
                matrix = cmds.xform(
                    binding.joint,
                    query=True,
                    worldSpace=True,
                    matrix=True,
                )
                cmds.xform(zero, worldSpace=True, matrix=matrix)
                control = _create_control_curve(
                    cmds,
                    f"{prefix}_{role}_CTRL",
                    _ROLE_SHAPES[role],
                    scale,
                )
                created_roots.append(control)
                cmds.parent(control, zero)
                cmds.setAttr(f"{control}.translate", 0.0, 0.0, 0.0, type="double3")
                cmds.setAttr(f"{control}.rotate", 0.0, 0.0, 0.0, type="double3")
                _color_control(cmds, control, _ROLE_COLORS[role])
                cmds.sets(control, add=selection_set)
                controls[role] = str(control)
                zero_groups[role] = str(zero)
                bindings[role] = {
                    "joint": binding.joint,
                    "inputKind": binding.input_kind,
                    "authoredPlugs": list(binding.authored_plugs),
                    "ikSolvers": list(binding.ik_solvers),
                    "fallback": role_binding.fallback,
                }

            for role, zero in zero_groups.items():
                parent_role = _available_parent_role(role, controls)
                if parent_role:
                    cmds.parent(zero, controls[parent_role])

            nodes = _owned_nodes(cmds, control_group, selection_set)
            metadata = {
                "schema": CONTROL_RIG_METADATA_SCHEMA,
                "version": CONTROL_RIG_METADATA_VERSION,
                "state": CONTROL_RIG_ATTACHED,
                "modelRootUuid": _node_uuid(cmds, root),
                "controlGroupUuid": _node_uuid(cmds, control_group),
                "selectionSetUuid": _node_uuid(cmds, selection_set),
                "nodes": [
                    {"uuid": _node_uuid(cmds, node), "name": str(node)}
                    for node in nodes
                ],
                "controls": {
                    role: _node_uuid(cmds, node)
                    for role, node in sorted(controls.items())
                },
                "zeroGroups": {
                    role: _node_uuid(cmds, node)
                    for role, node in sorted(zero_groups.items())
                },
                "bindings": bindings,
            }
            _write_metadata(cmds, root, metadata)
            return _result_from_metadata(cmds, root, metadata, created=True)
        except Exception:
            for node in reversed(created_roots):
                if cmds.objExists(node):
                    cmds.delete(node)
            _restore_raw_metadata(cmds, root, metadata_before)
            raise


def remove_mmd_control_rig(model_root: str, *, cmds_module=None) -> bool:
    """Delete only UUID-proven owned rig nodes and clear model metadata."""
    cmds = cmds_module or maya_cmds()
    root = _canonical_node(cmds, model_root)
    metadata = _read_metadata(cmds, root)
    if metadata is None:
        return False
    if metadata["state"] not in {CONTROL_RIG_ATTACHED, CONTROL_RIG_BAKED}:
        raise MmdControlRigBuildError("return the control rig to ATTACHED before removal")
    if _node_uuid(cmds, root) != metadata.get("modelRootUuid"):
        raise MmdControlRigBuildError("control-rig metadata model UUID mismatch")
    resolved = _resolve_owned_nodes(cmds, metadata)
    control_group = resolved[metadata["controlGroupUuid"]]
    selection_set = resolved[metadata["selectionSetUuid"]]
    actual = set(
        [control_group]
        + list(
            cmds.listRelatives(
                control_group,
                allDescendents=True,
                fullPath=True,
            )
            or []
        )
    )
    recorded_dag = set(resolved.values()) - {selection_set}
    if actual != recorded_dag:
        changed = ", ".join(sorted(actual.symmetric_difference(recorded_dag)))
        raise MmdControlRigBuildError(
            f"control group ownership topology changed: {changed}"
        )
    with _undo_chunk(cmds, "Remove MMD Control Rig"):
        if cmds.objExists(selection_set):
            cmds.delete(selection_set)
        if cmds.objExists(control_group):
            cmds.delete(control_group)
        if cmds.attributeQuery(ATTR_MMD_CONTROL_RIG_JSON, node=root, exists=True):
            cmds.deleteAttr(f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}")
    return True


def read_mmd_control_rig_metadata(model_root: str, *, cmds_module=None) -> Optional[Dict[str, Any]]:
    """Return validated scene metadata without exposing mutable internal state."""
    cmds = cmds_module or maya_cmds()
    root = _canonical_node(cmds, model_root)
    metadata = _read_metadata(cmds, root)
    return dict(metadata) if metadata is not None else None


def _result_from_metadata(
    cmds,
    root: str,
    metadata: Mapping[str, Any],
    *,
    created: bool,
) -> MmdControlRigBuildResult:
    resolved = _resolve_owned_nodes(cmds, metadata)
    if _node_uuid(cmds, root) != metadata.get("modelRootUuid"):
        raise MmdControlRigBuildError("control-rig metadata model UUID mismatch")
    controls = {
        role: resolved[uuid]
        for role, uuid in sorted(metadata.get("controls", {}).items())
    }
    zero_groups = {
        role: resolved[uuid]
        for role, uuid in sorted(metadata.get("zeroGroups", {}).items())
    }
    return MmdControlRigBuildResult(
        model_root=root,
        control_group=resolved[metadata["controlGroupUuid"]],
        selection_set=resolved[metadata["selectionSetUuid"]],
        controls=controls,
        zero_groups=zero_groups,
        state=str(metadata["state"]),
        created=created,
    )


def _read_metadata(cmds, root: str) -> Optional[Dict[str, Any]]:
    raw = _raw_metadata(cmds, root)
    if not raw:
        return None
    try:
        metadata = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise MmdControlRigBuildError("invalid MMD control-rig metadata JSON") from exc
    if not isinstance(metadata, dict):
        raise MmdControlRigBuildError("MMD control-rig metadata must be an object")
    required = {
        "schema": CONTROL_RIG_METADATA_SCHEMA,
        "version": CONTROL_RIG_METADATA_VERSION,
    }
    for key, expected in required.items():
        if metadata.get(key) != expected:
            raise MmdControlRigBuildError(f"unsupported control-rig metadata {key}")
    if metadata.get("state") not in CONTROL_RIG_STATES:
        raise MmdControlRigBuildError("unsupported control-rig metadata state")
    for key in ("modelRootUuid", "controlGroupUuid", "selectionSetUuid"):
        if not isinstance(metadata.get(key), str) or not metadata[key]:
            raise MmdControlRigBuildError(f"control-rig metadata missing {key}")
    if not isinstance(metadata.get("nodes"), list):
        raise MmdControlRigBuildError("control-rig metadata nodes must be an array")
    return metadata


def _resolve_owned_nodes(cmds, metadata: Mapping[str, Any]) -> Dict[str, str]:
    resolved = {}
    for row in metadata.get("nodes", []):
        if not isinstance(row, dict) or not isinstance(row.get("uuid"), str):
            raise MmdControlRigBuildError("invalid owned-node metadata row")
        uuid = row["uuid"]
        nodes = cmds.ls(uuid, long=True) or []
        if len(nodes) != 1:
            raise MmdControlRigBuildError(f"owned control-rig node is missing: {uuid}")
        resolved[uuid] = str(nodes[0])
    for uuid in (
        metadata["controlGroupUuid"],
        metadata["selectionSetUuid"],
        *metadata.get("controls", {}).values(),
        *metadata.get("zeroGroups", {}).values(),
    ):
        if uuid not in resolved:
            raise MmdControlRigBuildError(f"unrecorded control-rig UUID: {uuid}")
    return resolved


def _create_control_curve(cmds, name: str, shape: str, scale: float) -> str:
    if shape == "circle":
        points = [
            (1.0, 0.0, 0.0),
            (0.707, 0.0, 0.707),
            (0.0, 0.0, 1.0),
            (-0.707, 0.0, 0.707),
            (-1.0, 0.0, 0.0),
            (-0.707, 0.0, -0.707),
            (0.0, 0.0, -1.0),
            (0.707, 0.0, -0.707),
            (1.0, 0.0, 0.0),
        ]
    elif shape == "diamond":
        points = [(0, 0, 1), (1, 0, 0), (0, 0, -1), (-1, 0, 0), (0, 0, 1)]
    elif shape == "foot":
        points = [(-0.6, 0, 1.2), (0.6, 0, 1.2), (0.8, 0, -1), (-0.8, 0, -1), (-0.6, 0, 1.2)]
    else:
        points = [(-1, 0, -1), (-1, 0, 1), (1, 0, 1), (1, 0, -1), (-1, 0, -1)]
    scaled = [(x * scale, y * scale, z * scale) for x, y, z in points]
    return str(cmds.curve(name=name, degree=1, point=scaled))


def _color_control(cmds, control: str, color: int) -> None:
    for shape in cmds.listRelatives(control, shapes=True, fullPath=True) or []:
        cmds.setAttr(f"{shape}.overrideEnabled", True)
        cmds.setAttr(f"{shape}.overrideColor", int(color))


def _available_parent_role(role: str, controls: Mapping[str, str]) -> Optional[str]:
    parent = _ROLE_PARENTS.get(role)
    while parent and parent not in controls:
        parent = _ROLE_PARENTS.get(parent)
    return parent


def _controller_scale(cmds, root: str) -> float:
    try:
        bounds = [float(value) for value in cmds.exactWorldBoundingBox(root)]
        height = abs(bounds[4] - bounds[1])
        return max(height * 0.04, 0.25)
    except Exception:
        return 1.0


def _owned_nodes(cmds, control_group: str, selection_set: str) -> Tuple[str, ...]:
    descendants = cmds.listRelatives(
        control_group,
        allDescendents=True,
        fullPath=True,
    ) or []
    group = _canonical_node(cmds, control_group)
    selection = _canonical_node(cmds, selection_set)
    return tuple(sorted(set([group, selection] + [str(node) for node in descendants])))


def _write_metadata(cmds, root: str, metadata: Mapping[str, Any]) -> None:
    if not cmds.attributeQuery(ATTR_MMD_CONTROL_RIG_JSON, node=root, exists=True):
        cmds.addAttr(root, longName=ATTR_MMD_CONTROL_RIG_JSON, dataType="string")
    cmds.setAttr(
        f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}",
        json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        type="string",
    )


def _raw_metadata(cmds, root: str) -> Optional[str]:
    if not cmds.attributeQuery(ATTR_MMD_CONTROL_RIG_JSON, node=root, exists=True):
        return None
    return cmds.getAttr(f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}") or None


def _restore_raw_metadata(cmds, root: str, raw: Optional[str]) -> None:
    if raw is None:
        if cmds.attributeQuery(ATTR_MMD_CONTROL_RIG_JSON, node=root, exists=True):
            cmds.deleteAttr(f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}")
        return
    if not cmds.attributeQuery(ATTR_MMD_CONTROL_RIG_JSON, node=root, exists=True):
        cmds.addAttr(root, longName=ATTR_MMD_CONTROL_RIG_JSON, dataType="string")
    cmds.setAttr(f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}", raw, type="string")


def _canonical_node(cmds, node: str) -> str:
    nodes = cmds.ls(node, long=True) or []
    if len(nodes) != 1:
        raise MmdControlRigBuildError(f"expected one scene node: {node}")
    return str(nodes[0])


def _node_uuid(cmds, node: str) -> str:
    values = cmds.ls(node, uuid=True) or []
    if len(values) != 1:
        raise MmdControlRigBuildError(f"could not resolve node UUID: {node}")
    return str(values[0])


def _safe_prefix(root: str) -> str:
    leaf = root.rsplit("|", 1)[-1].replace(":", "_")
    return re.sub(r"[^A-Za-z0-9_]+", "_", leaf).strip("_") or "MMDModel"


@contextmanager
def _undo_chunk(cmds, label: str):
    """Group one public builder mutation into a single Maya Undo step."""
    cmds.undoInfo(openChunk=True, chunkName=label)
    try:
        yield
    finally:
        cmds.undoInfo(closeChunk=True)
