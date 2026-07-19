"""Headless scene-lifecycle gates for PHS-2 authoring colliders."""

from __future__ import annotations

import math
from pathlib import Path

from maya import cmds
import maya.api.OpenMaya as om

from mmd_tools.core.collider_authoring import (
    connect_collider_authoring_follow,
    migrate_legacy_collider_authoring_pose,
    refresh_collider_authoring_pose,
    set_collider_authoring_pose,
)
from mmd_tools.ui.presenters.physics_presenter import PhysicsPresenter
from tests.common.maya_test_base import MayaTestBase


class TestColliderSceneLifecycle(MayaTestBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plugin = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
        if not cmds.pluginInfo(str(plugin), query=True, loaded=True):
            cmds.loadPlugin(str(plugin))

    def _model(self, root_name: str, *, bone_index: int = 7):
        root = cmds.createNode("transform", name=root_name)
        skeleton = cmds.createNode("transform", name="Skeleton", parent=root)
        bone = cmds.createNode("joint", name="sharedBone", parent=skeleton)
        cmds.addAttr(bone, longName="mmd_bone_index", attributeType="long")
        cmds.setAttr(f"{bone}.mmd_bone_index", bone_index)
        cmds.addAttr(bone, longName="mmd_bone_name", dataType="string")
        cmds.setAttr(f"{bone}.mmd_bone_name", "shared", type="string")
        physics = cmds.createNode("transform", name="Physics", parent=root)
        bodies = cmds.createNode("transform", name="RigidBodies", parent=physics)
        collider = cmds.createNode("transform", name="rb_0_shared", parent=bodies)
        shape = cmds.createNode("mmdRigidBodyShape", name="rb_0_sharedShape", parent=collider)
        cmds.setAttr(f"{shape}.pmxIndex", 0)
        cmds.setAttr(f"{shape}.relatedBoneIndex", bone_index)
        cmds.setAttr(f"{shape}.physicsMode", 2)
        cmds.setAttr(f"{shape}.shapeType", 1)
        cmds.setAttr(f"{shape}.shapeSize", 0.5, 1.0, 1.5, type="double3")
        position = (2.25, 4.5, -6.75)
        rotation = (0.15, -0.25, 0.35)
        set_collider_authoring_pose(collider, shape, position, rotation)
        cmds.connectAttr(f"{bone}.message", f"{shape}.relatedBone")
        connect_collider_authoring_follow(collider, shape)
        return tuple((cmds.ls(node, long=True) or [node])[0] for node in (root, bone, bodies, collider, shape))

    @staticmethod
    def _source(shape: str) -> list[str]:
        sources = cmds.listConnections(
            f"{shape}.relatedBone", source=True, destination=False, type="joint"
        ) or []
        return [long_name for node in sources for long_name in (cmds.ls(node, long=True) or [node])]

    @staticmethod
    def _matrix(node: str) -> om.MMatrix:
        return om.MMatrix(cmds.xform(node, query=True, worldSpace=True, matrix=True))

    def test_two_roots_nested_namespace_and_reopen_remain_root_scoped(self):
        root_a, bone_a, bodies_a, collider_a, shape_a = self._model("modelA", bone_index=7)
        cmds.namespace(add="outer")
        cmds.namespace(add="outer:inner")
        root_b, bone_b, bodies_b, collider_b, shape_b = self._model(
            "outer:inner:modelB", bone_index=7
        )

        presenter = PhysicsPresenter.__new__(PhysicsPresenter)
        presenter.view = None
        presenter._refresh_binding_candidates(root_a, bodies_a, publish=False)
        self.assertEqual([item[1] for item in presenter._bone_candidates], [bone_a])
        self.assertEqual([item[1] for item in presenter._rigid_body_candidates], [collider_a])
        presenter._refresh_binding_candidates(root_b, bodies_b, publish=False)
        self.assertEqual([item[1] for item in presenter._bone_candidates], [bone_b])
        self.assertEqual([item[1] for item in presenter._rigid_body_candidates], [collider_b])
        self.assertEqual(self._source(shape_a), [bone_a])
        self.assertEqual(self._source(shape_b), [bone_b])

        cmds.setKeyframe(bone_a, attribute="translate", time=0, value=0.0)
        cmds.setKeyframe(bone_a, attribute="translateX", time=12, value=3.0)
        cmds.setKeyframe(bone_b, attribute="translate", time=0, value=0.0)
        cmds.setKeyframe(bone_b, attribute="translateY", time=12, value=-4.0)
        expected = {}
        for label, bone, collider, shape in (
            ("a", bone_a, collider_a, shape_a),
            ("b", bone_b, collider_b, shape_b),
        ):
            offsets = []
            for frame in (0, 12):
                cmds.currentTime(frame)
                offsets.append(self._matrix(collider) * self._matrix(bone).inverse())
            self.assertLessEqual(max(abs(offsets[0][i] - offsets[1][i]) for i in range(16)), 1e-8)
            expected[label] = {
                "position": cmds.getAttr(f"{shape}.position")[0],
                "rotation": cmds.getAttr(f"{shape}.rotation")[0],
                "index": cmds.getAttr(f"{shape}.relatedBoneIndex"),
            }

        scene = self.get_temp_filename("collider_scene_lifecycle.ma")
        cmds.file(rename=scene)
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(scene, open=True, force=True)
        for label, root_leaf in (("a", "modelA"), ("b", "outer:inner:modelB")):
            root = (cmds.ls(root_leaf, long=True) or [])[0]
            shape = (cmds.listRelatives(root, allDescendents=True, fullPath=True, type="mmdRigidBodyShape") or [])[0]
            collider = (cmds.listRelatives(shape, parent=True, fullPath=True) or [])[0]
            bone = self._source(shape)[0]
            self.assertTrue(collider.startswith(root + "|"))
            self.assertTrue(bone.startswith(root + "|"))
            self.assertEqual(cmds.getAttr(f"{shape}.relatedBoneIndex"), expected[label]["index"])
            self.assertListAlmostEqual(cmds.getAttr(f"{shape}.position")[0], expected[label]["position"])
            self.assertListAlmostEqual(cmds.getAttr(f"{shape}.rotation")[0], expected[label]["rotation"])
            offsets = []
            for frame in (0, 12):
                cmds.currentTime(frame)
                offsets.append(self._matrix(collider) * self._matrix(bone).inverse())
            self.assertLessEqual(max(abs(offsets[0][i] - offsets[1][i]) for i in range(16)), 1e-8)

    def test_deleted_source_stays_unbound_with_fallback_and_safe_pose(self):
        root, bone, _bodies, collider, shape = self._model("deletedSourceModel", bone_index=13)
        cmds.setAttr(f"{bone}.translate", 2.0, -3.0, 4.0, type="double3")
        before_delete = self._matrix(collider)
        raw_position = cmds.getAttr(f"{shape}.position")[0]
        raw_rotation = cmds.getAttr(f"{shape}.rotation")[0]
        cmds.delete(bone)
        self.assertEqual(self._source(shape), [])
        self.assertEqual(cmds.getAttr(f"{shape}.relatedBoneIndex"), 13)
        refresh_collider_authoring_pose(collider, shape)
        self.assertEqual(self._source(shape), [])
        self.assertEqual(cmds.getAttr(f"{shape}.relatedBoneIndex"), 13)
        self.assertListAlmostEqual(cmds.getAttr(f"{shape}.position")[0], raw_position)
        self.assertListAlmostEqual(cmds.getAttr(f"{shape}.rotation")[0], raw_rotation)
        self.assertTrue(all(math.isfinite(self._matrix(collider)[i]) for i in range(16)))
        self.assertTrue(cmds.objExists(root))
        self.assertTrue(all(math.isfinite(before_delete[i]) for i in range(16)))

    def test_duplicate_root_never_follows_original_bone(self):
        root, bone, _bodies, _collider, _shape = self._model("originalModel")
        duplicated_root = (cmds.ls(cmds.duplicate(root, name="duplicatedModel")[0], long=True) or [])[0]
        duplicated_shape = (cmds.listRelatives(
            duplicated_root, allDescendents=True, fullPath=True, type="mmdRigidBodyShape"
        ) or [])[0]
        duplicated_collider = (cmds.listRelatives(duplicated_shape, parent=True, fullPath=True) or [])[0]
        connect_collider_authoring_follow(duplicated_collider, duplicated_shape)
        duplicated_sources = self._source(duplicated_shape)
        self.assertNotIn(bone, duplicated_sources)
        for source in duplicated_sources:
            self.assertTrue(source.startswith(duplicated_root + "|"))
        self.assertEqual(cmds.getAttr(f"{duplicated_shape}.relatedBoneIndex"), 7)
        self.assertEqual(self._source(_shape), [bone])

    def test_reference_refresh_is_read_only_and_reference_scoped(self):
        root, bone, _bodies, collider, shape = self._model("referenceSource")
        cmds.deleteAttr(f"{shape}.mmdColliderAuthoringPoseVersion")
        source_scene = self.get_temp_filename("collider_reference_source.ma")
        cmds.file(rename=source_scene)
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(new=True, force=True)
        cmds.file(source_scene, reference=True, namespace="assetRef")

        referenced_root = (cmds.ls("assetRef:referenceSource", long=True) or [])[0]
        referenced_shape = (cmds.listRelatives(
            referenced_root, allDescendents=True, fullPath=True, type="mmdRigidBodyShape"
        ) or [])[0]
        referenced_collider = (cmds.listRelatives(referenced_shape, parent=True, fullPath=True) or [])[0]
        referenced_bone = self._source(referenced_shape)[0]
        self.assertTrue(cmds.referenceQuery(referenced_shape, isNodeReferenced=True))
        self.assertTrue(referenced_bone.startswith(referenced_root + "|"))
        reference_node = cmds.referenceQuery(referenced_shape, referenceNode=True)
        before_nodes = set(cmds.ls(long=True) or [])
        before_matrix = self._matrix(referenced_collider)
        before_source = self._source(referenced_shape)
        before_edits = cmds.referenceQuery(reference_node, editStrings=True) or []
        self.assertFalse(
            migrate_legacy_collider_authoring_pose(referenced_collider, referenced_shape)
        )
        refresh_collider_authoring_pose(referenced_collider, referenced_shape)
        self.assertEqual(set(cmds.ls(long=True) or []), before_nodes)
        self.assertEqual(self._source(referenced_shape), before_source)
        self.assertEqual(cmds.referenceQuery(reference_node, editStrings=True) or [], before_edits)
        after_matrix = self._matrix(referenced_collider)
        self.assertLessEqual(max(abs(before_matrix[i] - after_matrix[i]) for i in range(16)), 1e-12)
