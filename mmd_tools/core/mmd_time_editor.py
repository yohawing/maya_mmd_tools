"""One model's bone authoring curves in a Maya Time Editor clip.

This first slice excludes Animation Layers, Control Rig EDIT, HumanIK,
existing clips, morphs and IK-state channels. Clip mute does not restore scene
animation; removing this owned clip does. No global Time Editor mute is used.
"""
from contextlib import contextmanager
import json
import math
from uuid import uuid4

from maya import cmds

from .model_registry import _is_model_root
from .mmd_control_rig_builder import read_mmd_control_rig_metadata
from .logger import get_logger

logger = get_logger(__name__)
METADATA_ATTRIBUTE = "mmd_time_editor_json"
_CURVE_TYPES = ("animCurveTA", "animCurveTL", "animCurveTU")


class MmdTimeEditorError(ValueError):
    """The bounded model clip cannot safely claim or restore its inputs."""


def _node(uuid):
    names = cmds.ls(uuid, long=True) or []
    if len(names) != 1:
        raise MmdTimeEditorError("Time Editor owned node is missing or ambiguous: " + uuid)
    return names[0]


def _uuid(node):
    values = cmds.ls(node, uuid=True) or []
    if len(values) != 1:
        raise MmdTimeEditorError("Ambiguous Time Editor node: " + node)
    return values[0]


def _root(root):
    root = _node(_uuid(root))
    if not _is_model_root(root):
        raise MmdTimeEditorError("Select an imported MMD model root")
    return root


def _ref(plug):
    node, attribute = plug.split(".", 1)
    return {"uuid": _uuid(node), "attribute": attribute}


def _plug(ref):
    plug = _node(ref["uuid"]) + "." + ref["attribute"]
    if not cmds.objExists(plug):
        raise MmdTimeEditorError("Time Editor recorded input is missing: " + plug)
    return plug


def _canonical(plug):
    return _plug(_ref(plug))


def _read(root):
    if not cmds.attributeQuery(METADATA_ATTRIBUTE, node=root, exists=True):
        return None
    raw = cmds.getAttr(root + "." + METADATA_ATTRIBUTE)
    if not raw:
        return None
    try:
        row = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise MmdTimeEditorError("Invalid model Time Editor metadata") from exc
    if row.get("version") != 1 or row.get("modelUuid") != _uuid(root):
        raise MmdTimeEditorError("Time Editor metadata belongs to a different model or version")
    return row


@contextmanager
def _transaction(label):
    if not cmds.undoInfo(query=True, state=True):
        raise MmdTimeEditorError("Time Editor model clips require Maya Undo")
    name = label + "_" + uuid4().hex
    cmds.undoInfo(openChunk=True, chunkName=name)
    try:
        yield
    except Exception:
        cmds.undoInfo(closeChunk=True)
        if cmds.undoInfo(query=True, undoName=True) == name:
            cmds.undo()
        logger.error("Model Time Editor transaction failed", exc_info=True)
        raise
    else:
        cmds.undoInfo(closeChunk=True)


def _preflight(root):
    rig = read_mmd_control_rig_metadata(root)
    if rig and (rig.get("state") == "EDIT" or rig.get("owner") != "MMD_OWNED"):
        raise MmdTimeEditorError("Restore or bake Control Rig before creating a Time Editor clip")
    layers = cmds.ls(type="animLayer") or []
    base = cmds.animLayer(query=True, root=True) if layers else None
    if any(layer != base for layer in layers):
        raise MmdTimeEditorError("This clip workflow requires no non-base Animation Layers")
    if cmds.ls(type="timeEditorClip"):
        raise MmdTimeEditorError("This first model-clip workflow requires an empty Time Editor")
    if "HIKCharacterNode" in cmds.allNodeTypes() and cmds.ls(type="HIKCharacterNode"):
        raise MmdTimeEditorError("This clip workflow does not support HumanIK scenes")


def _animated_inputs(root):
    from mmd_tools.converters.vmd_scene_collector import VmdSceneCollector
    joints = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
    routes = VmdSceneCollector()._scene_authored_input_routes(joints, target_model=root)
    inputs = {}
    for joint in joints:
        for family in ("translate", "rotate"):
            for axis in "XYZ":
                logical = family + axis
                node, attr = routes.get(joint, {}).get(logical, (joint, logical))
                destination = _canonical(node + "." + attr)
                source = cmds.connectionInfo(destination, sourceFromDestination=True)
                if not source:
                    continue
                if cmds.nodeType(source.split(".", 1)[0]) not in _CURVE_TYPES:
                    raise MmdTimeEditorError("Unsupported authoring input writer: " + source)
                if not cmds.keyframe(source.split(".", 1)[0], query=True, keyframeCount=True):
                    continue
                inputs[destination] = {"destination": _ref(destination), "source": _ref(source)}
    if not inputs:
        raise MmdTimeEditorError("The model has no keyed bone authoring inputs")
    return inputs


