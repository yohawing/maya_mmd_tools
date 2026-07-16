"""Scene keying helpers shared by VMD animation conversion paths."""

import math
from typing import Any, Dict, List, Optional, Tuple

import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
import maya.cmds as cmds

from ..core import maya_animation_utils
from . import vmd_profile
from .vmd_context import VmdKeyingContext


class VmdKeyingError(RuntimeError):
    """Raised when API keying cannot proceed and slow fallback is disabled."""


def _layer_base_value(node_name: str, attr: str) -> float:
    """Return the current base value in the units expected by API anim curves."""
    try:
        value = cmds.getAttr(f"{node_name}.{attr}")
        if isinstance(value, (list, tuple)):
            value = value[0]
        if isinstance(value, (list, tuple)):
            value = value[0]
        value = float(value)
    except Exception:
        return 0.0
    return math.radians(value) if "rotate" in attr.lower() else value


def _base_ui_value_at_frame(node_name: str, attr: str, frame: float) -> float:
    """Return the target plug's base UI value at *frame* for setKeyframe fallback."""
    try:
        value = cmds.getAttr(f"{node_name}.{attr}", time=float(frame))
        if isinstance(value, (list, tuple)):
            value = value[0]
        if isinstance(value, (list, tuple)):
            value = value[0]
        return float(value)
    except Exception:
        return 0.0


def _layer_base_ui_value(node_name: str, attr: str) -> float:
    """Return the current base value in Maya UI units for additive layer deltas."""
    try:
        value = cmds.getAttr(f"{node_name}.{attr}")
        if isinstance(value, (list, tuple)):
            value = value[0]
        if isinstance(value, (list, tuple)):
            value = value[0]
        return float(value)
    except Exception:
        return 0.0


def _as_layer_delta(node_name: str, attr: str, value: float) -> float:
    """Convert an absolute sample to an additive animation-layer delta."""
    return float(value) - _layer_base_value(node_name, attr)


def _anim_curve_fn(curve_name: str) -> oma.MFnAnimCurve:
    """Return an anim curve function set for an existing curve node."""
    selection = om.MSelectionList()
    selection.add(curve_name)
    return oma.MFnAnimCurve(selection.getDependNode(0))


def _create_anim_curve_for_plug(plug_path: str) -> oma.MFnAnimCurve:
    """Reuse or create an anim curve for plugs not handled by attr-name helpers."""
    existing = cmds.listConnections(
        plug_path,
        source=True,
        destination=False,
        type="animCurve",
    ) or []
    if len(existing) == 1:
        return _anim_curve_fn(existing[0])
    if len(existing) > 1:
        raise VmdKeyingError(f"Multiple direct anim curves drive {plug_path}: {existing!r}")
    selection = om.MSelectionList()
    selection.add(plug_path)
    plug = selection.getPlug(0)
    curve = oma.MFnAnimCurve()
    curve.create(plug)
    return curve


def _curve_candidates_for_attr(
    node_name: str,
    attr: str,
    animation_layer: Optional[str],
) -> dict[str, Any]:
    """Collect candidate curve/network names for diagnostic keying errors."""
    plug = f"{node_name}.{attr}"
    candidates: dict[str, Any] = {
        "plug": plug,
        "animation_layer": animation_layer,
        "direct_anim_curves": [],
        "source_plugs": [],
        "blend_nodes": [],
        "layer_anim_curves": [],
        "blend_input_curves": {},
    }
    try:
        candidates["direct_anim_curves"] = cmds.listConnections(
            plug,
            source=True,
            destination=False,
            type="animCurve",
        ) or []
        source_plugs = cmds.listConnections(
            plug,
            source=True,
            destination=False,
            plugs=True,
        ) or []
        candidates["source_plugs"] = source_plugs
        blend_nodes = []
        for source_plug in source_plugs:
            if "." in source_plug:
                blend_nodes.append(source_plug.rsplit(".", 1)[0])
        blend_nodes.extend(cmds.listConnections(plug, source=True, destination=False) or [])
        blend_nodes = sorted(set(blend_nodes))
        candidates["blend_nodes"] = blend_nodes
        if animation_layer and cmds.objExists(animation_layer):
            candidates["layer_anim_curves"] = cmds.animLayer(animation_layer, query=True, animCurves=True) or []
        for blend_node in blend_nodes:
            input_curves = cmds.listConnections(
                blend_node,
                source=True,
                destination=False,
                type="animCurve",
            ) or []
            candidates["blend_input_curves"][blend_node] = input_curves
    except Exception as exc:
        candidates["candidate_error"] = str(exc)
    return candidates


