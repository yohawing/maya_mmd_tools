"""Shared VMD Bezier tangent helpers for animation conversion."""

import math
from typing import Dict, List, Optional, Tuple

import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
import maya.cmds as cmds

from . import vmd_profile
from .vmd_bone_interpolation import get_frame_interpolation, get_frame_number, is_linear_vmd_interp


def query_key_value(logger, plug: str, frame_number: float) -> Optional[float]:
    """Return the keyed value for a plug/frame pair, or None when unavailable."""
    try:
        values = cmds.keyframe(
            plug,
            query=True,
            time=(frame_number, frame_number),
            valueChange=True,
        )
    except Exception as exc:
        logger.debug(f"Failed to query key value for {plug} at {frame_number}: {exc}")
        return None
    if not values:
        return None
    return float(values[0])


def _unlock_anim_curve_tangent(logger, plug: str, frame_time: float) -> None:
    """Unlock in/out tangents for the animCurve key at frame_time."""
    try:
        curves = cmds.listConnections(plug, source=True, destination=False, type="animCurve") or []
        if not curves:
            return
        selection = om.MSelectionList()
        selection.add(curves[0])
        curve = oma.MFnAnimCurve(selection.getDependNode(0))
        times = cmds.keyframe(plug, query=True, time=(frame_time, frame_time), indexValue=True) or []
        if not times:
            return
        curve.setTangentsLocked(int(times[0]), False)
    except Exception as exc:
        logger.debug(f"Failed to unlock tangent for {plug} at {frame_time}: {exc}")


def _anim_curve_for_plug(converter, plug: str) -> Optional[oma.MFnAnimCurve]:
    """Resolve the animCurve that should receive tangent edits for *plug*."""
    try:
        curves = cmds.listConnections(plug, source=True, destination=False, type="animCurve") or []
        if curves:
            selection = om.MSelectionList()
            selection.add(curves[0])
            return oma.MFnAnimCurve(selection.getDependNode(0))

        animation_layer = converter.anim_layer if converter.use_animation_layers and converter.anim_layer else None
        if not animation_layer or not cmds.animLayer(animation_layer, query=True, exists=True):
            return None

        layer_curves = set(cmds.animLayer(animation_layer, query=True, animCurves=True) or [])
        attr = plug.rsplit(".", 1)[-1]
        axis = attr[-1] if attr and attr[-1] in "XYZ" else ""
        blend_nodes = cmds.listConnections(plug, source=True, destination=False) or []
        for blend_node in blend_nodes:
            candidate_inputs = []
            if axis:
                candidate_inputs.extend((f"{blend_node}.inputB{axis}", f"{blend_node}.inputB.inputB{axis}"))
            candidate_inputs.append(f"{blend_node}.inputB")
            for input_plug in candidate_inputs:
                if not cmds.objExists(input_plug):
                    continue
                input_curves = cmds.listConnections(input_plug, source=True, type="animCurve") or []
                for curve_name in input_curves:
                    if curve_name not in layer_curves:
                        continue
                    selection = om.MSelectionList()
                    selection.add(curve_name)
                    return oma.MFnAnimCurve(selection.getDependNode(0))

            if axis and cmds.nodeType(blend_node) == "animBlendNodeAdditiveRotation":
                continue

            input_curves = cmds.listConnections(blend_node, source=True, type="animCurve") or []
            for curve_name in input_curves:
                if curve_name not in layer_curves:
                    continue
                selection = om.MSelectionList()
                selection.add(curve_name)
                return oma.MFnAnimCurve(selection.getDependNode(0))
    except Exception as exc:
        converter.logger.debug(f"Failed to resolve animCurve for {plug}: {exc}")
    return None


def _time_key_index(curve: oma.MFnAnimCurve, frame_time: float) -> Optional[int]:
    try:
        index = curve.find(om.MTime(float(frame_time), om.MTime.uiUnit()))
    except Exception:
        return None
    if index is None or int(index) < 0:
        return None
    return int(index)


def _apply_api_tangent(
    curve: oma.MFnAnimCurve,
    key_index: int,
    angle_degrees: float,
    weight: float,
    *,
    in_tangent: bool,
) -> None:
    curve.setIsWeighted(True)
    curve.setTangentsLocked(key_index, False)
    curve.setWeightsLocked(key_index, False)
    if in_tangent:
        curve.setInTangentType(key_index, oma.MFnAnimCurve.kTangentFixed)
    else:
        curve.setOutTangentType(key_index, oma.MFnAnimCurve.kTangentFixed)
    curve.setTangent(key_index, om.MAngle(math.radians(angle_degrees)), float(weight), in_tangent)


