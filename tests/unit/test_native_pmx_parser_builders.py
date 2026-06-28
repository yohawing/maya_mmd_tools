"""Native PMX parser JSON-to-PmxData builder contract tests."""

from ctypes import c_float, c_uint32
import unittest

from mmd_tools.core.native.native_pmx_parser import (
    _build_joints,
    _build_morphs,
    _build_rigid_bodies,
    _build_vertices,
)
from mmd_tools.core.pmx_data.morph import PmxMorphType


def _rounded_uvs(uvs):
    return [tuple(round(component, 6) for component in uv) for uv in uvs]


class TestNativePmxParserBuilders(unittest.TestCase):
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