def _keying_error(
    node_name: str,
    attr: str,
    animation_layer: Optional[str],
    reason: str,
) -> VmdKeyingError:
    candidates = _curve_candidates_for_attr(node_name, attr, animation_layer)
    return VmdKeyingError(
        "VMD API keying failed and cmds.setKeyframe fallback is disabled. "
        f"node_attr={node_name}.{attr}; animation_layer={animation_layer}; "
        f"reason={reason}; curve_candidates={candidates}"
    )


def _ensure_fallback_allowed(
    node_name: str,
    attr: str,
    animation_layer: Optional[str],
    reason: str,
) -> None:
    if not vmd_profile.allow_setkeyframe_fallback():
        raise _keying_error(node_name, attr, animation_layer, reason)


def _anim_layer_curve_for_plug(plug: str, animation_layer: str) -> Optional[oma.MFnAnimCurve]:
    """Resolve one generated animLayer curve through Maya's official API."""
    with vmd_profile.scope("curve_resolve", count=1):
        resolved = cmds.animLayer(
            animation_layer,
            query=True,
            findCurveForPlug=plug,
        )
    if isinstance(resolved, (list, tuple)):
        if len(resolved) != 1:
            return None
        resolved = resolved[0]
    if not isinstance(resolved, str) or not resolved:
        return None
    return _anim_curve_fn(resolved)


def _find_layer_anim_curve(node_name: str, attr: str, animation_layer: str) -> Optional[oma.MFnAnimCurve]:
    """Find Maya's generated animLayer curve for a target plug."""
    return _anim_layer_curve_for_plug(f"{node_name}.{attr}", animation_layer)


def _ensure_layer_anim_curve(
    context: VmdKeyingContext,
    node_name: str,
    attr: str,
    samples: List[Tuple[float, float]],
    animation_layer: str,
) -> Optional[oma.MFnAnimCurve]:
    """Ensure Maya has built the animLayer blend network, then return its curve."""
    plug = f"{node_name}.{attr}"
    try:
        cmds.animLayer(animation_layer, edit=True, attribute=plug)
    except Exception as exc:
        context.logger.debug(f"animLayer attribute registration failed for {plug}: {exc}")

    curve = _find_layer_anim_curve(node_name, attr, animation_layer)
    if curve is not None:
        return curve

    if not samples:
        return None

    try:
        seed_frame = float(samples[0][0])
        with vmd_profile.scope("animLayer_seed_setKeyframe"):
            cmds.setKeyframe(
                node_name,
                attribute=attr,
                time=seed_frame,
                animLayer=animation_layer,
            )
    except Exception as exc:
        context.logger.debug(f"animLayer seed key failed for {plug}: {exc}")
        return None

    curve = _find_layer_anim_curve(node_name, attr, animation_layer)
    if curve is None:
        context.logger.debug(f"Could not locate animLayer curve for {plug} on {animation_layer}")
    return curve


def _create_scalar_channel_curves(
    context: VmdKeyingContext,
    node_name: str,
    attrs: List[str],
    channel_samples: Dict[str, List[Tuple[float, float]]],
    animation_layer: Optional[str],
) -> Dict[str, oma.MFnAnimCurve]:
    """Create or locate scalar anim curves for direct API key insertion."""
    if not animation_layer:
        simple_attrs = [attr for attr in attrs if "[" not in attr and "." not in attr]
        complex_attrs = [attr for attr in attrs if attr not in simple_attrs]
        curves: Dict[str, oma.MFnAnimCurve] = {}
        if simple_attrs:
            with vmd_profile.scope("curve_setup", count=len(simple_attrs)):
                curves.update(
                    maya_animation_utils.create_animation_curves(
                        node_name,
                        simple_attrs,
                        tangent_type=oma.MFnAnimCurve.kTangentLinear,
                        animation_layer=None,
                    )
                )
        if complex_attrs:
            with vmd_profile.scope("curve_setup", count=len(complex_attrs)):
                for attr in complex_attrs:
                    curves[attr] = _create_anim_curve_for_plug(f"{node_name}.{attr}")
        return curves

    curves: Dict[str, oma.MFnAnimCurve] = {}
    with vmd_profile.scope("animLayer_curve_setup", count=len(attrs)):
        for attr in attrs:
            curve = _ensure_layer_anim_curve(context, node_name, attr, channel_samples[attr], animation_layer)
            if curve is not None:
                curves[attr] = curve
        refreshed_curves: Dict[str, oma.MFnAnimCurve] = {}
        for attr in attrs:
            curve = _find_layer_anim_curve(node_name, attr, animation_layer)
            if curve is not None:
                refreshed_curves[attr] = curve
        curves = refreshed_curves
        cleared = set()
        for attr, curve in curves.items():
            curve_name = curve.name()
            if curve_name in cleared:
                continue
            try:
                for index in reversed(range(curve.numKeys)):
                    curve.remove(index)
                cleared.add(curve_name)
            except Exception as exc:
                context.logger.debug(f"Failed to clear animLayer seed keys for {node_name}.{attr}: {exc}")
    return curves


