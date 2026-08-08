#!/usr/bin/env python
"""Run the Maya-side v0.7 export release probes.

The probe deliberately starts a fresh Maya scene for each import boundary.  It
exports a small PMX fixture, verifies a representative PMX morph roundtrip,
verifies a representative rigid-body/joint PMX roundtrip, verifies PMX 2.1
soft-body, SDEF, Flip, Impulse, and PMD public export policy rejections, and
exports a VMD motion.
The JSON output is consumed by
:mod:`tools.export_release_gate`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import traceback
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
BUILD_ROOT = (ROOT / "build").resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.common.maya_plugin_setup import load_mmd_tools_plugin  # noqa: E402


DEFAULT_PMX = ROOT / "tests" / "data" / "for_unit_test" / "test_1bone_cube.pmx"
DEFAULT_PHYSICS_PMX = ROOT / "tests" / "data" / "physics" / "test_hair_physics.pmx"
DEFAULT_MORPH_PMX = ROOT / "tests" / "data" / "for_unit_test" / "test_vmd_morph_real_gate.pmx"
DEFAULT_VMD = ROOT / "tests" / "data" / "for_unit_test" / "test_1bone_cube_motion.vmd"
ORACLE_FRAMES = (0, 9, 19, 29, 39, 49)
FLOAT_TOLERANCE = 1.0e-4
SUPPORTED_MORPH_TYPES = (
    "vertex",
    "bone",
    "uv",
    "additional_uv1",
    "additional_uv2",
    "additional_uv3",
    "additional_uv4",
    "material",
    "group",
)
MORPH_TYPE_NAMES = {
    0: "group",
    1: "vertex",
    2: "bone",
    3: "uv",
    4: "additional_uv1",
    5: "additional_uv2",
    6: "additional_uv3",
    7: "additional_uv4",
    8: "material",
    9: "flip",
}
MORPH_OFFSET_ATTRIBUTES = {
    # Bone offsets must retain their original PMX-space values here.  The
    # regular attribute carries importer-scale-adjusted translations for the
    # runtime, while this provenance attribute is the export-facing payload.
    "bone": "mmd_bone_morph_offsets_raw_json",
    "group": "mmd_group_morph_offsets_json",
    "material": "mmd_material_morph_offsets_json",
    "uv": "mmd_uv_morph_offsets_json",
    "additional_uv1": "mmd_uv_morph_offsets_json",
    "additional_uv2": "mmd_uv_morph_offsets_json",
    "additional_uv3": "mmd_uv_morph_offsets_json",
    "additional_uv4": "mmd_uv_morph_offsets_json",
}
MORPH_ORACLE_EXCLUSIONS = (
    "sdef vertex deformation",
    "UV morph runtime evaluation",
    "Impulse morph physics effect",
)


def _require_build_path(value: str | Path, label: str) -> Path:
    """Resolve an output path and reject paths outside ``build/``."""
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if path != BUILD_ROOT and BUILD_ROOT not in path.parents:
        raise ValueError(f"{label} must resolve under {BUILD_ROOT}: {path}")
    return path


def _json_default(value: Any) -> Any:
    """Convert small Maya/Python scalar values into JSON-safe values."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return list(value)
    return str(value)


def _round_values(values: Iterable[float], digits: int = 7) -> list[float]:
    """Round numeric oracle values to make the digest deterministic."""
    return [round(float(value), digits) for value in values]


def _digest_json(value: Any) -> str:
    """Return a stable digest for a JSON-safe oracle fragment."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _write_independent_pmd_fixture(path: Path) -> None:
    """Write the repository's independent supported PMD fixture for this run."""
    from tests.common.pmd_mock import PmdMock

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PmdMock.create_minimal_pmd())


def _prepare_physics_probe_fixture(source: Path, out_dir: Path) -> tuple[Path, list[dict[str, Any]]]:
    """Create a probe input with malformed display-only tail references normalized."""
    from mmd_tools.core.pmx_data import PmxData
    from mmd_tools.core.pmx_data.bone import PmxBoneFlag

    data = PmxData().parse_file(str(source))
    normalizations = []
    for index, bone in enumerate(data.bones):
        if not bone.get_flag(PmxBoneFlag.CONNECT_BONE):
            continue
        if 0 <= bone.connect_bone_index < len(data.bones):
            continue
        normalizations.append(
            {
                "bone_index": index,
                "bone_name": bone.name,
                "reason": "invalid connected-tail index; use relative tail offset for probe input",
                "original_connect_bone_index": bone.connect_bone_index,
            }
        )
        bone.bone_flag = int(bone.bone_flag) & ~int(PmxBoneFlag.CONNECT_BONE)
        bone.connect_position_offset = (0.0, 0.0, 0.0)

    output = out_dir / "fixtures" / "physics_probe_input.pmx"
    output.parent.mkdir(parents=True, exist_ok=True)
    data.write_file(str(output))
    return output, normalizations


def _write_soft_body_probe_fixture(path: Path) -> Path:
    """Write a minimal PMX 2.1 fixture with one unsupported soft body."""
    from mmd_tools.core.pmx_data import PmxData
    from mmd_tools.core.pmx_data.soft_body import PmxSoftBody
    from tests.common.pmx_mock import PmxMock

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PmxMock.create_minimal_pmx(version=2.1))
    pmx = PmxData().parse_file(str(path))
    soft_body = PmxSoftBody(
        material_index_size=pmx.header.material_index_size,
        rigid_body_index_size=pmx.header.rigid_body_index_size,
        vertex_index_size=pmx.header.vertex_index_size,
        encoding_flag=0,
    )
    soft_body.name = "probe_cloth"
    soft_body.name_english = "probe_cloth"
    soft_body.material_index = 0
    pmx.soft_bodies = [soft_body]
    pmx.write_file(str(path))
    return path


def _write_sdef_probe_fixture(path: Path) -> Path:
    """Write a minimal PMX fixture with one raw SDEF vertex payload."""
    from mmd_tools.core.pmx_data import PmxData
    from tests.common.pmx_mock import PmxMock

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PmxMock.create_minimal_pmx())
    pmx = PmxData().parse_file(str(path))
    vertex = pmx.vertices[0]
    vertex.weight_transform_type = 3
    vertex.bone_indices = [0, 1]
    vertex.bone_weights = [0.75]
    vertex.sdef_c = (0.0, 0.25, 0.0)
    vertex.sdef_r0 = (-0.25, 0.0, 0.0)
    vertex.sdef_r1 = (0.25, 0.0, 0.0)
    pmx.write_file(str(path))
    return path


def _write_impulse_probe_fixture(path: Path) -> Path:
    """Write a minimal PMX 2.1 fixture with one raw Impulse morph."""
    from mmd_tools.core.pmx_data import PmxData
    from mmd_tools.core.pmx_data.morph import PmxMorph, PmxMorphType
    from mmd_tools.core.pmx_data.rigid_body import PmxRigidBody
    from tests.common.pmx_mock import PmxMock

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PmxMock.create_minimal_pmx(version=2.1))
    pmx = PmxData().parse_file(str(path))
    rigid_body = PmxRigidBody(
        bone_index_size=pmx.header.bone_index_size,
        encoding_flag=0,
    )
    rigid_body.name = "probe_impulse_body"
    rigid_body.name_english = "probe_impulse_body"
    rigid_body.related_bone_index = 0
    rigid_body.size = (0.5, 0.5, 0.5)
    rigid_body.mass = 1.0
    rigid_body.velocity_attenuation = 0.5
    rigid_body.rotation_attenuation = 0.5
    rigid_body.elasticity = 0.5
    rigid_body.friction = 0.5
    pmx.rigid_bodies = [rigid_body]

    morph = PmxMorph(
        vertex_index_size=pmx.header.vertex_index_size,
        material_index_size=pmx.header.material_index_size,
        bone_index_size=pmx.header.bone_index_size,
        morph_index_size=pmx.header.morph_index_size,
        rigid_body_index_size=pmx.header.rigid_body_index_size,
        encoding=pmx.header.encoding,
    )
    morph.name = "probe_impulse"
    morph.name_english = "probe_impulse"
    morph.panel = 4
    morph.morph_type = PmxMorphType.ImpulseMorph
    morph.offsets = [
        {
            "rigid_body_index": 0,
            "impulse": (0.1, -0.2, 0.3),
            "torque": (-0.4, 0.5, -0.6),
        }
    ]
    pmx.morphs = [morph]
    pmx.write_file(str(path))
    return path


def _write_flip_probe_fixture(path: Path) -> Path:
    """Write a minimal PMX 2.1 fixture with one raw Flip morph."""
    from mmd_tools.core.pmx_data import PmxData
    from mmd_tools.core.pmx_data.morph import PmxMorph, PmxMorphType
    from tests.common.pmx_mock import PmxMock

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PmxMock.create_minimal_pmx(version=2.1))
    pmx = PmxData().parse_file(str(path))
    vertex_morph = PmxMorph(
        vertex_index_size=pmx.header.vertex_index_size,
        material_index_size=pmx.header.material_index_size,
        bone_index_size=pmx.header.bone_index_size,
        morph_index_size=pmx.header.morph_index_size,
        rigid_body_index_size=pmx.header.rigid_body_index_size,
        encoding=pmx.header.encoding,
    )
    vertex_morph.name = "probe_flip_target"
    vertex_morph.name_english = "probe_flip_target"
    vertex_morph.panel = 4
    vertex_morph.morph_type = PmxMorphType.VertexMorph
    vertex_morph.offsets = [{"vertex_index": 0, "position_offset": (0.1, 0.0, 0.0)}]
    flip_morph = PmxMorph(
        vertex_index_size=pmx.header.vertex_index_size,
        material_index_size=pmx.header.material_index_size,
        bone_index_size=pmx.header.bone_index_size,
        morph_index_size=pmx.header.morph_index_size,
        rigid_body_index_size=pmx.header.rigid_body_index_size,
        encoding=pmx.header.encoding,
    )
    flip_morph.name = "probe_flip"
    flip_morph.name_english = "probe_flip"
    flip_morph.panel = 4
    flip_morph.morph_type = PmxMorphType.FlipMorph
    flip_morph.offsets = [{"morph_index": 0, "flip_rate": 0.25}]
    pmx.morphs = [vertex_morph, flip_morph]
    pmx.write_file(str(path))
    return path


def _write_bone_semantics_probe_fixture(path: Path, base_model: Path) -> Path:
    """Write a PMX 2.0 fixture covering the advertised bone semantic subset."""
    from mmd_tools.core.pmx_data import PmxData
    from mmd_tools.core.pmx_data.bone import PmxBone, PmxBoneFlag
    from mmd_tools.core.pmx_data.ik_link import PmxIKLink

    path.parent.mkdir(parents=True, exist_ok=True)
    pmx = PmxData().parse_file(str(base_model))
    size = pmx.header.bone_index_size
    encoding = pmx.header.encoding

    def bone(name: str, name_en: str, position: tuple[float, float, float], parent: int, flags: int) -> PmxBone:
        value = PmxBone(bone_index_size=size, encoding=encoding)
        value.name = name
        value.name_english = name_en
        value.position = position
        value.parent_bone_index = parent
        value.transform_layer = 0
        value.bone_flag = flags
        return value

    common = int(PmxBoneFlag.ROTATABLE | PmxBoneFlag.DISPLAY | PmxBoneFlag.OPERATABLE)
    root = bone("sem_root", "sem_root", (0.0, 0.0, 0.0), -1, common | int(PmxBoneFlag.MOVABLE))
    root.transform_layer = 2
    root.connect_position_offset = (0.0, 1.0, 0.0)

    offset = bone("sem_offset", "sem_offset", (0.0, 1.0, 0.0), 0, common)
    offset.connect_position_offset = (0.0, 2.0, 0.0)

    connected_flags = common | int(PmxBoneFlag.MOVABLE | PmxBoneFlag.CONNECT_BONE)
    connected_flags |= int(PmxBoneFlag.DEFORM_AFTER_PHYSICS | PmxBoneFlag.EXTERNAL_PARENT_DEFORM)
    connected = bone("sem_connected", "sem_connected", (0.0, 2.0, 0.0), 0, connected_flags)
    connected.connect_bone_index = 1
    connected.key_value = 1234

    grant_flags = common | int(PmxBoneFlag.LOCAL | PmxBoneFlag.GRANT_PARENT_ROTATE | PmxBoneFlag.GRANT_PARENT_MOVE)
    grant = bone("sem_grant", "sem_grant", (0.0, 3.0, 0.0), 1, grant_flags)
    grant.grant_parent_bone_index = 0
    grant.grant_rate = 0.35
    grant.connect_position_offset = (0.0, 1.0, 0.0)

    axis_flags = common | int(PmxBoneFlag.AXIS_FIXED | PmxBoneFlag.LOCAL_AXIS)
    axes = bone("sem_axes", "sem_axes", (0.0, 4.0, 0.0), 1, axis_flags)
    axes.axis_direction = (0.0, 1.0, 0.25)
    axes.x_axis_direction = (1.0, 0.1, 0.0)
    axes.z_axis_direction = (0.0, 0.0, 1.0)
    axes.connect_position_offset = (0.0, 1.0, 0.0)

    ik_flags = common | int(PmxBoneFlag.IK)
    ik = bone("sem_ik", "sem_ik", (0.0, 5.0, 0.0), 2, ik_flags)
    ik.ik_target_bone_index = 2
    ik.ik_loop_count = 8
    ik.ik_limit_angle = 0.75
    limited_link = PmxIKLink(size)
    limited_link.ik_bone_index = 4
    limited_link.angle_limit = 1
    limited_link.limit_min = (-0.1, -0.2, -0.3)
    limited_link.limit_max = (0.4, 0.5, 0.6)
    free_link = PmxIKLink(size)
    free_link.ik_bone_index = 3
    free_link.angle_limit = 0
    ik.ik_links = [limited_link, free_link]

    pmx.bones = [root, offset, connected, grant, axes, ik]
    pmx.write_file(str(path))
    return path


def _attribute_value(node: str, name: str) -> Any:
    """Read one optional Maya attribute without turning metadata into a blocker."""
    from maya import cmds

    if not cmds.attributeQuery(name, node=node, exists=True):
        return None
    try:
        return cmds.getAttr(f"{node}.{name}")
    except Exception:
        return None


def _scalar_attribute_value(node: str, name: str, converter: type) -> Any:
    """Read and convert one scalar attribute, returning ``None`` when absent."""
    value = _attribute_value(node, name)
    if isinstance(value, (list, tuple)) and len(value) == 1:
        value = value[0]
    if value is None:
        return None
    try:
        return converter(value)
    except (TypeError, ValueError):
        return None


