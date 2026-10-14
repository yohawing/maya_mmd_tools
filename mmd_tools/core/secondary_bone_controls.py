"""FK authoring controls for physics bones, with a separate read-only live display.

Only local authored channels enter the physics driver.  The live display is a
separate child of the result joint, so following simulation never feeds back.
"""

from __future__ import annotations

import json
import math

from mmd_tools.core.constants import ATTR_MMD_BONE_FLAGS
from mmd_tools.core.logger import get_logger

_ATTR = "mmdSecondaryControls"
_CHANNELS = tuple(f"{family}{axis}" for family in ("translate", "rotate") for axis in "XYZ")
_JOBS = {}
_LOGGER = get_logger(__name__)


def _cmds(cmds_module):
    if cmds_module is None:
        from maya import cmds
        return cmds
    return cmds_module


def _sources(cmds, plug):
    return cmds.listConnections(plug, source=True, destination=False, plugs=True) or []


def _pre(driver, channel):
    return f"{driver}.inPre{channel[0].upper()}{channel[1:]}"


def _move_layer_memberships(cmds, source, destination):
    """Move layer membership with its authored channel, retaining blend nodes.

    Reconnecting an animBlend output alone leaves Maya's member set pointing
    at the old plug, which prevents ordinary setKeyframe on the new control.
    """
    for member in cmds.listConnections(source, source=False, destination=True, plugs=True, type="animLayer") or []:
        if member.rsplit(".", 1)[-1].startswith("dagSetMembers["):
            cmds.disconnectAttr(source, member)
            cmds.connectAttr(destination, member)


def _read(cmds, root):
    if not cmds.attributeQuery(_ATTR, node=root, exists=True):
        return None
    data = json.loads(cmds.getAttr(f"{root}.{_ATTR}"))
    references = data.pop("references", {})
    resolved = {}
    for name, identifier in references.items():
        matches = cmds.ls(identifier, long=True) or []
        if len(matches) != 1:
            raise ValueError(f"Secondary control node is missing or ambiguous: {name}")
        resolved[name] = matches[0]
    data["world"] = resolved.get(data["world"], data["world"])
    data["nodes"] = [resolved.get(node, node) for node in data["nodes"]]
    for row in data["controls"]:
        for field in ("joint", "driver", "collider", "control", "proxy"):
            row[field] = resolved.get(row[field], row[field])
        if "destinations" in row:
            row["destinations"] = {channel: resolved.get(plug.rsplit(".", 1)[0], plug.rsplit(".", 1)[0]) + "." + plug.rsplit(".", 1)[1]
                                   for channel, plug in row["destinations"].items()}
    for group in data["groups"]:
        group["node"] = resolved.get(group["node"], group["node"])
    return data


def _write(cmds, root, data):
    if not cmds.attributeQuery(_ATTR, node=root, exists=True):
        cmds.addAttr(root, longName=_ATTR, dataType="string")
    names = set(data["nodes"]) | {data["world"]}
    for row in data["controls"]:
        names.update(row[field] for field in ("joint", "driver", "collider", "control", "proxy"))
        names.update(plug.rsplit(".", 1)[0] for plug in row.get("destinations", {}).values())
    saved = dict(data, references={name: cmds.ls(name, uuid=True)[0] for name in names})
    cmds.setAttr(f"{root}.{_ATTR}", json.dumps(saved), type="string")


def inspect(root, cmds_module=None):
    """Return saved controls and current display/physics state for the UI."""
    cmds = _cmds(cmds_module)
    data = _read(cmds, root)
    if not data:
        from mmd_tools.core.mmd_control_rig_builder import inspect_mmd_control_rig
        ready = inspect_mmd_control_rig(root, cmds_module=cmds) is not None
        return {"exists": False, "can_build": ready, "physics_enabled": False, "groups": [], "controls": []}
    result = dict(data, exists=True, physics_enabled=bool(cmds.getAttr(f"{data['world']}.enable")))
    result["groups"] = [dict(group, visible=bool(cmds.getAttr(f"{group['node']}.visibility"))) for group in data["groups"]]
    return result


def curve_points(shape_type, size):
    """Collider-sized polyline: a circle for sphere/capsule, wire box for box."""
    x, y, z = (max(0.0, float(value)) for value in size)
    if shape_type != 1:
        return [(x * math.cos(i * math.tau / 32), 0.0, x * math.sin(i * math.tau / 32)) for i in range(33)]
    corners = [(-x, -y, -z), (x, -y, -z), (x, -y, z), (-x, -y, z),
               (-x, y, -z), (x, y, -z), (x, y, z), (-x, y, z)]
    return [corners[i] for i in (0, 1, 2, 3, 0, 4, 5, 1, 5, 6, 2, 6, 7, 3, 7, 4)]


