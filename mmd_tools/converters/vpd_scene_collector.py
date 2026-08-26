"""Collect one current Maya character pose for VPD export.

VPD is a single-pose format.  The current evaluated MMD joint transforms are
the only value source; imported VPD/VMD payloads are intentionally not read.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Optional

from maya import cmds

from mmd_tools.core.constants import (
    ATTR_MMD_BONE_INDEX,
    ATTR_MMD_BONE_NAME,
)
from mmd_tools.core.logger import get_logger
from mmd_tools.core.mmd_control_rig_builder import (
    CONTROL_RIG_CONTROL_OWNED,
    read_mmd_control_rig_metadata,
)
from mmd_tools.core.mmd_control_rig_motion import (
    resolve_control_rig_direct_vmd_export_routes,
)
from mmd_tools.core.vpd_data import VpdData
from mmd_tools.core.vpd_data.bone_pose import BonePose
from mmd_tools.converters.vmd_import_state import get_stored_bind_translate
from mmd_tools.converters.vmd_scene_collector import (
    _build_rotation_export_context,
    _maya_joint_rotate_to_vmd_quaternion,
    _maya_translate_to_vmd_position,
)


logger = get_logger(__name__)


class VpdSceneCollectionError(ValueError):
    """Raised when a current-pose export cannot establish a safe scene payload."""


def _canonical_node(node: str) -> str:
    """Resolve one Maya node to a stable long DAG path."""
    matches = cmds.ls(node, long=True) or []
    if len(matches) != 1:
        raise VpdSceneCollectionError(f"VPD target node is ambiguous or missing: {node}")
    return str(matches[0])


def _finite_values(values, *, field: str) -> list[float]:
    """Convert a Maya numeric value to a finite list."""
    if isinstance(values, (list, tuple)) and len(values) == 1 and isinstance(values[0], (list, tuple)):
        values = values[0]
    if not isinstance(values, (list, tuple)):
        raise VpdSceneCollectionError(f"VPD {field} is not a numeric vector")
    try:
        result = [float(value) for value in values]
    except (TypeError, ValueError, OverflowError) as exc:
        raise VpdSceneCollectionError(f"VPD {field} is not a numeric vector") from exc
    if not all(math.isfinite(value) for value in result):
        raise VpdSceneCollectionError(f"VPD {field} contains a non-finite value")
    return result


class VpdSceneCollector:
    """Collect all indexed/named MMD joints below one current model root."""

    def can_collect(self, options: Optional[Mapping[str, Any]] = None) -> bool:
        """Validate the Current Model and its motion-owner route without writes."""

        options = dict(options or {})
        target = options.get("current_model_root") or options.get("target_model")
        if not target:
            return False
        root = _canonical_node(str(target))
        self._validate_control_rig_authority(root)
        return True

    def collect(self, options: Optional[Mapping[str, Any]] = None) -> VpdData:
        """Return a VPD payload from the current evaluated character scene."""
        options = dict(options or {})
        target = options.get("current_model_root") or options.get("target_model") or options.get("model_root")
        if not target:
            raise VpdSceneCollectionError("VPD export requires a Current Model")
        root = _canonical_node(str(target))
        target_type = str(cmds.nodeType(root) or "")
        if target_type not in {"transform", "joint"}:
            raise VpdSceneCollectionError(f"VPD target must be a model transform: {root}")

        self._validate_control_rig_authority(root)
        joints = []
        if target_type == "joint":
            joints.append(root)
        joints.extend(cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or [])
        joints = [_canonical_node(str(joint)) for joint in joints]

        candidates = []
        seen_indices = {}
        seen_names = {}
        for joint in joints:
            has_index = bool(cmds.attributeQuery(ATTR_MMD_BONE_INDEX, node=joint, exists=True))
            has_name = bool(cmds.attributeQuery(ATTR_MMD_BONE_NAME, node=joint, exists=True))
            if not has_index and not has_name:
                continue
            if not has_index or not has_name:
                raise VpdSceneCollectionError(
                    f"MMD bone identity is incomplete for VPD export: {joint}"
                )
            try:
                index = cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_INDEX}")
                name = str(cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_NAME}") or "")
            except Exception as exc:
                raise VpdSceneCollectionError(
                    f"MMD bone identity could not be read: {joint}"
                ) from exc
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                raise VpdSceneCollectionError(f"MMD bone index is invalid for VPD export: {joint}")
            if not name:
                raise VpdSceneCollectionError(f"MMD bone name is empty for VPD export: {joint}")
            try:
                name.encode("shift_jis")
            except UnicodeEncodeError as exc:
                raise VpdSceneCollectionError(
                    f"MMD bone name cannot be represented in Shift-JIS: {name!r}"
                ) from exc
            prior_index = seen_indices.get(index)
            if prior_index is not None and prior_index != joint:
                raise VpdSceneCollectionError(
                    f"duplicate MMD bone index {index} in VPD export: {prior_index}, {joint}"
                )
            prior_name = seen_names.get(name)
            if prior_name is not None and prior_name != joint:
                raise VpdSceneCollectionError(
                    f"duplicate MMD bone name {name!r} in VPD export: {prior_name}, {joint}"
                )
            seen_indices[index] = joint
            seen_names[name] = joint
            candidates.append((index, name, joint))

        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        if not candidates:
            raise VpdSceneCollectionError(
                "VPD export requires at least one indexed, named MMD joint"
            )
        try:
            rotation_context = _build_rotation_export_context(
                [joint for _index, _name, joint in candidates]
            )
        except Exception as exc:
            raise VpdSceneCollectionError(
                f"MMD joint rotation context could not be built for VPD export: {exc}"
            ) from exc
        payload = VpdData()
        for source_index, name, joint in candidates:
            pose = BonePose()
            pose.bone_index = source_index
            pose.bone_name = name
            translate = _finite_values(
                [cmds.getAttr(f"{joint}.translate{axis}") for axis in "XYZ"],
                field=f"{joint}.translate",
            )
            bind_translate = get_stored_bind_translate(joint) or (0.0, 0.0, 0.0)
            pose.position = list(
                _maya_translate_to_vmd_position(
                    translate,
                    bind_translate,
                    motion_scale=1.0,
                )
            )
            rotate = _finite_values(
                [cmds.getAttr(f"{joint}.rotate{axis}") for axis in "XYZ"],
                field=f"{joint}.rotate",
            )
            if len(rotate) != 3:
                raise VpdSceneCollectionError(f"MMD joint rotation is not XYZ: {joint}")
            try:
                pose.quaternion = list(
                    _maya_joint_rotate_to_vmd_quaternion(
                        joint,
                        rotate[0],
                        rotate[1],
                        rotate[2],
                        rotation_context.get(joint),
                    )
                )
            except Exception as exc:
                raise VpdSceneCollectionError(
                    f"MMD joint rotation could not be converted for VPD export: {joint}"
                ) from exc
            payload.bone_poses.append(pose)
        payload.header.bone_count = len(payload.bone_poses)
        return payload

    @staticmethod
    def _validate_control_rig_authority(root: str) -> None:
        """Validate Control-owned routes before reading evaluated MMD joints."""
        metadata = read_mmd_control_rig_metadata(root)
        if not isinstance(metadata, Mapping):
            return
        owner = str(metadata.get("owner") or "").upper()
        if owner == CONTROL_RIG_CONTROL_OWNED:
            try:
                resolve_control_rig_direct_vmd_export_routes(root)
            except Exception as exc:
                raise VpdSceneCollectionError(
                    f"Control Rig VPD export route is unresolved: {exc}"
                ) from exc
        elif owner and owner not in {"MMD_OWNED"}:
            raise VpdSceneCollectionError(
                f"VPD export requires a resolved motion owner, got {owner!r}"
            )


__all__ = ["VpdSceneCollectionError", "VpdSceneCollector"]
