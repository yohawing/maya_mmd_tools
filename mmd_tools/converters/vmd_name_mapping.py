"""Scene name-mapping helpers for VMD conversion."""

from __future__ import annotations

from typing import Any, Optional, Union

import maya.cmds as cmds

from ..core import maya_scene_utils
from ..core.constants import ATTR_MMD_BONE_INDEX, ATTR_MMD_BONE_NAME
from .vmd_context import VmdNameMappingContext


def _resolve_name_mapping_context(converter_or_context: Union[Any, VmdNameMappingContext]) -> VmdNameMappingContext:
    if isinstance(converter_or_context, VmdNameMappingContext):
        return converter_or_context
    factory = getattr(converter_or_context, "_name_mapping_context", None)
    if callable(factory):
        return factory()
    if not hasattr(converter_or_context, "bone_name_to_index"):
        converter_or_context.bone_name_to_index = {}
    if not hasattr(converter_or_context, "bone_index_to_joint"):
        converter_or_context.bone_index_to_joint = {}
    return VmdNameMappingContext(
        logger=converter_or_context.logger,
        bone_name_mapping=converter_or_context.bone_name_mapping,
        bone_name_to_index=converter_or_context.bone_name_to_index,
        bone_index_to_joint=converter_or_context.bone_index_to_joint,
        build_morph_mappings=converter_or_context._build_morph_mappings,
    )


def build_name_mappings(converter_or_context: Union[Any, VmdNameMappingContext], target_namespace: Optional[str] = None) -> None:
    """Build bone name/index mappings and refresh morph mappings for a scene."""
    context = _resolve_name_mapping_context(converter_or_context)
    context.logger.info("Building name mapping")

    context.bone_name_to_index.clear()
    context.bone_index_to_joint.clear()

    if target_namespace:
        joints = maya_scene_utils.list_objects(object_filter=f"{target_namespace}:*", type="joint")
    else:
        joints = maya_scene_utils.list_objects(type="joint")

    for joint in joints:
        if cmds.attributeQuery(ATTR_MMD_BONE_NAME, node=joint, exists=True):
            original_name = cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_NAME}")
            if original_name:
                context.bone_name_mapping[original_name] = joint

                if cmds.attributeQuery(ATTR_MMD_BONE_INDEX, node=joint, exists=True):
                    try:
                        idx = cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_INDEX}")
                        if idx is not None:
                            idx = int(idx)
                            context.bone_name_to_index[original_name] = idx
                            context.bone_index_to_joint[idx] = joint
                    except Exception:
                        pass

    context.logger.info(
        f"Built {len(context.bone_name_mapping)} bone mappings "
        f"(index mappings: {len(context.bone_index_to_joint)})"
    )

    context.build_morph_mappings()