def batch_create_and_key_curves(
    context: VmdKeyingContext,
    joint_name: str,
    channel_samples: Dict[str, List[Tuple[float, float]]],
) -> bool:
    """Create anim curves and add keyed channel samples through Maya API 2.0."""
    if not cmds.objExists(joint_name) or not channel_samples:
        return False
    attrs = list(channel_samples.keys())
    curves: Dict[str, oma.MFnAnimCurve] = {}
    animation_layer = context.anim_layer if context.use_animation_layers and context.anim_layer else None
    create_error: Optional[Exception] = None
    try:
        curves = maya_animation_utils.create_animation_curves(
            joint_name,
            attrs,
            tangent_type=oma.MFnAnimCurve.kTangentLinear,
            animation_layer=animation_layer,
        )
    except Exception as e:
        context.logger.debug(f"create_animation_curves failed for {joint_name}: {e}")
        create_error = e
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
                    value = _as_layer_delta(joint_name, attr, val) if animation_layer else float(val)
                    vals.append(value)
                if times is None or len(times) != len(vals):
                    times = om.MTimeArray()
                    for frame, _ in samples:
                        times.append(om.MTime(float(frame), om.MTime.uiUnit()))
                with vmd_profile.scope("addKeys", count=len(samples)):
                    curve.addKeys(times, vals, tangent, tangent, False)
                used_api = True
                success_any = True
                continue
            except Exception as e:
                context.logger.debug(f"addKeys failed for {joint_name}.{attr}: {e}")
                _ensure_fallback_allowed(joint_name, attr, animation_layer, f"addKeys failed: {e!r}")
        else:
            reason = "no API animCurve found"
            if create_error is not None:
                reason = f"create_animation_curves failed: {create_error!r}"
            _ensure_fallback_allowed(joint_name, attr, animation_layer, reason)
        for frame, val in samples:
            try:
                value = _as_layer_delta(joint_name, attr, val) if animation_layer else float(val)
                cmd_val = math.degrees(value) if "rotate" in attr.lower() else value
                key_args = {"attribute": attr, "time": frame, "value": cmd_val}
                if animation_layer:
                    key_args["animLayer"] = animation_layer
                with vmd_profile.scope("fallback_setKeyframe"):
                    cmds.setKeyframe(joint_name, **key_args)
                success_any = True
            except Exception:
                pass
        if not used_api:
            context.logger.debug(f"Used opt-in cmds.setKeyframe fallback for {joint_name}.{attr}")
    return success_any


