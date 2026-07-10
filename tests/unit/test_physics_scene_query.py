"""Physics scene metadata readerのMaya非依存ロジックを検証するテスト。"""

import unittest

from mmd_tools.core.physics_scene_query import MayaPhysicsSceneReader


class _FakeMayaAdapter:
    def __init__(self):
        self.children = {
            "|root": ["|root|joints", "|root|rb5", "|root|rb2", "|root|jointB"],
            "|other": ["|other|rb9"],
        }
        self.shapes = {
            ("|root|rb5", "bulletRigidBodyShape"): ["|root|rb5|bulletRigidBodyShape"],
            ("|root|rb5", "mmdRigidBodyLocator"): ["|root|rb5|mmdRigidBodyLocator"],
            ("|root|rb2", "bulletRigidBodyShape"): ["|root|rb2|bulletRigidBodyShape"],
            ("|root|jointB", "bulletRigidBodyConstraintShape"): ["|root|jointB|bulletRigidBodyConstraintShape"],
            ("|other|rb9", "bulletRigidBodyShape"): ["|other|rb9|bulletRigidBodyShape"],
        }
        self.parents = {
            "|root|rb5|bulletRigidBodyShape": ["|root|rb5"],
            "|root|rb2|bulletRigidBodyShape": ["|root|rb2"],
        }
        self.attrs = {
            "|root|rb5.mmd_rigid_body_index": 5,
            "|root|rb5.mmd_rigid_body_name": "hair",
            "|root|rb5.mmd_rigid_body_name_english": "hair_en",
            "|root|rb5.mmd_related_bone_index": 3,
            "|root|rb5.mmd_physics_mode": 1,
            "|root|rb5.mmd_collision_group": 7,
            "|root|rb5.mmd_collision_mask": 0xFF7F,
            "|root|rb5|bulletRigidBodyShape.colliderShapeType": 3,
            "|root|rb5|bulletRigidBodyShape.mass": 2.5,
            "|root|rb5|bulletRigidBodyShape.linearDamping": 0.15,
            "|root|rb5|bulletRigidBodyShape.angularDamping": 0.25,
            "|root|rb5|bulletRigidBodyShape.restitution": 0.35,
            "|root|rb5|bulletRigidBodyShape.friction": 0.45,
            "|root|rb2.mmd_rigid_body_index": 2,
            "|root|rb2.mmd_rigid_body_name": "skirt",
            "|root|rb2.mmd_related_bone_index": 9,
            "|root|rb2.mmd_physics_mode": 2,
            "|root|rb2|bulletRigidBodyShape.colliderShapeType": 1,
            "|root|rb2.mmd_collision_group": "invalid",
            "|root|rb2|bulletRigidBodyShape.mass": float("nan"),
            "|root|rb2|bulletRigidBodyShape.linearDamping": float("inf"),
            "|root|jointB.mmd_joint_name": "jointB",
            "|root|jointB.mmd_joint_name_english": "jointB_en",
            "|root|jointB.mmd_joint_type": 4,
            "|root|jointB.mmd_joint_is_pmx": 1,
            "|root|jointB|bulletRigidBodyConstraintShape.linearConstraintX": 0,
            "|root|jointB|bulletRigidBodyConstraintShape.linearConstraintY": 1,
            "|root|jointB|bulletRigidBodyConstraintShape.linearConstraintZ": 2,
            "|root|jointB|bulletRigidBodyConstraintShape.angularConstraintX": 2,
            "|root|jointB|bulletRigidBodyConstraintShape.angularConstraintY": 1,
            "|root|jointB|bulletRigidBodyConstraintShape.angularConstraintZ": 0,
            "|root|jointB|bulletRigidBodyConstraintShape.linearConstraintMinX": -1.0,
            "|root|jointB|bulletRigidBodyConstraintShape.linearConstraintMinY": -2.0,
            "|root|jointB|bulletRigidBodyConstraintShape.linearConstraintMinZ": -3.0,
            "|root|jointB|bulletRigidBodyConstraintShape.linearConstraintMaxX": 1.0,
            "|root|jointB|bulletRigidBodyConstraintShape.linearConstraintMaxY": 2.0,
            "|root|jointB|bulletRigidBodyConstraintShape.linearConstraintMaxZ": 3.0,
            "|root|jointB|bulletRigidBodyConstraintShape.angularConstraintMinX": -10.0,
            "|root|jointB|bulletRigidBodyConstraintShape.angularConstraintMinY": -20.0,
            "|root|jointB|bulletRigidBodyConstraintShape.angularConstraintMinZ": -30.0,
            "|root|jointB|bulletRigidBodyConstraintShape.angularConstraintMaxX": 10.0,
            "|root|jointB|bulletRigidBodyConstraintShape.angularConstraintMaxY": 20.0,
            "|root|jointB|bulletRigidBodyConstraintShape.angularConstraintMaxZ": 30.0,
            "|root|jointB|bulletRigidBodyConstraintShape.linearSpringStiffnessX": 0.1,
            "|root|jointB|bulletRigidBodyConstraintShape.linearSpringStiffnessY": 0.2,
            "|root|jointB|bulletRigidBodyConstraintShape.linearSpringStiffnessZ": 0.3,
            "|root|jointB|bulletRigidBodyConstraintShape.angularSpringStiffnessX": 0.4,
            "|root|jointB|bulletRigidBodyConstraintShape.angularSpringStiffnessY": "-inf",
            "|root|jointB|bulletRigidBodyConstraintShape.angularSpringStiffnessZ": 0.6,
            "|root|jointB|bulletRigidBodyConstraintShape.linearSpringEnabledX": True,
            "|root|jointB|bulletRigidBodyConstraintShape.linearSpringEnabledY": 0,
            "|root|jointB|bulletRigidBodyConstraintShape.linearSpringEnabledZ": "invalid",
            "|root|jointB|bulletRigidBodyConstraintShape.angularSpringEnabledX": "true",
            "|root|jointB|bulletRigidBodyConstraintShape.angularSpringEnabledY": "false",
            "|root|jointB|bulletRigidBodyConstraintShape.angularSpringEnabledZ": 1,
            "|other|rb9.mmd_rigid_body_index": 9,
            "|other|rb9.mmd_rigid_body_name": "outside",
            "|other|rb9|bulletRigidBodyShape.colliderShapeType": 2,
        }
        self.connections = {
            "|root|jointB|bulletRigidBodyConstraintShape.rigidBodyA": [
                "|root|rb2|bulletRigidBodyShape.outRigidBodyData"
            ],
            "|root|jointB|bulletRigidBodyConstraintShape.rigidBodyB": [
                "|root|rb5|bulletRigidBodyShape.outRigidBodyData"
            ],
        }

    def ls(self, node, long=True):
        return [node] if long and node in self.children else []

    def list_relatives(self, node, **kwargs):
        if kwargs.get("parent"):
            return self.parents.get(node, [])
        if kwargs.get("shapes"):
            return self.shapes.get((node, kwargs.get("type")), [])
        if kwargs.get("allDescendents") and kwargs.get("type") == "transform":
            return self.children.get(node, [])
        return []

    def attribute_exists(self, attr, node):
        return f"{node}.{attr}" in self.attrs

    def get_attr(self, attr_path):
        return self.attrs[attr_path]

    def list_connections(self, node, **_kwargs):
        return self.connections.get(node, [])

    def node_type(self, node):
        if node.endswith("bulletRigidBodyShape"):
            return "bulletRigidBodyShape"
        return "transform"