def _external_inputs(owned):
    """Capture exact incoming plugs crossing the owned graph boundary."""
    edges = []
    for uuid in sorted(owned):
        pairs = cmds.listConnections(
            _node(uuid), source=True, destination=False, connections=True, plugs=True
        ) or []
        for destination, source in zip(pairs[::2], pairs[1::2]):
            source_ref = _ref(source)
            if source_ref["uuid"] not in owned:
                edges.append({"source": source_ref, "destination": _ref(destination)})
    return sorted(edges, key=lambda edge: json.dumps(edge, sort_keys=True))


def _validate(row):
    source, composition, clip = (_node(row[key]) for key in ("sourceUuid", "compositionUuid", "clipUuid"))
    if (cmds.nodeType(source), cmds.nodeType(composition), cmds.nodeType(clip)) != (
        "timeEditorAnimSource", "timeEditorTracks", "timeEditorClip"
    ):
        raise MmdTimeEditorError("Time Editor owned node type changed")
    clip_id = int(cmds.timeEditorClip(clip, query=True, clipIdFromNodeName=True))
    if _uuid(cmds.timeEditorClip(clip_id, query=True, animSource=True)) != row["sourceUuid"]:
        raise MmdTimeEditorError("The model clip animation source was replaced")
    targets = {_canonical(p) for p in (cmds.timeEditorAnimSource(source, query=True, targets=True) or [])}
    expected = {_plug(item["destination"]) for item in row["inputs"]}
    if targets != expected:
        raise MmdTimeEditorError("The model clip target attributes changed")
    owned = set(row["ownedUuids"])
    if _external_inputs(owned) != row.get("externalInputEdges"):
        raise MmdTimeEditorError("Owned Time Editor external input connections changed")
    for edge in row.get("preservationEdges", []):
        destination = _plug(edge["destination"])
        current = cmds.connectionInfo(destination, sourceFromDestination=True)
        if not current or _ref(current) != edge["source"]:
            raise MmdTimeEditorError("Original quaternion curve preservation connection changed")
    allowed_external = set(row["externalOutputUuids"])
    for uuid in owned:
        node = _node(uuid)
        if cmds.nodeType(node) == "transform" and cmds.listRelatives(node, children=True):
            raise MmdTimeEditorError("Owned Time Editor curve preservation node has children")
        for target in cmds.listConnections(node, source=False, destination=True) or []:
            if _uuid(target) not in owned | allowed_external:
                raise MmdTimeEditorError("Owned Time Editor node is used outside its recorded model clip: " + node + " -> " + target + " (" + cmds.nodeType(target) + ")")
    for item in row["inputs"]:
        destination, original = _plug(item["destination"]), _plug(item["source"])
        current = cmds.connectionInfo(destination, sourceFromDestination=True)
        if not current or _ref(current) not in (item["source"], item["clipWriter"]):
            raise MmdTimeEditorError("Model input writer changed outside Time Editor: " + destination)
        if cmds.nodeType(original.split(".", 1)[0]) not in _CURVE_TYPES:
            raise MmdTimeEditorError("Original model curve was replaced")
    clips = cmds.timeEditorTracks(composition + ":0", query=True, allClips=True) or []
    scene_clips = {_uuid(node) for node in cmds.ls(type="timeEditorClip") or []}
    if set(clips) != {clip_id} or scene_clips != {row["clipUuid"]}:
        raise MmdTimeEditorError("Owned Time Editor composition contains additional clips")
    return source, composition, clip, clip_id


def _preserve_rotation_groups(inputs):
    """Keep original XYZ curve siblings associated while the clip owns them.

    Disconnected quaternion curves serialize different values in Maya 2024.
    An owned transform preserves their native sibling context, without copying
    key payloads or evaluating the MMD output a second time.
    """
    groups = {}
    for destination, item in inputs.items():
        if destination.endswith((".rotateX", ".rotateY", ".rotateZ")):
            groups.setdefault(destination[:-1], []).append((destination, item))
    edges = []
    for base, items in groups.items():
        if len(items) != 3:
            continue
        sink = cmds.createNode("transform", name="MMD_TimeEditor_CurveSiblings")
        cmds.setAttr(sink + ".visibility", False)
        cmds.setAttr(sink + ".hiddenInOutliner", True)
        cmds.setAttr(sink + ".rotateOrder", cmds.getAttr(base[:-7] + ".rotateOrder"))
        for destination, item in items:
            target = sink + ".rotate" + destination[-1]
            cmds.connectAttr(_plug(item["source"]), target)
            edges.append({"source": item["source"], "destination": _ref(target)})
    return edges


