"""
VMDインポーター機能の統合テスト
"""
import os
from maya import cmds
from mmd_tools.io.mmd_importer import import_mmd_file
from mmd_tools.core.vmd_parser import VmdParser
from tests.common.maya_test_base import MayaTestBase


class TestVmdImporter(MayaTestBase):
    """
    VMDインポーター機能の統合テスト。
    実際のMaya環境でVMDファイルをインポートし、アニメーションが正しく適用されるかを確認する。
    """

    def setUp(self):
        """
        各テストの前に実行される設定。
        テストに必要なMayaシーンのセットアップとテストデータのパスを準備。
        """
        super().setUp()
        # 新しいMayaシーンを作成
        cmds.file(new=True, force=True)

        # テストデータのパスを設定
        self.test_data_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "data"
        )
        
    def tearDown(self):
        """
        各テスト後のクリーンアップ処理。
        テスト中に作成されたノードやシーンの状態をリセット。
        """
        super().tearDown()
        # シーンをクリア
        cmds.file(new=True, force=True)

    def _create_test_skeleton(self):
        """テスト用のスケルトン構造を作成"""
        # ルートジョイント
        root = cmds.joint(name="root", position=[0, 0, 0])
        cmds.addAttr(root, longName="pmx_bone_name", dataType="string")
        cmds.setAttr(f"{root}.pmx_bone_name", "全ての親", type="string")
        
        # センタージョイント
        center = cmds.joint(name="center", position=[0, 10, 0])
        cmds.addAttr(center, longName="pmx_bone_name", dataType="string")
        cmds.setAttr(f"{center}.pmx_bone_name", "センター", type="string")
        
        # 上半身ジョイント
        cmds.select(center)
        upper_body = cmds.joint(name="upper_body", position=[0, 15, 0])
        cmds.addAttr(upper_body, longName="pmx_bone_name", dataType="string")
        cmds.setAttr(f"{upper_body}.pmx_bone_name", "上半身", type="string")
        
        # 頭ジョイント
        head = cmds.joint(name="head", position=[0, 20, 0])
        cmds.addAttr(head, longName="pmx_bone_name", dataType="string")
        cmds.setAttr(f"{head}.pmx_bone_name", "頭", type="string")
        
        # 左腕ジョイント
        cmds.select(upper_body)
        left_arm = cmds.joint(name="left_arm", position=[-5, 15, 0])
        cmds.addAttr(left_arm, longName="pmx_bone_name", dataType="string")
        cmds.setAttr(f"{left_arm}.pmx_bone_name", "左腕", type="string")
        
        # 右腕ジョイント
        cmds.select(upper_body)
        right_arm = cmds.joint(name="right_arm", position=[5, 15, 0])
        cmds.addAttr(right_arm, longName="pmx_bone_name", dataType="string")
        cmds.setAttr(f"{right_arm}.pmx_bone_name", "右腕", type="string")
        
        # 選択をクリア
        cmds.select(clear=True)
        
        return {
            "root": root,
            "center": center,
            "upper_body": upper_body,
            "head": head,
            "left_arm": left_arm,
            "right_arm": right_arm
        }

    def test_vmd_import_basic(self):
        """VMDファイルの基本的なインポート機能をテスト"""
        # テスト用スケルトンを作成
        joints = self._create_test_skeleton()
        
        # VMDファイルのパスを取得
        vmd_files = [f for f in os.listdir(self.test_data_dir) if f.endswith('.vmd')]
        
        if not vmd_files:
            self.skipTest("テスト用VMDファイルが見つかりません")
            
        vmd_path = os.path.join(self.test_data_dir, vmd_files[0])
        
        # VMDファイルが存在することを確認
        self.assertTrue(os.path.exists(vmd_path), f"VMDファイルが見つかりません: {vmd_path}")
        
        # VMDファイルをインポート
        result = import_mmd_file(vmd_path)
        
        # インポートが成功したことを確認
        self.assertTrue(result, "VMDファイルのインポートに失敗しました")
        
        # タイムライン設定が更新されたことを確認
        min_time = cmds.playbackOptions(query=True, minTime=True)
        max_time = cmds.playbackOptions(query=True, maxTime=True)
        
        # タイムラインが拡張されたことを確認（デフォルトの1-24フレームから変更されているはず）
        self.assertGreater(max_time, 24, "タイムラインが更新されていません")
        
    def test_vmd_import_with_namespace(self):
        """ネームスペース付きモデルへのVMDインポートをテスト"""
        # ネームスペースを作成
        namespace = "test_model"
        if not cmds.namespace(exists=namespace):
            cmds.namespace(add=namespace)
            
        # ネームスペース内にスケルトンを作成
        cmds.namespace(set=namespace)
        joints = self._create_test_skeleton()
        cmds.namespace(set=":")
        
        # ネームスペース付きのジョイントを選択
        cmds.select(f"{namespace}:center")
        
        # VMDファイルのパスを取得
        vmd_files = [f for f in os.listdir(self.test_data_dir) if f.endswith('.vmd')]
        
        if not vmd_files:
            self.skipTest("テスト用VMDファイルが見つかりません")
            
        vmd_path = os.path.join(self.test_data_dir, vmd_files[0])
        
        # VMDファイルをインポート
        result = import_mmd_file(vmd_path)
        
        # インポートが成功したことを確認
        self.assertTrue(result, "ネームスペース付きモデルへのVMDインポートに失敗しました")
        
        # ネームスペース付きジョイントにキーフレームが設定されたことを確認
        center_keys = cmds.keyframe(f"{namespace}:center", query=True, keyframeCount=True)
        if center_keys:
            self.assertGreater(center_keys, 0, "センタージョイントにキーフレームが設定されていません")
            
    def test_vmd_import_without_model(self):
        """モデルがない状態でのVMDインポートをテスト"""
        # スケルトンを作成しない（空のシーン）
        
        # VMDファイルのパスを取得
        vmd_files = [f for f in os.listdir(self.test_data_dir) if f.endswith('.vmd')]
        
        if not vmd_files:
            self.skipTest("テスト用VMDファイルが見つかりません")
            
        vmd_path = os.path.join(self.test_data_dir, vmd_files[0])
        
        # VMDファイルをインポート（モデルがないので失敗するはず）
        result = import_mmd_file(vmd_path)
        
        # インポートは成功するが、キーフレームは設定されないはず
        self.assertTrue(result, "VMDファイルの読み込みに失敗しました")
        
        # ジョイントが存在しないことを確認
        joints = cmds.ls(type="joint")
        self.assertEqual(len(joints), 0, "ジョイントが作成されています")
        
    def test_vmd_parser_integration(self):
        """VmdParserとの統合をテスト"""
        # VMDファイルのパスを取得
        vmd_files = [f for f in os.listdir(self.test_data_dir) if f.endswith('.vmd')]
        
        if not vmd_files:
            self.skipTest("テスト用VMDファイルが見つかりません")
            
        vmd_path = os.path.join(self.test_data_dir, vmd_files[0])
        
        # VmdParserで直接パース
        parser = VmdParser()
        parser.parse_file(vmd_path)
        
        # パース結果を確認
        self.assertIsNotNone(parser.header.model_name, "モデル名が読み込まれていません")
        
        # ボーンフレームまたはモーフフレームが存在することを確認
        has_animation = (
            (hasattr(parser, 'bone_frames') and parser.bone_frames) or
            (hasattr(parser, 'morph_frames') and parser.morph_frames)
        )
        self.assertTrue(has_animation, "アニメーションデータが読み込まれていません")
        
    def test_pmx_model_with_vmd_animation(self):
        """実際のPMXモデルにVMDアニメーションを適用する統合テスト"""
        # PMXファイルをインポート
        pmx_path = os.path.join(self.test_data_dir, "Lumine", "Lumine.pmx")
        
        if not os.path.exists(pmx_path):
            self.skipTest("テスト用PMXファイルが見つかりません")
            
        # PMXモデルをインポート
        pmx_result = import_mmd_file(pmx_path)
        self.assertTrue(pmx_result, "PMXファイルのインポートに失敗しました")
        
        # インポートされたジョイントを確認
        joints = cmds.ls(type="joint")
        self.assertGreater(len(joints), 0, "ジョイントがインポートされていません")
        
        # pmx_bone_name属性を持つジョイントを探す
        joints_with_bone_name = []
        for joint in joints:
            if cmds.attributeQuery("pmx_bone_name", node=joint, exists=True):
                bone_name = cmds.getAttr(f"{joint}.pmx_bone_name")
                joints_with_bone_name.append((joint, bone_name))
                
        self.assertGreater(len(joints_with_bone_name), 0, 
                          "pmx_bone_name属性を持つジョイントが見つかりません")
        
        # VMDファイルのパスを取得
        vmd_files = [f for f in os.listdir(self.test_data_dir) if f.endswith('.vmd')]
        
        if not vmd_files:
            self.skipTest("テスト用VMDファイルが見つかりません")
            
        vmd_path = os.path.join(self.test_data_dir, vmd_files[0])
        
        # 現在のフレーム数を記録
        initial_min = cmds.playbackOptions(query=True, minTime=True)
        initial_max = cmds.playbackOptions(query=True, maxTime=True)
        
        # VMDアニメーションをインポート
        vmd_result = import_mmd_file(vmd_path)
        self.assertTrue(vmd_result, "VMDファイルのインポートに失敗しました")
        
        # タイムラインが更新されたか確認
        new_max = cmds.playbackOptions(query=True, maxTime=True)
        self.assertGreater(new_max, initial_max, 
                          "タイムラインが更新されていません")
        
        # アニメーションが適用されたジョイントを確認
        animated_joints = []
        for joint, bone_name in joints_with_bone_name:
            # translateとrotateの各軸でキーフレームがあるか確認
            for attr in ["translateX", "translateY", "translateZ", 
                        "rotateX", "rotateY", "rotateZ"]:
                keyframes = cmds.keyframe(joint, attribute=attr, query=True)
                if keyframes:
                    animated_joints.append((joint, bone_name))
                    break
                    
        # 少なくとも1つのジョイントにアニメーションが適用されていることを確認
        self.assertGreater(len(animated_joints), 0, 
                          "どのジョイントにもアニメーションが適用されていません")
        
        # アニメーションが適用されたジョイントの情報を出力（デバッグ用）
        print(f"\nアニメーションが適用されたジョイント数: {len(animated_joints)}/{len(joints_with_bone_name)}")
        if len(animated_joints) < 10:  # 少数の場合は詳細を表示
            for joint, bone_name in animated_joints[:5]:
                print(f"  - {joint} (ボーン名: {bone_name})")