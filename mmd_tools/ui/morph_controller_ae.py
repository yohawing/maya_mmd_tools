"""Attribute Editor template for the model-scoped PMX morph controller."""

from __future__ import annotations

import json
import re

from maya import cmds, mel

from mmd_tools.core.constants import ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON
from mmd_tools.core.morph_metadata_reader import parse_blendshape_morph_entries


_WEIGHT_INDEX = re.compile(r"(?:weight|w)\[(\d+)\]$")


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
    """Build value sliders for every authored morph input element."""
    cmds.editorTemplate(beginScrollLayout=True)
    cmds.editorTemplate(beginLayout="Morph Weights", collapse=False)
    _build_weight_controls(node_name)
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
