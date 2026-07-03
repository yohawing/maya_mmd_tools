"""Scene name-mapping helpers for VMD conversion."""

from __future__ import annotations

from typing import Dict, Optional

import maya.cmds as cmds

from ..core import maya_utils
from ..core.constants import ATTR_MMD_BONE_INDEX, ATTR_MMD_BONE_NAME


def build_name_mappings(converter, target_namespace: Optional[str] = None) -> None:
    """Build bone name/index mappings and refresh morph mappings for a scene."""
    converter.logger.info("Building name mapping")

    converter.bone_name_to_index: Dict[str, int] = {}
    converter.bone_index_to_joint: Dict[int, str] = {}

    if target_namespace:
        joints = maya_utils.list_objects(object_filter=f"{target_namespace}:*", type="joint")
    else:
        joints = maya_utils.list_objects(type="joint")

    for joint in joints:
        if cmds.attributeQuery(ATTR_MMD_BONE_NAME, node=joint, exists=True):
            original_name = cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_NAME}")
            if original_name:
                converter.bone_name_mapping[original_name] = joint

                if cmds.attributeQuery(ATTR_MMD_BONE_INDEX, node=joint, exists=True):
                    try:
                        idx = cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_INDEX}")
                        if idx is not None:
                            idx = int(idx)
                            converter.bone_name_to_index[original_name] = idx
                            converter.bone_index_to_joint[idx] = joint
                    except Exception:
                        pass

    converter.logger.info(
        f"Built {len(converter.bone_name_mapping)} bone mappings "
        f"(index mappings: {len(converter.bone_index_to_joint)})"
    )

    converter._build_morph_mappings()