def allowed_channels(flags):
    """Respect the PMX movable and rotatable flags."""
    return tuple(channel for channel in _CHANNELS if flags & (4 if channel.startswith("translate") else 2))


def _plan(cmds, root):
    root = (cmds.ls(root, long=True) or [root])[0]
    shapes = cmds.listRelatives(root, allDescendents=True, fullPath=True, type="mmdRigidBodyShape") or []
    shapes.sort(key=lambda shape: int(cmds.getAttr(f"{shape}.pmxIndex")))
    rows, seen, worlds = [], set(), set()
    for shape in shapes:
        if int(cmds.getAttr(f"{shape}.physicsMode")) not in (1, 2):
            continue
        bones = cmds.listConnections(f"{shape}.relatedBone", source=True, destination=False, type="joint") or []
        if not bones:
            continue
        if len(bones) != 1:
            raise ValueError(f"Ambiguous collider bone: {shape}")
        matches = cmds.ls(bones[0], long=True) or []
        if len(matches) != 1:
            raise ValueError(f"Ambiguous bone path: {bones[0]}")
        joint = matches[0]
        if not joint.startswith(root + "|"):
            raise ValueError(f"Collider bone belongs to another model: {joint}")
        if joint in seen:
            continue
        seen.add(joint)
        drivers = cmds.listConnections(f"{joint}.rotate", source=True, destination=False, type="mmdPhysicsBoneDriver") or []
        drivers = sorted(set(drivers))
        if len(drivers) != 1:
            raise ValueError(f"Expected one physics driver: {joint}")
        driver = drivers[0]
        for family in ("translate", "rotate"):
            if not cmds.isConnected(f"{driver}.out{family.title()}", f"{joint}.{family}"):
                raise ValueError(f"Unsupported physics output route: {joint}.{family}")
        solvers = cmds.listConnections(f"{driver}.inSolverBoneMatrices", source=True, destination=False, type="mmdPhysicsSolver") or []
        if len(solvers) != 1:
            raise ValueError(f"Ambiguous physics solver: {joint}")
        world = cmds.listConnections(f"{solvers[0]}.inWorldSettings", source=True, destination=False, shapes=True) or []
        if len(world) != 1:
            raise ValueError(f"Missing physics world: {joint}")
        worlds.add(world[0])
        destinations = {channel: _pre(driver, channel) for channel in _CHANNELS}
        from mmd_tools.converters.vmd_redirected_authoring_proxy import resolve_redirected_authoring_proxy_authority
        proxy_routes, authority, claimed = resolve_redirected_authoring_proxy_authority(joint)
        if claimed:
            if not proxy_routes or any(f"{node}.{attr}" != destinations[channel] for channel, (node, attr) in authority.items()):
                raise ValueError(f"Stale VMD authoring proxy ownership: {joint}")
            destinations.update({channel: f"{node}.{attr}" for channel, (node, attr) in proxy_routes.items()})
        for channel in _CHANNELS:
            sources = _sources(cmds, destinations[channel])
            if len(sources) > 1 or (channel not in proxy_routes and any(not cmds.nodeType(source.split(".")[0]).startswith("animCurve") for source in sources)):
                raise ValueError(f"Unsupported upstream animation route; preserve existing rig: {destinations[channel]}")
        flags = int(cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_FLAGS}"))
        rows.append(dict(joint=joint, driver=driver, collider=shape,
                         group_id=int(cmds.getAttr(f"{shape}.collisionGroup")), channels=allowed_channels(flags), destinations=destinations))
    if not rows:
        raise ValueError("No physics-driven bones with a live physics driver were found")
    if len(worlds) != 1:
        raise ValueError("Secondary controls require one shared physics world")
    return rows, next(iter(worlds))


