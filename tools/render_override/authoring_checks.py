"""Real Maya checks for the source mesh / render proxy authoring boundary."""

from pathlib import Path


def check_authoring(cmds, root: str, scene_path: Path):
    """Exercise target creation, deformation, undo, and source persistence."""
    proxies = cmds.listRelatives(root, allDescendents=True, type="mmdRenderShape", fullPath=True) or []
    if len(proxies) != 1:
        raise AssertionError("expected one live render proxy")
    source = cmds.listConnections(f"{proxies[0]}.inputMesh", source=True, destination=False, shapes=True)[0]
    source = cmds.ls(source, long=True)[0]
    source_transform = cmds.listRelatives(source, parent=True, fullPath=True)[0]
    proxy_transform = cmds.listRelatives(proxies[0], parent=True, fullPath=True)[0]
    if source_transform == proxy_transform:
        from mmd_tools.io import cpp_fast_importer
        raise AssertionError(f"proxy must not enter morph target duplication: {cpp_fast_importer.__file__}")

    def points():
        return cmds.xform(f"{source}.vtx[*]", query=True, objectSpace=True, translation=True)

    initial = points()
    target = cmds.duplicate(source_transform, name="authoring_probe_target")[0]
    try:
        assert not cmds.listRelatives(target, allDescendents=True, type="mmdRenderShape")
    finally:
        cmds.delete(target)

    # Exercise the imported vertex morph through the same public controller
    # input used by the UI, rather than creating an unrelated test deformer.
    controllers = cmds.listConnections(f"{root}.mmd_morph_controller", source=True, destination=False) or []
    assert len(controllers) == 1, "expected the imported morph controller"
    plug = f"{controllers[0]}.inputWeight[0]"
    cmds.setAttr(plug, 1.0)
    changed = points()
    assert changed != initial, "imported morph did not affect source vertices"
    cmds.undo()
    assert points() == initial, "weight undo did not restore source"
    cmds.redo()
    assert points() == changed, "weight redo did not restore deformation"
    cmds.setAttr(plug, 0.0)
    assert points() == initial, "morph 0->1->0 did not restore source"

    # Native transient draw data is not the source-of-truth on scene reload.
    # The authored mesh and its deformer data must survive independently.
    scene_path.parent.mkdir(parents=True, exist_ok=True)
    cmds.file(rename=str(scene_path))
    cmds.file(save=True, type="mayaAscii", force=True)
    cmds.file(str(scene_path), open=True, force=True)
    assert points() == initial, "source mesh changed on scene reload"
    return {"separateTransforms": True, "targetWithoutProxy": True,
            "blendShapeUndoRedo": True, "morphRoundTrip": True,
            "sourceSaveReload": True}
