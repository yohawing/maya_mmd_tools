"""Author experimental sparse VMD rotation time curves for Control Rig tracks."""

from __future__ import annotations

import json
import math
from typing import Any, Callable, Iterable, Mapping

import maya.cmds as cmds

from .vmd_bone_interpolation import (
    get_frame_interpolation,
    get_frame_number,
    parse_vmd_interpolation,
)


_MARKER_ATTR = "mmdVmdRotationTimeCurve"
_BONE_NAME_ATTR = "mmdVmdRotationBoneName"
_CONTROL_UUID_ATTR = "mmdVmdRotationControlUuid"
_INTERPOLATION_ATTR = "mmdVmdRotationInterpolationJson"


def apply_vmd_rotation_time_curve(
    frames: Iterable[Any],
    quaternion_plugs: Iterable[str],
    vmd_bone_name: str,
    *,
    time_converter: Callable[[float], float] = float,
) -> dict[str, Any]:
    """Create or update one weighted ``animCurveTT`` for an XYZ quaternion track."""
    ordered = sorted(frames, key=get_frame_number)
    if len(ordered) < 2:
        raise RuntimeError("VMD rotation time curve requires at least two keys")
    plugs = [str(plug) for plug in quaternion_plugs]
    if len(plugs) != 3:
        raise RuntimeError("VMD rotation time curve requires a complete XYZ track")
    control = plugs[0].rsplit(".", 1)[0]
    if any(plug.rsplit(".", 1)[0] != control for plug in plugs):
        raise RuntimeError("VMD rotation time curve cannot span multiple controls")

    curves = []
    existing_time_curves = set()
    for plug in plugs:
        incoming = cmds.listConnections(
            plug, source=True, destination=False, plugs=True
        ) or []
        if len(incoming) != 1:
            raise RuntimeError(f"VMD rotation curve is unresolved: {plug}")
        curve = str(incoming[0]).split(".", 1)[0]
        if not str(cmds.nodeType(curve)).startswith("animCurve"):
            raise RuntimeError(f"VMD rotation source is not an animCurve: {plug}")
        if cmds.rotationInterpolation(curve, query=True) != "quaternionSlerp":
            raise RuntimeError(f"VMD rotation source is not quaternion: {curve}")
        curves.append(curve)
        sources = cmds.listConnections(
            f"{curve}.input", source=True, destination=False, plugs=True
        ) or []
        for source in sources:
            node = str(source).split(".", 1)[0]
            if (
                cmds.objExists(node)
                and cmds.nodeType(node) == "animCurveTT"
                and cmds.attributeQuery(_MARKER_ATTR, node=node, exists=True)
                and cmds.getAttr(f"{node}.{_MARKER_ATTR}")
            ):
                existing_time_curves.add(node)

    if len(existing_time_curves) > 1:
        raise RuntimeError(f"VMD rotation track has split time curves: {control}")
    created_time_curve = not existing_time_curves
    time_curve = (
        next(iter(existing_time_curves))
        if not created_time_curve
        else cmds.createNode(
            "animCurveTT",
            name=f"{control.rsplit('|', 1)[-1]}_vmdRotationTime",
        )
    )
    try:
        return _author_vmd_rotation_time_curve(
            ordered,
            curves,
            control,
            time_curve,
            vmd_bone_name,
            time_converter,
        )
    except Exception:
        if created_time_curve and cmds.objExists(time_curve):
            detach_and_delete_vmd_rotation_time_curve(cmds, time_curve)
        raise


