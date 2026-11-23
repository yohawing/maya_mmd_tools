"""Add a VMD bone motion to Maya's Time Editor as a baked clip.

Each call imports one VMD onto the model's authoring inputs, bakes every
touched channel per frame, moves the baked curves into a new Time Editor
animation source and places a clip for it on a new track. The model's
previous writers (scene curves or earlier clips) are reconnected afterwards,
so the same model can receive any number of clips. Morph, camera, light and
shadow keys are outside this slice and are dropped.
"""
from collections import defaultdict
from pathlib import Path
import re

from maya import cmds

from .logger import get_logger

logger = get_logger(__name__)

_CURVE_TYPES = ("animCurveTA", "animCurveTL", "animCurveTU")
_DRIVER_TYPES = _CURVE_TYPES + ("timeEditorInterpolator",)
_CHANNELS = ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ")
_ROTATE = ("rotateX", "rotateY", "rotateZ")
COMPOSITION_NAME = "MMD_Composition"


def _target_plugs(root):
    """Return every plug a model VMD import may key: bone authoring inputs and IK enabled."""
    from ..converters.vmd_ik_enabled_animation import collect_ik_nodes_by_bone_name
    from ..converters.vmd_scene_collector import VmdSceneCollector

    joints = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
    routes = VmdSceneCollector()._scene_authored_input_routes(joints, target_model=root)
    plugs = []
    for joint in joints:
        for channel in _CHANNELS:
            node, attr = routes.get(joint, {}).get(channel, (joint, channel))
            plugs.append(node + "." + attr)
    ik_nodes = set(collect_ik_nodes_by_bone_name(target_model=root).values())
    plugs += [node + ".enabled" for node in sorted(ik_nodes)]
    return plugs


def is_time_editor_driven(root):
    """Return True when a Time Editor clip writes any of the model's authoring inputs."""
    if not root or not cmds.objExists(root):
        return False
    plugs = [plug for plug in _target_plugs(root) if cmds.objExists(plug)]
    if not plugs:
        return False
    return bool(cmds.listConnections(plugs, source=True, destination=False, type="timeEditorInterpolator"))


def _clip_name(name, vmd_path):
    """Make an ASCII Maya node name from the given name or the VMD file name."""
    raw = name or Path(str(vmd_path)).stem
    return re.sub(r"[^0-9A-Za-z_]+", "_", raw).strip("_") or "VMD_Motion"


def _detach_inputs(plugs):
    """Disconnect the current writers of ``plugs`` and return them for restoring.

    Every writer is checked before anything is disconnected, so an unsupported
    driver (Animation Layer blend, constraint, HumanIK, ...) leaves the scene untouched.
    """
    edges = []
    for plug in plugs:
        source = cmds.connectionInfo(plug, sourceFromDestination=True)
        if not source:
            continue
        if cmds.nodeType(source.split(".", 1)[0]) not in _DRIVER_TYPES:
            raise ValueError(
                "Time Editor clips support only plain keys or other clips on this model; "
                "unsupported driver: " + source + " -> " + plug
            )
        edges.append((source, plug))
    for source, plug in edges:
        cmds.disconnectAttr(source, plug)
    return edges


def _restore_inputs(edges):
    for source, plug in edges:
        if not cmds.isConnected(source, plug):
            cmds.connectAttr(source, plug, force=True)


def _bake_to_source(vmd_data, vmd_path, options, name, before):
    """Import, bake and wrap the new curves into an animation source. Returns (source, plugs)."""
    from ..io import vmd_importer

    vmd_importer.import_vmd_file(vmd_data, str(vmd_path), options)
    # The importer leaves a time curve selected and bakeResults deletes it;
    # a stale selection entry crashes the next selection-reading command.
    cmds.select(clear=True)
    new_curves = sorted(set(cmds.ls(type="animCurve") or []) - before)
    plugs = []
    for curve in new_curves:
        if cmds.nodeType(curve) in _CURVE_TYPES:
            plugs += cmds.listConnections(curve + ".output", plugs=True, source=False, destination=True) or []
    if not plugs:
        raise ValueError("The VMD did not key any bone of this model")
    times = cmds.keyframe(new_curves, query=True, timeChange=True)
    start, end = int(min(times)), int(max(times))
    cmds.bakeResults(plugs, time=(start, max(end, start + 1)), sampleBy=1, simulation=False,
                     preserveOutsideKeys=False, sparseAnimCurveBake=False, minimizeRotation=False,
                     disableImplicitControl=False)
    # bakeResults resets rotation curves to Euler interpolation.
    rotations = defaultdict(list)
    for plug in plugs:
        node, attr = plug.split(".", 1)
        if attr in _ROTATE:
            rotations[node].append(cmds.connectionInfo(plug, sourceFromDestination=True).split(".", 1)[0])
    for curves in rotations.values():
        if len(curves) == 3:
            cmds.rotationInterpolation(*curves, convert="quaternionSlerp")
    source = cmds.timeEditorAnimSource(name + "_Source", attribute=plugs, removeSceneAnimation=True)
    return source, plugs