def _shape_points(cmds, row):
    from maya.api import OpenMaya as om
    shape, joint = row["collider"], row["joint"]
    kind = int(cmds.getAttr(f"{shape}.shapeType"))
    size = [cmds.getAttr(f"{shape}.shapeSize{axis}") for axis in "XYZ"]
    collider = (cmds.listRelatives(shape, parent=True, fullPath=True) or [])[0]
    relative = om.MMatrix(cmds.xform(collider, query=True, worldSpace=True, matrix=True)) * om.MMatrix(cmds.xform(joint, query=True, worldSpace=True, matrix=True)).inverse()
    if kind == 1:
        return [tuple(om.MPoint(*point) * relative)[:3] for point in curve_points(kind, size)]
    center = om.MPoint(0, 0, 0) * relative
    inverse = om.MMatrix(cmds.xform(joint, query=True, worldSpace=True, matrix=True)).inverse()
    children = cmds.listRelatives(joint, children=True, fullPath=True, type="joint") or []
    children = [child for child in children if not cmds.attributeQuery("mmdSecondaryControl", node=child, exists=True)]
    parents = cmds.listRelatives(joint, parent=True, fullPath=True, type="joint") or []
    axis = om.MVector(0, 1, 0)
    if children:
        tip = om.MPoint(*cmds.xform(children[0], query=True, worldSpace=True, translation=True)) * inverse
        axis = om.MVector(tip.x, tip.y, tip.z)
    elif parents:
        tail = om.MPoint(*cmds.xform(parents[0], query=True, worldSpace=True, translation=True)) * inverse
        axis = om.MVector(-tail.x, -tail.y, -tail.z)
    rotation = om.MVector(0, 1, 0).rotateTo(axis) if axis.length() > 1.0e-8 else om.MQuaternion()
    rotated = [om.MVector(*point).rotateBy(rotation) for point in curve_points(kind, size)]
    return [(point.x + center.x, point.y + center.y, point.z + center.z) for point in rotated]


def _attach_curve(cmds, parent, points):
    temporary = cmds.curve(degree=1, point=points)
    for shape in cmds.listRelatives(temporary, shapes=True, fullPath=True) or []:
        cmds.parent(shape, parent, shape=True, relative=True)
    cmds.delete(temporary)


def build(root, cmds_module=None):
    """Create controls without replacing non-animation upstream ownership."""
    cmds = _cmds(cmds_module)
    matches = cmds.ls(root, long=True) or []
    if len(matches) != 1:
        raise ValueError(f"Expected one model root: {root}")
    root = matches[0]
    if _read(cmds, root):
        raise ValueError("Secondary controls already exist")
    from mmd_tools.core.mmd_control_rig_builder import inspect_mmd_control_rig
    if inspect_mmd_control_rig(root, cmds_module=cmds) is None:
        raise ValueError("Set up the Control Rig before creating physics controllers")
    rows, world = _plan(cmds, root)
    # Geometry is evaluated before any input connection is changed.
    points = [_shape_points(cmds, row) for row in rows]
    data = dict(version=1, world=world, controls=[], groups=[], nodes=[])
    cmds.undoInfo(openChunk=True, chunkName="Build secondary bone controls")
    failed = False
    try:
        reverse = cmds.createNode("reverse", name="secondaryPhysicsOff")
        data["nodes"].append(reverse)
        cmds.connectAttr(f"{world}.enable", f"{reverse}.inputX")
        groups = {}
        for group_id in sorted({row["group_id"] for row in rows}):
            node = cmds.createNode("transform", name=f"secondaryGroup{group_id + 1}", parent=root)
            node = root + "|" + node.rsplit("|", 1)[-1]
            groups[group_id] = node
            data["nodes"].append(node)
            data["groups"].append(dict(id=group_id, label=f"Group {group_id + 1}", visible=True,
                                       count=sum(row["group_id"] == group_id for row in rows), node=node))
        for row, geometry in zip(rows, points):
            joint = row["joint"]
            parents = cmds.listRelatives(joint, parent=True, fullPath=True) or []
            control = cmds.createNode("joint", name=joint.rsplit("|", 1)[-1] + "_secondary_CTRL", parent=parents[0])
            control = parents[0] + "|" + control.rsplit("|", 1)[-1]
            row["control"] = control
            data["nodes"].append(control)
            cmds.setAttr(f"{control}.drawStyle", 2)
            cmds.addAttr(control, longName="mmdSecondaryControl", attributeType="bool", defaultValue=True)
            cmds.addAttr(control, longName="mmdSecondaryJoint", attributeType="message")
            cmds.connectAttr(f"{joint}.message", f"{control}.mmdSecondaryJoint")
            for attr in ("jointOrient", "rotateAxis", "scale"):
                cmds.setAttr(f"{control}.{attr}", *cmds.getAttr(f"{joint}.{attr}")[0])
            inverse_sources = _sources(cmds, f"{joint}.inverseScale")
            if inverse_sources:
                cmds.connectAttr(inverse_sources[0], f"{control}.inverseScale", force=True)
            else:
                for source in _sources(cmds, f"{control}.inverseScale"):
                    cmds.disconnectAttr(source, f"{control}.inverseScale")
                cmds.setAttr(f"{control}.inverseScale", *cmds.getAttr(f"{joint}.inverseScale")[0])
            for attr in ("rotateOrder", "segmentScaleCompensate"):
                cmds.setAttr(f"{control}.{attr}", cmds.getAttr(f"{joint}.{attr}"))
            _attach_curve(cmds, control, geometry)
            proxy = cmds.createNode("transform", name="secondaryPhysicsDisplay", parent=joint)
            proxy = joint + "|" + proxy.rsplit("|", 1)[-1]
            row["proxy"] = proxy
            data["nodes"].append(proxy)
            _attach_curve(cmds, proxy, geometry)
            cmds.setAttr(f"{proxy}.overrideEnabled", True)
            cmds.setAttr(f"{proxy}.overrideDisplayType", 2)
            visibility = cmds.createNode("multiplyDivide", name="secondaryGroupVisibility")
            data["nodes"].append(visibility)
            for axis, mode, destination in (("X", f"{reverse}.outputX", control), ("Y", f"{world}.enable", proxy)):
                cmds.connectAttr(mode, f"{visibility}.input1{axis}")
                cmds.connectAttr(f"{groups[row['group_id']]}.visibility", f"{visibility}.input2{axis}")
                cmds.connectAttr(f"{visibility}.output{axis}", f"{destination}.visibility")
            data["controls"].append(row)
            for channel in _CHANNELS:
                destination, authored = row["destinations"][channel], f"{control}.{channel}"
                value = cmds.getAttr(destination)
                sources = _sources(cmds, destination)
                cmds.setAttr(authored, value)
                if sources:
                    cmds.disconnectAttr(sources[0], destination)
                    cmds.connectAttr(sources[0], authored)
                _move_layer_memberships(cmds, destination, authored)
                cmds.connectAttr(authored, destination)
            for channel in ("scaleX", "scaleY", "scaleZ", "visibility"):
                cmds.setAttr(f"{control}.{channel}", lock=True, keyable=False, channelBox=False)
        _write(cmds, root, data)
        refresh_editability(root, cmds, record_undo=True)
    except Exception:
        failed = True
        raise
    finally:
        cmds.undoInfo(closeChunk=True)
        if failed:
            cmds.undo()
    install_callbacks(root, cmds)
    return inspect(root, cmds)


