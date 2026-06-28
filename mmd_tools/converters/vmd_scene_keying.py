"""Scene keying helpers shared by VMD animation conversion paths."""

import math
from typing import Dict, List, Optional, Tuple

import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
import maya.cmds as cmds

from ..core import maya_utils


def batch_create_and_key_curves(
    converter,
    joint_name: str,
    channel_samples: Dict[str, List[Tuple[float, float]]],
) -> bool:
    """Create anim curves and add keyed channel samples through Maya API 2.0."""
    if not cmds.objExists(joint_name) or not channel_samples:
        return False
    attrs = list(channel_samples.keys())
    curves: Dict[str, oma.MFnAnimCurve] = {}
    try:
        curves = maya_utils.create_animation_curves(
            joint_name,
            attrs,
            tangent_type=oma.MFnAnimCurve.kTangentLinear,
            animation_layer=None,
        )
    except Exception as e:
        converter.logger.debug(f"create_animation_curves failed for {joint_name}: {e}")
        curves = {}

    tangent = oma.MFnAnimCurve.kTangentLinear
    shared_times = None
    if channel_samples:
        first_samples = next((samples for samples in channel_samples.values() if samples), None)
        if first_samples:
            try:
                shared_times = om.MTimeArray()
                for frame, _ in first_samples:
                    shared_times.append(om.MTime(float(frame), om.MTime.uiUnit()))
            except Exception:
                shared_times = None

    success_any = False
    for attr, samples in channel_samples.items():
        if not samples:
            continue
        used_api = False
        if attr in curves:
            curve = curves[attr]
            try:
                times = shared_times
                vals = om.MDoubleArray()
                for frame, val in samples:
                    vals.append(float(val))
                if times is None or len(times) != len(vals):
                    times = om.MTimeArray()
                    for frame, _ in samples:
                        times.append(om.MTime(float(frame), om.MTime.uiUnit()))
                curve.addKeys(times, vals, tangent, tangent, False)
                used_api = True
                success_any = True
                continue
            except Exception as e:
                converter.logger.debug(f"addKeys failed for {joint_name}.{attr}, fallback: {e}")
        for frame, val in samples:
            try:
                cmd_val = math.degrees(val) if "rotate" in attr else val
                cmds.setKeyframe(joint_name, attribute=attr, time=frame, value=cmd_val)
                success_any = True
            except Exception:
                pass
        if not used_api:
            converter.logger.debug(f"Used cmds.setKeyframe fallback for {joint_name}.{attr}")
    return success_any


def batch_create_and_key_curve_arrays(
    converter,
    joint_name: str,
    channel_values: Dict[str, Optional[om.MDoubleArray]],
    static_state: Dict[str, dict],
    times: om.MTimeArray,
    frame_numbers: List[float],
) -> Tuple[int, int]:
    """Create anim curves and add collected MDoubleArray channel values."""
    if not cmds.objExists(joint_name) or not channel_values:
        return 0, 0

    dynamic_attrs = []
    skipped_static = 0
    for attr, values in channel_values.items():
        state = static_state.get(attr, {})
        if state.get("is_static", False):
            skipped_static += 1
            if state.get("first") is not None:
                try:
                    value = float(state["first"])
                    if "rotate" in attr:
                        value = math.degrees(value)
                    cmds.setAttr(f"{joint_name}.{attr}", value)
                except Exception:
                    pass
            continue
        if values is None or len(values) != len(times):
            continue
        dynamic_attrs.append(attr)

    if not dynamic_attrs:
        return 0, skipped_static

    try:
        curves = maya_utils.create_animation_curves(
            joint_name,
            dynamic_attrs,
            tangent_type=oma.MFnAnimCurve.kTangentLinear,
            animation_layer=None,
        )
    except Exception as e:
        converter.logger.debug(f"create_animation_curves failed for {joint_name}: {e}")
        curves = {}

    tangent = oma.MFnAnimCurve.kTangentLinear
    keyed = 0
    for attr in dynamic_attrs:
        values = channel_values[attr]
        curve = curves.get(attr)
        if curve:
            try:
                curve.addKeys(times, values, tangent, tangent, False)
                keyed += 1
                continue
            except Exception as e:
                converter.logger.debug(f"addKeys failed for {joint_name}.{attr}, fallback: {e}")

        for index, frame in enumerate(frame_numbers):
            try:
                value = float(values[index])
                if "rotate" in attr:
                    value = math.degrees(value)
                cmds.setKeyframe(joint_name, attribute=attr, time=frame, value=value)
            except Exception:
                pass
        keyed += 1

    return keyed, skipped_static


def batch_key_scalar_channels(
    converter,
    node_name: str,
    channel_samples: Dict[str, List[Tuple[float, float]]],
    animation_layer: Optional[str] = None,
) -> bool:
    """Key Maya UI scalar channels with MFnAnimCurve.addKeys and cmd fallback."""
    if not cmds.objExists(node_name) or not channel_samples:
        return False

    attrs = [attr for attr, samples in channel_samples.items() if samples]
    if not attrs:
        return False

    curves: Dict[str, oma.MFnAnimCurve] = {}
    try:
        curves = maya_utils.create_animation_curves(
            node_name,
            attrs,
            tangent_type=oma.MFnAnimCurve.kTangentLinear,
            animation_layer=animation_layer,
        )
    except Exception as exc:
        converter.logger.debug(f"create_animation_curves failed for {node_name}: {exc}")

    tangent = oma.MFnAnimCurve.kTangentLinear
    success_any = False
    for attr in attrs:
        samples = channel_samples[attr]
        curve = curves.get(attr)
        if curve:
            try:
                times = om.MTimeArray()
                values = om.MDoubleArray()
                for frame, value in samples:
                    times.append(om.MTime(float(frame), om.MTime.uiUnit()))
                    api_value = math.radians(float(value)) if "rotate" in attr else float(value)
                    values.append(api_value)
                curve.addKeys(times, values, tangent, tangent, False)
                success_any = True
                continue
            except Exception as exc:
                converter.logger.debug(f"addKeys failed for {node_name}.{attr}, fallback: {exc}")

        for frame, value in samples:
            try:
                key_args = {
                    "attribute": attr,
                    "time": frame,
                    "value": float(value),
                }
                if animation_layer:
                    key_args["animLayer"] = animation_layer
                cmds.setKeyframe(node_name, **key_args)
                success_any = True
            except Exception as exc:
                converter.logger.debug(f"setKeyframe fallback failed for {node_name}.{attr} at {frame}: {exc}")

    return success_any


def samples_as_anim_layer_deltas(
    node_name: str,
    channel_samples: Dict[str, List[Tuple[float, float]]],
):
    """Convert absolute channel samples to additive animLayer deltas."""
    adjusted = {}
    for attr, samples in channel_samples.items():
        if not samples:
            adjusted[attr] = samples
            continue
        try:
            base_value = cmds.getAttr(f"{node_name}.{attr}")
            if isinstance(base_value, (list, tuple)):
                base_value = base_value[0]
            if isinstance(base_value, (list, tuple)):
                base_value = base_value[0]
            base_value = float(base_value)
        except Exception:
            base_value = 0.0
        adjusted[attr] = [(frame, float(value) - base_value) for frame, value in samples]
    return adjusted
