"""Scene name-mapping helpers for VMD conversion."""

from __future__ import annotations

import inspect
from typing import Any, Optional, Union

import maya.api.OpenMaya as om
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


def build_name_mappings(
    converter_or_context: Union[Any, VmdNameMappingContext],
    target_namespace: Optional[str] = None,
    target_model: Optional[str] = None,
) -> None:
    """Build bone name/index mappings and refresh morph mappings for a scene."""
    context = _resolve_name_mapping_context(converter_or_context)
    context.logger.debug("Building name mapping")

    context.bone_name_to_index.clear()
    context.bone_index_to_joint.clear()
    context.bone_name_mapping.clear()

    if target_model:
        if cmds.objExists(target_model):
            joints = cmds.listRelatives(
                target_model,
                allDescendents=True,
                type="joint",
                fullPath=True,
            ) or []
        else:
            context.logger.warning("Target model does not exist; bone mapping is empty: %s", target_model)
            joints = []
    elif target_namespace:
        joints = maya_scene_utils.list_objects(object_filter=f"{target_namespace}:*", type="joint")
    else:
        joints = maya_scene_utils.list_objects(type="joint")

    for joint in joints:
        try:
            selection = om.MSelectionList()
            selection.add(joint)
            node_fn = om.MFnDependencyNode(selection.getDependNode(0))
            original_name = node_fn.findPlug(ATTR_MMD_BONE_NAME, False).asString()
        except (IndexError, RuntimeError):
            continue

        if not original_name:
            continue
        context.bone_name_mapping[original_name] = joint

        try:
            index_plug = node_fn.findPlug(ATTR_MMD_BONE_INDEX, False)
            if not index_plug.attribute().hasFn(om.MFn.kNumericAttribute):
                continue
            idx = index_plug.asInt()
        except RuntimeError:
            continue
        context.bone_name_to_index[original_name] = idx
        context.bone_index_to_joint[idx] = joint

    context.logger.debug(
        f"Built {len(context.bone_name_mapping)} bone mappings "
        f"(index mappings: {len(context.bone_index_to_joint)})"
    )

    _refresh_morph_mappings(context.build_morph_mappings, target_model)


def _refresh_morph_mappings(callback, target_model: Optional[str]) -> None:
    """Call old no-arg and new root-aware morph refresh callbacks safely."""
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        # Internal/bound callbacks in supported Maya Python expose signatures.
        # Unknown callables retain the historical no-argument contract.
        callback()
        return

    parameters = tuple(signature.parameters.values())
    accepts_positional = any(
        parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        for parameter in parameters
    )
    accepts_varargs = any(parameter.kind == inspect.Parameter.VAR_POSITIONAL for parameter in parameters)
    if accepts_positional or accepts_varargs:
        callback(target_model)
    else:
        callback()