def refresh_editability(root, cmds_module=None, *_, record_undo=False):
    """Lock authored channels during simulation, including selected controls."""
    cmds = _cmds(cmds_module)
    matches = cmds.ls(root, long=True) or []
    if len(matches) != 1:
        return
    root = matches[0]
    data = _read(cmds, root)
    if not data:
        return
    enabled = bool(cmds.getAttr(f"{data['world']}.enable"))
    undo_enabled = cmds.undoInfo(query=True, state=True)
    if not record_undo:
        cmds.undoInfo(stateWithoutFlush=False)
    try:
        for row in data["controls"]:
            for channel in _CHANNELS:
                editable = not enabled and channel in row["channels"]
                plug = f"{row['control']}.{channel}"
                if bool(cmds.getAttr(plug, lock=True)) != (not editable) or bool(cmds.getAttr(plug, keyable=True)) != editable:
                    cmds.setAttr(plug, lock=not editable, keyable=editable, channelBox=False)
    finally:
        if not record_undo:
            cmds.undoInfo(stateWithoutFlush=undo_enabled)


def install_callbacks(root, cmds_module=None):
    """Watch enable synchronously, including changes made outside the rig UI."""
    from maya.api import OpenMaya as om
    cmds = _cmds(cmds_module)
    identifier = cmds.ls(root, uuid=True)[0]
    previous = _JOBS.pop(identifier, None)
    if previous:
        try:
            om.MMessage.removeCallback(previous)
        except RuntimeError:
            pass
    data = _read(cmds, root)
    if data:
        selection = om.MSelectionList()
        selection.add(data["world"])

        def changed(message, plug, other, client_data):
            if message & om.MNodeMessage.kAttributeSet and plug.partialName(useLongNames=True) == "enable":
                refresh_editability(identifier, cmds)

        _JOBS[identifier] = om.MNodeMessage.addAttributeChangedCallback(selection.getDependNode(0), changed)
        refresh_editability(root, cmds)


