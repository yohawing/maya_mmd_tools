"""Move imported MMD animation ownership between joints and curve controls.

EDIT preserves existing animation nodes by reconnecting their outputs to the
owned curve controls. ATTACHED restores the original dependency-graph edges.
The imported skeleton hierarchy and solver-owned output joints are untouched.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import math
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma

from mmd_tools.core.constants import ATTR_MMD_BONE_NAME, ATTR_MMD_CONTROL_RIG_JSON
from mmd_tools.core.humanik_utils import maya_cmds
from mmd_tools.core.mmd_control_rig_builder import (
    CONTROL_RIG_ATTACHED,
    CONTROL_RIG_BAKED,
    CONTROL_RIG_CONVERTING,
    CONTROL_RIG_CONTROL_OWNED,
    CONTROL_RIG_EDIT,
    CONTROL_RIG_MMD_OWNED,
    MmdControlRigBuildError,
    inspect_mmd_control_rig,
    read_mmd_control_rig_metadata,
    remove_mmd_control_rig,
    resolve_mmd_control_rig_binding_authored_plugs,
    resolve_mmd_control_rig_binding_ik_solvers,
    resolve_mmd_control_rig_binding_joint,
)
from mmd_tools.core.mmd_control_rig_analyzer import (
    INPUT_BONE_MORPH_BASE,
    INPUT_DIRECT_CHANNEL,
    INPUT_IK_CONTROLLER,
    INPUT_IK_LINK_INPUT,
)
from mmd_tools.core.mmd_control_rig_basis import (
    MmdControlRigBasis,
    MmdControlRigBasisError,
    bone_to_control,
    control_to_bone,
    matrix_from_quaternion,
    validate_basis_record,
)
from mmd_tools.core.mmd_control_rig_channels import (
    ROTATE_CHANNELS,
    TRANSLATE_CHANNELS,
    derive_mmd_control_rig_channel_policy,
)
from mmd_tools.core.mmd_control_rig_anim_layers import (
    MmdControlRigAnimLayerError,
    apply_mmd_control_rig_anim_layer_route,
    capture_mmd_control_rig_anim_layers,
    resolve_mmd_control_rig_anim_layer_route,
    restore_mmd_control_rig_anim_layer_journal,
    restore_mmd_control_rig_anim_layer_route,
)
from mmd_tools.core.maya_animation_utils import is_plug_animated_or_driven


_CHANNELS = ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ")
_SAFE_ANIMATION_TYPES = ("animCurve",)
_SAFE_ANIMATION_NODES = frozenset({"pairBlend", "unitConversion"})

# Route classification is persisted with the journal so callers can report
# whether a bake copied an animation curve or sampled a changed transform
# basis.  ``same_basis`` is the only route that promises lossless payload
# preservation; all solver/append/JO conversions are explicitly sampled.
ROUTE_SAME_BASIS = "same_basis"
ROUTE_SAMPLED = "sampled"
ROUTE_UNSUPPORTED = "unsupported"


def _normalize_dense_frame_range(frame_range) -> Optional[Tuple[float, float]]:
    """Normalize an optional inclusive range for temporary dense sampling."""

    if frame_range is None:
        return None
    try:
        values = list(frame_range)
        if len(values) != 2:
            raise ValueError("expected exactly two values")
        start, end = (float(value) for value in values)
    except (TypeError, ValueError, OverflowError) as exc:
        raise MmdControlRigBuildError(
            "automatic Control Rig bake frame_range must contain two numbers"
        ) from exc
    if not math.isfinite(start) or not math.isfinite(end) or start > end:
        raise MmdControlRigBuildError(
            "automatic Control Rig bake frame_range must be finite and ordered"
        )
    return start, end


def _dense_sample_times(times, frame_range=None) -> List[float]:
    """Build a dense sample grid, optionally clipped to an inclusive range.

    The source curves are deliberately left untouched by this helper.  When a
    range is supplied, only the grid used for evaluated samples is clipped;
    authored keys outside the range remain available for the restoration
    journal and for any existing destination curve.
    """

    source_times = sorted({float(time) for time in (times or [])})
    if frame_range is None:
        if not source_times:
            return []
        first = math.floor(min(source_times))
        last = math.ceil(max(source_times))
        return sorted(
            set(source_times)
            | {float(frame) for frame in range(first, last + 1)}
        )

    start, end = _normalize_dense_frame_range(frame_range)

    clipped = {time for time in source_times if start <= time <= end}
    first = math.ceil(start)
    last = math.floor(end)
    if first <= last:
        clipped.update(float(frame) for frame in range(first, last + 1))
    # Preserve non-integral requested endpoints as well.  This keeps the
    # inclusive contract meaningful for callers that use sub-frame ranges.
    clipped.update((start, end))
    return sorted(clipped)


def control_rig_edit_routes_for_joints(joints, *, cmds_module=None) -> Dict[str, Dict[str, Tuple[str, str]]]:
    """Return VMD key destinations for joints owned by control rigs in EDIT."""
    cmds = cmds_module or maya_cmds()
    wanted = set()
    for joint in joints:
        matches = cmds.ls(joint, long=True) or []
        wanted.add(str(matches[0]) if len(matches) == 1 else str(joint))
    routes: Dict[str, Dict[str, Tuple[str, str]]] = {}
    roots = cmds.ls(
        f"*.{ATTR_MMD_CONTROL_RIG_JSON}",
        objectsOnly=True,
        long=True,
        recursive=True,
    ) or []
    for root in roots:
        metadata = read_mmd_control_rig_metadata(str(root), cmds_module=cmds)
        if not metadata or metadata.get("owner") != CONTROL_RIG_CONTROL_OWNED:
            continue
        for role, binding in metadata.get("bindings", {}).items():
            try:
                joint = resolve_mmd_control_rig_binding_joint(cmds, binding)
                authored_plugs = _owned_authored_plugs(
                    role,
                    binding,
                    cmds_module=cmds,
                )
            except MmdControlRigBuildError:
                continue
            if joint not in wanted:
                continue
            control_uuid = metadata.get("controls", {}).get(role)
            if not control_uuid:
                continue
            control = _resolve_uuid(cmds, control_uuid)
            for target in authored_plugs:
                channel = _control_channel_for_target(target)
                if channel in _CHANNELS:
                    routes.setdefault(joint, {})[channel] = (control, channel)
    return routes


def resolve_control_rig_direct_vmd_export_routes(
    model_root: str,
    *,
    cmds_module=None,
) -> Dict[str, Any]:
    """Resolve read-only Control-selector and MMD-value routes in EDIT.

    Controls decide whether a VMD bone track exists; they are never the value
    authority returned by this function.  Values are read from the UUID-backed
    authored plugs recorded for the corresponding MMD joint.  Fallback roles
    are intentionally omitted instead of being aliased to another bone.

    The persisted EDIT journal and the current dependency graph must agree.
    Missing, duplicate, foreign, or partially-owned routes fail closed.
    Actual key discovery remains a converter responsibility.
    """

    cmds = cmds_module or maya_cmds()
    metadata = read_mmd_control_rig_metadata(model_root, cmds_module=cmds)
    if metadata is None:
        raise MmdControlRigBuildError("MMD Control Rig metadata is missing")
    if (
        metadata.get("state") != CONTROL_RIG_EDIT
        or metadata.get("owner") != CONTROL_RIG_CONTROL_OWNED
    ):
        raise MmdControlRigBuildError(
            "direct VMD export requires EDIT / CONTROL_OWNED"
        )
    rig = inspect_mmd_control_rig(model_root, cmds_module=cmds)
    if rig is None:
        raise MmdControlRigBuildError("MMD Control Rig ownership is missing")

    root = _canonical_node(cmds, rig.model_root)
    descendants = {
        str(node)
        for node in (
            cmds.listRelatives(
                root,
                allDescendents=True,
                type="joint",
                fullPath=True,
            )
            or []
        )
    }
    owned_nodes = _resolved_owned_node_names(cmds, metadata)
    _ik_rows, channel_rows, _offset_rows = _resolve_edit_journal(cmds, metadata)
    candidates: Dict[str, Dict[str, Any]] = {}
    omitted_roles = []
    claimed_controls: Dict[str, str] = {}
    claimed_names: Dict[str, str] = {}
    claimed_targets: Dict[str, str] = {}

    bindings = metadata.get("bindings")
    if not isinstance(bindings, Mapping):
        raise MmdControlRigBuildError("control-rig bindings metadata is invalid")
    for role_value, binding in sorted(bindings.items()):
        role = str(role_value)
        if not isinstance(binding, Mapping):
            raise MmdControlRigBuildError(f"invalid Control Rig binding: {role}")
        if binding.get("fallback") is not None:
            omitted_roles.append({"role": role, "reason": "fallback"})
            continue

        control = rig.controls.get(role)
        if not control:
            raise MmdControlRigBuildError(f"missing owned control for {role}")
        control = _canonical_node(cmds, str(control))
        joint = _canonical_node(
            cmds,
            resolve_mmd_control_rig_binding_joint(cmds, binding),
        )
        if joint not in descendants:
            raise MmdControlRigBuildError(
                f"Control Rig binding joint is outside the target model: {role}"
            )
        if joint in candidates:
            raise MmdControlRigBuildError(
                f"multiple Control Rig roles claim one joint: {joint}"
            )
        prior_joint = claimed_controls.get(control)
        if prior_joint is not None and prior_joint != joint:
            raise MmdControlRigBuildError(
                f"one Control Rig control claims multiple joints: {control}"
            )

        bone_name = _required_mmd_bone_name(cmds, joint)
        prior_name_joint = claimed_names.get(bone_name)
        if prior_name_joint is not None and prior_name_joint != joint:
            raise MmdControlRigBuildError(
                f"multiple Control Rig joints claim VMD bone name: {bone_name}"
            )

        policy = derive_mmd_control_rig_channel_policy(role, binding)
        allowed_channels = tuple(
            dict.fromkeys(policy.keyable_channels + policy.passthrough_channels)
        )
        if not allowed_channels:
            raise MmdControlRigBuildError(
                f"Control Rig binding exposes no authored channels: {role}"
            )
        selector_plugs = tuple(f"{control}.{channel}" for channel in allowed_channels)
        for selector_plug in selector_plugs:
            _validate_direct_export_selector_writer(cmds, selector_plug)

        value_routes: Dict[str, Tuple[str, str]] = {}
        owned_targets = _owned_authored_plugs(role, binding, cmds_module=cmds)
        for target in owned_targets:
            logical_channel = _control_channel_for_target(target)
            if logical_channel not in allowed_channels:
                continue
            if logical_channel in value_routes:
                raise MmdControlRigBuildError(
                    f"duplicate authored channel in Control Rig binding: {role}.{logical_channel}"
                )
            canonical_target = _canonical_plug(cmds, target)
            prior_role = claimed_targets.get(canonical_target)
            if prior_role is not None:
                raise MmdControlRigBuildError(
                    f"multiple Control Rig roles claim authored plug: {canonical_target}"
                )
            expected_control = _canonical_plug(
                cmds,
                f"{control}.{logical_channel}",
            )
            matching_rows = [
                row
                for row in channel_rows
                if _canonical_plug(cmds, str(row["target"])) == canonical_target
            ]
            if len(matching_rows) != 1:
                raise MmdControlRigBuildError(
                    f"EDIT journal must contain exactly one authored route: {canonical_target}"
                )
            if _canonical_plug(cmds, str(matching_rows[0]["control"])) != expected_control:
                raise MmdControlRigBuildError(
                    f"EDIT journal control does not match binding: {canonical_target}"
                )
            incoming = _incoming_plugs(cmds, canonical_target)
            if len(incoming) != 1 or not _owned_writer_reaches_control(
                cmds,
                incoming[0] if incoming else "",
                set(selector_plugs),
                owned_nodes,
            ):
                raise MmdControlRigBuildError(
                    f"authored plug has an unknown Control Rig writer: {canonical_target}"
                )
            node, _separator, attribute = canonical_target.rpartition(".")
            value_routes[logical_channel] = (node, attribute)
            claimed_targets[canonical_target] = role

        _require_complete_direct_export_families(role, allowed_channels, value_routes)
        candidates[joint] = {
            "role": role,
            "joint": joint,
            "boneName": bone_name,
            "control": control,
            "selectorPlugs": selector_plugs,
            "valueRoutes": value_routes,
            "ownedFamilies": tuple(
                family
                for family in ("translate", "rotate")
                if any(channel.startswith(family) for channel in allowed_channels)
            ),
        }
        claimed_controls[control] = joint
        claimed_names[bone_name] = joint

    return {
        "modelRoot": root,
        "candidates": candidates,
        "omittedRoles": tuple(omitted_roles),
    }


def _resolved_owned_node_names(cmds, metadata: Mapping[str, Any]) -> Set[str]:
    """Resolve the complete UUID-owned helper inventory for writer checks."""

    result = set()
    for row in metadata.get("nodes", ()) or ():
        if not isinstance(row, Mapping) or not row.get("uuid"):
            raise MmdControlRigBuildError("invalid owned-node metadata row")
        nodes = cmds.ls(str(row["uuid"]), long=True) or []
        if len(nodes) != 1:
            raise MmdControlRigBuildError(
                f"owned control-rig node is missing: {row['uuid']}"
            )
        result.add(_canonical_node_name(cmds, str(nodes[0])))
    return result


def _required_mmd_bone_name(cmds, joint: str) -> str:
    """Read the authoritative VMD name without a Maya leaf-name fallback."""

    if not cmds.attributeQuery(ATTR_MMD_BONE_NAME, node=joint, exists=True):
        raise MmdControlRigBuildError(f"MMD bone name metadata is missing: {joint}")
    name = str(cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_NAME}") or "")
    if not name:
        raise MmdControlRigBuildError(f"MMD bone name metadata is empty: {joint}")
    return name


def _incoming_plugs(cmds, target: str) -> Tuple[str, ...]:
    try:
        values = cmds.listConnections(
            target,
            source=True,
            destination=False,
            plugs=True,
        ) or []
    except Exception as exc:
        raise MmdControlRigBuildError(
            f"could not inspect Control Rig writer: {target}"
        ) from exc
    if isinstance(values, (str, bytes)):
        values = [values]
    return tuple(str(value) for value in values)


def _validate_direct_export_selector_writer(cmds, plug: str) -> None:
    """Reject unknown selector graphs before key census can omit the track."""

    incoming = _incoming_plugs(cmds, plug)
    if len(incoming) > 1:
        raise MmdControlRigBuildError(f"multiple Control Rig selector writers: {plug}")
    if not incoming:
        return
    allowed_wrappers = {"pairBlend", "blendWeighted", "unitConversion", "animLayer"}
    visited = set()
    found_curve = False
    queue = list(incoming)
    while queue:
        source = str(queue.pop())
        node = source.split(".", 1)[0]
        canonical = _canonical_node_name(cmds, node)
        if canonical in visited:
            continue
        visited.add(canonical)
        node_type = str(cmds.nodeType(node) or "")
        if node_type.startswith("animCurve"):
            found_curve = True
            continue
        if node_type == "animLayer":
            continue
        if not (node_type in allowed_wrappers or node_type.startswith("animBlendNode")):
            raise MmdControlRigBuildError(
                f"unsupported Control Rig selector writer: {source} ({node_type})"
            )
        upstream = _incoming_plugs(cmds, node)
        if node_type == "unitConversion" and len(upstream) != 1:
            raise MmdControlRigBuildError(
                f"ambiguous unitConversion selector writer: {node}"
            )
        queue.extend(upstream)
    if not found_curve:
        raise MmdControlRigBuildError(
            f"Control Rig selector writer has no animation curve: {plug}"
        )


def _owned_writer_reaches_control(
    cmds,
    source: str,
    selector_plugs: Set[str],
    owned_nodes: Set[str],
    *,
    _visited: Optional[Set[str]] = None,
) -> bool:
    """Prove that every live authored-writer path ends at the assigned Control."""

    canonical_selectors = {_canonical_plug(cmds, plug) for plug in selector_plugs}
    canonical_source = _canonical_plug(cmds, source)
    if canonical_source in canonical_selectors:
        return True
    node = source.split(".", 1)[0]
    canonical_node = _canonical_node_name(cmds, node)
    visited = _visited if _visited is not None else set()
    if canonical_node in visited:
        return False
    visited.add(canonical_node)
    try:
        node_type = str(cmds.nodeType(node) or "")
    except Exception:
        return False
    if canonical_node not in owned_nodes and node_type != "unitConversion":
        return False
    upstream = _incoming_plugs(cmds, node)
    if not upstream or (node_type == "unitConversion" and len(upstream) != 1):
        return False
    return all(
        _owned_writer_reaches_control(
            cmds,
            upstream_source,
            selector_plugs,
            owned_nodes,
            _visited=set(visited),
        )
        for upstream_source in upstream
    )


def _require_complete_direct_export_families(
    role: str,
    allowed_channels: Tuple[str, ...],
    value_routes: Mapping[str, Tuple[str, str]],
) -> None:
    for family in ("translate", "rotate"):
        expected = {
            channel for channel in allowed_channels if channel.startswith(family)
        }
        if expected and expected != {
            f"{family}{axis}" for axis in "XYZ"
        }:
            raise MmdControlRigBuildError(
                f"partial Control Rig selector family: {role}.{family}"
            )
        actual = {channel for channel in value_routes if channel.startswith(family)}
        if actual != expected:
            raise MmdControlRigBuildError(
                f"partial Control Rig value route: {role}.{family}"
            )


def control_rig_edit_authoring_bases_for_joints(
    joints, *, cmds_module=None
) -> Dict[str, Mapping[str, Any]]:
    """Return UUID-validated authoring bases for complete XYZ routes.

    The regular route helper intentionally keeps its historic two-tuple API.
    VMD keying needs the additional static basis, so expose it through a
    separate lookup and fail closed when a role has only a partial rotation
    route. Complete IK-link ``inputRotateElement`` and optional primary-twist
    Append inputs are valid authored XYZ routes because EDIT installs their
    reciprocal live basis converters.
    """

    cmds = cmds_module or maya_cmds()
    wanted = set()
    for joint in joints:
        matches = cmds.ls(joint, long=True) or []
        wanted.add(str(matches[0]) if len(matches) == 1 else str(joint))
    result: Dict[str, Mapping[str, Any]] = {}
    roots = cmds.ls(
        f"*.{ATTR_MMD_CONTROL_RIG_JSON}",
        objectsOnly=True,
        long=True,
        recursive=True,
    ) or []
    for root in roots:
        metadata = read_mmd_control_rig_metadata(str(root), cmds_module=cmds)
        if not metadata or metadata.get("owner") != CONTROL_RIG_CONTROL_OWNED:
            continue
        bases = metadata.get("authoringBases") or {}
        for role, binding in metadata.get("bindings", {}).items():
            if not isinstance(binding, Mapping):
                continue
            is_twist = bool(binding.get("twistController")) or str(role) in {
                "left_arm_twist",
                "right_arm_twist",
                "left_wrist_twist",
                "right_wrist_twist",
            }
            if binding.get("inputKind") not in {
                INPUT_DIRECT_CHANNEL,
                INPUT_BONE_MORPH_BASE,
                INPUT_IK_LINK_INPUT,
                "append_base" if is_twist else INPUT_DIRECT_CHANNEL,
            }:
                continue
            try:
                joint = resolve_mmd_control_rig_binding_joint(cmds, binding)
                targets = _expanded_authored_plugs(binding, cmds_module=cmds)
                channels = {
                    _control_channel_for_target(target)
                    for target in targets
                    if _rotation_attr_for_target(target)
                    in {"rotateX", "rotateY", "rotateZ"}
                }
                basis = validate_basis_record(bases[role]).to_dict()
            except (KeyError, MmdControlRigBuildError, MmdControlRigBasisError):
                continue
            if joint not in wanted or channels != {"rotateX", "rotateY", "rotateZ"}:
                continue
            prior = result.get(joint)
            if prior is not None and prior != basis:
                # Two rigs claiming one joint with different bases is
                # ambiguous; callers must retain the legacy route instead.
                result.pop(joint, None)
                continue
            result[joint] = basis
    return result


def control_rig_fixed_axis_twist_joints(joints, *, cmds_module=None) -> set[str]:
    """Return EDIT-owned Twist joints with local-Z-only animator controls.

    Their hidden X/Y channels may still carry owned Quaternion motion curves;
    the channel policy keeps those residual channels locked and non-keyable.
    """

    cmds = cmds_module or maya_cmds()
    wanted = set()
    for joint in joints:
        matches = cmds.ls(joint, long=True) or []
        wanted.add(str(matches[0]) if len(matches) == 1 else str(joint))
    result = set()
    roots = cmds.ls(
        f"*.{ATTR_MMD_CONTROL_RIG_JSON}",
        objectsOnly=True,
        long=True,
        recursive=True,
    ) or []
    for root in roots:
        metadata = read_mmd_control_rig_metadata(str(root), cmds_module=cmds)
        if not metadata or metadata.get("owner") != CONTROL_RIG_CONTROL_OWNED:
            continue
        for role, binding in metadata.get("bindings", {}).items():
            if not isinstance(binding, Mapping) or not binding.get("twistController"):
                continue
            policy = derive_mmd_control_rig_channel_policy(str(role), binding)
            if policy.keyable_channels != ("rotateZ",) or not all(
                channel in policy.passthrough_channels for channel in ROTATE_CHANNELS[:2]
            ):
                continue
            try:
                joint = resolve_mmd_control_rig_binding_joint(cmds, binding)
            except MmdControlRigBuildError:
                continue
            if joint in wanted:
                result.add(joint)
    return result


def control_rig_edit_ik_enabled_plugs_for_model(
    model_root: str,
    *,
    cmds_module=None,
) -> Tuple[str, ...]:
    """Resolve target-model-owned ``control.ikEnabled`` plugs in EDIT.

    The model root's inspected Control Rig topology is the ownership boundary.
    Only bindings classified as ``INPUT_IK_CONTROLLER`` may contribute a
    controller plug; stale or malformed metadata fails closed instead of
    guessing from a name or namespace.
    """
    cmds = cmds_module or maya_cmds()
    try:
        rig = inspect_mmd_control_rig(model_root, cmds_module=cmds)
    except (MmdControlRigBuildError, RuntimeError):
        return ()
    if rig is None or rig.owner != CONTROL_RIG_CONTROL_OWNED:
        return ()

    # Always read the scene payload after inspection so binding roles come from
    # the same root whose topology was validated above.
    try:
        scene_metadata = read_mmd_control_rig_metadata(model_root, cmds_module=cmds)
    except (MmdControlRigBuildError, RuntimeError):
        return ()
    if not scene_metadata or scene_metadata.get("owner") != CONTROL_RIG_CONTROL_OWNED:
        return ()

    owned_controls = set()
    for role, binding in (scene_metadata.get("bindings") or {}).items():
        if not isinstance(binding, Mapping) or binding.get("inputKind") != INPUT_IK_CONTROLLER:
            continue
        control = rig.controls.get(role)
        if not control:
            continue
        try:
            plug = f"{control}.ikEnabled"
            if cmds.objExists(plug):
                owned_controls.add(plug)
        except RuntimeError:
            continue

    return tuple(sorted(owned_controls))


def control_rig_quaternion_safe_joints(joints, *, cmds_module=None) -> set[str]:
    """Return EDIT-owned joints whose complete rotation route keeps its basis."""
    cmds = cmds_module or maya_cmds()
    wanted = set()
    for joint in joints:
        matches = cmds.ls(joint, long=True) or []
        wanted.add(str(matches[0]) if len(matches) == 1 else str(joint))
    safe = set()
    roots = cmds.ls(
        f"*.{ATTR_MMD_CONTROL_RIG_JSON}",
        objectsOnly=True,
        long=True,
        recursive=True,
    ) or []
    for root in roots:
        metadata = read_mmd_control_rig_metadata(str(root), cmds_module=cmds)
        if not metadata or metadata.get("owner") != CONTROL_RIG_CONTROL_OWNED:
            continue
        for binding in metadata.get("bindings", {}).values():
            if binding.get("inputKind") != INPUT_DIRECT_CHANNEL:
                continue
            try:
                joint = resolve_mmd_control_rig_binding_joint(cmds, binding)
                targets = _expanded_authored_plugs(binding, cmds_module=cmds)
            except MmdControlRigBuildError:
                continue
            if joint not in wanted:
                continue
            rotations = {
                str(target).rsplit(".", 1)[-1]: str(target)
                for target in targets
                if str(target).rsplit(".", 1)[-1] in {"rotateX", "rotateY", "rotateZ"}
            }
            if set(rotations) != {"rotateX", "rotateY", "rotateZ"}:
                continue
            if all(
                _classify_route(cmds, binding, target)[0] == ROUTE_SAME_BASIS
                for target in rotations.values()
            ):
                safe.add(joint)
    return safe


def enter_mmd_control_rig_edit(model_root: str, *, cmds_module=None) -> Dict[str, Any]:
    """Route MMD authored inputs through owned controls without recreating keys."""
    cmds = cmds_module or maya_cmds()
    root = _canonical_node(cmds, model_root)
    metadata = read_mmd_control_rig_metadata(root, cmds_module=cmds)
    if metadata is None:
        raise MmdControlRigBuildError("build the MMD control rig before entering EDIT")
    if metadata["state"] == CONTROL_RIG_EDIT:
        return metadata
    if metadata["state"] not in {CONTROL_RIG_ATTACHED, CONTROL_RIG_BAKED}:
        raise MmdControlRigBuildError(f"cannot enter EDIT from {metadata['state']}")
    if metadata.get("owner") != CONTROL_RIG_MMD_OWNED:
        raise MmdControlRigBuildError(
            f"cannot enter EDIT while motion owner is {metadata.get('owner')}"
        )

    controls = {
        role: _resolve_uuid(cmds, uuid)
        for role, uuid in metadata.get("controls", {}).items()
    }
    operations: List[Tuple[str, str, str]] = []
    created_curve_nodes: List[str] = []
    curve_representations = _curve_representations(metadata)
    journal: Dict[str, Any] = {
        "channels": [],
        "offsetParentMatrix": [],
        "ikEnabled": [],
        "ikVisibilityInverters": [],
    }
    claimed_targets = set()
    display_reference_time = float(metadata["displayReferenceTime"])
    offset_controls = set()
    transaction_plugs = _entry_transaction_plugs(cmds, metadata, controls)
    try:
        layer_journal = capture_mmd_control_rig_anim_layers(
            cmds,
            root,
            None,
        )
    except MmdControlRigAnimLayerError as exc:
        raise MmdControlRigBuildError(str(exc)) from exc
    layer_blend_plugs = tuple(
        str(route["blend"])
        for route in (layer_journal.get("routes", {}) or {}).values()
        if isinstance(route, Mapping) and route.get("blend")
    )
    transaction_plugs = tuple(sorted(set(transaction_plugs) | set(layer_blend_plugs)))
    _assert_bake_route_supported(
        cmds,
        metadata,
        model_root=root,
        target_plugs=transaction_plugs,
        layer_journal=layer_journal,
    )
    plug_states = _capture_plug_states(cmds, transaction_plugs)
    metadata_before = _raw_metadata(cmds, root)
    added_attributes = []
    locked_twist_plugs = ()
    cmds.undoInfo(openChunk=True, chunkName="Enter MMD Control Rig Edit Locks")

    try:
        locked_twist_plugs = _unlock_fixed_twist_passthroughs(
            cmds,
            _fixed_twist_passthrough_plugs(metadata, controls),
        )
        # Persist the in-flight boundary before any graph mutation.  A failed
        # transition must restore this exact raw payload, including omitted
        # legacy owner fields.
        transitioning = dict(metadata)
        transitioning["owner"] = CONTROL_RIG_CONVERTING
        _write_metadata(cmds, root, transitioning)
        for role, binding in metadata.get("bindings", {}).items():
            if binding.get("inputKind") != INPUT_IK_CONTROLLER:
                continue
            control = controls.get(role)
            if control is None:
                continue
            plug = f"{control}.ikEnabled"
            if not cmds.attributeQuery("ikEnabled", node=control, exists=True):
                cmds.addAttr(control, longName="ikEnabled", attributeType="bool", keyable=True)
                added_attributes.append(plug)
                plug_states[plug] = _capture_plug_states(cmds, (plug,))[plug]

        with _undo_chunk(cmds, "Enter MMD Control Rig Edit"):
            for role, binding in metadata.get("bindings", {}).items():
                control = controls.get(role)
                if control is None:
                    raise MmdControlRigBuildError(f"missing owned control for {role}")
                for target in _owned_authored_plugs(
                    role,
                    binding,
                    cmds_module=cmds,
                ):
                    if target in claimed_targets:
                        continue
                    claimed_targets.add(target)
                    channel = _control_channel_for_target(target)
                    if channel not in _CHANNELS:
                        raise MmdControlRigBuildError(f"unsupported authored channel: {target}")
                    route_class, route_reasons = _classify_route(
                        cmds, binding, target
                    )
                    if route_class == ROUTE_UNSUPPORTED:
                        raise MmdControlRigBuildError(
                            f"unsupported control-rig route: {target} ({', '.join(route_reasons)})"
                        )
                    control_plug = f"{control}.{channel}"
                    channel_policy = derive_mmd_control_rig_channel_policy(
                        str(role), binding
                    )
                    translate_authorable = channel in channel_policy.keyable_channels and (
                        channel in TRANSLATE_CHANNELS
                    )
                    layer_route = layer_journal.get("routes", {}).get(target)
                    if translate_authorable and layer_route is not None:
                        layer_blend = layer_route.get("blend")
                        if not layer_route.get("curve") or not layer_blend:
                            raise MmdControlRigBuildError(
                                "translate animation-layer route has no direct curve/blend input: "
                                f"{target}"
                            )
                        baseline = _target_value_at_time(
                            cmds,
                            str(layer_blend),
                            display_reference_time,
                        )
                    else:
                        baseline = (
                            _target_value_at_time(cmds, target, display_reference_time)
                            if translate_authorable
                            else None
                        )
                    if layer_route is not None:
                        incoming_control = [
                            str(source)
                            for source in (
                                cmds.listConnections(
                                    control_plug,
                                    source=True,
                                    destination=False,
                                    plugs=True,
                                )
                                or []
                            )
                        ]
                        layer_source = layer_route.get("curve")
                        if incoming_control and incoming_control != [layer_source]:
                            raise MmdControlRigBuildError(
                                f"foreign animation-layer controller source: {control_plug}"
                            )
                        value = float(cmds.getAttr(target))
                        if translate_authorable:
                            control_source, baseline_node, baseline_target = (
                                _connect_layer_translate_baseline(
                                    cmds,
                                    layer_route,
                                    control_plug,
                                    float(baseline),
                                    operations,
                                    created_curve_nodes,
                                    existing_control_source=_existing_control_curve(
                                        cmds,
                                        curve_representations,
                                        target,
                                    ),
                                )
                            )
                        else:
                            control_source = layer_source
                            apply_mmd_control_rig_anim_layer_route(
                                cmds,
                                layer_route,
                                control_plug,
                                operations,
                            )
                        row = _journal_plug_row(
                            cmds,
                            source=layer_source,
                            control=control_plug,
                            target=target,
                            value=value,
                            control_source=control_source,
                            route_class=ROUTE_SAMPLED,
                            route_reasons=("anim_layer",),
                            layer_route=layer_route,
                            authoring_basis=metadata.get("authoringBases", {}).get(role),
                            twist_controller=bool(
                                metadata.get("bindings", {})
                                .get(role, {})
                                .get("twistController")
                            ),
                            fixed_axis_twist=_is_fixed_axis_twist_role(
                                role,
                                binding,
                            ),
                        )
                        if translate_authorable:
                            row["translateBaseline"] = float(baseline)
                            row["translateReferenceTime"] = float(display_reference_time)
                            row["translateBaselineOutput"] = f"{baseline_node}.output1D"
                            row["translateBaselineOutputRef"] = {
                                "nodeUuid": str((cmds.ls(baseline_node, uuid=True) or [""])[0]),
                                "attribute": "output1D",
                            }
                            row["translateBaselineTarget"] = baseline_target
                            row["translateBaselineTargetRef"] = _plug_reference(
                                cmds,
                                baseline_target,
                            )
                        journal["channels"].append(row)
                        _record_curve_representation(
                            curve_representations,
                            target,
                            layer_source,
                            control_source,
                            cmds,
                        )
                        continue
                    incoming = cmds.listConnections(
                        target, source=True, destination=False, plugs=True
                    ) or []
                    if len(incoming) > 1:
                        raise MmdControlRigBuildError(f"multiple incoming sources: {target}")
                    control_source = _existing_control_curve(
                        cmds, curve_representations, target
                    )
                    control_incoming = [
                        str(source)
                        for source in (
                            cmds.listConnections(
                                control_plug,
                                source=True,
                                destination=False,
                                plugs=True,
                            )
                            or []
                        )
                    ]
                    if control_incoming:
                        recorded_source = (
                            _canonical_plug(cmds, control_source)
                            if control_source
                            else None
                        )
                        live_sources = [
                            _canonical_plug(cmds, source)
                            for source in control_incoming
                        ]
                        if len(live_sources) != 1 or live_sources[0] != recorded_source:
                            raise MmdControlRigBuildError(
                                f"control channel already driven: {control_plug}"
                            )
                        # Older non-identity basis bakes retained the detached
                        # CONTROL representation on the controller. Its UUID is
                        # already authoritative in curveRepresentations, so it
                        # is safe to detach and reuse. Rollback reconnects the
                        # exact legacy edge if a later step fails.
                        cmds.disconnectAttr(control_incoming[0], control_plug)
                        operations.append(("connect", control_incoming[0], control_plug))

                    value = float(cmds.getAttr(target))
                    source = str(incoming[0]) if incoming else None
                    duplicated_control_source = False
                    if source and control_source is None:
                        control_source = _duplicate_animation_source(
                            cmds, source, created_curve_nodes
                        )
                        duplicated_control_source = True
                    elif source and control_source is not None:
                        # A previous EDIT/BAKE cycle already owns a detached
                        # controller representation. Reuse its UUID-backed
                        # curve instead of growing a new node each cycle.
                        pass
                    if source is not None:
                        _require_animation_source(cmds, source, target)
                        cmds.disconnectAttr(source, target)
                        operations.append(("connect", source, target))
                        if control_source:
                            if duplicated_control_source and baseline is not None:
                                _offset_animation_curve_values(
                                    cmds,
                                    control_source,
                                    -baseline,
                                )
                            if cmds.isConnected(control_source, control_plug):
                                cmds.disconnectAttr(control_source, control_plug)
                            cmds.connectAttr(control_source, control_plug, force=False)
                            operations.append(("disconnect", control_source, control_plug))
                    else:
                        cmds.setAttr(control_plug, value)
                    row = _journal_plug_row(
                        cmds,
                        source=source,
                        control=control_plug,
                        target=target,
                        value=value,
                        control_source=control_source,
                        route_class=route_class,
                        route_reasons=route_reasons,
                        authoring_basis=metadata.get("authoringBases", {}).get(role),
                        twist_controller=bool(
                            metadata.get("bindings", {})
                            .get(role, {})
                            .get("twistController")
                        ),
                        fixed_axis_twist=_is_fixed_axis_twist_role(
                            role,
                            binding,
                        ),
                    )
                    if baseline is not None:
                        if control_source is None:
                            cmds.setAttr(control_plug, 0.0)
                        baseline_node = _connect_translate_baseline(
                            cmds,
                            control_plug,
                            target,
                            baseline,
                            operations,
                        )
                        created_curve_nodes.append(baseline_node)
                        row["translateBaseline"] = baseline
                        row["translateReferenceTime"] = float(display_reference_time)
                        row["translateBaselineOutput"] = f"{baseline_node}.output1D"
                        row["translateBaselineOutputRef"] = {
                            "nodeUuid": str((cmds.ls(baseline_node, uuid=True) or [""])[0]),
                            "attribute": "output1D",
                        }
                    else:
                        cmds.connectAttr(control_plug, target, force=False)
                        operations.append(("disconnect", control_plug, target))
                    journal["channels"].append(row)
                    _record_curve_representation(
                        curve_representations,
                        target,
                        source,
                        control_source,
                        cmds,
                    )

                offset_controls.add(control)

                if binding.get("inputKind") == INPUT_IK_CONTROLLER:
                    visibility_controls = None
                    hidden_when_enabled_controls = None
                    if role in {"left_foot_ik", "right_foot_ik"}:
                        side = str(role).split("_", 1)[0]
                        visibility_controls = tuple(
                            controls[control_role]
                            for control_role in (
                                f"{side}_foot_ik_parent",
                                f"{side}_foot_ik",
                                f"{side}_toe_ik",
                            )
                            if control_role in controls
                        )
                        hidden_when_enabled_controls = tuple(
                            controls[control_role]
                            for control_role in (f"{side}_knee",)
                            if control_role in controls
                        )
                    _connect_ik_enabled(
                        cmds,
                        control,
                        binding,
                        journal,
                        operations,
                        created_curve_nodes=created_curve_nodes,
                        curve_representations=curve_representations,
                        layer_routes=layer_journal.get("routes", {}),
                        visibility_controls=visibility_controls,
                        hidden_when_enabled_controls=hidden_when_enabled_controls,
                    )

            _rebase_new_ik_link_control_curves(
                cmds,
                journal["channels"],
                created_curve_nodes,
                curve_representations,
            )
            _zero_control_display_offsets(
                cmds,
                sorted(offset_controls),
                journal,
                reference_time=display_reference_time,
            )
            rotation_converters = _create_live_rotation_converters(
                cmds,
                journal["channels"],
                metadata.get("authoringBases", {}),
                operations,
                created_curve_nodes,
            )
            from mmd_tools.converters.vmd_rotation_time_curve import (
                share_vmd_rotation_time_curve,
            )

            for group in _rotation_channel_groups(journal["channels"]):
                rows_by_attr = {
                    _rotation_attr_for_target(row["target"]): row for row in group
                }
                attrs = ("rotateX", "rotateY", "rotateZ")
                if set(rows_by_attr) != set(attrs):
                    continue
                share_vmd_rotation_time_curve(
                    cmds,
                    [rows_by_attr[attr].get("source") for attr in attrs],
                    [rows_by_attr[attr].get("controlSource") for attr in attrs],
                )
            metadata["journal"] = journal
            if rotation_converters:
                metadata["rotationConverters"] = rotation_converters
                _record_rotation_converter_nodes(metadata, rotation_converters)
            _record_ik_visibility_inverter_nodes(
                metadata,
                journal.get("ikVisibilityInverters", ()),
            )
            for row in curve_representations:
                row["activeOwner"] = CONTROL_RIG_CONTROL_OWNED
            metadata["curveRepresentations"] = curve_representations
            metadata["animLayerJournal"] = layer_journal
            metadata["routeDiagnostics"] = _route_diagnostics(journal)
            metadata["state"] = CONTROL_RIG_EDIT
            metadata["owner"] = CONTROL_RIG_CONTROL_OWNED
            _write_metadata(cmds, root, metadata)
    except Exception as exc:
        rollback_error = None
        try:
            _rollback(cmds, operations)
            _assert_created_curve_nodes_safe(
                cmds,
                created_curve_nodes,
                transaction_plugs,
            )
            _restore_plug_states(
                cmds,
                plug_states,
                allowed_sources={
                    str(source)
                    for _action, source, _target in operations
                    if source
                },
                owned_nodes=set(created_curve_nodes),
            )
            for node in reversed(created_curve_nodes):
                if cmds.objExists(node):
                    cmds.delete(node)
            for plug in reversed(added_attributes):
                node, _, attribute = plug.partition(".")
                if cmds.objExists(plug) and not (
                    cmds.listConnections(plug, source=False, destination=True) or []
                ):
                    cmds.deleteAttr(f"{node}.{attribute}")
            restore_mmd_control_rig_anim_layer_journal(cmds, layer_journal)
        except Exception as rollback_exc:
            rollback_error = rollback_exc
            # A caller may inject a failure into connectAttr itself. The
            # completed undo chunk is the only reliable graph restore path in
            # that case because the patched writer cannot reconnect sources.
            try:
                cmds.undo()
            except Exception:
                pass
            for node in reversed(created_curve_nodes):
                if cmds.objExists(node):
                    try:
                        _assert_created_curve_nodes_safe(
                            cmds,
                            (node,),
                            transaction_plugs,
                        )
                        cmds.delete(node)
                    except Exception:
                        # A topology-drifted curve may carry a foreign edge;
                        # leave it and its writer untouched.
                        continue
        try:
            # Metadata is restored even when a test-injected connection
            # failure also affects best-effort graph rollback.
            _restore_raw_metadata(cmds, root, metadata_before)
        except Exception as metadata_exc:
            rollback_error = rollback_error or metadata_exc
        if rollback_error is not None:
            raise MmdControlRigBuildError(
                f"control-rig enter EDIT failed and rollback was incomplete: {rollback_error}"
            ) from exc
        raise
    finally:
        _relock_fixed_twist_passthroughs(cmds, locked_twist_plugs)
        cmds.undoInfo(closeChunk=True)
    return metadata


def restore_mmd_control_rig_attached(model_root: str, *, cmds_module=None) -> Dict[str, Any]:
    """Restore the exact pre-EDIT animation connections and channel values."""
    cmds = cmds_module or maya_cmds()
    root = _canonical_node(cmds, model_root)
    metadata = read_mmd_control_rig_metadata(root, cmds_module=cmds)
    if metadata is None:
        raise MmdControlRigBuildError("MMD control rig metadata is missing")
    if metadata["state"] == CONTROL_RIG_ATTACHED:
        if metadata.get("owner") != CONTROL_RIG_MMD_OWNED:
            raise MmdControlRigBuildError(
                f"cannot restore ATTACHED while motion owner is {metadata.get('owner')}"
            )
        return metadata
    if metadata["state"] == CONTROL_RIG_BAKED:
        if metadata.get("owner") != CONTROL_RIG_MMD_OWNED:
            raise MmdControlRigBuildError(
                f"cannot restore ATTACHED while motion owner is {metadata.get('owner')}"
            )
        metadata["state"] = CONTROL_RIG_ATTACHED
        metadata["owner"] = CONTROL_RIG_MMD_OWNED
        _write_metadata(cmds, root, metadata)
        return metadata
    if metadata["state"] != CONTROL_RIG_EDIT:
        raise MmdControlRigBuildError(f"cannot restore ATTACHED from {metadata['state']}")

    ik_rows, channel_rows, offset_rows = _resolve_edit_journal(cmds, metadata)
    rotation_converters = _resolve_rotation_converters(cmds, metadata)

    with _edit_exit_transaction(
        cmds,
        root,
        "Restore MMD Control Rig Attached",
        "restore",
        ik_rows + channel_rows,
        offset_rows,
        layer_journal=metadata.get("animLayerJournal"),
        owned_nodes=tuple(
            node
            for converter in rotation_converters
            for node in (
                converter.get("compose"),
                converter.get("mult"),
                converter.get("decompose"),
            )
            if node
        ) + _translate_baseline_nodes(ik_rows + channel_rows),
    ):
        for row in reversed(ik_rows):
            if row.get("layerRoute"):
                if row.get("translateBaselineOutput"):
                    _disconnect_translate_baseline(cmds, row)
                    _disconnect_layer_control_source(cmds, row)
                restore_mmd_control_rig_anim_layer_route(
                    cmds,
                    row["layerRoute"],
                    row["control"],
                )
                continue
            source, target = row["control"], row["target"]
            _disconnect_translate_baseline(cmds, row)
            if cmds.isConnected(source, target):
                cmds.disconnectAttr(source, target)
            _disconnect_owned_rotation_writer(cmds, target, rotation_converters)
            control_source = row.get("controlSource")
            if control_source and cmds.isConnected(control_source, source):
                cmds.disconnectAttr(control_source, source)
            prior = row.get("source")
            if prior:
                cmds.connectAttr(prior, target, force=False)
            else:
                cmds.setAttr(target, bool(row["value"]))
        for row in reversed(channel_rows):
            if row.get("layerRoute"):
                if row.get("translateBaselineOutput"):
                    _disconnect_translate_baseline(cmds, row)
                    _disconnect_layer_control_source(cmds, row)
                restore_mmd_control_rig_anim_layer_route(
                    cmds,
                    row["layerRoute"],
                    row["control"],
                )
                continue
            control, target = row["control"], row["target"]
            _disconnect_translate_baseline(cmds, row)
            if cmds.isConnected(control, target):
                cmds.disconnectAttr(control, target)
            _disconnect_owned_rotation_writer(cmds, target, rotation_converters)
            control_source = row.get("controlSource")
            if control_source and cmds.isConnected(control_source, control):
                cmds.disconnectAttr(control_source, control)
            source = row.get("source")
            if source:
                if cmds.isConnected(source, control):
                    cmds.disconnectAttr(source, control)
                cmds.connectAttr(source, target, force=False)
            else:
                cmds.setAttr(target, float(row["value"]))
        _restore_offsets(cmds, offset_rows, strict=True)
        metadata.pop("journal", None)
        _drop_rotation_converter_nodes(metadata, rotation_converters)
        metadata.pop("rotationConverters", None)
        for row in metadata.get("curveRepresentations", []):
            if isinstance(row, dict):
                row["activeOwner"] = CONTROL_RIG_MMD_OWNED
        metadata["state"] = CONTROL_RIG_ATTACHED
        metadata["owner"] = CONTROL_RIG_MMD_OWNED
        _remove_rotation_converters(cmds, rotation_converters)
        _write_metadata(cmds, root, metadata)
        _remove_translate_baseline_nodes(cmds, ik_rows + channel_rows)
    return metadata


def restore_and_remove_mmd_control_rig(model_root: str, *, cmds_module=None) -> bool:
    """Restore MMD ownership, then delete the UUID-owned Control Rig.

    The topology is validated before changing ownership so a malformed or
    user-modified rig fails closed without partially restoring the scene.
    EDIT journals are replayed by :func:`restore_mmd_control_rig_attached`,
    which reinstates the exact MMD-side connections and channel values before
    the controls are removed.
    """

    cmds = cmds_module or maya_cmds()
    metadata = read_mmd_control_rig_metadata(model_root, cmds_module=cmds)
    if metadata is None:
        return False
    inspect_mmd_control_rig(model_root, cmds_module=cmds)
    with _undo_chunk(cmds, "Restore and Remove MMD Control Rig"):
        restore_mmd_control_rig_attached(model_root, cmds_module=cmds)
        return remove_mmd_control_rig(model_root, cmds_module=cmds)


def bake_mmd_control_rig(
    model_root: str,
    *,
    cmds_module=None,
    frame_range=None,
) -> Dict[str, Any]:
    """Commit controller animation edges back to MMD authored inputs.

    ``frame_range`` limits only temporary dense evaluation.  The default
    ``None`` retains the historical manual-bake behavior.
    """
    cmds = cmds_module or maya_cmds()
    root = _canonical_node(cmds, model_root)
    metadata = read_mmd_control_rig_metadata(root, cmds_module=cmds)
    if metadata is None or metadata.get("state") != CONTROL_RIG_EDIT:
        state = metadata.get("state") if metadata else "missing"
        raise MmdControlRigBuildError(f"cannot bake MMD control rig from {state}")
    if metadata.get("owner") != CONTROL_RIG_CONTROL_OWNED:
        raise MmdControlRigBuildError(
            f"cannot bake MMD control rig while motion owner is {metadata.get('owner')}"
        )
    dense_frame_range = _normalize_dense_frame_range(frame_range)
    ik_rows, channel_rows, offset_rows = _resolve_edit_journal(cmds, metadata)
    layer_journal = metadata.get("animLayerJournal")
    _assert_bake_route_supported(
        cmds,
        metadata,
        model_root=root,
        target_plugs=[row["target"] for row in ik_rows + channel_rows],
        layer_journal=layer_journal,
    )
    rotation_converters = _resolve_rotation_converters(cmds, metadata)
    rows = ik_rows + channel_rows
    created_curve_nodes = []
    sources_by_control = {}
    for row in rows:
        incoming = cmds.listConnections(
            row["control"], source=True, destination=False, plugs=True
        ) or []
        if len(incoming) > 1:
            raise MmdControlRigBuildError(
                f"multiple controller animation inputs: {row['control']}"
            )
        source = str(incoming[0]) if incoming else None
        if source:
            _require_animation_source(cmds, source, row["target"])
        sources_by_control[row["control"]] = source

    rotation_groups = [
        group
        for group in _rotation_channel_groups(
            channel_rows,
            include_sampled_direct=True,
            include_sampled_passthrough=True,
        )
        if not any(row.get("layerRoute") for row in group)
        and (
            all(row.get("twistController") for row in group)
            or any(
                row.get("routeClass", ROUTE_SAME_BASIS) == ROUTE_SAMPLED
                for row in group
            )
            or _rotation_group_uses_quaternion(cmds, group, sources_by_control)
        )
    ]
    quaternion_grouped_rows = {
        id(row)
        for group in rotation_groups
        if all(row.get("twistController") for row in group)
        or _rotation_group_uses_quaternion(cmds, group, sources_by_control)
        for row in group
    }
    live_target_samples_by_group = {
        id(group[0]): _capture_live_target_rotation_samples(
            cmds,
            group,
            sources_by_control,
            frame_range=dense_frame_range,
        )
        for group in rotation_groups
        if _rotation_group_requires_live_target_sampling(group)
    }
    with _edit_exit_transaction(
        cmds,
        root,
        "Bake MMD Control Rig",
        "bake",
        rows,
        offset_rows,
        layer_journal=layer_journal,
        curve_plugs=tuple(
            plug
            for row in rows
            for plug in (row.get("source"), row.get("controlSource"))
            if plug
        ),
        created_curve_nodes=created_curve_nodes,
        rotation_groups=rotation_groups,
        owned_nodes=tuple(
            node
            for converter in rotation_converters
            for node in (
                converter.get("compose"),
                converter.get("mult"),
                converter.get("decompose"),
            )
            if node
        ) + _translate_baseline_nodes(rows),
    ):
        mmd_sources_by_control = {}
        for row in reversed(ik_rows):
            mmd_sources_by_control[row["control"]] = _commit_control_input(
                cmds,
                row,
                sources_by_control[row["control"]],
                created_curve_nodes=created_curve_nodes,
                frame_range=dense_frame_range,
            )
        _disconnect_rotation_converters(cmds, rotation_converters)
        grouped_rows = {id(row) for group in rotation_groups for row in group}
        for group in reversed(rotation_groups):
            group_sources = _commit_control_rotation_group(
                cmds,
                group,
                sources_by_control,
                created_curve_nodes=created_curve_nodes,
                quaternion_interpolation=all(
                    id(row) in quaternion_grouped_rows for row in group
                ),
                evaluated_target_samples=live_target_samples_by_group.get(
                    id(group[0])
                ),
                frame_range=dense_frame_range,
            )
            mmd_sources_by_control.update(group_sources)
        for row in reversed(channel_rows):
            if id(row) in grouped_rows:
                continue
            mmd_sources_by_control[row["control"]] = _commit_control_input(
                cmds,
                row,
                sources_by_control[row["control"]],
                created_curve_nodes=created_curve_nodes,
                frame_range=dense_frame_range,
            )
        _restore_offsets(cmds, offset_rows, strict=True)
        _drop_rotation_converter_nodes(metadata, rotation_converters)
        representations = _curve_representations(metadata)
        metadata["curveRepresentations"] = representations
        for row in rows:
            _record_curve_representation(
                representations,
                row["target"],
                mmd_sources_by_control.get(row["control"]) or row.get("source"),
                row.get("controlSource") or sources_by_control[row["control"]],
                cmds,
                quaternion_interpolation=(
                    id(row) in quaternion_grouped_rows
                    if id(row) in grouped_rows
                    and str(row.get("target", "")).rsplit(".", 1)[-1]
                    in {"rotateX", "rotateY", "rotateZ"}
                    else None
                ),
            )
        metadata.pop("journal", None)
        metadata.pop("rotationConverters", None)
        metadata["routeDiagnostics"] = _route_diagnostics(
            {"channels": rows, "ikEnabled": []}
        )
        for row in metadata.get("curveRepresentations", []):
            if isinstance(row, dict):
                row["activeOwner"] = CONTROL_RIG_MMD_OWNED
        metadata["state"] = CONTROL_RIG_BAKED
        metadata["owner"] = CONTROL_RIG_MMD_OWNED
        _remove_rotation_converters(cmds, rotation_converters)
        _write_metadata(cmds, root, metadata)
        _remove_translate_baseline_nodes(cmds, rows)
    return metadata


def _resolve_edit_journal(cmds, metadata: Mapping[str, Any]):
    """Resolve an EDIT journal into rename-stable transaction rows."""
    journal = metadata.get("journal")
    if not isinstance(journal, dict):
        raise MmdControlRigBuildError("EDIT connection journal is missing")
    ik_rows = [_resolve_journal_plug_row(cmds, row) for row in journal.get("ikEnabled", [])]
    channel_rows = [
        _resolve_journal_plug_row(cmds, row) for row in journal.get("channels", [])
    ]
    offset_rows = [
        _resolve_journal_offset_row(cmds, row)
        for row in journal.get("offsetParentMatrix", [])
    ]
    return ik_rows, channel_rows, offset_rows


def _entry_transaction_plugs(cmds, metadata, controls) -> Tuple[str, ...]:
    """Collect every plug that enter-EDIT may disconnect or overwrite."""
    plugs = set()
    for role, binding in metadata.get("bindings", {}).items():
        control = controls.get(role)
        if control is None:
            raise MmdControlRigBuildError(f"missing owned control for {role}")
        for target in _owned_authored_plugs(
            role,
            binding,
            cmds_module=cmds,
        ):
            plugs.add(target)
            plugs.add(f"{control}.{_control_channel_for_target(target)}")
        plugs.add(f"{control}.offsetParentMatrix")
        if binding.get("inputKind") == INPUT_IK_CONTROLLER:
            for solver in resolve_mmd_control_rig_binding_ik_solvers(cmds, binding):
                plugs.add(f"{solver}.enabled")
            # ikEnabled is added lazily for legacy control nodes.  It is
            # included when already present; enter handles a newly-added plug
            # explicitly so its removal is part of rollback.
            if cmds.attributeQuery("ikEnabled", node=control, exists=True):
                plugs.add(f"{control}.ikEnabled")
            if role in {"left_foot_ik", "right_foot_ik"}:
                side = str(role).split("_", 1)[0]
                for display_role in (
                    f"{side}_foot_ik_parent",
                    f"{side}_foot_ik",
                    f"{side}_toe_ik",
                    f"{side}_knee",
                ):
                    display_control = controls.get(display_role)
                    if display_control is None:
                        continue
                    for shape in cmds.listRelatives(
                        display_control,
                        shapes=True,
                        fullPath=True,
                    ) or []:
                        plugs.add(f"{shape}.visibility")
    for plug in plugs:
        if not cmds.objExists(plug):
            raise MmdControlRigBuildError(f"enter EDIT plug is missing: {plug}")
    return tuple(sorted(plugs))


@contextmanager
def _edit_exit_transaction(
    cmds,
    root,
    label,
    action,
    rows,
    offset_rows,
    *,
    curve_plugs=(),
    created_curve_nodes=None,
    rotation_groups=(),
    layer_journal: Optional[Mapping[str, Any]] = None,
    owned_nodes=(),
):
    """Snapshot EDIT plugs and roll back a failed state transition."""
    twist_passthrough_plugs = tuple(
        str(row["control"])
        for row in rows
        if row.get("fixedAxisTwist")
        and str(row.get("control", "")).rsplit(".", 1)[-1]
        in {"rotateX", "rotateY"}
    )
    transaction_plugs = {
        str(row[key])
        for row in rows
        for key in ("control", "target")
    }
    transaction_plugs.update(
        str(row["translateBaselineTarget"])
        for row in rows
        if row.get("translateBaselineTarget")
    )
    transaction_plugs.update(str(row["control"]) for row in offset_rows)
    plug_states = _capture_plug_states(cmds, transaction_plugs)
    metadata_before = _raw_metadata(cmds, root)
    curve_snapshots = _capture_curve_snapshots(cmds, curve_plugs)
    curve_input_states = _capture_curve_input_states(cmds, curve_plugs)
    rotation_states = _capture_rotation_interpolation_states(cmds, rotation_groups)
    allowed_sources = _transaction_owned_sources(rows, curve_plugs, layer_journal)
    owned_nodes = set(str(node) for node in (owned_nodes or ()))
    locked_twist_plugs = ()
    cmds.undoInfo(openChunk=True, chunkName=f"{label} Locks")
    try:
        locked_twist_plugs = _unlock_fixed_twist_passthroughs(
            cmds,
            twist_passthrough_plugs,
        )
        transitioning = json.loads(metadata_before)
        transitioning["owner"] = CONTROL_RIG_CONVERTING
        _write_metadata(cmds, root, transitioning)
        yield
    except Exception as exc:
        try:
            _assert_created_curve_nodes_safe(
                cmds,
                created_curve_nodes or (),
                transaction_plugs,
            )
            # Sampled routes may create a new MMD animCurve when the source
            # joint had no authored curve before EDIT.  Remove those nodes
            # before restoring plug state so their transient connections do
            # not prevent the exact rollback from being re-established.
            for node in reversed(created_curve_nodes or ()):
                if cmds.objExists(node):
                    cmds.delete(node)
            _restore_plug_states(
                cmds,
                plug_states,
                allowed_sources=allowed_sources,
                owned_nodes=owned_nodes | set(created_curve_nodes or ()),
            )
            _restore_curve_snapshots(cmds, curve_snapshots)
            _restore_curve_input_states(cmds, curve_input_states)
            _restore_rotation_interpolation_states(cmds, rotation_states)
            if layer_journal:
                restore_mmd_control_rig_anim_layer_journal(cmds, layer_journal)
            _restore_raw_metadata(cmds, root, metadata_before)
        except Exception as rollback_exc:
            _discard_curve_snapshots(cmds, curve_snapshots)
            raise MmdControlRigBuildError(
                f"control-rig {action} failed and rollback was incomplete: {rollback_exc}"
            ) from exc
        _discard_curve_snapshots(cmds, curve_snapshots)
        raise
    else:
        _discard_curve_snapshots(cmds, curve_snapshots)
    finally:
        _relock_fixed_twist_passthroughs(cmds, locked_twist_plugs)
        cmds.undoInfo(closeChunk=True)


def _expanded_authored_plugs(
    binding: Mapping[str, Any], *, cmds_module=None
) -> Tuple[str, ...]:
    plugs = []
    authored_plugs = (
        resolve_mmd_control_rig_binding_authored_plugs(cmds_module, binding)
        if cmds_module is not None
        else tuple(str(plug) for plug in binding.get("authoredPlugs", []))
    )
    for plug in authored_plugs:
        if plug.endswith((".translate", ".baseTranslate")):
            plugs.extend(f"{plug}{axis}" for axis in "XYZ")
        elif plug.endswith((".rotate", ".baseRotate")):
            plugs.extend(f"{plug}{axis}" for axis in "XYZ")
        else:
            plugs.append(str(plug))
    return tuple(plugs)


def _owned_authored_plugs(
    role: str,
    binding: Mapping[str, Any],
    *,
    cmds_module=None,
) -> Tuple[str, ...]:
    """Return only authored targets exposed by the role's channel policy."""
    policy = derive_mmd_control_rig_channel_policy(_channel_policy_role(role), binding)
    allowed = set(policy.keyable_channels + policy.passthrough_channels)
    return tuple(
        target
        for target in _expanded_authored_plugs(binding, cmds_module=cmds_module)
        if _control_channel_for_target(target) in allowed
    )