def _vector_attribute_value(node: str, name: str) -> list[float]:
    """Read a Maya compound/vector attribute in a JSON-stable shape."""
    value = _attribute_value(node, name)
    if isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], (list, tuple)):
        value = value[0]
    if not isinstance(value, (list, tuple)):
        return []
    try:
        return _round_values(value)
    except (TypeError, ValueError):
        return []


def _find_material_nodes(meshes: Iterable[str]) -> list[str]:
    """Find unique MMD material nodes assigned below the supplied mesh transforms."""
    from maya import cmds

    materials = []
    for transform in meshes:
        shapes = cmds.listRelatives(transform, shapes=True, fullPath=True, type="mesh") or []
        for shape in shapes:
            shading_groups = cmds.listConnections(shape, type="shadingEngine") or []
            for shading_group in shading_groups:
                connected = cmds.listConnections(
                    shading_group,
                    source=True,
                    destination=False,
                ) or []
                for material in cmds.ls(connected, materials=True) or []:
                    if material in materials:
                        continue
                    if _scalar_attribute_value(material, "mmd_material", bool):
                        materials.append(str(material))
    return sorted(materials)


def _capture_material_oracle(meshes: Iterable[str]) -> list[dict[str, Any]]:
    """Capture authored MMD material semantics from shader nodes below a model."""
    from mmd_tools.converters.material_shader_parameters import ATTR_MMD_DIFFUSE_ALPHA, ATTR_MMD_EDGE_ALPHA
    from mmd_tools.core.constants import (
        ATTR_MMD_AMBIENT_COLOR,
        ATTR_MMD_DIFFUSE_COLOR,
        ATTR_MMD_DRAW_FLAGS,
        ATTR_MMD_EDGE_COLOR,
        ATTR_MMD_EDGE_SIZE,
        ATTR_MMD_EDGE_FLAG,
        ATTR_MMD_MATERIAL_INDEX,
        ATTR_MMD_MATERIAL_NAME,
        ATTR_MMD_MATERIAL_NAME_EN,
        ATTR_MMD_MEMO,
        ATTR_MMD_SHININESS,
        ATTR_MMD_SPECULAR_COLOR,
        ATTR_MMD_SHARED_TOON_FLAG,
        ATTR_MMD_SPHERE_PATH,
        ATTR_MMD_SPHERE_MODE,
        ATTR_MMD_SPHERE_TEXTURE_INDEX,
        ATTR_MMD_TEXTURE_INDEX,
        ATTR_MMD_TOON_TEXTURE_INDEX,
    )

    materials = []
    for node in _find_material_nodes(meshes):
        material_index = _scalar_attribute_value(node, ATTR_MMD_MATERIAL_INDEX, int)
        diffuse = _vector_attribute_value(node, ATTR_MMD_DIFFUSE_COLOR)
        diffuse_alpha = _scalar_attribute_value(node, ATTR_MMD_DIFFUSE_ALPHA, float)
        if diffuse_alpha is not None:
            diffuse.append(round(diffuse_alpha, 7))
        edge_color = _vector_attribute_value(node, ATTR_MMD_EDGE_COLOR)
        edge_alpha = _scalar_attribute_value(node, ATTR_MMD_EDGE_ALPHA, float)
        if edge_alpha is not None:
            edge_color.append(round(edge_alpha, 7))
        materials.append(
            {
                "index": material_index,
                "name": _attribute_value(node, ATTR_MMD_MATERIAL_NAME),
                "name_en": _attribute_value(node, ATTR_MMD_MATERIAL_NAME_EN),
                "diffuse": diffuse,
                "specular": _vector_attribute_value(node, ATTR_MMD_SPECULAR_COLOR),
                "ambient": _vector_attribute_value(node, ATTR_MMD_AMBIENT_COLOR),
                "edge_color": edge_color,
                "shininess": _scalar_attribute_value(node, ATTR_MMD_SHININESS, float),
                "draw_flags": _scalar_attribute_value(node, ATTR_MMD_DRAW_FLAGS, int),
                "edge_flag": _scalar_attribute_value(node, ATTR_MMD_EDGE_FLAG, int),
                "edge_size": _scalar_attribute_value(node, ATTR_MMD_EDGE_SIZE, float),
                "sphere_mode": _scalar_attribute_value(node, ATTR_MMD_SPHERE_MODE, int),
                "sphere_texture_index": _scalar_attribute_value(
                    node, ATTR_MMD_SPHERE_TEXTURE_INDEX, int
                ),
                "texture_index": _scalar_attribute_value(node, ATTR_MMD_TEXTURE_INDEX, int),
                "toon_texture_index": _scalar_attribute_value(
                    node, ATTR_MMD_TOON_TEXTURE_INDEX, int
                ),
                "shared_toon_flag": _scalar_attribute_value(
                    node, ATTR_MMD_SHARED_TOON_FLAG, int
                ),
                "memo": _attribute_value(node, ATTR_MMD_MEMO),
                "texture_path": _attribute_value(node, "mmd_texture_path"),
                "sphere_texture_path": _attribute_value(node, ATTR_MMD_SPHERE_PATH),
            }
        )
    return sorted(materials, key=lambda item: (item["index"] is None, item["index"] or -1, item["name"] or ""))


def _find_mesh_transforms(root: str) -> list[str]:
    """Return stable mesh transforms below a model root."""
    from maya import cmds

    shapes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
    transforms = []
    for shape in sorted(shapes):
        parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        if parents:
            transform = str(parents[0])
            if transform not in transforms:
                transforms.append(transform)
    return transforms


def _find_child_group(parent: str, group_name: str) -> str | None:
    """Return a named transform group directly below *parent*."""
    from maya import cmds

    children = cmds.listRelatives(parent, children=True, fullPath=True, type="transform") or []
    for child in children:
        leaf_name = str(child).rsplit("|", 1)[-1].rsplit(":", 1)[-1]
        if leaf_name == group_name:
            return str(child)
    return None


def _find_physics_shapes(root: str, group_name: str, node_type: str) -> list[tuple[str, str]]:
    """Return PMX-indexed ``(transform, shape)`` pairs for one physics group."""
    from maya import cmds

    from mmd_tools.core.constants import PHYSICS_GROUP

    physics_group = _find_child_group(root, PHYSICS_GROUP)
    if not physics_group:
        return []
    target_group = _find_child_group(physics_group, group_name)
    if not target_group:
        return []
    pairs = []
    for transform in cmds.listRelatives(target_group, children=True, fullPath=True, type="transform") or []:
        shapes = cmds.listRelatives(transform, shapes=True, fullPath=True, type=node_type) or []
        if shapes:
            pairs.append((str(transform), str(shapes[0])))
    return sorted(
        pairs,
        key=lambda pair: (
            _scalar_attribute_value(pair[1], "pmxIndex", int)
            if _scalar_attribute_value(pair[1], "pmxIndex", int) is not None
            else 2**31,
            pair[1],
        ),
    )


def _physics_message_index(
    shape: str,
    message_attr: str,
    fallback_attr: str,
    target_indices: Mapping[str, int],
) -> int:
    """Resolve one physics message reference without trusting a stale fallback."""
    from maya import cmds

    targets = cmds.listConnections(
        f"{shape}.{message_attr}", source=True, destination=False
    ) or []
    for target in targets:
        for long_name in cmds.ls(target, long=True) or []:
            if long_name in target_indices:
                return int(target_indices[long_name])
        short_name = str(target).rsplit("|", 1)[-1]
        if short_name in target_indices:
            return int(target_indices[short_name])
    fallback = _scalar_attribute_value(shape, fallback_attr, int)
    return int(fallback) if fallback is not None else -1


def _capture_physics_oracle(root: str) -> dict[str, list[dict[str, Any]]]:
    """Capture rigid-body/joint authoring attributes independently from the collector."""
    from mmd_tools.core.constants import CONSTRAINTS_GROUP, RIGID_BODIES_GROUP
    from mmd_tools.core.maya_angle import maya_angle_to_radians

    rigid_pairs = _find_physics_shapes(root, RIGID_BODIES_GROUP, "mmdRigidBodyShape")
    bone_indices = _bone_indices_below(root)
    rigid_body_indices: dict[str, int] = {}
    rigid_bodies = []
    for ordinal, (transform, shape) in enumerate(rigid_pairs):
        pmx_index = _scalar_attribute_value(shape, "pmxIndex", int)
        pmx_index = ordinal if pmx_index is None else pmx_index
        for name in (transform, *(str(value) for value in _long_names(transform))):
            rigid_body_indices[name] = int(pmx_index)
        rotation = maya_angle_to_radians(
            [
                _scalar_attribute_value(shape, "rotationX", float) or 0.0,
                _scalar_attribute_value(shape, "rotationY", float) or 0.0,
                _scalar_attribute_value(shape, "rotationZ", float) or 0.0,
            ]
        )
        rigid_bodies.append(
            {
                "pmx_index": int(pmx_index),
                "name": _attribute_value(shape, "nameJp") or "",
                "name_en": _attribute_value(shape, "nameEn") or "",
                "related_bone_index": _physics_message_index(
                    shape, "relatedBone", "relatedBoneIndex", bone_indices
                ),
                "group": _scalar_attribute_value(shape, "collisionGroup", int),
                "collision_mask": _scalar_attribute_value(shape, "collisionMask", int),
                "shape_type": _scalar_attribute_value(shape, "shapeType", int),
                "size": _vector_attribute_value(shape, "shapeSize"),
                "position": _vector_attribute_value(shape, "position"),
                "rotation": _round_values(rotation),
                "mass": _scalar_attribute_value(shape, "mass", float),
                "velocity_attenuation": _scalar_attribute_value(shape, "linearDamping", float),
                "rotation_attenuation": _scalar_attribute_value(shape, "angularDamping", float),
                "elasticity": _scalar_attribute_value(shape, "restitution", float),
                "friction": _scalar_attribute_value(shape, "friction", float),
                "physics_mode": _scalar_attribute_value(shape, "physicsMode", int),
            }
        )

    joint_pairs = _find_physics_shapes(root, CONSTRAINTS_GROUP, "mmdPhysicsJointShape")
    joints = []
    for ordinal, (_transform, shape) in enumerate(joint_pairs):
        pmx_index = _scalar_attribute_value(shape, "pmxIndex", int)
        pmx_index = ordinal if pmx_index is None else pmx_index
        rotation = maya_angle_to_radians(
            [
                _scalar_attribute_value(shape, "rotationX", float) or 0.0,
                _scalar_attribute_value(shape, "rotationY", float) or 0.0,
                _scalar_attribute_value(shape, "rotationZ", float) or 0.0,
            ]
        )
        rotation_limit_min = maya_angle_to_radians(
            [
                _scalar_attribute_value(shape, "rotationLimitMinX", float) or 0.0,
                _scalar_attribute_value(shape, "rotationLimitMinY", float) or 0.0,
                _scalar_attribute_value(shape, "rotationLimitMinZ", float) or 0.0,
            ]
        )
        rotation_limit_max = maya_angle_to_radians(
            [
                _scalar_attribute_value(shape, "rotationLimitMaxX", float) or 0.0,
                _scalar_attribute_value(shape, "rotationLimitMaxY", float) or 0.0,
                _scalar_attribute_value(shape, "rotationLimitMaxZ", float) or 0.0,
            ]
        )
        joints.append(
            {
                "pmx_index": int(pmx_index),
                "name": _attribute_value(shape, "nameJp") or "",
                "name_en": _attribute_value(shape, "nameEn") or "",
                "joint_type": _scalar_attribute_value(shape, "jointType", int),
                "rigid_body_a_index": _physics_message_index(
                    shape, "rigidBodyA", "rigidBodyAIndex", rigid_body_indices
                ),
                "rigid_body_b_index": _physics_message_index(
                    shape, "rigidBodyB", "rigidBodyBIndex", rigid_body_indices
                ),
                "position": _vector_attribute_value(shape, "position"),
                "rotation": _round_values(rotation),
                "translation_limit_min": _vector_attribute_value(shape, "translationLimitMin"),
                "translation_limit_max": _vector_attribute_value(shape, "translationLimitMax"),
                "rotation_limit_min": _round_values(rotation_limit_min),
                "rotation_limit_max": _round_values(rotation_limit_max),
                "spring_translation": _vector_attribute_value(shape, "springTranslation"),
                "spring_rotation": _vector_attribute_value(shape, "springRotation"),
            }
        )
    return {"rigid_bodies": rigid_bodies, "joints": joints}


def _normalize_morph_value(value: Any) -> Any:
    """Normalize PMX/Maya morph payloads without changing their semantics."""
    if isinstance(value, dict):
        return {str(key): _normalize_morph_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_morph_value(item) for item in value]
    if isinstance(value, float):
        return round(value, 7)
    return value


def _morph_json_attribute(node: str, attribute: str) -> list[dict[str, Any]]:
    """Read one required morph JSON attribute and fail closed when malformed."""
    from maya import cmds

    if not cmds.attributeQuery(attribute, node=node, exists=True):
        raise RuntimeError(f"morph node {node} is missing {attribute}")
    raw_value = cmds.getAttr(f"{node}.{attribute}")
    try:
        value = json.loads(raw_value or "[]")
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"morph node {node} has malformed {attribute}") from exc
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise RuntimeError(f"morph node {node} has non-list {attribute}")
    return _normalize_morph_value(value)


def _capture_sdef_import_provenance(root: str) -> dict[str, Any]:
    """Require positive SDEF count and raw payload after a fresh import."""
    from maya import cmds

    from mmd_tools.core.constants import (
        ATTR_MMD_PMX_SDEF_VERTEX_COUNT,
        ATTR_MMD_SDEF_VERTICES_JSON,
    )

    fresh_import_count = _scalar_attribute_value(
        root, ATTR_MMD_PMX_SDEF_VERTEX_COUNT, int
    )
    if fresh_import_count is None or fresh_import_count <= 0:
        raise RuntimeError(
            "fresh SDEF import did not retain a positive root vertex count: "
            f"{fresh_import_count!r}"
        )

    stored_payload = None
    stored_node = None
    for transform in _find_mesh_transforms(root):
        shapes = cmds.listRelatives(
            transform, shapes=True, fullPath=True, type="mesh"
        ) or []
        for node in (transform, *(str(shape) for shape in shapes)):
            raw_payload = _attribute_value(node, ATTR_MMD_SDEF_VERTICES_JSON)
            if raw_payload is None:
                continue
            try:
                payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"SDEF provenance on {node} is malformed") from exc
            if not isinstance(payload, dict) or not isinstance(payload.get("sdef_vertices"), list):
                raise RuntimeError(f"SDEF provenance on {node} is not a vertex payload")
            stored_payload = payload
            stored_node = node
            break
        if stored_payload is not None:
            break

    if stored_payload is None:
        raise RuntimeError("fresh SDEF import did not retain raw vertex provenance")
    stored_values = stored_payload["sdef_vertices"]
    provenance_count = sum(value is not None for value in stored_values)
    if provenance_count <= 0:
        raise RuntimeError("fresh SDEF import retained no non-null raw vertex payload")
    return {
        "fresh_import_sdef_vertex_count": fresh_import_count,
        "provenance_vertex_count": provenance_count,
        "provenance_node": stored_node,
    }


