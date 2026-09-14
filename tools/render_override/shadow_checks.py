"""Acceptance checks for the existing Ordered self-shadow path."""

import json

from tools.render_override.common import capture_view
from tools.render_override.render_override_visual_gate import read_png_rgb


def check_light_binding(cmds, panel, output_dir):
    """Verify that body lighting follows the MMD controller with shadows off."""
    from mmd_tools.converters.light_converter import find_mmd_light

    light = find_mmd_light()
    assert light, "missing model light controller"
    mode_plug = f"{light}.mmd_self_shadow_mode"
    color_plug = f"{light}.mmd_light_color"
    rotation_plug = f"{light}.rotateY"
    original_mode = cmds.getAttr(mode_plug)
    original_color = cmds.getAttr(color_plug)[0]
    original_rotation = cmds.getAttr(rotation_plug)

    def capture(label):
        cmds.refresh(force=True)
        path = capture_view(cmds, output_dir / f"light_{label}.png", panel, 1024, 1024)
        witness = json.loads(cmds.mmdOrderedRenderWitness())
        assert not witness["error"] and witness["drawCount"] > 0, witness
        return read_png_rgb(path)[2]

    try:
        cmds.setAttr(mode_plug, 0)
        baseline = capture("baseline")
        cmds.setAttr(color_plug, 0.08, 0.95, 0.1, type="float3")
        color_changed = capture("color_changed")
        cmds.setAttr(color_plug, *original_color, type="float3")
        assert capture("color_restored") == baseline
        cmds.setAttr(rotation_plug, original_rotation + 120.0)
        direction_changed = capture("direction_changed")
        cmds.setAttr(rotation_plug, original_rotation)
        assert capture("direction_restored") == baseline
        color_pixels = sum(a != b for a, b in zip(baseline, color_changed))
        direction_pixels = sum(a != b for a, b in zip(baseline, direction_changed))
        assert color_pixels > 100, "MMD light color did not affect native body"
        assert direction_pixels > 100, "MMD light direction did not affect native body"
        return {"colorChangedPixels": color_pixels,
                "directionChangedPixels": direction_pixels}
    finally:
        cmds.setAttr(color_plug, *original_color, type="float3")
        cmds.setAttr(rotation_plug, original_rotation)
        cmds.setAttr(mode_plug, original_mode)


def check_self_shadow(cmds, root, shape, panel, output_dir):
    """Check mode and PMX flag A/B/A on an imported, textured representative."""
    from mmd_tools.converters.light_converter import find_mmd_light
    from mmd_tools.converters.material_morph_runtime import _collect_shaders_by_material_index
    from tools.render_override.common import make_parity_camera

    light = find_mmd_light()
    assert light, "missing model light controller"
    camera = make_parity_camera(cmds, {"position": [0.04, 0.58, 3.4],
        "target": [0.04, 0.58, 0.0], "fov": 28.0, "near": 0.1, "far": 10000.0})
    cmds.lookThru(panel, camera)
    cmds.select(root)
    cmds.viewFit(camera, fitFactor=0.9, animate=False)
    cmds.select(clear=True)
    cmds.modelEditor(panel, edit=True, rendererOverrideName="mmdOrdered")
    mode_plug = f"{light}.mmd_self_shadow_mode"
    original_mode = cmds.getAttr(mode_plug)
    stages = {}

    def capture(label):
        cmds.refresh(force=True)
        path = capture_view(cmds, output_dir / f"shadow_{label}.png", panel, 1024, 1024)
        witness = json.loads(cmds.mmdOrderedRenderWitness(shadowDepth=True))
        assert not witness["error"], witness
        stages[label] = {"image": str(path), "witness": witness}
        return read_png_rgb(path)[2]

    shaders = _collect_shaders_by_material_index(root)
    flags = {shader: cmds.getAttr(f"{shader}.mmd_draw_flags") for shader in shaders.values()}
    try:
        cmds.setAttr(mode_plug, 0)
        off = capture("off")
        cmds.setAttr(mode_plug, 1)
        on = capture("on")
        witness = stages["on"]["witness"]
        assert witness["casterDrawCount"] > 0 and witness["receiverDrawCount"] > 0
        assert witness["sameFrameShadowReady"] and witness["shadowDepth"]["writtenSamples"] > 0
        assert witness["shadowDepth"]["invalidSamples"] == 0
        # Ordered owns GPU draws now; derive expected PMX caster membership
        # from the canonical material instead of retired GeometryOverride items.
        alpha_casters = {index for index, shader in shaders.items()
                         if flags[shader] & 4 and cmds.getAttr(shader + ".mmd_resolved_texture_path")
                         and (cmds.getAttr(shader + ".mmdTransparencyMode") != "opaque"
                              or cmds.getAttr(shader + ".mmd_diffuse_alpha") < 1.0)}
        assert alpha_casters, "fixture must contain textured transparent casters"
        assert alpha_casters <= set(witness["casterMaterialIndices"])
        for index in alpha_casters:
            shader = shaders[index]
            cmds.setAttr(shader + ".mmd_draw_flags", flags[shader] & ~4)
        capture("alpha_casters_off")
        alpha_off = stages["alpha_casters_off"]["witness"]
        assert not alpha_casters.intersection(alpha_off["casterMaterialIndices"])
        assert alpha_off["shadowDepth"]["hash"] != witness["shadowDepth"]["hash"], "alpha casters wrote no depth"
        for index in alpha_casters:
            shader = shaders[index]
            cmds.setAttr(shader + ".mmd_draw_flags", flags[shader])
        assert capture("alpha_casters_restored") == on
        cmds.setAttr(mode_plug, 2)
        mode2 = capture("mode2")
        mode2_witness = stages["mode2"]["witness"]
        assert mode2_witness["selfShadowMode"] == 2 and mode2_witness["sameFrameShadowReady"]
        assert mode2_witness["shadowDepth"]["writtenSamples"] > 0 and mode2_witness["shadowDepth"]["invalidSamples"] == 0
        assert mode2 != off, "mode2 did not produce visible self-shadow"
        cmds.setAttr(mode_plug, 1)
        assert capture("restored") == on, "self-shadow mode round trip changed pixels"
        changed = sum(a != b for a, b in zip(off, on))
        assert changed > 100, "self-shadow ON produced no meaningful image change"

        for shader, value in flags.items():
            cmds.setAttr(f"{shader}.mmd_draw_flags", value & ~4)
        capture("casters_off")
        assert stages["casters_off"]["witness"]["casterDrawCount"] == 0
        for shader, value in flags.items():
            # `value` is the ORIGINAL PMX flag record, so this simultaneously
            # restores the caster bit and disables only the receiver bit.
            cmds.setAttr(f"{shader}.mmd_draw_flags", value & ~8)
        capture("receivers_off")
        assert stages["receivers_off"]["witness"]["receiverDrawCount"] == 0
        assert stages["receivers_off"]["witness"]["casterDrawCount"] == witness["casterDrawCount"]
        for shader, value in flags.items():
            cmds.setAttr(f"{shader}.mmd_draw_flags", value)
        assert capture("flags_restored") == on, "PMX flag round trip changed pixels"
        return {"modeRoundTrip": True, "casterReceiverFlags": True,
                "texturedTransparentCasters": sorted(alpha_casters),
                "changedPixels": changed, "stages": stages}
    finally:
        for shader, value in flags.items():
            cmds.setAttr(f"{shader}.mmd_draw_flags", value)
        cmds.setAttr(mode_plug, original_mode)