def _channel_policy_role(role: str) -> str:
    """Return the semantic role used for channel-policy derivation."""

    return str(role)


def _is_fixed_axis_twist_role(role: str, binding: Mapping[str, Any]) -> bool:
    policy = derive_mmd_control_rig_channel_policy(str(role), binding)
    return bool(policy.passthrough_channels)


def _fixed_twist_passthrough_plugs(metadata, controls) -> Tuple[str, ...]:
    plugs = []
    for role, binding in metadata.get("bindings", {}).items():
        if not _is_fixed_axis_twist_role(str(role), binding):
            continue
        control = controls.get(role)
        if control:
            plugs.extend(f"{control}.{channel}" for channel in ("rotateX", "rotateY"))
    return tuple(plugs)


def _unlock_fixed_twist_passthroughs(cmds, plugs) -> Tuple[str, ...]:
    locked = tuple(
        str(plug)
        for plug in dict.fromkeys(plugs)
        if cmds.objExists(plug) and bool(cmds.getAttr(plug, lock=True))
    )
    for plug in locked:
        cmds.setAttr(plug, lock=False)
    return locked


def _relock_fixed_twist_passthroughs(cmds, plugs) -> None:
    for plug in plugs:
        if cmds.objExists(plug):
            cmds.setAttr(plug, lock=True, keyable=False, channelBox=False)


