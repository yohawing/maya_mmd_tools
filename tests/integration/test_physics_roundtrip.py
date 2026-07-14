"""Integration test: PMX import → DAG scene → export collector → field parity.

Verifies that the full round-trip through the physics scene builder and
export collector preserves all rigid-body and joint fields from the
original PMX data.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from maya import cmds

from tests.common.maya_test_base import MayaTestBase

from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.converters.physics_scene_builder import build_physics_scene
from mmd_tools.converters.physics_export_collector import collect_physics_from_scene

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "physics" / "test_hair_physics.pmx"


def _create_minimal_joints(bones) -> list[str]:
    joints = []
    for i, bone in enumerate(bones):
        jnt = cmds.createNode("joint", name=f"bone_{i}")
        cmds.xform(jnt, worldSpace=True, translation=list(bone.position))
        joints.append(jnt)
    return joints


@unittest.skipUnless(FIXTURE_PATH.exists(), "hair physics fixture not found")
class TestPhysicsRoundTrip(MayaTestBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plugin_path = str(Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py")
        try:
            cmds.loadPlugin(plugin_path)
        except Exception:
            pass
        cls.pmx = parse_pmx_file(str(FIXTURE_PATH))

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()

    def _build_scene(self):
        root = cmds.group(empty=True, name="test_rt_root")
        maya_joints = _create_minimal_joints(self.pmx.bones)
        build_physics_scene(
            rigid_bodies=self.pmx.rigid_bodies,
            joints=self.pmx.joints,
            bones=self.pmx.bones,
            maya_joints=maya_joints,
            root_group=root,
        )
        bone_index_by_joint: dict[str, int] = {}
        for idx, jnt in enumerate(maya_joints):
            for long_name in cmds.ls(jnt, long=True) or []:
                bone_index_by_joint[long_name] = idx
            bone_index_by_joint[jnt.rsplit("|", 1)[-1]] = idx
        return root, maya_joints, bone_index_by_joint

    def test_rigid_body_count(self):
        root, _, bone_map = self._build_scene()
        rbs, _ = collect_physics_from_scene(root, bone_map)
        self.assertEqual(len(rbs), len(self.pmx.rigid_bodies))

    def test_joint_count(self):
        root, _, bone_map = self._build_scene()
        _, jts = collect_physics_from_scene(root, bone_map)
        self.assertEqual(len(jts), len(self.pmx.joints))

    def test_rigid_body_fields(self):
        """All rigid body fields survive the DAG round-trip."""
        root, _, bone_map = self._build_scene()
        rbs, _ = collect_physics_from_scene(root, bone_map)

        for i, (exported, original) in enumerate(zip(rbs, self.pmx.rigid_bodies)):
            with self.subTest(rigid_body=i):
                self.assertEqual(exported["name"], original.name, f"rb[{i}] name")
                self.assertEqual(exported["name_english"], original.name_english, f"rb[{i}] name_english")
                self.assertEqual(exported["related_bone_index"], original.related_bone_index, f"rb[{i}] related_bone_index")
                self.assertEqual(exported["group"], original.group, f"rb[{i}] group")
                self.assertEqual(exported["collision_mask"], original.collision_mask, f"rb[{i}] collision_mask")
                self.assertEqual(exported["shape_type"], original.shape_type, f"rb[{i}] shape_type")
                self.assertEqual(exported["physics_mode"], original.physics_mode, f"rb[{i}] physics_mode")

                for c, label in enumerate(("x", "y", "z")):
                    self.assertAlmostEqual(
                        exported["size"][c], original.size[c],
                        places=5, msg=f"rb[{i}] size.{label}",
                    )
                    self.assertAlmostEqual(
                        exported["position"][c], original.position[c],
                        places=5, msg=f"rb[{i}] position.{label}",
                    )
                    self.assertAlmostEqual(
                        exported["rotation"][c], original.rotation[c],
                        places=5, msg=f"rb[{i}] rotation.{label}",
                    )

                self.assertAlmostEqual(exported["mass"], original.mass, places=5, msg=f"rb[{i}] mass")
                self.assertAlmostEqual(exported["velocity_attenuation"], original.velocity_attenuation, places=5, msg=f"rb[{i}] velocity_attenuation")
                self.assertAlmostEqual(exported["rotation_attenuation"], original.rotation_attenuation, places=5, msg=f"rb[{i}] rotation_attenuation")
                self.assertAlmostEqual(exported["elasticity"], original.elasticity, places=5, msg=f"rb[{i}] elasticity")
                self.assertAlmostEqual(exported["friction"], original.friction, places=5, msg=f"rb[{i}] friction")

    def test_joint_fields(self):
        """All joint fields survive the DAG round-trip."""
        root, _, bone_map = self._build_scene()
        _, jts = collect_physics_from_scene(root, bone_map)

        for i, (exported, original) in enumerate(zip(jts, self.pmx.joints)):
            with self.subTest(joint=i):
                self.assertEqual(exported["name"], original.name, f"jt[{i}] name")
                self.assertEqual(exported["name_english"], original.name_english, f"jt[{i}] name_english")
                self.assertEqual(exported["joint_type"], original.joint_type, f"jt[{i}] joint_type")
                self.assertEqual(exported["rigid_body_a_index"], original.rigid_body_a_index, f"jt[{i}] rb_a")
                self.assertEqual(exported["rigid_body_b_index"], original.rigid_body_b_index, f"jt[{i}] rb_b")

                for c, label in enumerate(("x", "y", "z")):
                    self.assertAlmostEqual(
                        exported["position"][c], original.position[c],
                        places=5, msg=f"jt[{i}] position.{label}",
                    )
                    self.assertAlmostEqual(
                        exported["rotation"][c], original.rotation[c],
                        places=5, msg=f"jt[{i}] rotation.{label}",
                    )
                    self.assertAlmostEqual(
                        exported["translation_limit_min"][c], original.translation_limit_min[c],
                        places=5, msg=f"jt[{i}] trans_limit_min.{label}",
                    )
                    self.assertAlmostEqual(
                        exported["translation_limit_max"][c], original.translation_limit_max[c],
                        places=5, msg=f"jt[{i}] trans_limit_max.{label}",
                    )
                    self.assertAlmostEqual(
                        exported["rotation_limit_min"][c], original.rotation_limit_min[c],
                        places=5, msg=f"jt[{i}] rot_limit_min.{label}",
                    )
                    self.assertAlmostEqual(
                        exported["rotation_limit_max"][c], original.rotation_limit_max[c],
                        places=5, msg=f"jt[{i}] rot_limit_max.{label}",
                    )
                    self.assertAlmostEqual(
                        exported["spring_translation"][c], original.spring_translation[c],
                        places=5, msg=f"jt[{i}] spring_trans.{label}",
                    )
                    self.assertAlmostEqual(
                        exported["spring_rotation"][c], original.spring_rotation[c],
                        places=5, msg=f"jt[{i}] spring_rot.{label}",
                    )

    def test_source_payload_roundtrip(self):
        """PMX payload stored during import is readable via solver utility."""
        import base64
        from mmd_tools.core.constants import ATTR_MMD_SOURCE_PMX_PAYLOAD
        from mmd_tools.core.physics_solver import read_source_pmx_payload

        root, _, _ = self._build_scene()
        pmx_bytes = FIXTURE_PATH.read_bytes()
        encoded = base64.b64encode(pmx_bytes).decode("ascii")
        cmds.addAttr(root, longName=ATTR_MMD_SOURCE_PMX_PAYLOAD, dataType="string", hidden=True)
        cmds.setAttr(f"{root}.{ATTR_MMD_SOURCE_PMX_PAYLOAD}", encoded, type="string")

        recovered = read_source_pmx_payload(root)
        self.assertIsNotNone(recovered)
        self.assertEqual(len(recovered), len(pmx_bytes))
        self.assertEqual(recovered, pmx_bytes)


if __name__ == "__main__":
    unittest.main()
