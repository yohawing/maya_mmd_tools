"""Attribute Editor template for the model-scoped PMX morph controller."""

from maya import cmds, mel


def build(node_name):
    """Build blendShape-like controls for the controller's aliased weights."""
    cmds.editorTemplate(beginScrollLayout=True)
    cmds.editorTemplate(beginLayout="Morph Weights", collapse=False)
    indices = cmds.getAttr(f"{node_name}.inputWeight", multiIndices=True) or []
    for index in indices:
        attribute = f"inputWeight[{index}]"
        plug = f"{node_name}.{attribute}"
        label = cmds.aliasAttr(plug, query=True) or f"Morph {index}"
        cmds.editorTemplate(addControl=attribute, label=label)
    cmds.editorTemplate(endLayout=True)
    cmds.editorTemplate(addExtraControls=True)
    cmds.editorTemplate(endScrollLayout=True)


def install():
    """Register the conventional Maya AE template entry point."""
    mel.eval(
        r'''
global proc AEmmdMorphControllerTemplate(string $nodeName)
{
    python("from mmd_tools.ui import morph_controller_ae; morph_controller_ae.build('" + $nodeName + "')");
}
'''
    )