def _control_channel_for_target(target: str) -> str:
    """Map MMD append child names onto the equivalent control channel."""
    channel = target.rsplit(".", 1)[-1]
    if channel.startswith("baseRotate") and channel[-1:] in "XYZ":
        return f"rotate{channel[-1]}"
    if channel.startswith("baseTranslate") and channel[-1:] in "XYZ":
        return f"translate{channel[-1]}"
    if channel.startswith("inputRotateElement") and channel[-1:] in "XYZ":
        return f"rotate{channel[-1]}"
    return channel


def _require_animation_source(cmds, source: str, target: str) -> None:
    node = source.split(".", 1)[0]
    node_type = str(cmds.nodeType(node))
    if node_type in _SAFE_ANIMATION_NODES or node_type.startswith(_SAFE_ANIMATION_TYPES):
        return
    raise MmdControlRigBuildError(
        f"unsupported animation input blocks EDIT: {source} -> {target} ({node_type}); "
        "animLayer/animBlend routes must be flattened explicitly by the caller"
    )


def _assert_bake_route_supported(
    cmds,
    metadata: Mapping[str, Any],
    *,
    model_root: Optional[str] = None,
    target_plugs=(),
    layer_journal: Optional[Mapping[str, Any]] = None,
) -> None:
    """Fail closed for unsupported layer/blend writers.

    A captured target-exclusive layer journal is the one supported exception:
    its direct source is transferred through the controller and never
    flattened.  All other populated layers and blend inputs remain rejected.
    """
    supported_routes = {
        str(target): route
        for target, route in (layer_journal or {}).get("routes", {}).items()
    }
    # A non-base layer owns an animBlendNode and flattening it would silently
    # discard layer weights.  Reject any populated non-base layer before the
    # transaction mutates the graph.  Maya command stubs may not implement
    # animLayer; in that case source-node validation below remains authoritative.
    try:
        layers = cmds.ls(type="animLayer") or []
    except Exception:
        layers = []
    for layer in layers:
        name = str(layer)
        if name in {"BaseAnimation", "baseAnimation"}:
            continue
        try:
            attrs = cmds.animLayer(name, query=True, attribute=True) or []
        except Exception:
            attrs = []
        try:
            weight = float(cmds.animLayer(name, query=True, weight=True) or 0.0)
        except Exception:
            weight = 1.0 if attrs else 0.0
        if attrs and weight > 1.0e-8:
            if model_root and layer_journal is not None:
                names = {
                    str(row.get("name"))
                    for row in (layer_journal.get("layers", []) or [])
                }
                if name in names:
                    continue
            raise MmdControlRigBuildError(
                f"active animLayer is unsupported for control-rig bake: {name}"
            )

    for binding in metadata.get("bindings", {}).values():
        for target in _expanded_authored_plugs(binding, cmds_module=cmds):
            incoming = cmds.listConnections(
                target, source=True, destination=False, plugs=True
            ) or []
            for source in incoming:
                node_type = str(cmds.nodeType(str(source).split(".", 1)[0]))
                if node_type.startswith("animBlendNode"):
                    if target in supported_routes:
                        continue
                    raise MmdControlRigBuildError(
                        f"animBlend input is unsupported for control-rig bake: {source} -> {target}"
                    )


def _parent_basis_is_animated_or_driven(cmds, parent: str) -> bool:
    """Return whether a parent's rotation/scale basis can vary over time.

    Reading the current value is insufficient: an animCurve or another DG
    driver may evaluate to identity at the current frame while changing the
    basis elsewhere.  Query both incoming connections and keyed times for the
    scalar channels (plus compound plugs for wrappers that only expose those
    connections).  Missing command features are treated as inconclusive so
    lightweight command doubles retain the static-value path.
    """

    for attribute in ("rotate", "scale"):
        if is_plug_animated_or_driven(
            f"{parent}.{attribute}",
            cmds_module=cmds,
        ):
            return True
        for axis in "XYZ":
            if is_plug_animated_or_driven(
                f"{parent}.{attribute}{axis}",
                cmds_module=cmds,
            ):
                return True
    return False


def _classify_route(cmds, binding: Mapping[str, Any], target: str):
    """Return ``(route class, reasons)`` for one authored input plug."""
    input_kind = str(binding.get("inputKind") or "")
    reasons = []
    if input_kind in {"ik_controller", "ik_link_input"}:
        reasons.extend(("ik", input_kind))
    elif input_kind in {"append_base", "bone_morph_base"}:
        reasons.append(input_kind)
    elif input_kind != "direct_channel":
        return ROUTE_UNSUPPORTED, [f"input_kind:{input_kind or 'missing'}"]

    joint = None
    try:
        joint = resolve_mmd_control_rig_binding_joint(cmds, binding)
    except MmdControlRigBuildError:
        joint = binding.get("joint")
    if joint and cmds.objExists(joint):
        try:
            orient = [
                float(cmds.getAttr(f"{joint}.jointOrient{axis}") or 0.0)
                for axis in "XYZ"
                if cmds.attributeQuery(f"jointOrient{axis}", node=joint, exists=True)
            ]
            if any(abs(value) > 1.0e-8 for value in orient):
                reasons.append("joint_orient")
        except Exception:
            reasons.append("joint_orient")
        try:
            rotate_order = int(cmds.getAttr(f"{joint}.rotateOrder"))
            if rotate_order != 0:  # Maya kXYZ
                reasons.append("rotate_order")
        except Exception:
            pass
        try:
            current = str(joint)
            visited_parents = set()
            while True:
                parents = cmds.listRelatives(current, parent=True, fullPath=True) or []
                if not parents:
                    break
                parent = str(parents[0])
                if parent in visited_parents:
                    break
                visited_parents.add(parent)
                rotation = [
                    float(cmds.getAttr(f"{parent}.rotate{axis}") or 0.0)
                    for axis in "XYZ"
                ]
                scale = []
                for axis in "XYZ":
                    raw_value = cmds.getAttr(f"{parent}.scale{axis}")
                    scale.append(1.0 if raw_value is None else float(raw_value))
                if (
                    any(abs(value) > 1.0e-8 for value in rotation)
                    or any(abs(value - 1.0) > 1.0e-8 for value in scale)
                    or _parent_basis_is_animated_or_driven(cmds, parent)
                ):
                    reasons.append("parent_basis")
                current = parent
        except Exception:
            pass
    return (ROUTE_SAMPLED if reasons else ROUTE_SAME_BASIS), tuple(sorted(set(reasons)))