def inspect_model_clip(root):
    """Resolve UUID-backed clip identity and reject modified ownership."""
    root = _root(root)
    row = _read(root)
    if not row:
        return {"active": False, "model": root, "clipId": None, "clip": None,
                "source": None, "composition": None, "inputCount": 0}
    source, composition, clip, clip_id = _validate(row)
    return {"active": True, "model": root, "clipId": clip_id, "clip": clip,
            "source": source, "composition": composition, "inputCount": len(row["inputs"])}


def create_model_clip(root, name=None, start=None):
    """Create one clip, preserving original curves and recording exact inputs."""
    root = _root(root)
    if _read(root):
        raise MmdTimeEditorError("This model already owns a Time Editor clip")
    if start is not None and not math.isfinite(float(start)):
        raise MmdTimeEditorError("Clip start must be a finite frame")
    _preflight(root)
    inputs = _animated_inputs(root)
    name = str(name or "MMD_Motion")
    before = set(cmds.ls("*", uuid=True))
    with _transaction("Create MMD Time Editor Clip"):
        source = cmds.timeEditorAnimSource(
            name + "_Source", addObjects=";".join(sorted({p.split(".", 1)[0] for p in inputs})),
            type=list(_CURVE_TYPES), removeSceneAnimation=False, recursively=False,
        )
        targets = cmds.timeEditorAnimSource(source, query=True, targets=True) or []
        for target in targets:
            if _canonical(target) not in inputs:
                cmds.timeEditorAnimSource(source, edit=True, removeSource=target)
        actual = {_canonical(p) for p in (cmds.timeEditorAnimSource(source, query=True, targets=True) or [])}
        if actual != set(inputs):
            raise MmdTimeEditorError("Time Editor could not populate every exact authoring input: " + repr(sorted(set(inputs) - actual)[:5]))
        timing = cmds.timeEditorAnimSource(source, query=True, calculateTiming=True)
        composition = cmds.timeEditorComposition(name + "_Composition", createTrack=True)
        composition = cmds.timeEditorComposition(composition, query=True, tracksNode=True)
        clip_id = cmds.timeEditorClip(name, animSource=source, track=composition + ":0",
                                     startTime=float(start) if start is not None else timing[0])
        clip = cmds.timeEditorClip(clip_id, query=True, clipNode=True)
        for destination, item in inputs.items():
            writer = cmds.connectionInfo(destination, sourceFromDestination=True)
            if not writer:
                raise MmdTimeEditorError("Time Editor did not connect an authoring input")
            item["clipWriter"] = _ref(writer)
        preservation_edges = _preserve_rotation_groups(inputs)
        owned = {uuid for uuid in set(cmds.ls("*", uuid=True)) - before if cmds.nodeType(_node(uuid)) != "timeEditor"}
        external = {_uuid(p.split(".", 1)[0]) for p in inputs}
        external.update(_uuid(n) for n in cmds.ls(type="timeEditor") or [])
        row = {"version": 1, "modelUuid": _uuid(root), "sourceUuid": _uuid(source),
               "compositionUuid": _uuid(composition), "clipUuid": _uuid(clip),
               "inputs": list(inputs.values()), "ownedUuids": sorted(owned),
               "preservationEdges": preservation_edges,
               "externalInputEdges": _external_inputs(owned),
               "externalOutputUuids": sorted(external)}
        _validate(row)
        if not cmds.attributeQuery(METADATA_ATTRIBUTE, node=root, exists=True):
            cmds.addAttr(root, longName=METADATA_ATTRIBUTE, dataType="string")
        cmds.setAttr(root + "." + METADATA_ATTRIBUTE, json.dumps(row), type="string")
    return inspect_model_clip(root)


def remove_model_clip(root):
    """Remove only this owned clip and require exact original source restoration."""
    root = _root(root)
    row = _read(root)
    if not row:
        return False
    _source, composition, _clip, clip_id = _validate(row)
    with _transaction("Remove MMD Time Editor Clip"):
        cmds.timeEditorClip(edit=True, removeClip=True, clipId=clip_id)
        for item in row["inputs"]:
            destination = _plug(item["destination"])
            current = cmds.connectionInfo(destination, sourceFromDestination=True)
            if not current or _ref(current) != item["source"]:
                raise MmdTimeEditorError("Time Editor did not restore the original source: " + destination)
        cmds.timeEditorComposition(composition, edit=True, delete=True)
        remaining = []
        for uuid in row["ownedUuids"]:
            nodes = cmds.ls(uuid, long=True) or []
            remaining.extend(nodes)
        if remaining:
            cmds.delete(remaining)
        cmds.deleteAttr(root + "." + METADATA_ATTRIBUTE)
    return True