def _capture_impulse_import_provenance(root: str) -> dict[str, Any]:
    """Require positive raw Impulse metadata on the fresh-import model root."""
    entries = []
    for node in _owned_morph_network_nodes(root):
        if _attribute_value(node, "mmd_morph_type") != "impulse":
            continue
        entries.append(
            {
                "index": _required_morph_int(node, "mmd_morph_index"),
                "name": _required_morph_string(node, "mmd_morph_name"),
                "offsets": _morph_json_attribute(
                    node, "mmd_impulse_morph_offsets_json"
                ),
            }
        )
    if not entries:
        raise RuntimeError("fresh Impulse import did not retain raw morph provenance")
    offset_count = sum(len(entry["offsets"]) for entry in entries)
    if offset_count <= 0:
        raise RuntimeError("fresh Impulse import retained no raw offsets")
    return {
        "fresh_import_impulse_morph_count": len(entries),
        "provenance_offset_count": offset_count,
        "provenance_morph_indices": [entry["index"] for entry in entries],
    }


def _capture_flip_import_provenance(root: str) -> dict[str, Any]:
    """Require positive raw Flip metadata on the fresh-import model root."""
    entries = []
    for node in _owned_morph_network_nodes(root):
        if _attribute_value(node, "mmd_morph_type") != "flip":
            continue
        entries.append(
            {
                "index": _required_morph_int(node, "mmd_morph_index"),
                "name": _required_morph_string(node, "mmd_morph_name"),
                "offsets": _morph_json_attribute(node, "mmd_flip_morph_offsets_json"),
            }
        )
    if not entries:
        raise RuntimeError("fresh Flip import did not retain raw morph provenance")
    offset_count = sum(len(entry["offsets"]) for entry in entries)
    if offset_count <= 0:
        raise RuntimeError("fresh Flip import retained no raw offsets")
    return {
        "fresh_import_flip_morph_count": len(entries),
        "provenance_offset_count": offset_count,
        "provenance_morph_indices": [entry["index"] for entry in entries],
    }


def _owned_morph_network_nodes(root: str) -> list[str]:
    """Return the registry-owned morph networks without a scene-wide fallback."""
    from maya import cmds

    if not cmds.attributeQuery("mmd_model_registry", node=root, exists=True):
        raise RuntimeError(f"morph oracle requires a model registry on {root}")
    registries = cmds.listConnections(
        f"{root}.mmd_model_registry", source=True, destination=False, type="network"
    ) or []
    if len(registries) != 1:
        raise RuntimeError(f"morph oracle expected one model registry on {root}, found {len(registries)}")
    registry = str(registries[0])
    if not cmds.attributeQuery("morphMembers", node=registry, exists=True):
        return []
    members = cmds.listConnections(
        f"{registry}.morphMembers", source=True, destination=False, type="network"
    ) or []
    unique_members = sorted({str(member) for member in members})
    if len(unique_members) != len(members):
        raise RuntimeError(f"morph registry {registry} contains duplicate members")
    return unique_members


def _capture_morph_meshes(root: str) -> tuple[list[str], list[dict[str, Any]], list[list[float]]]:
    """Read direct object-space mesh coordinates and PMX source-index mappings."""
    from maya import cmds

    mesh_shapes = []
    descriptors = []
    base_vertices = []
    for transform in _find_mesh_transforms(root):
        for shape in cmds.listRelatives(transform, shapes=True, fullPath=True, type="mesh") or []:
            if cmds.getAttr(f"{shape}.intermediateObject"):
                continue
            vertices = _round_values(
                cmds.xform(
                    f"{shape}.vtx[*]", query=True, objectSpace=True, translation=True
                )
                or []
            )
            vertex_count = len(vertices) // 3
            if len(vertices) != vertex_count * 3:
                raise RuntimeError(f"mesh {shape} returned malformed object-space vertices")
            source_indices = None
            if cmds.attributeQuery("mmd_source_vertex_indices", node=shape, exists=True):
                source_indices = cmds.getAttr(f"{shape}.mmd_source_vertex_indices")
                if isinstance(source_indices, (list, tuple)) and len(source_indices) == 1:
                    source_indices = source_indices[0]
                if not isinstance(source_indices, (list, tuple)):
                    raise RuntimeError(f"mesh {shape} has malformed mmd_source_vertex_indices")
                try:
                    source_indices = [int(index) for index in source_indices]
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(f"mesh {shape} has non-integer source vertex indices") from exc
                if len(source_indices) != vertex_count:
                    raise RuntimeError(f"mesh {shape} source vertex index count differs from vertex count")
            mesh_shapes.append(str(shape))
            descriptors.append(
                {"vertex_count": vertex_count, "source_vertex_indices": source_indices}
            )
            base_vertices.append(vertices)
    return mesh_shapes, descriptors, base_vertices


def _required_morph_string(node: str, attribute: str) -> str:
    """Read one required string morph attribute without accepting a default."""
    from maya import cmds

    if not cmds.attributeQuery(attribute, node=node, exists=True):
        raise RuntimeError(f"morph node {node} is missing {attribute}")
    value = cmds.getAttr(f"{node}.{attribute}")
    if not isinstance(value, str):
        raise RuntimeError(f"morph node {node} has malformed {attribute}")
    return value


def _required_morph_int(node: str, attribute: str) -> int:
    """Read one required integer morph attribute without coercing malformed data."""
    from maya import cmds

    if not cmds.attributeQuery(attribute, node=node, exists=True):
        raise RuntimeError(f"morph node {node} is missing {attribute}")
    value = cmds.getAttr(f"{node}.{attribute}")
    if isinstance(value, bool):
        raise RuntimeError(f"morph node {node} has malformed {attribute}")
    try:
        integer = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"morph node {node} has malformed {attribute}") from exc
    if integer != value:
        raise RuntimeError(f"morph node {node} has non-integral {attribute}")
    return integer


def _capture_additional_uv_oracle(root: str) -> dict[str, Any]:
    """Capture imported PMX additional-UV channel count and source values."""
    from maya import cmds

    from mmd_tools.core.constants import (
        ATTR_MMD_ADDITIONAL_UVS_JSON,
        ATTR_MMD_PMX_ADDITIONAL_UV_COUNT,
    )

    channel_count = 0
    source_vertices: dict[int, list[list[float]]] = {}
    for transform in _find_mesh_transforms(root):
        shapes = cmds.listRelatives(transform, shapes=True, fullPath=True, type="mesh") or []
        shape = str(shapes[0]) if shapes else transform
        count = None
        raw_payload = None
        for node in (transform, shape):
            if count is None:
                count = _scalar_attribute_value(node, ATTR_MMD_PMX_ADDITIONAL_UV_COUNT, int)
            if raw_payload is None:
                raw_payload = _attribute_value(node, ATTR_MMD_ADDITIONAL_UVS_JSON)
        if count is None and raw_payload is None:
            continue
        if count is None or count < 1 or count > 4 or not isinstance(raw_payload, str):
            raise RuntimeError(f"additional UV storage on {transform} is malformed")
        try:
            payload = json.loads(raw_payload)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"additional UV storage on {transform} is malformed") from exc
        if not isinstance(payload, dict) or payload.get("channel_count") != count:
            raise RuntimeError(f"additional UV storage on {transform} has mismatched channel count")
        source_indices = payload.get("source_vertex_indices")
        values = payload.get("additional_uvs")
        source_count = payload.get("source_vertex_count")
        if (
            not isinstance(source_indices, list)
            or not isinstance(values, list)
            or len(source_indices) != len(values)
            or not isinstance(source_count, int)
            or source_count < len(values)
        ):
            raise RuntimeError(f"additional UV storage on {transform} is malformed")
        channel_count = max(channel_count, count)
        for source_index, channels in zip(source_indices, values):
            if not isinstance(source_index, int) or source_index < 0:
                raise RuntimeError(f"additional UV storage on {transform} has invalid source index")
            normalized = _normalize_morph_value(channels)
            if not isinstance(normalized, list) or len(normalized) != count:
                raise RuntimeError(f"additional UV storage on {transform} has invalid channel payload")
            previous = source_vertices.get(source_index)
            if previous is not None and previous != normalized:
                raise RuntimeError(f"additional UV storage differs for source vertex {source_index}")
            source_vertices[source_index] = normalized
    return {
        "channel_count": channel_count,
        "vertices": [source_vertices[index] for index in sorted(source_vertices)],
        "source_indices": sorted(source_vertices),
    }


def _capture_morph_oracle(root: str) -> dict[str, Any]:
    """Capture direct Maya morph payloads and one-weight controller results.

    This is deliberately independent of ``ExportSceneCollector``.  It reads
    blendShape provenance, registry-owned network nodes, and the model-owned
    controller after a fresh PMX import.
    """
    from maya import cmds

    mesh_shapes, vertex_meshes, base_vertices = _capture_morph_meshes(root)
    vertex_metadata: dict[int, dict[str, Any]] = {}
    for shape in mesh_shapes:
        for history_node in cmds.listHistory(shape, pruneDagObjects=True) or []:
            if cmds.nodeType(history_node) != "blendShape":
                continue
            raw_names = _attribute_value(history_node, "mmd_blendshape_morph_names_json")
            if raw_names is None:
                continue
            try:
                entries = json.loads(raw_names or "{}")
            except (TypeError, ValueError) as exc:
                raise RuntimeError(f"blendShape {history_node} has malformed morph metadata") from exc
            if not isinstance(entries, dict):
                raise RuntimeError(f"blendShape {history_node} morph metadata is not an object")
            for raw_weight_index, entry in entries.items():
                try:
                    weight_index = int(raw_weight_index)
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(f"blendShape {history_node} has invalid weight index") from exc
                if not isinstance(entry, dict):
                    raise RuntimeError(f"blendShape {history_node} has malformed morph entry")
                raw_index = entry.get("index", weight_index)
                name = entry.get("name")
                if not isinstance(name, str):
                    raise RuntimeError(f"blendShape {history_node} has malformed morph name")
                try:
                    morph_index = int(raw_index)
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(f"blendShape {history_node} has invalid PMX morph index") from exc
                metadata = {"index": morph_index, "name": name, "weight_index": weight_index}
                existing = vertex_metadata.get(morph_index)
                if existing is not None and existing != metadata:
                    raise RuntimeError(f"duplicate vertex morph metadata differs at index {morph_index}")
                vertex_metadata[morph_index] = metadata

    controllers = []
    if cmds.attributeQuery("mmd_morph_controller", node=root, exists=True):
        controllers = cmds.listConnections(
            f"{root}.mmd_morph_controller",
            source=True,
            destination=False,
            type="mmdMorphController",
        ) or []
    if not controllers and vertex_metadata:
        raise RuntimeError("vertex morph metadata exists without an mmdMorphController")
    if not controllers:
        return {
            "morphs": [],
            "additional_uvs": _capture_additional_uv_oracle(root),
            "vertex_meshes": [],
            "vertex_runtime_deltas": {},
            "controller_outputs": {},
            "unsupported_types": [],
        }
    if len(controllers) != 1:
        raise RuntimeError(f"expected one model morph controller, found {len(controllers)}")
    controller = str(controllers[0])
    input_indices = sorted(
        int(index) for index in (cmds.getAttr(f"{controller}.inputWeight", multiIndices=True) or [])
    )
    if input_indices != list(range(len(input_indices))):
        raise RuntimeError(f"morph controller input indices are not contiguous: {input_indices}")

    morphs_by_index: dict[int, dict[str, Any]] = {
        index: {"index": index, "name": metadata["name"], "type": "vertex"}
        for index, metadata in vertex_metadata.items()
    }
    unsupported_types = set()
    for node in _owned_morph_network_nodes(root):
        morph_type = _required_morph_string(node, "mmd_morph_type")
        if morph_type not in MORPH_OFFSET_ATTRIBUTES:
            unsupported_types.add(morph_type)
            continue
        morph_index = _required_morph_int(node, "mmd_morph_index")
        entry = {
            "index": morph_index,
            "name": _required_morph_string(node, "mmd_morph_name"),
            "name_en": _required_morph_string(node, "mmd_morph_name_en"),
            "type": morph_type,
            "panel": _required_morph_int(node, "mmd_morph_panel"),
            "offsets": _morph_json_attribute(node, MORPH_OFFSET_ATTRIBUTES[morph_type]),
        }
        existing = morphs_by_index.get(morph_index)
        if existing is not None and existing != entry:
            raise RuntimeError(f"morph metadata differs at index {morph_index}")
        morphs_by_index[morph_index] = entry

    def _read_weight(plug: str) -> float:
        value = cmds.getAttr(plug)
        if isinstance(value, (list, tuple)) and len(value) == 1:
            value = value[0]
        if value is None:
            raise RuntimeError(f"morph weight output is missing: {plug}")
        try:
            return round(float(value), 7)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"morph weight output is malformed: {plug}") from exc

    def _capture_vertices() -> list[list[float]]:
        return [
            _round_values(
                cmds.xform(
                    f"{shape}.vtx[*]", query=True, objectSpace=True, translation=True
                )
                or []
            )
            for shape in mesh_shapes
        ]

    original_weights = {
        index: _read_weight(f"{controller}.inputWeight[{index}]") for index in input_indices
    }
    runtime_deltas: dict[str, list[list[float]]] = {}
    controller_outputs: dict[str, list[float]] = {}
    try:
        for index in input_indices:
            cmds.setAttr(f"{controller}.inputWeight[{index}]", 0.0)
        for source_index in input_indices:
            cmds.setAttr(f"{controller}.inputWeight[{source_index}]", 1.0)
            controller_outputs[str(source_index)] = [
                _read_weight(f"{controller}.outputWeight[{output_index}]")
                for output_index in input_indices
            ]
            if morphs_by_index.get(source_index, {}).get("type") == "vertex":
                cmds.refresh(force=True)
                runtime_deltas[str(source_index)] = [
                    _round_values(
                        result - base for result, base in zip(result_vertices, base_mesh_vertices)
                    )
                    for result_vertices, base_mesh_vertices in zip(_capture_vertices(), base_vertices)
                ]
            cmds.setAttr(f"{controller}.inputWeight[{source_index}]", 0.0)
    finally:
        for index, value in original_weights.items():
            cmds.setAttr(f"{controller}.inputWeight[{index}]", value)
        for index in input_indices:
            cmds.getAttr(f"{controller}.outputWeight[{index}]")

    return {
        "morphs": [morphs_by_index[index] for index in sorted(morphs_by_index)],
        "additional_uvs": _capture_additional_uv_oracle(root),
        "vertex_meshes": vertex_meshes,
        "vertex_runtime_deltas": runtime_deltas,
        "controller_outputs": controller_outputs,
        "unsupported_types": sorted(unsupported_types),
    }


