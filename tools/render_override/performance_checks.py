"""Measure native geometry work during camera-only viewport changes."""

import json
import time


def check_camera_updates(cmds, shape, panel, frames=12):
    """Camera motion must not reevaluate mesh streams or upload Ordered buffers."""
    camera = cmds.modelEditor(panel, query=True, camera=True)
    if cmds.nodeType(camera) == "camera":
        camera = cmds.listRelatives(camera, parent=True, fullPath=True)[0]
    plug = f"{camera}.rotateY"
    original = cmds.getAttr(plug)
    cmds.refresh(force=True)
    before = json.loads(cmds.mmdRenderWitness(node=shape, json=True))
    ordered_before = json.loads(cmds.mmdOrderedRenderWitness())
    start = time.perf_counter()
    try:
        for frame in range(frames):
            cmds.setAttr(plug, original + (frame + 1) * 0.1)
            cmds.refresh(force=True)
    finally:
        cmds.setAttr(plug, original)
        cmds.refresh(force=True)
    elapsed = time.perf_counter() - start
    after = json.loads(cmds.mmdRenderWitness(node=shape, json=True))
    ordered_after = json.loads(cmds.mmdOrderedRenderWitness())
    result = {"geometryUpdates": after["geometryUpdates"] - before["geometryUpdates"]}
    result.update(frames=frames, elapsedSeconds=elapsed)
    result["orderedUploads"] = ordered_after["geometryUploads"] - ordered_before["geometryUploads"]
    assert result["geometryUpdates"] == 0, result
    assert result["orderedUploads"] == 0, result
    # A real deformation must still invalidate the cached native geometry.
    controllers = cmds.ls(type="mmdMorphController") or []
    if controllers:
        weight = f"{controllers[0]}.inputWeight[0]"
        value = cmds.getAttr(weight)
        cmds.setAttr(weight, value + 1.0)
        cmds.refresh(force=True)
        deformed = json.loads(cmds.mmdRenderWitness(node=shape, json=True))
        cmds.undo()
        cmds.refresh(force=True)
        restored = json.loads(cmds.mmdRenderWitness(node=shape, json=True))
        result["deformationUpdates"] = deformed["geometryUpdates"] - after["geometryUpdates"]
        result["undoUpdates"] = restored["geometryUpdates"] - deformed["geometryUpdates"]
        assert result["deformationUpdates"] > 0 and result["undoUpdates"] > 0, result
    return result
