"""Negative controls for the product Ordered renderer and its resource owner."""

import json

from tools.render_override.common import capture_view
from tools.render_override.render_override_visual_gate import read_png_rgb


def check_renderer_lifecycle(cmds, panel, plugin, output_dir):
    """Check missing-light fail-closed drawing, plain scenes and safe unload."""
    from mmd_tools.converters.light_converter import find_mmd_light

    def capture(label):
        cmds.refresh(force=True)
        return read_png_rgb(capture_view(cmds, output_dir / (label + ".png"), panel, 1024, 1024))[2]

    light = find_mmd_light()
    assert light
    before = capture("light_present")
    cmds.setAttr(light + ".mmd_light", False)
    missing = capture("light_missing")
    witness = json.loads(cmds.mmdOrderedRenderWitness(shadowDepth=True))
    assert witness["drawCount"] > 0 and not witness["error"], witness
    assert witness["casterDrawCount"] == 0 and witness["receiverDrawCount"] == 0
    assert not witness["frameResourcesReady"] and not witness["sameFrameShadowReady"]
    assert not witness["casterMaterialIndices"]
    assert sum(pixel != missing[0] for pixel in missing) > 1000, "missing light erased the model"
    cmds.setAttr(light + ".mmd_light", True)
    assert capture("light_restored") == before
    name = cmds.pluginInfo(str(plugin), query=True, name=True)
    rejected = False
    try:
        cmds.unloadPlugin(name)
    except RuntimeError:
        rejected = True
    assert rejected and cmds.pluginInfo(name, query=True, loaded=True)
    assert capture("live_unload_rejected") == before

    cmds.file(new=True, force=True)
    cmds.polySphere(radius=2)
    cmds.lookThru(panel, "persp")
    cmds.viewFit("persp", fitFactor=.8, animate=False)
    cmds.select(clear=True)
    cmds.modelEditor(panel, edit=True, rendererOverrideName="", displayLights="default")
    stock = capture("plain_stock")
    assert sum(pixel != stock[0] for pixel in stock) > 1000
    cmds.modelEditor(panel, edit=True, rendererOverrideName="mmdOrdered")
    assert capture("plain_ordered") == stock, "Ordered changed a scene with no MMD shapes"
    cmds.file(new=True, force=True)
    cmds.refresh(force=True)
    cmds.unloadPlugin(name)
    assert not cmds.pluginInfo(name, query=True, loaded=True)
    return {"missingLightFailClosed": True, "missingLightRestore": True,
            "liveUnloadRejected": True, "nonMmdScenePreserved": True, "emptySceneUnload": True}