def _build_source_morph_oracle(source_model: Path) -> dict[str, Any]:
    """Build the fixture's morph expectations directly from its PMX payload."""
    from mmd_tools.core.pmx_data import PmxData

    pmx = PmxData().parse_file(str(source_model))
    entries = []
    group_offsets: dict[int, list[dict[str, Any]]] = {}
    vertex_offsets: dict[str, list[dict[str, Any]]] = {}
    supported_indices = set()
    unsupported = []
    for index, morph in enumerate(pmx.morphs):
        morph_type = MORPH_TYPE_NAMES.get(int(morph.morph_type))
        if morph_type is None or morph_type not in SUPPORTED_MORPH_TYPES:
            unsupported.append(str(int(morph.morph_type)))
            continue
        supported_indices.add(index)
        raw_offsets = _normalize_morph_value(list(getattr(morph, "offsets", []) or []))
        entry = {
            "index": index,
            "name": str(getattr(morph, "name", "") or ""),
            "type": morph_type,
        }
        if morph_type == "vertex":
            offsets = []
            for offset in raw_offsets:
                if not isinstance(offset, dict):
                    raise RuntimeError(f"vertex morph {index} has malformed offset")
                try:
                    position = offset["position_offset"]
                    offsets.append(
                        {
                            "vertex_index": int(offset["vertex_index"]),
                            "object_space_delta": _round_values(
                                [float(position[0]), float(position[1]), -float(position[2])]
                            ),
                        }
                    )
                except (KeyError, TypeError, ValueError, IndexError) as exc:
                    raise RuntimeError(f"vertex morph {index} has malformed offset") from exc
            vertex_offsets[str(index)] = offsets
        else:
            entry.update(
                name_en=str(getattr(morph, "name_english", "") or ""),
                panel=int(getattr(morph, "panel", 0)),
                offsets=raw_offsets,
            )
            if morph_type == "group":
                group_offsets[index] = raw_offsets
        entries.append(entry)
    if unsupported:
        raise RuntimeError(f"morph oracle fixture contains excluded types: {sorted(set(unsupported))}")
    missing_types = sorted(set(SUPPORTED_MORPH_TYPES) - {entry["type"] for entry in entries})
    if missing_types:
        raise RuntimeError(f"morph oracle fixture is missing supported types: {missing_types}")
    sdef_vertices = [
        index
        for index, vertex in enumerate(pmx.vertices)
        if int(getattr(vertex, "weight_transform_type", 0)) == 3
    ]
    if sdef_vertices:
        raise RuntimeError(f"morph oracle excludes SDEF vertices: {sdef_vertices}")

    controller_outputs = {
        str(source_index): [
            1.0 if output_index == source_index else 0.0
            for output_index in range(len(pmx.morphs))
        ]
        for source_index in range(len(pmx.morphs))
    }

    def expand(source_index: int, current_index: int, rate: float, path: set[int]) -> None:
        for offset in group_offsets.get(current_index, []):
            try:
                target_index = int(offset["morph_index"])
                contribution = rate * float(
                    offset["morph_rate"]
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(f"group morph {current_index} has malformed offset") from exc
            if target_index not in supported_indices or target_index in path:
                raise RuntimeError(
                    f"group morph {current_index} references excluded or cyclic target {target_index}"
                )
            controller_outputs[str(source_index)][target_index] += contribution
            if target_index in group_offsets:
                expand(source_index, target_index, contribution, path | {target_index})

    for source_index in group_offsets:
        expand(source_index, source_index, 1.0, {source_index})
    additional_uv_count = int(getattr(pmx.header, "additional_uv", 0) or 0)
    additional_uv_vertices = [
        _normalize_morph_value(
            [list(channel) for channel in (getattr(vertex, "additional_uvs", ()) or ())]
        )
        for vertex in pmx.vertices
    ]
    return {
        "morphs": entries,
        "additional_uvs": {
            "channel_count": additional_uv_count,
            "vertices": additional_uv_vertices,
            "source_indices": list(range(len(additional_uv_vertices))),
        },
        "vertex_offsets": vertex_offsets,
        "controller_outputs": {
            key: _round_values(value) for key, value in controller_outputs.items()
        },
        "unsupported_types": [],
        "source": str(source_model),
    }


def _expected_vertex_mesh_deltas(
    offsets: Any, vertex_meshes: Any
) -> list[list[float]]:
    """Map parser-owned PMX vertex offsets onto direct Maya mesh index maps."""
    if not isinstance(offsets, list) or not isinstance(vertex_meshes, list):
        raise ValueError("vertex morph runtime payload is malformed")
    mapped_indices: dict[int, list[tuple[int, int]]] = {}
    deltas = []
    for mesh_index, descriptor in enumerate(vertex_meshes):
        if not isinstance(descriptor, dict):
            raise ValueError(f"vertex mesh {mesh_index} is malformed")
        vertex_count = descriptor.get("vertex_count")
        if isinstance(vertex_count, bool) or not isinstance(vertex_count, int) or vertex_count < 0:
            raise ValueError(f"vertex mesh {mesh_index} has invalid vertex_count")
        source_indices = descriptor.get("source_vertex_indices")
        if source_indices is None:
            if len(vertex_meshes) != 1:
                raise ValueError("multiple meshes require mmd_source_vertex_indices")
            source_indices = list(range(vertex_count))
        if not isinstance(source_indices, list) or len(source_indices) != vertex_count:
            raise ValueError(f"vertex mesh {mesh_index} has invalid source vertex indices")
        deltas.append([0.0] * (vertex_count * 3))
        for local_index, source_index in enumerate(source_indices):
            if isinstance(source_index, bool) or not isinstance(source_index, int) or source_index < 0:
                raise ValueError(f"vertex mesh {mesh_index} has invalid source vertex index")
            mapped_indices.setdefault(source_index, []).append((mesh_index, local_index))
    for offset_index, offset in enumerate(offsets):
        if not isinstance(offset, dict):
            raise ValueError(f"vertex offset {offset_index} is malformed")
        source_index = offset.get("vertex_index")
        vector = offset.get("object_space_delta")
        if isinstance(source_index, bool) or not isinstance(source_index, int) or source_index < 0:
            raise ValueError(f"vertex offset {offset_index} has invalid vertex_index")
        if not isinstance(vector, list) or len(vector) != 3:
            raise ValueError(f"vertex offset {offset_index} has invalid object_space_delta")
        try:
            values = [float(value) for value in vector]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"vertex offset {offset_index} has invalid object_space_delta") from exc
        targets = mapped_indices.get(source_index)
        if not targets:
            raise ValueError(f"vertex offset {offset_index} references missing vertex {source_index}")
        for mesh_index, local_index in targets:
            start = local_index * 3
            for component, value in enumerate(values):
                deltas[mesh_index][start + component] += value
    return [_round_values(values) for values in deltas]


def _compare_morph_oracles(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> list[str]:
    """Compare PMX parser expectations with direct fresh-import Maya evidence."""
    failures: list[str] = []
    expected_unsupported = sorted(expected.get("unsupported_types", []) or [])
    actual_unsupported = sorted(actual.get("unsupported_types", []) or [])
    if expected_unsupported != actual_unsupported:
        failures.append(
            f"morph excluded types differ: expected {expected_unsupported}, actual {actual_unsupported}"
        )
    expected_morphs = expected.get("morphs")
    actual_morphs = actual.get("morphs")
    if not isinstance(expected_morphs, list) or not isinstance(actual_morphs, list):
        return failures + ["morph entries are missing or malformed"]
    if len(expected_morphs) != len(actual_morphs):
        failures.append(
            f"morphs count differs: expected {len(expected_morphs)}, actual {len(actual_morphs)}"
        )
    for index, (source, result) in enumerate(zip(expected_morphs, actual_morphs)):
        if not isinstance(source, dict) or not isinstance(result, dict):
            failures.append(f"morphs[{index}] is malformed")
            continue
        for field in ("index", "name", "type"):
            if source.get(field) != result.get(field):
                failures.append(
                    f"morphs[{index}].{field}: expected {source.get(field)!r}, "
                    f"actual {result.get(field)!r}"
                )
        if source.get("type") != "vertex":
            for field in ("name_en", "panel", "offsets"):
                if _normalize_morph_value(source.get(field)) != _normalize_morph_value(result.get(field)):
                    failures.append(
                        f"morphs[{index}].{field}: expected {source.get(field)!r}, "
                        f"actual {result.get(field)!r}"
                    )
    expected_additional_uvs = expected.get("additional_uvs")
    actual_additional_uvs = actual.get("additional_uvs")
    if _normalize_morph_value(expected_additional_uvs) != _normalize_morph_value(actual_additional_uvs):
        failures.append(
            "additional UV channel/value payload differs: "
            f"expected {expected_additional_uvs!r}, actual {actual_additional_uvs!r}"
        )
    expected_runtime = expected.get("vertex_offsets")
    actual_runtime = actual.get("vertex_runtime_deltas")
    actual_meshes = actual.get("vertex_meshes")
    if not isinstance(expected_runtime, dict) or not isinstance(actual_runtime, dict):
        failures.append("vertex morph runtime evidence is missing or malformed")
    else:
        if set(actual_runtime) != set(expected_runtime):
            failures.append(
                f"vertex morph runtime keys differ: expected {sorted(expected_runtime)}, "
                f"actual {sorted(actual_runtime)}"
            )
        for morph_index, source_offsets in expected_runtime.items():
            actual_mesh_deltas = actual_runtime.get(morph_index)
            if actual_mesh_deltas is None:
                continue
            try:
                expected_mesh_deltas = _expected_vertex_mesh_deltas(source_offsets, actual_meshes)
            except (TypeError, ValueError) as exc:
                failures.append(f"morphs[{morph_index}] parser runtime payload is invalid: {exc}")
                continue
            if not isinstance(actual_mesh_deltas, list) or len(expected_mesh_deltas) != len(actual_mesh_deltas):
                failures.append(f"morphs[{morph_index}] runtime mesh count differs")
                continue
            for mesh_index, (source_delta, result_delta) in enumerate(
                zip(expected_mesh_deltas, actual_mesh_deltas)
            ):
                if not isinstance(result_delta, list):
                    failures.append(f"morphs[{morph_index}] mesh[{mesh_index}] runtime is malformed")
                    continue
                difference = _compare_float_lists(source_delta, result_delta)
                if difference > FLOAT_TOLERANCE:
                    failures.append(
                        f"morphs[{morph_index}] mesh[{mesh_index}] vertices max error {difference:g}"
                    )
    expected_outputs = expected.get("controller_outputs")
    actual_outputs = actual.get("controller_outputs")
    if not isinstance(expected_outputs, dict) or not isinstance(actual_outputs, dict):
        failures.append("morph controller outputs are missing or malformed")
    else:
        if set(actual_outputs) != set(expected_outputs):
            failures.append(
                f"morph controller input keys differ: expected {sorted(expected_outputs)}, "
                f"actual {sorted(actual_outputs)}"
            )
        for source_index, source_values in expected_outputs.items():
            result_values = actual_outputs.get(source_index)
            if not isinstance(source_values, list) or not isinstance(result_values, list):
                failures.append(f"morph controller input {source_index} output is malformed")
                continue
            difference = _compare_float_lists(source_values, result_values)
            if difference > FLOAT_TOLERANCE:
                failures.append(
                    f"morph controller input {source_index} output max error {difference:g}"
                )
    return failures


def _compare_morph_payload_fields(
    expected: Mapping[str, Any], actual: Mapping[str, Any]
) -> list[str]:
    """Compare raw PMX morph and additional-UV fields across an export boundary."""
    failures: list[str] = []
    expected_morphs = expected.get("morphs")
    actual_morphs = actual.get("morphs")
    if not isinstance(expected_morphs, list) or not isinstance(actual_morphs, list):
        return ["exported morph payload is missing or malformed"]
    if len(expected_morphs) != len(actual_morphs):
        failures.append(
            f"exported morph count differs: expected {len(expected_morphs)}, actual {len(actual_morphs)}"
        )
    for index, (source, result) in enumerate(zip(expected_morphs, actual_morphs)):
        if _normalize_morph_value(source) != _normalize_morph_value(result):
            failures.append(f"exported morphs[{index}] payload differs")
    if _normalize_morph_value(expected.get("additional_uvs")) != _normalize_morph_value(
        actual.get("additional_uvs")
    ):
        failures.append("exported additional UV channel/value payload differs")
    return failures


def _long_names(node: str) -> list[str]:
    """Return full DAG names for a node without importing Maya at module load."""
    from maya import cmds

    return [str(value) for value in (cmds.ls(node, long=True) or [])]


def _bone_indices_below(root: str) -> dict[str, int]:
    """Build full/short joint-name to PMX index mappings for physics references."""
    from maya import cmds

    result: dict[str, int] = {}
    joints = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
    for joint in joints:
        index = _scalar_attribute_value(joint, "mmd_bone_index", int)
        if index is None:
            continue
        for name in (str(joint), *(str(value) for value in (cmds.ls(joint, long=True) or []))):
            result[name] = int(index)
        result[str(joint).rsplit("|", 1)[-1]] = int(index)
    return result


def _normalize_bone_vector(value: Any, field: str) -> list[float]:
    """Normalize one three-component bone semantic vector."""
    if isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], (list, tuple)):
        value = value[0]
    normalized = _round_values(value)
    if not isinstance(normalized, list) or len(normalized) != 3:
        raise RuntimeError(f"bone semantic field {field} is malformed")
    return [float(component) for component in normalized]


