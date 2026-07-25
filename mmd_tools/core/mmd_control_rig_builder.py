"""Create and remove the detached NURBS controls for an MMD-native rig.

This first builder slice creates an ATTACHED, display-only control hierarchy.
It never reparents the imported skeleton and never connects controller outputs
to MMD joints.  Model-root metadata records exact node UUID ownership so later
state transitions and removal can fail closed instead of deleting user nodes.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from mmd_tools.core.constants import (
    ATTR_MMD_BONE_FLAGS,
    ATTR_MMD_BONE_OFFSET,
    ATTR_MMD_CONNECT_INDEX,
    ATTR_MMD_CONTROL_RIG_JSON,
    ATTR_MMD_PMX_REST_POSITION,
)
from mmd_tools.core.humanik_utils import maya_cmds
from mmd_tools.core.mmd_control_rig_analyzer import (
    MmdControlRigRoleBinding,
    MmdControlRigSpec,
    STATUS_FALLBACK,
    analyze_mmd_control_rig,
)
from mmd_tools.core.pmx_data.bone import PmxBoneFlag


CONTROL_RIG_METADATA_SCHEMA = "mmd_tools.mmd_control_rig"
CONTROL_RIG_METADATA_VERSION = 3
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


_FINGER_ROLE_CHAINS = tuple(
    tuple(f"{side}_{finger}_{index}" for index in indexes)
    for side in ("left", "right")
    for finger, indexes in (
        ("thumb", (0, 1, 2)),
        ("index", (1, 2, 3)),
        ("middle", (1, 2, 3)),
        ("ring", (1, 2, 3)),
        ("pinky", (1, 2, 3)),
    )
)
_FINGER_ROLES = tuple(role for chain in _FINGER_ROLE_CHAINS for role in chain)
_FINGER_ROLE_PARENTS = {
    role: (f"{role.split('_', 1)[0]}_wrist" if index == 0 else chain[index - 1])
    for chain in _FINGER_ROLE_CHAINS
    for index, role in enumerate(chain)
}

_ROLE_COLORS = {
    "master": 17,
    "center": 17,
    "groove": 14,
    "left_foot_ik": 6,
    "right_foot_ik": 13,
    "waist": 14,
    "left_foot_ik_parent": 6,
    "right_foot_ik_parent": 13,
    "left_toe_ik": 6,
    "right_toe_ik": 13,
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
    "left_leg": 6,
    "left_knee": 6,
    "right_leg": 13,
    "right_knee": 13,
    **{role: 6 if role.startswith("left_") else 13 for role in _FINGER_ROLES},
}

_ROLE_PARENTS = {
    "center": "master",
    "groove": "center",
    "left_foot_ik_parent": "master",
    "right_foot_ik_parent": "master",
    "left_foot_ik": "left_foot_ik_parent",
    "right_foot_ik": "right_foot_ik_parent",
    "left_toe_ik": "left_foot_ik",
    "right_toe_ik": "right_foot_ik",
    "waist": "groove",
    "lower_body": "waist",
    "upper_body": "waist",
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
    "left_leg": "groove",
    "left_knee": "left_leg",
    "right_leg": "groove",
    "right_knee": "right_leg",
    **_FINGER_ROLE_PARENTS,
}

_ROLE_TEMPLATE_ALIASES = {
    **{role: "finger" for role in _FINGER_ROLES},
    "waist": "circle",
    "left_foot_ik_parent": "circle",
    "right_foot_ik_parent": "circle",
    "left_toe_ik": "circle",
    "right_toe_ik": "circle",
}

_AUTO_ORIENT_SHAPE_ROLES = frozenset(
    {
        "waist",
        "lower_body",
        "upper_body",
        "upper_body2",
        "neck",
        "head",
        "left_shoulder",
        "left_arm",
        "left_elbow",
        "left_wrist",
        "right_shoulder",
        "right_arm",
        "right_elbow",
        "right_wrist",
        "left_leg",
        "left_knee",
        "right_leg",
        "right_knee",
        *_FINGER_ROLES,
    }
)


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
            namespace = _namespace_prefix(root)
            group_kwargs = {
                "empty": True,
                "name": f"{namespace}Controls",
            }
            control_group_parent = _control_group_parent(rig_spec, root)
            if control_group_parent is not None:
                group_kwargs["parent"] = control_group_parent
            control_group = cmds.group(**group_kwargs)
            created_roots.append(control_group)
            selection_set = cmds.sets(empty=True, name=f"{namespace}Controls_SET")
            created_roots.append(selection_set)
            scale = _controller_scale(cmds, root)
            display_reference_time = _current_time(cmds)
            controls: Dict[str, str] = {}
            zero_groups: Dict[str, str] = {}
            bindings: Dict[str, Dict[str, Any]] = {}
            indexed_joints = {
                bone.bone_index: bone.joint
                for bone in rig_spec.bones
                if bone.bone_index is not None
            }
            for role_binding in rig_spec.roles:
                if not _should_build_role_control(role_binding):
                    continue
                binding = role_binding.binding
                assert binding is not None
                role = role_binding.role
                zero = cmds.createNode(
                    "transform",
                    name=f"{namespace}{role}_ZERO",
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
                    f"{namespace}{role}_CTRL",
                    role,
                    scale,
                    shape_rotation=_control_shape_rotation(
                        cmds,
                        root,
                        role,
                        binding,
                        indexed_joints,
                    ),
                )
                created_roots.append(control)
                parented = cmds.parent(control, zero)
                if parented:
                    control = str(parented[0])
                control = str(cmds.rename(control, f"{namespace}{role}_CTRL"))
                _rename_control_shapes(cmds, control, namespace, role)
                cmds.setAttr(f"{control}.translate", 0.0, 0.0, 0.0, type="double3")
                cmds.setAttr(f"{control}.rotate", 0.0, 0.0, 0.0, type="double3")
                _color_control(cmds, control, _ROLE_COLORS[role])
                cmds.sets(control, add=selection_set)
                controls[role] = str(control)
                zero_groups[role] = str(zero)
                bindings[role] = _binding_metadata(role_binding, cmds_module=cmds)

            # Parent only concrete nodes.  Semantic fallback aliases are
            # added afterwards and must never be interpreted as new DAG
            # edges (e.g. groove_ZERO aliasing center_ZERO would otherwise
            # attempt to parent center_ZERO below its own child).
            _parent_zero_groups(cmds, zero_groups, controls)
            _apply_fallback_role_aliases(
                rig_spec.roles,
                controls,
                zero_groups,
                bindings,
                cmds_module=cmds,
            )

            nodes = _owned_nodes(cmds, control_group, selection_set)
            metadata = {
                "schema": CONTROL_RIG_METADATA_SCHEMA,
                "version": CONTROL_RIG_METADATA_VERSION,
                "state": CONTROL_RIG_ATTACHED,
                "displayReferenceTime": display_reference_time,
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
    if metadata.get("schema") != CONTROL_RIG_METADATA_SCHEMA:
        raise MmdControlRigBuildError("unsupported control-rig metadata schema")
    version = metadata.get("version")
    if version != CONTROL_RIG_METADATA_VERSION:
        raise MmdControlRigBuildError("unsupported control-rig metadata version")
    if metadata.get("state") not in CONTROL_RIG_STATES:
        raise MmdControlRigBuildError("unsupported control-rig metadata state")
    for key in ("modelRootUuid", "controlGroupUuid", "selectionSetUuid"):
        if not isinstance(metadata.get(key), str) or not metadata[key]:
            raise MmdControlRigBuildError(f"control-rig metadata missing {key}")
    if not isinstance(metadata.get("nodes"), list):
        raise MmdControlRigBuildError("control-rig metadata nodes must be an array")
    try:
        display_reference_time = float(metadata["displayReferenceTime"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MmdControlRigBuildError("control-rig display reference is missing") from exc
    if not math.isfinite(display_reference_time):
        raise MmdControlRigBuildError("control-rig display reference must be finite")
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


@lru_cache(maxsize=1)
def _control_curve_templates() -> Mapping[str, Tuple[Mapping[str, Any], ...]]:
    """Load the artist-authored controller shape library bundled with the plug-in."""

    path = Path(__file__).resolve().parents[1] / "config" / "mmd_control_rig_curve_shapes.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        raise MmdControlRigBuildError(f"could not load control curve templates: {path}") from exc
    if payload.get("schema") != "mmd_tools.mmd_control_rig_curve_shapes" or payload.get("version") != 1:
        raise MmdControlRigBuildError("unsupported control curve template schema")
    templates = payload.get("templates")
    if not isinstance(templates, dict):
        raise MmdControlRigBuildError("control curve templates must be an object")
    return {
        str(role): tuple(shape for shape in shapes if isinstance(shape, dict))
        for role, shapes in templates.items()
        if isinstance(shapes, list)
    }


def _create_control_curve(
    cmds,
    name: str,
    role: str,
    scale: float,
    *,
    shape_rotation=None,
) -> str:
    templates = _control_curve_templates().get(_control_curve_template_role(role), ())
    if not templates:
        raise MmdControlRigBuildError(f"missing control curve template: {role}")
    return _create_template_control_curve(
        cmds,
        name,
        templates,
        scale,
        shape_rotation=shape_rotation,
    )


def _control_curve_template_role(role: str) -> str:
    """Return the shared artist template key for one concrete control role."""

    return _ROLE_TEMPLATE_ALIASES.get(role, role)


def _create_template_control_curve(
    cmds,
    name: str,
    templates: Tuple[Mapping[str, Any], ...],
    scale: float,
    *,
    shape_rotation=None,
) -> str:
    """Create one transform containing every NURBS shape in a role template."""

    control = None
    temporary = None
    try:
        for index, template in enumerate(templates):
            points = template.get("points")
            knots = template.get("knots")
            degree = int(template.get("degree", 1))
            if not isinstance(points, list) or not points:
                raise MmdControlRigBuildError(f"invalid control curve template: {name}[{index}]")
            scaled = [tuple(float(value) * scale for value in point) for point in points]
            if shape_rotation is not None:
                scaled = [_rotate_shape_point(point, shape_rotation) for point in scaled]
            kwargs = {
                "name": name if control is None else f"{name}_SHAPE_TMP",
                "degree": degree,
                "point": scaled,
                "periodic": bool(template.get("periodic", False)),
            }
            if isinstance(knots, list) and knots:
                kwargs["knot"] = [float(value) for value in knots]
            created = str(cmds.curve(**kwargs))
            if control is None:
                control = created
                continue
            temporary = created
            for shape in cmds.listRelatives(temporary, shapes=True, fullPath=True) or []:
                cmds.parent(shape, control, shape=True, relative=True)
            cmds.delete(temporary)
            temporary = None
        if control is None:
            raise MmdControlRigBuildError(f"empty control curve template: {name}")
        return str(control)
    except Exception:
        for node in (temporary, control):
            if node and cmds.objExists(node):
                cmds.delete(node)
        raise


def _control_shape_rotation(cmds, root, role, binding, indexed_joints):
    """Infer a display-only curve rotation for a PMX chain without LocalAxis.

    Controller and ZERO transforms remain in the authored animation basis.  The
    returned shortest-arc rotation is applied only to curve CV positions.
    """
    if role not in _AUTO_ORIENT_SHAPE_ROLES:
        return None
    if binding.pmx_flags & int(PmxBoneFlag.LOCAL_AXIS):
        return None
    if _joint_chain_has_local_axis(cmds, binding.joint, root):
        return None
    direction = _pmx_tail_direction(cmds, binding, indexed_joints)
    if direction is None:
        return None
    maya_direction = (direction[0], direction[1], -direction[2])
    return _shortest_arc_from_positive_z(maya_direction)


def _joint_chain_has_local_axis(cmds, joint: str, root: str) -> bool:
    """Return whether an indexed ancestor contributes a PMX LocalAxis basis."""
    current = str(joint)
    while current and current != root:
        if cmds.attributeQuery(ATTR_MMD_BONE_FLAGS, node=current, exists=True):
            flags = int(cmds.getAttr(f"{current}.{ATTR_MMD_BONE_FLAGS}") or 0)
            if flags & int(PmxBoneFlag.LOCAL_AXIS):
                return True
        parents = cmds.listRelatives(current, parent=True, fullPath=True, type="joint") or []
        current = str(parents[0]) if parents else ""
    return False


def _pmx_tail_direction(cmds, binding, indexed_joints):
    """Return one bone's PMX-space tail vector from preserved import metadata."""
    joint = binding.joint
    if cmds.attributeQuery(ATTR_MMD_CONNECT_INDEX, node=joint, exists=True):
        target_index = int(cmds.getAttr(f"{joint}.{ATTR_MMD_CONNECT_INDEX}"))
        target = indexed_joints.get(target_index)
        source_position = _vector_attribute(cmds, joint, ATTR_MMD_PMX_REST_POSITION)
        target_position = _vector_attribute(cmds, target, ATTR_MMD_PMX_REST_POSITION)
        if source_position is not None and target_position is not None:
            return tuple(target - source for source, target in zip(source_position, target_position))
    return _vector_attribute(cmds, joint, ATTR_MMD_BONE_OFFSET)


