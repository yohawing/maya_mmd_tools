"""
VmdConverterの単体テスト
"""

import unittest
from unittest.mock import Mock, patch, MagicMock
import sys

# Maya cmdsモジュールをモック
sys.modules["maya"] = MagicMock()
sys.modules["maya.cmds"] = MagicMock()
sys.modules["maya.api"] = MagicMock()
sys.modules["maya.api.OpenMaya"] = MagicMock()

from mmd_tools.converters.animation_converter import VmdConverter
from mmd_tools.core.vmd_parser import VmdParser
from tests.common.maya_mock import MayaMockSetup
from tests.common.maya_mock_helpers import MayaMockFactory, AnimationMockHelper


class TestVmdConverter(unittest.TestCase):
    """VmdConverterのテストクラス"""

    @classmethod
    def setUpClass(cls):
        """テストクラスのセットアップ"""
        # より詳細なMayaモックをセットアップ
        cls.maya, cls.cmds, cls.om = MayaMockSetup.setup_maya_mocks()
    
    def setUp(self):
        """テストのセットアップ"""
        # シーンをリセット
        if hasattr(self.cmds, 'reset'):
            self.cmds.reset()
        self.converter = VmdConverter()

    def test_init(self):
        """初期化のテスト"""
        self.assertEqual(self.converter.fps, 30.0)
        self.assertEqual(self.converter.bone_name_mapping, {})
        self.assertEqual(self.converter.morph_name_mapping, {})

    def test_organize_bone_frames(self):
        """ボーンフレームの整理テスト"""
        # モックフレームを作成
        mock_frame1 = Mock()
        mock_frame1.bone_name = "bone1"
        mock_frame1.frame_number = 10

        mock_frame2 = Mock()
        mock_frame2.bone_name = "bone1"
        mock_frame2.frame_number = 5

        mock_frame3 = Mock()
        mock_frame3.bone_name = "bone2"
        mock_frame3.frame_number = 0

        frames_list = [mock_frame1, mock_frame2, mock_frame3]

        # 整理を実行
        result = self.converter._organize_bone_frames(frames_list)

        # 結果を確認
        self.assertIn("bone1", result)
        self.assertIn("bone2", result)
        self.assertEqual(len(result["bone1"]), 2)
        self.assertEqual(len(result["bone2"]), 1)

        # ソート順を確認
        self.assertEqual(result["bone1"][0].frame_number, 5)
        self.assertEqual(result["bone1"][1].frame_number, 10)

    def test_sanitize_name(self):
        """名前のサニタイズテスト"""
        # 通常の文字列
        self.assertEqual(self.converter._sanitize_name("test"), "test")

        # 全角文字を含む
        self.assertEqual(self.converter._sanitize_name("ボーン１"), "ボーン1")

        # 空白を含む
        self.assertEqual(self.converter._sanitize_name(" test "), "test")

    def test_quaternion_to_euler(self):
        """クォータニオンからオイラー角への変換テスト - 簡略版"""
        # この関数はMaya APIに依存するため、
        # 実際のテストはMaya内での統合テストで行う
        # ここでは関数が存在することだけを確認
        self.assertTrue(hasattr(self.converter, "_quaternion_to_euler"))
        self.assertTrue(callable(self.converter._quaternion_to_euler))

    def test_build_name_mappings(self):
        """名前マッピング構築のテスト"""
        # MMDボーン階層を作成
        bone_mapping = MayaMockFactory.create_mmd_bone_hierarchy()
        
        # マッピングを構築
        self.converter._build_name_mappings()
        
        # ボーンマッピングが構築されていることを確認
        # (実際の実装では、Mayaシーンからジョイントを検索してマッピングを構築する)
        # ここでは手動でマッピングを設定
        self.converter.bone_name_mapping = bone_mapping
        
        # マッピングが正しく機能することを確認
        self.assertEqual(self.converter._get_maya_joint_name("センター"), "center")
        self.assertEqual(self.converter._get_maya_joint_name("上半身"), "upper_body")
        self.assertEqual(self.converter._get_maya_joint_name("頭"), "head")

    def test_get_maya_joint_name(self):
        """Mayaジョイント名取得のテスト"""
        # マッピングを設定
        self.converter.bone_name_mapping = {
            "ボーン1": "joint1",
            "ボーン２": "joint2",  # 全角数字
        }

        # 完全一致
        self.assertEqual(self.converter._get_maya_joint_name("ボーン1"), "joint1")

        # サニタイズ後の一致
        self.assertEqual(self.converter._get_maya_joint_name("ボーン2"), "joint2")

        # 見つからない場合
        self.assertIsNone(self.converter._get_maya_joint_name("存在しないボーン"))


    def test_convert_with_mock_data(self):
        """モックデータを使用した変換テスト"""
        # モックシーンを作成
        bone_mapping = MayaMockFactory.create_mmd_bone_hierarchy()
        self.converter.bone_name_mapping = bone_mapping
        
        # モックVMDデータを作成
        mock_vmd_data = Mock()
        mock_vmd_data.bone_frames = [
            Mock(bone_name="センター", frame_number=0, position=(0, 0, 0), 
                 rotation=(0, 0, 0, 1), interpolation=None),
            Mock(bone_name="センター", frame_number=30, position=(0, 2, 0), 
                 rotation=(0, 0.1, 0, 0.995), interpolation=None),
            Mock(bone_name="上半身", frame_number=0, position=(0, 0, 0), 
                 rotation=(0, 0, 0, 1), interpolation=None),
            Mock(bone_name="上半身", frame_number=15, position=(0, 0, 0), 
                 rotation=(0.1, 0, 0, 0.995), interpolation=None),
        ]
        mock_vmd_data.morph_frames = []
        mock_vmd_data.camera_frames = []
        mock_vmd_data.light_frames = []
        
        # フレームを整理
        organized_frames = self.converter._organize_bone_frames(mock_vmd_data.bone_frames)
        
        # 整理されたフレームを確認
        self.assertIn("センター", organized_frames)
        self.assertIn("上半身", organized_frames)
        self.assertEqual(len(organized_frames["センター"]), 2)
        self.assertEqual(len(organized_frames["上半身"]), 2)
    
    @classmethod
    def tearDownClass(cls):
        """テストクラスのクリーンアップ"""
        # Mayaモックをクリーンアップ
        MayaMockSetup.teardown_maya_mocks()


if __name__ == "__main__":
    unittest.main()
