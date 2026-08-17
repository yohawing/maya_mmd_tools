"""Attribute Editor template for the model-scoped PMX morph controller."""

from __future__ import annotations

import json
import re

from maya import cmds, mel

from mmd_tools.core.constants import ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON
from mmd_tools.core.morph_metadata_reader import parse_blendshape_morph_entries


_WEIGHT_INDEX = re.compile(r"(?:weight|w)\[(\d+)\]$")
_WEIGHT_COLUMNS: list[str] = []


def _destination_label(node_name: str, index: int) -> str | None:
    """Resolve one semantic morph name from an owned output destination."""
    destinations = cmds.listConnections(
        f"{node_name}.outputWeight[{index}]",
        source=False,
        destination=True,
        plugs=True,
    ) or []
    for destination in destinations:
        target, _, attribute = str(destination).partition(".")
        try:
            if cmds.attributeQuery("mmd_morph_index", node=target, exists=True):
                if int(cmds.getAttr(f"{target}.mmd_morph_index")) != index:
                    continue
                name = cmds.getAttr(f"{target}.mmd_morph_name")
                if name:
                    return str(name)
            if (
                cmds.nodeType(target) == "blendShape"
                and cmds.attributeQuery(
                    ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON,
                    node=target,
                    exists=True,
                )
            ):
                match = _WEIGHT_INDEX.fullmatch(attribute)
                if match is None:
                    continue
                raw = cmds.getAttr(f"{target}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}")
                entries = parse_blendshape_morph_entries(json.loads(raw or "{}"))
                entry = entries.get(int(match.group(1)))
                if entry and entry.get("name"):
                    return str(entry["name"])
        except (RuntimeError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return None


def build(node_name):
    """Build the conventional AE template when called directly by extensions."""
    cmds.editorTemplate(beginScrollLayout=True)
    cmds.editorTemplate(beginLayout="Morph Weights", collapse=False)
    _new_weight_controls(f"{node_name}.inputWeight")
    cmds.editorTemplate(endLayout=True)
    cmds.editorTemplate(addExtraControls=True)
    cmds.editorTemplate(endScrollLayout=True)


def _build_weight_controls(node_name: str) -> list[str]:
    """Create concrete value controls and return their Maya UI names."""
    controls = []
    indices = cmds.getAttr(f"{node_name}.inputWeight", multiIndices=True) or []
    for index in indices:
        plug = f"{node_name}.inputWeight[{index}]"
        label = _destination_label(node_name, int(index))
        label = label or cmds.aliasAttr(plug, query=True) or f"Morph {index}"
        # editorTemplate(addControl=...) does not reliably materialize controls
        # for multi elements.  Bind a concrete AE slider to the full plug.
        controls.append(
            cmds.attrFieldSliderGrp(
                attribute=plug,
                label=label,
                minValue=0.0,
                maxValue=1.0,
                precision=3,
            )
        )
    return controls


def _new_weight_controls(attribute_name: str) -> str:
    """Create the weight column under callCustom's current AE layout."""
    node_name = str(attribute_name).split(".", 1)[0]
    previous_parent = cmds.setParent(query=True)
    column = cmds.columnLayout(adjustableColumn=True)
    _WEIGHT_COLUMNS.append(column)
    try:
        _build_weight_controls(node_name)
    finally:
        cmds.setParent(previous_parent)
    return column


def _replace_weight_controls(attribute_name: str) -> None:
    """Retarget reusable AE custom controls when Maya changes the shown node."""
    node_name = str(attribute_name).split(".", 1)[0]
    live_columns = []
    for column in _WEIGHT_COLUMNS:
        if not cmds.layout(column, exists=True):
            continue
        live_columns.append(column)
        for child in cmds.layout(column, query=True, childArray=True) or []:
            cmds.deleteUI(child)
        previous_parent = cmds.setParent(query=True)
        cmds.setParent(column)
        try:
            _build_weight_controls(node_name)
        finally:
            cmds.setParent(previous_parent)
    _WEIGHT_COLUMNS[:] = live_columns


def install():
    """Register the conventional Maya AE template entry point."""
    mel.eval(
        r'''
global proc AEmmdMorphControllerTemplate(string $nodeName)
{
    editorTemplate -beginScrollLayout;
    editorTemplate -beginLayout "Morph Weights" -collapse false;
    editorTemplate -callCustom "AEmmdMorphControllerWeightsNew" "AEmmdMorphControllerWeightsReplace" "inputWeight";
    editorTemplate -endLayout;
    editorTemplate -addExtraControls;
    editorTemplate -endScrollLayout;
}

global proc AEmmdMorphControllerWeightsNew(string $attrName)
{
    python("from mmd_tools.ui import morph_controller_ae; morph_controller_ae._new_weight_controls('" + $attrName + "')");
}

global proc AEmmdMorphControllerWeightsReplace(string $attrName)
{
    python("from mmd_tools.ui import morph_controller_ae; morph_controller_ae._replace_weight_controls('" + $attrName + "')");
}
'''
    )
    if not cmds.about(batch=True):
        mel.eval("refreshEditorTemplates;")