def _bone_semantic_payload(index: int, bone: Any) -> dict[str, Any]:
    """Return canonical source/export payload for one PMX bone."""
    from mmd_tools.core.pmx_data.bone import PmxBoneFlag

    flags = int(bone.bone_flag)
    payload: dict[str, Any] = {
        "index": index,
        "name": str(bone.name),
        "name_en": str(bone.name_english),
        "position": _normalize_bone_vector(bone.position, "position"),
        "parent_index": int(bone.parent_bone_index),
        "transform_layer": int(bone.transform_layer),
        "bone_flag": flags,
        "connect_bone_index": None,
        "connect_position_offset": None,
        "grant_parent_bone_index": None,
        "grant_rate": None,
        "axis_direction": None,
        "x_axis_direction": None,
        "z_axis_direction": None,
        "key_value": None,
        "ik_target_bone_index": None,
        "ik_loop_count": None,
        "ik_limit_angle": None,
        "ik_links": None,
    }
    if flags & int(PmxBoneFlag.CONNECT_BONE):
        payload["connect_bone_index"] = int(bone.connect_bone_index)
    else:
        payload["connect_position_offset"] = _normalize_bone_vector(
            bone.connect_position_offset, "connect_position_offset"
        )
    if flags & int(PmxBoneFlag.GRANT_PARENT_ROTATE | PmxBoneFlag.GRANT_PARENT_MOVE):
        payload["grant_parent_bone_index"] = int(bone.grant_parent_bone_index)
        payload["grant_rate"] = float(bone.grant_rate)
    if flags & int(PmxBoneFlag.AXIS_FIXED):
        payload["axis_direction"] = _normalize_bone_vector(bone.axis_direction, "axis_direction")
    if flags & int(PmxBoneFlag.LOCAL_AXIS):
        payload["x_axis_direction"] = _normalize_bone_vector(bone.x_axis_direction, "x_axis_direction")
        payload["z_axis_direction"] = _normalize_bone_vector(bone.z_axis_direction, "z_axis_direction")
    if flags & int(PmxBoneFlag.EXTERNAL_PARENT_DEFORM):
        payload["key_value"] = int(bone.key_value)
    if flags & int(PmxBoneFlag.IK):
        payload["ik_target_bone_index"] = int(bone.ik_target_bone_index)
        payload["ik_loop_count"] = int(bone.ik_loop_count)
        payload["ik_limit_angle"] = float(bone.ik_limit_angle)
        payload["ik_links"] = [
            {
                "bone": int(link.ik_bone_index),
                "limit_enabled": bool(link.angle_limit),
                "lower_limit": _normalize_bone_vector(link.limit_min, "lower_limit"),
                "upper_limit": _normalize_bone_vector(link.limit_max, "upper_limit"),
            }
            for link in bone.ik_links
        ]
    return payload


def _build_source_bone_semantics_oracle(source_model: Path) -> dict[str, Any]:
    """Build exact PMX bone semantics directly from the parser payload."""
    from mmd_tools.core.pmx_data import PmxData

    pmx = PmxData().parse_file(str(source_model))
    return {
        "bones": [_bone_semantic_payload(index, bone) for index, bone in enumerate(pmx.bones)],
        "source": str(source_model),
    }


def _required_bone_attribute(node: str, attribute: str) -> Any:
    """Read one required imported bone metadata attribute."""
    from maya import cmds

    if not cmds.attributeQuery(attribute, node=node, exists=True):
        raise RuntimeError(f"bone {node} is missing {attribute}")
    value = _attribute_value(node, attribute)
    if value is None:
        raise RuntimeError(f"bone {node} has empty {attribute}")
    return value


def _required_bone_vector(node: str, attribute: str) -> list[float]:
    """Read and validate one imported three-component vector attribute."""
    return _normalize_bone_vector(_required_bone_attribute(node, attribute), attribute)


def _capture_bone_semantics_oracle(root: str) -> dict[str, Any]:
    """Capture canonical imported bone semantics from Maya metadata attributes."""
    from maya import cmds
    from mmd_tools.core.pmx_data.bone import PmxBoneFlag

    joints_by_index: dict[int, str] = {}
    for joint in cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []:
        value = _scalar_attribute_value(str(joint), "mmd_bone_index", int)
        if value is None:
            continue
        if value in joints_by_index:
            raise RuntimeError(f"duplicate Maya bone index {value}")
        joints_by_index[value] = str(joint)
    if not joints_by_index:
        raise RuntimeError("fresh Maya import has no indexed bones")

    bones = []
    for index in sorted(joints_by_index):
        joint = joints_by_index[index]
        flags = int(_required_bone_attribute(joint, "mmd_bone_flags"))
        payload: dict[str, Any] = {
            "index": index,
            "name": str(_required_bone_attribute(joint, "mmd_bone_name")),
            "name_en": str(_required_bone_attribute(joint, "mmd_bone_name_en")),
            "position": _required_bone_vector(joint, "mmd_pmx_rest_position"),
            "parent_index": int(_required_bone_attribute(joint, "mmd_bone_parent_index")),
            "transform_layer": int(_required_bone_attribute(joint, "mmd_deform_layer")),
            "bone_flag": flags,
            "connect_bone_index": None,
            "connect_position_offset": None,
            "grant_parent_bone_index": None,
            "grant_rate": None,
            "axis_direction": None,
            "x_axis_direction": None,
            "z_axis_direction": None,
            "key_value": None,
            "ik_target_bone_index": None,
            "ik_loop_count": None,
            "ik_limit_angle": None,
            "ik_links": None,
        }
        if flags & int(PmxBoneFlag.CONNECT_BONE):
            payload["connect_bone_index"] = int(
                _required_bone_attribute(joint, "mmd_connect_bone_index")
            )
        else:
            payload["connect_position_offset"] = _required_bone_vector(joint, "mmd_bone_offset")
        if flags & int(PmxBoneFlag.GRANT_PARENT_ROTATE | PmxBoneFlag.GRANT_PARENT_MOVE):
            payload["grant_parent_bone_index"] = int(
                _required_bone_attribute(joint, "mmd_grant_parent_index")
            )
            payload["grant_rate"] = float(_required_bone_attribute(joint, "mmd_grant_rate"))
        if flags & int(PmxBoneFlag.AXIS_FIXED):
            payload["axis_direction"] = _required_bone_vector(joint, "mmd_axis_direction")
        if flags & int(PmxBoneFlag.LOCAL_AXIS):
            payload["x_axis_direction"] = _required_bone_vector(joint, "mmd_x_axis_direction")
            payload["z_axis_direction"] = _required_bone_vector(joint, "mmd_z_axis_direction")
        if flags & int(PmxBoneFlag.EXTERNAL_PARENT_DEFORM):
            payload["key_value"] = int(_required_bone_attribute(joint, "mmd_external_parent_key"))
        if flags & int(PmxBoneFlag.IK):
            payload["ik_target_bone_index"] = int(
                _required_bone_attribute(joint, "mmd_ik_target_index")
            )
            payload["ik_loop_count"] = int(_required_bone_attribute(joint, "mmd_ik_loop"))
            payload["ik_limit_angle"] = float(_required_bone_attribute(joint, "mmd_ik_limit_angle"))
            raw_links = _required_bone_attribute(joint, "mmd_ik_links")
            if not isinstance(raw_links, str):
                raise RuntimeError(f"bone {joint} has malformed mmd_ik_links")
            try:
                links = json.loads(raw_links)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"bone {joint} has malformed mmd_ik_links") from exc
            if not isinstance(links, list):
                raise RuntimeError(f"bone {joint} has malformed mmd_ik_links")
            payload["ik_links"] = links
        bones.append(payload)
    return {"bones": bones, "root": root}


def _capture_scene_oracle(root: str, frames: Iterable[int]) -> dict[str, Any]:
    """Capture mesh, pose, and model metadata from the current Maya scene."""
    from maya import cmds

    meshes = _find_mesh_transforms(root)
    mesh_oracle = []
    for transform in meshes:
        shapes = cmds.listRelatives(transform, shapes=True, fullPath=True, type="mesh") or []
        shape = str(shapes[0]) if shapes else transform
        raw_vertices = cmds.xform(f"{shape}.vtx[*]", query=True, worldSpace=True, translation=True) or []
        try:
            face_count = int(cmds.polyEvaluate(transform, face=True))
        except Exception:
            face_count = 0
        mesh_oracle.append(
            {
                "transform": transform,
                "vertex_count": len(raw_vertices) // 3,
                "face_count": face_count,
                "vertices": _round_values(raw_vertices),
                "vertex_digest": _digest_json(_round_values(raw_vertices)),
            }
        )

    joints = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
    indexed_joints = []
    for joint in joints:
        index = _attribute_value(joint, "mmd_bone_index")
        if index is None:
            continue
        matrix = cmds.xform(joint, query=True, worldSpace=True, matrix=True) or []
        indexed_joints.append(
            {
                "index": int(index),
                "name": _attribute_value(joint, "mmd_bone_name") or str(joint),
                "translation": _round_values(
                    cmds.xform(joint, query=True, worldSpace=True, translation=True) or []
                ),
                "matrix": _round_values(matrix),
            }
        )
    indexed_joints.sort(key=lambda item: (item["index"], item["name"]))

    pose_by_frame = {}
    for frame in frames:
        cmds.currentTime(frame, edit=True)
        cmds.refresh(force=True)
        pose_by_frame[str(int(frame))] = [
            {
                "index": item["index"],
                "name": item["name"],
                "translation": _round_values(
                    cmds.xform(
                        next(
                            joint
                            for joint in joints
                            if (_attribute_value(joint, "mmd_bone_index") == item["index"])
                        ),
                        query=True,
                        worldSpace=True,
                        translation=True,
                    )
                    or []
                ),
            }
            for item in indexed_joints
        ]

    metadata = {
        name: _attribute_value(root, name)
        for name in (
            "mmd_file_type",
            "mmd_file_version",
            "mmd_model_name",
            "mmd_model_name_en",
            "mmd_comment",
            "mmd_comment_en",
            "mmd_display_frames_json",
        )
    }
    return {
        "mesh": mesh_oracle,
        "materials": _capture_material_oracle(meshes),
        "morphs": _capture_morph_oracle(root),
        "pose": {"joint_count": len(indexed_joints), "joints": indexed_joints, "frames": pose_by_frame},
        "physics": _capture_physics_oracle(root),
        "metadata": metadata,
    }


def _compare_float_lists(expected: list[float], actual: list[float]) -> float:
    """Return the largest absolute difference between two flat vectors."""
    if len(expected) != len(actual):
        return float("inf")
    return max((abs(float(a) - float(b)) for a, b in zip(expected, actual)), default=0.0)


def _normalize_material_field(field: str, value: Any) -> Any:
    """Normalize equivalent unset texture-path representations before comparison."""
    if field in ("texture_path", "sphere_texture_path") and value == "":
        return None
    return value


def _compare_scene_oracles(
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
    *,
    pose: bool,
    mesh: bool = True,
    materials: bool = True,
    physics: bool = False,
    morphs: bool = False,
) -> list[str]:
    """Compare required scene oracle fields and return semantic failures."""
    failures: list[str] = []
    if mesh:
        expected_mesh = list(expected.get("mesh", []))
        actual_mesh = list(actual.get("mesh", []))
        if len(expected_mesh) != len(actual_mesh):
            failures.append(f"mesh count differs: expected {len(expected_mesh)}, actual {len(actual_mesh)}")
        for index, (source, result) in enumerate(zip(expected_mesh, actual_mesh)):
            for field in ("vertex_count", "face_count"):
                if source.get(field) != result.get(field):
                    failures.append(
                        f"mesh[{index}].{field}: expected {source.get(field)}, actual {result.get(field)}"
                    )
            difference = _compare_float_lists(source.get("vertices", []), result.get("vertices", []))
            if difference > FLOAT_TOLERANCE:
                failures.append(f"mesh[{index}].vertices max error {difference:g}")

    if materials:
        expected_materials = list(expected.get("materials", []))
        actual_materials = list(actual.get("materials", []))
        if len(expected_materials) != len(actual_materials):
            failures.append(
                f"material count differs: expected {len(expected_materials)}, actual {len(actual_materials)}"
            )
        for index, (source, result) in enumerate(zip(expected_materials, actual_materials)):
            for field in (
                "index",
                "name",
                "name_en",
                "memo",
                "texture_path",
                "sphere_texture_path",
                "draw_flags",
                "edge_flag",
                "sphere_mode",
                "sphere_texture_index",
                "texture_index",
                "toon_texture_index",
                "shared_toon_flag",
            ):
                expected_value = _normalize_material_field(field, source.get(field))
                actual_value = _normalize_material_field(field, result.get(field))
                if expected_value != actual_value:
                    failures.append(
                        f"material[{index}].{field}: expected {expected_value!r}, "
                        f"actual {actual_value!r}"
                    )
            for field in ("diffuse", "specular", "ambient", "edge_color"):
                difference = _compare_float_lists(source.get(field, []), result.get(field, []))
                if difference > FLOAT_TOLERANCE:
                    failures.append(f"material[{index}].{field} max error {difference:g}")
            expected_edge_size = source.get("edge_size")
            actual_edge_size = result.get("edge_size")
            if expected_edge_size is None or actual_edge_size is None:
                if expected_edge_size != actual_edge_size:
                    failures.append(
                        f"material[{index}].edge_size: expected {expected_edge_size!r}, "
                        f"actual {actual_edge_size!r}"
                    )
            elif abs(float(expected_edge_size) - float(actual_edge_size)) > FLOAT_TOLERANCE:
                failures.append(
                    f"material[{index}].edge_size max error "
                    f"{abs(float(expected_edge_size) - float(actual_edge_size)):g}"
                )
            expected_shininess = source.get("shininess")
            actual_shininess = result.get("shininess")
            if expected_shininess is None or actual_shininess is None:
                if expected_shininess != actual_shininess:
                    failures.append(
                        f"material[{index}].shininess: expected {expected_shininess!r}, "
                        f"actual {actual_shininess!r}"
                    )
            elif abs(float(expected_shininess) - float(actual_shininess)) > FLOAT_TOLERANCE:
                failures.append(
                    f"material[{index}].shininess max error "
                    f"{abs(float(expected_shininess) - float(actual_shininess)):g}"
                )

    expected_metadata = expected.get("metadata", {})
    actual_metadata = actual.get("metadata", {})
    for name in ("mmd_file_type", "mmd_model_name"):
        if expected_metadata.get(name) != actual_metadata.get(name):
            failures.append(
                f"metadata.{name}: expected {expected_metadata.get(name)!r}, actual {actual_metadata.get(name)!r}"
            )

    if pose:
        expected_pose = expected.get("pose", {})
        actual_pose = actual.get("pose", {})
        if expected_pose.get("joint_count") != actual_pose.get("joint_count"):
            failures.append(
                f"pose.joint_count: expected {expected_pose.get('joint_count')}, actual {actual_pose.get('joint_count')}"
            )
        expected_frames = expected_pose.get("frames", {})
        actual_frames = actual_pose.get("frames", {})
        for frame, expected_joints in expected_frames.items():
            actual_joints = actual_frames.get(frame)
            if actual_joints is None:
                failures.append(f"pose frame {frame} is missing")
                continue
            if len(expected_joints) != len(actual_joints):
                failures.append(f"pose frame {frame} joint count differs")
                continue
            for expected_joint, actual_joint in zip(expected_joints, actual_joints):
                if expected_joint["name"] != actual_joint["name"]:
                    failures.append(
                        f"pose frame {frame} bone name differs: {expected_joint['name']!r} vs {actual_joint['name']!r}"
                    )
                difference = _compare_float_lists(
                    expected_joint.get("translation", []), actual_joint.get("translation", [])
                )
                if difference > FLOAT_TOLERANCE:
                    failures.append(f"pose frame {frame} bone {expected_joint['name']} max error {difference:g}")

    if physics:
        expected_physics = expected.get("physics", {})
        actual_physics = actual.get("physics", {})
        scalar_fields = {
            "rigid_bodies": (
                "pmx_index",
                "name",
                "name_en",
                "related_bone_index",
                "group",
                "collision_mask",
                "shape_type",
                "physics_mode",
            ),
            "joints": (
                "pmx_index",
                "name",
                "name_en",
                "joint_type",
                "rigid_body_a_index",
                "rigid_body_b_index",
            ),
        }
        vector_fields = {
            "rigid_bodies": ("size", "position", "rotation"),
            "joints": (
                "position",
                "rotation",
                "translation_limit_min",
                "translation_limit_max",
                "rotation_limit_min",
                "rotation_limit_max",
                "spring_translation",
                "spring_rotation",
            ),
        }
        float_fields = {
            "rigid_bodies": (
                "mass",
                "velocity_attenuation",
                "rotation_attenuation",
                "elasticity",
                "friction",
            ),
            "joints": (),
        }
        for section in ("rigid_bodies", "joints"):
            expected_items = list(expected_physics.get(section, []))
            actual_items = list(actual_physics.get(section, []))
            if len(expected_items) != len(actual_items):
                failures.append(
                    f"physics.{section} count differs: expected {len(expected_items)}, "
                    f"actual {len(actual_items)}"
                )
            for index, (source, result) in enumerate(zip(expected_items, actual_items)):
                for field in scalar_fields[section]:
                    if source.get(field) != result.get(field):
                        failures.append(
                            f"physics.{section}[{index}].{field}: expected {source.get(field)!r}, "
                            f"actual {result.get(field)!r}"
                        )
                for field in float_fields[section]:
                    source_value = source.get(field)
                    result_value = result.get(field)
                    if source_value is None or result_value is None:
                        if source_value != result_value:
                            failures.append(
                                f"physics.{section}[{index}].{field}: expected {source_value!r}, "
                                f"actual {result_value!r}"
                            )
                    elif abs(float(source_value) - float(result_value)) > FLOAT_TOLERANCE:
                        failures.append(
                            f"physics.{section}[{index}].{field} max error "
                            f"{abs(float(source_value) - float(result_value)):g}"
                        )
                for field in vector_fields[section]:
                    difference = _compare_float_lists(source.get(field, []), result.get(field, []))
                    if difference > FLOAT_TOLERANCE:
                        failures.append(
                            f"physics.{section}[{index}].{field} max error {difference:g}"
                        )
    if morphs:
        failures.extend(
            _compare_morph_oracles(
                expected.get("morphs", {}),
                actual.get("morphs", {}),
            )
        )
    return failures