class TestMayaPhysicsSceneReader(unittest.TestCase):
    def test_collect_reads_root_scoped_bullet_metadata(self):
        reader = MayaPhysicsSceneReader(_FakeMayaAdapter())

        refs = reader.collect("|root")

        self.assertEqual([rigid.index for rigid in refs.rigid_bodies], [2, 5])
        self.assertEqual([rigid.name for rigid in refs.rigid_bodies], ["skirt", "hair"])
        self.assertEqual([rigid.shape_type for rigid in refs.rigid_bodies], [1, 2])
        self.assertEqual([rigid.physics_mode for rigid in refs.rigid_bodies], [2, 1])
        self.assertEqual([rigid.related_bone_index for rigid in refs.rigid_bodies], [9, 3])
        self.assertEqual([rigid.locator_shape for rigid in refs.rigid_bodies], [None, "|root|rb5|mmdRigidBodyLocator"])
        self.assertEqual(refs.rigid_bodies[0].collision_group, 0)
        self.assertEqual(refs.rigid_bodies[0].collision_mask, 0xFFFF)
        self.assertEqual(refs.rigid_bodies[0].mass, 0.0)
        self.assertEqual(refs.rigid_bodies[0].linear_damping, 0.0)
        self.assertEqual(refs.rigid_bodies[0].angular_damping, 0.0)
        self.assertEqual(refs.rigid_bodies[0].restitution, 0.0)
        self.assertEqual(refs.rigid_bodies[0].friction, 0.0)
        self.assertEqual(refs.rigid_bodies[1].collision_group, 7)
        self.assertEqual(refs.rigid_bodies[1].collision_mask, 0xFF7F)
        self.assertEqual(refs.rigid_bodies[1].mass, 2.5)
        self.assertEqual(refs.rigid_bodies[1].linear_damping, 0.15)
        self.assertEqual(refs.rigid_bodies[1].angular_damping, 0.25)
        self.assertEqual(refs.rigid_bodies[1].restitution, 0.35)
        self.assertEqual(refs.rigid_bodies[1].friction, 0.45)

        self.assertEqual(len(refs.joints), 1)
        joint = refs.joints[0]
        self.assertEqual(joint.name, "jointB")
        self.assertEqual(joint.joint_type, 4)
        self.assertEqual(joint.rigid_body_a_index, 2)
        self.assertEqual(joint.rigid_body_b_index, 5)
        self.assertTrue(joint.is_pmx)
        self.assertEqual(joint.linear_constraint_states, (0, 1, 2))
        self.assertEqual(joint.angular_constraint_states, (2, 1, 0))
        self.assertEqual(joint.translation_limit_min, (-1.0, -2.0, -3.0))
        self.assertEqual(joint.translation_limit_max, (1.0, 2.0, 3.0))
        self.assertEqual(joint.rotation_limit_min_degrees, (-10.0, -20.0, -30.0))
        self.assertEqual(joint.rotation_limit_max_degrees, (10.0, 20.0, 30.0))
        self.assertEqual(joint.spring_translation, (0.1, 0.2, 0.3))
        self.assertEqual(joint.spring_rotation, (0.4, 0.0, 0.6))
        self.assertEqual(joint.spring_translation_enabled, (True, False, False))
        self.assertEqual(joint.spring_rotation_enabled, (True, False, True))


if __name__ == "__main__":
    unittest.main()