def _vector_attribute(cmds, node, attribute):
    if not node or not cmds.attributeQuery(attribute, node=node, exists=True):
        return None
    value = cmds.getAttr(f"{node}.{attribute}")
    if isinstance(value, (list, tuple)) and len(value) == 1:
        value = value[0]
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    vector = tuple(float(component) for component in value)
    return vector if all(math.isfinite(component) for component in vector) else None


def _shortest_arc_from_positive_z(direction):
    """Return an axis/cos/sin tuple rotating local +Z onto ``direction``."""
    length = math.sqrt(sum(float(component) ** 2 for component in direction))
    if not math.isfinite(length) or length <= 1.0e-8:
        return None
    target = tuple(float(component) / length for component in direction)
    cosine = max(-1.0, min(1.0, target[2]))
    if cosine >= 1.0 - 1.0e-10:
        return ((0.0, 1.0, 0.0), 1.0, 0.0)
    if cosine <= -1.0 + 1.0e-10:
        return ((0.0, 1.0, 0.0), -1.0, 0.0)
    axis = (-target[1], target[0], 0.0)
    sine = math.sqrt(axis[0] ** 2 + axis[1] ** 2)
    axis = (axis[0] / sine, axis[1] / sine, 0.0)
    return (axis, cosine, sine)


