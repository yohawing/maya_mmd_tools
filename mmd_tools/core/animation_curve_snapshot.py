"""Capture, restore, and copy Maya animation curves and channel state."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple

import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma

from mmd_tools.core.mmd_control_rig_builder import MmdControlRigBuildError


def set_sampled_curve_tangents(cmds, curve_plug: str, target: str) -> None:
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


def sampled_curve_tangent_types(target: str) -> Tuple[str, str]:
    """Return explicit in/out tangent types for a sampled destination plug."""
    attribute = str(target).rsplit(".", 1)[-1].lower()
    if attribute.endswith("enabled") or attribute.startswith("ik"):
        # The outgoing ``step`` tangent owns the held-state behavior. Maya
        # 2027 also rejects ``stepnext`` as an in-tangent, so keep the incoming
        # side portable and non-semantic while retaining the stepped output.
        return "auto", "step"
    return "linear", "linear"


def sampled_curve_type(cmds, control_source: Optional[str], target: str) -> str:
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


def copy_animation_curve(cmds, source: str, destination: str) -> None:
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


def capture_animation_curve_payload(cmds, node: str) -> Dict[str, Any]:
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
                ("lock", "lock"),
                ("weightLock", "weightLock"),
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


def capture_animation_channel_snapshot(cmds, plug: str) -> Dict[str, Any]:
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


def clear_animation_curve_keys(cmds, node: str) -> None:
    """Remove all keys while retaining the animCurve node and its UUID."""
    selection = om.MSelectionList()
    selection.add(node)
    curve_fn = oma.MFnAnimCurve(selection.getDependNode(0))
    for index in reversed(range(curve_fn.numKeys)):
        curve_fn.remove(index)


def restore_animation_channel_snapshot(
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


def restore_animation_curve_payload(cmds, node: str, payload: Mapping[str, Any]) -> None:
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
        # Rebuilt keys start locked in Maya. Restoring asymmetric VMD
        # handles while locked makes one side overwrite the other.
        lock_options = {"weightLock": False} if payload.get("weightedTangents") else {}
        cmds.keyTangent(node, time=(time, time), edit=True, lock=False, **lock_options)
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
        if payload.get("weightedTangents"):
            lock_options["weightLock"] = bool(key.get("weightLock", False))
        cmds.keyTangent(node, time=(time, time), edit=True,
                        lock=bool(key.get("lock", True)), **lock_options)
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


# Internal calls retain their original names.
_capture_animation_curve_payload = capture_animation_curve_payload
_clear_animation_curve_keys = clear_animation_curve_keys
_restore_animation_curve_payload = restore_animation_curve_payload
_sampled_curve_tangent_types = sampled_curve_tangent_types
