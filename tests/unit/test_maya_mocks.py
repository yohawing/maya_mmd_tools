"""
Maya mockの動作確認テスト
"""

import unittest
import sys
from tests.common.maya_mock import MayaMockSetup, create_mock_joint_hierarchy, create_mock_mesh


class TestMayaMocks(unittest.TestCase):
    """Maya mockの動作を確認するテストクラス"""
    
    @classmethod
    def setUpClass(cls):
        """テストクラスのセットアップ"""
        cls.maya, cls.cmds, cls.om = MayaMockSetup.setup_maya_mocks()
    
    @classmethod
    def tearDownClass(cls):
        """テストクラスのクリーンアップ"""
        MayaMockSetup.teardown_maya_mocks()
    
    def setUp(self):
        """各テストのセットアップ"""
        # シーンをリセット
        self.cmds.reset()
    
    def test_joint_creation(self):
        """ジョイント作成のテスト"""
        # ジョイントを作成
        joint_name = self.cmds.joint(name="test_joint", position=(1, 2, 3))
        
        # 確認
        self.assertEqual(joint_name, "test_joint")
        self.assertTrue(self.cmds.objExists("test_joint"))
        self.assertEqual(self.cmds.nodeType("test_joint"), "joint")
        
        # 位置を確認
        self.assertEqual(self.cmds.getAttr("test_joint.translateX"), 1)
        self.assertEqual(self.cmds.getAttr("test_joint.translateY"), 2)
        self.assertEqual(self.cmds.getAttr("test_joint.translateZ"), 3)
    
    def test_joint_hierarchy(self):
        """ジョイント階層作成のテスト"""
        # ヘルパー関数を使用して階層を作成
        hierarchy = create_mock_joint_hierarchy(["root", "spine", "chest", "neck", "head"])
        
        # 階層を確認
        joints = self.cmds.ls(type="joint")
        self.assertEqual(len(joints), 5)
        
        # 親子関係を確認
        self.assertIsNone(hierarchy["root"])
        self.assertEqual(hierarchy["spine"], "root")
        self.assertEqual(hierarchy["chest"], "spine")
        self.assertEqual(hierarchy["neck"], "chest")
        self.assertEqual(hierarchy["head"], "neck")
    
    def test_mesh_creation(self):
        """メッシュ作成のテスト"""
        # メッシュを作成
        mesh, shape = self.cmds.polyCube(name="test_cube")
        
        # 確認
        self.assertEqual(mesh, "test_cube")
        self.assertEqual(shape, "test_cubeShape")
        self.assertTrue(self.cmds.objExists("test_cube"))
        self.assertEqual(self.cmds.nodeType("test_cube"), "mesh")
    
    def test_attribute_operations(self):
        """アトリビュート操作のテスト"""
        # オブジェクトを作成
        joint = self.cmds.joint(name="test_joint")
        
        # アトリビュートを設定
        self.cmds.setAttr("test_joint.translateX", 5.0)
        self.cmds.setAttr("test_joint.rotateY", 45.0)
        self.cmds.setAttr("test_joint.scaleZ", 2.0)
        
        # 値を確認
        self.assertEqual(self.cmds.getAttr("test_joint.translateX"), 5.0)
        self.assertEqual(self.cmds.getAttr("test_joint.rotateY"), 45.0)
        self.assertEqual(self.cmds.getAttr("test_joint.scaleZ"), 2.0)
    
    def test_animation_keyframes(self):
        """アニメーションキーフレームのテスト"""
        # ジョイントを作成
        joint = self.cmds.joint(name="anim_joint")
        
        # キーフレームを設定
        self.cmds.currentTime(0)
        self.cmds.setKeyframe(joint, attribute="translateX", value=0.0)
        
        self.cmds.currentTime(10)
        self.cmds.setKeyframe(joint, attribute="translateX", value=10.0)
        
        self.cmds.currentTime(20)
        self.cmds.setKeyframe(joint, attribute="translateX", value=5.0)
        
        # キーフレームが記録されていることを確認
        self.assertIn(joint, self.cmds._keyframes)
        self.assertIn("translateX", self.cmds._keyframes[joint])
        self.assertEqual(len(self.cmds._keyframes[joint]["translateX"]), 3)
    
    def test_selection(self):
        """選択操作のテスト"""
        # オブジェクトを作成
        joint1 = self.cmds.joint(name="joint1")
        joint2 = self.cmds.joint(name="joint2")
        joint3 = self.cmds.joint(name="joint3")
        
        # 選択
        self.cmds.select(joint1, joint2)
        selected = self.cmds.ls(selection=True)
        self.assertEqual(selected, ["joint1", "joint2"])
        
        # 追加選択
        self.cmds.select(joint3, add=True)
        selected = self.cmds.ls(selection=True)
        self.assertEqual(selected, ["joint1", "joint2", "joint3"])
        
        # クリア
        self.cmds.select(clear=True)
        selected = self.cmds.ls(selection=True)
        self.assertEqual(selected, [])
    
    def test_group_operations(self):
        """グループ操作のテスト"""
        # オブジェクトを作成
        joint1 = self.cmds.joint(name="joint1")
        joint2 = self.cmds.joint(name="joint2")
        
        # グループ化
        group = self.cmds.group(joint1, joint2, name="test_group")
        
        # 確認
        self.assertEqual(group, "test_group")
        self.assertTrue(self.cmds.objExists("test_group"))
        self.assertEqual(self.cmds.nodeType("test_group"), "transform")
        
        # 親子関係を確認
        self.assertEqual(self.cmds._scene_objects[joint1]["parent"], "test_group")
        self.assertEqual(self.cmds._scene_objects[joint2]["parent"], "test_group")
    
    def test_playback_options(self):
        """プレイバックオプションのテスト"""
        # 設定
        self.cmds.playbackOptions(minTime=1, maxTime=100, fps=24)
        
        # 取得して確認
        options = self.cmds.playbackOptions()
        self.assertEqual(options["minTime"], 1)
        self.assertEqual(options["maxTime"], 100)
        self.assertEqual(options["fps"], 24)
    
    def test_openmaya_vector(self):
        """OpenMaya MVectorのテスト"""
        # ベクトルを作成
        vec = self.om.MVector(1.0, 2.0, 3.0)
        
        # 値を確認
        self.assertEqual(vec.x, 1.0)
        self.assertEqual(vec.y, 2.0)
        self.assertEqual(vec.z, 3.0)
    
    def test_openmaya_quaternion(self):
        """OpenMaya MQuaternionのテスト"""
        # クォータニオンを作成
        quat = self.om.MQuaternion(0.0, 0.0, 0.707, 0.707)
        
        # 値を確認
        self.assertEqual(quat.x, 0.0)
        self.assertEqual(quat.y, 0.0)
        self.assertEqual(quat.z, 0.707)
        self.assertEqual(quat.w, 0.707)
        
        # オイラー角への変換をテスト
        euler = quat.asEulerRotation()
        self.assertIsInstance(euler, self.om.MEulerRotation)
    
    def test_parent_child_operations(self):
        """親子関係操作の詳細テスト"""
        # ジョイントを作成
        parent_joint = self.cmds.joint(name="parent")
        child1 = self.cmds.joint(name="child1")
        child2 = self.cmds.joint(name="child2")
        
        # 親子関係を設定
        self.cmds.parent(child1, parent_joint)
        self.cmds.parent(child2, parent_joint)
        
        # 確認
        self.assertEqual(self.cmds._scene_objects[child1]["parent"], "parent")
        self.assertEqual(self.cmds._scene_objects[child2]["parent"], "parent")
        self.assertIn("child1", self.cmds._scene_objects[parent_joint]["children"])
        self.assertIn("child2", self.cmds._scene_objects[parent_joint]["children"])
        
        # 親を変更
        new_parent = self.cmds.joint(name="new_parent")
        self.cmds.parent(child1, new_parent)
        
        # 新しい親子関係を確認
        self.assertEqual(self.cmds._scene_objects[child1]["parent"], "new_parent")
        self.assertNotIn("child1", self.cmds._scene_objects[parent_joint]["children"])
        self.assertIn("child1", self.cmds._scene_objects[new_parent]["children"])


if __name__ == "__main__":
    unittest.main()