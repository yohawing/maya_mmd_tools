"""Functional PMX import contract shared by Python and native C++ routes."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from maya import cmds

from mmd_tools.converters.export_scene_collector import ExportSceneCollector
from mmd_tools.converters import material_morph_runtime
from mmd_tools.core.constants import (
    ATTR_MMD_ADDITIONAL_UVS_JSON,
    ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON,
    ATTR_MMD_BONE_INDEX,
    ATTR_MMD_BONE_PARENT_INDEX,
    ATTR_MMD_COMMENT,
    ATTR_MMD_COMMENT_EN,
    ATTR_MMD_DISPLAY_FRAMES_JSON,
    ATTR_MMD_IMPULSE_MORPH_OFFSETS_JSON,
    ATTR_MMD_MODEL_NAME,
    ATTR_MMD_MODEL_NAME_EN,
    ATTR_MMD_PMX_ADDITIONAL_UV_COUNT,
    ATTR_MMD_SOURCE_VERTEX_INDICES,
    ATTR_MMD_TEXTURE_TABLE_JSON,
    ATTR_MMD_UV_MORPH_OFFSETS_JSON,
)
from mmd_tools.core.pmx_data import PmxData
from mmd_tools.core.pmx_data.morph import PmxMorph, PmxMorphType
from mmd_tools.core.pmx_data.rigid_body import PmxRigidBody
from mmd_tools.core.physics_dag_descriptor import build_descriptors_from_dag
from mmd_tools.core.physics_descriptor import build_descriptors_from_pmx


_MORPH_TYPE_NAMES = {
    PmxMorphType.GroupMorph: "group",
    PmxMorphType.VertexMorph: "vertex",
    PmxMorphType.BoneMorph: "bone",
    PmxMorphType.UVMorph: "uv",
    PmxMorphType.AdditionalUVMorph1: "additional_uv1",
    PmxMorphType.AdditionalUVMorph2: "additional_uv2",
    PmxMorphType.AdditionalUVMorph3: "additional_uv3",
    PmxMorphType.AdditionalUVMorph4: "additional_uv4",
    PmxMorphType.MaterialMorph: "material",
    PmxMorphType.FlipMorph: "flip",
    PmxMorphType.ImpulseMorph: "impulse",
}

_MORPH_OFFSET_ATTRIBUTES = {
    "bone": "mmd_bone_morph_offsets_json",
    "group": "mmd_group_morph_offsets_json",
    "material": "mmd_material_morph_offsets_json",
    "uv": ATTR_MMD_UV_MORPH_OFFSETS_JSON,
    "additional_uv1": ATTR_MMD_UV_MORPH_OFFSETS_JSON,
    "additional_uv2": ATTR_MMD_UV_MORPH_OFFSETS_JSON,
    "additional_uv3": ATTR_MMD_UV_MORPH_OFFSETS_JSON,
    "additional_uv4": ATTR_MMD_UV_MORPH_OFFSETS_JSON,
    "flip": "mmd_flip_morph_offsets_json",
    "impulse": ATTR_MMD_IMPULSE_MORPH_OFFSETS_JSON,
}


def write_morph_contract_fixture(output_path: str) -> str:
    """Create a bounded real PMX fixture containing common import morph kinds.

    The checked-in VMD morph fixture already covers vertex, bone, material and
    group morphs. This helper extends a copy with all five UV channels, a
    nonzero PMX 2.1 Flip metadata entry, and an Impulse entry targeting a
    valid rigid body. The C++ route parses this file as a real PMX; no mock
    importer or synthetic Maya nodes are involved. Impulse is checked as
    nonzero metadata; its runtime effect remains unproven.
    """

    source_path = Path(__file__).resolve().parents[1] / "data" / "for_unit_test" / "test_vmd_morph_real_gate.pmx"
    pmx = PmxData().parse_file(str(source_path))
    pmx.header.version = 2.1
    pmx.header.additional_uv = 4
    for vertex_index, vertex in enumerate(pmx.vertices):
        vertex.additional_uv_count = 4
        vertex.additional_uvs = [
            tuple(
                float(vertex_index) + channel * 0.01 + component * 0.1
                for component in range(1, 5)
            )
            for channel in range(4)
        ]

    if not pmx.rigid_bodies:
        rigid_body = PmxRigidBody(pmx.header.bone_index_size, pmx.header.encoding)
        rigid_body.name = "contract_impulse_body"
        rigid_body.name_english = "contract_impulse_body"
        rigid_body.related_bone_index = 0
        rigid_body.group = 0
        rigid_body.collision_mask = 0xFFFF
        rigid_body.shape_type = 0
        rigid_body.size = (0.5, 0.5, 0.5)
        rigid_body.position = (0.0, 0.0, 0.0)
        rigid_body.rotation = (0.0, 0.0, 0.0)
        rigid_body.mass = 1.0
        rigid_body.velocity_attenuation = 0.5
        rigid_body.rotation_attenuation = 0.5
        rigid_body.elasticity = 0.1
        rigid_body.friction = 0.5
        rigid_body.physics_mode = 0
        pmx.rigid_bodies.append(rigid_body)

    def append_morph(name: str, morph_type: PmxMorphType, offsets: List[Dict[str, Any]]) -> int:
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
            "contract_uv_{}".format(channel),
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

    vertex_morph_index = next(
        index
        for index, morph in enumerate(pmx.morphs)
        if morph.morph_type == PmxMorphType.VertexMorph
    )
    append_morph(
        "contract_flip",
        PmxMorphType.FlipMorph,
        [{"morph_index": vertex_morph_index, "flip_rate": 0.75}],
    )
    append_morph(
        "contract_impulse",
        PmxMorphType.ImpulseMorph,
        [
            {
                "rigid_body_index": 0,
                "is_local": 1,
                "impulse": (0.25, -0.5, 0.75),
                "torque": (-0.125, 0.25, -0.375),
            }
        ],
    )

    impulse_morph = pmx.morphs[-1]
    if not pmx.rigid_bodies or any(
        not 0 <= int(offset["rigid_body_index"]) < len(pmx.rigid_bodies)
        for offset in impulse_morph.offsets
    ):
        raise AssertionError("contract impulse must reference a valid rigid body")

    pmx.write_file(output_path)
    return output_path


def _assert_close(test_case: Any, actual: Sequence[float], expected: Sequence[float], label: str) -> None:
    test_case.assertEqual(len(actual), len(expected), label)
    for actual_value, expected_value in zip(actual, expected):
        test_case.assertAlmostEqual(float(actual_value), float(expected_value), places=4, msg=label)


def _assert_descriptor_semantics(test_case: Any, actual: Any, expected: Any) -> None:
    """Compare typed physics fields and references without hashing float sign bits."""

    test_case.assertEqual(
        len(actual.rigid_bodies), len(expected.rigid_bodies), "physics rigid body descriptors"
    )
    test_case.assertEqual(
        len(actual.joints), len(expected.joints), "physics joint descriptors"
    )
    body_integer_fields = ("shape", "collision_group", "collision_mask", "bone_index", "mode")
    body_float_fields = (
        ("shape_size", 3),
        ("position_xyz", 3),
        ("rotation_euler_xyz", 3),
        ("body_from_bone_position_xyz", 3),
        ("body_from_bone_rotation_xyzw", 4),
        ("bone_from_body_position_xyz", 3),
        ("bone_from_body_rotation_xyzw", 4),
    )
    body_scalar_fields = ("mass", "linear_damping", "angular_damping", "friction", "restitution")
    for index, (actual_body, expected_body) in enumerate(
        zip(actual.rigid_bodies, expected.rigid_bodies)
    ):
        for field in body_integer_fields:
            test_case.assertEqual(
                int(getattr(actual_body, field)),
                int(getattr(expected_body, field)),
                "physics rigid body {} {}".format(index, field),
            )
        for field in body_scalar_fields:
            test_case.assertEqual(
                float(getattr(actual_body, field)),
                float(getattr(expected_body, field)),
                "physics rigid body {} {}".format(index, field),
            )
        for field, count in body_float_fields:
            test_case.assertEqual(
                tuple(float(value) for value in getattr(actual_body, field)[:count]),
                tuple(float(value) for value in getattr(expected_body, field)[:count]),
                "physics rigid body {} {}".format(index, field),
            )

    joint_integer_fields = ("kind", "rigidbody_a", "rigidbody_b")
    joint_float_fields = (
        ("position_xyz", 3),
        ("rotation_euler_xyz", 3),
        ("translation_lower_limit_xyz", 3),
        ("translation_upper_limit_xyz", 3),
        ("rotation_lower_limit_xyz", 3),
        ("rotation_upper_limit_xyz", 3),
        ("spring_translation_factor_xyz", 3),
        ("spring_rotation_factor_xyz", 3),
    )
    for index, (actual_joint, expected_joint) in enumerate(zip(actual.joints, expected.joints)):
        for field in joint_integer_fields:
            test_case.assertEqual(
                int(getattr(actual_joint, field)),
                int(getattr(expected_joint, field)),
                "physics joint {} {}".format(index, field),
            )
        for field, count in joint_float_fields:
            test_case.assertEqual(
                tuple(float(value) for value in getattr(actual_joint, field)[:count]),
                tuple(float(value) for value in getattr(expected_joint, field)[:count]),
                "physics joint {} {}".format(index, field),
            )

    actual_errors = [error.__dict__ for error in actual.validation_errors]
    expected_errors = [error.__dict__ for error in expected.validation_errors]
    test_case.assertEqual(actual_errors, expected_errors, "physics descriptor validation errors")


def _read_source_indices(shape: str) -> List[int]:
    transform = (cmds.listRelatives(shape, parent=True, fullPath=True) or [shape])[0]
    if cmds.attributeQuery(ATTR_MMD_SOURCE_VERTEX_INDICES, node=transform, exists=True):
        values = cmds.getAttr("{}.{}".format(transform, ATTR_MMD_SOURCE_VERTEX_INDICES)) or []
        return [int(value) for value in values]
    return list(range(int(cmds.polyEvaluate(shape, vertex=True))))


def _mesh_source_map(root: str) -> Dict[int, Tuple[str, int]]:
    result: Dict[int, Tuple[str, int]] = {}
    shapes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
    for shape in shapes:
        if cmds.getAttr("{}.intermediateObject".format(shape)):
            continue
        for local_index, source_index in enumerate(_read_source_indices(shape)):
            result.setdefault(int(source_index), (shape, local_index))
    return result


def _assert_additional_uv_storage(test_case: Any, root: str, pmx: Any) -> None:
    """Compare every imported additional-UV payload with its PMX source values."""
    expected_count = int(getattr(pmx.header, "additional_uv", 0) or 0)
    if expected_count == 0:
        return

    shapes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
    seen_transforms = set()
    seen_values: Dict[int, List[List[float]]] = {}
    for shape in shapes:
        if cmds.getAttr("{}.intermediateObject".format(shape)):
            continue
        transform = (cmds.listRelatives(shape, parent=True, fullPath=True) or [shape])[0]
        if transform in seen_transforms:
            continue
        seen_transforms.add(transform)
        storage_node = next(
            (
                node
                for node in (transform, shape)
                if cmds.attributeQuery(ATTR_MMD_ADDITIONAL_UVS_JSON, node=node, exists=True)
            ),
            None,
        )
        count_node = next(
            (
                node
                for node in (transform, shape)
                if cmds.attributeQuery(ATTR_MMD_PMX_ADDITIONAL_UV_COUNT, node=node, exists=True)
            ),
            None,
        )
        test_case.assertIsNotNone(storage_node, "additional UV payload on {}".format(transform))
        test_case.assertIsNotNone(count_node, "additional UV count on {}".format(transform))
        actual_count = int(cmds.getAttr("{}.{}".format(count_node, ATTR_MMD_PMX_ADDITIONAL_UV_COUNT)))
        test_case.assertEqual(actual_count, expected_count, "additional UV channel count")
        payload = json.loads(cmds.getAttr("{}.{}".format(storage_node, ATTR_MMD_ADDITIONAL_UVS_JSON)))
        test_case.assertEqual(payload.get("schema_version"), 1, "additional UV schema")
        source_indices = payload.get("source_vertex_indices")
        values = payload.get("additional_uvs")
        test_case.assertEqual(payload.get("channel_count"), expected_count, "additional UV payload count")
        test_case.assertEqual(payload.get("source_vertex_count"), len(pmx.vertices), "additional UV source count")
        test_case.assertIsInstance(source_indices, list, "additional UV source indices")
        test_case.assertIsInstance(values, list, "additional UV payload values")
        local_source_indices = _read_source_indices(shape)
        test_case.assertEqual(payload.get("vertex_count"), len(local_source_indices), "additional UV vertex count")
        test_case.assertEqual(source_indices, local_source_indices, "additional UV source mapping")
        test_case.assertEqual(len(values), len(source_indices), "additional UV payload rows")
        for source_index, channels in zip(source_indices, values):
            test_case.assertIsInstance(source_index, int, "additional UV source index type")
            test_case.assertIn(source_index, range(len(pmx.vertices)), "additional UV source index bounds")
            expected_channels = getattr(pmx.vertices[source_index], "additional_uvs", ()) or ()
            test_case.assertEqual(len(expected_channels), expected_count, "additional UV source channels")
            test_case.assertIsInstance(channels, list, "additional UV channel rows")
            test_case.assertEqual(len(channels), expected_count, "additional UV channel rows")
            for channel_index, (actual_channel, expected_channel) in enumerate(
                zip(channels, expected_channels)
            ):
                _assert_close(
                    test_case,
                    actual_channel,
                    expected_channel,
                    "source vertex {} additional UV {}".format(source_index, channel_index),
                )
            previous = seen_values.get(source_index)
            if previous is None:
                seen_values[source_index] = channels
            else:
                test_case.assertEqual(
                    channels,
                    previous,
                    "additional UV duplicate source {}".format(source_index),
                )

    test_case.assertTrue(seen_values, "additional UV source payload")
    required_source_indices = {
        int(offset["vertex_index"])
        for morph in pmx.morphs
        if morph.morph_type
        in (
            PmxMorphType.UVMorph,
            PmxMorphType.AdditionalUVMorph1,
            PmxMorphType.AdditionalUVMorph2,
            PmxMorphType.AdditionalUVMorph3,
            PmxMorphType.AdditionalUVMorph4,
        )
        for offset in getattr(morph, "offsets", []) or ()
        if "vertex_index" in offset
    }
    missing_source_indices = sorted(required_source_indices - set(seen_values))
    test_case.assertFalse(
        missing_source_indices,
        "additional UV morph source values missing: {}".format(missing_source_indices),
    )


def _weight_map(vertex: Any) -> Dict[int, float]:
    mode = int(vertex.weight_transform_type)
    if mode == 0:
        pairs = [(vertex.bone_indices[0], 1.0)]
    elif mode in (1, 3):
        weight = float(vertex.bone_weights[0])
        pairs = [(vertex.bone_indices[0], weight), (vertex.bone_indices[1], 1.0 - weight)]
    else:
        pairs = list(zip(vertex.bone_indices[:4], vertex.bone_weights[:4]))
    return {int(index): float(weight) for index, weight in pairs if float(weight) > 1.0e-7}


def _skin_cluster(shape: str) -> str:
    history = cmds.listHistory(shape, pruneDagObjects=True) or []
    clusters = [node for node in history if cmds.nodeType(node) == "skinCluster"]
    if len(clusters) != 1:
        raise AssertionError("expected one skinCluster for {}, got {}".format(shape, clusters))
    return clusters[0]


def _scene_morph_nodes(root: str) -> Dict[int, str]:
    # Each parity case starts from a fresh Maya scene and imports one model.
    # Some legacy vertex metadata nodes predate the ``mmd_model_root`` message
    # and therefore cannot be filtered through that optional attribute.
    nodes_by_index: Dict[int, str] = {}
    for node in cmds.ls(long=True) or []:
        if not cmds.attributeQuery("mmd_morph_index", node=node, exists=True):
            continue
        morph_index = int(cmds.getAttr("{}.mmd_morph_index".format(node)))
        if morph_index in nodes_by_index:
            raise AssertionError(
                "duplicate imported morph index {}: {} and {}".format(
                    morph_index, nodes_by_index[morph_index], node
                )
            )
        nodes_by_index[morph_index] = node
    return nodes_by_index


def _source_morph_value(morph: Any) -> Dict[str, Any]:
    kind = _MORPH_TYPE_NAMES[morph.morph_type]
    value = {
        "name": str(morph.name),
        "name_en": str(morph.name_english),
        "panel": int(morph.panel),
        "type": kind,
        "offsets": deepcopy(list(morph.offsets or [])),
    }
    if kind == "vertex":
        value["offsets"] = [
            {
                "vertex_index": int(offset["vertex_index"]),
                "position_offset": list(offset["position_offset"]),
            }
            for offset in morph.offsets
        ]
    return value


def assert_import_contract(
    test_case: Any,
    root: str,
    pmx: Any,
    *,
    expect_physics: bool,
) -> None:
    """Assert source-derived values for one imported route.

    Vertex, bone, material, and group morphs are evaluated with nonzero inputs.
    UV, Flip, and Impulse morphs are checked as metadata and raw provenance
    because their viewport/physics effect is outside this import contract.
    Physics checks use the typed DAG descriptor, which proves authoring fields
    and message/index provenance, not a live Bullet step.
    """

    test_case.assertTrue(root and cmds.objExists(root))
    for attribute, expected in (
        (ATTR_MMD_MODEL_NAME, pmx.header.model_name),
        (ATTR_MMD_MODEL_NAME_EN, pmx.header.model_name_english),
        (ATTR_MMD_COMMENT, pmx.header.comment),
        (ATTR_MMD_COMMENT_EN, pmx.header.comment_english),
    ):
        test_case.assertTrue(cmds.attributeQuery(attribute, node=root, exists=True), attribute)
        test_case.assertEqual(cmds.getAttr("{}.{}".format(root, attribute)), expected, attribute)
    display_frames = json.loads(cmds.getAttr("{}.{}".format(root, ATTR_MMD_DISPLAY_FRAMES_JSON)))
    test_case.assertEqual(len(display_frames), len(pmx.display_frames), "display frame metadata count")
    textures = json.loads(cmds.getAttr("{}.{}".format(root, ATTR_MMD_TEXTURE_TABLE_JSON)))
    test_case.assertEqual(textures, [str(value) for value in pmx.textures], "texture table")

    mesh_map = _mesh_source_map(root)
    test_case.assertGreater(len(mesh_map), 0, "source vertex mapping")
    test_case.assertLessEqual(len(mesh_map), len(pmx.vertices), "source vertex mapping bounds")
    test_case.assertEqual(
        sum(int(cmds.polyEvaluate(shape, face=True)) for shape in cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or [] if not cmds.getAttr("{}.intermediateObject".format(shape))),
        len(pmx.faces),
        "face count",
    )
    required_source_indices = {0, max(0, len(pmx.vertices) - 1)}
    required_source_indices.update(
        int(vertex_index)
        for morph in pmx.morphs
        for offset in getattr(morph, "offsets", []) or []
        if "vertex_index" in offset
        for vertex_index in [offset["vertex_index"]]
    )
    missing_source_indices = sorted(set(required_source_indices) - set(mesh_map))
    test_case.assertFalse(
        missing_source_indices,
        "required PMX source vertices missing from imported meshes: {}".format(
            missing_source_indices
        ),
    )
    _assert_additional_uv_storage(test_case, root, pmx)
    sample_indices = set(required_source_indices)
    seen_weight_modes = set()
    for index, vertex in enumerate(pmx.vertices):
        if int(vertex.weight_transform_type) not in seen_weight_modes:
            sample_indices.add(index)
            seen_weight_modes.add(int(vertex.weight_transform_type))
    for source_index in sorted(sample_indices):
        shape, local_index = mesh_map[source_index]
        expected_position = (pmx.vertices[source_index].position[0], pmx.vertices[source_index].position[1], -pmx.vertices[source_index].position[2])
        actual_position = cmds.pointPosition("{}.vtx[{}]".format(shape, local_index), local=True)
        _assert_close(test_case, actual_position, expected_position, "vertex {} position".format(source_index))
        uv_component = cmds.polyListComponentConversion(
            "{}.vtx[{}]".format(shape, local_index), toUV=True
        ) or []
        uv_components = cmds.filterExpand(uv_component, selectionMask=35) or []
        test_case.assertTrue(uv_components, "vertex {} UV mapping".format(source_index))
        # Maya's UV origin is opposite PMX's vertical convention.
        source_uv = pmx.vertices[source_index].uv
        expected_uv = (source_uv[0], 1.0 - source_uv[1])
        matching_uv = False
        for uv_component in uv_components:
            actual_uv = cmds.polyEditUV(uv_component, query=True) or []
            if len(actual_uv) >= 2 and max(
                abs(float(actual_uv[0]) - float(expected_uv[0])),
                abs(float(actual_uv[1]) - float(expected_uv[1])),
            ) <= 1.0e-4:
                matching_uv = True
                break
        test_case.assertTrue(matching_uv, "vertex {} UV".format(source_index))

        cluster = _skin_cluster(shape)
        influences = cmds.skinCluster(cluster, query=True, influence=True) or []
        influence_indices = {
            int(cmds.getAttr("{}.{}".format(joint, ATTR_MMD_BONE_INDEX))): joint
            for joint in influences
            if cmds.attributeQuery(ATTR_MMD_BONE_INDEX, node=joint, exists=True)
        }
        values = cmds.skinPercent(
            cluster,
            "{}.vtx[{}]".format(shape, local_index),
            query=True,
            value=True,
        ) or []
        actual_weights = {
            int(cmds.getAttr("{}.{}".format(joint, ATTR_MMD_BONE_INDEX))): float(value)
            for joint, value in zip(influences, values)
            if float(value) > 1.0e-7 and cmds.attributeQuery(ATTR_MMD_BONE_INDEX, node=joint, exists=True)
        }
        test_case.assertEqual(set(actual_weights), set(_weight_map(pmx.vertices[source_index])), "vertex {} skin influences".format(source_index))
        for bone_index, expected_weight in _weight_map(pmx.vertices[source_index]).items():
            test_case.assertAlmostEqual(actual_weights[bone_index], expected_weight, places=4, msg="vertex {} skin weight".format(source_index))
        test_case.assertTrue(influence_indices, "skin influences")

    collected = ExportSceneCollector().collect_from_model_root(root)
    test_case.assertEqual(len(collected["materials"]), len(pmx.materials), "material count")
    for actual, expected in zip(collected["materials"], pmx.materials):
        test_case.assertEqual(actual["name"], expected.name)
        test_case.assertEqual(actual["name_english"], expected.name_english)
        test_case.assertEqual(actual["memo"], expected.memo, "material memo")
        for field in ("diffuse", "specular", "ambient", "edge_color"):
            _assert_close(test_case, actual[field], getattr(expected, field), "material {}".format(field))
        for field in ("specular_coefficient", "edge_size"):
            test_case.assertAlmostEqual(actual[field], getattr(expected, field), places=4, msg="material {}".format(field))
        for field in ("draw_flag", "sphere_mode", "shared_toon_flag", "texture_index", "sphere_texture_index", "toon_texture_index"):
            test_case.assertEqual(int(actual[field]), int(getattr(expected, field)), "material {}".format(field))

    joints = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
    joints_by_index = {
        int(cmds.getAttr("{}.{}".format(joint, ATTR_MMD_BONE_INDEX))): joint
        for joint in joints
        if cmds.attributeQuery(ATTR_MMD_BONE_INDEX, node=joint, exists=True)
    }
    test_case.assertEqual(set(joints_by_index), set(range(len(pmx.bones))), "indexed bones")
    for bone_index in (0, len(pmx.bones) - 1):
        joint = joints_by_index[bone_index]
        test_case.assertEqual(
            int(cmds.getAttr("{}.{}".format(joint, ATTR_MMD_BONE_PARENT_INDEX))),
            int(pmx.bones[bone_index].parent_bone_index),
            "bone {} parent".format(bone_index),
        )

    expected_morphs = [_source_morph_value(morph) for morph in pmx.morphs]
    impulse_morphs = [morph for morph in pmx.morphs if morph.morph_type == PmxMorphType.ImpulseMorph]
    if impulse_morphs:
        test_case.assertGreater(len(pmx.rigid_bodies), 0, "impulse rigid body fixture")
        for morph in impulse_morphs:
            for offset in morph.offsets or []:
                test_case.assertIn(
                    int(offset["rigid_body_index"]),
                    range(len(pmx.rigid_bodies)),
                    "impulse rigid body reference",
                )
    scene_morph_nodes = _scene_morph_nodes(root)
    test_case.assertEqual(set(scene_morph_nodes), set(range(len(expected_morphs))), "morph indexes")
    for morph_index, expected in enumerate(expected_morphs):
        node = scene_morph_nodes[morph_index]
        kind = expected["type"]
        test_case.assertEqual(cmds.getAttr("{}.mmd_morph_type".format(node)), kind, "morph {} type".format(morph_index))
        test_case.assertEqual(cmds.getAttr("{}.mmd_morph_name".format(node)), expected["name"], "morph {} name".format(morph_index))
        if kind == "vertex":
            continue
        attribute = _MORPH_OFFSET_ATTRIBUTES[kind]
        test_case.assertTrue(cmds.attributeQuery(attribute, node=node, exists=True), "morph {} raw attribute".format(morph_index))
        actual_offsets = json.loads(cmds.getAttr("{}.{}".format(node, attribute)) or "[]")
        test_case.assertEqual(actual_offsets, _json_safe(expected["offsets"]), "morph {} offsets".format(morph_index))
        if kind == "flip":
            test_case.assertTrue(actual_offsets, "flip morph offsets")
            test_case.assertGreater(
                max(abs(float(offset.get("flip_rate", 0.0))) for offset in actual_offsets),
                1.0e-6,
                "flip morph nonzero metadata",
            )
        if kind == "impulse":
            test_case.assertTrue(actual_offsets, "impulse morph offsets")
            test_case.assertGreater(
                max(
                    max(abs(float(value)) for value in offset.get("impulse", ()))
                    for offset in actual_offsets
                ),
                1.0e-6,
                "impulse morph nonzero metadata",
            )

    blend_shapes = []
    for shape in cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []:
        if cmds.getAttr("{}.intermediateObject".format(shape)):
            continue
        blend_shapes.extend(
            node for node in cmds.listHistory(shape, pruneDagObjects=True) or [] if cmds.nodeType(node) == "blendShape"
        )
    vertex_indices = [index for index, morph in enumerate(pmx.morphs) if morph.morph_type == PmxMorphType.VertexMorph]
    bone_indices = [index for index, morph in enumerate(pmx.morphs) if morph.morph_type == PmxMorphType.BoneMorph]
    material_indices = [index for index, morph in enumerate(pmx.morphs) if morph.morph_type == PmxMorphType.MaterialMorph]
    group_indices = [index for index, morph in enumerate(pmx.morphs) if morph.morph_type == PmxMorphType.GroupMorph]
    functional_indices = set(vertex_indices + bone_indices + material_indices + group_indices)
    controllers = []
    if functional_indices:
        controllers = cmds.listConnections("{}.mmd_morph_controller".format(root), source=True, destination=False, type="mmdMorphController") or []
    controller = None
    if functional_indices:
        test_case.assertEqual(len(controllers), 1, "morph controller for functional morphs")
        controller = controllers[0]
        for index in range(len(expected_morphs)):
            cmds.setAttr("{}.inputWeight[{}]".format(controller, index), 0.0)
    if vertex_indices:
        test_case.assertTrue(blend_shapes, "vertex morph blendShape")
        metadata = json.loads(cmds.getAttr("{}.{}".format(blend_shapes[0], ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON)) or "{}")
        test_case.assertEqual(sorted(int(index) for index in metadata), vertex_indices, "vertex morph metadata indexes")
        vertex_index = vertex_indices[0]
        source_index = int(pmx.morphs[vertex_index].offsets[0]["vertex_index"])
        shape, local_index = mesh_map[source_index]
        base_position = cmds.pointPosition("{}.vtx[{}]".format(shape, local_index), local=True)
        cmds.setAttr("{}.inputWeight[{}]".format(controller, vertex_index), 1.0)
        offset = pmx.morphs[vertex_index].offsets[0]["position_offset"]
        expected_position = tuple(base + delta for base, delta in zip(base_position, (offset[0], offset[1], -offset[2])))
        actual_position = cmds.pointPosition("{}.vtx[{}]".format(shape, local_index), local=True)
        _assert_close(test_case, actual_position, expected_position, "vertex morph evaluated delta")
        cmds.setAttr("{}.inputWeight[{}]".format(controller, vertex_index), 0.0)

    for group_index in group_indices:
        direct_targets = [
            offset
            for offset in (pmx.morphs[group_index].offsets or [])
            if int(offset.get("morph_index", -1)) in vertex_indices
        ]
        if not direct_targets:
            continue
        test_case.assertIsNotNone(controller, "group morph controller")
        target_index = int(direct_targets[0]["morph_index"])
        cmds.setAttr("{}.inputWeight[{}]".format(controller, group_index), 1.0)
        actual_output = cmds.getAttr("{}.outputWeight[{}]".format(controller, target_index))
        expected_output = float(direct_targets[0].get("morph_rate", 0.0))
        test_case.assertAlmostEqual(actual_output, expected_output, places=4, msg="group controller propagation")
        cmds.setAttr("{}.inputWeight[{}]".format(controller, group_index), 0.0)

    if bone_indices:
        bone_morph_index = bone_indices[0]
        bone_morph = pmx.morphs[bone_morph_index]
        bone_offset = next(
            (
                offset
                for offset in bone_morph.offsets or []
                if max(abs(float(value)) for value in offset.get("translation", (0.0, 0.0, 0.0))) > 1.0e-6
            ),
            None,
        )
        test_case.assertIsNotNone(bone_offset, "bone morph nonzero translation fixture")
        joint_index = int(bone_offset["bone_index"])
        joint = joints_by_index[joint_index]
        base_translate = tuple(cmds.getAttr("{}.translate".format(joint))[0])
        cmds.setAttr("{}.inputWeight[{}]".format(controller, bone_morph_index), 1.0)
        actual_translate = tuple(cmds.getAttr("{}.translate".format(joint))[0])
        expected_translate = tuple(
            base + delta
            for base, delta in zip(
                base_translate,
                (
                    float(bone_offset["translation"][0]),
                    float(bone_offset["translation"][1]),
                    -float(bone_offset["translation"][2]),
                ),
            )
        )
        _assert_close(test_case, actual_translate, expected_translate, "bone morph evaluated translation")
        test_case.assertGreater(
            max(abs(actual - base) for actual, base in zip(actual_translate, base_translate)),
            1.0e-6,
            "bone morph input did not change joint translation",
        )
        cmds.setAttr("{}.inputWeight[{}]".format(controller, bone_morph_index), 0.0)

    if material_indices:
        material_morph_index = material_indices[0]
        material_morph = pmx.morphs[material_morph_index]
        material_offset = material_morph.offsets[0]
        expected_offset = tuple(float(value) for value in material_offset["diffuse"][:3])
        test_case.assertGreater(
            max(abs(value) for value in expected_offset),
            1.0e-6,
            "material morph nonzero diffuse fixture",
        )
        evaluators = list(material_morph_runtime._collect_existing_evaluators().values())
        test_case.assertTrue(evaluators, "material morph evaluator")
        evaluator = evaluators[0]
        cmds.refresh(force=True)
        diffuse_at_zero = tuple(cmds.getAttr("{}.outputDiffuse".format(evaluator))[0])
        cmds.setAttr("{}.inputWeight[{}]".format(controller, material_morph_index), 1.0)
        cmds.refresh(force=True)
        diffuse_at_one = tuple(cmds.getAttr("{}.outputDiffuse".format(evaluator))[0])
        actual_delta = tuple(one - zero for one, zero in zip(diffuse_at_one, diffuse_at_zero))
        if int(material_offset.get("operation_type", 0)) == 0:
            expected_delta = tuple(
                zero * (offset - 1.0)
                for zero, offset in zip(diffuse_at_zero, expected_offset)
            )
        else:
            expected_delta = expected_offset
        _assert_close(test_case, actual_delta, expected_delta, "material morph evaluated diffuse")
        test_case.assertGreater(
            max(abs(value) for value in actual_delta),
            1.0e-6,
            "material morph input did not change evaluator output",
        )
        cmds.setAttr("{}.inputWeight[{}]".format(controller, material_morph_index), 0.0)

    if expect_physics:
        test_case.assertEqual(len(pmx.rigid_bodies), len(cmds.listRelatives(root, allDescendents=True, type="mmdRigidBodyShape", fullPath=True) or []), "physics rigid body count")
        bone_joints = [joints_by_index.get(index) for index in range(len(pmx.bones))]
        actual_descriptors = build_descriptors_from_dag(root, bone_joints=bone_joints, bone_count=len(pmx.bones))
        expected_descriptors = build_descriptors_from_pmx(pmx.rigid_bodies, pmx.joints, pmx.bones)
        _assert_descriptor_semantics(test_case, actual_descriptors, expected_descriptors)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def assert_scene_reopen_contract(test_case: Any, pmx: Any, *, expect_physics: bool) -> str:
    """Save and reopen the current scene, then recover its model root."""

    scene_path = test_case.get_temp_filename("import_route_contract.ma")
    cmds.file(rename=scene_path)
    cmds.file(save=True, type="mayaAscii", force=True)
    cmds.file(new=True, force=True)
    cmds.file(scene_path, open=True, force=True)
    roots = [
        node
        for node in cmds.ls(type="transform", long=True) or []
        if cmds.attributeQuery(ATTR_MMD_MODEL_NAME, node=node, exists=True)
        and cmds.getAttr("{}.{}".format(node, ATTR_MMD_MODEL_NAME)) == pmx.header.model_name
    ]
    test_case.assertEqual(len(roots), 1, "saved scene model root")
    root = roots[0]
    test_case.assertEqual(cmds.getAttr("{}.{}".format(root, ATTR_MMD_MODEL_NAME_EN)), pmx.header.model_name_english)
    test_case.assertEqual(len(cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []), len(pmx.bones))
    assert_import_contract(test_case, root, pmx, expect_physics=expect_physics)
    return root
