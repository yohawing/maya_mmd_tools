"""Injected Maya scene operations for PMX bone registration and reindexing.

The adapter owns only structural/metadata operations.  It never opens an undo
chunk; callers coordinate undo and transaction boundaries.  All validation is
performed before the first write in a reindex or unregister operation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
import json
import math
from typing import Any

from mmd_tools.core.constants import (
    ATTR_MMD_AXIS_DIRECTION,
    ATTR_MMD_BONE_FLAGS,
    ATTR_MMD_BONE_INDEX,
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_BONE_NAME_EN,
    ATTR_MMD_BONE_OFFSET,
    ATTR_MMD_BONE_PARENT_INDEX,
    ATTR_MMD_CONNECT_BONE_INDEX,
    ATTR_MMD_CONNECTION_BONE,
    ATTR_MMD_CONNECT_INDEX,
    ATTR_MMD_DEFORM_LAYER,
    ATTR_MMD_EXTERNAL_PARENT_KEY,
    ATTR_MMD_FIXED_AXIS,
    ATTR_MMD_GRANT_PARENT,
    ATTR_MMD_GRANT_PARENT_INDEX,
    ATTR_MMD_GRANT_RATE,
    ATTR_MMD_IK_LINKS,
    ATTR_MMD_IK_LIMIT_ANGLE,
    ATTR_MMD_IK_LOOP,
    ATTR_MMD_IK_TARGET,
    ATTR_MMD_IK_TARGET_INDEX,
    ATTR_MMD_LOCAL_X_AXIS,
    ATTR_MMD_LOCAL_Z_AXIS,
    ATTR_MMD_DISPLAY_FRAMES_JSON,
    ATTR_MMD_MODEL_ROOT,
    ATTR_MMD_MODEL_REGISTRY,
    ATTR_MMD_REGISTRY_MORPH_MEMBERS,
    ATTR_MMD_REGISTRY_ROOT,
    ATTR_MMD_REGISTRY_SCHEMA,
    ATTR_MMD_BONE_MORPH_OFFSETS_RAW_JSON,
    ATTR_MMD_CONTROL_RIG_JSON,
    ATTR_MMD_PMX_REST_POSITION,
    ATTR_MMD_X_AXIS_DIRECTION,
    ATTR_MMD_Z_AXIS_DIRECTION,
)
from mmd_tools.core.bone_authoring import BoneResetPlan, make_bone_reset_plan
from mmd_tools.core.model_authoring_spec import MmdBoneSpec, MmdModelAuthoringSpec
from mmd_tools.core.pmx_data.bone import PmxBoneFlag


class MayaBoneAuthoringError(ValueError):
    """Raised when a bone operation cannot be applied without semantic loss."""


_BONE_REFERENCE_FIELDS = (
    ("parent_index", (ATTR_MMD_BONE_PARENT_INDEX,)),
    ("connect_bone_index", (ATTR_MMD_CONNECT_INDEX, ATTR_MMD_CONNECT_BONE_INDEX)),
    ("grant_parent_index", (ATTR_MMD_GRANT_PARENT_INDEX,)),
    ("ik_target_index", (ATTR_MMD_IK_TARGET_INDEX,)),
)
_BONE_ATTRS = (
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_BONE_NAME_EN,
    ATTR_MMD_BONE_INDEX,
    ATTR_MMD_BONE_PARENT_INDEX,
    ATTR_MMD_PMX_REST_POSITION,
    ATTR_MMD_DEFORM_LAYER,
    ATTR_MMD_BONE_FLAGS,
    ATTR_MMD_CONNECT_INDEX,
    ATTR_MMD_CONNECT_BONE_INDEX,
    ATTR_MMD_CONNECTION_BONE,
    ATTR_MMD_BONE_OFFSET,
    ATTR_MMD_GRANT_PARENT_INDEX,
    ATTR_MMD_GRANT_PARENT,
    ATTR_MMD_GRANT_RATE,
    ATTR_MMD_FIXED_AXIS,
    ATTR_MMD_AXIS_DIRECTION,
    ATTR_MMD_LOCAL_X_AXIS,
    ATTR_MMD_X_AXIS_DIRECTION,
    ATTR_MMD_LOCAL_Z_AXIS,
    ATTR_MMD_Z_AXIS_DIRECTION,
    ATTR_MMD_EXTERNAL_PARENT_KEY,
    ATTR_MMD_IK_TARGET_INDEX,
    ATTR_MMD_IK_TARGET,
    ATTR_MMD_IK_LOOP,
    ATTR_MMD_IK_LIMIT_ANGLE,
    ATTR_MMD_IK_LINKS,
)


def _fail(message: str) -> None:
    raise MayaBoneAuthoringError(message)


def _require_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{field} must be a non-empty string")
    return value


def _require_number(value: Any, *, field: str, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{field} must be a finite number")
    converted = float(value)
    if not math.isfinite(converted) or (positive and converted <= 0):
        _fail(f"{field} must be finite and {'> 0' if positive else 'valid'}")
    return converted


def _require_index(value: Any, *, field: str, minimum: int = -1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail(f"{field} must be an integer >= {minimum}")
    return value


def _require_vector(value: Any, *, field: str, size: int = 3) -> tuple[float, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence) or len(value) != size:
        _fail(f"{field} must contain exactly {size} numbers")
    result = []
    for index, component in enumerate(value):
        result.append(_require_number(component, field=f"{field}[{index}]"))
    return tuple(result)


def _call(adapter: Any, method: str, *args: Any, **kwargs: Any) -> Any:
    try:
        return getattr(adapter, method)(*args, **kwargs)
    except AttributeError as exc:
        raise MayaBoneAuthoringError(f"injected adapter is missing {method}()") from exc
    except Exception as exc:
        raise MayaBoneAuthoringError(f"adapter {method}() failed: {exc}") from exc


def _exists(adapter: Any, node: str, attr: str) -> bool:
    return bool(_call(adapter, "attribute_exists", attr, node))


def _get(adapter: Any, node: str, attr: str) -> Any:
    return _call(adapter, "get_attr", f"{node}.{attr}")


def _canonical_identity(adapter: Any, node: Any) -> str:
    """Resolve one Maya DAG/DG alias to its unique long identity."""
    if not isinstance(node, str) or not node:
        _fail("registry root connection must contain a non-empty node identity")
    paths = _call(adapter, "ls", node, long=True) or []
    if isinstance(paths, (str, bytes, bytearray)) or len(paths) != 1:
        _fail(f"registry root connection is not uniquely resolvable: {node!r}")
    identity = paths[0]
    if not isinstance(identity, str) or not identity:
        _fail(f"registry root connection has invalid identity: {node!r}")
    return identity


def _descendant_joints(adapter: Any, root: str) -> list[str]:
    joints = _call(adapter, "list_relatives", root, allDescendents=True, fullPath=True, type="joint") or []
    if isinstance(joints, (str, bytes, bytearray)):
        _fail("joint descendants must be a sequence")
    return [str(joint) for joint in joints]


def _direct_joint_children(adapter: Any, joint: str, descendants: set[str]) -> tuple[str, ...]:
    """Return canonical direct joint children discovered below ``root``."""
    children = _call(
        adapter,
        "list_relatives",
        joint,
        children=True,
        fullPath=True,
        type="joint",
    ) or []
    if isinstance(children, (str, bytes, bytearray)):
        _fail(f"direct joint children for {joint!r} must be a sequence")
    result: list[str] = []
    for child in children:
        canonical = _canonical_identity(adapter, child)
        if canonical in descendants and canonical not in result:
            result.append(canonical)
    return tuple(result)


def _require_root_joint(adapter: Any, root: str, joint: str) -> None:
    _require_string(root, field="root")
    _require_string(joint, field="joint")
    if not _call(adapter, "object_exists", root) or not _call(adapter, "object_exists", joint):
        _fail("root and joint must exist")
    if joint not in _descendant_joints(adapter, root):
        _fail(f"joint {joint!r} is not a descendant of root {root!r}")


def _set_attr(adapter: Any, node: str, attr: str, kind: str, value: Any) -> None:
    if not _exists(adapter, node, attr):
        if kind == "string":
            _call(adapter, "add_attr", node, longName=attr, dataType="string")
        elif kind == "vector":
            _call(adapter, "add_attr", node, longName=attr, attributeType="double3")
        elif kind == "double":
            _call(adapter, "add_attr", node, longName=attr, attributeType="double")
        else:
            _call(adapter, "add_attr", node, longName=attr, attributeType="long")
    if kind == "vector":
        for suffix in ("X", "Y", "Z"):
            child = f"{attr}{suffix}"
            if not _exists(adapter, node, child):
                _call(adapter, "add_attr", node, longName=child, attributeType="double", parent=attr)
    if kind == "string":
        _call(adapter, "set_attr", f"{node}.{attr}", str(value), type="string")
    elif kind == "vector":
        _call(adapter, "set_attr", f"{node}.{attr}", *value, type="double3")
    else:
        _call(adapter, "set_attr", f"{node}.{attr}", value)


def _read_int(adapter: Any, node: str, attr: str, *, minimum: int = -1) -> int:
    return _require_index(_get(adapter, node, attr), field=f"{node}.{attr}", minimum=minimum)


def _bone_names_by_index(adapter: Any, root: str, own: MmdBoneSpec) -> dict[int, str]:
    """Build the strict index-to-name table used by reference aliases."""
    names: dict[int, str] = {own.index: own.name}
    for node in _descendant_joints(adapter, root):
        if not _exists(adapter, node, ATTR_MMD_BONE_INDEX):
            continue
        index = _read_int(adapter, node, ATTR_MMD_BONE_INDEX, minimum=0)
        if not _exists(adapter, node, ATTR_MMD_BONE_NAME):
            _fail(f"registered joint {node!r} is missing {ATTR_MMD_BONE_NAME}")
        name = _get(adapter, node, ATTR_MMD_BONE_NAME)
        _require_string(name, field=f"{node}.{ATTR_MMD_BONE_NAME}")
        if index in names and names[index] != name:
            _fail(f"bone index {index} has conflicting names")
        names[index] = name
    return names


def _read_json(adapter: Any, node: str, attr: str, *, field: str) -> Any:
    raw = _get(adapter, node, attr)
    if not isinstance(raw, str):
        _fail(f"{field} must contain JSON text")
    try:
        return json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        _fail(f"{field} contains malformed JSON: {exc}")


def _mapping_list(value: Any, *, field: str) -> list[dict[str, Any]]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        _fail(f"{field} must be a list")
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            _fail(f"{field}[{index}] must be a mapping")
        result.append(dict(item))
    return result


def _validate_reindex_specs(old_spec: MmdModelAuthoringSpec, new_spec: MmdModelAuthoringSpec) -> dict[int, int]:
    if not isinstance(old_spec, MmdModelAuthoringSpec) or not isinstance(new_spec, MmdModelAuthoringSpec):
        _fail("old_spec and new_spec must be MmdModelAuthoringSpec values")
    old_by_binding = {bone.binding_identity: bone for bone in old_spec.bones}
    new_by_binding = {bone.binding_identity: bone for bone in new_spec.bones}
    if None in old_by_binding or None in new_by_binding or set(old_by_binding) != set(new_by_binding):
        _fail("bone binding identities must match exactly")
    mapping = {old.index: new_by_binding[binding].index for binding, old in old_by_binding.items()}
    if len(mapping) != len(old_spec.bones) or len(set(mapping.values())) != len(mapping):
        _fail("bone reindex mapping must be one-to-one")
    old_indices = set(mapping)

    def remap(value: int | None, *, field: str) -> int | None:
        if value is None:
            return None
        if value == -1:
            return -1
        if value not in old_indices:
            _fail(f"{field} references unknown old bone index {value}")
        return mapping[value]

    for binding, old_bone in old_by_binding.items():
        new_bone = new_by_binding[binding]
        for field in ("parent_index", "connect_bone_index", "grant_parent_index", "ik_target_index"):
            if remap(getattr(old_bone, field), field=f"old bone {old_bone.index}.{field}") != getattr(new_bone, field):
                _fail(f"new spec changes {field}; apply_bone_reindex only remaps indices")
        if len(old_bone.ik_links) != len(new_bone.ik_links):
            _fail(f"bone {old_bone.index} changes IK link structure")
        for old_link, new_link in zip(old_bone.ik_links, new_bone.ik_links):
            if set(old_link) != set(new_link):
                _fail(f"bone {old_bone.index} changes IK link fields")
            for key, value in old_link.items():
                if key == "bone":
                    value = _require_index(value, field=f"bone {old_bone.index}.ik_links.bone", minimum=0)
                expected = remap(value, field=f"bone {old_bone.index}.ik_links.{key}") if key == "bone" else value
                if new_link.get(key) != expected:
                    _fail(f"bone {old_bone.index} changes IK link payload")
    for morph in old_spec.morphs:
        new_morph = next((item for item in new_spec.morphs if item.binding_identity == morph.binding_identity), None)
        if new_morph is None or morph.morph_type != new_morph.morph_type:
            continue
        if morph.morph_type != "bone":
            continue
        if len(morph.offsets) != len(new_morph.offsets):
            _fail(f"morph {morph.index} changes offset structure")
        for old_offset, new_offset in zip(morph.offsets, new_morph.offsets):
            expected = dict(old_offset)
            old_bone_index = _require_index(old_offset.get("bone_index"), field=f"morph {morph.index}.bone_index", minimum=0)
            expected["bone_index"] = remap(old_bone_index, field=f"morph {morph.index}.bone_index")
            if dict(new_offset) != expected:
                _fail(f"morph {morph.index} changes offset payload")
    return mapping


def capture_rest_position(root: str, joint: str, model_scale: float, adapter: Any) -> tuple[float, float, float]:
    """Capture world translation as PMX absolute rest coordinates."""
    _require_root_joint(adapter, root, joint)
    scale = _require_number(model_scale, field="model_scale", positive=True)
    if hasattr(adapter, "xform"):
        raw = _call(adapter, "xform", joint, query=True, worldSpace=True, translation=True)
    else:
        raw = _get(adapter, joint, "worldMatrix[0]")
        if raw is None:
            raw = _get(adapter, joint, "worldMatrix")
        if isinstance(raw, Sequence) and len(raw) == 1 and isinstance(raw[0], Sequence):
            raw = raw[0]
        if isinstance(raw, Sequence) and len(raw) >= 15:
            raw = (raw[12], raw[13], raw[14])
    x, y, z = _require_vector(raw, field="world translation")
    return (x / scale, y / scale, -z / scale)


def register_existing_joint(root: str, bone: MmdBoneSpec, adapter: Any) -> None:
    """Register one existing descendant joint using only canonical Spec data."""
    if not isinstance(bone, MmdBoneSpec):
        _fail("bone must be an MmdBoneSpec")
    joint = _require_string(bone.binding_identity, field="bone.binding_identity")
    _require_root_joint(adapter, root, joint)
    if _exists(adapter, joint, ATTR_MMD_BONE_INDEX):
        _fail(f"joint {joint!r} is already registered")
    descendants = _descendant_joints(adapter, root)
    existing_indices = {
        _read_int(adapter, item, ATTR_MMD_BONE_INDEX)
        for item in descendants
        if _exists(adapter, item, ATTR_MMD_BONE_INDEX)
    }
    if bone.index in existing_indices:
        _fail(f"bone index {bone.index} is already registered")
    known_indices = existing_indices | {bone.index}
    for field in ("parent_index", "connect_bone_index", "grant_parent_index", "ik_target_index"):
        value = getattr(bone, field)
        if value is not None and value != -1 and value not in known_indices:
            _fail(f"bone.{field} references unknown index {value}")

    flags = int(bone.flags)
    grant_flags = PmxBoneFlag.GRANT_PARENT_ROTATE | PmxBoneFlag.GRANT_PARENT_MOVE
    if flags & PmxBoneFlag.CONNECT_BONE:
        if bone.connect_bone_index is None or bone.connect_bone_index < 0:
            _fail("CONNECT_BONE requires a non-negative connect_bone_index")
    elif bone.connect_bone_index is not None:
        _fail("connect_bone_index requires CONNECT_BONE flag")
    if flags & grant_flags:
        if bone.grant_parent_index is None or bone.grant_parent_index < 0:
            _fail("grant flags require a non-negative grant_parent_index")
    elif bone.grant_parent_index is not None:
        _fail("grant_parent_index requires a grant flag")
    if bone.fixed_axis is not None and not flags & PmxBoneFlag.AXIS_FIXED:
        _fail("fixed_axis requires AXIS_FIXED flag")
    if (bone.local_axis_x is not None or bone.local_axis_z is not None) and not flags & PmxBoneFlag.LOCAL_AXIS:
        _fail("local axes require LOCAL_AXIS flag")
    if bone.external_parent_key is not None and not flags & PmxBoneFlag.EXTERNAL_PARENT_DEFORM:
        _fail("external_parent_key requires EXTERNAL_PARENT_DEFORM flag")
    if flags & PmxBoneFlag.IK:
        if bone.ik_target_index is None or bone.ik_target_index < 0:
            _fail("IK requires a non-negative ik_target_index")
    elif bone.ik_target_index is not None:
        _fail("ik_target_index requires IK flag")

    names_by_index = _bone_names_by_index(adapter, root, bone)

    def reference_name(index: int, field: str) -> str:
        try:
            return names_by_index[index]
        except KeyError:
            _fail(f"bone.{field} references unknown index {index}")

    _set_attr(adapter, joint, ATTR_MMD_BONE_NAME, "string", bone.name)
    _set_attr(adapter, joint, ATTR_MMD_BONE_NAME_EN, "string", bone.name_english)
    _set_attr(adapter, joint, ATTR_MMD_BONE_INDEX, "long", bone.index)
    _set_attr(adapter, joint, ATTR_MMD_BONE_PARENT_INDEX, "long", bone.parent_index)
    _set_attr(adapter, joint, ATTR_MMD_PMX_REST_POSITION, "vector", bone.rest_position)
    _set_attr(adapter, joint, ATTR_MMD_DEFORM_LAYER, "long", bone.transform_layer)
    _set_attr(adapter, joint, ATTR_MMD_BONE_FLAGS, "long", bone.flags)
    tail = bone.tail_offset or ((0.0, -1.0, 0.0) if flags & PmxBoneFlag.CONNECT_BONE else (0.0, 0.0, 0.0))
    _set_attr(adapter, joint, ATTR_MMD_BONE_OFFSET, "vector", tail)
    if flags & PmxBoneFlag.CONNECT_BONE:
        _set_attr(adapter, joint, ATTR_MMD_CONNECT_INDEX, "long", bone.connect_bone_index)
        _set_attr(adapter, joint, ATTR_MMD_CONNECT_BONE_INDEX, "long", bone.connect_bone_index)
        _set_attr(
            adapter,
            joint,
            ATTR_MMD_CONNECTION_BONE,
            "string",
            reference_name(bone.connect_bone_index, "connect_bone_index"),
        )
    if flags & grant_flags:
        _set_attr(adapter, joint, ATTR_MMD_GRANT_PARENT_INDEX, "long", bone.grant_parent_index)
        _set_attr(
            adapter,
            joint,
            ATTR_MMD_GRANT_PARENT,
            "string",
            reference_name(bone.grant_parent_index, "grant_parent_index"),
        )
        _set_attr(adapter, joint, ATTR_MMD_GRANT_RATE, "double", bone.grant_ratio)
    if flags & PmxBoneFlag.AXIS_FIXED:
        _set_attr(adapter, joint, ATTR_MMD_FIXED_AXIS, "vector", bone.fixed_axis or (0.0, 0.0, 1.0))
        _set_attr(adapter, joint, ATTR_MMD_AXIS_DIRECTION, "vector", bone.fixed_axis or (0.0, 0.0, 1.0))
    if flags & PmxBoneFlag.LOCAL_AXIS:
        _set_attr(adapter, joint, ATTR_MMD_LOCAL_X_AXIS, "vector", bone.local_axis_x or (1.0, 0.0, 0.0))
        _set_attr(adapter, joint, ATTR_MMD_X_AXIS_DIRECTION, "vector", bone.local_axis_x or (1.0, 0.0, 0.0))
        _set_attr(adapter, joint, ATTR_MMD_LOCAL_Z_AXIS, "vector", bone.local_axis_z or (0.0, 0.0, 1.0))
        _set_attr(adapter, joint, ATTR_MMD_Z_AXIS_DIRECTION, "vector", bone.local_axis_z or (0.0, 0.0, 1.0))
    if flags & PmxBoneFlag.EXTERNAL_PARENT_DEFORM:
        _set_attr(adapter, joint, ATTR_MMD_EXTERNAL_PARENT_KEY, "long", bone.external_parent_key)
    if flags & PmxBoneFlag.IK:
        _set_attr(adapter, joint, ATTR_MMD_IK_TARGET_INDEX, "long", bone.ik_target_index)
        _set_attr(
            adapter,
            joint,
            ATTR_MMD_IK_TARGET,
            "string",
            reference_name(bone.ik_target_index, "ik_target_index"),
        )
        _set_attr(adapter, joint, ATTR_MMD_IK_LOOP, "long", bone.ik_loop_count)
        _set_attr(adapter, joint, ATTR_MMD_IK_LIMIT_ANGLE, "double", bone.ik_limit_radian or 0.0)
        _set_attr(adapter, joint, ATTR_MMD_IK_LINKS, "string", json.dumps([dict(link) for link in bone.ik_links], ensure_ascii=False))


def register_existing_joints(root: str, bones: Sequence[MmdBoneSpec], adapter: Any) -> None:
    """Register a complete set of descendant joints in two metadata phases.

    A PMX bone can reference a later index (IK targets and grant parents are
    common examples).  The historical single-joint helper intentionally
    rejects such forward references.  Template creation, however, creates a
    complete hierarchy before writing metadata, so all base fields are written
    first and reference aliases are resolved only after every index/name is
    known.  No partial registration is allowed when validation fails.
    """
    if not isinstance(bones, Sequence) or isinstance(bones, (str, bytes, bytearray)):
        _fail("bones must be a sequence")
    items = tuple(bones)
    if not items:
        _fail("bones must not be empty")
    if any(not isinstance(item, MmdBoneSpec) for item in items):
        _fail("bones entries must be MmdBoneSpec values")
    _require_string(root, field="root")
    if not _call(adapter, "object_exists", root):
        _fail("root must exist")

    descendants = _descendant_joints(adapter, root)
    bindings = [
        _require_string(item.binding_identity, field=f"bone[{item.index}].binding_identity")
        for item in items
    ]
    if len(set(bindings)) != len(bindings):
        _fail("bone binding identities must be unique")
    if any(binding not in descendants for binding in bindings):
        _fail("all bone bindings must be descendants of root")
    if any(_exists(adapter, binding, ATTR_MMD_BONE_INDEX) for binding in bindings):
        _fail("one or more bone joints are already registered")
    indices = [item.index for item in items]
    if len(set(indices)) != len(indices):
        _fail("bone indices must be unique")
    existing_indices = {
        _read_int(adapter, binding, ATTR_MMD_BONE_INDEX, minimum=0)
        for binding in descendants
        if _exists(adapter, binding, ATTR_MMD_BONE_INDEX)
    }
    known_indices = set(indices) | existing_indices
    for item in items:
        for field in ("parent_index", "connect_bone_index", "grant_parent_index", "ik_target_index"):
            value = getattr(item, field)
            if value is not None and value != -1 and value not in known_indices:
                _fail(f"bone.{field} references unknown index {value}")
        flags = int(item.flags)
        grant_flags = PmxBoneFlag.GRANT_PARENT_ROTATE | PmxBoneFlag.GRANT_PARENT_MOVE
        if flags & PmxBoneFlag.CONNECT_BONE:
            if item.connect_bone_index is None or item.connect_bone_index < 0:
                _fail("CONNECT_BONE requires a non-negative connect_bone_index")
        elif item.connect_bone_index is not None:
            _fail("connect_bone_index requires CONNECT_BONE flag")
        if flags & grant_flags:
            if item.grant_parent_index is None or item.grant_parent_index < 0:
                _fail("grant flags require a non-negative grant_parent_index")
        elif item.grant_parent_index is not None:
            _fail("grant_parent_index requires a grant flag")
        if item.fixed_axis is not None and not flags & PmxBoneFlag.AXIS_FIXED:
            _fail("fixed_axis requires AXIS_FIXED flag")
        if (item.local_axis_x is not None or item.local_axis_z is not None) and not flags & PmxBoneFlag.LOCAL_AXIS:
            _fail("local axes require LOCAL_AXIS flag")
        if item.external_parent_key is not None and not flags & PmxBoneFlag.EXTERNAL_PARENT_DEFORM:
            _fail("external_parent_key requires EXTERNAL_PARENT_DEFORM flag")
        if flags & PmxBoneFlag.IK:
            if item.ik_target_index is None or item.ik_target_index < 0:
                _fail("IK requires a non-negative ik_target_index")
        elif item.ik_target_index is not None:
            _fail("ik_target_index requires IK flag")
        for link_index, link in enumerate(item.ik_links):
            if not isinstance(link, Mapping):
                _fail(f"bone.ik_links[{link_index}] must be a mapping")
            link_bone = link.get("bone")
            if not isinstance(link_bone, int) or isinstance(link_bone, bool) or link_bone not in known_indices:
                _fail(f"bone.ik_links[{link_index}].bone references unknown index {link_bone!r}")

    names_by_index = {item.index: item.name for item in items}
    for binding in descendants:
        if not _exists(adapter, binding, ATTR_MMD_BONE_INDEX):
            continue
        index = _read_int(adapter, binding, ATTR_MMD_BONE_INDEX, minimum=0)
        if not _exists(adapter, binding, ATTR_MMD_BONE_NAME):
            _fail(f"registered joint {binding!r} is missing {ATTR_MMD_BONE_NAME}")
        name = _get(adapter, binding, ATTR_MMD_BONE_NAME)
        _require_string(name, field=f"{binding}.{ATTR_MMD_BONE_NAME}")
        if index in names_by_index and names_by_index[index] != name:
            _fail(f"bone index {index} has conflicting names")
        names_by_index[index] = name
    # Phase 1: all non-reference payloads.  This intentionally does not call
    # register_existing_joint(), whose one-at-a-time validation rejects forward
    # references by design.
    for item in items:
        joint = item.binding_identity
        assert joint is not None
        flags = int(item.flags)
        _set_attr(adapter, joint, ATTR_MMD_BONE_NAME, "string", item.name)
        _set_attr(adapter, joint, ATTR_MMD_BONE_NAME_EN, "string", item.name_english)
        _set_attr(adapter, joint, ATTR_MMD_BONE_INDEX, "long", item.index)
        _set_attr(adapter, joint, ATTR_MMD_BONE_PARENT_INDEX, "long", item.parent_index)
        _set_attr(adapter, joint, ATTR_MMD_PMX_REST_POSITION, "vector", item.rest_position)
        _set_attr(adapter, joint, ATTR_MMD_DEFORM_LAYER, "long", item.transform_layer)
        _set_attr(adapter, joint, ATTR_MMD_BONE_FLAGS, "long", item.flags)
        tail = item.tail_offset or ((0.0, -1.0, 0.0) if flags & PmxBoneFlag.CONNECT_BONE else (0.0, 0.0, 0.0))
        _set_attr(adapter, joint, ATTR_MMD_BONE_OFFSET, "vector", tail)
        if flags & (PmxBoneFlag.GRANT_PARENT_ROTATE | PmxBoneFlag.GRANT_PARENT_MOVE):
            _set_attr(adapter, joint, ATTR_MMD_GRANT_RATE, "double", item.grant_ratio)
        if flags & PmxBoneFlag.AXIS_FIXED:
            _set_attr(adapter, joint, ATTR_MMD_FIXED_AXIS, "vector", item.fixed_axis or (0.0, 0.0, 1.0))
            _set_attr(adapter, joint, ATTR_MMD_AXIS_DIRECTION, "vector", item.fixed_axis or (0.0, 0.0, 1.0))
        if flags & PmxBoneFlag.LOCAL_AXIS:
            _set_attr(adapter, joint, ATTR_MMD_LOCAL_X_AXIS, "vector", item.local_axis_x or (1.0, 0.0, 0.0))
            _set_attr(adapter, joint, ATTR_MMD_X_AXIS_DIRECTION, "vector", item.local_axis_x or (1.0, 0.0, 0.0))
            _set_attr(adapter, joint, ATTR_MMD_LOCAL_Z_AXIS, "vector", item.local_axis_z or (0.0, 0.0, 1.0))
            _set_attr(adapter, joint, ATTR_MMD_Z_AXIS_DIRECTION, "vector", item.local_axis_z or (0.0, 0.0, 1.0))
        if flags & PmxBoneFlag.EXTERNAL_PARENT_DEFORM:
            _set_attr(adapter, joint, ATTR_MMD_EXTERNAL_PARENT_KEY, "long", item.external_parent_key)
        if flags & PmxBoneFlag.IK:
            _set_attr(adapter, joint, ATTR_MMD_IK_LOOP, "long", item.ik_loop_count)
            _set_attr(adapter, joint, ATTR_MMD_IK_LIMIT_ANGLE, "double", item.ik_limit_radian or 0.0)
            _set_attr(adapter, joint, ATTR_MMD_IK_LINKS, "string", json.dumps([dict(link) for link in item.ik_links], ensure_ascii=False))

    # Phase 2: index/name aliases.  Every target is guaranteed to exist in the
    # complete set above, so references are deterministic even when forward.
    for item in items:
        joint = item.binding_identity
        assert joint is not None

        def reference_name(index: int, field: str) -> str:
            try:
                return names_by_index[index]
            except KeyError:
                _fail(f"bone.{field} references unknown index {index}")

        flags = int(item.flags)
        if flags & PmxBoneFlag.CONNECT_BONE:
            _set_attr(adapter, joint, ATTR_MMD_CONNECT_INDEX, "long", item.connect_bone_index)
            _set_attr(adapter, joint, ATTR_MMD_CONNECT_BONE_INDEX, "long", item.connect_bone_index)
            _set_attr(adapter, joint, ATTR_MMD_CONNECTION_BONE, "string", reference_name(item.connect_bone_index, "connect_bone_index"))
        if flags & (PmxBoneFlag.GRANT_PARENT_ROTATE | PmxBoneFlag.GRANT_PARENT_MOVE):
            _set_attr(adapter, joint, ATTR_MMD_GRANT_PARENT_INDEX, "long", item.grant_parent_index)
            _set_attr(adapter, joint, ATTR_MMD_GRANT_PARENT, "string", reference_name(item.grant_parent_index, "grant_parent_index"))
        if flags & PmxBoneFlag.IK:
            _set_attr(adapter, joint, ATTR_MMD_IK_TARGET_INDEX, "long", item.ik_target_index)
            _set_attr(adapter, joint, ATTR_MMD_IK_TARGET, "string", reference_name(item.ik_target_index, "ik_target_index"))


def _scene_display_payload(adapter: Any, root: str) -> tuple[str, list[dict[str, Any]]] | None:
    if not _exists(adapter, root, ATTR_MMD_DISPLAY_FRAMES_JSON):
        return None
    raw = _get(adapter, root, ATTR_MMD_DISPLAY_FRAMES_JSON)
    if not isinstance(raw, str):
        _fail("display frame metadata must be JSON text")
    try:
        frames = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        _fail(f"display frame metadata is malformed: {exc}")
    frame_items = _mapping_list(frames, field="display_frames")
    for frame_index, frame in enumerate(frame_items):
        elements = frame.get("elements")
        if isinstance(elements, (str, bytes, bytearray)) or not isinstance(elements, Sequence):
            _fail(f"display_frames[{frame_index}].elements must be a list")
        for element_index, element in enumerate(elements):
            if not isinstance(element, Mapping):
                _fail(f"display_frames[{frame_index}].elements[{element_index}] must be a mapping")
            kind = element.get("type")
            if kind in (0, "bone"):
                _require_index(
                    element.get("index"),
                    field=f"display_frames[{frame_index}].elements[{element_index}].index",
                    minimum=0,
                )
            elif kind in (1, "morph"):
                _require_index(
                    element.get("index"),
                    field=f"display_frames[{frame_index}].elements[{element_index}].index",
                    minimum=0,
                )
            else:
                _fail(f"display frame element type is unsupported: {kind!r}")
    return raw, frame_items


def _remap_display_frames(frames: list[dict[str, Any]], mapping: Mapping[int, int]) -> list[dict[str, Any]]:
    result = []
    for frame_index, frame in enumerate(frames):
        elements = frame.get("elements")
        if isinstance(elements, (str, bytes, bytearray)) or not isinstance(elements, Sequence):
            _fail(f"display_frames[{frame_index}].elements must be a list")
        updated = dict(frame)
        updated_elements = []
        for element_index, raw_element in enumerate(elements):
            if not isinstance(raw_element, Mapping):
                _fail(f"display_frames[{frame_index}].elements[{element_index}] must be a mapping")
            element = dict(raw_element)
            kind = element.get("type")
            if kind in (0, "bone"):
                old_index = _require_index(element.get("index"), field="display frame bone index", minimum=0)
                if old_index not in mapping:
                    _fail(f"display frame references unknown bone index {old_index}")
                element["index"] = mapping[old_index]
            elif kind in (1, "morph"):
                _require_index(element.get("index"), field="display frame morph index", minimum=0)
            else:
                _fail(f"display frame element type is unsupported: {kind!r}")
            updated_elements.append(element)
        updated["elements"] = updated_elements
        result.append(updated)
    return result


def _registry_morph_nodes(adapter: Any, root: str) -> list[str] | None:
    """Return registry-owned morphs, or ``None`` for legacy root links."""
    if not _exists(adapter, root, ATTR_MMD_MODEL_REGISTRY):
        return None
    registries = _call(
        adapter,
        "list_connections",
        f"{root}.{ATTR_MMD_MODEL_REGISTRY}",
        source=True,
        destination=False,
    ) or []
    if isinstance(registries, (str, bytes, bytearray)) or len(registries) != 1:
        _fail("model registry connection must resolve to exactly one node")
    registry = str(registries[0])
    if not _call(adapter, "object_exists", registry):
        _fail(f"model registry does not exist: {registry}")
    if not _exists(adapter, registry, ATTR_MMD_REGISTRY_SCHEMA):
        _fail("model registry schema attribute is missing")
    if str(_get(adapter, registry, ATTR_MMD_REGISTRY_SCHEMA) or "") != "1":
        _fail("unsupported model registry schema")
    if not _exists(adapter, registry, ATTR_MMD_REGISTRY_ROOT):
        _fail("model registry root attribute is missing")
    registry_roots = _call(
        adapter,
        "list_connections",
        f"{registry}.{ATTR_MMD_REGISTRY_ROOT}",
        source=True,
        destination=False,
    ) or []
    if isinstance(registry_roots, (str, bytes, bytearray)):
        _fail("model registry root connection must be a sequence")
    try:
        requested_root = _canonical_identity(adapter, root)
        linked_root = _canonical_identity(adapter, registry_roots[0]) if len(registry_roots) == 1 else None
    except MayaBoneAuthoringError:
        raise
    if len(registry_roots) != 1 or linked_root != requested_root:
        _fail("model registry root connection is invalid")
    if not _exists(adapter, registry, ATTR_MMD_REGISTRY_MORPH_MEMBERS):
        return []
    members = _call(
        adapter,
        "list_connections",
        f"{registry}.{ATTR_MMD_REGISTRY_MORPH_MEMBERS}",
        source=True,
        destination=False,
    ) or []
    if isinstance(members, (str, bytes, bytearray)):
        _fail("model registry morph members must be a sequence")
    result = []
    for member in members:
        member = str(member)
        if not _call(adapter, "object_exists", member):
            _fail(f"model registry morph member does not exist: {member}")
        result.append(member)
    return result


def _morph_nodes(adapter: Any, root: str) -> list[str]:
    registry_nodes = _registry_morph_nodes(adapter, root)
    nodes = registry_nodes if registry_nodes is not None else (_call(adapter, "ls", type="network") or [])
    result = []
    for node in nodes:
        node = str(node)
        if not _exists(adapter, node, "mmd_morph_type"):
            if registry_nodes is not None:
                _fail(f"model registry morph member is missing mmd_morph_type: {node}")
            continue
        if registry_nodes is not None:
            result.append(node)
            continue
        if not _exists(adapter, node, ATTR_MMD_MODEL_ROOT):
            continue
        roots = _call(adapter, "list_connections", f"{node}.{ATTR_MMD_MODEL_ROOT}", source=True, destination=False) or []
        if root in roots:
            result.append(node)
    return result


def _remap_bone_morph_json(adapter: Any, node: str, attr: str, mapping: Mapping[int, int]) -> str | None:
    if not _exists(adapter, node, attr):
        return None
    values = _mapping_list(_read_json(adapter, node, attr, field=f"{node}.{attr}"), field=f"{node}.{attr}")
    updated = []
    for offset in values:
        old_index = _require_index(offset.get("bone_index"), field=f"{node}.{attr}.bone_index", minimum=0)
        if old_index not in mapping:
            _fail(f"{node}.{attr} references unknown bone index {old_index}")
        offset["bone_index"] = mapping[old_index]
        updated.append(offset)
    return json.dumps(updated, ensure_ascii=False, separators=(",", ":"))


def apply_bone_reindex(
    root: str,
    old_spec: MmdModelAuthoringSpec,
    new_spec: MmdModelAuthoringSpec,
    adapter: Any,
) -> None:
    """Remap all known bone references in one prevalidated adapter call."""
    mapping = _validate_reindex_specs(old_spec, new_spec)
    _require_string(root, field="root")
    joints = _descendant_joints(adapter, root)
    by_binding = {bone.binding_identity: bone for bone in old_spec.bones}
    scene_joints: dict[str, str] = {}
    for joint in joints:
        if _exists(adapter, joint, ATTR_MMD_BONE_INDEX):
            scene_joints[joint] = joint
    if set(scene_joints) != {binding for binding in by_binding if binding is not None}:
        _fail("scene bone binding identities do not match old_spec")

    # Validate all direct bone references and build the final values before any
    # temporary index write occurs.
    direct_updates: list[tuple[str, str, int]] = []
    for joint, old_bone in ((binding, by_binding[binding]) for binding in scene_joints):
        if _read_int(adapter, joint, ATTR_MMD_BONE_INDEX) != old_bone.index:
            _fail(f"scene bone index mismatch for {joint}")
        new_bone = next(item for item in new_spec.bones if item.binding_identity == joint)
        for field, attrs in _BONE_REFERENCE_FIELDS:
            old_value = getattr(old_bone, field)
            new_value = getattr(new_bone, field)
            if old_value is None:
                stale = [attr for attr in attrs if _exists(adapter, joint, attr)]
                if stale:
                    _fail(f"{joint} has stale {field} metadata: {stale!r}")
                continue
            if attrs == (ATTR_MMD_BONE_PARENT_INDEX,):
                actual = _read_int(adapter, joint, attrs[0])
                if actual != old_value:
                    _fail(f"{joint}.{attrs[0]} does not match old_spec")
                direct_updates.append((joint, attrs[0], new_value))
                continue
            present = [attr for attr in attrs if _exists(adapter, joint, attr)]
            if old_value == -1 and not present:
                continue
            if not present:
                _fail(f"{joint} is missing {field} metadata")
            for attr in present:
                if _read_int(adapter, joint, attr) != old_value:
                    _fail(f"{joint}.{attr} does not match old_spec")
                direct_updates.append((joint, attr, new_value if new_value is not None else -1))
        if _exists(adapter, joint, ATTR_MMD_IK_LINKS):
            links = _mapping_list(
                _read_json(adapter, joint, ATTR_MMD_IK_LINKS, field=f"{joint}.{ATTR_MMD_IK_LINKS}"),
                field=f"{joint}.{ATTR_MMD_IK_LINKS}",
            )
            if len(links) != len(old_bone.ik_links):
                _fail(f"{joint}.{ATTR_MMD_IK_LINKS} does not match old_spec")
            for link in links:
                old_index = _require_index(link.get("bone"), field=f"{joint}.ik_links.bone", minimum=0)
                if old_index not in mapping:
                    _fail(f"{joint}.ik_links references unknown bone index {old_index}")
            updated_links = [dict(link, bone=mapping[link["bone"]]) for link in links]
            direct_updates.append(
                (
                    joint,
                    ATTR_MMD_IK_LINKS,
                    json.dumps(updated_links, ensure_ascii=False, separators=(",", ":")),
                )
            )
        elif old_bone.ik_links:
            _fail(f"{joint}.{ATTR_MMD_IK_LINKS} is missing")

    display = _scene_display_payload(adapter, root)
    display_json = None if display is None else json.dumps(_remap_display_frames(display[1], mapping), ensure_ascii=False, separators=(",", ":"))
    physics_updates: list[tuple[str, int]] = []
    for node in _call(adapter, "list_relatives", root, allDescendents=True, fullPath=True) or []:
        node = str(node)
        if not _exists(adapter, node, "relatedBoneIndex"):
            continue
        old_index = _read_int(adapter, node, "relatedBoneIndex")
        if old_index != -1 and old_index not in mapping:
            _fail(f"{node}.relatedBoneIndex references unknown bone index {old_index}")
        physics_updates.append((node, mapping.get(old_index, -1)))

    morph_updates: list[tuple[str, str, str]] = []
    for node in _morph_nodes(adapter, root):
        morph_type = _get(adapter, node, "mmd_morph_type")
        if morph_type != "bone":
            continue
        for attr in (ATTR_MMD_BONE_MORPH_OFFSETS_RAW_JSON, "mmd_bone_morph_offsets_json"):
            updated = _remap_bone_morph_json(adapter, node, attr, mapping)
            if updated is not None:
                morph_updates.append((node, attr, updated))

    for joint in scene_joints:
        _set_attr(adapter, joint, ATTR_MMD_BONE_INDEX, "long", -(by_binding[joint].index + 1))
    for joint, attr, value in direct_updates:
        _set_attr(adapter, joint, attr, "string" if attr == ATTR_MMD_IK_LINKS else "long", value)
    for joint in scene_joints:
        new_bone = next(item for item in new_spec.bones if item.binding_identity == joint)
        _set_attr(adapter, joint, ATTR_MMD_BONE_INDEX, "long", new_bone.index)
    if display_json is not None:
        _set_attr(adapter, root, ATTR_MMD_DISPLAY_FRAMES_JSON, "string", display_json)
    for node, value in physics_updates:
        _set_attr(adapter, node, "relatedBoneIndex", "long", value)
    for node, attr, value in morph_updates:
        _set_attr(adapter, node, attr, "string", value)


def unregister_existing_joint(root: str, joint: str, adapter: Any) -> None:
    """Remove canonical MMD bone metadata while retaining the Maya joint."""
    _require_root_joint(adapter, root, joint)
    if not _exists(adapter, joint, ATTR_MMD_BONE_INDEX):
        _fail(f"joint {joint!r} is not registered")
    index = _read_int(adapter, joint, ATTR_MMD_BONE_INDEX)
    for other in _descendant_joints(adapter, root):
        if other == joint or not _exists(adapter, other, ATTR_MMD_BONE_INDEX):
            continue
        for _field, attrs in _BONE_REFERENCE_FIELDS:
            for attr in attrs:
                if _exists(adapter, other, attr) and _read_int(adapter, other, attr) == index:
                    _fail(f"joint {joint!r} is referenced by {other}.{attr}")
        if _exists(adapter, other, ATTR_MMD_IK_LINKS):
            links = _mapping_list(
                _read_json(adapter, other, ATTR_MMD_IK_LINKS, field=f"{other}.{ATTR_MMD_IK_LINKS}"),
                field=f"{other}.{ATTR_MMD_IK_LINKS}",
            )
            for link in links:
                link_index = _require_index(link.get("bone"), field=f"{other}.ik_links.bone", minimum=0)
                if link_index == index:
                    _fail(f"joint {joint!r} is referenced by {other}.{ATTR_MMD_IK_LINKS}")
    display = _scene_display_payload(adapter, root)
    if display is not None:
        for frame in display[1]:
            for element in frame.get("elements", []):
                if element.get("type") in (0, "bone") and element.get("index") == index:
                    _fail(f"joint {joint!r} is referenced by display frames")
    for node in _call(adapter, "list_relatives", root, allDescendents=True, fullPath=True) or []:
        node = str(node)
        if _exists(adapter, node, "relatedBoneIndex") and _read_int(adapter, node, "relatedBoneIndex") == index:
            _fail(f"joint {joint!r} is referenced by physics node {node}")
    for node in _morph_nodes(adapter, root):
        if _get(adapter, node, "mmd_morph_type") != "bone":
            continue
        for attr in (ATTR_MMD_BONE_MORPH_OFFSETS_RAW_JSON, "mmd_bone_morph_offsets_json"):
            if not _exists(adapter, node, attr):
                continue
            offsets = _mapping_list(_read_json(adapter, node, attr, field=f"{node}.{attr}"), field=f"{node}.{attr}")
            for offset in offsets:
                offset_index = _require_index(offset.get("bone_index"), field=f"{node}.{attr}.bone_index", minimum=0)
                if offset_index == index:
                    _fail(f"joint {joint!r} is referenced by morph node {node}")
    for attr in _BONE_ATTRS:
        if _exists(adapter, joint, attr):
            _call(adapter, "delete_attr", f"{joint}.{attr}")


def _animation_warning(root: str, joints: Sequence[str], adapter: Any) -> str | None:
    """Return a non-blocking animation warning, or ``None`` when absent."""
    try:
        current = None
        for name in ("current_time", "currentTime"):
            method = getattr(adapter, name, None)
            if callable(method):
                try:
                    # MayaCmdsAdapter exposes current_time() without kwargs;
                    # raw maya.cmds.currentTime accepts query=True.
                    current = method() if name == "current_time" else method(query=True)
                except TypeError:
                    current = method(q=True)
                break
        animated = False
        detector_failed = False
        control_rig_owned = False
        if _exists(adapter, root, ATTR_MMD_CONTROL_RIG_JSON):
            raw_metadata = _get(adapter, root, ATTR_MMD_CONTROL_RIG_JSON)
            if raw_metadata:
                try:
                    metadata = json.loads(raw_metadata)
                    control_rig_owned = (
                        isinstance(metadata, Mapping)
                        and metadata.get("schema") == "mmd_tools.mmd_control_rig"
                        and bool(metadata)
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    # Malformed model-owned metadata is still a non-blocking
                    # detector warning; reset remains available.
                    detector_failed = True
        for joint in joints:
            if callable(getattr(adapter, "keyframe", None)):
                try:
                    count = _call(
                        adapter,
                        "keyframe",
                        f"{joint}.translate",
                        query=True,
                        keyframeCount=True,
                    )
                    animated = bool(count)
                    if not animated:
                        count = _call(
                            adapter,
                            "keyframe",
                            f"{joint}.rotate",
                            query=True,
                            keyframeCount=True,
                        )
                        animated = bool(count)
                except Exception:
                    # Detector failure is deliberately a warning, never a
                    # reset blocker.  Continue probing other joints.
                    detector_failed = True
            if animated:
                break
            # A direct incoming animCurve connection is a valid fallback when
            # keyframeCount is unavailable.  Ordinary DG/IK connections are
            # intentionally ignored.
            if callable(getattr(adapter, "list_connections", None)):
                for plug in ("translate", "rotate", "scale"):
                    try:
                        connections = _call(
                            adapter,
                            "list_connections",
                            f"{joint}.{plug}",
                            source=True,
                            destination=False,
                        ) or []
                    except Exception:
                        detector_failed = True
                        continue
                    for connection in connections:
                        try:
                            if callable(getattr(adapter, "node_type", None)) and _call(
                                adapter, "node_type", connection
                            ) == "animCurve":
                                animated = True
                                break
                        except Exception:
                            detector_failed = True
                            continue
                    if animated:
                        break
            if animated:
                break
        if control_rig_owned:
            frame = "?" if current is None else str(current)
            return f"owned Control Rig routes detected; current frame {frame} will be captured as PMX Rest"
        if not animated and detector_failed:
            return "animation detection unavailable; current frame will be captured as PMX Rest"
        if not animated:
            return None
        frame = "?" if current is None else str(current)
        return f"animation inputs detected; current frame {frame} will be captured as PMX Rest"
    except Exception as exc:
        return f"animation detection unavailable ({exc}); current frame will be captured as PMX Rest"


def _read_only_reason(node: str, adapter: Any) -> str | None:
    """Probe common injected-adapter read-only/reference contracts."""
    for method_name in ("is_read_only", "node_is_read_only", "is_locked"):
        method = getattr(adapter, method_name, None)
        if not callable(method):
            continue
        try:
            if bool(method(node)):
                return f"node {node!r} is read-only"
        except Exception as exc:
            return f"read-only state for {node!r} is unknown: {exc}"
    reference_query = getattr(adapter, "reference_query", None)
    if callable(reference_query):
        try:
            if bool(reference_query(node, isNodeReferenced=True)):
                return f"node {node!r} is referenced/read-only"
        except Exception as exc:
            return f"reference state for {node!r} is unknown: {exc}"
    return None


def _validate_removed_scene_references(
    root: str,
    removed: Mapping[str, MmdBoneSpec],
    adapter: Any,
) -> list[str]:
    """Inspect display/physics/morph references without mutating Maya."""
    blockers: list[str] = []
    removed_indices = {bone.index for bone in removed.values()}
    display = _scene_display_payload(adapter, root)
    if display is not None:
        for frame in display[1]:
            for element in frame.get("elements", []):
                if element.get("type") in (0, "bone") and element.get("index") in removed_indices:
                    blockers.append("removed bone is referenced by display frames")
    for node in _call(adapter, "list_relatives", root, allDescendents=True, fullPath=True) or []:
        node = str(node)
        if _exists(adapter, node, "relatedBoneIndex") and _read_int(adapter, node, "relatedBoneIndex") in removed_indices:
            blockers.append(f"removed bone is referenced by physics node {node}")
    for node in _morph_nodes(adapter, root):
        if _get(adapter, node, "mmd_morph_type") != "bone":
            continue
        for attr in (ATTR_MMD_BONE_MORPH_OFFSETS_RAW_JSON, "mmd_bone_morph_offsets_json"):
            if not _exists(adapter, node, attr):
                continue
            offsets = _mapping_list(_read_json(adapter, node, attr, field=f"{node}.{attr}"), field=f"{node}.{attr}")
            for offset in offsets:
                _require_index(offset.get("bone_index"), field=f"{node}.{attr}.bone_index", minimum=0)
            if any(offset.get("bone_index") in removed_indices for offset in offsets):
                blockers.append(f"removed bone is referenced by morph node {node}")
    return sorted(set(blockers))


def _validate_scene_binding_payload(
    root: str,
    spec: MmdModelAuthoringSpec,
    adapter: Any,
) -> list[str]:
    """Check registered joint reference metadata before structural writes."""
    blockers: list[str] = []
    by_binding = {bone.binding_identity: bone for bone in spec.bones if bone.binding_identity is not None}
    descendants = {
        _canonical_identity(adapter, item)
        for item in _descendant_joints(adapter, root)
        if _exists(adapter, _canonical_identity(adapter, item), ATTR_MMD_BONE_INDEX)
    }
    for binding, bone in by_binding.items():
        if binding not in descendants:
            continue
        if _read_int(adapter, binding, ATTR_MMD_BONE_INDEX, minimum=0) != bone.index:
            blockers.append(f"scene index mismatch for {binding!r}")
        if not _exists(adapter, binding, ATTR_MMD_BONE_PARENT_INDEX):
            blockers.append(f"{binding} is missing parent index metadata")
        else:
            if _read_int(adapter, binding, ATTR_MMD_BONE_PARENT_INDEX) != bone.parent_index:
                blockers.append(f"{binding} parent index metadata does not match authoring spec")
        for field, attrs in _BONE_REFERENCE_FIELDS[1:]:
            value = getattr(bone, field)
            present = [attr for attr in attrs if _exists(adapter, binding, attr)]
            if value is None:
                if present:
                    blockers.append(f"{binding} has stale {field} metadata")
                continue
            if not present:
                blockers.append(f"{binding} is missing {field} metadata")
                continue
            for attr in present:
                if _read_int(adapter, binding, attr) != value:
                    blockers.append(f"{binding}.{attr} does not match authoring spec")
        if bone.ik_links:
            if not _exists(adapter, binding, ATTR_MMD_IK_LINKS):
                blockers.append(f"{binding} is missing IK link metadata")
            else:
                try:
                    links = _mapping_list(
                        _read_json(adapter, binding, ATTR_MMD_IK_LINKS, field=f"{binding}.{ATTR_MMD_IK_LINKS}"),
                        field=f"{binding}.{ATTR_MMD_IK_LINKS}",
                    )
                    if len(links) != len(bone.ik_links):
                        blockers.append(f"{binding} IK link metadata does not match authoring spec")
                except MayaBoneAuthoringError as exc:
                    blockers.append(str(exc))
    return sorted(set(blockers))


def plan_bone_reset(
    root: str,
    current_spec: MmdModelAuthoringSpec,
    model_scale: float,
    adapter: Any,
    *,
    requested_order: Sequence[str] | None = None,
) -> BoneResetPlan:
    """Read-only scene-as-authority preflight for the Bone Tab Reset action."""
    try:
        _require_string(root, field="root")
        scale = _require_number(model_scale, field="model_scale", positive=True)
        descendants = []
        for item in _descendant_joints(adapter, root):
            canonical = _canonical_identity(adapter, item)
            if canonical not in descendants:
                descendants.append(canonical)
        # Existing persisted bindings must resolve to one descendant.  Missing
        # or ambiguous nodes become removals/blockers before any writes.
        by_binding = {
            bone.binding_identity: bone
            for bone in current_spec.bones
            if bone.binding_identity is not None
        }
        blockers: list[str] = []
        root_read_only = _read_only_reason(root, adapter)
        if root_read_only:
            blockers.append(root_read_only)
        for joint in descendants:
            reason = _read_only_reason(joint, adapter)
            if reason:
                blockers.append(reason)
        scene_registered: set[str] = set()
        for joint in descendants:
            if not _exists(adapter, joint, ATTR_MMD_BONE_INDEX):
                if joint in by_binding:
                    blockers.append(f"registered binding {joint!r} is missing bone index metadata")
                continue
            index = _read_int(adapter, joint, ATTR_MMD_BONE_INDEX, minimum=0)
            if joint not in by_binding:
                blockers.append(f"unregistered joint {joint!r} has stale bone index {index}")
                continue
            if by_binding[joint].index != index:
                blockers.append(f"scene index mismatch for {joint!r}")
                continue
            scene_registered.add(joint)
        removed = {binding: bone for binding, bone in by_binding.items() if binding not in descendants}
        blockers.extend(_validate_scene_binding_payload(root, current_spec, adapter))
        blockers.extend(_validate_removed_scene_references(root, removed, adapter))
        descriptors: list[MmdBoneSpec] = []
        descendant_set = set(descendants)
        direct_children = {
            joint: _direct_joint_children(adapter, joint, descendant_set)
            for joint in descendants
        }
        provisional_new_indices = {
            joint: max((bone.index for bone in current_spec.bones), default=-1) + offset
            for offset, joint in enumerate(
                (item for item in descendants if item not in by_binding),
                start=1,
            )
        }
        for joint in descendants:
            position = capture_rest_position(root, joint, scale, adapter)
            existing = by_binding.get(joint)
            if existing is not None:
                descriptors.append(replace(existing, rest_position=position, binding_identity=joint))
            else:
                leaf = joint.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
                parents = _call(
                    adapter,
                    "list_relatives",
                    joint,
                    parent=True,
                    fullPath=True,
                    type="joint",
                ) or []
                if isinstance(parents, (str, bytes, bytearray)):
                    _fail(f"parent joints for {joint!r} must be a sequence")
                if len(parents) > 1:
                    _fail(f"joint {joint!r} has multiple direct joint parents")
                parent_binding = _canonical_identity(adapter, parents[0]) if parents else None
                parent_index = (
                    by_binding[parent_binding].index
                    if parent_binding in by_binding
                    else provisional_new_indices.get(parent_binding, -1)
                )
                children = direct_children.get(joint, ())
                if len(children) == 1:
                    child_binding = children[0]
                    child_index = (
                        by_binding[child_binding].index
                        if child_binding in by_binding
                        else provisional_new_indices[child_binding]
                    )
                    flags = int(PmxBoneFlag.CONNECT_BONE)
                    connect_bone_index = child_index
                    tail_offset = None
                else:
                    flags = 0
                    connect_bone_index = None
                    tail_offset = (0.0, -1.0, 0.0)
                descriptors.append(
                    MmdBoneSpec(
                        name=leaf,
                        name_english=leaf,
                        parent_index=parent_index,
                        rest_position=position,
                        flags=flags,
                        connect_bone_index=connect_bone_index,
                        tail_offset=tail_offset,
                        binding_identity=joint,
                    )
                )
        warning = _animation_warning(root, descendants, adapter)
        derivation_warnings: list[str] = []
        for joint in descendants:
            if joint in by_binding:
                continue
            children = direct_children.get(joint, ())
            if len(children) == 0:
                derivation_warnings.append(
                    f"new bone {joint!r} has zero direct children; derived tail offset (0.0, -1.0, 0.0)"
                )
            elif len(children) > 1:
                derivation_warnings.append(
                    f"new bone {joint!r} has multiple direct children; derived tail offset (0.0, -1.0, 0.0)"
                )
        effective_order = requested_order
        if effective_order is None:
            # Maya's allDescendents order is not a semantic ordering (it is
            # commonly child-first). Keep persisted bindings stable, then
            # append newly discovered descendants before contiguous reindex.
            effective_order = tuple(
                bone.binding_identity
                for bone in current_spec.bones
                if bone.binding_identity in {item.binding_identity for item in descriptors}
            )
        plan = make_bone_reset_plan(
            current_spec,
            descriptors,
            requested_order=effective_order,
            blockers=blockers,
            warnings=tuple(derivation_warnings) + (() if warning is None else (warning,)),
        )
        return plan
    except Exception as exc:
        return BoneResetPlan(
            current_spec=current_spec,
            target_spec=None,
            expected_fingerprint=current_spec.fingerprint(),
            blockers=(str(exc),),
        )


def apply_bone_reset_structure(
    root: str,
    plan: BoneResetPlan,
    adapter: Any,
) -> MmdModelAuthoringSpec:
    """Apply one already validated structural reset inside an open transaction."""
    if not isinstance(plan, BoneResetPlan) or not plan.is_valid or plan.target_spec is None:
        _fail("bone reset plan is blocked or malformed")
    current = plan.current_spec
    target = plan.target_spec
    by_binding = {bone.binding_identity: bone for bone in current.bones if bone.binding_identity is not None}
    descendants = {
        _canonical_identity(adapter, item)
        for item in _descendant_joints(adapter, root)
    }
    # Remove metadata that still exists.  All external references were checked
    # during preflight, so these calls cannot partially fail for semantics.
    for binding in plan.removed_bindings:
        if binding in descendants and _exists(adapter, binding, ATTR_MMD_BONE_INDEX):
            unregister_existing_joint(root, binding, adapter)

    additions = [bone for bone in target.bones if bone.binding_identity not in by_binding]
    next_index = max((bone.index for bone in current.bones), default=-1) + 1
    intermediate_indices: dict[str, int] = {
        binding: bone.index
        for binding, bone in by_binding.items()
        if binding is not None and binding in descendants
    }
    for bone in additions:
        if bone.binding_identity is None:
            _fail("added bone is missing binding identity")
        intermediate_indices[bone.binding_identity] = next_index
        next_index += 1
    target_to_intermediate = {}
    for bone in target.bones:
        binding = bone.binding_identity
        if binding in intermediate_indices:
            target_to_intermediate[bone.index] = intermediate_indices[binding]

    def remap_intermediate(value: int | None) -> int | None:
        if value is None or value == -1:
            return value
        try:
            return target_to_intermediate[value]
        except KeyError as exc:
            _fail(f"bone reference {value} is not present in reset target")
            raise AssertionError from exc

    temporary_additions = []
    for bone in additions:
        temporary_additions.append(
            replace(
                bone,
                index=intermediate_indices[bone.binding_identity],
                parent_index=remap_intermediate(bone.parent_index),
                connect_bone_index=remap_intermediate(bone.connect_bone_index),
                grant_parent_index=remap_intermediate(bone.grant_parent_index),
                ik_target_index=remap_intermediate(bone.ik_target_index),
                ik_links=tuple(
                    {**link, "bone": remap_intermediate(link.get("bone"))}
                    for link in bone.ik_links
                ),
            )
        )
    if temporary_additions:
        register_existing_joints(root, tuple(temporary_additions), adapter)
    survivors = [bone for bone in current.bones if bone.binding_identity in descendants and bone.binding_identity in {item.binding_identity for item in target.bones}]
    intermediate = MmdModelAuthoringSpec(
        model=current.model,
        bones=tuple([*survivors, *temporary_additions]),
        materials=current.materials,
        morphs=current.morphs,
        schema_version=current.schema_version,
    )
    # One complete remap updates indices and all known display/physics/morph
    # references after additions/removals have been staged.
    apply_bone_reindex(root, intermediate, target, adapter)
    return target


__all__ = [
    "MayaBoneAuthoringError",
    "register_existing_joint",
    "register_existing_joints",
    "capture_rest_position",
    "apply_bone_reindex",
    "unregister_existing_joint",
    "plan_bone_reset",
    "apply_bone_reset_structure",
    "BoneResetPlan",
]
