"""Real-panel A/B/A checks for native display mode and source visibility."""

import json

from tools.render_override.common import capture_view, write_report
from tools.render_override.render_override_visual_gate import read_png_rgb


def check_display_modes(cmds, root, shape, panel, output_dir):
    """Require visible wire geometry, texture changes and reversible Hide."""
    source = cmds.listConnections(f"{shape}.inputMesh", source=True, destination=False, shapes=True)[0]
    source_transform = cmds.listRelatives(source, parent=True, fullPath=True)[0]
    original_style = cmds.modelEditor(panel, query=True, displayAppearance=True)
    original_textures = cmds.modelEditor(panel, query=True, displayTextures=True)
    original_visibility = cmds.getAttr(f"{source_transform}.visibility")
    stages = {}

    def capture(label):
        cmds.refresh(force=True)
        # Do not let playblast become the operation undone by the Hide test.
        cmds.undoInfo(stateWithoutFlush=False)
        try:
            path = capture_view(cmds, output_dir / f"display_{label}.png", panel, 640, 480)
        finally:
            cmds.undoInfo(stateWithoutFlush=True)
        witness = json.loads(cmds.mmdOrderedRenderWitness(shadowDepth=True))
        assert not witness["error"], witness
        stages[label] = {"image": str(path), "witness": witness,
                         "style": cmds.modelEditor(panel, query=True, displayAppearance=True),
                         "textures": cmds.modelEditor(panel, query=True, displayTextures=True)}
        write_report(output_dir / "display_checks.json", {"stages": stages})
        return read_png_rgb(path)[2]

    def difference(left, right):
        return sum(max(abs(a - b) for a, b in zip(x, y)) > 8 for x, y in zip(left, right))

    try:
        cmds.select(clear=True)
        cmds.modelEditor(panel, edit=True, displayAppearance="smoothShaded", displayTextures=True)
        textured = capture("textured")
        assert stages["textured"]["witness"]["drawCount"] > 0
        cmds.modelEditor(panel, edit=True, displayTextures=False)
        plain = capture("untextured")
        assert difference(textured, plain) > 100, "texture toggle did not change the native image"
        cmds.modelEditor(panel, edit=True, displayTextures=True)
        assert capture("texture_restored") == textured

        cmds.modelEditor(panel, edit=True, displayAppearance="wireframe")
        wire = capture("wireframe")
        assert stages["wireframe"]["witness"]["drawCount"] == 0, "filled Ordered draws survived wireframe"
        assert stages["wireframe"]["witness"]["casterDrawCount"] == 0
        cmds.setAttr(f"{source_transform}.visibility", False)
        wire_hidden = capture("wire_hidden")
        assert stages["wire_hidden"]["witness"]["drawCount"] == 0
        assert stages["wire_hidden"]["witness"]["casterDrawCount"] == 0
        assert difference(wire, wire_hidden) > 100, "wireframe became an empty viewport"
        cmds.setAttr(f"{source_transform}.visibility", True)
        cmds.modelEditor(panel, edit=True, displayAppearance="smoothShaded")
        assert capture("shaded_restored") == textured

        cmds.setAttr(f"{source_transform}.visibility", False)
        hidden = capture("hidden")
        assert stages["hidden"]["witness"]["drawCount"] == 0, "source Hide left proxy draws"
        assert stages["hidden"]["witness"]["casterDrawCount"] == 0
        assert difference(textured, hidden) > 100
        cmds.undo()
        assert capture("hide_undone") == textured
        return {"textureRoundTrip": True, "wireframeRoundTrip": True,
                "sourceHideUndo": True, "textureChangedPixels": difference(textured, plain),
                "wirePixels": difference(wire, wire_hidden), "stages": stages}
    finally:
        cmds.setAttr(f"{source_transform}.visibility", original_visibility)
        cmds.modelEditor(panel, edit=True, displayAppearance=original_style, displayTextures=original_textures)
