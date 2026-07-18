"""Compile one deterministic payload-free runtime model snapshot from Maya DAG metadata."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from maya import cmds

from mmd_tools.converters.morph_scene_metadata import iter_morph_network_metadata
from mmd_tools.core.constants import (
    ATTR_MMD_BONE_FLAGS,
    ATTR_MMD_BONE_INDEX,
    ATTR_MMD_BONE_MORPH_OFFSETS_RAW_JSON,
    ATTR_MMD_BONE_PARENT_INDEX,
    ATTR_MMD_DEFORM_LAYER,
    ATTR_MMD_FIXED_AXIS,
    ATTR_MMD_GRANT_PARENT_INDEX,
    ATTR_MMD_GRANT_RATE,
    ATTR_MMD_IK_LIMIT_ANGLE,
    ATTR_MMD_IK_LINKS,
    ATTR_MMD_IK_LOOP,
    ATTR_MMD_IK_TARGET_INDEX,
    ATTR_MMD_LOCAL_X_AXIS,
    ATTR_MMD_LOCAL_Z_AXIS,
    ATTR_MMD_PMX_REST_POSITION,
)
from mmd_tools.core.native.mmd_anim_runtime_types import (
    MMD_RUNTIME_MODEL_APPEND_LOCAL,
    MMD_RUNTIME_MODEL_APPEND_ROTATION,
    MMD_RUNTIME_MODEL_APPEND_TRANSLATION,
    MMD_RUNTIME_MODEL_BONE_FIXED_AXIS,
    MMD_RUNTIME_MODEL_BONE_LOCAL_AXIS,
    MMD_RUNTIME_MODEL_BONE_TRANSFORM_AFTER_PHYSICS,
    MMD_RUNTIME_MODEL_IK_LINK_ANGLE_LIMIT,
    MmdRuntimeFfiModelAppendTransform,
    MmdRuntimeFfiModelBoneMorphOffset,
    MmdRuntimeFfiModelBoneV2,
    MmdRuntimeFfiModelGroupMorphOffset,
    MmdRuntimeFfiModelIkLink,
    MmdRuntimeFfiModelIkSolver,
)
from mmd_tools.core.pmx_data.bone import PmxBoneFlag


class ModelDagDescriptorError(ValueError):
    pass


@dataclass(frozen=True)
class ModelDagDescriptorSet:
    bones: list[MmdRuntimeFfiModelBoneV2]
    ik_solvers: list[MmdRuntimeFfiModelIkSolver]
    ik_links: list[MmdRuntimeFfiModelIkLink]
    append_transforms: list[MmdRuntimeFfiModelAppendTransform]
    morph_count: int
    bone_morph_offsets: list[MmdRuntimeFfiModelBoneMorphOffset]
    group_morph_offsets: list[MmdRuntimeFfiModelGroupMorphOffset]


def _has_attr(node: str, attr: str) -> bool:
    return bool(cmds.attributeQuery(attr, node=node, exists=True))


def _required(node: str, attr: str):
    if not _has_attr(node, attr):
        user_attrs = cmds.listAttr(node, userDefined=True) or []
        raise ModelDagDescriptorError(
            f"{node}: missing required attribute {attr}; user attributes={user_attrs}"
        )
    return cmds.getAttr(f"{node}.{attr}")


def _vector3(node: str, attr: str) -> tuple[float, float, float]:
    raw = _required(node, attr)
    values = raw[0] if isinstance(raw, (list, tuple)) and len(raw) == 1 else raw
    try:
        result = tuple(float(value) for value in values)
    except (TypeError, ValueError) as exc:
        raise ModelDagDescriptorError(f"{node}.{attr}: expected finite double3") from exc
    if len(result) != 3 or not all(math.isfinite(value) for value in result):
        raise ModelDagDescriptorError(f"{node}.{attr}: expected finite double3")
    return result


def _json_list(node: str, attr: str) -> list:
    raw = _required(node, attr)
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError) as exc:
        raise ModelDagDescriptorError(f"{node}.{attr}: invalid JSON") from exc
    if not isinstance(value, list):
        raise ModelDagDescriptorError(f"{node}.{attr}: expected JSON list")
    return value


def _finite_float(node: str, attr: str) -> float:
    try:
        value = float(_required(node, attr))
    except (TypeError, ValueError) as exc:
        raise ModelDagDescriptorError(f"{node}.{attr}: expected finite float") from exc
    if not math.isfinite(value):
        raise ModelDagDescriptorError(f"{node}.{attr}: expected finite float")
    return value


def _indexed_joints(root_group: str) -> list[str]:
    joints = cmds.listRelatives(root_group, allDescendents=True, type="joint", fullPath=True) or []
    if cmds.nodeType(root_group) == "joint":
        joints.append(root_group)
    indexed: dict[int, str] = {}
    for joint in joints:
        if not _has_attr(joint, ATTR_MMD_BONE_INDEX):
            raise ModelDagDescriptorError(f"{joint}: missing required attribute {ATTR_MMD_BONE_INDEX}")
        index = int(cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_INDEX}"))
        if index < 0 or index in indexed:
            raise ModelDagDescriptorError(f"duplicate or invalid bone index {index}")
        indexed[index] = joint
    if not indexed:
        raise ModelDagDescriptorError(f"{root_group}: no indexed MMD bones")
    expected = list(range(max(indexed) + 1))
    if sorted(indexed) != expected:
        raise ModelDagDescriptorError(f"{root_group}: bone indices must be contiguous")
    return [indexed[index] for index in expected]


def build_model_descriptors_from_dag(root_group: str) -> ModelDagDescriptorSet:
    joints = _indexed_joints(root_group)
    bone_count = len(joints)
    bones: list[MmdRuntimeFfiModelBoneV2] = []
    ik_solvers: list[MmdRuntimeFfiModelIkSolver] = []
    ik_links: list[MmdRuntimeFfiModelIkLink] = []
    append_transforms: list[MmdRuntimeFfiModelAppendTransform] = []

    for bone_index, joint in enumerate(joints):
        parent_index = int(_required(joint, ATTR_MMD_BONE_PARENT_INDEX))
        if parent_index < -1 or parent_index >= bone_count or parent_index == bone_index:
            raise ModelDagDescriptorError(f"{joint}: invalid parent index {parent_index}")
        rest = _vector3(joint, ATTR_MMD_PMX_REST_POSITION)
        pmx_flags = int(_required(joint, ATTR_MMD_BONE_FLAGS))
        model_flags = 0
        if pmx_flags & int(PmxBoneFlag.DEFORM_AFTER_PHYSICS):
            model_flags |= MMD_RUNTIME_MODEL_BONE_TRANSFORM_AFTER_PHYSICS
        fixed_axis = (0.0, 0.0, 0.0)
        if pmx_flags & int(PmxBoneFlag.AXIS_FIXED):
            fixed_axis = _vector3(joint, ATTR_MMD_FIXED_AXIS)
            model_flags |= MMD_RUNTIME_MODEL_BONE_FIXED_AXIS
        local_x = (0.0, 0.0, 0.0)
        local_z = (0.0, 0.0, 0.0)
        if pmx_flags & int(PmxBoneFlag.LOCAL_AXIS):
            local_x = _vector3(joint, ATTR_MMD_LOCAL_X_AXIS)
            local_z = _vector3(joint, ATTR_MMD_LOCAL_Z_AXIS)
            model_flags |= MMD_RUNTIME_MODEL_BONE_LOCAL_AXIS
        bones.append(
            MmdRuntimeFfiModelBoneV2(
                parent_index=parent_index,
                rest_position_xyz=rest,
                transform_order=int(_required(joint, ATTR_MMD_DEFORM_LAYER)),
                flags=model_flags,
                fixed_axis_xyz=fixed_axis,
                local_axis_x_xyz=local_x,
                local_axis_z_xyz=local_z,
            )
        )

        if pmx_flags & int(PmxBoneFlag.IK):
            entries = _json_list(joint, ATTR_MMD_IK_LINKS)
            link_offset = len(ik_links)
            for entry in entries:
                if not isinstance(entry, dict) or "bone" not in entry:
                    raise ModelDagDescriptorError(f"{joint}.{ATTR_MMD_IK_LINKS}: invalid link")
                link_index = int(entry["bone"])
                if link_index < 0 or link_index >= bone_count:
                    raise ModelDagDescriptorError(f"{joint}: invalid IK link index {link_index}")
                limited = bool(entry.get("limit_enabled", False))
                lower = tuple(float(v) for v in entry.get("lower_limit", (0.0, 0.0, 0.0)))
                upper = tuple(float(v) for v in entry.get("upper_limit", (0.0, 0.0, 0.0)))
                if len(lower) != 3 or len(upper) != 3 or not all(math.isfinite(v) for v in lower + upper):
                    raise ModelDagDescriptorError(f"{joint}: invalid IK angle limit")
                ik_links.append(
                    MmdRuntimeFfiModelIkLink(
                        bone_index=link_index,
                        flags=MMD_RUNTIME_MODEL_IK_LINK_ANGLE_LIMIT if limited else 0,
                        angle_limit_min_xyz=lower,
                        angle_limit_max_xyz=upper,
                    )
                )
            target = int(_required(joint, ATTR_MMD_IK_TARGET_INDEX))
            if target < 0 or target >= bone_count:
                raise ModelDagDescriptorError(f"{joint}: invalid IK target index {target}")
            ik_solvers.append(
                MmdRuntimeFfiModelIkSolver(
                    ik_bone_index=bone_index,
                    target_bone_index=target,
                    link_offset=link_offset,
                    link_count=len(entries),
                    iteration_count=int(_required(joint, ATTR_MMD_IK_LOOP)),
                    limit_angle=_finite_float(joint, ATTR_MMD_IK_LIMIT_ANGLE),
                )
            )

        grant_mask = int(PmxBoneFlag.GRANT_PARENT_ROTATE | PmxBoneFlag.GRANT_PARENT_MOVE)
        if pmx_flags & grant_mask:
            source_index = int(_required(joint, ATTR_MMD_GRANT_PARENT_INDEX))
            if source_index < 0 or source_index >= bone_count:
                raise ModelDagDescriptorError(f"{joint}: invalid append source index {source_index}")
            append_flags = 0
            if pmx_flags & int(PmxBoneFlag.GRANT_PARENT_ROTATE):
                append_flags |= MMD_RUNTIME_MODEL_APPEND_ROTATION
            if pmx_flags & int(PmxBoneFlag.GRANT_PARENT_MOVE):
                append_flags |= MMD_RUNTIME_MODEL_APPEND_TRANSLATION
            if pmx_flags & int(PmxBoneFlag.LOCAL):
                append_flags |= MMD_RUNTIME_MODEL_APPEND_LOCAL
            append_transforms.append(
                MmdRuntimeFfiModelAppendTransform(
                    target_bone_index=bone_index,
                    source_bone_index=source_index,
                    ratio=_finite_float(joint, ATTR_MMD_GRANT_RATE),
                    flags=append_flags,
                )
            )

    bone_morph_offsets: list[MmdRuntimeFfiModelBoneMorphOffset] = []
    group_morph_offsets: list[MmdRuntimeFfiModelGroupMorphOffset] = []
    morph_count = 0
    metadata_items = sorted(
        iter_morph_network_metadata(root_group=root_group),
        key=lambda metadata: (-1 if metadata.index is None else metadata.index, metadata.node),
    )
    for metadata in metadata_items:
        if metadata.index is None or metadata.index < 0:
            raise ModelDagDescriptorError(f"{metadata.node}: missing valid morph index")
        morph_count = max(morph_count, metadata.index + 1)
        if metadata.morph_type == "bone":
            for entry in _json_list(metadata.node, ATTR_MMD_BONE_MORPH_OFFSETS_RAW_JSON):
                if not isinstance(entry, dict) or "bone_index" not in entry:
                    raise ModelDagDescriptorError(f"{metadata.node}: invalid bone morph offset")
                target = int(entry["bone_index"])
                position = tuple(float(v) for v in entry.get("translation", ()))
                rotation = tuple(float(v) for v in entry.get("rotation", ()))
                if target < 0 or target >= bone_count or len(position) != 3 or len(rotation) != 4:
                    raise ModelDagDescriptorError(f"{metadata.node}: invalid bone morph offset")
                if not all(math.isfinite(v) for v in position + rotation):
                    raise ModelDagDescriptorError(f"{metadata.node}: non-finite bone morph offset")
                bone_morph_offsets.append(
                    MmdRuntimeFfiModelBoneMorphOffset(
                        morph_index=metadata.index,
                        target_bone_index=target,
                        position_offset_xyz=position,
                        rotation_offset_xyzw=rotation,
                    )
                )
        elif metadata.morph_type == "group":
            for entry in _json_list(metadata.node, "mmd_group_morph_offsets_json"):
                if not isinstance(entry, dict) or "morph_index" not in entry:
                    raise ModelDagDescriptorError(f"{metadata.node}: invalid group morph offset")
                child = int(entry["morph_index"])
                ratio = float(entry.get("morph_rate", 0.0))
                if child < 0 or not math.isfinite(ratio):
                    raise ModelDagDescriptorError(f"{metadata.node}: invalid group morph offset")
                group_morph_offsets.append(
                    MmdRuntimeFfiModelGroupMorphOffset(
                        morph_index=metadata.index,
                        child_morph_index=child,
                        ratio=ratio,
                    )
                )

    for offset in group_morph_offsets:
        if offset.child_morph_index >= morph_count:
            raise ModelDagDescriptorError(
                f"group morph child index {offset.child_morph_index} exceeds morph count {morph_count}"
            )

    return ModelDagDescriptorSet(
        bones=bones,
        ik_solvers=ik_solvers,
        ik_links=ik_links,
        append_transforms=append_transforms,
        morph_count=morph_count,
        bone_morph_offsets=bone_morph_offsets,
        group_morph_offsets=group_morph_offsets,
    )
