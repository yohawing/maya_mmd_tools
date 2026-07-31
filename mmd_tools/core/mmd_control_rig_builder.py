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
    ATTR_MMD_FIXED_AXIS,
    ATTR_MMD_AXIS_DIRECTION,
    ATTR_MMD_LOCAL_X_AXIS,
    ATTR_MMD_LOCAL_Z_AXIS,
)
from mmd_tools.core.humanik_utils import maya_cmds
from mmd_tools.core.mmd_control_rig_analyzer import (
    INPUT_IK_CONTROLLER,
    MmdControlRigRoleBinding,
    MmdControlRigSpec,
    STATUS_FALLBACK,
    analyze_mmd_control_rig,
)
from mmd_tools.core.mmd_control_rig_channels import (
    ALL_CHANNELS,
    apply_mmd_control_rig_channel_policy,
    derive_mmd_control_rig_channel_policy,
    union_mmd_control_rig_channel_policies,
)
from mmd_tools.core.mmd_control_rig_basis import (
    MmdControlRigBasisError,
    basis_from_shape_rotation,
    matrix_from_quaternion,
    validate_basis_record,
)
from mmd_tools.core.pmx_local_axis import maya_basis_from_pmx_local_axes
from mmd_tools.core.pmx_data.bone import PmxBoneFlag


CONTROL_RIG_METADATA_SCHEMA = "mmd_tools.mmd_control_rig"
CONTROL_RIG_METADATA_VERSION = 3
CONTROL_RIG_ATTACHED = "ATTACHED"
CONTROL_RIG_EDIT = "EDIT"
CONTROL_RIG_BAKED = "BAKED"
CONTROL_RIG_STATES = frozenset({CONTROL_RIG_ATTACHED, CONTROL_RIG_EDIT, CONTROL_RIG_BAKED})

# ``state`` is retained as the legacy lifecycle field.  ``owner`` is the
# authoritative single-writer field for motion transitions.  Keep both the
# descriptive and owner-prefixed names available to callers because the
# metadata is consumed by older development-mode surfaces as well.
CONTROL_RIG_MMD_OWNED = "MMD_OWNED"
CONTROL_RIG_CONTROL_OWNED = "CONTROL_OWNED"
CONTROL_RIG_CONVERTING = "CONVERTING"
CONTROL_RIG_OWNERS = frozenset(
    {CONTROL_RIG_MMD_OWNED, CONTROL_RIG_CONTROL_OWNED, CONTROL_RIG_CONVERTING}
)
CONTROL_RIG_OWNER_MMD = CONTROL_RIG_MMD_OWNED
CONTROL_RIG_OWNER_CONTROL = CONTROL_RIG_CONTROL_OWNED
CONTROL_RIG_OWNER_CONVERTING = CONTROL_RIG_CONVERTING


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
    aim_spaces: Mapping[str, str] = None
    state: str = CONTROL_RIG_ATTACHED
    owner: str = CONTROL_RIG_MMD_OWNED
    created: bool = True


@dataclass(frozen=True)
class _ControlChannelState:
    """Exact Maya child-channel flags captured for migration rollback."""

    locked: bool
    keyable: bool
    channel_box: bool


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
_FINGER_ROOT_ROLES = frozenset(chain[0] for chain in _FINGER_ROLE_CHAINS)
_TWIST_RING_ROLES = frozenset(
    {
        "left_arm_twist",
        "right_arm_twist",
        "left_wrist_twist",
        "right_wrist_twist",
    }
)
_TWIST_CURVE_SCALE = 0.5
_NECK_CURVE_SCALE = 3.0
_TWIST_CHILD_ROLES = {
    "left_arm_twist": "left_elbow",
    "left_wrist_twist": "left_wrist",
    "right_arm_twist": "right_elbow",
    "right_wrist_twist": "right_wrist",
}
_ARM_ORIENTATION_ROLES = frozenset(
    f"{side}_{role}"
    for side in ("left", "right")
    for role in ("shoulder", "arm", "arm_twist", "elbow", "wrist_twist", "wrist")
)
_IK_DISPLAY_ONLY_ROLES = frozenset(
    {
        "left_foot_ik_parent",
        "right_foot_ik_parent",
        "left_foot_ik",
        "right_foot_ik",
        "left_toe_ik",
        "right_toe_ik",
    }
)
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
    "left_arm_twist": 6,
    "right_arm_twist": 13,
    "left_wrist_twist": 6,
    "right_wrist_twist": 13,
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
    # Keep the optional primary twist joints in the visible FK chain.  The
    # Kokomi hierarchy (arm -> arm twist -> elbow -> wrist twist -> wrist)
    # needs the intermediate rings to carry authored roll into downstream
    # controls; ``_available_parent_role`` transparently skips absent rings.
    "left_elbow": "left_arm_twist",
    "left_wrist_twist": "left_elbow",
    "left_wrist": "left_wrist_twist",
    "right_shoulder": "upper_body2",
    "right_arm": "right_shoulder",
    "right_elbow": "right_arm_twist",
    "right_wrist_twist": "right_elbow",
    "right_wrist": "right_wrist_twist",
    "left_arm_twist": "left_arm",
    "right_arm_twist": "right_arm",
    "left_leg": "groove",
    "left_knee": "left_leg",
    "right_leg": "groove",
    "right_knee": "right_leg",
    **_FINGER_ROLE_PARENTS,
}