def _route_diagnostics(journal: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for section in ("channels", "ikEnabled"):
        for row in journal.get(section, []) or []:
            rows.append(
                {
                    "target": row.get("target"),
                    "routeClass": row.get("routeClass", ROUTE_SAME_BASIS),
                    "reasons": list(row.get("routeReasons") or []),
                }
            )
    return rows


def _plug_reference(cmds, plug: str) -> Dict[str, str]:
    """Return a rename-stable UUID and attribute reference for one plug."""
    node, separator, attribute = str(plug).partition(".")
    if not separator or not attribute:
        raise MmdControlRigBuildError(f"invalid journal plug: {plug}")
    uuids = cmds.ls(node, uuid=True) or []
    if len(uuids) != 1:
        raise MmdControlRigBuildError(f"could not resolve journal plug node: {plug}")
    return {"nodeUuid": str(uuids[0]), "attribute": attribute}


def _journal_plug_row(
    cmds,
    *,
    source: Optional[str],
    control: str,
    target: str,
    value: Any,
    control_source: Optional[str] = None,
    route_class: str = ROUTE_SAME_BASIS,
    route_reasons=(),
    layer_route: Optional[Mapping[str, Any]] = None,
    authoring_basis: Optional[Mapping[str, Any]] = None,
    twist_controller: bool = False,
    fixed_axis_twist: bool = False,
) -> Dict[str, Any]:
    """Create a connection-journal row with readable and stable plug names."""
    return {
        "source": source,
        "sourceRef": _plug_reference(cmds, source) if source else None,
        "control": control,
        "controlRef": _plug_reference(cmds, control),
        "target": target,
        "targetRef": _plug_reference(cmds, target),
        "value": value,
        "controlSource": control_source,
        "controlSourceRef": (
            _plug_reference(cmds, control_source) if control_source else None
        ),
        "routeClass": str(route_class),
        "routeReasons": list(route_reasons or ()),
        "layerRoute": dict(layer_route) if layer_route else None,
        "authoringBasis": dict(authoring_basis) if authoring_basis else None,
        "twistController": bool(twist_controller),
        "fixedAxisTwist": bool(fixed_axis_twist),
    }


def _resolve_plug_reference(
    cmds,
    reference: Any,
    description: str,
) -> str:
    """Resolve a journal plug from its authoritative UUID reference."""
    if isinstance(reference, Mapping):
        node_uuid = reference.get("nodeUuid")
        attribute = reference.get("attribute")
        if node_uuid and attribute:
            nodes = cmds.ls(str(node_uuid), long=True) or []
            if len(nodes) != 1:
                raise MmdControlRigBuildError(
                    f"{description} node is missing: {node_uuid}"
                )
            plug = f"{nodes[0]}.{attribute}"
            if not cmds.objExists(plug):
                raise MmdControlRigBuildError(f"{description} plug is missing: {plug}")
            return str(plug)
    raise MmdControlRigBuildError(f"{description} UUID reference is missing")


def _resolve_journal_plug_row(cmds, row: Mapping[str, Any]) -> Dict[str, Any]:
    """Resolve one connection journal row without mutating persisted metadata."""
    resolved = dict(row)
    resolved["control"] = _resolve_plug_reference(
        cmds,
        row.get("controlRef"),
        "journal control",
    )
    resolved["target"] = _resolve_plug_reference(
        cmds,
        row.get("targetRef"),
        "journal target",
    )
    source = row.get("source")
    resolved["source"] = (
        _resolve_plug_reference(
            cmds,
            row.get("sourceRef"),
            "journal source",
        )
        if source
        else None
    )
    control_source = row.get("controlSource")
    resolved["controlSource"] = (
        _resolve_plug_reference(
            cmds,
            row.get("controlSourceRef"),
            "journal control curve source",
        )
        if control_source
        else None
    )
    baseline_output = row.get("translateBaselineOutput")
    resolved["translateBaselineOutput"] = (
        _resolve_plug_reference(
            cmds,
            row.get("translateBaselineOutputRef"),
            "translate baseline helper",
        )
        if baseline_output
        else None
    )
    baseline_target = row.get("translateBaselineTarget")
    resolved["translateBaselineTarget"] = (
        _resolve_plug_reference(
            cmds,
            row.get("translateBaselineTargetRef"),
            "translate baseline target",
        )
        if baseline_target
        else None
    )
    layer_route = row.get("layerRoute")
    if layer_route:
        try:
            resolved["layerRoute"] = resolve_mmd_control_rig_anim_layer_route(
                cmds,
                layer_route,
            )
        except MmdControlRigAnimLayerError as exc:
            raise MmdControlRigBuildError(str(exc)) from exc
    return resolved


def _resolve_journal_offset_row(cmds, row: Mapping[str, Any]) -> Dict[str, Any]:
    """Resolve one display-offset journal row by its control UUID."""
    resolved = dict(row)
    resolved["control"] = _resolve_plug_reference(
        cmds,
        row.get("controlRef"),
        "journal offset",
    )
    return resolved


def _prepare_ik_enabled_routes(
    cmds,
    control: str,
    solvers: Tuple[str, ...],
    *,
    curve_representations=None,
    layer_routes: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Validate IK ownership before touching any solver or controller edge.

    ``control.ikEnabled`` is the single EDIT writer for every solver in one
    binding.  Existing incoming animation is therefore accepted only when it
    is the exact controller curve recorded for the target (or the exact
    solver source that is about to be journaled).  All other graphs fail
    closed before a target connection is disconnected.
    """
    control_plug = f"{control}.ikEnabled"
    control_incoming = [
        str(source)
        for source in (
            cmds.listConnections(
                control_plug,
                source=True,
                destination=False,
                plugs=True,
            )
            or []
        )
    ]
    if len(control_incoming) > 1:
        raise MmdControlRigBuildError(
            f"multiple IK enabled controller sources: {control_plug}"
        )
    active_control_source = control_incoming[0] if control_incoming else None
    if active_control_source:
        _require_animation_source(cmds, active_control_source, control_plug)

    plans: List[Dict[str, Any]] = []
    represented_sources = set()
    solver_sources_without_representation = set()
    for solver in solvers:
        target = f"{solver}.enabled"
        if not cmds.objExists(target):
            raise MmdControlRigBuildError(f"IK solver enabled input is missing: {target}")
        incoming = [
            str(source)
            for source in (
                cmds.listConnections(
                    target,
                    source=True,
                    destination=False,
                    plugs=True,
                )
                or []
            )
        ]
        if len(incoming) > 1:
            raise MmdControlRigBuildError(f"multiple IK enabled sources: {target}")
        source = incoming[0] if incoming else None
        layer_route = (layer_routes or {}).get(target)
        if layer_route is not None:
            source = layer_route.get("curve")
            if incoming != [layer_route.get("blendOutput")]:
                raise MmdControlRigBuildError(
                    f"animLayer target has an unknown writer: {target}"
                )
            control_source = layer_route.get("curve")
        elif source:
            _require_animation_source(cmds, source, target)
        else:
            control_source = None
        if layer_route is None:
            control_source = (
            _existing_control_curve(cmds, curve_representations, target)
            if curve_representations is not None
            else None
            )

        if active_control_source:
            expected = control_source or source
            if expected != active_control_source:
                raise MmdControlRigBuildError(
                    f"foreign IK enabled controller source: {active_control_source} -> "
                    f"{control_plug} (expected {expected or 'none'})"
                )
            # A shared source is already the authoritative controller writer;
            # journal it instead of duplicating a second curve.
            control_source = active_control_source
        elif control_source:
            represented_sources.add(control_source)
        elif source:
            solver_sources_without_representation.add(source)

        plans.append(
            {
                "solver": solver,
                "target": target,
                "source": source,
                "controlSource": control_source,
                "layerRoute": layer_route,
                "value": bool(cmds.getAttr(target)),
            }
        )

    if not active_control_source:
        if len(represented_sources) > 1:
            raise MmdControlRigBuildError(
                f"IK solvers have different enabled animation sources: {control}"
            )
        if len(solver_sources_without_representation) > 1:
            raise MmdControlRigBuildError(
                f"IK solvers have different enabled animation sources: {control}"
            )
        if represented_sources and solver_sources_without_representation:
            represented = next(iter(represented_sources))
            if represented not in solver_sources_without_representation:
                raise MmdControlRigBuildError(
                    f"IK solvers have different enabled animation sources: {control}"
                )
    return plans, active_control_source


def _connect_ik_enabled(
    cmds,
    control,
    binding,
    journal,
    operations,
    *,
    created_curve_nodes=None,
    curve_representations=None,
    layer_routes: Optional[Mapping[str, Mapping[str, Any]]] = None,
    visibility_controls=None,
    hidden_when_enabled_controls=None,
) -> None:
    solvers = resolve_mmd_control_rig_binding_ik_solvers(cmds, binding)
    if not solvers:
        return
    if not cmds.attributeQuery("ikEnabled", node=control, exists=True):
        cmds.addAttr(control, longName="ikEnabled", attributeType="bool", keyable=True)
    control_plug = f"{control}.ikEnabled"
    plans, active_control_source = _prepare_ik_enabled_routes(
        cmds,
        control,
        solvers,
        curve_representations=curve_representations,
        layer_routes=layer_routes,
    )
    for plan in plans:
        target = plan["target"]
        source = plan["source"]
        control_source = plan["controlSource"]
        layer_route = plan.get("layerRoute")
        value = plan["value"]
        if layer_route is not None:
            apply_mmd_control_rig_anim_layer_route(
                cmds,
                layer_route,
                control_plug,
                operations,
            )
            journal["ikEnabled"].append(
                _journal_plug_row(
                    cmds,
                    source=source,
                    control=control_plug,
                    target=target,
                    value=value,
                    control_source=control_source,
                    route_class=ROUTE_SAMPLED,
                    route_reasons=("ik", "anim_layer"),
                    layer_route=layer_route,
                )
            )
            if curve_representations is not None:
                _record_curve_representation(
                    curve_representations,
                    target,
                    source,
                    control_source,
                    cmds,
                )
            continue
        if source:
            if control_source is None:
                if active_control_source is None:
                    control_source = _duplicate_animation_source(
                        cmds,
                        source,
                        created_curve_nodes if created_curve_nodes is not None else [],
                    )
                    active_control_source = control_source
                else:
                    control_source = active_control_source
            elif active_control_source is None:
                active_control_source = control_source
            elif active_control_source != control_source:
                raise MmdControlRigBuildError(
                    f"IK solvers have different enabled animation sources: {control}"
                )
            if not cmds.isConnected(control_source, control_plug):
                cmds.connectAttr(control_source, control_plug, force=False)
                operations.append(("disconnect", control_source, control_plug))
            cmds.disconnectAttr(source, target)
            operations.append(("connect", source, target))
        elif control_source:
            if active_control_source is None:
                active_control_source = control_source
            elif active_control_source != control_source:
                raise MmdControlRigBuildError(
                    f"IK solvers have different enabled animation sources: {control}"
                )
            if not cmds.isConnected(control_source, control_plug):
                cmds.connectAttr(control_source, control_plug, force=False)
                operations.append(("disconnect", control_source, control_plug))
        elif active_control_source is None:
            cmds.setAttr(control_plug, value)
        cmds.connectAttr(control_plug, target, force=False)
        operations.append(("disconnect", control_plug, target))
        journal["ikEnabled"].append(
            _journal_plug_row(
                cmds,
                source=source,
                control=control_plug,
                target=target,
                value=value,
                control_source=control_source,
                route_class=ROUTE_SAMPLED,
                route_reasons=("ik",),
            )
        )
        if curve_representations is not None:
            _record_curve_representation(
                curve_representations, target, source, control_source, cmds
            )

    # Keep visible IK shapes synchronized with the EDIT owner's single-writer
    # state. The custom solver's enabled input is not a readable Maya source
    # attribute, so visibility must use the controller-side ikEnabled plug.
    if visibility_controls is not None:
        _connect_ik_control_visibility(
            cmds,
            visibility_controls or (control,),
            control_plug,
            operations,
        )
    if hidden_when_enabled_controls:
        inverters = _connect_ik_control_visibility(
            cmds,
            hidden_when_enabled_controls,
            control_plug,
            operations,
            inverted=True,
            created_nodes=created_curve_nodes,
        )
        journal.setdefault("ikVisibilityInverters", []).extend(inverters)


def _connect_ik_control_visibility(
    cmds,
    controls,
    control_plug: str,
    operations: List[Tuple[str, str, str]],
    *,
    inverted: bool = False,
    created_nodes=None,
) -> List[Dict[str, str]]:
    """Drive curve shapes from IK state, optionally with inverse visibility."""

    inverter = None
    inverter_records = []

    for control in controls:
        shapes = cmds.listRelatives(
            control,
            shapes=True,
            fullPath=True,
        ) or []
        for shape in shapes:
            target = f"{shape}.visibility"
            if not cmds.objExists(target):
                continue
            incoming = [
                str(source)
                for source in (
                    cmds.listConnections(
                        target,
                        source=True,
                        destination=False,
                        plugs=True,
                    )
                    or []
                )
            ]
            expected_source = control_plug
            if inverted and incoming:
                source_node = incoming[0].split(".", 1)[0]
                if str(cmds.nodeType(source_node)) == "reverse":
                    inverter_inputs = cmds.listConnections(
                        f"{source_node}.inputX",
                        source=True,
                        destination=False,
                        plugs=True,
                    ) or []
                    if len(inverter_inputs) == 1 and _plugs_match(
                        cmds, str(inverter_inputs[0]), control_plug
                    ):
                        inverter = source_node
                        expected_source = f"{inverter}.outputX"
            if incoming and not any(
                _plugs_match(cmds, source, expected_source) for source in incoming
            ):
                raise MmdControlRigBuildError(
                    f"foreign IK control visibility source: {control_plug} -> {target}"
                )
            if incoming and any(
                _plugs_match(cmds, source, expected_source) for source in incoming
            ):
                continue
            if inverted:
                if inverter is None:
                    name = f"{str(control).rsplit('|', 1)[-1]}_IK_VISIBILITY_REVERSE"
                    inverter = str(cmds.createNode("reverse", name=name))
                    if created_nodes is not None:
                        created_nodes.append(inverter)
                    cmds.connectAttr(control_plug, f"{inverter}.inputX", force=False)
                    operations.append(("disconnect", control_plug, f"{inverter}.inputX"))
                expected_source = f"{inverter}.outputX"
            cmds.connectAttr(expected_source, target, force=False)
            operations.append(("disconnect", expected_source, target))
        if inverted and inverter is not None:
            uuid = str((cmds.ls(inverter, uuid=True) or [""])[0])
            if uuid and not any(row.get("uuid") == uuid for row in inverter_records):
                inverter_records.append({"uuid": uuid, "name": inverter})
    return inverter_records


def _plugs_match(cmds, left: str, right: str) -> bool:
    """Compare plugs after Maya short/long DAG-name normalization."""

    left_node, left_sep, left_attr = str(left).partition(".")
    right_node, right_sep, right_attr = str(right).partition(".")
    if not left_sep or not right_sep or left_attr != right_attr:
        return str(left) == str(right)
    left_nodes = cmds.ls(left_node, long=True) or []
    right_nodes = cmds.ls(right_node, long=True) or []
    if len(left_nodes) == 1 and len(right_nodes) == 1:
        return str(left_nodes[0]) == str(right_nodes[0])
    return str(left) == str(right)


def _zero_control_display_offsets(
    cmds,
    controls: List[str],
    journal: Dict[str, Any],
    *,
    reference_time: Optional[float] = None,
) -> None:
    """Cancel authored local values for unique controls in one time-sampling batch."""
    plugs = []
    for control in controls:
        plug = f"{control}.offsetParentMatrix"
        if not cmds.objExists(plug):
            continue
        incoming = cmds.listConnections(plug, source=True, destination=False, plugs=True) or []
        if incoming:
            raise MmdControlRigBuildError(f"control offsetParentMatrix is already driven: {control}")
        plugs.append((control, plug))
    if not plugs:
        return
    restore_time = None
    try:
        restore_time = float(cmds.currentTime(query=True))
        if reference_time is not None:
            cmds.currentTime(reference_time, edit=True)
        for control, plug in plugs:
            previous = list(cmds.getAttr(plug))
            local = om.MMatrix(cmds.xform(control, query=True, objectSpace=True, matrix=True))
            inverse = list(local.inverse())
            cmds.setAttr(plug, *inverse, type="matrix")
            journal["offsetParentMatrix"].append(
                {
                    "control": plug,
                    "controlRef": _plug_reference(cmds, plug),
                    "value": previous,
                }
            )
    finally:
        if restore_time is not None:
            cmds.currentTime(restore_time, edit=True)


def _target_value_at_time(cmds, target: str, reference_time: float) -> float:
    """Read one authored target at the display reference without changing time."""

    try:
        return float(cmds.getAttr(target, time=float(reference_time)))
    except (TypeError, RuntimeError):
        # Lightweight command doubles and a few Maya compound plugs do not
        # accept the optional time keyword.  The caller already owns the
        # reference-time transaction in those environments.
        return float(cmds.getAttr(target))


def _connect_translate_baseline(
    cmds,
    control_plug: str,
    target: str,
    baseline: float,
    operations: List[Tuple[str, str, str]],
) -> str:
    """Route a delta-valued control channel through an owned additive helper."""

    node = None
    try:
        node = str(
            cmds.createNode(
                "plusMinusAverage",
                name=f"{control_plug.split('.', 1)[0].rsplit('|', 1)[-1]}_TRANSLATE_BASELINE",
            )
        )
        cmds.setAttr(f"{node}.operation", 1)
        cmds.setAttr(f"{node}.input1D[1]", float(baseline))
        input_plug = f"{node}.input1D[0]"
        output_plug = f"{node}.output1D"
        cmds.connectAttr(control_plug, input_plug, force=False)
        operations.append(("disconnect", control_plug, input_plug))
        cmds.connectAttr(output_plug, target, force=False)
        operations.append(("disconnect", output_plug, target))
        return node
    except Exception:
        if node and cmds.objExists(node):
            try:
                cmds.delete(node)
            except Exception:
                pass
        raise


def _connect_layer_translate_baseline(
    cmds,
    layer_route: Mapping[str, Any],
    control_plug: str,
    baseline: float,
    operations: List[Tuple[str, str, str]],
    created_curve_nodes: List[str],
    existing_control_source: Optional[str] = None,
) -> Tuple[str, str, str]:
    """Route a duplicated layer curve as a controller delta.

    The animLayer curve remains the authoritative absolute source.  Only its
    owned duplicate is shifted, while the additive helper restores the
    absolute value at the animLayer ``inputB`` destination.
    """

    route = resolve_mmd_control_rig_anim_layer_route(cmds, layer_route)
    curve = route.get("curve")
    blend = route.get("blend")
    if not curve or not blend:
        raise MmdControlRigBuildError(
            f"translate animation-layer route is missing curve/blend: {control_plug}"
        )
    incoming_control = [
        str(value)
        for value in (
            cmds.listConnections(
                control_plug,
                source=True,
                destination=False,
                plugs=True,
            )
            or []
        )
    ]
    if incoming_control:
        raise MmdControlRigBuildError(
            f"foreign animation-layer controller source: {control_plug}"
        )
    incoming_blend = [
        str(value)
        for value in (
            cmds.listConnections(
                blend,
                source=True,
                destination=False,
                plugs=True,
            )
            or []
        )
    ]
    if incoming_blend != [curve]:
        raise MmdControlRigBuildError(
            f"foreign animation-layer blend source: {blend}"
        )
    control_source = existing_control_source
    if control_source is None:
        control_source = _duplicate_animation_source(cmds, curve, created_curve_nodes)
        _offset_animation_curve_values(cmds, control_source, -float(baseline))
    elif str(control_source).split(".", 1)[0] == str(curve).split(".", 1)[0]:
        raise MmdControlRigBuildError(
            f"animation-layer controller source aliases original curve: {curve}"
        )
    cmds.disconnectAttr(curve, blend)
    operations.append(("connect", curve, blend))
    cmds.connectAttr(control_source, control_plug, force=False)
    operations.append(("disconnect", control_source, control_plug))
    baseline_node = _connect_translate_baseline(
        cmds,
        control_plug,
        blend,
        baseline,
        operations,
    )
    created_curve_nodes.append(baseline_node)
    return str(control_source), str(baseline_node), str(blend)


def _offset_animation_curve_values(cmds, source: str, offset: float) -> None:
    """Shift a newly-owned curve while preserving key and tangent payload."""

    node = str(source).split(".", 1)[0]
    if not str(cmds.nodeType(node)).startswith("animCurve"):
        raise MmdControlRigBuildError(f"translate control source is not an animCurve: {source}")
    payload = _capture_animation_curve_payload(cmds, node)
    if payload.get("captureFailed"):
        raise MmdControlRigBuildError(
            f"could not capture translate control curve payload: {source}"
        )
    keys = payload.get("keys") or ()
    if not keys:
        return
    _clear_animation_curve_keys(cmds, node)
    for key in keys:
        value = key.get("value")
        if value is None:
            continue
        cmds.setKeyframe(
            node,
            time=float(key.get("time", 0.0)),
            value=float(value) + float(offset),
        )
    _restore_animation_curve_payload(cmds, node, payload)


def _translate_baseline(row: Mapping[str, Any]) -> float:
    """Return a persisted translate baseline, defaulting to legacy zero."""

    value = row.get("translateBaseline")
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) else 0.0


def _translate_baseline_output(row: Mapping[str, Any]) -> Optional[str]:
    """Resolve the helper output recorded on a translate journal row."""

    output = row.get("translateBaselineOutput")
    return str(output) if output else None


def _translate_reference_time(row: Mapping[str, Any]) -> Optional[float]:
    """Return the persisted display reference used to center a delta curve."""

    value = row.get("translateReferenceTime")
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _recenter_translate_control_source(
    cmds,
    row: Mapping[str, Any],
    control_source: Optional[str],
) -> None:
    """Shift an owned translate curve so its display-reference value is zero."""

    if not row.get("translateBaselineOutput") or not control_source:
        return
    reference_time = _translate_reference_time(row)
    if reference_time is None:
        return
    node = str(control_source).split(".", 1)[0]
    if not str(cmds.nodeType(node)).startswith("animCurve"):
        return
    try:
        value = float(cmds.getAttr(control_source, time=reference_time))
    except (TypeError, RuntimeError):
        value = float(cmds.getAttr(control_source))
    if not math.isfinite(value) or abs(value) <= 1.0e-12:
        return
    _offset_animation_curve_values(cmds, control_source, -value)


def _disconnect_translate_baseline(cmds, row: Mapping[str, Any]) -> None:
    """Disconnect one owned additive writer before restoring/baking a target."""

    output = _translate_baseline_output(row)
    target = row.get("translateBaselineTarget") or row.get("target")
    if output and target:
        destinations = [
            _canonical_plug(cmds, str(value))
            for value in (
                cmds.listConnections(
                    output,
                    source=False,
                    destination=True,
                    plugs=True,
                )
                or []
            )
        ]
        if destinations != [_canonical_plug(cmds, str(target))]:
            raise MmdControlRigBuildError(
                f"foreign translate baseline fan-out: {output}"
            )
        incoming = [
            _canonical_plug(cmds, str(value))
            for value in (
                cmds.listConnections(
                    target,
                    source=True,
                    destination=False,
                    plugs=True,
                )
                or []
            )
        ]
        if incoming != [_canonical_plug(cmds, str(output))]:
            raise MmdControlRigBuildError(
                f"foreign translate baseline writer: {target}"
            )
        cmds.disconnectAttr(output, target)


def _disconnect_layer_control_source(cmds, row: Mapping[str, Any]) -> None:
    """Disconnect the owned duplicate layer curve, rejecting foreign writers."""

    control = row.get("control")
    expected = row.get("controlSource")
    if not control or not expected:
        raise MmdControlRigBuildError(
            f"translate animation-layer control source is missing: {control}"
        )
    incoming = [
        str(value)
        for value in (
            cmds.listConnections(
                control,
                source=True,
                destination=False,
                plugs=True,
            )
            or []
        )
    ]
    if incoming != [expected]:
        raise MmdControlRigBuildError(
            f"foreign animation-layer controller source: {control}"
        )
    if cmds.isConnected(expected, control):
        cmds.disconnectAttr(expected, control)


def _translate_baseline_nodes(rows) -> Tuple[str, ...]:
    nodes = []
    for row in rows or ():
        output = _translate_baseline_output(row)
        if output:
            node = str(output).split(".", 1)[0]
            if node not in nodes:
                nodes.append(node)
    return tuple(nodes)


def _remove_translate_baseline_nodes(cmds, rows) -> None:
    for node in _translate_baseline_nodes(rows):
        if cmds.objExists(node):
            cmds.delete(node)


def _curve_representations(metadata: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Return persisted two-owner curve rows without exposing metadata state."""
    rows = metadata.get("curveRepresentations", [])
    deduped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        target_ref = row.get("targetRef")
        if isinstance(target_ref, Mapping) and target_ref.get("nodeUuid") and target_ref.get("attribute"):
            key = (str(target_ref["nodeUuid"]), str(target_ref["attribute"]))
        else:
            key = (str(row.get("target") or ""), "")
        previous = deduped.get(key)
        if previous is None or _curve_representation_score(row) >= _curve_representation_score(previous):
            deduped[key] = row
    return list(deduped.values())


def _curve_representation_score(row: Mapping[str, Any]) -> int:
    """Prefer UUID-resolvable rows when legacy duplicate entries coexist."""
    return int(bool(row.get("targetRef"))) + int(bool(row.get("controlRef"))) + int(bool(row.get("mmdRef")))


def _curve_representation_target_matches(cmds, row: Mapping[str, Any], target: str) -> bool:
    """Compare curve rows by UUID-backed target refs before display names."""
    target_ref = row.get("targetRef")
    if isinstance(target_ref, Mapping) and target_ref.get("nodeUuid") and target_ref.get("attribute"):
        try:
            return _resolve_plug_reference(cmds, target_ref, "curve target") == target
        except MmdControlRigBuildError:
            return False
    return str(row.get("target") or "") == str(target)


def _existing_control_curve(cmds, rows, target: str) -> Optional[str]:
    """Resolve a detached CONTROL curve for a target by UUID authority."""
    if not rows:
        return None
    for row in rows:
        if not _curve_representation_target_matches(cmds, row, target):
            continue
        ref = row.get("controlRef")
        if not ref:
            continue
        try:
            return _resolve_plug_reference(cmds, ref, "control curve")
        except MmdControlRigBuildError:
            continue
    return None


def _record_curve_representation(
    rows,
    target,
    mmd_source,
    control_source,
    cmds,
    *,
    quaternion_interpolation: Optional[bool] = None,
):
    """Persist both MMD and controller curve UUIDs for one authored channel."""
    existing = next(
        (row for row in rows if _curve_representation_target_matches(cmds, row, target)),
        None,
    )
    payload = {
        "target": target,
        "targetRef": _plug_reference(cmds, target),
        "mmd": mmd_source,
        "mmdRef": _plug_reference(cmds, mmd_source) if mmd_source else None,
        "control": control_source,
        "controlRef": _plug_reference(cmds, control_source) if control_source else None,
    }
    if quaternion_interpolation is not None:
        payload["quaternionInterpolation"] = bool(quaternion_interpolation)
    if existing is None:
        rows.append(payload)
    else:
        existing.update(payload)


def _capture_curve_snapshots(cmds, plugs):
    snapshots = []
    created = []
    for plug in sorted(set(str(value) for value in plugs if value)):
        node = plug.split(".", 1)[0]
        if not cmds.objExists(node) or not str(cmds.nodeType(node)).startswith("animCurve"):
            continue
        try:
            backup = _duplicate_animation_source(cmds, plug, created)
        except MmdControlRigBuildError:
            continue
        if backup != plug:
            snapshots.append((plug, backup))
    return snapshots


def _capture_curve_input_states(cmds, plugs):
    """Capture animCurve time inputs that a compound bake may rewire."""
    states = {}
    for plug in sorted(set(str(value) for value in plugs if value)):
        node = plug.split(".", 1)[0]
        input_plug = f"{node}.input"
        if not cmds.objExists(input_plug) or not str(cmds.nodeType(node)).startswith(
            "animCurve"
        ):
            continue
        states[input_plug] = [
            str(source)
            for source in (
                cmds.listConnections(
                    input_plug,
                    source=True,
                    destination=False,
                    plugs=True,
                )
                or []
            )
        ]
    return states


def _restore_curve_input_states(cmds, states) -> None:
    """Restore exact animCurve time inputs after a failed compound bake."""
    for input_plug, prior_sources in states.items():
        current_sources = cmds.listConnections(
            input_plug,
            source=True,
            destination=False,
            plugs=True,
        ) or []
        for source in current_sources:
            if str(source) not in prior_sources:
                cmds.disconnectAttr(source, input_plug)
        for source in prior_sources:
            if not cmds.isConnected(source, input_plug):
                cmds.connectAttr(source, input_plug, force=True)


def _restore_curve_snapshots(cmds, snapshots):
    for original, backup in snapshots:
        # A snapshot restore is part of the transaction's rollback contract.
        # Let copy failures reach ``_edit_exit_transaction`` so callers cannot
        # report a successful rollback while the original curve is incomplete.
        # Avoid an unnecessary rewrite when a curve was not touched.  Maya can
        # recompute quaternion/animLayer outputs by a few ulps when an
        # otherwise identical curve is pasted back, which breaks exact graph
        # rollback assertions and is not a meaningful restore.
        try:
            current = _capture_animation_curve_payload(cmds, original.split(".", 1)[0])
            saved = _capture_animation_curve_payload(cmds, backup.split(".", 1)[0])
            if (
                current.get("times") == saved.get("times")
                and current.get("values") == saved.get("values")
                and current.get("keys") == saved.get("keys")
                and current.get("weightedTangents") == saved.get("weightedTangents")
                and current.get("preInfinite") == saved.get("preInfinite")
                and current.get("postInfinite") == saved.get("postInfinite")
            ):
                continue
        except Exception:
            pass
        _copy_animation_curve(cmds, backup, original)


def _discard_curve_snapshots(cmds, snapshots):
    for _original, backup in snapshots:
        node = backup.split(".", 1)[0]
        if cmds.objExists(node):
            try:
                cmds.delete(node)
            except Exception:
                pass


def _duplicate_animation_source(cmds, source: str, created_nodes: List[str]) -> str:
    """Clone one authored animation node, retaining key/tangent data and UUID."""
    node, _, attribute = source.partition(".")
    if not node or not attribute:
        raise MmdControlRigBuildError(f"invalid animation source: {source}")
    _require_animation_source(cmds, source, source)
    try:
        duplicates = cmds.duplicate(node, upstreamNodes=True, returnRootsOnly=True)
    except Exception:
        duplicates = []
    if duplicates:
        duplicate = str(duplicates[0])
    elif str(cmds.nodeType(node)).startswith("animCurve"):
        # Dependency-only animCurve nodes are not duplicated by Maya's DAG
        # duplicate command. Create a same-typed node and copy its keys.
        try:
            duplicate = str(cmds.createNode(cmds.nodeType(node)))
            _copy_animation_curve(cmds, f"{node}.{attribute}", f"{duplicate}.{attribute}")
        except Exception as exc:
            raise MmdControlRigBuildError(
                f"could not duplicate animation source: {source}: {exc}"
            ) from exc
    elif str(cmds.nodeType(node)).startswith("animBlendNode"):
        # Blend nodes are generated by Maya's legacy VMD route and cannot be
        # cloned with a stable cross-version attribute schema. Fail closed
        # instead of claiming one UUID is two independent owner curves.
        raise MmdControlRigBuildError(
            f"independent control curve is unsupported for blend source: {source}"
        )
    else:
        raise MmdControlRigBuildError(f"could not duplicate animation source: {source}")
    created_nodes.append(duplicate)
    plug = f"{duplicate}.{attribute}"
    if not cmds.objExists(plug):
        raise MmdControlRigBuildError(f"duplicated animation source plug is missing: {plug}")
    return plug


def _supports_live_authoring_basis(row: Mapping[str, Any]) -> bool:
    """Allow complete transform-like XYZ routes while excluding special writers."""
    if row.get("routeClass", ROUTE_SAME_BASIS) == ROUTE_SAME_BASIS:
        return True
    target_attr = str(row.get("target", "")).rsplit(".", 1)[-1]
    reasons = set(row.get("routeReasons") or ())
    standard_rotate = target_attr in {"rotateX", "rotateY", "rotateZ"}
    bone_morph_base = (
        "bone_morph_base" in reasons
        and target_attr.startswith("baseRotate")
        and target_attr.endswith(("X", "Y", "Z"))
    )
    ik_link_input = (
        "ik_link_input" in reasons
        and target_attr.startswith("inputRotateElement")
        and target_attr.endswith(("X", "Y", "Z"))
    )
    if "bone_morph_base" in reasons and not bone_morph_base:
        return False
    if "ik_link_input" in reasons and not ik_link_input:
        return False
    if not standard_rotate and not bone_morph_base and not ik_link_input:
        return False
    if row.get("layerRoute") is not None:
        return False
    special_reasons = {
        "anim_layer",
        "append_base",
        "ik_controller",
        "rotate_order",
    }
    if "ik" in reasons and not ik_link_input:
        return False
    return not bool(reasons.intersection(special_reasons))


def _supports_bake_authoring_basis(row: Mapping[str, Any]) -> bool:
    """Allow joint or bone-morph-base XYZ basis sampling."""
    target_attr = str(row.get("target", "")).rsplit(".", 1)[-1]
    reasons = set(row.get("routeReasons") or ())
    standard_rotate = target_attr in {"rotateX", "rotateY", "rotateZ"}
    bone_morph_base = (
        "bone_morph_base" in reasons
        and target_attr.startswith("baseRotate")
        and target_attr.endswith(("X", "Y", "Z"))
    )
    ik_link_input = (
        "ik_link_input" in reasons
        and target_attr.startswith("inputRotateElement")
        and target_attr.endswith(("X", "Y", "Z"))
    )
    if "bone_morph_base" in reasons and not bone_morph_base:
        return False
    if "ik_link_input" in reasons and not ik_link_input:
        return False
    if not standard_rotate and not bone_morph_base and not ik_link_input:
        return False
    if row.get("layerRoute") is not None:
        return False
    special_reasons = {
        "anim_layer",
        "append_base",
        "ik_controller",
    }
    if "ik" in reasons and not ik_link_input:
        return False
    return not bool(reasons.intersection(special_reasons))


def _consistent_rotation_group_basis(
    rows: List[Mapping[str, Any]],
) -> MmdControlRigBasis:
    """Return one canonical basis shared by a complete XYZ route.

    Persisted quaternion components can acquire insignificant normalization
    differences after JSON round-trips.  Compare validated canonical records
    instead of raw floats while still rejecting genuinely mixed XYZ bases.
    """

    bases = [validate_basis_record(row.get("authoringBasis")) for row in rows]
    if not bases:
        raise MmdControlRigBasisError("rotation XYZ basis is missing")
    basis = bases[0]
    if any(
        candidate.source != basis.source
        or any(
            abs(actual - expected) > 1.0e-9
            for actual, expected in zip(candidate.quaternion, basis.quaternion)
        )
        for candidate in bases[1:]
    ):
        raise MmdControlRigBuildError("rotation XYZ basis is inconsistent")
    return basis


def _create_live_rotation_converters(
    cmds,
    rows: List[Mapping[str, Any]],
    authoring_bases: Mapping[str, Any],
    operations: List[Tuple[str, str, str]],
    created_nodes: List[str],
) -> List[Dict[str, Any]]:
    """Create matrix-DG conjugation for complete direct XYZ authoring groups.

    Maya 2024 does not expose a quaternion DG node.  A compose/mult/decompose
    chain implements ``B * q_control * inverse(B)`` while retaining ordinary
    rotate channels as the artist-facing inputs. Complete direct XYZ groups,
    bone-morph base rotations, and optional twist-controller Append XYZ inputs
    are eligible; IK, partial, and non-twist append routes remain fail-closed.
    """

    converters: List[Dict[str, Any]] = []
    non_identity_rows = []
    for row in rows:
        target_attr = str(row.get("target", "")).rsplit(".", 1)[-1]
        bone_morph_base = (
            "bone_morph_base" in set(row.get("routeReasons") or ())
            and target_attr.startswith("baseRotate")
            and target_attr.endswith(("X", "Y", "Z"))
        )
        ik_link_input = (
            "ik_link_input" in set(row.get("routeReasons") or ())
            and target_attr.startswith("inputRotateElement")
            and target_attr.endswith(("X", "Y", "Z"))
        )
        if (
            target_attr not in {"rotateX", "rotateY", "rotateZ"}
            and not bone_morph_base
            and not ik_link_input
            and not (
                row.get("twistController")
                and (
                    target_attr.startswith("baseRotate")
                    or target_attr.startswith("inputRotateElement")
                )
                and target_attr.endswith(("X", "Y", "Z"))
            )
        ):
            continue
        record = row.get("authoringBasis")
        if not record:
            continue
        try:
            basis = validate_basis_record(record)
        except MmdControlRigBasisError as exc:
            raise MmdControlRigBuildError("invalid control-rig authoring basis in EDIT journal") from exc
        if basis.quaternion != (0.0, 0.0, 0.0, 1.0):
            # Sampled/IK/Append routes are converted by their own route
            # handlers.  They must not be rejected here merely because the
            # source joint carries a non-identity authoring basis; this live
            # Euler conjugation chain is only responsible for direct XYZ
            # channels that share the control basis.
            if not _supports_live_authoring_basis(row):
                continue
            non_identity_rows.append(row)
            reasons = set(row.get("routeReasons") or ())
            if "ik_controller" in reasons or (
                "ik" in reasons and "ik_link_input" not in reasons
            ):
                raise MmdControlRigBuildError(
                    "non-identity authoring basis is unsupported for IK/append route"
                )
            if "append_base" in reasons and not row.get("twistController"):
                raise MmdControlRigBuildError(
                    "non-identity authoring basis is unsupported for non-twist Append route"
                )
    live_groups = _rotation_channel_groups(rows, include_sampled_direct=True)
    grouped_rows = {id(row) for group in live_groups for row in group}
    if any(
        id(row) not in grouped_rows
        and row.get("routeClass", ROUTE_SAME_BASIS) == ROUTE_SAME_BASIS
        for row in non_identity_rows
    ):
        raise MmdControlRigBuildError(
            "non-identity authoring basis requires a complete XYZ rotation route"
        )
    for group in live_groups:
        # Special sampled writers (animLayer, IK, Append) carry their own route
        # conversion. Direct joint XYZ routes still need live conjugation for
        # artist edits even when JO/parent basis made curve transfer sampled.
        if any(not _supports_live_authoring_basis(row) for row in group):
            continue
        if not group[0].get("authoringBasis"):
            continue
        try:
            basis = _consistent_rotation_group_basis(group)
        except MmdControlRigBasisError as exc:
            raise MmdControlRigBuildError("invalid control-rig authoring basis in EDIT journal") from exc
        if basis.quaternion == (0.0, 0.0, 0.0, 1.0):
            continue
        control_node = str(group[0]["control"]).rsplit(".", 1)[0]
        # IK-link targets are nested compound plugs such as
        # ``solver.inputRotate[6].inputRotateElementX``.  Persist the owning
        # dependency-node UUID, not the intermediate compound path.
        target_node = str(group[0]["target"]).split(".", 1)[0]
        namespace = target_node.rsplit("|", 1)[-1].replace(":", "_")
        try:
            compose = str(cmds.createNode("composeMatrix", name=f"{namespace}_CR_BASIS_COMPOSE"))
            mult = str(cmds.createNode("multMatrix", name=f"{namespace}_CR_BASIS_MULT"))
            decompose = str(cmds.createNode("decomposeMatrix", name=f"{namespace}_CR_BASIS_DECOMPOSE"))
            created_nodes.extend((compose, mult, decompose))
            matrix = matrix_from_quaternion(basis.quaternion)
            inverse_matrix = matrix_from_quaternion(
                (-basis.quaternion[0], -basis.quaternion[1], -basis.quaternion[2], basis.quaternion[3])
            )
            # Maya composes row-vector matrices. The quaternion contract
            # q_bone = B * q_control * inverse(B) therefore uses the reversed
            # matrix order inverse(B) * Q * B.
            cmds.setAttr(f"{mult}.matrixIn[0]", *inverse_matrix, type="matrix")
            cmds.setAttr(f"{mult}.matrixIn[2]", *matrix, type="matrix")
            for axis in "XYZ":
                cmds.connectAttr(
                    f"{control_node}.rotate{axis}",
                    f"{compose}.inputRotate{axis}",
                    force=False,
                )
            cmds.connectAttr(f"{compose}.outputMatrix", f"{mult}.matrixIn[1]", force=False)
            cmds.connectAttr(f"{mult}.matrixSum", f"{decompose}.inputMatrix", force=False)
            try:
                rotate_order = int(cmds.getAttr(f"{target_node}.rotateOrder"))
                cmds.setAttr(f"{decompose}.inputRotateOrder", rotate_order)
            except Exception:
                pass
            for row in group:
                axis = str(row["target"]).rsplit(".", 1)[-1][-1]
                control = str(row["control"])
                target = str(row["target"])
                for incoming in cmds.listConnections(
                    target, source=True, destination=False, plugs=True
                ) or []:
                    if not _plug_reaches_control(cmds, str(incoming), control):
                        continue
                    cmds.disconnectAttr(incoming, target)
                    operations.append(("connect", str(incoming), target))
                output = f"{decompose}.outputRotate{axis}"
                cmds.connectAttr(output, target, force=False)
                operations.append(("disconnect", output, target))
            converters.append(
                {
                    "basis": basis.to_dict(),
                    "controlNodeUuid": str((cmds.ls(control_node, uuid=True) or [""])[0]),
                    "targetNodeUuid": str((cmds.ls(target_node, uuid=True) or [""])[0]),
                    "composeUuid": str((cmds.ls(compose, uuid=True) or [""])[0]),
                    "multUuid": str((cmds.ls(mult, uuid=True) or [""])[0]),
                    "decomposeUuid": str((cmds.ls(decompose, uuid=True) or [""])[0]),
                    "control": control_node,
                    "target": target_node,
                }
            )
        except Exception as exc:
            raise MmdControlRigBuildError(
                f"could not create live authoring-basis converter for {target_node}: {exc}"
            ) from exc
    return converters


def _rebase_new_ik_link_control_curves(
    cmds,
    rows: List[Mapping[str, Any]],
    created_curve_nodes: List[str],
    curve_representations: List[Dict[str, Any]],
) -> None:
    """Convert first-entry IK-link curves from solver XYZ into control basis.

    Legacy VMD import owns ``mmdCcdIk.inputRotate`` in solver-local space.
    Entering EDIT duplicates those curves before installing the reciprocal live
    converter.  Only freshly duplicated curves are rebased here; UUID-backed
    control curves from an earlier EDIT/BAKE cycle are already in control space.
    """

    created = {_canonical_node(cmds, node) for node in created_curve_nodes}
    for group in _rotation_channel_groups(rows, include_sampled_direct=True):
        if not all(
            "ik_link_input" in set(row.get("routeReasons") or ())
            for row in group
        ):
            continue
        if not group[0].get("authoringBasis"):
            # Legacy metadata without a persisted display basis retains the
            # historical direct XYZ route.
            continue
        basis = _consistent_rotation_group_basis(group)
        if basis.quaternion == (0.0, 0.0, 0.0, 1.0):
            continue
        attrs = ("rotateX", "rotateY", "rotateZ")
        rows_by_attr = {
            _rotation_attr_for_target(row["target"]): row for row in group
        }
        control_node = str(rows_by_attr["rotateX"]["control"]).rsplit(".", 1)[0]
        target_node = str(rows_by_attr["rotateX"]["target"]).split(".", 1)[0]
        control_sources = [
            rows_by_attr[attr].get("controlSource") for attr in attrs
        ]
        if not any(control_sources):
            raw_values = tuple(
                float(rows_by_attr[attr]["value"]) for attr in attrs
            )
            converted = _convert_rotation_values_between_bases(
                cmds,
                raw_values,
                basis,
                source_node=target_node,
                destination_node=control_node,
            )
            for attr, value in zip(attrs, converted):
                cmds.setAttr(f"{control_node}.{attr}", value)
            continue
        curve_nodes = [
            _canonical_node(cmds, str(source).split(".", 1)[0])
            if source
            else None
            for source in control_sources
        ]
        new_sources = [node in created if node else False for node in curve_nodes]
        if not any(new_sources):
            continue
        if any(node and node not in created for node in curve_nodes):
            # A legacy partial representation may already contain control-space
            # curves. Do not reinterpret those persisted values as solver XYZ.
            continue
        times = sorted(
            {
                float(time)
                for node in curve_nodes
                if node
                for time in (cmds.keyframe(node, query=True, timeChange=True) or [])
            }
        )
        if not times:
            times = [float(cmds.currentTime(query=True))]
        source_uses_quaternion = all(
            str(cmds.rotationInterpolation(node, query=True)) == "quaternionSlerp"
            for node in curve_nodes
            if node
        )
        if not source_uses_quaternion and len(times) > 1:
            # Legacy IK-link curves are solver-local Euler channels. Basis
            # conjugation does not commute with independent Euler interpolation,
            # so converting only their sparse endpoints changes the evaluated
            # pose between keys. Preserve every authored animation frame before
            # switching the artist-facing XYZ triplet to quaternion Slerp. This
            # density is intentionally limited to non-identity IK-link routes.
            first_frame = int(math.ceil(times[0]))
            last_frame = int(math.floor(times[-1]))
            times = sorted(set(times).union(range(first_frame, last_frame + 1)))
        raw_samples = [
            tuple(
                _animation_curve_value_at_time(cmds, node, time)
                if node
                else float(rows_by_attr[attr]["value"])
                for attr, node in zip(attrs, curve_nodes)
            )
            for time in times
        ]
        for index, (attr, node) in enumerate(zip(attrs, curve_nodes)):
            if node:
                continue
            node = str(cmds.createNode("animCurveTA"))
            created_curve_nodes.append(node)
            created.add(_canonical_node(cmds, node))
            source = f"{node}.output"
            control_plug = str(rows_by_attr[attr]["control"])
            cmds.connectAttr(source, control_plug, force=False)
            rows_by_attr[attr]["controlSource"] = source
            rows_by_attr[attr]["controlSourceRef"] = _plug_reference(cmds, source)
            control_sources[index] = source
            curve_nodes[index] = _canonical_node(cmds, node)
            _record_curve_representation(
                curve_representations,
                str(rows_by_attr[attr]["target"]),
                rows_by_attr[attr].get("source"),
                source,
                cmds,
            )
        for time, raw_values in zip(times, raw_samples):
            converted = _convert_rotation_values_between_bases(
                cmds,
                raw_values,
                basis,
                source_node=target_node,
                destination_node=control_node,
            )
            for node, value in zip(curve_nodes, converted):
                cmds.setKeyframe(node, time=(time, time), value=value)
        _apply_quaternion_interpolation_to_plugs(cmds, curve_nodes)


def _animation_curve_value_at_time(cmds, node: str, time: float) -> float:
    """Evaluate one time-input animCurve in Maya UI units, bypassing time warps."""

    selection = om.MSelectionList()
    selection.add(node)
    curve = oma.MFnAnimCurve(selection.getDependNode(0))
    value = float(curve.evaluate(om.MTime(float(time), om.MTime.uiUnit())))
    return math.degrees(value) if str(cmds.nodeType(node)) == "animCurveTA" else value


def _convert_rotation_values_between_bases(
    cmds,
    values,
    basis,
    *,
    source_node: str,
    destination_node: str,
) -> Tuple[float, float, float]:
    """Convert one solver-local Euler tuple into the persisted control basis."""

    try:
        source_order = int(cmds.getAttr(f"{source_node}.rotateOrder"))
    except Exception:
        source_order = 0
    try:
        destination_order = int(cmds.getAttr(f"{destination_node}.rotateOrder"))
    except Exception:
        destination_order = 0
    quaternion = _quaternion_from_euler_degrees(values, rotate_order=source_order)
    converted = bone_to_control(quaternion, basis)
    return _euler_degrees_from_quaternion(
        converted,
        rotate_order=destination_order,
    )


def _resolve_rotation_converters(cmds, metadata: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Resolve persisted live converter nodes by UUID, failing closed."""

    resolved = []
    for row in metadata.get("rotationConverters", []) or []:
        if not isinstance(row, Mapping):
            raise MmdControlRigBuildError("invalid rotation converter metadata")
        current = dict(row)
        for key in ("composeUuid", "multUuid", "decomposeUuid"):
            uuid = row.get(key)
            if not uuid:
                raise MmdControlRigBuildError(f"rotation converter UUID is missing: {key}")
            name_key = {
                "composeUuid": "compose",
                "multUuid": "mult",
                "decomposeUuid": "decompose",
            }[key]
            current[name_key] = _resolve_uuid(cmds, str(uuid))
        try:
            current["basis"] = validate_basis_record(row.get("basis")).to_dict()
        except MmdControlRigBasisError as exc:
            raise MmdControlRigBuildError("invalid rotation converter basis metadata") from exc
        resolved.append(current)
    return resolved


def _remove_rotation_converters(cmds, converters: List[Mapping[str, Any]]) -> None:
    """Disconnect and delete persisted live converter nodes after a commit."""

    for row in reversed(converters):
        for key in ("compose", "mult", "decompose"):
            node = row.get(key)
            if not node or not cmds.objExists(node):
                continue
            try:
                cmds.delete(node)
            except Exception as exc:
                raise MmdControlRigBuildError(
                    f"could not remove live authoring-basis converter: {node}"
                ) from exc


def _record_rotation_converter_nodes(
    metadata: Dict[str, Any], converters: List[Mapping[str, Any]]
) -> None:
    """Add converter UUIDs to the owned-node inventory for EDIT inspection."""

    rows = metadata.setdefault("nodes", [])
    known = {str(row.get("uuid")) for row in rows if isinstance(row, Mapping)}
    for converter in converters:
        for uuid_key, name_key in (
            ("composeUuid", "compose"),
            ("multUuid", "mult"),
            ("decomposeUuid", "decompose"),
        ):
            uuid = str(converter.get(uuid_key) or "")
            if uuid and uuid not in known:
                rows.append({"uuid": uuid, "name": str(converter.get(name_key) or uuid)})
                known.add(uuid)


def _record_ik_visibility_inverter_nodes(
    metadata: Dict[str, Any], inverters
) -> None:
    """Persist IK visibility reverse nodes in the rig-owned inventory."""
    records = [dict(row) for row in inverters or () if isinstance(row, Mapping)]
    if not records:
        return
    owned_rows = metadata.setdefault("nodes", [])
    known = {str(row.get("uuid")) for row in owned_rows if isinstance(row, Mapping)}
    current = {
        str(row.get("uuid")): dict(row)
        for row in metadata.get("ikVisibilityInverters", []) or []
        if isinstance(row, Mapping) and row.get("uuid")
    }
    for record in records:
        uuid = str(record.get("uuid") or "")
        if not uuid:
            continue
        current[uuid] = record
        if uuid not in known:
            owned_rows.append({"uuid": uuid, "name": str(record.get("name") or uuid)})
            known.add(uuid)
    metadata["ikVisibilityInverters"] = list(current.values())


def _drop_rotation_converter_nodes(metadata: Dict[str, Any], converters: List[Mapping[str, Any]]) -> None:
    """Remove converter UUID rows after a successful Restore/Bake."""

    uuids = {
        str(converter.get(key))
        for converter in converters
        for key in ("composeUuid", "multUuid", "decomposeUuid")
        if converter.get(key)
    }
    metadata["nodes"] = [
        row
        for row in metadata.get("nodes", [])
        if not isinstance(row, Mapping) or str(row.get("uuid")) not in uuids
    ]


def _disconnect_rotation_converters(cmds, converters: List[Mapping[str, Any]]) -> None:
    """Detach converter outputs before a Control->Bone curve transaction."""

    for row in converters:
        decompose = row.get("decompose")
        if not decompose or not cmds.objExists(decompose):
            continue
        for axis in "XYZ":
            output = f"{decompose}.outputRotate{axis}"
            for target in cmds.listConnections(
                output, source=False, destination=True, plugs=True
            ) or []:
                try:
                    cmds.disconnectAttr(output, target)
                except Exception:
                    if cmds.isConnected(output, target):
                        raise
                # Maya inserts unitConversion nodes when a decompose angle
                # feeds a custom Append scalar. Remove that owned wrapper's
                # terminal edge as well, otherwise the stale wrapper remains
                # the incoming writer during scalar bake/restore.
                wrapper = str(target).split(".", 1)[0]
                for wrapped_target in cmds.listConnections(
                    wrapper, source=False, destination=True, plugs=True
                ) or []:
                    wrapped_node = str(wrapped_target).split(".", 1)[0]
                    target_node = str(row.get("target") or "").rsplit(".", 1)[0]
                    if _canonical_node_name(cmds, target_node) != _canonical_node_name(
                        cmds, wrapped_node
                    ):
                        continue
                    for wrapped_source in cmds.listConnections(
                        wrapped_target, source=True, destination=False, plugs=True
                    ) or []:
                        if str(wrapped_source).split(".", 1)[0] != wrapper:
                            continue
                        try:
                            cmds.disconnectAttr(wrapped_source, wrapped_target)
                        except Exception:
                            if cmds.isConnected(wrapped_source, wrapped_target):
                                raise


def _disconnect_owned_rotation_writer(
    cmds, target: str, converters: List[Mapping[str, Any]]
) -> None:
    """Detach only an owned basis converter output from ``target``.

    Restore must clear the converter's incoming edge before reconnecting the
    pre-EDIT MMD source (or setting a literal value).  A generic
    ``disconnectAttr`` against every incoming source would destroy a foreign
    animation/solver writer, so the decompose nodes are matched against the
    persisted converter inventory first.
    """

    owned = {
        _canonical_node_name(cmds, str(row.get("decompose")))
        for row in converters
        if row.get("decompose")
    }
    if not owned:
        return
    for incoming in cmds.listConnections(
        target, source=True, destination=False, plugs=True
    ) or []:
        if not _plug_reaches_owned_converter(cmds, str(incoming), owned):
            continue
        try:
            cmds.disconnectAttr(incoming, target)
        except Exception:
            if cmds.isConnected(incoming, target):
                raise


def _plug_reaches_owned_converter(
    cmds, plug: str, owned_nodes: Set[str], *, _visited: Optional[Set[str]] = None
) -> bool:
    """Return whether ``plug`` is fed by an owned converter node.

    Maya may insert ``unitConversion`` nodes between a decompose output and a
    custom numeric input (for example ``mmdAppend.baseRotateX``).  Resolve
    only that upstream chain so Restore can remove the wrapper edge without
    touching an unrelated foreign writer.
    """

    visited = _visited if _visited is not None else set()
    node = str(plug).split(".", 1)[0]
    if _canonical_node_name(cmds, node) in owned_nodes:
        return True
    if node in visited or not cmds.objExists(node):
        return False
    visited.add(node)
    try:
        upstream = cmds.listConnections(
            node, source=True, destination=False, plugs=True
        ) or []
    except Exception:
        return False
    return any(
        _plug_reaches_owned_converter(cmds, str(source), owned_nodes, _visited=visited)
        for source in upstream
    )


def _plug_reaches_control(
    cmds, plug: str, control: str, *, _visited: Optional[Set[str]] = None
) -> bool:
    """Return whether an incoming wrapper is ultimately driven by ``control``."""

    if _canonical_plug(cmds, plug) == _canonical_plug(cmds, control):
        return True
    visited = _visited if _visited is not None else set()
    node = str(plug).split(".", 1)[0]
    if node in visited or not cmds.objExists(node):
        return False
    visited.add(node)
    try:
        upstream = cmds.listConnections(
            node, source=True, destination=False, plugs=True
        ) or []
    except Exception:
        return False
    return any(
        _plug_reaches_control(cmds, str(source), control, _visited=visited)
        for source in upstream
    )


def _canonical_plug(cmds, plug: str) -> str:
    """Canonicalize a Maya plug for short-name/long-path comparisons."""

    node, separator, attribute = str(plug).partition(".")
    if not separator:
        return str(plug)
    try:
        matches = cmds.ls(node, long=True) or []
    except Exception:
        matches = []
    return f"{str(matches[0]) if len(matches) == 1 else node}.{attribute}"


def _canonical_node_name(cmds, node: str) -> str:
    """Canonicalize a Maya node name for short-name/long-path comparisons."""

    try:
        matches = cmds.ls(str(node), long=True) or []
    except Exception:
        matches = []
    return str(matches[0]) if len(matches) == 1 else str(node)


def _rollback(cmds, operations) -> None:
    for action, source, target in reversed(operations):
        try:
            if action == "disconnect" and cmds.isConnected(source, target):
                cmds.disconnectAttr(source, target)
            elif action == "connect" and not cmds.isConnected(source, target):
                cmds.connectAttr(source, target, force=False)
        except Exception:
            pass


def _restore_offsets(cmds, rows, *, strict: bool = False) -> None:
    for row in reversed(rows):
        try:
            cmds.setAttr(row["control"], *row["value"], type="matrix")
        except Exception:
            if strict:
                raise


def _rotation_channel_groups(
    rows: List[Mapping[str, Any]],
    *,
    include_sampled_direct: bool = False,
    include_sampled_passthrough: bool = False,
) -> List[List[Mapping[str, Any]]]:
    """Group complete direct XYZ rotation routes for compound bake transfer.

    A quaternion curve is a three-channel value.  Group only standard
    ``rotateX/Y/Z`` targets on one control and one destination transform;
    append, IK, and partially mapped routes remain on the scalar fail-closed
    path.
    """
    candidates: Dict[Tuple[str, str, str], Dict[str, Mapping[str, Any]]] = {}
    for row in rows:
        control = str(row.get("control") or "")
        target = str(row.get("target") or "")
        control_node, _, control_attr = control.partition(".")
        target_node, _, target_attr = target.partition(".")
        target_leaf = target.rsplit(".", 1)[-1]
        target_group = target.rsplit(".", 1)[0]
        if not control_node or not target_node:
            continue
        if control_attr not in {"rotateX", "rotateY", "rotateZ"}:
            continue
        passthrough_compound = bool(include_sampled_passthrough) and target_leaf.startswith(
            ("baseRotate", "inputRotateElement")
        )
        basis_compound = bool(include_sampled_direct) and _supports_live_authoring_basis(row)
        if target_leaf not in {"rotateX", "rotateY", "rotateZ"}:
            if not (
                row.get("twistController")
                or passthrough_compound
                or basis_compound
            ) or not target_leaf.endswith(("X", "Y", "Z")):
                continue
            if not (
                target_leaf.startswith("baseRotate")
                or target_leaf.startswith("inputRotateElement")
            ):
                continue
        rotation_attr = _rotation_attr_for_target(target)
        if rotation_attr != control_attr and not row.get("twistController"):
            continue
        if (
            row.get("routeClass", ROUTE_SAME_BASIS) != ROUTE_SAME_BASIS
            and not row.get("twistController")
        ):
            if not include_sampled_passthrough and (
                not include_sampled_direct or not _supports_live_authoring_basis(row)
            ):
                continue
        key = (control_node, target_group, ROUTE_SAME_BASIS)
        candidates.setdefault(key, {})[rotation_attr] = row

    groups = []
    for channel_rows in candidates.values():
        if set(channel_rows) == {"rotateX", "rotateY", "rotateZ"}:
            groups.append([channel_rows[attr] for attr in ("rotateX", "rotateY", "rotateZ")])
    return groups


def _rotation_attr_for_target(target: str) -> str:
    """Normalize rotate/baseRotate/inputRotateElement children to rotateXYZ."""

    attribute = str(target).rsplit(".", 1)[-1]
    if attribute in {"rotateX", "rotateY", "rotateZ"}:
        return attribute
    if attribute.startswith(("baseRotate", "inputRotateElement")) and attribute.endswith(
        ("X", "Y", "Z")
    ):
        return f"rotate{attribute[-1]}"
    return attribute


def _rotation_group_uses_quaternion(cmds, rows, sources_by_control) -> bool:
    """Return whether every controller source belongs to a quaternion compound."""
    curves = []
    for row in rows:
        source = row.get("controlSource") or sources_by_control.get(row.get("control"))
        if not source:
            return False
        node = str(source).split(".", 1)[0]
        try:
            if not str(cmds.nodeType(node)).startswith("animCurve"):
                return False
            if cmds.rotationInterpolation(node, query=True) != "quaternionSlerp":
                return False
        except Exception:
            return False
        curves.append(node)
    return len(set(curves)) == 3


def _capture_rotation_interpolation_states(cmds, groups):
    """Capture destination compound interpolation for transaction rollback."""
    states = []
    for group in groups:
        plugs = [str(row["target"]) for row in group]
        curves = []
        for row in group:
            source = row.get("source")
            if not source:
                curves = []
                break
            node = str(source).split(".", 1)[0]
            if not str(cmds.nodeType(node)).startswith("animCurve"):
                curves = []
                break
            curves.append(node)
        if len(curves) == 3:
            try:
                states.append((plugs, cmds.rotationInterpolation(curves[0], query=True)))
            except Exception:
                pass
    return states


def _restore_rotation_interpolation_states(cmds, states) -> None:
    """Restore compound interpolation after animation payload rollback."""
    for plugs, mode in states:
        cmds.rotationInterpolation(*plugs, convert=mode)


def _rotation_group_requires_live_target_sampling(
    rows: List[Mapping[str, Any]],
) -> bool:
    """Return whether a non-transform basis route must bake evaluated targets."""

    if any(
        str(row["target"]).rsplit(".", 1)[-1]
        in {"rotateX", "rotateY", "rotateZ"}
        for row in rows
    ):
        return False
    if not all(_supports_bake_authoring_basis(row) for row in rows):
        return False
    try:
        basis = _consistent_rotation_group_basis(rows)
    except MmdControlRigBasisError:
        return False
    return basis.quaternion != (0.0, 0.0, 0.0, 1.0)


def _capture_live_target_rotation_samples(
    cmds,
    rows: List[Mapping[str, Any]],
    sources_by_control: Mapping[str, Optional[str]],
    *,
    frame_range=None,
):
    """Capture dense raw solver inputs while the live basis converter exists."""

    attrs = ("rotateX", "rotateY", "rotateZ")
    rows_by_attr = {_rotation_attr_for_target(row["target"]): row for row in rows}
    sources = [sources_by_control.get(rows_by_attr[attr]["control"]) for attr in attrs]
    mmd_sources = [rows_by_attr[attr].get("source") for attr in attrs]
    times = sorted(
        {
            float(time)
            for plug in (*sources, *mmd_sources)
            if plug
            for time in (
                cmds.keyframe(
                    str(plug).split(".", 1)[0],
                    query=True,
                    timeChange=True,
                )
                or []
            )
        }
    )
    times = _dense_sample_times(times, frame_range)
    if not times:
        times = [float(cmds.currentTime(query=True))]
    return [
        (
            time,
            tuple(
                float(cmds.getAttr(rows_by_attr[attr]["target"], time=time))
                for attr in attrs
            ),
        )
        for time in times
    ]


def _commit_control_rotation_group(
    cmds,
    rows: List[Mapping[str, Any]],
    sources_by_control: Mapping[str, Optional[str]],
    *,
    created_curve_nodes=None,
    quaternion_interpolation: Optional[bool] = None,
    evaluated_target_samples=None,
    frame_range=None,
) -> Dict[str, Optional[str]]:
    """Bake one sparse three-axis rotation compound without scalar teardown.

    Controller values are never sampled after an individual axis is detached.
    All three sparse curve payloads are transferred before Maya quaternion
    interpolation is restored on the resulting destination compound.  The
    control plugs directly drove these exact target plugs in EDIT, so copying
    their authored payload avoids solver-amplified resampling error even when
    the route was classified as sampled for its surrounding transform basis.
    """
    if len(rows) != 3:
        raise MmdControlRigBuildError("incomplete quaternion rotation route")
    attrs = ("rotateX", "rotateY", "rotateZ")
    rows_by_attr = {_rotation_attr_for_target(row["target"]): row for row in rows}
    if set(rows_by_attr) != set(attrs):
        raise MmdControlRigBuildError("quaternion rotation route is not standard XYZ")

    controls = [str(rows_by_attr[attr]["control"]) for attr in attrs]
    targets = [str(rows_by_attr[attr]["target"]) for attr in attrs]
    control_node = controls[0].rsplit(".", 1)[0]
    target_node = targets[0].rsplit(".", 1)[0]
    standard_rotate_targets = all(
        str(target).rsplit(".", 1)[-1] in {"rotateX", "rotateY", "rotateZ"}
        for target in targets
    )
    if any(value.rsplit(".", 1)[0] != control_node for value in controls):
        raise MmdControlRigBuildError("quaternion rotation controls are split across nodes")
    if any(value.rsplit(".", 1)[0] != target_node for value in targets):
        raise MmdControlRigBuildError("quaternion rotation targets are split across nodes")

    control_sources = []
    mmd_sources = []
    for attr in attrs:
        row = rows_by_attr[attr]
        source = sources_by_control.get(row["control"])
        control_source = row.get("controlSource") or source
        control_sources.append(str(control_source) if control_source else None)
        mmd_sources.append(str(row["source"]) if row.get("source") else None)

    if evaluated_target_samples is not None:
        return _sample_control_rotation_group_passthrough(
            cmds,
            rows,
            {row["control"]: source for row, source in zip(rows, control_sources)},
            created_curve_nodes=created_curve_nodes,
            evaluated_samples=evaluated_target_samples,
            frame_range=frame_range,
        )

    # A non-identity authoring basis cannot be represented by copying Euler
    # payloads.  Capture the complete XYZ union grid while the controller is
    # still live, convert each sample by quaternion conjugation, then replace
    # the MMD curves in place so their UUIDs and time-input graphs survive.
    basis_record = rows_by_attr["rotateX"].get("authoringBasis")
    if basis_record and all(_supports_bake_authoring_basis(row) for row in rows):
        try:
            basis = _consistent_rotation_group_basis(rows)
        except MmdControlRigBasisError as exc:
            raise MmdControlRigBuildError("invalid authoring basis on rotation journal") from exc
        if basis.quaternion != (0.0, 0.0, 0.0, 1.0):
            # Only transform rotateXYZ targets can own Maya's quaternion
            # interpolation state. IK-link inputRotateElementXYZ must retain
            # the evaluated live pose through dense sampling after basis
            # conversion; sparse converted keys would interpolate as Euler.
            preserve_quaternion = standard_rotate_targets and (
                True
                if quaternion_interpolation is None
                else bool(quaternion_interpolation)
            )
            return _sample_control_rotation_group_to_bone(
                cmds,
                rows,
                {row["control"]: source for row, source in zip(rows, control_sources)},
                basis.to_dict(),
                created_curve_nodes=created_curve_nodes,
                quaternion_interpolation=preserve_quaternion,
                frame_range=frame_range,
            )

    if not standard_rotate_targets:
        return _sample_control_rotation_group_passthrough(
            cmds,
            rows,
            {row["control"]: source for row, source in zip(rows, control_sources)},
            created_curve_nodes=created_curve_nodes,
            frame_range=frame_range,
        )

    # Disconnect all three control edges first.  This keeps the compound
    # intact while destination curves are connected/copied and converted.
    for row in rows:
        control, target = row["control"], row["target"]
        try:
            cmds.disconnectAttr(control, target)
        except RuntimeError:
            if cmds.isConnected(control, target):
                raise
    result: Dict[str, Optional[str]] = {}
    destination_plugs = []

    # Maya's rotationInterpolation state belongs to the XYZ compound, not to
    # an individual animCurve.  Copy all three curves through the transform
    # nodes in one clipboard operation; scalar copyKey/pasteKey rewrites a
    # +/-180 degree quaternion curve into an unrelated Euler representation.
    if standard_rotate_targets and all(
        control_source and mmd_source
        for control_source, mmd_source in zip(control_sources, mmd_sources)
    ):
        for row, mmd_source in zip(rows, mmd_sources):
            target = row["target"]
            if not cmds.isConnected(mmd_source, target):
                cmds.connectAttr(mmd_source, target, force=False)
            destination_plugs.append(str(target))
        _copy_rotation_curve_group(cmds, control_node, target_node)
        for row, control_source, mmd_source in zip(rows, control_sources, mmd_sources):
            control = row["control"]
            if cmds.isConnected(control_source, control):
                cmds.disconnectAttr(control_source, control)
            result[control] = str(mmd_source)
        if quaternion_interpolation is not False:
            _apply_quaternion_interpolation_to_plugs(cmds, destination_plugs)
        from mmd_tools.converters.vmd_rotation_time_curve import (
            share_vmd_rotation_time_curve,
        )

        share_vmd_rotation_time_curve(cmds, control_sources, mmd_sources)
        return result

    for attr, row, control_source, mmd_source in zip(
        attrs, rows, control_sources, mmd_sources
    ):
        control, target = row["control"], row["target"]
        if control_source and mmd_source:
            if cmds.isConnected(control_source, control):
                cmds.disconnectAttr(control_source, control)
            if not cmds.isConnected(mmd_source, target):
                cmds.connectAttr(mmd_source, target, force=False)
            _copy_animation_curve(cmds, control_source, mmd_source)
            result[control] = str(mmd_source)
            destination_plugs.append(str(target))
        elif control_source:
            if cmds.isConnected(control_source, control):
                cmds.disconnectAttr(control_source, control)
            if not cmds.isConnected(control_source, target):
                cmds.connectAttr(control_source, target, force=False)
            result[control] = None
            destination_plugs.append(str(target))
        elif mmd_source:
            if not cmds.isConnected(mmd_source, target):
                cmds.connectAttr(mmd_source, target, force=False)
            result[control] = str(mmd_source)
            destination_plugs.append(str(target))
        else:
            cmds.setAttr(target, float(cmds.getAttr(control)))
            result[control] = None

    if standard_rotate_targets and quaternion_interpolation is not False:
        _apply_quaternion_interpolation_to_plugs(cmds, destination_plugs)
    from mmd_tools.converters.vmd_rotation_time_curve import (
        share_vmd_rotation_time_curve,
    )

    baked_sources = [
        result.get(control) or control_source
        for control, control_source in zip(controls, control_sources)
    ]
    share_vmd_rotation_time_curve(cmds, control_sources, baked_sources)
    return result


def _sample_control_rotation_group_passthrough(
    cmds,
    rows: List[Mapping[str, Any]],
    control_sources: Mapping[str, Optional[str]],
    *,
    created_curve_nodes=None,
    evaluated_samples=None,
    frame_range=None,
) -> Dict[str, Optional[str]]:
    """Bake evaluated XYZ values into a non-transform rotation compound.

    IK link inputs such as ``inputRotate[slot].inputRotateElementXYZ`` cannot
    own Maya's transform-level quaternion interpolation. Sample all three
    evaluated Control channels before detaching any of them, then write dense
    angle curves so their solver input matches the live Control pose.
    """

    attrs = ("rotateX", "rotateY", "rotateZ")
    rows_by_attr = {_rotation_attr_for_target(row["target"]): row for row in rows}
    control_node = str(rows_by_attr["rotateX"]["control"]).rsplit(".", 1)[0]
    sources = [control_sources.get(rows_by_attr[attr]["control"]) for attr in attrs]
    mmd_sources = [rows_by_attr[attr].get("source") for attr in attrs]
    times = sorted(
        {
            float(time)
            for plug in (*sources, *mmd_sources)
            if plug
            for time in (
                cmds.keyframe(
                    str(plug).split(".", 1)[0],
                    query=True,
                    timeChange=True,
                )
                or []
            )
        }
    )
    times = _dense_sample_times(times, frame_range)
    if not times:
        times = [float(cmds.currentTime(query=True))]
    samples = list(evaluated_samples) if evaluated_samples is not None else [
        (
            time,
            tuple(
                float(cmds.getAttr(f"{control_node}.{axis}", time=time))
                for axis in attrs
            ),
        )
        for time in times
    ]

    if not any((*sources, *mmd_sources)):
        values = samples[0][1]
        result: Dict[str, Optional[str]] = {}
        for index, attr in enumerate(attrs):
            row = rows_by_attr[attr]
            control = str(row["control"])
            target = str(row["target"])
            if cmds.isConnected(control, target):
                cmds.disconnectAttr(control, target)
            cmds.setAttr(target, float(values[index]))
            result[control] = None
        return result

    result: Dict[str, Optional[str]] = {}
    for index, attr in enumerate(attrs):
        row = rows_by_attr[attr]
        control = str(row["control"])
        target = str(row["target"])
        control_source = sources[index]
        mmd_source = mmd_sources[index]
        if cmds.isConnected(control, target):
            cmds.disconnectAttr(control, target)
        if control_source and cmds.isConnected(control_source, control):
            cmds.disconnectAttr(control_source, control)
        if mmd_source:
            curve = str(mmd_source).split(".", 1)[0]
            source = str(mmd_source)
            if evaluated_samples is not None:
                # The existing MMD curve may still consume a shared VMD
                # Bezier time curve. Dense samples are already expressed in
                # scene time, so retaining that input would apply the time
                # warp a second time after bake. Reuse the curve UUID, but
                # restore ordinary Maya time before replacing its keys.
                input_plug = f"{curve}.input"
                for incoming in cmds.listConnections(
                    input_plug,
                    source=True,
                    destination=False,
                    plugs=True,
                ) or []:
                    if cmds.isConnected(incoming, input_plug):
                        cmds.disconnectAttr(incoming, input_plug)
                if cmds.objExists("time1.outTime"):
                    cmds.connectAttr("time1.outTime", input_plug, force=False)
                # Keep the curve connected and clear through MFnAnimCurve;
                # cmds.cutKey may delete the node when its final key goes.
                if not cmds.isConnected(source, target):
                    cmds.connectAttr(source, target, force=False)
                _clear_animation_curve_keys(cmds, curve)
        else:
            try:
                curve = str(cmds.createNode("animCurveTA"))
                if created_curve_nodes is not None:
                    created_curve_nodes.append(curve)
                source = f"{curve}.output"
            except Exception as exc:
                raise MmdControlRigBuildError(
                    f"could not create sampled rotation curve: {target}"
                ) from exc
        for time, values in samples:
            cmds.setKeyframe(
                curve,
                time=(time, time),
                value=float(values[index]),
            )
        _set_sampled_curve_tangents(cmds, source, target)
        if not cmds.isConnected(source, target):
            cmds.connectAttr(source, target, force=False)
        result[control] = source
    return result


def _sample_control_rotation_group_to_bone(
    cmds,
    rows: List[Mapping[str, Any]],
    control_sources: Mapping[str, Optional[str]],
    basis: Mapping[str, Any],
    *,
    created_curve_nodes=None,
    quaternion_interpolation: bool = False,
    frame_range=None,
) -> Dict[str, Optional[str]]:
    """Sample a complete controller XYZ group through the basis inverse.

    Quaternion controller compounds retain their sparse union key grid. Euler
    and Bezier compounds are sampled at every integer frame as well as their
    authored key times, because basis conjugation couples all three channels
    and copying only their sparse keys would change the evaluated pose.
    """

    attrs = ("rotateX", "rotateY", "rotateZ")
    rows_by_attr = {_rotation_attr_for_target(row["target"]): row for row in rows}
    control = str(rows_by_attr["rotateX"]["control"])
    target = str(rows_by_attr["rotateX"]["target"])
    control_node = control.rsplit(".", 1)[0]
    target_node = target.rsplit(".", 1)[0]
    sources = [control_sources.get(rows_by_attr[attr]["control"]) for attr in attrs]
    mmd_sources = [rows_by_attr[attr].get("source") for attr in attrs]
    times = sorted(
        {
            float(time)
            for plug in (*sources, *mmd_sources)
            if plug
            for time in (cmds.keyframe(str(plug).split(".", 1)[0], query=True, timeChange=True) or [])
        }
    )
    if not times:
        try:
            times = [float(cmds.currentTime(query=True))]
        except Exception:
            times = [0.0]
    elif not quaternion_interpolation:
        times = _dense_sample_times(times, frame_range)
    elif frame_range is not None:
        start, end = _normalize_dense_frame_range(frame_range)
        times = [time for time in times if start <= time <= end]
        if not times:
            times = [start, end]
    samples = []
    try:
        control_rotate_order = int(cmds.getAttr(f"{control_node}.rotateOrder"))
    except Exception:
        control_rotate_order = 0
    try:
        target_rotate_order = int(cmds.getAttr(f"{target_node}.rotateOrder"))
    except Exception:
        target_rotate_order = 0
    for time in times:
        values = [float(cmds.getAttr(f"{control_node}.{axis}", time=time)) for axis in attrs]
        control_quaternion = _quaternion_from_euler_degrees(
            values,
            rotate_order=control_rotate_order,
        )
        bone_quaternion = control_to_bone(control_quaternion, basis)
        samples.append(
            (
                time,
                _euler_degrees_from_quaternion(
                    bone_quaternion,
                    rotate_order=target_rotate_order,
                ),
            )
        )

    # A newly authored static controller has no detached control curve yet.
    # Persist its basis-space XYZ values before replacing the live converter
    # with bone-space curves; otherwise the next EDIT would duplicate the
    # converted bone curves and apply the authoring basis a second time.
    control_samples = [
        (
            time,
            tuple(
                float(cmds.getAttr(f"{control_node}.{axis}", time=time))
                for axis in attrs
            ),
        )
        for time in times
    ]
    for index, (axis, row) in enumerate(
        zip(attrs, (rows_by_attr[attr] for attr in attrs))
    ):
        if sources[index]:
            continue
        curve = None
        try:
            curve = str(cmds.createNode("animCurveTA"))
            if created_curve_nodes is not None:
                created_curve_nodes.append(curve)
            for time, values in control_samples:
                cmds.setKeyframe(
                    curve,
                    time=(time, time),
                    value=float(values[index]),
                )
            source = f"{curve}.output"
            sources[index] = source
            row["controlSource"] = source
        except Exception as exc:
            if curve and cmds.objExists(curve):
                try:
                    cmds.delete(curve)
                except Exception:
                    pass
            raise MmdControlRigBuildError(
                f"could not retain basis-space control rotation curve: {row['control']}"
            ) from exc
    # Keep the authored MMD curve nodes when present.  If an imported motion
    # had no source curves, create one curve per axis and connect them to the
    # target.  All three axes always receive the same union time grid.
    result: Dict[str, Optional[str]] = {}
    for axis, row, mmd_source in zip(attrs, (rows_by_attr[attr] for attr in attrs), mmd_sources):
        control_plug = row["control"]
        target_plug = row["target"]
        if cmds.isConnected(control_plug, target_plug):
            cmds.disconnectAttr(control_plug, target_plug)
        if mmd_source:
            node = str(mmd_source).split(".", 1)[0]
            # Every pre-existing source key is part of ``times`` by
            # construction, so setting the union grid in place preserves the
            # animCurve node UUID without destructive ``cutKey`` (which Maya
            # removes when its last key is cleared).
            for time, values in samples:
                cmds.setKeyframe(node, time=(time, time), value=float(values[attrs.index(axis)]))
            if not cmds.isConnected(mmd_source, target_plug):
                cmds.connectAttr(mmd_source, target_plug, force=False)
            result[control_plug] = str(mmd_source)
            continue
        curve_type = "animCurveTA"
        try:
            node = str(cmds.createNode(curve_type))
            if created_curve_nodes is not None:
                created_curve_nodes.append(node)
            for time, values in samples:
                cmds.setKeyframe(node, time=(time, time), value=float(values[attrs.index(axis)]))
            source = f"{node}.output"
            cmds.connectAttr(source, target_plug, force=False)
            result[control_plug] = source
        except Exception as exc:
            if "node" in locals() and cmds.objExists(node):
                try:
                    cmds.delete(node)
                except Exception:
                    pass
            raise MmdControlRigBuildError(
                f"could not create basis-converted MMD rotation curve: {target_plug}"
            ) from exc
    # The CONTROL representation remains UUID-addressable for the next EDIT,
    # but BAKED/MMD_OWNED must not leave it connected to the controller. The
    # generic re-entry path treats any live controller writer as foreign; this
    # matches the scalar and quaternion bake paths, which already detach it.
    for row, control_source in zip(
        (rows_by_attr[attr] for attr in attrs),
        sources,
    ):
        if control_source and cmds.isConnected(control_source, row["control"]):
            cmds.disconnectAttr(control_source, row["control"])
    if quaternion_interpolation and all(
        str(row["target"]).rsplit(".", 1)[-1] in {"rotateX", "rotateY", "rotateZ"}
        for row in rows
    ):
        _apply_quaternion_interpolation_to_plugs(cmds, [row["target"] for row in rows])
    from mmd_tools.converters.vmd_rotation_time_curve import (
        share_vmd_rotation_time_curve,
    )

    share_vmd_rotation_time_curve(
        cmds,
        sources,
        [result.get(rows_by_attr[attr]["control"]) for attr in attrs],
    )
    return result


def _quaternion_from_euler_degrees(
    values,
    *,
    rotate_order: int = 0,
) -> Tuple[float, float, float, float]:
    """Convert Maya Euler degrees and rotate order to an xyzw quaternion."""

    try:
        if int(rotate_order) not in range(6):
            raise ValueError(f"invalid Maya rotate order: {rotate_order}")
        rotation = om.MEulerRotation(
            math.radians(float(values[0])),
            math.radians(float(values[1])),
            math.radians(float(values[2])),
            int(rotate_order),
        )
        q = rotation.asQuaternion()
        return (float(q.x), float(q.y), float(q.z), float(q.w))
    except Exception:
        if int(rotate_order) != 0:
            raise
        x, y, z = (math.radians(float(value)) * 0.5 for value in values)
        cx, sx = math.cos(x), math.sin(x)
        cy, sy = math.cos(y), math.sin(y)
        cz, sz = math.cos(z), math.sin(z)
        return (
            sx * cy * cz - cx * sy * sz,
            cx * sy * cz + sx * cy * sz,
            cx * cy * sz - sx * sy * cz,
            cx * cy * cz + sx * sy * sz,
        )


def _euler_degrees_from_quaternion(
    quaternion,
    *,
    rotate_order: int = 0,
) -> Tuple[float, float, float]:
    """Convert an xyzw quaternion to Maya Euler degrees in one rotate order."""

    try:
        if int(rotate_order) not in range(6):
            raise ValueError(f"invalid Maya rotate order: {rotate_order}")
        q = om.MQuaternion(*[float(value) for value in quaternion])
        rotation = q.asEulerRotation()
        rotation.reorderIt(int(rotate_order))
        return tuple(math.degrees(float(value)) for value in (rotation.x, rotation.y, rotation.z))
    except Exception:
        if int(rotate_order) != 0:
            raise
        x, y, z, w = (float(value) for value in quaternion)
        sinr = 2.0 * (w * x + y * z)
        cosr = 1.0 - 2.0 * (x * x + y * y)
        roll = math.atan2(sinr, cosr)
        sinp = 2.0 * (w * y - z * x)
        pitch = math.copysign(math.pi / 2.0, sinp) if abs(sinp) >= 1.0 else math.asin(sinp)
        siny = 2.0 * (w * z + x * y)
        cosy = 1.0 - 2.0 * (y * y + z * z)
        yaw = math.atan2(siny, cosy)
        return tuple(math.degrees(value) for value in (roll, pitch, yaw))


def _copy_rotation_curve_group(cmds, source_node: str, destination_node: str) -> None:
    """Copy sparse XYZ curve payload while retaining compound interpolation."""
    try:
        cmds.copyKey(
            source_node,
            attribute=["rotateX", "rotateY", "rotateZ"],
            option="curve",
        )
        cmds.pasteKey(destination_node, option="replaceCompletely")
    except Exception as exc:
        raise MmdControlRigBuildError(
            f"could not copy quaternion rotation curves: {source_node} -> {destination_node}"
        ) from exc


def _apply_quaternion_interpolation_to_plugs(cmds, plugs: List[str]) -> None:
    """Apply quaternion slerp to a complete, keyed rotation compound."""
    if len(plugs) != 3:
        return
    curves = []
    for plug in plugs:
        node = str(plug).split(".", 1)[0]
        try:
            if str(cmds.nodeType(node)).startswith("animCurve"):
                curves.append(node)
                continue
        except Exception:
            return
        incoming = cmds.listConnections(
            plug, source=True, destination=False, plugs=True
        ) or []
        if len(incoming) != 1:
            return
        try:
            curve = str(incoming[0]).split(".", 1)[0]
            if not str(cmds.nodeType(curve)).startswith("animCurve"):
                return
            curves.append(curve)
        except Exception:
            return
    try:
        cmds.rotationInterpolation(*curves, convert="quaternionSlerp")
    except Exception as exc:
        raise MmdControlRigBuildError(
            f"could not preserve quaternion interpolation for {plugs[0]}: {exc}"
        ) from exc


def _commit_control_input(
    cmds,
    row: Mapping[str, Any],
    source: Optional[str],
    *,
    created_curve_nodes=None,
    frame_range=None,
) -> Optional[str]:
    control, target = row["control"], row["target"]
    if row.get("layerRoute"):
        if not row.get("translateBaselineOutput"):
            restore_mmd_control_rig_anim_layer_route(
                cmds,
                row["layerRoute"],
                control,
            )
            return str(row.get("source")) if row.get("source") else None
        _disconnect_translate_baseline(cmds, row)
        _disconnect_layer_control_source(cmds, row)
        control_source = row.get("controlSource")
        mmd_source = row.get("source")
        if not control_source or not mmd_source:
            raise MmdControlRigBuildError(
                f"translate animation-layer route curves are incomplete: {target}"
            )
        _require_animation_source(cmds, control_source, control)
        restore_mmd_control_rig_anim_layer_route(
            cmds,
            row["layerRoute"],
            control,
        )
        _copy_animation_curve(cmds, control_source, mmd_source)
        _offset_animation_curve_values(
            cmds,
            mmd_source,
            _translate_baseline(row),
        )
        _recenter_translate_control_source(cmds, row, control_source)
        return str(mmd_source)
    value = cmds.getAttr(control)
    baseline = _translate_baseline(row)
    _disconnect_translate_baseline(cmds, row)
    if cmds.isConnected(control, target):
        cmds.disconnectAttr(control, target)
    control_source = row.get("controlSource") or source
    mmd_source = row.get("source")
    if row.get("routeClass", ROUTE_SAME_BASIS) == ROUTE_SAMPLED:
        result = _sample_control_input_to_mmd(
            cmds,
            row,
            control_source,
            mmd_source,
            created_curve_nodes=created_curve_nodes,
            frame_range=frame_range,
        )
        _recenter_translate_control_source(cmds, row, control_source)
        return result
    if control_source and mmd_source:
        # The two curves stay as separate nodes. Bake copies controller keys
        # into the original MMD curve when possible, then makes that curve the
        # sole writer of the authored input.
        if cmds.isConnected(control_source, control):
            cmds.disconnectAttr(control_source, control)
        if not cmds.isConnected(mmd_source, target):
            cmds.connectAttr(mmd_source, target, force=False)
        _copy_animation_curve(cmds, control_source, mmd_source)
        if row.get("translateBaselineOutput"):
            _offset_animation_curve_values(cmds, mmd_source, baseline)
            _recenter_translate_control_source(cmds, row, control_source)
        return str(mmd_source)
    if control_source and not mmd_source:
        # No authored MMD curve existed before EDIT. Keep the controller curve
        # intact and make it the sole MMD writer for this newly authored route.
        if cmds.isConnected(control_source, control):
            cmds.disconnectAttr(control_source, control)
        if row.get("translateBaselineOutput"):
            absolute_source = _duplicate_animation_source(
                cmds,
                control_source,
                created_curve_nodes if created_curve_nodes is not None else [],
            )
            _offset_animation_curve_values(cmds, absolute_source, baseline)
            if not cmds.isConnected(absolute_source, target):
                cmds.connectAttr(absolute_source, target, force=False)
            _recenter_translate_control_source(cmds, row, control_source)
            return absolute_source
        if not cmds.isConnected(control_source, target):
            cmds.connectAttr(control_source, target, force=False)
        return None
    if source:
        if cmds.isConnected(source, control):
            cmds.disconnectAttr(source, control)
        if not cmds.isConnected(source, target):
            cmds.connectAttr(source, target, force=False)
    else:
        cmds.setAttr(target, value + baseline if row.get("translateBaselineOutput") else value)
    return str(source) if source else None


def _sample_control_input_to_mmd(
    cmds,
    row: Mapping[str, Any],
    control_source: Optional[str],
    mmd_source: Optional[str],
    *,
    created_curve_nodes=None,
    frame_range=None,
) -> Optional[str]:
    """Sample controller values into an MMD animCurve.

    Solver/append/IK routes do not share a transform basis, so copying the
    controller curve payload would be misleading.  Existing MMD key times are
    retained as the deterministic sample grid; when no MMD source exists, a
    new curve is created and keyed over that same grid.  The detached
    controller curve remains available through the persisted dual-owner
    representation.
    """
    control, target = row["control"], row["target"]
    source_node = mmd_source.split(".", 1)[0] if mmd_source else None
    control_node = control_source.split(".", 1)[0] if control_source else None
    source_times = []
    if source_node:
        source_times = [
            float(time)
            for time in (cmds.keyframe(source_node, query=True, timeChange=True) or [])
        ]
    control_times = []
    if control_node:
        control_times = [
            float(time)
            for time in (cmds.keyframe(control_node, query=True, timeChange=True) or [])
        ]
    times = _dense_sample_times(source_times + control_times, frame_range)
    sampled_values = [
        (
            time,
            float(cmds.getAttr(control, time=time))
            + (_translate_baseline(row) if row.get("translateBaselineOutput") else 0.0),
        )
        for time in sorted(set(times))
    ]
    if cmds.isConnected(control, target):
        cmds.disconnectAttr(control, target)
    if control_source and cmds.isConnected(control_source, control):
        cmds.disconnectAttr(control_source, control)
    if mmd_source:
        if not cmds.isConnected(mmd_source, target):
            cmds.connectAttr(mmd_source, target, force=False)
        target_node, _, target_attr = target.partition(".")
        for time, value in sampled_values:
            cmds.setKeyframe(
                target_node,
                attribute=target_attr,
                time=(time, time),
                value=value,
            )
        _set_sampled_curve_tangents(cmds, mmd_source, target)
        if sampled_values or cmds.isConnected(mmd_source, target):
            return str(mmd_source)

    if sampled_values:
        curve_type = _sampled_curve_type(cmds, control_source, target)
        try:
            curve_node = str(cmds.createNode(curve_type))
            if created_curve_nodes is not None:
                created_curve_nodes.append(curve_node)
            for time, value in sampled_values:
                cmds.setKeyframe(curve_node, time=(time, time), value=value)
            _set_sampled_curve_tangents(cmds, f"{curve_node}.output", target)
            curve_plug = f"{curve_node}.output"
            cmds.connectAttr(curve_plug, target, force=False)
            return curve_plug
        except Exception as exc:
            if 'curve_node' in locals() and cmds.objExists(curve_node):
                try:
                    cmds.delete(curve_node)
                except Exception:
                    pass
            raise MmdControlRigBuildError(
                f"could not create sampled MMD animation curve for {target}: {exc}"
            ) from exc
    value = float(cmds.getAttr(control)) + (
        _translate_baseline(row) if row.get("translateBaselineOutput") else 0.0
    )
    cmds.setAttr(target, value)
    return None


def _set_sampled_curve_tangents(cmds, curve_plug: str, target: str) -> None:
    """Use non-overshooting interpolation for sampled solver inputs.

    Transform-space samples are a discrete approximation of a solved pose and
    must not introduce Bezier overshoot between integer frames.  Boolean IK
    state is piecewise constant, so a step tangent keeps the prior state until
    the next keyed frame.
    """
    curve_node = str(curve_plug).split(".", 1)[0]
    in_tangent, out_tangent = _sampled_curve_tangent_types(target)
    cmds.keyTangent(
        curve_node,
        edit=True,
        inTangentType=in_tangent,
        outTangentType=out_tangent,
    )


def _sampled_curve_tangent_types(target: str) -> Tuple[str, str]:
    """Return explicit in/out tangent types for a sampled destination plug."""
    attribute = str(target).rsplit(".", 1)[-1].lower()
    if attribute.endswith("enabled") or attribute.startswith("ik"):
        # The outgoing ``step`` tangent owns the held-state behavior. Maya
        # 2027 also rejects ``stepnext`` as an in-tangent, so keep the incoming
        # side portable and non-semantic while retaining the stepped output.
        return "auto", "step"
    return "linear", "linear"


def _sampled_curve_type(cmds, control_source: Optional[str], target: str) -> str:
    """Choose a native scalar animCurve type for a newly sampled target."""
    if control_source:
        source_node = control_source.split(".", 1)[0]
        try:
            node_type = str(cmds.nodeType(source_node))
            if node_type.startswith("animCurve"):
                return node_type
        except Exception:
            pass
    attribute = target.rsplit(".", 1)[-1].lower()
    if "rotate" in attribute:
        return "animCurveTA"
    if attribute.endswith("enabled") or attribute.startswith("ik"):
        return "animCurveTU"
    return "animCurveTL"


def _copy_animation_curve(cmds, source: str, destination: str) -> None:
    """Copy an animCurve payload while preserving both node identities."""
    source_node = source.split(".", 1)[0]
    destination_node = destination.split(".", 1)[0]
    if not str(cmds.nodeType(source_node)).startswith("animCurve"):
        return
    if not str(cmds.nodeType(destination_node)).startswith("animCurve"):
        return
    try:
        try:
            interpolation = cmds.rotationInterpolation(source_node, query=True)
        except Exception:
            interpolation = None
        payload = _capture_animation_curve_payload(cmds, source_node)
        if not isinstance(payload, Mapping) or "times" not in payload:
            # Payload inspection is best-effort across Maya versions.  When a
            # tangent/infinity query is unavailable, copyKey/pasteKey remains
            # the authoritative fallback and must run before any destructive
            # destination clearing.
            cmds.copyKey(source_node, option="curve")
            cmds.pasteKey(destination_node, option="replaceCompletely")
            return
        capture_failed = bool(payload.get("captureFailed"))
        if not payload["times"]:
            # Clear Existing Motion may intentionally leave an empty but
            # UUID-stable controller curve for a role that has no active VMD
            # payload.  Maya's pasteKey rejects an empty clipboard; clear the
            # authored destination directly instead of treating that as a
            # failed bake.
            selection = om.MSelectionList()
            selection.add(destination_node)
            destination_fn = oma.MFnAnimCurve(selection.getDependNode(0))
            for index in reversed(range(destination_fn.numKeys)):
                destination_fn.remove(index)
            _restore_animation_curve_payload(cmds, destination_node, payload)
            return
        if capture_failed:
            # A non-empty ``times`` list proves that the source is keyed even
            # when a later tangent/infinity query failed.  Preserve the keyed
            # source through Maya's native clipboard path instead of trying to
            # interpret its incomplete metadata payload.
            cmds.copyKey(source_node, option="curve")
            cmds.pasteKey(destination_node, option="replaceCompletely")
            return
        # Paste through Maya's clipboard only as a last resort.  For native
        # scalar curves, rebuilding the destination from the captured payload
        # avoids Maya re-evaluating the source through the current transform
        # (which can alter values on a failed transaction, especially for
        # quaternion ``animCurveTA`` inputs).
        _clear_animation_curve_keys(cmds, destination_node)
        for key in payload.get("keys", ()):
            value = key.get("value")
            if value is None:
                continue
            cmds.setKeyframe(
                destination_node,
                time=float(key.get("time", 0.0)),
                value=float(value),
            )
        _restore_animation_curve_payload(cmds, destination_node, payload)
        if interpolation and interpolation != "none":
            try:
                cmds.rotationInterpolation(destination_node, convert=interpolation)
            except Exception:
                # Scalar curves do not all expose compound interpolation; the
                # key/tangent payload above remains the authoritative restore.
                pass
    except Exception as exc:
        raise MmdControlRigBuildError(
            f"could not copy control animation curve: {source} -> {destination}"
        ) from exc


def _capture_animation_curve_payload(cmds, node: str) -> Dict[str, Any]:
    """Capture key/tangent/infinity data for a native animCurve node."""
    def _scalar(value):
        if isinstance(value, (list, tuple)):
            return value[0] if value else None
        return value

    times = None
    try:
        times = [float(value) for value in (cmds.keyframe(node, query=True, timeChange=True) or [])]
        values = [float(value) for value in (cmds.keyframe(node, query=True, valueChange=True) or [])]
        payload = {"captureFailed": False, "times": times, "values": values, "keys": []}
        weighted = cmds.keyTangent(node, query=True, weightedTangents=True)
        if isinstance(weighted, (list, tuple)):
            weighted = weighted[0] if weighted else False
        payload["weightedTangents"] = bool(weighted)
        try:
            payload["preInfinite"] = _scalar(
                cmds.setInfinity(node, query=True, preInfinite=True)
            )
            payload["postInfinite"] = _scalar(
                cmds.setInfinity(node, query=True, postInfinite=True)
            )
        except Exception:
            pass
        for index, time in enumerate(times):
            query = {"time": (time, time), "query": True}
            key = {"time": time, "value": values[index] if index < len(values) else None}
            for option, name in (
                ("inTangentType", "inType"),
                ("outTangentType", "outType"),
                ("inAngle", "inAngle"),
                ("outAngle", "outAngle"),
                ("inWeight", "inWeight"),
                ("outWeight", "outWeight"),
            ):
                result = cmds.keyTangent(node, **query, **{option: True}) or []
                key[name] = _scalar(result)
            payload["keys"].append(key)
        return payload
    except Exception:
        # Keep the result mapping distinct from a successful empty curve.  The
        # caller will use copyKey/pasteKey when this best-effort payload query
        # is unavailable, rather than clearing a keyed destination.  A
        # successfully observed time list remains useful: ``[]`` proves that
        # the source is empty, while a non-empty list proves that it is keyed.
        payload = {"captureFailed": True}
        if times is not None:
            payload["times"] = times
        return payload


def _capture_animation_channel_snapshot(cmds, plug: str) -> Dict[str, Any]:
    """Capture one channel's incoming edges, curve payload, and value.

    VMD Control Rig transactions snapshot both controller plugs and ordinary
    model/camera/light plugs.  Keeping the Maya connection query in one
    primitive avoids the two paths drifting on conversion-node handling or on
    the distinction between a keyed animCurve and a plain value channel.
    """
    incoming = [
        str(source)
        for source in (
            cmds.listConnections(
                plug,
                source=True,
                destination=False,
                plugs=True,
                skipConversionNodes=False,
            )
            or []
        )
    ]
    incoming_nodes = [source.split(".", 1)[0] for source in incoming]
    curve_node = None
    curve_payload: Dict[str, Any] = {}
    if len(incoming_nodes) == 1 and str(cmds.nodeType(incoming_nodes[0])).startswith("animCurve"):
        curve_node = incoming_nodes[0]
        curve_payload = _capture_animation_curve_payload(cmds, curve_node)
    return {
        "incoming": incoming,
        "curve_node": curve_node,
        "curve_type": cmds.nodeType(curve_node) if curve_node else None,
        "curve_payload": curve_payload,
        "value": cmds.getAttr(plug),
    }


def _clear_animation_curve_keys(cmds, node: str) -> None:
    """Remove all keys while retaining the animCurve node and its UUID."""
    selection = om.MSelectionList()
    selection.add(node)
    curve_fn = oma.MFnAnimCurve(selection.getDependNode(0))
    for index in reversed(range(curve_fn.numKeys)):
        curve_fn.remove(index)


def _restore_animation_channel_snapshot(
    cmds,
    row: Mapping[str, Any],
    *,
    destination: str,
    recreate_curve: bool = False,
) -> None:
    """Restore one channel snapshot without replacing the original curve node.

    ``recreate_curve`` is used for scene channels whose original animCurve may
    have been deleted by the failed import.  Controller snapshots require the
    source to remain present and therefore fail closed when it is missing.
    """
    prior_sources = [str(source) for source in (row.get("incoming") or [])]
    current_sources = [
        str(source)
        for source in (
            cmds.listConnections(
                destination,
                source=True,
                destination=False,
                plugs=True,
                skipConversionNodes=False,
            )
            or []
        )
    ]
    for source in current_sources:
        if source in prior_sources:
            continue
        cmds.disconnectAttr(source, destination)
        source_node = source.split(".", 1)[0]
        if cmds.objExists(source_node) and str(cmds.nodeType(source_node)).startswith("animCurve"):
            cmds.delete(source_node)

    payload = row.get("curve_payload")
    payload_known = (
        isinstance(payload, Mapping)
        and "times" in payload
        and "keys" in payload
        and not payload.get("captureFailed")
    )
    curve_node = row.get("curve_node")
    curve_type = row.get("curve_type")
    if curve_node is None and prior_sources:
        source_node = prior_sources[0].split(".", 1)[0]
        if cmds.objExists(source_node) and str(cmds.nodeType(source_node)).startswith("animCurve"):
            curve_node = source_node
    if curve_node and not cmds.objExists(curve_node):
        if not recreate_curve or not curve_type:
            raise RuntimeError(f"original animation source is missing: {curve_node}")
        if not payload_known:
            raise RuntimeError(f"animation curve payload is unavailable: {curve_node}")
        curve_node = str(cmds.createNode(curve_type, name=str(curve_node).rsplit("|", 1)[-1]))
    for source in prior_sources:
        if not cmds.objExists(source):
            raise RuntimeError(f"original animation source is missing: {source}")
        if not cmds.isConnected(source, destination):
            cmds.connectAttr(source, destination, force=True)

    if curve_node and cmds.objExists(curve_node):
        if not payload_known:
            # A failed/partial query is deliberately not treated as an empty
            # curve.  Keep the existing keys when the node survived, while
            # still restoring its incoming connection above.
            return
        _clear_animation_curve_keys(cmds, curve_node)
        for key in payload.get("keys", ()):
            cmds.setKeyframe(
                curve_node,
                time=float(key.get("time", 0.0)),
                value=float(key.get("value", 0.0)),
            )
        _restore_animation_curve_payload(cmds, curve_node, payload)
    elif not prior_sources and row.get("value") is not None:
        value = row["value"]
        if (
            isinstance(value, (list, tuple))
            and len(value) == 1
            and isinstance(value[0], (list, tuple))
        ):
            value = value[0]
        if isinstance(value, (list, tuple)):
            cmds.setAttr(destination, *value)
        else:
            cmds.setAttr(destination, value)


def _restore_animation_curve_payload(cmds, node: str, payload: Mapping[str, Any]) -> None:
    """Reapply tangent and infinity metadata after Maya curve paste."""
    if not payload or payload.get("captureFailed"):
        return
    try:
        cmds.keyTangent(
            node,
            edit=True,
            weightedTangents=bool(payload.get("weightedTangents", False)),
        )
    except Exception:
        pass
    for key in payload.get("keys", ()):
        time = float(key.get("time", 0.0))
        tangent_types = {"time": (time, time), "edit": True}
        if key.get("inType") is not None:
            tangent_types["inTangentType"] = key["inType"]
        if key.get("outType") is not None:
            tangent_types["outTangentType"] = key["outType"]
        try:
            cmds.keyTangent(node, **tangent_types)
        except Exception:
            pass

        # Maya converts non-fixed tangents to ``fixed`` when angle/weight is
        # edited explicitly.  Reapply those values only for sides that were
        # originally fixed; linear/auto/step types derive them from the curve.
        fixed_values = {"time": (time, time), "edit": True}
        for tangent_type, angle_name, angle_option, weight_name, weight_option in (
            ("inType", "inAngle", "inAngle", "inWeight", "inWeight"),
            ("outType", "outAngle", "outAngle", "outWeight", "outWeight"),
        ):
            if key.get(tangent_type) != "fixed":
                continue
            if key.get(angle_name) is not None:
                fixed_values[angle_option] = key[angle_name]
            if key.get(weight_name) is not None:
                fixed_values[weight_option] = key[weight_name]
        if len(fixed_values) > 2:
            try:
                cmds.keyTangent(node, **fixed_values)
            except Exception:
                pass
    infinity_kwargs = {
        name: payload[name]
        for name in ("preInfinite", "postInfinite")
        if payload.get(name) is not None
    }
    if infinity_kwargs:
        try:
            cmds.setInfinity(node, edit=True, **infinity_kwargs)
        except Exception:
            pass


def _source_identity(cmds, source: str) -> Tuple[str, Optional[str]]:
    """Return a source plug plus its node UUID for topology comparisons."""

    source = str(source)
    node = source.split(".", 1)[0]
    try:
        uuids = cmds.ls(node, uuid=True) or []
    except Exception:
        uuids = []
    return source, str(uuids[0]) if len(uuids) == 1 else None


def _transaction_owned_sources(rows, curve_plugs, layer_journal) -> Set[str]:
    """Collect source plugs that an EDIT exit transaction may legitimately use."""

    sources = set()
    for row in rows or ():
        if not isinstance(row, Mapping):
            continue
        for key in ("source", "controlSource"):
            value = row.get(key)
            if value:
                sources.add(str(value))
    sources.update(str(plug) for plug in (curve_plugs or ()) if plug)
    for route in (layer_journal or {}).get("routes", {}).values():
        if not isinstance(route, Mapping):
            continue
        for key in ("curve", "source", "controlSource"):
            value = route.get(key)
            if value:
                sources.add(str(value))
    return sources


def _assert_created_curve_nodes_safe(cmds, nodes, transaction_plugs) -> None:
    """Refuse to delete a transaction curve carrying a foreign output edge."""

    # ``listConnections`` often returns a short DAG path while journal rows
    # persist full paths.  Compare canonical plugs so a legitimate output
    # does not look foreign during sampled-route rollback.
    transaction_plugs = {
        _canonical_plug(cmds, str(plug)) for plug in (transaction_plugs or ())
    }
    owned_nodes = {
        _canonical_node_name(cmds, str(node))
        for node in (nodes or ())
        if node and cmds.objExists(node)
    }
    for node in nodes or ():
        if not node or not cmds.objExists(node):
            continue
        destinations = cmds.listConnections(
            node,
            source=False,
            destination=True,
            plugs=True,
        ) or []
        foreign = []
        for destination in destinations:
            canonical_destination = _canonical_plug(cmds, str(destination))
            destination_node = canonical_destination.split(".", 1)[0]
            if (
                canonical_destination not in transaction_plugs
                and destination_node not in owned_nodes
            ):
                foreign.append(str(destination))
        if foreign:
            raise MmdControlRigBuildError(
                f"control-rig transaction topology drift on {node}: "
                f"foreign destinations {foreign}"
            )


def _capture_plug_states(cmds, plugs) -> Dict[str, Dict[str, Any]]:
    states = {}
    for plug in sorted(set(plugs)):
        incoming = list(
            cmds.listConnections(
                plug, source=True, destination=False, plugs=True
            )
            or []
        )
        states[plug] = {
            "incoming": incoming,
            "incomingIdentity": tuple(_source_identity(cmds, source) for source in incoming),
            "type": str(cmds.getAttr(plug, type=True)),
            "value": cmds.getAttr(plug),
        }
    return states


def _restore_plug_states(
    cmds,
    states: Mapping[str, Mapping[str, Any]],
    *,
    allowed_sources=(),
    owned_nodes=(),
) -> None:
    """Restore plugs only when no unknown writer appeared during the transaction."""

    allowed_sources = {str(source) for source in (allowed_sources or ()) if source}
    owned_nodes = {str(node) for node in (owned_nodes or ()) if node}
    for plug, state in states.items():
        prior_sources = [str(source) for source in (state.get("incoming") or ())]
        prior_identity = {
            source: identity
            for source, identity in zip(
                prior_sources,
                state.get("incomingIdentity") or (),
            )
        }
        current_sources = [
            str(source)
            for source in (
                cmds.listConnections(
                    plug, source=True, destination=False, plugs=True
                )
                or []
            )
        ]
        for source in current_sources:
            if source in prior_sources:
                expected = prior_identity.get(source)
                actual = _source_identity(cmds, source)
                if expected and expected[1] and actual[1] and expected[1] != actual[1]:
                    raise MmdControlRigBuildError(
                        f"control-rig transaction topology drift on {plug}: "
                        f"source identity changed for {source}"
                    )
                continue
            source_node = source.split(".", 1)[0]
            if source in allowed_sources or source_node in owned_nodes:
                continue
            raise MmdControlRigBuildError(
                f"control-rig transaction topology drift on {plug}: "
                f"foreign writer {source}"
            )

    for plug in states:
        for source in cmds.listConnections(
            plug, source=True, destination=False, plugs=True
        ) or []:
            cmds.disconnectAttr(source, plug)
    for plug, state in states.items():
        incoming = state["incoming"]
        if not incoming:
            _set_plug_value(cmds, plug, state["value"], state["type"])
        for source in incoming:
            if not cmds.isConnected(source, plug):
                cmds.connectAttr(source, plug, force=False)


def _set_plug_value(cmds, plug: str, value: Any, attr_type: str) -> None:
    if attr_type == "matrix":
        cmds.setAttr(plug, *list(value), type="matrix")
    elif attr_type == "bool":
        cmds.setAttr(plug, bool(value))
    else:
        cmds.setAttr(plug, value)


def _resolve_uuid(cmds, uuid: str) -> str:
    nodes = cmds.ls(uuid, long=True) or []
    if len(nodes) != 1:
        raise MmdControlRigBuildError(f"owned control-rig node is missing: {uuid}")
    return str(nodes[0])


def _canonical_node(cmds, node: str) -> str:
    nodes = cmds.ls(node, long=True) or []
    if len(nodes) != 1:
        raise MmdControlRigBuildError(f"expected one scene node: {node}")
    return str(nodes[0])


def _write_metadata(cmds, root: str, metadata: Mapping[str, Any]) -> None:
    cmds.setAttr(
        f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}",
        json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        type="string",
    )


def _raw_metadata(cmds, root: str) -> Optional[str]:
    """Read the persisted metadata without legacy owner normalization."""
    if not cmds.attributeQuery(ATTR_MMD_CONTROL_RIG_JSON, node=root, exists=True):
        return None
    return cmds.getAttr(f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}")


def _restore_raw_metadata(cmds, root: str, raw: Optional[str]) -> None:
    """Restore the exact metadata payload that preceded a transition."""
    plug = f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}"
    if raw is None:
        if cmds.attributeQuery(ATTR_MMD_CONTROL_RIG_JSON, node=root, exists=True):
            cmds.deleteAttr(plug)
        return
    if not cmds.attributeQuery(ATTR_MMD_CONTROL_RIG_JSON, node=root, exists=True):
        cmds.addAttr(root, longName=ATTR_MMD_CONTROL_RIG_JSON, dataType="string")
    cmds.setAttr(plug, raw, type="string")


@contextmanager
def _undo_chunk(cmds, label: str):
    cmds.undoInfo(openChunk=True, chunkName=label)
    try:
        yield
    finally:
        cmds.undoInfo(closeChunk=True)