def _rotate_shape_point(point, rotation):
    """Apply a Rodrigues rotation tuple to one controller CV position."""
    axis, cosine, sine = rotation
    x, y, z = (float(component) for component in point)
    ax, ay, az = axis
    cross = (ay * z - az * y, az * x - ax * z, ax * y - ay * x)
    dot = ax * x + ay * y + az * z
    one_minus_cosine = 1.0 - cosine
    return (
        x * cosine + cross[0] * sine + ax * dot * one_minus_cosine,
        y * cosine + cross[1] * sine + ay * dot * one_minus_cosine,
        z * cosine + cross[2] * sine + az * dot * one_minus_cosine,
    )


def _color_control(cmds, control: str, color: int) -> None:
    for shape in cmds.listRelatives(control, shapes=True, fullPath=True) or []:
        cmds.setAttr(f"{shape}.overrideEnabled", True)
        cmds.setAttr(f"{shape}.overrideColor", int(color))


def _rename_control_shapes(cmds, control: str, namespace: str, role: str) -> None:
    """Give every generated curve shape a short, deterministic scene name."""
    shapes = cmds.listRelatives(control, shapes=True, fullPath=True) or []
    for index, shape in enumerate(shapes, start=1):
        suffix = "Shape" if index == 1 else f"Shape{index}"
        cmds.rename(shape, f"{namespace}{role}_CTRL{suffix}")