_ROLE_TEMPLATE_ALIASES = {
    **{role: "finger" for role in _FINGER_ROLES},
    "left_arm": "circle",
    "right_arm": "circle",
    "right_elbow": "left_elbow",
    "right_wrist": "left_wrist",
    "left_arm_twist": "circle",
    "right_arm_twist": "circle",
    "left_wrist_twist": "circle",
    "right_wrist_twist": "circle",
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
        "left_arm_twist",
        "right_arm_twist",
        "left_wrist_twist",
        "right_wrist_twist",
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
        result = _result_from_metadata(cmds, root, existing, created=False)
        _migrate_existing_control_channel_policy(cmds, existing)
        return result

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
            aim_spaces: Dict[str, str] = {}
            role_joints: Dict[str, str] = {
                role_binding.role: str(role_binding.binding.joint)
                for role_binding in rig_spec.roles
                if _should_build_role_control(role_binding)
                and role_binding.binding is not None
            }
            bindings: Dict[str, Dict[str, Any]] = {}
            authoring_bases: Dict[str, Dict[str, Any]] = {}
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
                if (
                    binding.bone_index is None
                    or binding.input_kind == INPUT_IK_CONTROLLER
                    or role in _IK_DISPLAY_ONLY_ROLES
                ):
                    # The model-root fallback is a scene transform rather
                    # than an imported PMX bone and has no bind-pose record.
                    # Preserve existing placement; solver-owned IK handles
                    # likewise follow their current goal display and are not
                    # authoring-basis rotations.
                    matrix = cmds.xform(
                        binding.joint,
                        query=True,
                        worldSpace=True,
                        matrix=True,
                    )
                else:
                    matrix = _saved_bind_world_matrix(cmds, binding.joint)
                cmds.xform(zero, worldSpace=True, matrix=matrix)
                basis_matrix = matrix
                if (
                    role in _ARM_ORIENTATION_ROLES
                    and binding.bone_index is not None
                    and binding.input_kind == INPUT_IK_CONTROLLER
                ):
                    # Solver goals may display at the live pose, but FK-style
                    # arm authoring axes must remain bind-pose deterministic.
                    basis_matrix = _saved_bind_world_matrix(cmds, binding.joint)
                shape_rotation = _control_shape_rotation(
                    cmds,
                    root,
                    role,
                    binding,
                    indexed_joints,
                    bind_world_matrix=basis_matrix,
                    role_joints=role_joints,
                )
                authoring_rotation, display_rotation = _control_basis_rotations(
                    binding,
                    shape_rotation,
                )
                try:
                    basis = basis_from_shape_rotation(authoring_rotation)
                    authoring_bases[role] = dict(basis.to_dict())
                except MmdControlRigBasisError as exc:
                    raise MmdControlRigBuildError(
                        f"invalid control-rig basis for role {role}"
                    ) from exc
                # The static authoring basis is a real transform now.  Keep
                # the CV templates in their canonical +Z orientation so the
                # visible shape and the motion converter share one source of
                # truth instead of applying the basis twice.
                aim_space = cmds.createNode(
                    "transform",
                    name=f"{namespace}{role}_AIM_SPACE",
                    parent=zero,
                )
                cmds.xform(
                    aim_space,
                    objectSpace=True,
                    matrix=matrix_from_quaternion(basis.quaternion),
                )
                created_roots.append(aim_space)
                aim_spaces[role] = str(aim_space)
                control = _create_control_curve(
                    cmds,
                    f"{namespace}{role}_CTRL",
                    role,
                    _role_controller_scale(
                        cmds,
                        root,
                        role,
                        binding,
                        indexed_joints,
                        scale,
                    ),
                    shape_rotation=display_rotation,
                )
                created_roots.append(control)
                parented = cmds.parent(control, aim_space)
                if parented:
                    control = str(parented[0])
                control = str(cmds.rename(control, f"{namespace}{role}_CTRL"))
                _rename_control_shapes(cmds, control, namespace, role)
                cmds.setAttr(f"{control}.translate", 0.0, 0.0, 0.0, type="double3")
                cmds.setAttr(f"{control}.rotate", 0.0, 0.0, 0.0, type="double3")
                apply_mmd_control_rig_channel_policy(
                    cmds,
                    control,
                    derive_mmd_control_rig_channel_policy(
                        _channel_policy_role(role), binding
                    ),
                )
                _color_control(cmds, control, _ROLE_COLORS[role])
                cmds.sets(control, add=selection_set)
                controls[role] = str(control)
                zero_groups[role] = str(zero)
                role_joints[role] = str(binding.joint)
                bindings[role] = _binding_metadata(role_binding, cmds_module=cmds)

            # Parent only concrete nodes.  Semantic fallback aliases are
            # added afterwards and must never be interpreted as new DAG
            # edges (e.g. groove_ZERO aliasing center_ZERO would otherwise
            # attempt to parent center_ZERO below its own child).
            helper_nodes = _parent_zero_groups(
                cmds,
                zero_groups,
                controls,
                role_joints,
            )
            created_roots.extend(helper_nodes)
            _apply_fallback_role_aliases(
                rig_spec.roles,
                controls,
                zero_groups,
                bindings,
                authoring_bases,
                aim_spaces,
                cmds_module=cmds,
            )

            nodes = tuple(
                sorted(
                    set(
                        _owned_nodes(cmds, control_group, selection_set)
                        + tuple(_canonical_node(cmds, node) for node in helper_nodes)
                    )
                )
            )
            metadata = {
                "schema": CONTROL_RIG_METADATA_SCHEMA,
                "version": CONTROL_RIG_METADATA_VERSION,
                "state": CONTROL_RIG_ATTACHED,
                "owner": CONTROL_RIG_MMD_OWNED,
                "displayReferenceTime": display_reference_time,
                "modelRootUuid": _node_uuid(cmds, root),
                "controlGroupUuid": _node_uuid(cmds, control_group),
                "selectionSetUuid": _node_uuid(cmds, selection_set),
                "helperNodes": [_node_uuid(cmds, node) for node in helper_nodes],
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
                "aimSpaces": {
                    role: _node_uuid(cmds, node)
                    for role, node in sorted(aim_spaces.items())
                },
                "bindings": bindings,
                "authoringBases": authoring_bases,
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
    if metadata.get("owner") != CONTROL_RIG_MMD_OWNED:
        raise MmdControlRigBuildError("cannot remove a control rig during ownership conversion")
    if _node_uuid(cmds, root) != metadata.get("modelRootUuid"):
        raise MmdControlRigBuildError("control-rig metadata model UUID mismatch")
    resolved = _resolve_owned_nodes(cmds, metadata)
    _validate_control_rig_topology(cmds, metadata, resolved)
    control_group = resolved[metadata["controlGroupUuid"]]
    selection_set = resolved[metadata["selectionSetUuid"]]
    helper_nodes = [
        resolved[uuid] for uuid in metadata.get("helperNodes", [])
    ]
    ik_visibility_inverters = [
        resolved[str(row["uuid"])]
        for row in metadata.get("ikVisibilityInverters", []) or []
        if isinstance(row, Mapping) and row.get("uuid")
    ]
    rotation_time_curves = []
    for row in metadata.get("rotationTimeCurves", []) or []:
        from mmd_tools.converters.vmd_rotation_time_curve import (
            resolve_vmd_rotation_time_curve_record,
        )

        try:
            node, _control, _rotation_curves = resolve_vmd_rotation_time_curve_record(
                row,
                cmds_module=cmds,
            )
        except RuntimeError as exc:
            raise MmdControlRigBuildError(str(exc)) from exc
        rotation_time_curves.append(node)
    with _undo_chunk(cmds, "Remove MMD Control Rig"):
        for node in helper_nodes:
            if cmds.objExists(node):
                cmds.delete(node)
        for node in ik_visibility_inverters:
            if cmds.objExists(node):
                cmds.delete(node)
        if cmds.objExists(selection_set):
            cmds.delete(selection_set)
        for node in rotation_time_curves:
            if cmds.objExists(node):
                from mmd_tools.converters.vmd_rotation_time_curve import (
                    detach_and_delete_vmd_rotation_time_curve,
                )

                detach_and_delete_vmd_rotation_time_curve(cmds, node)
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


def inspect_mmd_control_rig(
    model_root: str,
    *,
    cmds_module=None,
) -> Optional[MmdControlRigBuildResult]:
    """Resolve an existing UUID-owned Control Rig without mutating the scene.

    A model with no control-rig metadata returns ``None``.  Any malformed,
    stale, ambiguous, or model-root-mismatched metadata raises the same
    :class:`MmdControlRigBuildError` used by build/remove recovery paths.  The
    result is produced through ``_result_from_metadata`` so inspection and
    idempotent build share one ownership invariant implementation.
    """

    cmds = cmds_module or maya_cmds()
    root = _canonical_node(cmds, model_root)
    metadata = _read_metadata(cmds, root)
    if metadata is None:
        return None
    return _result_from_metadata(
        cmds,
        root,
        metadata,
        created=False,
        validate_topology=True,
    )


# Resolver spelling for callers that treat inspection as an ownership lookup.
resolve_mmd_control_rig = inspect_mmd_control_rig


def _result_from_metadata(
    cmds,
    root: str,
    metadata: Mapping[str, Any],
    *,
    created: bool,
    validate_topology: bool = False,
) -> MmdControlRigBuildResult:
    resolved = _resolve_owned_nodes(cmds, metadata)
    if _node_uuid(cmds, root) != metadata.get("modelRootUuid"):
        raise MmdControlRigBuildError("control-rig metadata model UUID mismatch")
    if validate_topology:
        _validate_control_rig_topology(cmds, metadata, resolved)
    controls = {
        role: resolved[uuid]
        for role, uuid in sorted(metadata.get("controls", {}).items())
    }
    zero_groups = {
        role: resolved[uuid]
        for role, uuid in sorted(metadata.get("zeroGroups", {}).items())
    }
    aim_spaces = {
        role: resolved[uuid]
        for role, uuid in sorted(metadata.get("aimSpaces", {}).items())
        if uuid in resolved
    }
    return MmdControlRigBuildResult(
        model_root=root,
        control_group=resolved[metadata["controlGroupUuid"]],
        selection_set=resolved[metadata["selectionSetUuid"]],
        controls=controls,
        zero_groups=zero_groups,
        aim_spaces=aim_spaces,
        state=str(metadata["state"]),
        owner=str(metadata.get("owner", _owner_for_state(metadata["state"]))),
        created=created,
    )


def _migrate_existing_control_channel_policy(cmds, metadata: Mapping[str, Any]) -> None:
    """Repair channel flags for an existing MMD-owned rig transactionally.

    The runtime channel state is intentionally not persisted in metadata.  A
    build/reuse action is the explicit migration request for v3 rigs, while
    EDIT/CONTROL_OWNED and CONVERTING states remain untouched so a live
    authoring transaction cannot be interrupted by a convenience call.
    """

    if metadata.get("state") not in {CONTROL_RIG_ATTACHED, CONTROL_RIG_BAKED}:
        return
    if metadata.get("owner") != CONTROL_RIG_MMD_OWNED:
        return

    controls = metadata.get("controls") or {}
    bindings = metadata.get("bindings")
    if not isinstance(controls, Mapping):
        raise MmdControlRigBuildError("control-rig metadata controls must be an object")
    if bindings is None:
        bindings = {}
    if not isinstance(bindings, Mapping):
        raise MmdControlRigBuildError("control-rig metadata bindings must be an object")

    # Group semantic roles by recorded UUID so fallback aliases sharing one
    # physical node receive one deterministic policy and one transaction.
    grouped = {}
    for role, uuid in sorted(controls.items(), key=lambda item: str(item[0])):
        if not isinstance(uuid, str) or not uuid:
            raise MmdControlRigBuildError(
                f"control-rig metadata control UUID is invalid: {role}"
            )
        control = _resolve_uuid_node(cmds, uuid, f"control for role {role}")
        binding = bindings.get(role)
        policy = derive_mmd_control_rig_channel_policy(
            _channel_policy_role(str(role)), binding
        )
        grouped.setdefault(uuid, {"control": control, "entries": []})["entries"].append(
            (str(role), binding, policy)
        )

    migrations = []
    for row in grouped.values():
        entries = tuple(row["entries"])
        policy = union_mmd_control_rig_channel_policies(
            tuple(item[2] for item in entries)
        )
        migrations.append((row["control"], policy))
    if not migrations:
        return

    snapshots = _capture_control_channel_states(cmds, migrations)
    if _control_channel_policies_are_applied(snapshots, migrations):
        return

    with _undo_chunk(cmds, "Migrate MMD Control Rig Channel Policy"):
        try:
            for control, policy in migrations:
                apply_mmd_control_rig_channel_policy(cmds, control, policy)
        except Exception:
            try:
                _restore_control_channel_states(cmds, snapshots)
            except Exception as restore_error:
                raise MmdControlRigBuildError(
                    "MMD control-rig channel migration rollback failed"
                ) from restore_error
            raise


def _control_channel_policies_are_applied(snapshots, migrations) -> bool:
    """Return whether every physical control already matches its policy."""
    for control, policy in migrations:
        for channel in ALL_CHANNELS:
            state = snapshots[f"{control}.{channel}"]
            if channel in policy.keyable_channels:
                expected = _ControlChannelState(False, True, False)
            elif channel in policy.channel_box_channels:
                expected = _ControlChannelState(False, False, True)
            elif channel in policy.passthrough_channels:
                expected = _ControlChannelState(True, False, False)
            else:
                expected = _ControlChannelState(True, False, False)
            if state != expected:
                return False
    return True


def _capture_control_channel_states(cmds, migrations) -> Mapping[str, _ControlChannelState]:
    snapshots = {}
    for control, _policy in migrations:
        for channel in ALL_CHANNELS:
            plug = f"{control}.{channel}"
            snapshots[plug] = _ControlChannelState(
                locked=bool(cmds.getAttr(plug, lock=True)),
                keyable=bool(cmds.getAttr(plug, keyable=True)),
                channel_box=bool(cmds.getAttr(plug, channelBox=True)),
            )
    return snapshots


def _restore_control_channel_states(
    cmds,
    snapshots: Mapping[str, _ControlChannelState],
) -> None:
    # Unlock all channels before restoring keyability/Channel Box flags; Maya
    # rejects those writes while an attribute is locked.
    for plug in snapshots:
        cmds.setAttr(plug, lock=False)
    for plug, state in snapshots.items():
        cmds.setAttr(plug, keyable=state.keyable)
        cmds.setAttr(plug, channelBox=state.channel_box)
    for plug, state in snapshots.items():
        cmds.setAttr(plug, lock=state.locked)


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
    state = metadata.get("state")
    if state not in CONTROL_RIG_STATES:
        raise MmdControlRigBuildError("unsupported control-rig metadata state")
    owner = metadata.get("owner")
    if owner is None:
        # v3 metadata written before explicit ownership used ATTACHED/BAKED
        # for MMD-owned motion and EDIT for control-owned motion.
        owner = _owner_for_state(state)
        metadata["owner"] = owner
    elif owner not in CONTROL_RIG_OWNERS:
        raise MmdControlRigBuildError("unsupported control-rig metadata owner")
    elif owner != CONTROL_RIG_CONVERTING and owner != _owner_for_state(state):
        raise MmdControlRigBuildError(
            "control-rig metadata state and owner disagree"
        )
    for key in ("modelRootUuid", "controlGroupUuid", "selectionSetUuid"):
        if not isinstance(metadata.get(key), str) or not metadata[key]:
            raise MmdControlRigBuildError(f"control-rig metadata missing {key}")
    if not isinstance(metadata.get("nodes"), list):
        raise MmdControlRigBuildError("control-rig metadata nodes must be an array")
    if not isinstance(metadata.get("helperNodes", []), list):
        raise MmdControlRigBuildError("control-rig metadata helperNodes must be an array")
    if "authoringBases" in metadata:
        _validate_basis_metadata(metadata["authoringBases"])
    if "aimSpaces" in metadata and not isinstance(metadata["aimSpaces"], Mapping):
        raise MmdControlRigBuildError("control-rig AIM_SPACE metadata must be an object")
    try:
        display_reference_time = float(metadata["displayReferenceTime"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MmdControlRigBuildError("control-rig display reference is missing") from exc
    if not math.isfinite(display_reference_time):
        raise MmdControlRigBuildError("control-rig display reference must be finite")
    return metadata


def _validate_basis_metadata(value: Any) -> None:
    """Validate optional per-role basis records without rewriting metadata."""

    if not isinstance(value, Mapping):
        raise MmdControlRigBuildError("control-rig basis metadata must be an object")
    for role, record in value.items():
        try:
            validate_basis_record(record)
        except MmdControlRigBasisError as exc:
            raise MmdControlRigBuildError(
                f"invalid control-rig basis metadata for role {role}"
            ) from exc


def _owner_for_state(state: str) -> str:
    """Derive explicit motion ownership from a legacy lifecycle state."""
    return (
        CONTROL_RIG_CONTROL_OWNED
        if state == CONTROL_RIG_EDIT
        else CONTROL_RIG_MMD_OWNED
    )


def _resolve_owned_nodes(cmds, metadata: Mapping[str, Any]) -> Dict[str, str]:
    resolved = {}
    for row in metadata.get("nodes", []):
        if not isinstance(row, dict) or not isinstance(row.get("uuid"), str):
            raise MmdControlRigBuildError("invalid owned-node metadata row")
        uuid = row["uuid"]
        if uuid in resolved:
            raise MmdControlRigBuildError(f"duplicate owned control-rig UUID: {uuid}")
        nodes = cmds.ls(uuid, long=True) or []
        if len(nodes) != 1:
            raise MmdControlRigBuildError(f"owned control-rig node is missing: {uuid}")
        resolved[uuid] = str(nodes[0])
    for key in ("controls", "zeroGroups"):
        if not isinstance(metadata.get(key), Mapping):
            raise MmdControlRigBuildError(f"control-rig metadata {key} must be an object")
    for uuid in (
        metadata["controlGroupUuid"],
        metadata["selectionSetUuid"],
        *metadata.get("helperNodes", []),
        *metadata.get("controls", {}).values(),
        *metadata.get("zeroGroups", {}).values(),
        *metadata.get("aimSpaces", {}).values(),
    ):
        if not isinstance(uuid, str) or not uuid:
            raise MmdControlRigBuildError("invalid referenced control-rig UUID")
        if uuid not in resolved:
            raise MmdControlRigBuildError(f"unrecorded control-rig UUID: {uuid}")
    return resolved


def _validate_control_rig_topology(
    cmds,
    metadata: Mapping[str, Any],
    resolved: Mapping[str, str],
) -> None:
    """Fail closed when the recorded control DAG no longer matches the scene."""

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
    # ``nodes`` also owns DG helpers used by authoring-basis converters and
    # animation constraints.  They are UUID-scoped lifecycle nodes but are
    # not DAG descendants of Controls, so comparing them to listRelatives()
    # would falsely report every valid EDIT rig as topology-corrupted.
    recorded_dag = {
        node
        for node in set(resolved.values()) - {selection_set}
        if _is_dag_node(cmds, node)
    }
    if actual != recorded_dag:
        changed = ", ".join(sorted(actual.symmetric_difference(recorded_dag)))
        raise MmdControlRigBuildError(
            f"control group ownership topology changed: {changed}"
        )


def _is_dag_node(cmds, node: str) -> bool:
    """Return whether a resolved UUID belongs to the Control DAG."""

    try:
        return bool(cmds.ls(node, dag=True, long=True) or [])
    except (AttributeError, TypeError):
        # Lightweight inspection doubles predate the dag query.  Keep their
        # historical behavior; real Maya command modules expose ``ls(dag=)``.
        return True
    except Exception:
        return False


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


def _control_shape_rotation(
    cmds,
    root,
    role,
    binding,
    indexed_joints,
    *,
    bind_world_matrix=None,
    role_joints=None,
):
    """Infer a stable joint-local authoring basis for one control.

    ``ZERO`` already carries the bound joint world matrix.  PMX tail metadata is
    stored in model/world space, so a joint with Local Axis (or one below such a
    joint) must first convert that direction into ``ZERO`` local space.  Applying
    the world direction directly would double the joint orientation; returning
    identity would leave the canonical XY control plane facing joint-local +Z
    instead of following the bone's local +X direction.
    """
    if role not in _AUTO_ORIENT_SHAPE_ROLES:
        return None
    direction = (
        _twist_child_direction(
            cmds,
            binding,
            (role_joints or {}).get(_TWIST_CHILD_ROLES.get(role)),
        )
        if role in _TWIST_RING_ROLES
        else _pmx_tail_direction(cmds, binding, indexed_joints)
    )
    if direction is None and role in _TWIST_RING_ROLES:
        direction = _vector_attribute(cmds, binding.joint, ATTR_MMD_FIXED_AXIS)
        if direction is None:
            direction = _vector_attribute(
                cmds,
                binding.joint,
                ATTR_MMD_AXIS_DIRECTION,
            )
    if direction is None:
        return None
    maya_direction = (direction[0], direction[1], -direction[2])

    if role in _ARM_ORIENTATION_ROLES:
        world_axes = _arm_control_world_axes(role, maya_direction)
        if world_axes is None:
            return None
        bind_axes = _rotation_axes_from_matrix(bind_world_matrix)
        if bind_axes is None:
            has_local_axis_basis, bind_axes = _joint_chain_local_axis_basis(
                cmds,
                binding.joint,
                root,
                self_has_local_axis=bool(
                    binding.pmx_flags & int(PmxBoneFlag.LOCAL_AXIS)
                ),
            )
            if has_local_axis_basis and bind_axes is None:
                return None
        if bind_axes is None:
            bind_axes = (
                (1.0, 0.0, 0.0),
                (0.0, 1.0, 0.0),
                (0.0, 0.0, 1.0),
            )
        local_axes = tuple(
            _world_direction_to_local_axes(axis, bind_axes)
            for axis in world_axes
        )
        return _rotation_from_basis_rows(local_axes)

    has_local_axis_basis, bind_axes = _joint_chain_local_axis_basis(
        cmds,
        binding.joint,
        root,
        self_has_local_axis=bool(
            binding.pmx_flags & int(PmxBoneFlag.LOCAL_AXIS)
        ),
    )
    if has_local_axis_basis:
        if bind_axes is None:
            return None
        local_direction = _world_direction_to_local_axes(
            maya_direction,
            bind_axes,
        )
        return _shortest_arc_from_positive_z(local_direction)
    return _shortest_arc_from_positive_z(maya_direction)


def _twist_child_direction(cmds, binding, child_joint=None):
    """Return a primary twist joint's actual child direction in PMX space."""

    source = _vector_attribute(
        cmds,
        binding.joint,
        ATTR_MMD_PMX_REST_POSITION,
    )
    if source is None:
        return None
    children = [str(child_joint)] if child_joint else (
        cmds.listRelatives(
            binding.joint,
            children=True,
            fullPath=True,
            type="joint",
        ) or []
    )
    candidates = []
    for child in children:
        target = _vector_attribute(
            cmds,
            str(child),
            ATTR_MMD_PMX_REST_POSITION,
        )
        if target is None:
            continue
        direction = tuple(target[index] - source[index] for index in range(3))
        length = math.sqrt(sum(component * component for component in direction))
        if math.isfinite(length) and length > 1.0e-8:
            candidates.append((length, direction))
    if not candidates:
        return None
    # Prefer the structural child over nearby distribution helpers.
    return max(candidates, key=lambda item: item[0])[1]


def _arm_control_world_axes(role, direction):
    """Return mirrored ergonomic X/Y/Z axes with Z aimed at the child."""

    z_axis = _normalized_vector(direction)
    if z_axis is None:
        return None
    # Match yw_test_model: Z follows the bone and X uses mirrored depth.
    depth_sign = -1.0 if str(role).startswith("left_") else 1.0
    x_axis = None
    for reference in ((0.0, 0.0, depth_sign), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0)):
        projection = sum(reference[index] * z_axis[index] for index in range(3))
        x_axis = _normalized_vector(
            tuple(
                reference[index] - projection * z_axis[index]
                for index in range(3)
            )
        )
        if x_axis is not None:
            break
    if x_axis is None:
        return None
    y_axis = _normalized_vector(_cross_product(z_axis, x_axis))
    if y_axis is None:
        return None
    z_axis = _normalized_vector(_cross_product(x_axis, y_axis))
    if z_axis is None:
        return None
    return x_axis, y_axis, z_axis


def _rotation_axes_from_matrix(matrix):
    """Extract finite right-handed rotation rows from a Maya matrix payload."""

    if matrix is None:
        return None
    try:
        values = tuple(float(value) for value in matrix)
    except (TypeError, ValueError):
        return None
    if len(values) != 16 or not all(math.isfinite(value) for value in values):
        return None
    x_axis = _normalized_vector(values[0:3])
    if x_axis is None:
        return None
    raw_y = values[4:7]
    projection = sum(raw_y[index] * x_axis[index] for index in range(3))
    y_axis = _normalized_vector(
        tuple(raw_y[index] - projection * x_axis[index] for index in range(3))
    )
    if y_axis is None:
        return None
    z_axis = _normalized_vector(_cross_product(x_axis, y_axis))
    if z_axis is None:
        return None
    raw_z = values[8:11]
    if sum(z_axis[index] * raw_z[index] for index in range(3)) < 0.0:
        y_axis = tuple(-value for value in y_axis)
        z_axis = tuple(-value for value in z_axis)
    return x_axis, y_axis, z_axis


def _rotation_from_basis_rows(axes):
    """Convert orthonormal Maya rotation rows to an axis/cos/sin tuple."""

    rows = tuple(tuple(float(value) for value in row) for row in axes)
    if len(rows) != 3 or any(len(row) != 3 for row in rows):
        return None
    cosine = max(
        -1.0,
        min(1.0, 0.5 * (rows[0][0] + rows[1][1] + rows[2][2] - 1.0)),
    )
    vector = (
        rows[1][2] - rows[2][1],
        rows[2][0] - rows[0][2],
        rows[0][1] - rows[1][0],
    )
    vector_length = math.sqrt(sum(value * value for value in vector))
    sine = min(1.0, 0.5 * vector_length)
    if sine > 1.0e-8:
        return (
            tuple(value / vector_length for value in vector),
            cosine,
            sine,
        )
    if cosine > 0.0:
        return ((0.0, 1.0, 0.0), 1.0, 0.0)

    # Stable 180-degree fallback without depending on Euler order.
    components = [
        math.sqrt(max(0.0, 0.5 * (rows[index][index] + 1.0)))
        for index in range(3)
    ]
    largest = max(range(3), key=lambda index: components[index])
    if components[largest] <= 1.0e-8:
        return None
    for index in range(3):
        if index == largest:
            continue
        components[index] = math.copysign(
            components[index],
            rows[largest][index] + rows[index][largest],
        )
    axis = _normalized_vector(components)
    return (axis, -1.0, 0.0) if axis is not None else None


def _normalized_vector(vector):
    """Return a finite normalized three-vector, or ``None`` if degenerate."""

    try:
        values = tuple(float(value) for value in vector)
    except (TypeError, ValueError):
        return None
    if len(values) != 3 or not all(math.isfinite(value) for value in values):
        return None
    length = math.sqrt(sum(value * value for value in values))
    if length <= 1.0e-8:
        return None
    return tuple(value / length for value in values)


def _cross_product(left, right):
    """Return the three-dimensional cross product of two vectors."""

    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _control_basis_rotations(binding, shape_rotation):
    """Use one authoring basis for direct, accumulator, and IK-link controls.

    EDIT installs the reciprocal basis converter for complete IK-link XYZ
    inputs, so leg and knee manipulators can use the same child-facing axes as
    arm controls without changing the raw ``mmdCcdIk.inputRotate`` contract.
    """

    return shape_rotation, None


def _joint_chain_local_axis_basis(
    cmds,
    joint: str,
    root: str,
    *,
    self_has_local_axis: bool = False,
):
    """Return the nearest PMX Local Axis bind basis affecting one joint.

    Bone import authors Local Axis as ``jointOrient`` only on the declaring
    joint.  Descendants without their own Local Axis inherit that same bind
    rotation, so the nearest declaration is the static world basis needed to
    convert PMX tail directions into controller-local space.
    """
    current = str(joint)
    is_self = True
    while current and current != root:
        has_local_axis = bool(self_has_local_axis and is_self)
        if cmds.attributeQuery(ATTR_MMD_BONE_FLAGS, node=current, exists=True):
            flags = int(cmds.getAttr(f"{current}.{ATTR_MMD_BONE_FLAGS}") or 0)
            has_local_axis = has_local_axis or bool(
                flags & int(PmxBoneFlag.LOCAL_AXIS)
            )
        if has_local_axis:
            return True, _local_axis_basis(cmds, current)
        parents = cmds.listRelatives(
            current,
            parent=True,
            fullPath=True,
            type="joint",
        ) or []
        current = str(parents[0]) if parents else ""
        is_self = False
    return False, None


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


def _local_axis_basis(cmds, joint):
    """Return orthonormal Maya-world X/Y/Z rows from PMX Local Axis metadata."""
    local_x = _vector_attribute(cmds, joint, ATTR_MMD_LOCAL_X_AXIS)
    local_z = _vector_attribute(cmds, joint, ATTR_MMD_LOCAL_Z_AXIS)
    if local_x is None or local_z is None:
        return None
    try:
        return maya_basis_from_pmx_local_axes(local_x, local_z)
    except ValueError:
        return None


def _world_direction_to_local_axes(direction, axes):
    """Project one Maya-world direction onto orthonormal local basis rows."""
    world = tuple(float(component) for component in direction)
    return tuple(
        sum(world[index] * axis[index] for index in range(3))
        for axis in axes
    )


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


def _actual_joint_parent(cmds, role: str, role_joints: Mapping[str, str]) -> Optional[str]:
    """Return the concrete parent joint for a bound role, when unambiguous."""
    joint = role_joints.get(role)
    if not joint:
        return None
    parents = cmds.listRelatives(
        joint,
        parent=True,
        fullPath=True,
        type="joint",
    ) or []
    return str(parents[0]) if len(parents) == 1 else None


def _parent_zero_groups(
    cmds,
    zero_groups: Mapping[str, str],
    controls: Mapping[str, str],
    role_joints: Mapping[str, str],
) -> Tuple[str, ...]:
    """Parent zero groups while preserving omitted helper-joint motion.

    Finger roots and arm roles may have concrete parents that are not exposed
    as semantic controls. Knee controls also author raw pre-solver rotation,
    so they must follow the evaluated thigh instead of assuming the semantic
    leg control has the same world basis. Following that real parent joint is
    acyclic because each controller only drives its own downstream joint, and
    retains helper/solver motion without feeding the controller world matrix
    back into the authored input.

    Returns:
        Constraint nodes owned by the Control Rig lifecycle.
    """
    helper_nodes = []
    for role, zero in zero_groups.items():
        parent_role = _available_parent_role(role, controls)
        concrete_parent = _actual_joint_parent(cmds, role, role_joints)
        needs_joint_follow = (
            role in _FINGER_ROOT_ROLES
            or role in {"left_knee", "right_knee"}
            or (role in _ARM_ORIENTATION_ROLES and concrete_parent is not None)
        )
        if needs_joint_follow:
            if concrete_parent is None:
                raise MmdControlRigBuildError(
                    f"control parent joint is unresolved: {role}"
                )
            constraint = cmds.parentConstraint(
                concrete_parent,
                zero,
                maintainOffset=True,
                name=f"{zero.rsplit('|', 1)[-1]}_FOLLOW",
            ) or []
            if len(constraint) != 1:
                raise MmdControlRigBuildError(
                    f"control follow constraint was not created: {role}"
                )
            helper_nodes.append(str(constraint[0]))
            continue
        if parent_role:
            cmds.parent(zero, controls[parent_role])
    return tuple(helper_nodes)


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


def _channel_policy_role(role: str) -> str:
    """Return the semantic role used for channel-policy derivation."""

    return str(role)


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
        "pmxFlags": int(binding.pmx_flags),
        "inputKind": binding.input_kind,
        "authoredPlugs": list(binding.authored_plugs),
        "ikSolvers": list(binding.ik_solvers),
        "fallback": role_binding.fallback,
    }
    if role_binding.role in _TWIST_RING_ROLES:
        metadata["twistController"] = True
        metadata["twistAuthority"] = (
            "append_base"
            if binding.input_kind == "append_base"
            else "fixed_axis_direct"
        )
        axis = (
            _vector_attribute(cmds_module, binding.joint, ATTR_MMD_FIXED_AXIS)
            if cmds_module is not None
            else None
        )
        if axis is None and cmds_module is not None:
            axis = _vector_attribute(cmds_module, binding.joint, ATTR_MMD_AXIS_DIRECTION)
        if axis is None:
            axis = getattr(binding, "fixed_axis", None)
        if axis is not None:
            metadata["fixedAxis"] = list(axis)
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
    authoring_bases: Optional[Dict[str, Dict[str, Any]]] = None,
    aim_spaces: Optional[Dict[str, str]] = None,
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
            if aim_spaces is not None:
                if target not in aim_spaces:
                    raise MmdControlRigBuildError(
                        f"control-rig fallback AIM_SPACE is unavailable: {role}->{target}"
                    )
                aim_spaces[role] = aim_spaces[target]
            bindings[role] = _binding_metadata(role_binding, cmds_module=cmds_module)
            if authoring_bases is not None:
                if target not in authoring_bases:
                    raise MmdControlRigBuildError(
                        f"control-rig fallback basis target is unavailable: {role}->{target}"
                    )
                authoring_bases[role] = dict(authoring_bases[target])
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


def _saved_bind_world_matrix(cmds, joint: str):
    """Resolve one joint's static bind matrix without reading live pose.

    Control ``ZERO`` groups are an authoring basis, not an animation sample.
    A build can therefore only use a saved ``dagPose``/skin bind candidate;
    silently falling back to ``joint.worldMatrix`` would capture an IK/VMD
    pose and make the resulting FK controls frame-dependent.
    """

    from mmd_tools.core.physics_bind_basis import (
        BIND_BASIS_MISSING,
        BindBasisResolutionError,
        resolve_imported_bind_world_matrix,
    )

    try:
        matrix = resolve_imported_bind_world_matrix(joint)
    except BindBasisResolutionError as exc:
        if exc.reason_code != BIND_BASIS_MISSING:
            raise MmdControlRigBuildError(
                f"saved bind basis is invalid for control-rig joint: {joint}"
            ) from exc
        if cmds.attributeQuery(
            ATTR_MMD_PMX_REST_POSITION,
            node=joint,
            exists=True,
        ):
            raise MmdControlRigBuildError(
                f"saved bind basis is unavailable for control-rig joint: {joint}"
            ) from exc
        # Synthetic graphs used by headless tests and non-MMD helper
        # transforms have no PMX rest metadata and therefore no authored
        # basis contract to preserve.  Keep their display placement.
        return cmds.xform(joint, query=True, worldSpace=True, matrix=True)
    except Exception as exc:
        raise MmdControlRigBuildError(
            f"saved bind basis could not be resolved for control-rig joint: {joint}"
        ) from exc
    try:
        values = [float(matrix[index]) for index in range(16)]
    except (TypeError, ValueError, IndexError) as exc:
        raise MmdControlRigBuildError(
            f"saved bind basis is not a 4x4 matrix for control-rig joint: {joint}"
        ) from exc
    if len(values) != 16 or not all(math.isfinite(value) for value in values):
        raise MmdControlRigBuildError(
            f"saved bind basis is non-finite for control-rig joint: {joint}"
        )
    return values


def _role_controller_scale(
    cmds,
    root: str,
    role: str,
    binding,
    indexed_joints: Mapping[int, str],
    scene_scale: float,
) -> float:
    """Return display-only scale for one role's authored curve template.

    Primary twist rings are intentionally half-size so they remain readable
    on top of the arm/wrist controls.  The neck shape is authored with a
    local offset; cap it from the local PMX bone length instead of letting a
    distant descendant/whole-model bound make the ring oversized.  Its edited
    template is then tripled to keep the neck control comfortably selectable.
    """

    scale = float(scene_scale)
    if role in _TWIST_RING_ROLES:
        scale *= _TWIST_CURVE_SCALE
    if role != "neck":
        return scale
    local_direction = _vector_attribute(cmds, binding.joint, ATTR_MMD_BONE_OFFSET)
    if local_direction is None:
        local_direction = _pmx_tail_direction(cmds, binding, indexed_joints)
    if local_direction is None:
        return scale * 0.5 * _NECK_CURVE_SCALE
    length = math.sqrt(sum(float(value) ** 2 for value in local_direction))
    if not math.isfinite(length) or length <= 1.0e-8:
        return scale * 0.5 * _NECK_CURVE_SCALE
    # Neck's template extent is roughly two units.  Keep the displayed width
    # below the local bone length while retaining a small usable minimum for
    # very short stylized neck bones.
    local_scale = length * 0.35
    bounded_scale = max(scale * 0.05, min(scale * 0.75, local_scale))
    return bounded_scale * _NECK_CURVE_SCALE


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
