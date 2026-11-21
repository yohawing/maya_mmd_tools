"""Bake Control Rig rotation channel groups and preserve their interpolation."""

from __future__ import annotations
import math
from typing import Any, Dict, List, Mapping, Optional, Tuple

from mmd_tools.core.animation_curve_snapshot import (
    clear_animation_curve_keys as _clear_animation_curve_keys,
    copy_animation_curve as _copy_animation_curve,
    set_sampled_curve_tangents as _set_sampled_curve_tangents,
)
from mmd_tools.core.mmd_control_rig_builder import MmdControlRigBuildError
from mmd_tools.core.mmd_control_rig_basis import (
    MmdControlRigBasis,
    MmdControlRigBasisError,
    control_to_bone,
    euler_degrees_from_quaternion,
    quaternion_from_euler_degrees,
    validate_basis_record,
)


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


def _supports_live_authoring_basis(row: Mapping[str, Any]) -> bool:
    """Allow complete transform-like XYZ routes while excluding special writers."""
    if row.get("routeClass", ROUTE_SAME_BASIS) == ROUTE_SAME_BASIS:
        return True
    return _supports_bake_authoring_basis(row, exclude_rotate_order=True)


def _supports_bake_authoring_basis(
    row: Mapping[str, Any], *, exclude_rotate_order: bool = False
) -> bool:
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
    if exclude_rotate_order:
        special_reasons.add("rotate_order")
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


def _union_key_times(cmds, plugs) -> List[float]:
    """Return sorted unique key times from the nodes of the supplied plugs."""
    times = set()
    for plug in plugs:
        if plug:
            node = str(plug).split(".", 1)[0]
            keys = cmds.keyframe(node, query=True, timeChange=True) or []
            times.update(float(time) for time in keys)
    return sorted(times)


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
    times = _union_key_times(cmds, (*sources, *mmd_sources))
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


def _create_keyed_curve(cmds, curve_type, keys, created_curve_nodes, failure_message) -> str:
    """Register a new curve before writing its (time, value) keys."""
    node = None
    try:
        node = str(cmds.createNode(curve_type))
        if created_curve_nodes is not None:
            created_curve_nodes.append(node)
        for time, value in keys:
            cmds.setKeyframe(node, time=(time, time), value=value)
    except Exception as exc:
        if node and cmds.objExists(node):
            try:
                cmds.delete(node)
            except Exception:
                pass
        raise MmdControlRigBuildError(failure_message) from exc
    return f"{node}.output"


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
    times = _union_key_times(cmds, (*sources, *mmd_sources))
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
            for time, values in samples:
                cmds.setKeyframe(curve, time=(time, time), value=float(values[index]))
        else:
            keys = ((time, float(values[index])) for time, values in samples)
            source = _create_keyed_curve(
                cmds, "animCurveTA", keys, created_curve_nodes,
                f"could not create sampled rotation curve: {target}",
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
    times = _union_key_times(cmds, (*sources, *mmd_sources))
    if not times:
        try:
            times = [float(cmds.currentTime(query=True))]
        except Exception:
            times = [0.0]
    elif not quaternion_interpolation or not _rotation_group_uses_quaternion(cmds, rows, control_sources):
        times = _dense_sample_times(times, frame_range)
    elif frame_range is not None:
        start, end = _normalize_dense_frame_range(frame_range)
        times = [time for time in times if start <= time <= end]
        if not times:
            times = [start, end]
    control_samples = []
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
        control_samples.append((time, tuple(values)))
        control_quaternion = quaternion_from_euler_degrees(
            values,
            rotate_order=control_rotate_order,
        )
        bone_quaternion = control_to_bone(control_quaternion, basis)
        samples.append(
            (
                time,
                euler_degrees_from_quaternion(
                    bone_quaternion,
                    rotate_order=target_rotate_order,
                ),
            )
        )

    # A newly authored static controller has no detached control curve yet.
    # Persist its basis-space XYZ values before replacing the live converter
    # with bone-space curves; otherwise the next EDIT would duplicate the
    # converted bone curves and apply the authoring basis a second time.
    for index, (axis, row) in enumerate(
        zip(attrs, (rows_by_attr[attr] for attr in attrs))
    ):
        if sources[index]:
            continue
        keys = ((time, float(values[index])) for time, values in control_samples)
        source = _create_keyed_curve(
            cmds, "animCurveTA", keys, created_curve_nodes,
            f"could not retain basis-space control rotation curve: {row['control']}",
        )
        sources[index] = source
        row["controlSource"] = source
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
        message = f"could not create basis-converted MMD rotation curve: {target_plug}"
        keys = ((time, float(values[attrs.index(axis)])) for time, values in samples)
        source = _create_keyed_curve(cmds, "animCurveTA", keys, created_curve_nodes, message)
        try:
            cmds.connectAttr(source, target_plug, force=False)
        except Exception as exc:
            raise MmdControlRigBuildError(message) from exc
        result[control_plug] = source
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