def batch_create_and_key_curve_arrays(
    context: VmdKeyingContext,
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
    animation_layer = context.anim_layer if context.use_animation_layers and context.anim_layer else None
    layer_static_values: Dict[str, om.MDoubleArray] = {}
    for attr, values in channel_values.items():
        state = static_state.get(attr, {})
        if state.get("is_static", False):
            if state.get("first") is not None:
                if animation_layer:
                    layer_static_values[attr] = om.MDoubleArray(
                        len(times),
                        _as_layer_delta(joint_name, attr, float(state["first"])),
                    )
                    dynamic_attrs.append(attr)
                else:
                    skipped_static += 1
                    try:
                        value = float(state["first"])
                        if "rotate" in attr.lower():
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

    create_error: Optional[Exception] = None
    try:
        curves = maya_animation_utils.create_animation_curves(
            joint_name,
            dynamic_attrs,
            tangent_type=oma.MFnAnimCurve.kTangentLinear,
            animation_layer=animation_layer,
        )
    except Exception as e:
        context.logger.debug(f"create_animation_curves failed for {joint_name}: {e}")
        create_error = e
        curves = {}

    tangent = oma.MFnAnimCurve.kTangentLinear
    keyed = 0
    for attr in dynamic_attrs:
        values = layer_static_values.get(attr) or channel_values[attr]
        curve = curves.get(attr)
        if curve:
            try:
                if animation_layer and attr not in layer_static_values:
                    layer_values = om.MDoubleArray()
                    for index in range(len(values)):
                        layer_values.append(_as_layer_delta(joint_name, attr, values[index]))
                    values_to_key = layer_values
                else:
                    values_to_key = values
                with vmd_profile.scope("addKeys", count=len(values_to_key)):
                    curve.addKeys(times, values_to_key, tangent, tangent, False)
                keyed += 1
                continue
            except Exception as e:
                context.logger.debug(f"addKeys failed for {joint_name}.{attr}: {e}")
                _ensure_fallback_allowed(joint_name, attr, animation_layer, f"addKeys failed: {e!r}")
        else:
            reason = "no API animCurve found"
            if create_error is not None:
                reason = f"create_animation_curves failed: {create_error!r}"
            _ensure_fallback_allowed(joint_name, attr, animation_layer, reason)

        for index, frame in enumerate(frame_numbers):
            try:
                value = float(values[index])
                if animation_layer and attr not in layer_static_values:
                    value = _as_layer_delta(joint_name, attr, value)
                if "rotate" in attr.lower():
                    value = math.degrees(value)
                key_args = {"attribute": attr, "time": frame, "value": value}
                if animation_layer:
                    key_args["animLayer"] = animation_layer
                with vmd_profile.scope("fallback_setKeyframe"):
                    cmds.setKeyframe(joint_name, **key_args)
            except Exception:
                pass
        keyed += 1

    return keyed, skipped_static


def batch_key_scalar_channels(
    context: VmdKeyingContext,
    node_name: str,
    channel_samples: Dict[str, List[Tuple[float, float]]],
    animation_layer: Optional[str] = None,
) -> bool:
    """Key Maya UI scalar channels with MFnAnimCurve.addKeys and cmd fallback.

    When *animation_layer* is provided, *channel_samples* must contain additive
    layer deltas in Maya UI units. Rotation layer deltas are degrees and are
    converted to radians only for animCurveTA API insertion.
    """
    if not cmds.objExists(node_name) or not channel_samples:
        return False

    attrs = [attr for attr, samples in channel_samples.items() if samples]
    if not attrs:
        return False

    curves: Dict[str, oma.MFnAnimCurve] = {}
    create_error: Optional[Exception] = None
    try:
        curves = _create_scalar_channel_curves(
            context,
            node_name,
            attrs,
            channel_samples,
            animation_layer=animation_layer,
        )
    except Exception as exc:
        context.logger.debug(f"create_animation_curves failed for {node_name}: {exc}")
        create_error = exc

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
                    api_value = math.radians(float(value)) if "rotate" in attr.lower() else float(value)
                    values.append(api_value)
                with vmd_profile.scope("addKeys", count=len(samples)):
                    curve.addKeys(times, values, tangent, tangent, False)
                success_any = True
                continue
            except Exception as exc:
                context.logger.debug(f"addKeys failed for {node_name}.{attr}: {exc}")
                _ensure_fallback_allowed(node_name, attr, animation_layer, f"addKeys failed: {exc!r}")
        else:
            reason = "no API animCurve found"
            if create_error is not None:
                reason = f"create_animation_curves failed: {create_error!r}"
            _ensure_fallback_allowed(node_name, attr, animation_layer, reason)

        fallback_base_values = []
        if animation_layer:
            with vmd_profile.scope("fallback_base_values_build", count=len(samples)):
                fallback_base_values = [
                    _base_ui_value_at_frame(node_name, attr, float(frame))
                    for frame, _value in samples
                ]
        for index, (frame, value) in enumerate(samples):
            try:
                key_value = float(value)
                if animation_layer:
                    key_value = fallback_base_values[index] + key_value
                key_args = {
                    "attribute": attr,
                    "time": frame,
                    "value": key_value,
                }
                if animation_layer:
                    key_args["animLayer"] = animation_layer
                with vmd_profile.scope("fallback_setKeyframe"):
                    cmds.setKeyframe(node_name, **key_args)
                success_any = True
            except Exception as exc:
                context.logger.debug(f"setKeyframe fallback failed for {node_name}.{attr} at {frame}: {exc}")

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
        base_value = _layer_base_ui_value(node_name, attr)
        attr_samples = []
        for frame, value in samples:
            attr_samples.append((frame, float(value) - base_value))
        adjusted[attr] = attr_samples
    return adjusted