def _available_parent_role(role: str, controls: Mapping[str, str]) -> Optional[str]:
    parent = _ROLE_PARENTS.get(role)
    while parent and parent not in controls:
        parent = _ROLE_PARENTS.get(parent)
    return parent


def _parent_zero_groups(
    cmds,
    zero_groups: Mapping[str, str],
    controls: Mapping[str, str],
) -> None:
    """Parent concrete zero groups below their nearest available control."""
    for role, zero in zero_groups.items():
        parent_role = _available_parent_role(role, controls)
        if parent_role:
            cmds.parent(zero, controls[parent_role])


def _should_build_role_control(role_binding: MmdControlRigRoleBinding) -> bool:
    """Return whether a role deserves its own curve control.

    A semantic fallback to another role reuses that role's authored input.  A
    second curve would therefore be inert and, once motion routing is enabled,
    would also compete for the same destination channels.  The model-root
    fallback is different: it is the only concrete binding for ``master`` and
    must still receive a control.
    """
    binding = role_binding.binding
    if binding is None or binding.blocked:
        return False
    if role_binding.status == STATUS_FALLBACK:
        return role_binding.fallback == "model_root"
    return True


def _control_group_parent(spec: MmdControlRigSpec, root: str) -> Optional[str]:
    """Keep a model-root fallback master outside the DAG it will drive."""
    master = spec.roles_by_name.get("master")
    if (
        master is not None
        and master.status == STATUS_FALLBACK
        and master.fallback == "model_root"
    ):
        return None
    return root


def _fallback_alias_target(role_binding: MmdControlRigRoleBinding) -> Optional[str]:
    """Return the concrete role whose control a semantic fallback aliases."""
    if role_binding.status != STATUS_FALLBACK:
        return None
    fallback = role_binding.fallback
    if not fallback or fallback == "model_root":
        return None
    return str(fallback)


def _binding_metadata(
    role_binding: MmdControlRigRoleBinding,
    *,
    cmds_module=None,
) -> Dict[str, Any]:
    """Serialize one role binding for persisted control-rig metadata."""
    binding = role_binding.binding
    if binding is None:
        raise MmdControlRigBuildError(
            f"control-rig role has no binding: {role_binding.role}"
        )
    metadata = {
        "joint": binding.joint,
        "inputKind": binding.input_kind,
        "authoredPlugs": list(binding.authored_plugs),
        "ikSolvers": list(binding.ik_solvers),
        "fallback": role_binding.fallback,
    }
    if cmds_module is not None:
        metadata.update(
            {
                "jointUuid": _node_uuid(cmds_module, binding.joint),
                "ikSolverUuids": [
                    _node_uuid(cmds_module, solver) for solver in binding.ik_solvers
                ],
                "authoredPlugRefs": _authored_plug_refs(
                    cmds_module, binding.authored_plugs
                ),
            }
        )
    return metadata


