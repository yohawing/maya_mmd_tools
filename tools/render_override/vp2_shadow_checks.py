"""Bounded native-shadow comparison using the existing Ordered effects fixture.

This intentionally tests standardSurface, not an unimplemented MMD VP2 shader.
Native shadow requests are not evidence of completed same-frame map updates.
"""

import hashlib
from pathlib import Path

from tools.render_override.common import capture_view
from tools.render_override.render_override_visual_gate import read_png_rgb
from tools.render_override.vp2_feasibility import make_override


def image_difference(first, second):
    """Reject incompatible captures before reporting changed pixel counts."""
    if first[:2] != second[:2] or len(first[2]) != len(second[2]):
        raise ValueError("shadow comparison image dimensions differ")
    return sum(a != b for a, b in zip(first[2], second[2]))


def check_vp2_shadow(cmds, root, panel, output_dir, baseline):
    """Compare native whole-scene and per-material passes against fresh Ordered."""
    from maya.api import OpenMayaRender as omr
    from mmd_tools.converters.light_converter import find_mmd_light
    from mmd_tools.converters.material_morph_runtime import _collect_shaders_by_material_index

    shaders = _collect_shaders_by_material_index(root)
    indices = {shader: index for index, shader in shaders.items()}
    proxies = cmds.listRelatives(root, ad=True, type="mmdRenderShape", fullPath=True) or []
    sources = []
    for proxy in proxies:
        upstream = cmds.connectionInfo(proxy + ".inputMesh", sourceFromDestination=True)
        source = upstream.rsplit(".", 1)[0]
        groups = cmds.listConnections(source, type="shadingEngine") or []
        assigned = {s for group in groups for s in
                    (cmds.listConnections(group + ".surfaceShader", source=True, destination=False) or [])}
        if len(assigned) != 1 or next(iter(assigned)) not in indices:
            return {"status": "not_run", "classification": "insufficient",
                    "reason": "native flag comparison requires one canonical material per source mesh"}
        sources.append((source, next(iter(assigned))))
    if not sources:
        raise RuntimeError("no ordinary source meshes for native shadow comparison")
    light_root = find_mmd_light()
    lights = cmds.listRelatives(light_root, shapes=True, type="directionalLight", fullPath=True) or []
    if not lights:
        raise RuntimeError("MMD light has no native directionalLight")
    override = make_override()
    original_override = cmds.modelEditor(panel, query=True, rendererOverrideName=True)
    original_lighting = cmds.modelEditor(panel, query=True, displayLights=True)
    original_shadows = cmds.modelEditor(panel, query=True, shadows=True)
    saved = {}

    def set_attr(plug, value):
        if plug not in saved:
            saved[plug] = cmds.getAttr(plug)
        cmds.setAttr(plug, value)

    result = {"status": "fail", "scope": "native standardSurface shadow capability; not MMD parity",
              "probeSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "stages": {}, "sameFixtureCameraViewport": True,
              "shadowMapResolution": 2048,
              "sameFrameShadowConsumption": {"status": "not_run", "classification": "insufficient",
                  "reason": "request API cannot witness map completion or same-frame receiver binding"},
              "mode1Mode2": {"status": "not_run", "classification": "insufficient",
                  "reason": "native shadows have no implemented MMD MODE1/MODE2 projection mapping"}}
    omr.MRenderer.registerOverride(override)
    try:
        cmds.modelEditor(panel, edit=True, rendererOverrideName="", displayLights="all", shadows=True)
        for source, _ in sources:
            set_attr(source + ".visibility", True)
        for light in lights:
            set_attr(light + ".useDepthMapShadows", True)
            set_attr(light + ".dmapResolution", 2048)
        override.request_shadows(lights)
        for route in ("whole", "filtered"):
            pixels = {}
            for label in ("off", "on", "native_on_control", "alpha_casters_off", "casters_off", "receivers_off", "restored"):
                cmds.modelEditor(panel, edit=True, rendererOverrideName="")
                for source, shader in sources:
                    flags = cmds.getAttr(shader + ".mmd_draw_flags")
                    alpha = indices[shader] in baseline["texturedTransparentCasters"]
                    set_attr(source + ".castsShadows", bool(flags & 4) and label != "casters_off"
                             and not (label == "alpha_casters_off" and alpha))
                    set_attr(source + ".receiveShadows", bool(flags & 8) and label != "receivers_off")
                for light in lights:
                    set_attr(light + ".useDepthMapShadows", label != "off")
                members = [source for source, _ in sources]
                # The object set also filters scene lights. Keep the same light
                # in every pass; otherwise an ambient-only image is no shadow test.
                override.configure([None] if route == "whole"
                                   else [[source] + lights for source in members])
                cmds.modelEditor(panel, edit=True, rendererOverrideName="vp2Feasibility")
                cmds.refresh(force=True)
                path = capture_view(cmds, output_dir / f"vp2_{route}_{label}.png", panel, 1024, 1024)
                pixels[label] = read_png_rgb(path)
                result["stages"][route + "_" + label] = {"image": str(path),
                    "requestedCasterSources": [source for source, _ in sources if cmds.getAttr(source + ".castsShadows")],
                    "nativeShadowEnabled": label != "off", "mmdModeMapping": "off" if label == "off" else "native-on"}
            deltas = {label: image_difference(pixels["on"], pixels[label])
                      for label in ("off", "native_on_control", "alpha_casters_off", "casters_off", "receivers_off", "restored")}
            ordered_on = read_png_rgb(Path(baseline["stages"]["on"]["image"]))
            result[route] = {"changedPixelsFromOn": deltas,
                "rawOrderedChangedPixels": image_difference(ordered_on, pixels["on"]),
                "classification": "different" if deltas["off"] > 100 else "insufficient",
                "reason": "native appearance and shadow projection differ; no MMD parity oracle",
                "roundTripStable": deltas["restored"] == 0}
            result[route]["capabilities"] = {
                label: {"classification": "different" if deltas[label] > 100 else "insufficient",
                        "changedPixels": deltas[label],
                        "reason": "native toggle has visible effect; MMD parity not established"
                        if deltas[label] > 100 else "no meaningful native toggle effect observed"}
                for label in ("off", "alpha_casters_off", "casters_off", "receivers_off")}
        result["status"] = "pass"
        return result
    finally:
        cmds.modelEditor(panel, edit=True, rendererOverrideName="")
        omr.MRenderer.deregisterOverride(override)
        override.operations = []
        for light in lights:
            omr.MRenderer.setLightRequiresShadows(light, False)
        for plug, value in saved.items():
            cmds.setAttr(plug, value)
        cmds.modelEditor(panel, edit=True, rendererOverrideName=original_override,
                         displayLights=original_lighting, shadows=original_shadows)