def clear_callbacks(cmds_module=None):
    """Remove transient jobs on plugin unload/new scene."""
    if not _JOBS:
        return
    from maya.api import OpenMaya as om
    try:
        for job in _JOBS.values():
            try:
                om.MMessage.removeCallback(job)
            except RuntimeError:
                pass
    finally:
        _JOBS.clear()


def install_scene_callbacks(cmds_module=None):
    cmds = _cmds(cmds_module)
    clear_callbacks(cmds)
    for root in cmds.ls(f"*.{_ATTR}", objectsOnly=True, long=True, recursive=True) or []:
        try:
            install_callbacks(root, cmds)
        except Exception:
            _LOGGER.error("Could not restore secondary control callbacks for %s", root, exc_info=True)


def set_group_visibility(root, group_id, visible, cmds_module=None):
    cmds = _cmds(cmds_module)
    data = _read(cmds, root)
    group = next(group for group in data["groups"] if group["id"] == group_id)
    cmds.setAttr(f"{group['node']}.visibility", bool(visible))


def rename_group(root, group_id, label, cmds_module=None):
    """Change only the display label, never the collision configuration."""
    cmds = _cmds(cmds_module)
    data = _read(cmds, root)
    group = next(group for group in data["groups"] if group["id"] == group_id)
    group["label"] = str(label).strip() or f"Group {group_id + 1}"
    _write(cmds, root, data)


def bake(root, start, end, cmds_module=None):
    """Sample physics sequentially, then key all local channels and turn it off.

    The shared world is disabled, so other models in that world also stop.
    Even PMX-locked translation is baked to preserve simulated displacement.
    """
    cmds = _cmds(cmds_module)
    data = _read(cmds, root)
    if not data or not cmds.getAttr(f"{data['world']}.enable"):
        raise ValueError("Enable physics before baking secondary controls")
    start, end = float(start), float(end)
    if not math.isfinite(start) or not math.isfinite(end) or end < start:
        raise ValueError("Bake end must not precede start")
    frames = [start + step for step in range(int(math.floor(end - start)) + 1)]
    if frames[-1] < end:
        frames.append(end)
    from mmd_tools.core.physics_preroll import _capture_scene, _restore_interaction, _rollback_physics, run_physics_preroll
    solvers = sorted({solver for row in data["controls"] for solver in (
        cmds.listConnections(f"{row['driver']}.inSolverBoneMatrices", source=True,
                             destination=False, type="mmdPhysicsSolver") or [])})
    all_solvers = sorted(set(solvers) | set(cmds.listConnections(f"{data['world']}.message", source=False,
                                                               destination=True, type="mmdPhysicsSolver") or []))
    snapshot = _capture_scene(data["world"], all_solvers, cmds)
    samples = []
    try:
        if snapshot.playback:
            cmds.play(state=False)
        if len(frames) > 1:
            cmds.currentTime(start, edit=True)
            run_physics_preroll(data["world"], solvers, maya_cmds=cmds)
        for frame in frames:
            cmds.currentTime(frame, edit=True)
            for solver in solvers:
                if not cmds.getAttr(f"{solver}.outSolved"):
                    raise ValueError(f"Physics did not solve frame {frame}: {solver}")
            samples.append((frame, [[cmds.getAttr(f"{row['joint']}.{channel}") for channel in _CHANNELS] for row in data["controls"]]))
        cmds.undoInfo(openChunk=True, chunkName="Bake secondary physics controls")
        failed = False
        try:
            cmds.setAttr(f"{data['world']}.enable", False)
            for row in data["controls"]:
                for channel in _CHANNELS:
                    cmds.setAttr(f"{row['control']}.{channel}", lock=False)
            for frame, values in samples:
                for row, channels in zip(data["controls"], values):
                    for channel, value in zip(_CHANNELS, channels):
                        cmds.setKeyframe(row["control"], attribute=channel, time=frame, value=value)
        except Exception:
            failed = True
            raise
        finally:
            refresh_editability(root, cmds, record_undo=True)
            cmds.undoInfo(closeChunk=True)
            if failed:
                cmds.undo()
    except Exception:
        undo_enabled = cmds.undoInfo(query=True, state=True)
        cmds.undoInfo(stateWithoutFlush=False)
        try:
            restoration_errors = _rollback_physics(data["world"], snapshot, all_solvers, cmds)
        finally:
            cmds.undoInfo(stateWithoutFlush=undo_enabled)
        if restoration_errors:
            _LOGGER.error("Secondary bake rollback: %s", "; ".join(restoration_errors))
        raise
    finally:
        undo_enabled = cmds.undoInfo(query=True, state=True)
        cmds.undoInfo(stateWithoutFlush=False)
        try:
            restoration_errors = _restore_interaction(snapshot, cmds)
        finally:
            cmds.undoInfo(stateWithoutFlush=undo_enabled)
        if restoration_errors:
            _LOGGER.error("Secondary bake interaction restore: %s", "; ".join(restoration_errors))
    return inspect(root, cmds)


