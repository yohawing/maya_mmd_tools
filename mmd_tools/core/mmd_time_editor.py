"""Add a model or camera VMD motion to Maya's Time Editor as a baked clip.

Each call imports one VMD onto the model's authoring inputs (or the MMD
camera rig), bakes every touched channel per frame, moves the baked curves
into a new Time Editor animation source and places a clip for it on a new
track. The previous writers (scene curves or earlier clips) are reconnected
afterwards, so the same model or camera can receive any number of clips.
Bone, morph and IK state keys share one model clip; a camera-only VMD becomes
a camera clip. Light and shadow keys are dropped, and so are camera keys of a
model VMD.
"""
from pathlib import Path
import re
from types import SimpleNamespace

from maya import cmds
from maya.api import OpenMaya as om, OpenMayaAnim as oma

from .logger import get_logger

logger = get_logger(__name__)

_CURVE_TYPES = ("animCurveTA", "animCurveTL", "animCurveTU")
_DRIVER_TYPES = _CURVE_TYPES + ("timeEditorInterpolator",)
_CHANNELS = ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ")
COMPOSITION_NAME = "MMD_Composition"


def _target_plugs(root):
    """Return every plug a model VMD import may key: bone authoring inputs, IK enabled and morph weights."""
    from ..converters.vmd_ik_enabled_animation import collect_ik_nodes_by_bone_name
    from ..converters.vmd_morph_mapping import build_morph_mappings, iter_morph_mappings
    from ..converters.vmd_scene_collector import VmdSceneCollector

    joints = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
    routes = VmdSceneCollector()._scene_authored_input_routes(joints, target_model=root)
    plugs = []
    for joint in joints:
        for channel in _CHANNELS:
            node, attr = routes.get(joint, {}).get(channel, (joint, channel))
            plug = node + "." + attr
            if node == joint:
                # Physics bones: the driver owns the joint; VMD keys go to its pre-physics input.
                driver = (cmds.connectionInfo(plug, sourceFromDestination=True) or ".").split(".", 1)[0]
                if driver and cmds.nodeType(driver) == "mmdPhysicsBoneDriver":
                    plug = driver + ".inPre" + channel[0].upper() + channel[1:]
            plugs.append(plug)
    ik_nodes = set(collect_ik_nodes_by_bone_name(target_model=root).values())
    plugs += [node + ".enabled" for node in sorted(ik_nodes)]
    # Same morph targets the importer keys (controller inputs, blendShape weights, morph networks).
    morphs = SimpleNamespace()
    build_morph_mappings(morphs, target_model=root)
    plugs += sorted({node + "." + attr for entry in morphs.morph_name_mapping.values()
                     for node, attr, _name in iter_morph_mappings(entry)})
    return plugs


def _camera_plugs():
    """Return the existing MMD camera rig's plugs written by a curve or a clip (empty without a rig)."""
    from ..converters.vmd_camera_animation import ATTR_MMD_CAMERA

    # Same lookup as the importer's get_or_create_camera.
    cameras = cmds.ls("*." + ATTR_MMD_CAMERA, objectsOnly=True, long=True)
    if not cameras:
        return []
    top = "|" + cameras[0].split("|")[1]
    nodes = [top] + (cmds.listRelatives(top, allDescendents=True, fullPath=True) or [])
    pairs = cmds.listConnections(nodes, source=True, destination=False, connections=True, plugs=True) or []
    return sorted({plug for plug, source in zip(pairs[::2], pairs[1::2])
                   if cmds.nodeType(source.split(".", 1)[0]) in _DRIVER_TYPES})


def is_time_editor_driven(root=None):
    """Return True when a Time Editor clip writes the model's authoring inputs.

    Without a ``root`` the MMD camera rig is checked instead.
    """
    if root and not cmds.objExists(root):
        return False
    plugs = [plug for plug in (_target_plugs(root) if root else _camera_plugs()) if cmds.objExists(plug)]
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


