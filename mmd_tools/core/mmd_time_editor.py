"""Import VMD motion normally and move its scene animation into the Time Editor."""
import math
from pathlib import Path
import re

from maya import cmds
from maya.api import OpenMaya as om, OpenMayaAnim as oma

from .logger import get_logger

logger = get_logger(__name__)

_CURVE_TYPES = ("animCurveTA", "animCurveTL", "animCurveTU")
COMPOSITION_NAME = "MMD_Composition"


def _upstream(root, node_type):
    """Find upstream writers for a model's skeleton and meshes, or the MMD camera."""
    if root:
        nodes = (cmds.listRelatives(root, allDescendents=True, type=("joint", "mesh"), fullPath=True)
                 if cmds.objExists(root) else [])
    else:
        cameras = cmds.ls("*.mmd_camera", objectsOnly=True, long=True)
        if not cameras:
            return []
        # The rig root recorded by the camera importer; a user group above it is not the rig.
        roots = (cmds.listConnections(cameras[0] + ".mmd_camera_root_node", source=True, destination=False)
                 if cmds.attributeQuery("mmd_camera_root_node", node=cameras[0], exists=True) else None)
        top = (cmds.ls(roots, long=True) if roots else None) or cmds.listRelatives(
            cameras[0], parent=True, fullPath=True) or [cameras[0]]
        top = top[0]
        nodes = [top] + (cmds.listRelatives(top, allDescendents=True, fullPath=True) or [])
    if not nodes:
        return []
    history = set(cmds.listHistory(nodes, pruneDagObjects=False) or []).union(nodes)
    # Maya history can stop at a controller or omit a camera transform's local keys.
    inputs = cmds.listConnections(list(history), source=True, destination=False, type=node_type) or []
    return set(cmds.ls(list(history), type=node_type) or []).union(inputs)


def is_time_editor_driven(root=None):
    """Return whether a Time Editor interpolator drives the model or camera."""
    return bool(_upstream(root, "timeEditorInterpolator"))


def _clip_name(name, vmd_path):
    """Make an ASCII Maya node name from the given name or the VMD file name."""
    raw = name or Path(str(vmd_path)).stem
    return re.sub(r"[^0-9A-Za-z_]+", "_", raw).strip("_") or "VMD_Motion"


def _plug_key(plug):
    node, _, attr = plug.partition(".")
    uuids = cmds.ls(node, uuid=True) or [node]
    return uuids[0], attr


def _follow_track(tracks, plugs):
    """Return (track, end) of the latest-ending clip that animates any of ``plugs``.

    Clips on different tracks that overlap in time are averaged by the Time
    Editor, so a clip sharing channels goes after that clip on the same track
    (overlapping it there crossfades). Returns (None, None) when nothing is shared.
    """
    keys = {_plug_key(plug) for plug in plugs}
    best = (None, None)
    for track in cmds.timeEditorTracks(tracks, query=True, allTracksRecursive=True) or []:
        for clip in cmds.timeEditorTracks(track, query=True, allClips=True) or []:
            source = cmds.timeEditorClip(clip, query=True, animSource=True)
            targets = cmds.timeEditorAnimSource(source, query=True, targets=True) if source else None
            if not targets or keys.isdisjoint(_plug_key(target) for target in targets):
                continue
            end = (cmds.timeEditorClip(clip, query=True, startTime=True)
                   + cmds.timeEditorClip(clip, query=True, duration=True))
            if best[1] is None or end > best[1]:
                best = (track, end)
    return best


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