def _tracks_node():
    """Return the active composition's tracks node, creating MMD_Composition when none exists."""
    if cmds.timeEditorComposition(query=True, allCompositions=True):
        tracks = cmds.timeEditorComposition(query=True, active=True)
        if tracks:
            return tracks
    composition = cmds.timeEditorComposition(COMPOSITION_NAME, createTrack=False)
    return cmds.timeEditorComposition(composition, query=True, tracksNode=True)


def import_vmd_clip(root, vmd_data, vmd_path, options, name=None, start=None):
    """Add the VMD bone motion to ``root`` as a new Time Editor clip on a new track.

    Args:
        root: MMD model root transform.
        vmd_data: Parsed ``VmdData``. Its morph/camera/light/shadow frames are cleared.
        vmd_path: Source VMD path (used for the clip name and by the importer).
        options: VMD import options (``pmx_path``, ``vmd_fps``, ...).
        name: Optional clip name; defaults to the ASCII form of the file name.
        start: Optional clip start time; defaults to the source's first key.

    Returns:
        dict with ``clipId``, ``clip``, ``source``, ``track`` and ``targets`` (baked plug count).
    """
    if not vmd_data.bone_frames:
        raise ValueError("Time Editor clips need VMD bone motion; this file has none")
    dropped = [attr for attr in ("morph_frames", "camera_frames", "light_frames", "shadow_frames")
               if getattr(vmd_data, attr, None)]
    for attr in dropped:
        setattr(vmd_data, attr, [])
    if dropped:
        logger.info("Time Editor clip keeps bone motion only; dropped: %s", ", ".join(dropped))

    import_options = dict(options or {})
    import_options.update(target_model=root, scene_animation_only=False, use_animation_layers=False,
                          clear_existing_motion=False, bake_mode=False, create_mmd_control_rig=False,
                          import_camera_animation=False, import_light_animation=False)
    name = _clip_name(name, vmd_path)

    selection = cmds.ls(selection=True, long=True) or []
    undo_state = cmds.undoInfo(query=True, state=True)
    cmds.undoInfo(stateWithoutFlush=False)
    try:
        cmds.select(clear=True)
        before = set(cmds.ls(type="animCurve") or [])
        edges = _detach_inputs(_target_plugs(root))
        try:
            source, plugs = _bake_to_source(vmd_data, vmd_path, import_options, name, before)
        except Exception:
            cmds.select(clear=True)
            leftovers = list(set(cmds.ls(type="animCurve") or []) - before)
            if leftovers:
                cmds.delete(leftovers)
            raise
        finally:
            _restore_inputs(edges)

        tracks = _tracks_node()
        index = cmds.timeEditorTracks(tracks, edit=True, addTrack=-1, trackType=0)
        track = "%s:%d" % (tracks, index)
        if start is None:
            start = cmds.timeEditorAnimSource(source, query=True, calculateTiming=True)[0]
        clip_id = cmds.timeEditorClip(name, animSource=source, track=track, startTime=float(start))
        return {"clipId": clip_id, "clip": cmds.timeEditorClip(clip_id, query=True, clipNode=True),
                "source": source, "track": track, "targets": len(plugs)}
    finally:
        cmds.undoInfo(stateWithoutFlush=undo_state)
        existing = [node for node in selection if cmds.objExists(node)]
        if existing:
            cmds.select(existing, replace=True)
        else:
            cmds.select(clear=True)