def _bake_curves(plugs, start, end):
    """Sample imported curves in Maya's internal units, sharing rotation time maps."""
    channels = []
    for plug in plugs:
        source = cmds.connectionInfo(plug, sourceFromDestination=True)
        curve, _, attr = source.partition(".")
        if attr != "output" or cmds.nodeType(curve) not in _CURVE_TYPES:
            break
        driver = cmds.connectionInfo(curve + ".input", sourceFromDestination=True)
        warp = None
        if driver:
            node, _, attr = driver.partition(".")
            kind = cmds.nodeType(node)
            if kind == "animCurveTT" and attr == "output" and not cmds.connectionInfo(
                    node + ".input", isDestination=True):
                warp = node
            elif kind != "time" or attr != "outTime":
                break
        channels.append((plug, curve, warp))

    if len(channels) != len(plugs):
        cmds.bakeResults(plugs, time=(start, end), sampleBy=1, simulation=False,
                         preserveOutsideKeys=False, sparseAnimCurveBake=False, minimizeRotation=False,
                         disableImplicitControl=False)
        return

    def anim_curve(name):
        selection = om.MSelectionList()
        selection.add(name)
        return oma.MFnAnimCurve(selection.getDependNode(0))

    frames = [om.MTime(i, om.MTime.uiUnit()) for i in range(start, end + 1)]
    times = om.MTimeArray(frames)
    warped = {}
    originals = set()
    replacements = []
    for plug, name, warp in channels:
        if warp and warp not in warped:
            evaluate = anim_curve(warp).evaluate
            warped[warp] = [evaluate(t) for t in frames]
        inputs = warped.get(warp, frames)
        curve = anim_curve(name)
        values = ([curve.evaluate(inputs[0])] * len(frames) if curve.numKeys == 1
                  else [curve.evaluate(t) for t in inputs])
        baked = oma.MFnAnimCurve()
        baked.create(curve.animCurveType)
        baked.addKeys(times, om.MDoubleArray(values), baked.kTangentLinear, baked.kTangentLinear)
        replacements.append((baked.name(), plug))
        originals.add(name)
    # Quaternion rotation curves depend on their siblings while being sampled.
    for name, plug in replacements:
        cmds.connectAttr(name + ".output", plug, force=True)
    cmds.delete(list(originals.union(warped)))


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
        raise ValueError("The VMD did not key anything for the Time Editor clip")
    for plug in plugs:
        # bakeResults and Time Editor sources skip non-keyable attributes (e.g. camera orthographicWidth).
        if not cmds.getAttr(plug, keyable=True):
            cmds.setAttr(plug, keyable=True)
    times = cmds.keyframe(new_curves, query=True, timeChange=True)
    start, end = int(min(times)), int(max(times))
    _bake_curves(plugs, start, max(end, start + 1))
    # Filter each rotation triplet together so equivalent Euler poses stay continuous.
    rotations = {}
    for plug in plugs:
        node, attr = plug.rsplit(".", 1)
        if attr in ("rotateX", "rotateY", "rotateZ"):
            source_plug = cmds.connectionInfo(plug, sourceFromDestination=True)
            rotations.setdefault(node, {})[attr] = source_plug.split(".")[0]
    for axes in rotations.values():
        if len(axes) == 3:
            cmds.filterCurve(axes["rotateX"], axes["rotateY"], axes["rotateZ"], filter="euler")
        else:
            # The camera rig splits rotation across its target (X/Y) and camera (Z).
            for curve in axes.values():
                cmds.filterCurve(curve, filter="euler")
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
    """Add the VMD motion as a new Time Editor clip on a new track.

    With a model ``root`` the bone, morph and IK state motion drives the model;
    without one the camera motion drives the MMD camera rig (created if needed).

    Args:
        root: MMD model root transform, or None for a camera VMD.
        vmd_data: Parsed ``VmdData``. Frames the clip does not take (light,
            shadow, and camera for a model clip) are cleared.
        vmd_path: Source VMD path (used for the clip name and by the importer).
        options: VMD import options (``pmx_path``, ``vmd_fps``, ...).
        name: Optional clip name; defaults to the ASCII form of the file name.
        start: Optional clip start time; defaults to the source's first key.

    Returns:
        dict with ``clipId``, ``clip``, ``source``, ``track`` and ``targets`` (baked plug count).
    """
    import_options = dict(options or {})
    import_options.update(use_animation_layers=False, clear_existing_motion=False, bake_mode=False,
                          create_mmd_control_rig=False, import_light_animation=False)
    if root:
        if not (vmd_data.bone_frames or vmd_data.morph_frames):
            raise ValueError("Time Editor clips need VMD bone or morph motion for a model; this file has none")
        dropped = ("camera_frames", "light_frames", "shadow_frames")
        import_options.update(target_model=root, scene_animation_only=False, import_camera_animation=False)
    else:
        if not getattr(vmd_data, "camera_frames", None):
            raise ValueError("Time Editor clips need VMD bone, morph or camera motion; this file has none")
        dropped = ("light_frames", "shadow_frames")
        import_options.pop("target_model", None)
        import_options.update(scene_animation_only=True, import_camera_animation=True)
    dropped = [attr for attr in dropped if getattr(vmd_data, attr, None)]
    for attr in dropped:
        setattr(vmd_data, attr, [])
    if dropped:
        logger.info("Time Editor clip skips other motion; dropped: %s", ", ".join(dropped))
    name = _clip_name(name, vmd_path)

    selection = cmds.ls(selection=True, long=True) or []
    undo_state = cmds.undoInfo(query=True, state=True)
    cmds.undoInfo(stateWithoutFlush=False)
    try:
        cmds.select(clear=True)
        before = set(cmds.ls(type="animCurve") or [])
        edges = _detach_inputs(_target_plugs(root) if root else _camera_plugs())
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