def _scene_source(root, name):
    """Keep ordinary scene curves and sample only VMD time-warped channels."""
    curves = _upstream(root, "animCurve")
    plugs = []
    warped = []
    warps = set()
    for curve in sorted(curves):
        if cmds.nodeType(curve) not in _CURVE_TYPES:
            continue
        driver = cmds.listConnections(curve + ".input", source=True, destination=False) or []
        warp = None
        if driver and cmds.nodeType(driver[0]) != "time":
            warp = driver[0]
            if cmds.nodeType(warp) != "animCurveTT" or cmds.connectionInfo(warp + ".input", isDestination=True):
                continue
        for plug in cmds.listConnections(curve + ".output", plugs=True, source=False, destination=True) or []:
            kind = cmds.nodeType(plug.split(".", 1)[0])
            if kind == "timeEditorAnimSource":
                continue
            if kind.startswith("animBlendNode") or kind in ("pairBlend", "blendWeighted", "unitConversion"):
                raise ValueError("Unsupported Time Editor curve destination: " + curve + " -> " + plug)
            plugs.append(plug)
            if warp:
                warped.append(plug)
                warps.add(warp)
    if not plugs:
        raise ValueError("No scene animation found for the Time Editor clip")
    for plug in plugs:
        # bakeResults and Time Editor sources skip non-keyable attributes (e.g. camera orthographicWidth).
        if not cmds.getAttr(plug, keyable=True):
            cmds.setAttr(plug, keyable=True)
    if warped:
        times = cmds.keyframe(list(warps), query=True, timeChange=True)
        start, end = math.floor(min(times)), math.ceil(max(times))
        _bake_curves(warped, start, max(end, start + 1))
    # Filter each rotation triplet together so equivalent Euler poses stay continuous.
    rotations = {}
    for plug in warped:
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


def _place_clip(source, plugs, name, start):
    """Append to the last track sharing channels or create an independent track."""
    tracks = _tracks_node()
    track, end = _follow_track(tracks, plugs)
    if track is None:
        index = cmds.timeEditorTracks(tracks, edit=True, addTrack=-1, trackType=0)
        track = "%s:%d" % (tracks, index)
    if start is None:
        start = end if end is not None else cmds.timeEditorAnimSource(
            source, query=True, calculateTiming=True)[0]
    clip_id = cmds.timeEditorClip(name, animSource=source, track=track, startTime=float(start))
    return {"clipId": clip_id, "clip": cmds.timeEditorClip(clip_id, query=True, clipNode=True),
            "source": source, "track": track, "targets": len(plugs)}


def import_vmd_clip(root, vmd_data, vmd_path, options, name=None, start=None):
    """Import the VMD motion and move the resulting scene curves into a clip.

    With a model ``root`` the bone, morph and IK state motion drives the model;
    without one the camera motion drives the MMD camera rig (created if needed).

    Args:
        root: MMD model root transform, or None for a camera VMD.
        vmd_data: Parsed ``VmdData``. Frames the clip does not take (light,
            shadow, and camera for a model clip) are cleared.
        vmd_path: Source VMD path (used for the clip name and by the importer).
        options: VMD import options (``pmx_path``, ``vmd_fps``, ...).
        name: Optional clip name; defaults to the ASCII form of the file name.
        start: Optional clip start time; defaults to the previous overlapping channel clip's end or the first key.

    Returns:
        dict with ``clipId``, ``clip``, ``source``, ``track`` and ``targets`` (animated plug count).
    """
    from ..io import vmd_importer

    import_options = dict(options or {})
    import_options.update(use_animation_layers=False, clear_existing_motion=False, import_light_animation=False)
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
        edges = []
        source_node = None
        try:
            for node in cmds.ls(type="timeEditorInterpolator") or []:
                pairs = cmds.listConnections(node, source=False, destination=True, connections=True, plugs=True) or []
                for source, plug in zip(pairs[::2], pairs[1::2]):
                    if source.endswith(".message"):
                        continue
                    cmds.disconnectAttr(source, plug)
                    edges.append((source, plug))
            vmd_importer.import_vmd_file(vmd_data, str(vmd_path), import_options)
            cmds.select(clear=True)
            source_node, plugs = _scene_source(root, name)
            # Reconnect before placement so Maya reuses the existing interpolators.
            _restore_inputs(edges)
            edges.clear()
            return _place_clip(source_node, plugs, name, start)
        except Exception:
            cmds.select(clear=True)
            if source_node and cmds.objExists(source_node):
                cmds.delete(source_node)
            leftovers = list(set(cmds.ls(type="animCurve") or []) - before)
            if leftovers:
                cmds.delete(leftovers)
            raise
        finally:
            _restore_inputs(edges)
    finally:
        cmds.undoInfo(stateWithoutFlush=undo_state)
        existing = [node for node in selection if cmds.objExists(node)]
        if existing:
            cmds.select(existing, replace=True)
        else:
            cmds.select(clear=True)
