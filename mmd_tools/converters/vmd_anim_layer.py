"""Animation layer helpers shared by VMD and VPD converters."""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

import maya.cmds as cmds


TRANSFORM_LAYER_ATTRS = (
    "translateX",
    "translateY",
    "translateZ",
    "rotateX",
    "rotateY",
    "rotateZ",
)


def add_existing_attrs_to_anim_layer(anim_layer: Optional[str], node: str, attrs: Iterable[str]) -> None:
    """Add existing node attributes to an animation layer."""
    if not anim_layer or not cmds.objExists(node):
        return

    for attr in attrs:
        if cmds.attributeQuery(attr, node=node, exists=True):
            cmds.animLayer(anim_layer, edit=True, attribute=f"{node}.{attr}")


def add_transform_attrs_to_anim_layer(anim_layer: Optional[str], objects: Sequence[str]) -> None:
    """Add standard transform channels for existing objects to an animation layer."""
    for obj in objects:
        add_existing_attrs_to_anim_layer(anim_layer, obj, TRANSFORM_LAYER_ATTRS)