def _edit_first_material_with_material_tab(root: str) -> dict[str, Any]:
    """Edit one imported PMX material through the real MaterialTab presenter."""
    from mmd_tools.ui.presenters.material_presenter import MaterialPresenter
    from mmd_tools.ui.qt_compat import QApplication
    from mmd_tools.ui.tabs.material_tab import MaterialTab
    from mmd_tools.ui.application_state import ApplicationState

    application = QApplication.instance()
    if application is None:
        application = QApplication([])

    view = MaterialTab()
    state = ApplicationState()
    presenter = MaterialPresenter(view, state)
    state.current_model_root = root
    presenter.load_materials()
    if view.material_list.count() < 1:
        raise RuntimeError("MaterialTab found no imported PMX materials")

    view.material_list.setCurrentRow(0)
    application.processEvents()
    item = view.material_list.currentItem()
    if not presenter.current_material and item is not None:
        presenter.on_material_selected(item, None)
    if not presenter.current_material:
        raise RuntimeError("MaterialTab did not select the first imported PMX material")

    before = float(view.specular_coefficient_spin.value())
    maximum = float(view.specular_coefficient_spin.maximum())
    minimum = float(view.specular_coefficient_spin.minimum())
    delta = 0.01 if before + 0.01 <= maximum else -0.01
    after_target = max(minimum, min(maximum, before + delta))
    view.specular_coefficient_spin.setValue(after_target)
    application.processEvents()
    after = float(view.specular_coefficient_spin.value())
    if abs(after - before) <= FLOAT_TOLERANCE:
        raise RuntimeError("MaterialTab edit was clamped and did not change the specular coefficient")

    presenter.apply_changes()
    application.processEvents()
    authored = _scalar_attribute_value(presenter.current_material, "shininess", float)
    if authored is None or abs(authored - after) > FLOAT_TOLERANCE:
        raise RuntimeError(
            f"MaterialTab Apply did not persist shininess: expected {after:g}, actual {authored!r}"
        )
    return {
        "material": presenter.current_material,
        "field": "shininess",
        "before": round(before, 7),
        "after": round(after, 7),
    }


def _import_options() -> dict[str, Any]:
    """Return deterministic, shader-light Maya import options for the probe."""
    return {
        "scale": 1.0,
        "import_physics": True,
        "setup_rig": False,
        "setup_bone_orientation": False,
        "create_mmd_control_rig": False,
        "create_mmd_shaders": False,
        "use_cpp_fast_load": False,
        "use_native_pmx_parse": False,
        "require_native_pmx_parse": False,
    }


def _fresh_import(path: Path, *, target_model: str | None = None, pmx_path: Path | None = None) -> str:
    """Create a new scene and import one model or VMD fixture."""
    from maya import cmds
    from mmd_tools.io.mmd_importer import import_mmd_file

    cmds.file(new=True, force=True)
    options = _import_options()
    if target_model is not None:
        options["target_model"] = target_model
    if pmx_path is not None:
        options["pmx_path"] = str(pmx_path)
    root = import_mmd_file(str(path), options=options)
    if not root:
        raise RuntimeError(f"Maya import returned no root: {path}")
    return str(root)


def _run_model_case(
    export_format: str,
    source_model: Path,
    out_dir: Path,
    *,
    compare_physics: bool = False,
    compare_morphs: bool = False,
) -> dict[str, Any]:
    """Export one model format, fresh-import it, and compare scene oracles."""
    from mmd_tools.core.pmx_data import PmxData
    from mmd_tools.services.export_workflow_service import (
        ExportWorkflowRequest,
        ExportWorkflowService,
    )

    output = out_dir / f"model.{export_format}"
    report_dir = out_dir / "report"
    source_root = _fresh_import(source_model)
    source_oracle = _capture_scene_oracle(source_root, (0,))
    source_morph_oracle = None
    if compare_morphs:
        source_morph_oracle = _build_source_morph_oracle(source_model)
        source_morph_failures = _compare_morph_oracles(
            source_morph_oracle,
            source_oracle["morphs"],
        )
        if source_morph_failures:
            raise AssertionError("source morph import oracle failed: " + "; ".join(source_morph_failures))
        source_oracle["morphs"] = source_morph_oracle
    oracle_names = ["materials", "mesh", "pose", "metadata"]
    if compare_physics:
        oracle_names.append("physics")
    if compare_morphs:
        oracle_names.append("morphs")
    request = ExportWorkflowRequest(
        str(output),
        {
            "export_format": export_format,
            "require_target": True,
            "target_model": source_root,
            "target_identity": source_root,
            "validation_report_dir": str(report_dir),
            "validation_report_evidence": {
                "gate": "V070-EXPORT-RELEASE-GATE-1",
                "fixture": source_model.name,
                "fresh_import": True,
                "oracles": oracle_names,
            },
        },
    )
    workflow = ExportWorkflowService()
    if export_format == "pmd":
        validation = workflow.validate(request)
        policy_codes = [issue.code for issue in validation.report.issues]
        if validation.state != "Blocked" or policy_codes != ["PMD_EXPORT_POLICY_REJECT"]:
            raise AssertionError(
                f"PMD policy probe expected one blocking rejection, got "
                f"state={validation.state!r}, issues={policy_codes!r}"
            )
        report_dir.mkdir(parents=True, exist_ok=True)
        evidence = request.options["validation_report_evidence"]
        validation.report.write_canonical_json(
            report_dir / "report.json",
            target_identity=source_root,
            provenance="ExportWorkflowService",
            evidence=evidence,
        )
        validation.report.write_markdown(
            report_dir / "report.md",
            target_identity=source_root,
            provenance="ExportWorkflowService",
            evidence=evidence,
        )
        if output.exists():
            raise AssertionError(f"PMD policy rejection created an output: {output}")
        return {
            "status": "policy-reject",
            "format": export_format,
            "source": str(source_model),
            "output": None,
            "report_json": str(report_dir / "report.json"),
            "report_md": str(report_dir / "report.md"),
            "policy_code": "PMD_EXPORT_POLICY_REJECT",
            "import_oracles": {
                "mesh": source_oracle["mesh"],
                "materials": source_oracle["materials"],
                "morphs": source_oracle["morphs"],
                "pose": source_oracle["pose"],
                "physics": source_oracle["physics"],
                "metadata": source_oracle["metadata"],
            },
            "collection": {
                "collector": "ExportWorkflowService validation -> PMD policy",
                "target_model": source_root,
                "source_fresh_import": True,
                "export_writer_called": False,
            },
        }

    result = workflow.execute(request)
    if not result.succeeded:
        raise RuntimeError(f"{export_format} export failed: {result.error or result.report}")
    parsed = PmxData().parse_file(str(output))
    exported_morph_oracle = None
    if compare_morphs:
        exported_morph_oracle = _build_source_morph_oracle(output)
        exported_morph_failures = _compare_morph_payload_fields(
            source_morph_oracle,
            exported_morph_oracle,
        )
        if exported_morph_failures:
            raise AssertionError(
                "exported morph payload oracle failed: " + "; ".join(exported_morph_failures)
            )
    result_root = _fresh_import(output)
    result_oracle = _capture_scene_oracle(result_root, (0,))
    failures = _compare_scene_oracles(
        source_oracle,
        result_oracle,
        pose=True,
        physics=compare_physics,
        morphs=compare_morphs,
    )
    if failures:
        raise AssertionError("; ".join(failures))
    return {
        "status": "pass",
        "format": export_format,
        "source": str(source_model),
        "output": str(output),
        "report_json": str(report_dir / "report.json"),
        "report_md": str(report_dir / "report.md"),
        "parsed_counts": {
            "vertices": len(parsed.vertices),
            "faces": len(parsed.faces),
            "materials": len(parsed.materials),
            "bones": len(parsed.bones),
            "morphs": len(parsed.morphs),
            "rigid_bodies": len(parsed.rigid_bodies),
            "joints": len(parsed.joints),
        },
        "oracles": {
            "mesh": result_oracle["mesh"],
            "materials": result_oracle["materials"],
            "morphs": result_oracle["morphs"],
            "pose": result_oracle["pose"],
            "physics": result_oracle["physics"],
            "metadata": result_oracle["metadata"],
        },
        "morph_oracle": {
            "source": source_morph_oracle,
            "exported_file": exported_morph_oracle,
            "fresh_import": result_oracle["morphs"],
            "comparison": {
                "status": "pass",
                "checked_types": list(SUPPORTED_MORPH_TYPES),
                "boundaries": ["source_import", "exported_pmx", "fresh_import"],
                "fixture": source_model.name,
            },
        }
        if compare_morphs
        else None,
        "morph_coverage": {
            "verified_types": list(SUPPORTED_MORPH_TYPES),
            "verified_fields": {
                "vertex": [
                    "index",
                    "name",
                    "weight_1_object_space_deltas",
                    "additional_uv_channel_count",
                    "additional_uv_per_vertex_values",
                ],
                "bone": ["index", "name", "name_en", "panel", "raw_offsets"],
                "uv": ["index", "name", "name_en", "panel", "uv_offsets"],
                "additional_uv1": ["index", "name", "name_en", "panel", "additional_uv_offsets"],
                "additional_uv2": ["index", "name", "name_en", "panel", "additional_uv_offsets"],
                "additional_uv3": ["index", "name", "name_en", "panel", "additional_uv_offsets"],
                "additional_uv4": ["index", "name", "name_en", "panel", "additional_uv_offsets"],
                "material": ["index", "name", "name_en", "panel", "offsets"],
                "group": ["index", "name", "name_en", "panel", "offsets", "controller_outputs"],
            },
            "excluded_boundaries": list(MORPH_ORACLE_EXCLUSIONS),
            "source_oracle": "PMX parser payload",
            "scene_oracle": "direct Maya DAG/network attributes and controller outputs",
            "visual_parity_claimed": False,
        }
        if compare_morphs
        else None,
        "collection": {
            "collector": "ExportWorkflowService -> ExportSceneCollector.collect",
            "target_model": source_root,
            "source_fresh_import": True,
        },
    }


def _compare_bone_semantics(
    expected: Mapping[str, Any], actual: Mapping[str, Any], boundary: str
) -> list[str]:
    """Compare canonical bone semantics and identify the first differing field."""
    expected_bones = expected.get("bones")
    actual_bones = actual.get("bones")
    if not isinstance(expected_bones, list) or not isinstance(actual_bones, list):
        return [f"{boundary}.bones missing or malformed"]
    failures: list[str] = []
    if len(expected_bones) != len(actual_bones):
        failures.append(
            f"{boundary}.bone count differs: expected {len(expected_bones)}, actual {len(actual_bones)}"
        )
    for index, (source, result) in enumerate(zip(expected_bones, actual_bones)):
        if _normalize_morph_value(source) == _normalize_morph_value(result):
            continue
        if not isinstance(source, dict) or not isinstance(result, dict):
            failures.append(f"{boundary}.bones[{index}] malformed")
            continue
        for field in source:
            if _normalize_morph_value(source.get(field)) != _normalize_morph_value(result.get(field)):
                failures.append(
                    f"{boundary}.bones[{index}].{field} differs: "
                    f"expected {source.get(field)!r}, actual {result.get(field)!r}"
                )
    return failures