def _authored_plug_refs(cmds, plugs) -> List[Dict[str, str]]:
    refs = []
    for plug in plugs:
        node, attribute = str(plug).split(".", 1)
        refs.append(
            {
                "nodeUuid": _node_uuid(cmds, node),
                "attribute": attribute,
            }
        )
    return refs


def resolve_mmd_control_rig_binding_joint(cmds, binding: Mapping[str, Any]) -> str:
    """Resolve a binding joint from its authoritative UUID."""
    uuid = binding.get("jointUuid")
    if not uuid:
        raise MmdControlRigBuildError("binding joint UUID is missing")
    return _resolve_uuid_node(cmds, str(uuid), "binding joint")


def resolve_mmd_control_rig_binding_ik_solvers(
    cmds,
    binding: Mapping[str, Any],
) -> Tuple[str, ...]:
    """Resolve all solver nodes from their authoritative UUIDs."""
    uuids = binding.get("ikSolverUuids")
    if uuids is None:
        raise MmdControlRigBuildError("IK solver UUID metadata is missing")
    return tuple(
        _resolve_uuid_node(cmds, str(uuid), "IK solver") for uuid in uuids
    )


def resolve_mmd_control_rig_binding_authored_plugs(
    cmds,
    binding: Mapping[str, Any],
) -> Tuple[str, ...]:
    """Resolve authored input plugs, preferring UUID-backed node references."""
    refs = binding.get("authoredPlugRefs")
    if refs is None:
        raise MmdControlRigBuildError("authored plug UUID metadata is missing")
    plugs = []
    for ref in refs:
        if not isinstance(ref, Mapping):
            raise MmdControlRigBuildError("invalid authored plug reference")
        uuid = ref.get("nodeUuid")
        attribute = ref.get("attribute")
        if not uuid or not attribute:
            raise MmdControlRigBuildError("incomplete authored plug reference")
        node = _resolve_uuid_node(cmds, str(uuid), "authored plug node")
        plugs.append(f"{node}.{attribute}")
    return tuple(plugs)


def _resolve_uuid_node(cmds, uuid: str, description: str) -> str:
    nodes = cmds.ls(uuid, long=True) or []
    if len(nodes) != 1:
        raise MmdControlRigBuildError(f"{description} UUID is missing: {uuid}")
    return str(nodes[0])


def _apply_fallback_role_aliases(
    role_bindings,
    controls: Dict[str, str],
    zero_groups: Dict[str, str],
    bindings: Dict[str, Dict[str, Any]],
    *,
    cmds_module=None,
) -> None:
    """Alias semantic fallback roles to existing controls without new nodes."""
    pending = {
        role_binding.role: role_binding
        for role_binding in role_bindings
        if _fallback_alias_target(role_binding) is not None
    }
    while pending:
        applied = False
        for role in sorted(tuple(pending)):
            role_binding = pending[role]
            target = _fallback_alias_target(role_binding)
            if target not in controls:
                continue
            controls[role] = controls[target]
            zero_groups[role] = zero_groups[target]
            bindings[role] = _binding_metadata(role_binding, cmds_module=cmds_module)
            del pending[role]
            applied = True
        if applied:
            continue
        unresolved = ", ".join(
            f"{role}->{_fallback_alias_target(pending[role])}"
            for role in sorted(pending)
        )
        raise MmdControlRigBuildError(
            f"control-rig fallback alias target is unavailable: {unresolved}"
        )


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


def _current_time(cmds) -> float:
    """Return the Maya time used as the deterministic control display reference."""
    try:
        return float(cmds.currentTime(query=True))
    except (TypeError, ValueError, RuntimeError):
        return 0.0


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


def _namespace_prefix(root: str) -> str:
    """Return an absolute Maya namespace prefix for generated node names."""
    leaf = root.rsplit("|", 1)[-1]
    if ":" not in leaf:
        return ":"
    return f":{leaf.rsplit(':', 1)[0]}:"


@contextmanager
def _undo_chunk(cmds, label: str):
    """Group one public builder mutation into a single Maya Undo step."""
    cmds.undoInfo(openChunk=True, chunkName=label)
    try:
        yield
    finally:
        cmds.undoInfo(closeChunk=True)
