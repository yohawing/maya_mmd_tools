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
            "|root|rb5|bulletRigidBodyShape.colliderShapeType": 3,
            "|root|rb2.mmd_rigid_body_index": 2,
            "|root|rb2.mmd_rigid_body_name": "skirt",
            "|root|rb2.mmd_related_bone_index": 9,
            "|root|rb2.mmd_physics_mode": 2,
            "|root|rb2|bulletRigidBodyShape.colliderShapeType": 1,
            "|root|jointB.mmd_joint_name": "jointB",
            "|root|jointB.mmd_joint_name_english": "jointB_en",
            "|root|jointB.mmd_joint_type": 4,
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

        self.assertEqual(len(refs.joints), 1)
        self.assertEqual(refs.joints[0].name, "jointB")
        self.assertEqual(refs.joints[0].joint_type, 4)
        self.assertEqual(refs.joints[0].rigid_body_a_index, 2)
        self.assertEqual(refs.joints[0].rigid_body_b_index, 5)


if __name__ == "__main__":
    unittest.main()