def _run_bone_semantics_case(source_model: Path, out_dir: Path) -> dict[str, Any]:
    """Roundtrip the PMX bone semantic subset through Maya and the exporter."""
    from mmd_tools.services.export_workflow_service import (
        ExportWorkflowRequest,
        ExportWorkflowService,
    )

    probe_model = _write_bone_semantics_probe_fixture(
        out_dir / "fixtures" / "bone_semantics_input.pmx",
        source_model,
    )
    source_parser = _build_source_bone_semantics_oracle(probe_model)
    source_root = _fresh_import(probe_model)
    source_import = _capture_bone_semantics_oracle(source_root)
    failures = _compare_bone_semantics(source_parser, source_import, "source_import")
    if failures:
        raise AssertionError("source bone semantics oracle failed: " + "; ".join(failures))

    output = out_dir / "model.pmx"
    report_dir = out_dir / "report"
    request = ExportWorkflowRequest(
        str(output),
        {
            "export_format": "pmx",
            "require_target": True,
            "target_model": source_root,
            "target_identity": source_root,
            "validation_report_dir": str(report_dir),
            "validation_report_evidence": {
                "gate": "V070-EXPORT-RELEASE-GATE-1",
                "fixture": probe_model.name,
                "fresh_import": True,
                "oracles": ["bone_semantics", "source_import", "exported_pmx", "fresh_import"],
            },
        },
    )
    result = ExportWorkflowService().execute(request)
    if not result.succeeded:
        raise RuntimeError(f"PMX bone semantics export failed: {result.error or result.report}")

    exported_parser = _build_source_bone_semantics_oracle(output)
    failures = _compare_bone_semantics(source_parser, exported_parser, "exported_pmx")
    if failures:
        raise AssertionError("exported bone semantics oracle failed: " + "; ".join(failures))
    fresh_root = _fresh_import(output)
    fresh_import = _capture_bone_semantics_oracle(fresh_root)
    failures = _compare_bone_semantics(source_parser, fresh_import, "fresh_import")
    if failures:
        raise AssertionError("fresh bone semantics oracle failed: " + "; ".join(failures))

    return {
        "status": "pass",
        "format": "pmx_bone_semantics",
        "source": str(probe_model),
        "output": str(output),
        "report_json": str(report_dir / "report.json"),
        "report_md": str(report_dir / "report.md"),
        "parsed_counts": {"bones": len(exported_parser["bones"])},
        "bone_semantics": {
            "source": source_parser,
            "source_import": source_import,
            "exported_file": exported_parser,
            "fresh_import": fresh_import,
            "comparison": {
                "status": "pass",
                "boundaries": ["source_import", "exported_pmx", "fresh_import"],
            },
        },
        "bone_semantics_coverage": {
            "verified_fields": [
                "index",
                "name",
                "name_en",
                "position",
                "parent_index",
                "transform_layer",
                "bone_flag",
                "connect_bone_index",
                "connect_position_offset",
                "grant_parent_bone_index",
                "grant_rate",
                "axis_direction",
                "x_axis_direction",
                "z_axis_direction",
                "key_value",
                "ik_target_bone_index",
                "ik_loop_count",
                "ik_limit_angle",
                "ik_links",
            ],
            "source_oracle": "PMX parser payload",
            "maya_oracle": "direct Maya bone metadata attributes",
        },
    }


def _run_soft_body_policy_case(out_dir: Path) -> dict[str, Any]:
    """Prove PMX 2.1 soft-body provenance reaches the public export rejection."""
    from mmd_tools.core.constants import ATTR_MMD_PMX_SOFT_BODY_COUNT
    from mmd_tools.services.export_workflow_service import (
        ExportWorkflowRequest,
        ExportWorkflowService,
    )

    source_model = _write_soft_body_probe_fixture(
        out_dir / "fixtures" / "soft_body_policy_input.pmx"
    )
    output = out_dir / "model.pmx"
    report_dir = out_dir / "report"
    source_root = _fresh_import(source_model)
    source_soft_body_count = _scalar_attribute_value(
        source_root, ATTR_MMD_PMX_SOFT_BODY_COUNT, int
    )
    request = ExportWorkflowRequest(
        str(output),
        {
            "export_format": "pmx",
            "require_target": True,
            "target_model": source_root,
            "target_identity": source_root,
            "validation_report_dir": str(report_dir),
            "validation_report_evidence": {
                "gate": "V070-EXPORT-RELEASE-GATE-1",
                "fixture": source_model.name,
                "fresh_import": True,
                "oracles": ["soft_body_provenance", "policy_reject"],
            },
        },
    )
    validation = ExportWorkflowService().validate(request)
    policy_codes = [issue.code for issue in validation.report.issues]
    if validation.state != "Blocked" or "PMX_SOFT_BODIES_UNSUPPORTED" not in policy_codes:
        raise AssertionError(
            "PMX soft-body policy probe expected a blocking rejection, "
            f"got state={validation.state!r}, issues={policy_codes!r}"
        )
    report_dir.mkdir(parents=True, exist_ok=True)
    evidence = request.options["validation_report_evidence"]
    validation.report.write_canonical_json(
        report_dir / "report.json",
        target_identity=source_root,
        provenance="ExportWorkflowService validation",
        evidence=evidence,
    )
    validation.report.write_markdown(
        report_dir / "report.md",
        target_identity=source_root,
        provenance="ExportWorkflowService validation",
        evidence=evidence,
    )
    if output.exists():
        raise AssertionError(f"PMX soft-body policy rejection created an output: {output}")
    return {
        "status": "policy-reject",
        "format": "pmx_soft_body",
        "source": str(source_model),
        "output": None,
        "report_json": str(report_dir / "report.json"),
        "report_md": str(report_dir / "report.md"),
        "policy_code": "PMX_SOFT_BODIES_UNSUPPORTED",
        "import_oracles": {"soft_body_count": source_soft_body_count},
        "collection": {
            "collector": "ExportWorkflowService validation -> soft-body policy",
            "target_model": source_root,
            "source_fresh_import": True,
            "export_writer_called": False,
        },
    }


