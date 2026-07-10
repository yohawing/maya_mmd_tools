"""Native PMX parser JSON-to-PmxData builder contract tests."""

from ctypes import c_float, c_uint32
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mmd_tools.core.native.native_pmx_parser import (
    _build_joints,
    _build_morphs,
    _build_rigid_bodies,
    _build_vertices,
    _preserve_soft_bodies_from_legacy,
    parse_pmx_native,
)
from mmd_tools.core.pmx_data import PmxData
from mmd_tools.core.pmx_data.header import PmxEncoding
from mmd_tools.core.pmx_data.morph import PmxMorphType
from mmd_tools.core.pmx_data.soft_body import PmxSoftBody, _UNSUPPORTED_DETAIL_SIZE


def _rounded_uvs(uvs):
    return [tuple(round(component, 6) for component in uv) for uv in uvs]


def _make_minimal_pmx21_data():
    pmx_data = PmxData()
    pmx_data.header.version = 2.1
    pmx_data.header.encoding = PmxEncoding.UTF16LE
    pmx_data.header.vertex_index_size = 1
    pmx_data.header.texture_index_size = 1
    pmx_data.header.material_index_size = 1
    pmx_data.header.bone_index_size = 1
    pmx_data.header.morph_index_size = 1
    pmx_data.header.rigid_body_index_size = 1
    return pmx_data


def _make_test_soft_body():
    soft_body = PmxSoftBody(
        material_index_size=1,
        rigid_body_index_size=1,
        vertex_index_size=1,
        encoding_flag=PmxEncoding.UTF16LE,
    )
    soft_body.name = "布"
    soft_body.name_english = "cloth"
    soft_body.kind = 0
    soft_body.material_index = -1
    soft_body.collision_group = 3
    soft_body.collision_mask = 0x00F0
    soft_body.flags = 0x07
    soft_body.bending_constraints_distance = 4
    soft_body.cluster_count = 2
    soft_body.total_mass = 12.5
    soft_body.collision_margin = 0.25
    soft_body._unsupported_detail = bytes(range(_UNSUPPORTED_DETAIL_SIZE))
    soft_body.anchors = [(0, 5, 1)]
    soft_body.pins = [6, 7]
    return soft_body