def _apply_cmds_tangent(
    converter,
    plug: str,
    frame_number: float,
    next_frame_number: float,
    frame_time: float,
    next_frame_time: float,
    out_angle: float,
    out_weight: float,
    in_angle: float,
    in_weight: float,
) -> None:
    try:
        _unlock_anim_curve_tangent(converter.logger, plug, frame_time)
        _unlock_anim_curve_tangent(converter.logger, plug, next_frame_time)
        cmds.keyTangent(
            plug,
            edit=True,
            time=(frame_time, frame_time),
            weightedTangents=True,
            lock=False,
            weightLock=False,
        )
        cmds.keyTangent(
            plug,
            edit=True,
            time=(frame_time, frame_time),
            ott="fixed",
        )
        cmds.keyTangent(
            plug,
            edit=True,
            time=(frame_time, frame_time),
            oa=out_angle,
            ow=out_weight,
        )
        cmds.keyTangent(
            plug,
            edit=True,
            time=(next_frame_time, next_frame_time),
            weightedTangents=True,
            lock=False,
            weightLock=False,
        )
        cmds.keyTangent(
            plug,
            edit=True,
            time=(next_frame_time, next_frame_time),
            itt="fixed",
        )
        cmds.keyTangent(
            plug,
            edit=True,
            time=(next_frame_time, next_frame_time),
            ia=in_angle,
            iw=in_weight,
        )
    except Exception as exc:
        converter.logger.debug(
            f"Failed to apply VMD Bezier tangent for {plug} "
            f"{frame_number}->{next_frame_number}: {exc}"
        )


def apply_vmd_bezier_tangents(
    converter,
    joint: str,
    frames: List,
    attrs,
    channel_interp_map: Dict[str, str],
    interpolation_parser=None,
) -> None:
    """Apply VMD Bezier interpolation as Maya weighted tangents."""
    if len(frames) < 2:
        return

    if isinstance(attrs, dict):
        attr_targets = attrs
        source_attrs = list(attrs.keys())
    else:
        attr_targets = {attr: (joint, attr) for attr in attrs}
        source_attrs = list(attrs)

    curves_by_attr: Dict[str, Optional[oma.MFnAnimCurve]] = {}
    for source_attr in source_attrs:
        target_node, target_attr = attr_targets.get(source_attr, (joint, source_attr))
        curves_by_attr[source_attr] = _anim_curve_for_plug(converter, f"{target_node}.{target_attr}")

    for frame_index in range(len(frames) - 1):
        frame = frames[frame_index]
        next_frame = frames[frame_index + 1]
        frame_number = get_frame_number(frame)
        next_frame_number = get_frame_number(next_frame)
        frame_time = converter.vmd_frame_to_maya_time(frame_number)
        next_frame_time = converter.vmd_frame_to_maya_time(next_frame_number)
        dt = next_frame_time - frame_time
        if dt <= 0.0:
            continue

        # VMD interpolation bytes are stored on the arriving key.
        parse_interpolation = interpolation_parser or converter._parse_vmd_interpolation
        interpolation = parse_interpolation(get_frame_interpolation(next_frame))
        if not interpolation:
            continue

        for source_attr in source_attrs:
            channel_name = channel_interp_map.get(source_attr)
            if not channel_name:
                continue
            points: Optional[Tuple[float, float, float, float]] = interpolation.get(channel_name)
            if not points or is_linear_vmd_interp(points):
                continue

            target_node, target_attr = attr_targets.get(source_attr, (joint, source_attr))
            plug = f"{target_node}.{target_attr}"
            curve = curves_by_attr.get(source_attr)
            key_index = _time_key_index(curve, frame_time) if curve is not None else None
            next_key_index = _time_key_index(curve, next_frame_time) if curve is not None else None
            if curve is not None and key_index is not None and next_key_index is not None:
                try:
                    value = float(curve.value(key_index))
                    next_value = float(curve.value(next_key_index))
                except Exception:
                    value = query_key_value(converter.logger, plug, frame_time)
                    next_value = query_key_value(converter.logger, plug, next_frame_time)
            else:
                value = query_key_value(converter.logger, plug, frame_time)
                next_value = query_key_value(converter.logger, plug, next_frame_time)
            if value is None or next_value is None:
                continue

            x1, y1, x2, y2 = points
            dv = next_value - value
            out_dx = dt * x1
            out_dy = dv * y1
            in_dx = dt * (1.0 - x2)
            in_dy = dv * (1.0 - y2)
            out_angle = math.degrees(math.atan2(out_dy, out_dx))
            in_angle = math.degrees(math.atan2(in_dy, in_dx))
            out_weight = math.sqrt((out_dx * out_dx) + (out_dy * out_dy))
            in_weight = math.sqrt((in_dx * in_dx) + (in_dy * in_dy))

            if curve is not None and key_index is not None and next_key_index is not None:
                try:
                    _apply_api_tangent(curve, key_index, out_angle, out_weight, in_tangent=False)
                    _apply_api_tangent(curve, next_key_index, in_angle, in_weight, in_tangent=True)
                    vmd_profile.add_count("api_tangent_segments")
                    continue
                except Exception as exc:
                    converter.logger.debug(f"API tangent edit failed for {plug}; falling back to cmds: {exc}")

            _apply_cmds_tangent(
                converter,
                plug,
                frame_number,
                next_frame_number,
                frame_time,
                next_frame_time,
                out_angle,
                out_weight,
                in_angle,
                in_weight,
            )
            vmd_profile.add_count("cmds_tangent_segments")