def _run_sdef_policy_case(out_dir: Path) -> dict[str, Any]:
    """Prove fresh-import SDEF provenance reaches the public export rejection."""
    from mmd_tools.core.pmx_data import PmxData
    from mmd_tools.services.export_workflow_service import (
        ExportWorkflowRequest,
        ExportWorkflowService,
    )

    source_model = _write_sdef_probe_fixture(
        out_dir / "fixtures" / "sdef_policy_input.pmx"
    )
    source_pmx = PmxData().parse_file(str(source_model))
    source_sdef_count = sum(
        int(getattr(vertex, "weight_transform_type", 0)) == 3
        for vertex in source_pmx.vertices
    )
    if source_sdef_count <= 0:
        raise RuntimeError("SDEF probe fixture did not contain an SDEF vertex")

    output = out_dir / "model.pmx"
    report_dir = out_dir / "report"
    source_root = _fresh_import(source_model)
    import_oracles = {
        "source_sdef_vertex_count": source_sdef_count,
        **_capture_sdef_import_provenance(source_root),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    sentinel = b"pre-existing-sdef-policy-target"
    output.write_bytes(sentinel)
    before = output.read_bytes()
    request = ExportWorkflowRequest(
        str(output),
        {
            "export_format": "pmx",
            "require_target": True,
            "target_model": source_root,
            "target_identity": source_root,
            "validation_report_dir": str(report_dir),
            "validation_report_evidence": {
                "gate": "V070-EXPORT-RELEASE-GATE-1",
                "fixture": source_model.name,
                "fresh_import": True,
                "oracles": ["sdef_provenance", "policy_reject"],
            },
        },
    )
    validation = ExportWorkflowService().validate(request)
    policy_codes = [issue.code for issue in validation.report.issues]
    if validation.state != "Blocked" or "PMX_VERTEX_SDEF_UNSUPPORTED" not in policy_codes:
        raise AssertionError(
            "PMX SDEF policy probe expected a blocking rejection, "
            f"got state={validation.state!r}, issues={policy_codes!r}"
        )
    payload = validation.payload
    collected_sdef_count = sum(
        int(vertex.get("weight_transform_type", 0)) == 3
        for vertex in payload.get("vertices", [])
    ) if isinstance(payload, dict) else 0
    if collected_sdef_count <= 0:
        raise AssertionError("SDEF collector payload did not retain a positive vertex count")
    import_oracles["collected_sdef_vertex_count"] = collected_sdef_count

    report_dir.mkdir(parents=True, exist_ok=True)
    evidence = request.options["validation_report_evidence"]
    validation.report.write_canonical_json(
        report_dir / "report.json",
        target_identity=source_root,
        provenance="ExportWorkflowService validation",
        evidence=evidence,
    )
    validation.report.write_markdown(
        report_dir / "report.md",
        target_identity=source_root,
        provenance="ExportWorkflowService validation",
        evidence=evidence,
    )
    after = output.read_bytes() if output.exists() else None
    output_safety = {
        "target_existed_before": True,
        "target_exists_after": output.exists(),
        "created": False,
        "overwritten": after != before,
        "preserved": after == before,
        "writer_called": False,
    }
    if not output_safety["target_exists_after"] or not output_safety["preserved"]:
        raise AssertionError(f"SDEF policy rejection changed output target: {output}")
    return {
        "status": "policy-reject",
        "format": "pmx_sdef",
        "source": str(source_model),
        "output": None,
        "output_target": str(output),
        "report_json": str(report_dir / "report.json"),
        "report_md": str(report_dir / "report.md"),
        "policy_code": "PMX_VERTEX_SDEF_UNSUPPORTED",
        "import_oracles": import_oracles,
        "collection": {
            "collector": "ExportWorkflowService validation -> SDEF policy",
            "target_model": source_root,
            "source_fresh_import": True,
            "export_writer_called": False,
        },
        "output_safety": output_safety,
    }


def _run_impulse_policy_case(out_dir: Path) -> dict[str, Any]:
    """Prove fresh-import PMX 2.1 Impulse metadata reaches policy rejection."""
    from mmd_tools.core.pmx_data import PmxData
    from mmd_tools.core.pmx_data.morph import PmxMorphType
    from mmd_tools.services.export_workflow_service import (
        ExportWorkflowRequest,
        ExportWorkflowService,
    )

    source_model = _write_impulse_probe_fixture(
        out_dir / "fixtures" / "impulse_policy_input.pmx"
    )
    source_pmx = PmxData().parse_file(str(source_model))
    source_impulse_count = sum(
        int(getattr(morph, "morph_type", -1)) == int(PmxMorphType.ImpulseMorph)
        for morph in source_pmx.morphs
    )
    if source_impulse_count <= 0:
        raise RuntimeError("Impulse probe fixture did not contain an Impulse morph")

    output = out_dir / "model.pmx"
    report_dir = out_dir / "report"
    source_root = _fresh_import(source_model)
    import_oracles = {
        "source_impulse_morph_count": source_impulse_count,
        **_capture_impulse_import_provenance(source_root),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    sentinel = b"pre-existing-impulse-policy-target"
    output.write_bytes(sentinel)
    before = output.read_bytes()
    request = ExportWorkflowRequest(
        str(output),
        {
            "export_format": "pmx",
            "require_target": True,
            "target_model": source_root,
            "target_identity": source_root,
            "validation_report_dir": str(report_dir),
            "validation_report_evidence": {
                "gate": "V070-EXPORT-RELEASE-GATE-1",
                "fixture": source_model.name,
                "fresh_import": True,
                "oracles": ["impulse_provenance", "policy_reject"],
            },
        },
    )
    validation = ExportWorkflowService().validate(request)
    policy_codes = [issue.code for issue in validation.report.issues]
    if validation.state != "Blocked" or "MORPH_TYPE_UNSUPPORTED" not in policy_codes:
        raise AssertionError(
            "PMX Impulse policy probe expected a blocking rejection, "
            f"got state={validation.state!r}, issues={policy_codes!r}"
        )
    payload = validation.payload
    collected_impulse_count = sum(
        morph.get("type") == "impulse"
        for morph in payload.get("morphs", [])
    ) if isinstance(payload, dict) else 0
    if collected_impulse_count <= 0:
        raise AssertionError("Impulse collector payload did not retain a positive morph count")
    import_oracles["collected_impulse_morph_count"] = collected_impulse_count

    report_dir.mkdir(parents=True, exist_ok=True)
    evidence = request.options["validation_report_evidence"]
    validation.report.write_canonical_json(
        report_dir / "report.json",
        target_identity=source_root,
        provenance="ExportWorkflowService validation",
        evidence=evidence,
    )
    validation.report.write_markdown(
        report_dir / "report.md",
        target_identity=source_root,
        provenance="ExportWorkflowService validation",
        evidence=evidence,
    )
    after = output.read_bytes() if output.exists() else None
    output_safety = {
        "target_existed_before": True,
        "target_exists_after": output.exists(),
        "created": False,
        "overwritten": after != before,
        "preserved": after == before,
        "writer_called": False,
    }
    if not output_safety["target_exists_after"] or not output_safety["preserved"]:
        raise AssertionError(f"Impulse policy rejection changed output target: {output}")
    return {
        "status": "policy-reject",
        "format": "pmx_impulse",
        "source": str(source_model),
        "output": None,
        "output_target": str(output),
        "report_json": str(report_dir / "report.json"),
        "report_md": str(report_dir / "report.md"),
        "policy_code": "MORPH_TYPE_UNSUPPORTED",
        "import_oracles": import_oracles,
        "collection": {
            "collector": "ExportWorkflowService validation -> Impulse policy",
            "target_model": source_root,
            "source_fresh_import": True,
            "export_writer_called": False,
        },
        "output_safety": output_safety,
    }


def _run_flip_policy_case(out_dir: Path) -> dict[str, Any]:
    """Prove fresh-import PMX 2.1 Flip metadata reaches policy rejection."""
    from mmd_tools.core.pmx_data import PmxData
    from mmd_tools.core.pmx_data.morph import PmxMorphType
    from mmd_tools.services.export_workflow_service import (
        ExportWorkflowRequest,
        ExportWorkflowService,
    )

    source_model = _write_flip_probe_fixture(
        out_dir / "fixtures" / "flip_policy_input.pmx"
    )
    source_pmx = PmxData().parse_file(str(source_model))
    source_flip_count = sum(
        int(getattr(morph, "morph_type", -1)) == int(PmxMorphType.FlipMorph)
        for morph in source_pmx.morphs
    )
    if source_flip_count <= 0:
        raise RuntimeError("Flip probe fixture did not contain a Flip morph")

    output = out_dir / "model.pmx"
    report_dir = out_dir / "report"
    source_root = _fresh_import(source_model)
    import_oracles = {
        "source_flip_morph_count": source_flip_count,
        **_capture_flip_import_provenance(source_root),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    sentinel = b"pre-existing-flip-policy-target"
    output.write_bytes(sentinel)
    before = output.read_bytes()
    request = ExportWorkflowRequest(
        str(output),
        {
            "export_format": "pmx",
            "require_target": True,
            "target_model": source_root,
            "target_identity": source_root,
            "validation_report_dir": str(report_dir),
            "validation_report_evidence": {
                "gate": "V070-EXPORT-RELEASE-GATE-1",
                "fixture": source_model.name,
                "fresh_import": True,
                "oracles": ["flip_provenance", "policy_reject"],
            },
        },
    )
    validation = ExportWorkflowService().validate(request)
    policy_codes = [issue.code for issue in validation.report.issues]
    if validation.state != "Blocked" or "MORPH_TYPE_UNSUPPORTED" not in policy_codes:
        raise AssertionError(
            "PMX Flip policy probe expected a blocking rejection, "
            f"got state={validation.state!r}, issues={policy_codes!r}"
        )
    payload = validation.payload
    collected_flip_count = sum(
        morph.get("type") == "flip"
        for morph in payload.get("morphs", [])
    ) if isinstance(payload, dict) else 0
    if collected_flip_count <= 0:
        raise AssertionError("Flip collector payload did not retain a positive morph count")
    import_oracles["collected_flip_morph_count"] = collected_flip_count

    report_dir.mkdir(parents=True, exist_ok=True)
    evidence = request.options["validation_report_evidence"]
    validation.report.write_canonical_json(
        report_dir / "report.json",
        target_identity=source_root,
        provenance="ExportWorkflowService validation",
        evidence=evidence,
    )
    validation.report.write_markdown(
        report_dir / "report.md",
        target_identity=source_root,
        provenance="ExportWorkflowService validation",
        evidence=evidence,
    )
    after = output.read_bytes() if output.exists() else None
    output_safety = {
        "target_existed_before": True,
        "target_exists_after": output.exists(),
        "created": False,
        "overwritten": after != before,
        "preserved": after == before,
        "writer_called": False,
    }
    if not output_safety["target_exists_after"] or not output_safety["preserved"]:
        raise AssertionError(f"Flip policy rejection changed output target: {output}")
    return {
        "status": "policy-reject",
        "format": "pmx_flip",
        "source": str(source_model),
        "output": None,
        "output_target": str(output),
        "report_json": str(report_dir / "report.json"),
        "report_md": str(report_dir / "report.md"),
        "policy_code": "MORPH_TYPE_UNSUPPORTED",
        "import_oracles": import_oracles,
        "collection": {
            "collector": "ExportWorkflowService validation -> Flip policy",
            "target_model": source_root,
            "source_fresh_import": True,
            "export_writer_called": False,
        },
        "output_safety": output_safety,
    }


def _prepare_morph_probe_fixture(source_model: Path, out_dir: Path) -> Path:
    """Extend the existing PMX 2.0 morph fixture with UV fields."""
    from mmd_tools.core.pmx_data import PmxData
    from mmd_tools.core.pmx_data.morph import PmxMorph, PmxMorphType

    pmx = PmxData().parse_file(str(source_model))
    pmx.header.version = 2.0
    pmx.header.additional_uv = 4
    for vertex_index, vertex in enumerate(pmx.vertices):
        vertex.additional_uv_count = 4
        vertex.additional_uvs = [
            (
                vertex_index + channel * 0.01 + 0.1,
                vertex_index + channel * 0.01 + 0.2,
                vertex_index + channel * 0.01 + 0.3,
                vertex_index + channel * 0.01 + 0.4,
            )
            for channel in range(4)
        ]

    def append_morph(name: str, morph_type: PmxMorphType, offsets: list[dict[str, Any]]) -> int:
        morph = PmxMorph(
            pmx.header.vertex_index_size,
            pmx.header.material_index_size,
            pmx.header.bone_index_size,
            pmx.header.morph_index_size,
            pmx.header.rigid_body_index_size,
            pmx.header.encoding,
        )
        morph.name = name
        morph.name_english = name
        morph.panel = 4
        morph.morph_type = morph_type
        morph.offsets = offsets
        pmx.morphs.append(morph)
        return len(pmx.morphs) - 1

    uv_types = (
        PmxMorphType.UVMorph,
        PmxMorphType.AdditionalUVMorph1,
        PmxMorphType.AdditionalUVMorph2,
        PmxMorphType.AdditionalUVMorph3,
        PmxMorphType.AdditionalUVMorph4,
    )
    for channel, morph_type in enumerate(uv_types):
        append_morph(
            f"probe_uv_{channel}",
            morph_type,
            [
                {
                    "vertex_index": channel % len(pmx.vertices),
                    "uv_offset": (
                        0.125 + channel,
                        -0.25 - channel,
                        0.375 + channel,
                        -0.5 - channel,
                    ),
                }
            ],
        )
    output = out_dir / "fixtures" / "morph_probe_input.pmx"
    output.parent.mkdir(parents=True, exist_ok=True)
    pmx.write_file(str(output))
    return output


def _run_morph_case(source_model: Path, out_dir: Path) -> dict[str, Any]:
    """Roundtrip the morph fixture with field-level Maya import/export evidence."""
    probe_model = _prepare_morph_probe_fixture(source_model, out_dir)
    case = _run_model_case(
        "pmx",
        probe_model,
        out_dir,
        compare_morphs=True,
    )
    case["format"] = "pmx_morph"
    case["source_fixture"] = str(source_model)
    case["probe_fixture"] = str(probe_model)
    return case


def _run_physics_case(source_model: Path, out_dir: Path) -> dict[str, Any]:
    """Roundtrip the repository physics fixture with rigid/joint oracle checks."""
    probe_model, normalizations = _prepare_physics_probe_fixture(source_model, out_dir)
    case = _run_model_case(
        "pmx",
        probe_model,
        out_dir,
        compare_physics=True,
    )
    case["format"] = "pmx_physics"
    case["source_fixture"] = str(source_model)
    case["input_normalizations"] = normalizations
    return case


def _run_vmd_case(source_pmx: Path, source_vmd: Path, out_dir: Path) -> dict[str, Any]:
    """Roundtrip a VMD through a Maya scene and compare fresh-import poses."""
    from mmd_tools.core.vmd_data import VmdData
    from mmd_tools.services.export_workflow_service import (
        ExportWorkflowRequest,
        ExportWorkflowService,
    )

    source_root = _fresh_import(source_pmx)
    source_root = _import_vmd_into_current_scene(source_root, source_pmx, source_vmd)
    source_oracle = _capture_scene_oracle(source_root, ORACLE_FRAMES)
    output = out_dir / "motion.vmd"
    report_dir = out_dir / "report"
    result = ExportWorkflowService().execute(
        ExportWorkflowRequest(
            str(output),
            {
                "vmd_mode": "C",
                "export_format": "vmd",
                "require_target": True,
                "target_model": source_root,
                "start_frame": min(ORACLE_FRAMES),
                "end_frame": max(ORACLE_FRAMES),
                "model_name": VmdData().parse_file(str(source_vmd)).header.model_name,
                "target_identity": source_root,
                "validation_report_dir": str(report_dir),
                "validation_report_evidence": {
                    "gate": "V070-EXPORT-RELEASE-GATE-1",
                    "fixture": source_vmd.name,
                    "fresh_import": True,
                    "oracles": ["pose", "metadata"],
                },
            },
        ),
        acknowledge_warnings=True,
    )
    if not result.succeeded:
        raise RuntimeError(f"vmd export failed: {result.error or result.report}")
    parsed = VmdData().parse_file(str(output))
    if not parsed.bone_frames:
        raise AssertionError("VMD output contains no bone frames")
    fresh_root = _fresh_import(source_pmx)
    fresh_root = _import_vmd_into_current_scene(fresh_root, source_pmx, output)
    result_oracle = _capture_scene_oracle(fresh_root, ORACLE_FRAMES)
    failures = _compare_scene_oracles(source_oracle, result_oracle, pose=True, mesh=False, materials=False)
    if failures:
        raise AssertionError("; ".join(failures))
    return {
        "status": "pass",
        "format": "vmd",
        "source": str(source_vmd),
        "output": str(output),
        "report_json": str(report_dir / "report.json"),
        "report_md": str(report_dir / "report.md"),
        "parsed_counts": {
            "bone_frames": len(parsed.bone_frames),
            "morph_frames": len(parsed.morph_frames),
            "camera_frames": len(parsed.camera_frames),
            "light_frames": len(parsed.light_frames),
        },
        "oracles": {
            "mesh": result_oracle["mesh"],
            "pose": result_oracle["pose"],
            "metadata": result_oracle["metadata"],
        },
        "collection": {
            "collector": "ExportWorkflowService -> VmdSceneCollector.collect",
            "target_model": source_root,
            "source_fresh_import": True,
            "result_fresh_import": True,
        },
    }


def _import_vmd_into_current_scene(root: str, pmx_path: Path, vmd_path: Path) -> str:
    """Apply a model-owned VMD to the current model and require success."""
    from mmd_tools.io.mmd_importer import import_mmd_file

    result = import_mmd_file(
        str(vmd_path),
        options={
            **_import_options(),
            "target_model": root,
            "pmx_path": str(pmx_path),
            "bake_mode": False,
        },
    )
    if not result:
        raise RuntimeError(f"Maya VMD import returned no result: {vmd_path}")
    return root


def run_probe(pmx_path: Path, vmd_path: Path, out_dir: Path) -> dict[str, Any]:
    """Run all model and motion cases in one initialized Maya process."""
    from mmd_tools.core.pmx_data import PmxData
    from maya import standalone

    os.environ.setdefault("MMD_TOOLS_SKIP_SHADER_OVERRIDE", "1")
    try:
        standalone.initialize(name="python")
    except RuntimeError:
        pass
    load_mmd_tools_plugin(ROOT)
    out_dir.mkdir(parents=True, exist_ok=True)
    PmxData().parse_file(str(pmx_path))
    pmd_path = out_dir / "fixtures" / "independent_minimal.pmd"
    _write_independent_pmd_fixture(pmd_path)
    cases = []
    for export_format, source_model in (
        ("pmx", pmx_path),
        ("pmd", pmd_path),
    ):
        case_dir = out_dir / export_format
        try:
            case = _run_model_case(export_format, source_model, case_dir)
            case["conversion_warnings"] = []
        except Exception as exc:
            case = {
                "status": "fail",
                "format": export_format,
                "source": str(source_model),
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=12),
            }
        cases.append(case)
    try:
        cases.append(_run_morph_case(DEFAULT_MORPH_PMX, out_dir / "pmx-morph"))
    except Exception as exc:
        cases.append(
            {
                "status": "fail",
                "format": "pmx_morph",
                "source": str(DEFAULT_MORPH_PMX),
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=12),
            }
        )
    bone_case_dir = out_dir / "pmx-bone-semantics"
    bone_fixture = bone_case_dir / "fixtures" / "bone_semantics_input.pmx"
    try:
        cases.append(_run_bone_semantics_case(DEFAULT_PMX, bone_case_dir))
    except Exception as exc:
        cases.append(
            {
                "status": "fail",
                "format": "pmx_bone_semantics",
                "source": str(bone_fixture),
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=12),
            }
        )
    try:
        cases.append(_run_physics_case(DEFAULT_PHYSICS_PMX, out_dir / "pmx-physics"))
    except Exception as exc:
        cases.append(
            {
                "status": "fail",
                "format": "pmx_physics",
                "source": str(DEFAULT_PHYSICS_PMX),
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=12),
            }
        )
    soft_body_case_dir = out_dir / "pmx-soft-body"
    soft_body_fixture = soft_body_case_dir / "fixtures" / "soft_body_policy_input.pmx"
    try:
        cases.append(_run_soft_body_policy_case(soft_body_case_dir))
    except Exception as exc:
        cases.append(
            {
                "status": "fail",
                "format": "pmx_soft_body",
                "source": str(soft_body_fixture),
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=12),
            }
        )
    sdef_case_dir = out_dir / "pmx-sdef"
    sdef_fixture = sdef_case_dir / "fixtures" / "sdef_policy_input.pmx"
    try:
        cases.append(_run_sdef_policy_case(sdef_case_dir))
    except Exception as exc:
        cases.append(
            {
                "status": "fail",
                "format": "pmx_sdef",
                "source": str(sdef_fixture),
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=12),
            }
        )
    impulse_case_dir = out_dir / "pmx-impulse"
    impulse_fixture = impulse_case_dir / "fixtures" / "impulse_policy_input.pmx"
    try:
        cases.append(_run_impulse_policy_case(impulse_case_dir))
    except Exception as exc:
        cases.append(
            {
                "status": "fail",
                "format": "pmx_impulse",
                "source": str(impulse_fixture),
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=12),
            }
        )
    flip_case_dir = out_dir / "pmx-flip"
    flip_fixture = flip_case_dir / "fixtures" / "flip_policy_input.pmx"
    try:
        cases.append(_run_flip_policy_case(flip_case_dir))
    except Exception as exc:
        cases.append(
            {
                "status": "fail",
                "format": "pmx_flip",
                "source": str(flip_fixture),
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=12),
            }
        )
    try:
        cases.append(_run_vmd_case(pmx_path, vmd_path, out_dir / "vmd"))
    except Exception as exc:
        cases.append(
            {
                "status": "fail",
                "format": "vmd",
                "source": str(vmd_path),
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=12),
            }
        )
    accepted_case_statuses = {"pass", "policy-reject"}
    report = {
        "schema_version": 1,
        "gate": "V070-EXPORT-RELEASE-GATE-1",
        "maya_version": _maya_version(),
        "status": "pass" if all(case["status"] in accepted_case_statuses for case in cases) else "fail",
        "fixture": {
            "pmx": str(pmx_path),
            "pmx_morph": str(DEFAULT_MORPH_PMX),
            "pmx_bone_semantics": str(bone_fixture),
            "pmx_physics": str(DEFAULT_PHYSICS_PMX),
            "pmx_soft_body": str(soft_body_fixture),
            "pmx_sdef": str(sdef_fixture),
            "pmx_impulse": str(impulse_fixture),
            "pmx_flip": str(flip_fixture),
            "pmd": str(pmd_path),
            "vmd": str(vmd_path),
        },
        "cases": cases,
    }
    (out_dir / "maya-probe.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
    return report


def _maya_version() -> str:
    """Return the active Maya version without importing it at module load."""
    from maya import cmds

    return str(cmds.about(version=True))


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the Maya probe, and return a process status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pmx", default=str(DEFAULT_PMX))
    parser.add_argument("--vmd", default=str(DEFAULT_VMD))
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args(argv)
    try:
        report = run_probe(
            Path(args.pmx).resolve(),
            Path(args.vmd).resolve(),
            _require_build_path(args.out_dir, "--out-dir"),
        )
    except Exception as exc:
        print(f"Maya export release probe failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1
    print(json.dumps({"status": report["status"], "maya_version": report["maya_version"]}, ensure_ascii=False))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