class TestNativePmxParserBuilders(unittest.TestCase):
    def test_parse_pmx_native_preserves_soft_bodies_after_native_build(self):
        native_pmx = PmxData()
        lib = SimpleNamespace(mmd_runtime_parse_pmx_non_geometry_json=object())

        with patch("mmd_tools.core.native.mmd_anim_runtime.get_mmd_runtime_library", return_value=lib), patch(
            "mmd_tools.core.native.native_pmx_parser.Path.read_bytes", return_value=b"pmx"
        ), patch(
            "mmd_tools.core.native.native_pmx_parser._parse_pmx_bytes", return_value=native_pmx
        ) as parse_bytes, patch(
            "mmd_tools.core.native.native_pmx_parser._preserve_soft_bodies_from_legacy"
        ) as preserve:
            result = parse_pmx_native("cloth.pmx")

        self.assertIs(result, native_pmx)
        parse_bytes.assert_called_once_with(lib, b"pmx")
        preserve.assert_called_once_with("cloth.pmx", native_pmx)

    def test_parse_pmx_native_keeps_native_result_when_soft_body_preservation_fails(self):
        native_pmx = _make_minimal_pmx21_data()
        lib = SimpleNamespace(mmd_runtime_parse_pmx_non_geometry_json=object())

        with patch("mmd_tools.core.native.mmd_anim_runtime.get_mmd_runtime_library", return_value=lib), patch(
            "mmd_tools.core.native.native_pmx_parser.Path.read_bytes", return_value=b"pmx"
        ), patch(
            "mmd_tools.core.native.native_pmx_parser._parse_pmx_bytes", return_value=native_pmx
        ), patch(
            "mmd_tools.core.native.native_pmx_parser._preserve_soft_bodies_from_legacy", return_value=False
        ):
            self.assertIs(parse_pmx_native("cloth.pmx"), native_pmx)

    def test_parse_pmx_native_failure_returns_none_and_logs_fallback_at_debug(self):
        """Optional native failure detail is DEBUG-only; caller falls back."""
        from mmd_tools.core.native import native_pmx_parser as native_mod

        lib = SimpleNamespace(mmd_runtime_parse_pmx_non_geometry_json=object())

        with patch("mmd_tools.core.native.mmd_anim_runtime.get_mmd_runtime_library", return_value=lib), patch(
            "mmd_tools.core.native.native_pmx_parser.Path.read_bytes", return_value=b"pmx"
        ), patch(
            "mmd_tools.core.native.native_pmx_parser._parse_pmx_bytes",
            side_effect=RuntimeError("native boom"),
        ), patch.object(native_mod, "logger") as mock_logger:
            result = parse_pmx_native("broken.pmx")

        self.assertIsNone(result)
        # call[0] is args tuple (Py3.7-safe; _Call.args is 3.8+)
        debug_messages = [call[0][0] for call in mock_logger.debug.call_args_list if call[0]]
        info_messages = [call[0][0] for call in mock_logger.info.call_args_list if call[0]]
        expected = "Native PMX parse failed, will fallback: %s"
        self.assertIn(expected, debug_messages)
        self.assertNotIn(expected, info_messages)

    def test_parse_pmx_native_soft_body_copy_survives_write_reparse(self):
        native_pmx = _make_minimal_pmx21_data()
        legacy_pmx = _make_minimal_pmx21_data()
        legacy_pmx.soft_bodies = [_make_test_soft_body()]
        lib = SimpleNamespace(mmd_runtime_parse_pmx_non_geometry_json=object())

        with patch("mmd_tools.core.native.mmd_anim_runtime.get_mmd_runtime_library", return_value=lib), patch(
            "mmd_tools.core.native.native_pmx_parser.Path.read_bytes", return_value=b"pmx"
        ), patch(
            "mmd_tools.core.native.native_pmx_parser._parse_pmx_bytes", return_value=native_pmx
        ), patch(
            "mmd_tools.core.pmx_data.legacy_parser.parse_pmx_file_legacy", return_value=legacy_pmx
        ):
            parsed_native = parse_pmx_native("cloth.pmx")

        self.assertIs(parsed_native, native_pmx)
        with TemporaryDirectory() as temp_dir:
            out_path = Path(temp_dir) / "roundtrip.pmx"
            parsed_native.write_file(str(out_path))
            reparsed = PmxData().parse_file(str(out_path))

        self.assertEqual(len(reparsed.soft_bodies), 1)
        reparsed_soft_body = reparsed.soft_bodies[0]
        self.assertEqual(reparsed_soft_body.name, "布")
        self.assertAlmostEqual(reparsed_soft_body.total_mass, 12.5)
        self.assertAlmostEqual(reparsed_soft_body.collision_margin, 0.25)
        self.assertEqual(reparsed_soft_body._unsupported_detail, bytes(range(_UNSUPPORTED_DETAIL_SIZE)))
        self.assertEqual(reparsed_soft_body.anchors, [(0, 5, 1)])
        self.assertEqual(reparsed_soft_body.pins, [6, 7])

    def test_preserve_soft_bodies_from_legacy_for_pmx21_native_result(self):
        native_pmx = _make_minimal_pmx21_data()
        legacy_pmx = _make_minimal_pmx21_data()
        soft_body = _make_test_soft_body()
        legacy_pmx.soft_bodies = [soft_body]

        with patch(
            "mmd_tools.core.pmx_data.legacy_parser.parse_pmx_file_legacy",
            return_value=legacy_pmx,
        ) as parse_legacy:
            result = _preserve_soft_bodies_from_legacy("cloth.pmx", native_pmx)

        self.assertTrue(result)
        parse_legacy.assert_called_once_with("cloth.pmx")
        self.assertEqual(native_pmx.soft_bodies, [soft_body])

    def test_preserve_soft_bodies_from_legacy_does_not_overwrite_existing_native_data(self):
        native_pmx = PmxData()
        native_pmx.header.version = 2.1
        native_soft_body = object()
        native_pmx.soft_bodies = [native_soft_body]

        with patch("mmd_tools.core.pmx_data.legacy_parser.parse_pmx_file_legacy") as parse_legacy:
            result = _preserve_soft_bodies_from_legacy("cloth.pmx", native_pmx)

        self.assertTrue(result)
        parse_legacy.assert_not_called()
        self.assertEqual(native_pmx.soft_bodies, [native_soft_body])

    def test_preserve_soft_bodies_from_legacy_skips_pmx20(self):
        native_pmx = PmxData()
        native_pmx.header.version = 2.0

        with patch("mmd_tools.core.pmx_data.legacy_parser.parse_pmx_file_legacy") as parse_legacy:
            result = _preserve_soft_bodies_from_legacy("model.pmx", native_pmx)

        self.assertTrue(result)
        parse_legacy.assert_not_called()

    def test_build_vertices_preserves_additional_uv_channels(self):
        positions = (c_float * 6)(0.0, 1.0, 2.0, 3.0, 4.0, 5.0)
        normals = (c_float * 6)(0.0, 0.0, 1.0, 0.0, 1.0, 0.0)
        uvs = (c_float * 4)(0.1, 0.2, 0.3, 0.4)
        edges = (c_float * 2)(1.0, 2.0)
        skin_indices = (c_uint32 * 8)(0, 1, 2, 3, 4, 5, 6, 7)
        skin_weights = (c_float * 8)(1.0, 0.0, 0.0, 0.0, 0.25, 0.25, 0.25, 0.25)
        additional_uv0 = (c_float * 8)(1.0, 1.1, 1.2, 1.3, 2.0, 2.1, 2.2, 2.3)
        additional_uv1 = (c_float * 8)(3.0, 3.1, 3.2, 3.3, 4.0, 4.1, 4.2, 4.3)

        vertices = _build_vertices(
            2,
            positions,
            normals,
            uvs,
            edges,
            skin_indices,
            skin_weights,
            ["bdef1", "bdef4"],
            None,
            None,
            None,
            [additional_uv0, additional_uv1],
        )

        self.assertEqual(vertices[0].additional_uv_count, 2)
        self.assertEqual(_rounded_uvs(vertices[0].additional_uvs), [(1.0, 1.1, 1.2, 1.3), (3.0, 3.1, 3.2, 3.3)])
        self.assertEqual(vertices[1].additional_uv_count, 2)
        self.assertEqual(_rounded_uvs(vertices[1].additional_uvs), [(2.0, 2.1, 2.2, 2.3), (4.0, 4.1, 4.2, 4.3)])

    def test_build_rigid_body_accepts_mmd_anim_json_names(self):
        bodies = _build_rigid_bodies(
            [
                {
                    "name": "rb",
                    "englishName": "rb-en",
                    "boneIndex": 3,
                    "group": 2,
                    "mask": 0xFFFE,
                    "shape": "capsule",
                    "size": [1.0, 2.0, 3.0],
                    "position": [4.0, 5.0, 6.0],
                    "rotation": [0.1, 0.2, 0.3],
                    "mass": 7.0,
                    "linearDamping": 0.4,
                    "angularDamping": 0.5,
                    "restitution": 0.6,
                    "friction": 0.7,
                    "mode": "dynamicBone",
                }
            ]
        )

        body = bodies[0]
        self.assertEqual(body.related_bone_index, 3)
        self.assertEqual(body.collision_mask, 0xFFFE)
        self.assertEqual(body.shape_type, 2)
        self.assertEqual(body.physics_mode, 2)
        self.assertEqual(body.velocity_attenuation, 0.4)
        self.assertEqual(body.rotation_attenuation, 0.5)

    def test_build_joint_accepts_mmd_anim_json_names(self):
        joints = _build_joints(
            [
                {
                    "name": "joint",
                    "englishName": "joint-en",
                    "type": "point2point",
                    "rigidBodyIndexA": 4,
                    "rigidBodyIndexB": 5,
                    "position": [1.0, 2.0, 3.0],
                    "rotation": [0.1, 0.2, 0.3],
                    "translationLowerLimit": [-1.0, -2.0, -3.0],
                    "translationUpperLimit": [1.0, 2.0, 3.0],
                    "rotationLowerLimit": [-0.1, -0.2, -0.3],
                    "rotationUpperLimit": [0.1, 0.2, 0.3],
                    "springTranslationFactor": [0.4, 0.5, 0.6],
                    "springRotationFactor": [0.7, 0.8, 0.9],
                }
            ]
        )

        joint = joints[0]
        self.assertEqual(joint.joint_type, 2)
        self.assertEqual(joint.rigid_body_a_index, 4)
        self.assertEqual(joint.rigid_body_b_index, 5)
        self.assertEqual(joint.translation_limit_min, (-1.0, -2.0, -3.0))
        self.assertEqual(joint.translation_limit_max, (1.0, 2.0, 3.0))
        self.assertEqual(joint.rotation_limit_min, (-0.1, -0.2, -0.3))
        self.assertEqual(joint.rotation_limit_max, (0.1, 0.2, 0.3))
        self.assertEqual(joint.spring_translation, (0.4, 0.5, 0.6))
        self.assertEqual(joint.spring_rotation, (0.7, 0.8, 0.9))

    def test_build_morphs_accepts_mmd_anim_json_names(self):
        morphs = _build_morphs(
            [
                {
                    "name": "group",
                    "englishName": "group-en",
                    "type": "group",
                    "groupOffsets": [{"morphIndex": 1, "weight": 0.25}],
                },
                {
                    "name": "addUv",
                    "englishName": "addUv-en",
                    "type": "additionalUv",
                    "additionalUvOffsets": [{"vertexIndex": 2, "uvIndex": 2, "uv": [0.1, 0.2, 0.3, 0.4]}],
                },
                {
                    "name": "material",
                    "englishName": "material-en",
                    "type": "material",
                    "materialOffsets": [
                        {
                            "materialIndex": 3,
                            "operation": "add",
                            "diffuse": [0.1, 0.2, 0.3, 0.4],
                            "specular": [0.5, 0.6, 0.7],
                            "specularPower": 0.8,
                            "ambient": [0.9, 1.0, 1.1],
                            "edgeColor": [1.2, 1.3, 1.4, 1.5],
                            "edgeSize": 1.6,
                            "textureFactor": [1.7, 1.8, 1.9, 2.0],
                            "sphereTextureFactor": [2.1, 2.2, 2.3, 2.4],
                            "toonTextureFactor": [2.5, 2.6, 2.7, 2.8],
                        }
                    ],
                },
                {
                    "name": "flip",
                    "englishName": "flip-en",
                    "type": "flip",
                    "flipOffsets": [{"morphIndex": 4, "weight": 0.5}],
                },
                {
                    "name": "impulse",
                    "englishName": "impulse-en",
                    "type": "impulse",
                    "impulseOffsets": [
                        {
                            "rigidBodyIndex": 5,
                            "local": True,
                            "velocity": [0.1, 0.2, 0.3],
                            "torque": [0.4, 0.5, 0.6],
                        }
                    ],
                },
            ]
        )

        self.assertEqual(morphs[0].offsets[0]["morph_rate"], 0.25)
        self.assertEqual(morphs[1].morph_type, PmxMorphType.AdditionalUVMorph3)
        self.assertEqual(morphs[1].offsets[0]["uv_offset"], (0.1, 0.2, 0.3, 0.4))
        self.assertEqual(morphs[2].offsets[0]["operation_type"], 1)
        self.assertEqual(morphs[2].offsets[0]["texture_factor"], (1.7, 1.8, 1.9, 2.0))
        self.assertEqual(morphs[2].offsets[0]["sphere_texture_factor"], (2.1, 2.2, 2.3, 2.4))
        self.assertEqual(morphs[2].offsets[0]["toon_texture_factor"], (2.5, 2.6, 2.7, 2.8))
        self.assertEqual(morphs[3].offsets[0]["flip_rate"], 0.5)
        self.assertEqual(morphs[4].offsets[0]["is_local"], 1)
        self.assertEqual(morphs[4].offsets[0]["impulse"], (0.1, 0.2, 0.3))
        self.assertEqual(morphs[4].offsets[0]["torque"], (0.4, 0.5, 0.6))


if __name__ == "__main__":
    unittest.main()
