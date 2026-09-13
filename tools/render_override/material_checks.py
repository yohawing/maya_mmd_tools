"""Real viewport material-edit and Undo/Redo checks."""

import json

from tools.render_override.common import capture_view
from tools.render_override.render_override_visual_gate import read_png_rgb, write_png_rgb


def check_material_edits(cmds, root, shape, panel, output_dir):
    """Use the authoring command, then inspect committed native material values."""
    from mmd_tools.converters.material_morph_runtime import _collect_shaders_by_material_index

    shaders = _collect_shaders_by_material_index(root)
    index, shader = next(iter(sorted(shaders.items())))
    root = cmds.ls(root, long=True)[0]

    def capture(name):
        # Playblast changes panel/time state internally; do not let that hide
        # the authoring command from the next Undo/Redo operation.
        cmds.undoInfo(stateWithoutFlush=False)
        try:
            return capture_view(cmds, output_dir / name, panel, 640, 480)
        finally:
            cmds.undoInfo(stateWithoutFlush=True)

    def witness():
        cmds.refresh(force=True)
        data = json.loads(cmds.mmdRenderWitness(node=shape, json=True))
        return next(item for item in data["items"] if item["materialIndex"] == index and not item["outline"])

    before = witness()
    before_image = capture("material_before.png")
    edits = [{"field": "diffuse_color", "value": [0.05, 0.8, 0.1]},
             {"field": "viewport_diffuse", "value": [0.05, 0.8, 0.1]},
             {"field": "diffuse_alpha", "value": 0.35}]
    if cmds.nodeType(shader) in {"dx11Shader", "GLSLShader"}:
        edits.append({"field": "viewport_diffuse_alpha", "value": 0.35})
    applied = json.loads(cmds.mmdAuthoringSetMaterialValues(payload=json.dumps({
        "version": 1, "root": root, "shader": shader,
        "material_index": index, "updates": edits,
    })))
    assert applied["ok"], applied
    changed = witness()
    changed_image = capture("material_changed.png")
    before_pixels = read_png_rgb(before_image)[2]
    changed_pixels = read_png_rgb(changed_image)[2]
    changed_count = sum(max(abs(a - b) for a, b in zip(left, right)) > 8
                        for left, right in zip(before_pixels, changed_pixels))
    assert changed_count > 100, "material data changed but viewport pixels did not"
    assert abs(changed["materialValues"]["DiffuseColorRGB"][1] - 0.8) < 1e-5, changed
    assert abs(changed["diffuseAlpha"] - 0.35) < 1e-5, changed
    cmds.undo()
    undone = witness()
    assert undone["materialValues"] == before["materialValues"] and undone["diffuseAlpha"] == before["diffuseAlpha"], (before, undone)
    cmds.redo()
    redone = witness()
    assert redone["materialValues"] == changed["materialValues"] and redone["diffuseAlpha"] == changed["diffuseAlpha"]
    cmds.undo()
    flags_plug = f"{shader}.mmd_draw_flags"
    flags = cmds.getAttr(flags_plug)
    cmds.setAttr(flags_plug, flags ^ 0x1C)
    flagged = witness()
    assert flagged["selfShadowMap"] == bool((flags ^ 0x1C) & 4), flagged
    assert flagged["selfShadow"] == bool((flags ^ 0x1C) & 8), flagged
    cmds.undo()
    restored = witness()
    assert restored["selfShadowMap"] == before["selfShadowMap"] and restored["selfShadow"] == before["selfShadow"]
    texture = output_dir / "material_probe_texture.png"
    write_png_rgb(texture, 4, 4, [(30, 70, 210)] * 16)
    for attribute, slot in (
        ("mmd_resolved_texture_path", "mainTexture"),
        ("mmd_resolved_sphere_texture_path", "sphereTexture"),
        ("mmd_resolved_toon_texture_path", "toonTexture"),
    ):
        cmds.undoInfo(openChunk=True)
        try:
            if slot == "toonTexture":
                cmds.setAttr(f"{shader}.mmd_shared_toon_flag", 0)
            cmds.setAttr(f"{shader}.{attribute}", str(texture), type="string")
        finally:
            cmds.undoInfo(closeChunk=True)
        textured = witness()
        assert textured[f"{slot}Path"] == str(texture), textured
        assert textured[f"{slot}Acquired"], textured
        cmds.undo()
        assert witness()[f"{slot}Path"] == before[f"{slot}Path"]

    # Real evaluator DG -> native queue -> committed GPU material, including
    # graph removal restoring the editable authored base connection.
    from mmd_tools.converters.material_morph_runtime import bind_native_material_alpha
    evaluator = cmds.createNode("mmdMaterialMorphEval", name="native_material_probe")
    binding = bind_native_material_alpha(root, shape, shaders_by_index={index: shader},
                                         evaluators_by_shader={shader: evaluator})
    assert binding["success"], binding
    cmds.setAttr(f"{evaluator}.contribution[0].operationType", 1)
    for suffix, value in zip("RGBA", (0.1, -0.2, 0.1, -0.4)):
        cmds.setAttr(f"{evaluator}.contribution[0].diffuseOffset{suffix}", value)
    zero = witness()
    cmds.setAttr(f"{evaluator}.contribution[0].weight", 1.0)
    one = witness()
    assert one["materialValues"] != zero["materialValues"] and one["diffuseAlpha"] < zero["diffuseAlpha"]
    cmds.setAttr(f"{evaluator}.contribution[0].weight", 0.0)
    assert witness()["materialValues"] == zero["materialValues"]
    cmds.delete(evaluator)
    rebound = bind_native_material_alpha(root, shape, shaders_by_index={index: shader}, evaluators_by_shader={})
    assert rebound["success"], rebound
    assert witness()["materialValues"] == before["materialValues"]
    from mmd_tools.adapters.maya_cmds_adapter import MayaCmdsAdapter
    from mmd_tools.adapters.maya_material_authoring import MayaMaterialAuthoring
    adapter = MayaMaterialAuthoring(MayaCmdsAdapter())
    cmds.undoInfo(openChunk=True)
    try:
        adapter.apply_material_reindex_fast(root, 0, 1)
    finally:
        cmds.undoInfo(closeChunk=True)
    index = 1
    assert witness()["materialValues"] == before["materialValues"], "material identity changed on reindex"
    cmds.undo()
    index = 0
    assert witness()["materialValues"] == before["materialValues"], "material identity changed on undo"
    return {"valueApplyUndoRedo": True, "drawFlagsUndo": True,
            "textureSwapUndo": True, "materialMorphRoundTrip": True,
            "materialReindexUndo": True, "materialIndex": index, "changedPixels": changed_count}