def _author_vmd_rotation_time_curve(
    ordered: list[Any],
    curves: list[str],
    control: str,
    time_curve: str,
    vmd_bone_name: str,
    time_converter: Callable[[float], float],
) -> dict[str, Any]:
    """Populate and connect a validated TT node."""
    cmds.cutKey(time_curve, clear=True)
    serialized_interpolation = []
    for frame in ordered:
        vmd_frame = float(get_frame_number(frame))
        time = float(time_converter(vmd_frame))
        cmds.setKeyframe(time_curve, time=time, value=time)
        serialized_interpolation.append(
            {
                "frame": vmd_frame,
                "bytes": list(bytes(get_frame_interpolation(frame))[:64]),
            }
        )
    cmds.keyTangent(time_curve, edit=True, weightedTangents=True)
    for previous, arriving in zip(ordered, ordered[1:]):
        start = float(time_converter(float(get_frame_number(previous))))
        end = float(time_converter(float(get_frame_number(arriving))))
        dt = end - start
        if dt <= 0.0:
            continue
        points = parse_vmd_interpolation(get_frame_interpolation(arriving)).get(
            "rotation"
        )
        if not points:
            continue
        x1, y1, x2, y2 = points
        _set_segment_tangents(
            time_curve,
            start,
            end,
            dt * x1,
            dt * y1,
            dt * (1.0 - x2),
            dt * (1.0 - y2),
        )

    control_uuid = _single_uuid(control)
    _set_marker(time_curve, _MARKER_ATTR, True, "bool")
    _set_marker(time_curve, _BONE_NAME_ATTR, str(vmd_bone_name), "string")
    _set_marker(time_curve, _CONTROL_UUID_ATTR, control_uuid, "string")
    _set_marker(
        time_curve,
        _INTERPOLATION_ATTR,
        json.dumps(serialized_interpolation, separators=(",", ":")),
        "string",
    )
    for curve in curves:
        input_plug = f"{curve}.input"
        for source in cmds.listConnections(
            input_plug, source=True, destination=False, plugs=True
        ) or []:
            if source != f"{time_curve}.output":
                cmds.disconnectAttr(source, input_plug)
        if not cmds.isConnected(f"{time_curve}.output", input_plug):
            cmds.connectAttr(f"{time_curve}.output", input_plug, force=False)
    return {
        "boneName": str(vmd_bone_name),
        "controlUuid": control_uuid,
        "rotationTimeCurveUuid": _single_uuid(time_curve),
        "rotationCurveUuids": [_single_uuid(curve) for curve in curves],
        "keyCount": len(ordered),
        "interpolationBytesAttribute": _INTERPOLATION_ATTR,
    }


def record_vmd_rotation_time_curve_metadata(
    model_root: str,
    records: Iterable[Mapping[str, Any]],
    *,
    replace_existing: bool = False,
) -> None:
    """Persist rename-stable Experimental time-curve ownership on the rig root."""
    from mmd_tools.core.mmd_control_rig_builder import (
        _write_metadata,
        read_mmd_control_rig_metadata,
    )

    metadata = read_mmd_control_rig_metadata(model_root)
    if metadata is None:
        raise RuntimeError("MMD Control Rig metadata is missing for rotation time curves")
    unique = {}
    if not replace_existing:
        unique = {
            str(record["controlUuid"]): dict(record)
            for record in metadata.get("rotationTimeCurves", []) or []
            if record.get("controlUuid") and record.get("rotationTimeCurveUuid")
        }
    unique.update(
        {
        str(record["controlUuid"]): dict(record)
        for record in records
        if record.get("controlUuid") and record.get("rotationTimeCurveUuid")
        }
    )
    metadata["rotationInterpolationMode"] = "vmd_time_curve_experimental"
    metadata["rotationTimeCurves"] = [unique[key] for key in sorted(unique)]
    _write_metadata(cmds, model_root, metadata)


