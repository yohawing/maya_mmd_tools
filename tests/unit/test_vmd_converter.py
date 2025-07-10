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


class TestVmdConverter(unittest.TestCase):
    """VmdConverterのテストクラス"""

    def setUp(self):
        """テストのセットアップ"""
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
        """名前マッピング構築のテスト - 簡略版"""
        # 手動でマッピングを設定してテスト
        with patch("maya.cmds.ls") as mock_ls:
            mock_ls.return_value = []  # 空のリストを返す

            # マッピングを構築（何も追加されない）
            self.converter._build_name_mappings()

            # 空であることを確認
            self.assertEqual(self.converter.bone_name_mapping, {})

        # 手動でマッピングを追加してget_maya_joint_nameをテスト
        self.converter.bone_name_mapping = {
            "ボーン1": "joint1",
            "ボーン2": "joint2",
        }

        # マッピングが正しく機能することを確認
        self.assertEqual(self.converter._get_maya_joint_name("ボーン1"), "joint1")
        self.assertEqual(self.converter._get_maya_joint_name("ボーン2"), "joint2")

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


if __name__ == "__main__":
    unittest.main()