def remove(root, cmds_module=None):
    """Restore driver authoring routes, transferring keys instead of deleting them."""
    cmds = _cmds(cmds_module)
    data = _read(cmds, root)
    if not data:
        return
    for row in data["controls"]:
        for channel in _CHANNELS:
            authored = f"{row['control']}.{channel}"
            destination = row.get("destinations", {}).get(channel, _pre(row["driver"], channel))
            if not cmds.isConnected(authored, destination):
                raise ValueError(f"Secondary control route changed: {authored}")
            if len(_sources(cmds, authored)) > 1:
                raise ValueError(f"Ambiguous controller input; preserve animation: {authored}")
    cmds.undoInfo(openChunk=True, chunkName="Remove secondary bone controls")
    failed = False
    try:
        for row in data["controls"]:
            for channel in _CHANNELS:
                authored = f"{row['control']}.{channel}"
                destination = row.get("destinations", {}).get(channel, _pre(row["driver"], channel))
                value, sources = cmds.getAttr(authored), _sources(cmds, authored)
                cmds.disconnectAttr(authored, destination)
                if sources:
                    cmds.setAttr(authored, lock=False)
                    cmds.disconnectAttr(sources[0], authored)
                    cmds.connectAttr(sources[0], destination)
                else:
                    cmds.setAttr(destination, value)
                _move_layer_memberships(cmds, authored, destination)
        cmds.delete([node for node in data["nodes"] if cmds.objExists(node)])
        cmds.deleteAttr(f"{root}.{_ATTR}")
    except Exception:
        failed = True
        raise
    finally:
        cmds.undoInfo(closeChunk=True)
        if failed:
            cmds.undo()
    job = _JOBS.pop(cmds.ls(root, uuid=True)[0], None)
    if job:
        from maya.api import OpenMaya as om
        om.MMessage.removeCallback(job)


def authoring_routes(root, cmds_module=None):
    """Return controller destinations for importers that preserve pre-physics input."""
    data = _read(_cmds(cmds_module), root)
    return {row["joint"]: {channel: (row["control"], channel) for channel in _CHANNELS}
            for row in data["controls"]} if data else {}


def secondary_control_input_route(driver, joint, channel, cmds_module=None):
    """Resolve only a marked controller connected to this exact bone and input."""
    cmds = _cmds(cmds_module)
    if channel not in _CHANNELS:
        return None
    sources = cmds.listConnections(_pre(driver, channel), source=True, destination=False,
                                   plugs=True, skipConversionNodes=True) or []
    if len(sources) == 1:
        source_node = sources[0].rsplit(".", 1)[0]
        if cmds.attributeQuery("mmd_vmd_authoring_proxy", node=source_node, exists=True):
            from mmd_tools.converters.vmd_redirected_authoring_proxy import resolve_redirected_authoring_proxy_authority
            routes, authority, claimed = resolve_redirected_authoring_proxy_authority(joint)
            if not claimed or channel not in routes or authority.get(channel) != (driver, _pre(driver, channel).rsplit(".", 1)[1]):
                return None
            sources = cmds.listConnections(".".join(routes[channel]), source=True, destination=False,
                                           plugs=True, skipConversionNodes=True) or []
    if len(sources) != 1 or sources[0].rsplit(".", 1)[-1] != channel:
        return None
    control = sources[0].rsplit(".", 1)[0]
    if not cmds.attributeQuery("mmdSecondaryControl", node=control, exists=True):
        return None
    if not cmds.getAttr(f"{control}.mmdSecondaryControl"):
        return None
    linked = cmds.listConnections(f"{control}.mmdSecondaryJoint", source=True, destination=False) or []
    target = cmds.ls(joint, long=True) or []
    linked = cmds.ls(linked, long=True) or [] if linked else []
    if len(target) != 1 or linked != target:
        return None
    return control, channel