def capture_vmd_rotation_time_curve_snapshot(metadata: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Capture UUID-owned time curves before an Experimental re-import."""
    from mmd_tools.core.mmd_control_rig_motion import _capture_animation_curve_payload

    snapshot = []
    for record in (metadata or {}).get("rotationTimeCurves", []) or []:
        node, _control, _rotation_curves = resolve_vmd_rotation_time_curve_record(
            record
        )
        uuid = str(record["rotationTimeCurveUuid"])
        snapshot.append(
            {
                "uuid": uuid,
                "node": node,
                "payload": _capture_animation_curve_payload(cmds, node),
                "destinations": list(
                    cmds.listConnections(
                        f"{node}.output",
                        source=False,
                        destination=True,
                        plugs=True,
                    )
                    or []
                ),
                "markers": {
                    attr: cmds.getAttr(f"{node}.{attr}")
                    for attr in (
                        _BONE_NAME_ATTR,
                        _CONTROL_UUID_ATTR,
                        _INTERPOLATION_ATTR,
                    )
                    if cmds.attributeQuery(attr, node=node, exists=True)
                },
            }
        )
    return snapshot


def restore_vmd_rotation_time_curve_snapshot(
    snapshot: Iterable[Mapping[str, Any]],
    attempted_records: Iterable[Mapping[str, Any]] = (),
) -> None:
    """Restore prior TT payloads and remove nodes created by a failed import."""
    from mmd_tools.core.mmd_control_rig_motion import (
        _clear_animation_curve_keys,
        _restore_animation_curve_payload,
    )

    rows = list(snapshot or [])
    prior_uuids = {str(row.get("uuid") or "") for row in rows}
    attempted_uuids = {
        str(record.get("rotationTimeCurveUuid") or "")
        for record in attempted_records or []
        if record.get("rotationTimeCurveUuid")
    }
    for uuid in sorted(attempted_uuids - prior_uuids):
        nodes = cmds.ls(uuid, long=True) or []
        if len(nodes) == 1:
            cmds.delete(nodes[0])
    for row in rows:
        nodes = cmds.ls(str(row.get("uuid") or ""), long=True) or []
        if len(nodes) != 1:
            raise RuntimeError(f"original VMD rotation time curve is missing: {row.get('node')}")
        node = str(nodes[0])
        output = f"{node}.output"
        prior_destinations = [
            str(destination) for destination in row.get("destinations", []) or []
        ]
        current_destinations = cmds.listConnections(
            output, source=False, destination=True, plugs=True
        ) or []
        for destination in current_destinations:
            if str(destination) not in prior_destinations:
                cmds.disconnectAttr(output, destination)
        for destination in prior_destinations:
            if cmds.objExists(destination) and not cmds.isConnected(output, destination):
                for source in cmds.listConnections(
                    destination, source=True, destination=False, plugs=True
                ) or []:
                    cmds.disconnectAttr(source, destination)
                cmds.connectAttr(output, destination, force=False)
        payload = row.get("payload") or {}
        if not payload.get("captureFailed") and "keys" in payload:
            _clear_animation_curve_keys(cmds, node)
            for key in payload.get("keys", []):
                cmds.setKeyframe(
                    node,
                    time=float(key.get("time", 0.0)),
                    value=float(key.get("value", 0.0)),
                )
            _restore_animation_curve_payload(cmds, node, payload)
        for attr, value in (row.get("markers") or {}).items():
            _set_marker(node, str(attr), value, "string")


def stage_vmd_rotation_time_curve_disable(
    snapshot: Iterable[Mapping[str, Any]],
) -> list[str]:
    """Detach prior Experimental TT nodes while retaining them for rollback."""
    nodes = []
    for row in snapshot or []:
        matches = cmds.ls(str(row.get("uuid") or ""), long=True) or []
        if len(matches) != 1:
            raise RuntimeError(f"VMD rotation time curve is missing: {row.get('node')}")
        node = str(matches[0])
        if cmds.nodeType(node) != "animCurveTT":
            raise RuntimeError(f"VMD rotation time curve has wrong type: {node}")
        output = f"{node}.output"
        for destination in cmds.listConnections(
            output, source=False, destination=True, plugs=True
        ) or []:
            if not str(destination).endswith(".input"):
                continue
            if cmds.isConnected(output, destination):
                cmds.disconnectAttr(output, destination)
            if cmds.objExists("time1.outTime") and not cmds.listConnections(
                destination, source=True, destination=False, plugs=True
            ):
                cmds.connectAttr("time1.outTime", destination, force=False)
        nodes.append(node)
    return nodes


def commit_vmd_rotation_time_curve_disable(
    model_root: str,
    nodes: Iterable[str],
    *,
    clear_metadata: bool = True,
) -> None:
    """Clear Experimental ownership and delete staged TT nodes after import."""
    from mmd_tools.core.mmd_control_rig_builder import (
        _write_metadata,
        read_mmd_control_rig_metadata,
    )

    owned_nodes = [str(node) for node in nodes]
    if not owned_nodes:
        return
    for node in owned_nodes:
        if not cmds.objExists(node) or cmds.nodeType(node) != "animCurveTT":
            raise RuntimeError(f"staged VMD rotation time curve is missing: {node}")
    if clear_metadata:
        metadata = read_mmd_control_rig_metadata(model_root)
        if metadata is None:
            raise RuntimeError("MMD Control Rig metadata is missing for rotation time curves")
        metadata.pop("rotationInterpolationMode", None)
        metadata.pop("rotationTimeCurves", None)
        _write_metadata(cmds, model_root, metadata)
    cmds.delete(owned_nodes)


def rotation_time_curve_interpolation_by_bone(
    metadata: Mapping[str, Any] | None,
) -> dict[str, dict[int, bytes]]:
    """Return original VMD interpolation bytes from UUID-owned TT nodes."""
    if (metadata or {}).get("rotationInterpolationMode") != "vmd_time_curve_experimental":
        return {}
    result: dict[str, dict[int, bytes]] = {}
    for record in (metadata or {}).get("rotationTimeCurves", []) or []:
        node, _control, _rotation_curves = resolve_vmd_rotation_time_curve_record(
            record
        )
        if not cmds.attributeQuery(_INTERPOLATION_ATTR, node=node, exists=True):
            raise RuntimeError(f"VMD rotation interpolation payload is missing: {node}")
        raw = cmds.getAttr(f"{node}.{_INTERPOLATION_ATTR}") or "[]"
        try:
            rows = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"VMD rotation interpolation payload is invalid: {node}") from exc
        bone_name = str(record.get("boneName") or "")
        for row in rows:
            values = bytes(int(value) for value in row.get("bytes", []))
            if len(values) == 64:
                result.setdefault(bone_name, {})[int(round(float(row["frame"])))] = values
    return result


def resolve_vmd_rotation_time_curve_record(
    record: Mapping[str, Any],
    *,
    cmds_module=None,
) -> tuple[str, str, list[str]]:
    """Resolve and validate one UUID-owned Experimental rotation track."""
    maya_cmds = cmds_module or cmds

    def resolve(uuid: str, label: str) -> str:
        nodes = maya_cmds.ls(uuid, long=True) if uuid else []
        if len(nodes) != 1:
            raise RuntimeError(f"owned VMD rotation {label} is missing: {uuid or 'missing UUID'}")
        return str(nodes[0])

    time_uuid = str(record.get("rotationTimeCurveUuid") or "")
    control_uuid = str(record.get("controlUuid") or "")
    rotation_uuids = [str(value) for value in record.get("rotationCurveUuids", []) or []]
    if len(rotation_uuids) != 3 or len(set(rotation_uuids)) != 3:
        raise RuntimeError("owned VMD rotation track does not have three sibling curves")
    time_curve = resolve(time_uuid, "time curve")
    control = resolve(control_uuid, "control")
    rotation_curves = [resolve(uuid, "sibling curve") for uuid in rotation_uuids]
    if maya_cmds.nodeType(time_curve) != "animCurveTT":
        raise RuntimeError(f"owned VMD rotation time curve has wrong type: {time_curve}")
    if (
        not maya_cmds.attributeQuery(_MARKER_ATTR, node=time_curve, exists=True)
        or not maya_cmds.getAttr(f"{time_curve}.{_MARKER_ATTR}")
        or not maya_cmds.attributeQuery(
            _CONTROL_UUID_ATTR, node=time_curve, exists=True
        )
        or str(maya_cmds.getAttr(f"{time_curve}.{_CONTROL_UUID_ATTR}"))
        != control_uuid
    ):
        raise RuntimeError(f"owned VMD rotation time curve marker is invalid: {time_curve}")
    if any(
        not str(maya_cmds.nodeType(curve)).startswith("animCurve")
        for curve in rotation_curves
    ):
        raise RuntimeError("owned VMD rotation sibling has wrong node type")
    expected_destinations = {f"{curve}.input" for curve in rotation_curves}
    actual_destinations = {
        str(value)
        for value in (
            maya_cmds.listConnections(
                f"{time_curve}.output",
                source=False,
                destination=True,
                plugs=True,
            )
            or []
        )
    }
    if not expected_destinations.issubset(actual_destinations):
        raise RuntimeError("owned VMD rotation sibling curves are not driven by their time curve")
    return time_curve, control, rotation_curves


def share_vmd_rotation_time_curve(
    cmds_module,
    control_sources: Iterable[str | None],
    destination_sources: Iterable[str | None],
) -> str | None:
    """Connect one control-owned TT to the baked MMD quaternion siblings."""
    maya_cmds = cmds_module
    time_curves = set()
    for source in control_sources:
        if not source:
            return None
        curve = str(source).split(".", 1)[0]
        incoming = maya_cmds.listConnections(
            f"{curve}.input", source=True, destination=False, plugs=True
        ) or []
        if len(incoming) != 1:
            return None
        node = str(incoming[0]).split(".", 1)[0]
        if (
            maya_cmds.nodeType(node) != "animCurveTT"
            or not maya_cmds.attributeQuery(_MARKER_ATTR, node=node, exists=True)
            or not maya_cmds.getAttr(f"{node}.{_MARKER_ATTR}")
        ):
            return None
        time_curves.add(node)
    if len(time_curves) != 1:
        raise RuntimeError("Quaternion siblings do not share one VMD rotation time curve")
    time_curve = next(iter(time_curves))
    destinations = [source for source in destination_sources if source]
    if len(destinations) != 3:
        raise RuntimeError("VMD rotation time curve bake requires three destination curves")
    for source in destinations:
        curve = str(source).split(".", 1)[0]
        input_plug = f"{curve}.input"
        for incoming in maya_cmds.listConnections(
            input_plug, source=True, destination=False, plugs=True
        ) or []:
            if incoming != f"{time_curve}.output":
                maya_cmds.disconnectAttr(incoming, input_plug)
        if not maya_cmds.isConnected(f"{time_curve}.output", input_plug):
            maya_cmds.connectAttr(f"{time_curve}.output", input_plug, force=False)
    return time_curve


def detach_and_delete_vmd_rotation_time_curve(cmds_module, node: str) -> None:
    """Restore ordinary Maya time on dependent curves, then delete one TT."""
    maya_cmds = cmds_module
    output = f"{node}.output"
    destinations = maya_cmds.listConnections(
        output, source=False, destination=True, plugs=True
    ) or []
    for destination in destinations:
        if not str(destination).endswith(".input"):
            continue
        if maya_cmds.isConnected(output, destination):
            maya_cmds.disconnectAttr(output, destination)
        if maya_cmds.objExists("time1.outTime") and not maya_cmds.listConnections(
            destination, source=True, destination=False, plugs=True
        ):
            maya_cmds.connectAttr("time1.outTime", destination, force=False)
    maya_cmds.delete(node)


def _set_segment_tangents(
    curve: str,
    start: float,
    end: float,
    out_dx: float,
    out_dy: float,
    in_dx: float,
    in_dy: float,
) -> None:
    cmds.keyTangent(
        curve,
        edit=True,
        time=(start, start),
        lock=False,
        weightLock=False,
        outTangentType="fixed",
        outAngle=math.degrees(math.atan2(out_dy, out_dx)),
        outWeight=math.hypot(out_dx, out_dy),
    )
    cmds.keyTangent(
        curve,
        edit=True,
        time=(end, end),
        lock=False,
        weightLock=False,
        inTangentType="fixed",
        inAngle=math.degrees(math.atan2(in_dy, in_dx)),
        inWeight=math.hypot(in_dx, in_dy),
    )


def _single_uuid(node: str) -> str:
    values = cmds.ls(node, uuid=True) or []
    if len(values) != 1:
        raise RuntimeError(f"could not resolve UUID for {node}")
    return str(values[0])


def _set_marker(node: str, attr: str, value: Any, kind: str) -> None:
    if not cmds.attributeQuery(attr, node=node, exists=True):
        if kind == "string":
            cmds.addAttr(node, longName=attr, dataType="string")
        else:
            cmds.addAttr(node, longName=attr, attributeType="bool")
    if kind == "string":
        cmds.setAttr(f"{node}.{attr}", value, type="string")
    else:
        cmds.setAttr(f"{node}.{attr}", bool(value))
