"""Check Ordered geometry reuse across viewport and scene edits."""

import json
import time


def _changed_pixels(before, after):
    return sum(max(abs(a - b) for a, b in zip(left, right)) > 8
               for left, right in zip(before, after))


def check_camera_updates(cmds, shape, panel, frames=12, capture=None):
    """Reuse buffers for camera/world edits while preserving scene changes."""
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
    if capture is not None:
        transform = cmds.listRelatives(shape, parent=True, fullPath=True)[0]
        world_plug = f"{transform}.translateX"
        original_world = cmds.getAttr(world_plug)
        before_world = capture("world_before")["render"]
        before_world_witness = json.loads(cmds.mmdRenderWitness(node=shape, json=True))
        before_world_ordered = json.loads(cmds.mmdOrderedRenderWitness())
        try:
            cmds.setAttr(world_plug, original_world + 2.0)
            moved = capture("world_moved")["render"]
            moved_witness = json.loads(cmds.mmdRenderWitness(node=shape, json=True))
            moved_ordered = json.loads(cmds.mmdOrderedRenderWitness())
            result["worldChangedPixels"] = _changed_pixels(before_world, moved)
            result["worldGeometryUpdates"] = (
                moved_witness["geometryUpdates"] - before_world_witness["geometryUpdates"])
            result["worldOrderedUploads"] = (
                moved_ordered["geometryUploads"] - before_world_ordered["geometryUploads"])
            assert result["worldChangedPixels"] > 100, result
            assert result["worldGeometryUpdates"] == 0, result
            assert result["worldOrderedUploads"] == 0, result
        finally:
            cmds.setAttr(world_plug, original_world)
        restored = capture("world_restored")["render"]
        result["worldRestorePixels"] = _changed_pixels(before_world, restored)
        assert result["worldRestorePixels"] < 100, result

    # A real deformation must still invalidate the cached native geometry.
    controllers = cmds.ls(type="mmdMorphController") or []
    if controllers:
        weight = f"{controllers[0]}.inputWeight[0]"
        value = cmds.getAttr(weight)
        deformation_before = json.loads(cmds.mmdRenderWitness(node=shape, json=True))
        cmds.setAttr(weight, value + 1.0)
        cmds.refresh(force=True)
        deformed = json.loads(cmds.mmdRenderWitness(node=shape, json=True))
        cmds.undo()
        cmds.refresh(force=True)
        restored = json.loads(cmds.mmdRenderWitness(node=shape, json=True))
        result["deformationUpdates"] = deformed["geometryUpdates"] - deformation_before["geometryUpdates"]
        result["undoUpdates"] = restored["geometryUpdates"] - deformed["geometryUpdates"]
        assert result["deformationUpdates"] > 0 and result["undoUpdates"] > 0, result
    return result
