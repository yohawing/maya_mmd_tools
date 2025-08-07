"""VPDパーサーのユニットテスト"""

import unittest
import os
import tempfile

from mmd_tools.core.vpd_data import VpdData
from mmd_tools.core.vpd_data.header import VpdHeader
from mmd_tools.core.vpd_data.bone_pose import BonePose
from mmd_tools.core.exceptions import MMDParseException


class TestVpdData(unittest.TestCase):
    """VpdDataクラスのテスト"""
    
    def setUp(self):
        """テストの初期化"""
        self.vpd_data = VpdData()
        self.test_dir = tempfile.mkdtemp()
    
    def tearDown(self):
        """テストの後処理"""
        # テスト用ディレクトリのクリーンアップ
        import shutil
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
    
    def test_init(self):
        """初期化のテスト"""
        self.assertIsInstance(self.vpd_data.header, VpdHeader)
        self.assertEqual(len(self.vpd_data.bone_poses), 0)
    
    def test_parse_simple_vpd(self):
        """シンプルなVPDファイルの解析テスト"""
        # テスト用VPDファイルを作成
        vpd_content = """Vocaloid Pose Data file

test.osm;
2;

Bone0{センター
  1.000000,2.000000,3.000000;
  0.000000,0.000000,0.000000,1.000000;
}

Bone1{上半身
  0.000000,0.000000,0.000000;
  0.707107,0.000000,0.000000,0.707107;
}
"""
        
        test_file = os.path.join(self.test_dir, "test.vpd")
        with open(test_file, 'w', encoding='shift-jis') as f:
            f.write(vpd_content)
        
        # 解析実行
        self.vpd_data.parse_file(test_file)
        
        # ヘッダーの検証
        self.assertEqual(self.vpd_data.header.signature, "Vocaloid Pose Data file")
        self.assertEqual(self.vpd_data.header.parent_file, "test.osm")
        self.assertEqual(self.vpd_data.header.bone_count, 2)
        
        # ボーンポーズの検証
        self.assertEqual(len(self.vpd_data.bone_poses), 2)
        
        # 最初のボーン
        bone0 = self.vpd_data.bone_poses[0]
        self.assertEqual(bone0.bone_index, 0)
        self.assertEqual(bone0.bone_name, "センター")
        self.assertEqual(bone0.position, [1.0, 2.0, 3.0])
        self.assertEqual(bone0.quaternion, [0.0, 0.0, 0.0, 1.0])
        
        # 2番目のボーン
        bone1 = self.vpd_data.bone_poses[1]
        self.assertEqual(bone1.bone_index, 1)
        self.assertEqual(bone1.bone_name, "上半身")
        self.assertEqual(bone1.position, [0.0, 0.0, 0.0])
        self.assertAlmostEqual(bone1.quaternion[0], 0.707107, places=5)
        self.assertAlmostEqual(bone1.quaternion[3], 0.707107, places=5)
    
    def test_parse_without_header(self):
        """ヘッダー情報が不完全なVPDファイルの解析テスト"""
        vpd_content = """Vocaloid Pose Data file

Bone0{センター
  0.000000,0.000000,0.000000;
  0.000000,0.000000,0.000000,1.000000;
}
"""
        
        test_file = os.path.join(self.test_dir, "test_no_header.vpd")
        with open(test_file, 'w', encoding='shift-jis') as f:
            f.write(vpd_content)
        
        # 解析実行
        self.vpd_data.parse_file(test_file)
        
        # ボーン数が自動設定されることを確認
        self.assertEqual(self.vpd_data.header.bone_count, 1)
        self.assertEqual(len(self.vpd_data.bone_poses), 1)
    
    def test_write_file(self):
        """VPDファイルの書き出しテスト"""
        # データの準備
        self.vpd_data.header.parent_file = "output.osm"
        
        bone_pose = BonePose()
        bone_pose.bone_index = 0
        bone_pose.bone_name = "テストボーン"
        bone_pose.position = [1.5, 2.5, 3.5]
        bone_pose.quaternion = [0.0, 0.707107, 0.0, 0.707107]
        self.vpd_data.bone_poses.append(bone_pose)
        
        # ファイル書き出し
        output_file = os.path.join(self.test_dir, "output.vpd")
        self.vpd_data.write_file(output_file)
        
        # 書き出されたファイルを読み込んで検証
        new_vpd = VpdData()
        new_vpd.parse_file(output_file)
        
        self.assertEqual(new_vpd.header.parent_file, "output.osm")
        self.assertEqual(len(new_vpd.bone_poses), 1)
        self.assertEqual(new_vpd.bone_poses[0].bone_name, "テストボーン")
        self.assertAlmostEqual(new_vpd.bone_poses[0].position[0], 1.5, places=5)
    
    def test_invalid_file(self):
        """無効なファイルの解析テスト"""
        # 存在しないファイル
        with self.assertRaises(FileNotFoundError):
            self.vpd_data.parse_file("nonexistent.vpd")
        
        # 無効な形式のファイル
        invalid_content = "This is not a VPD file"
        test_file = os.path.join(self.test_dir, "invalid.vpd")
        with open(test_file, 'w') as f:
            f.write(invalid_content)
        
        with self.assertRaises(MMDParseException):
            self.vpd_data.parse_file(test_file)


class TestBonePose(unittest.TestCase):
    """BonePoseクラスのテスト"""
    
    def test_init(self):
        """初期化のテスト"""
        bone_pose = BonePose()
        self.assertEqual(bone_pose.bone_index, 0)
        self.assertEqual(bone_pose.bone_name, "")
        self.assertEqual(bone_pose.position, [0.0, 0.0, 0.0])
        self.assertEqual(bone_pose.quaternion, [0.0, 0.0, 0.0, 1.0])
    
    def test_to_vpd_format(self):
        """VPD形式文字列生成のテスト"""
        bone_pose = BonePose()
        bone_pose.bone_index = 5
        bone_pose.bone_name = "左腕"
        bone_pose.position = [1.0, 2.0, 3.0]
        bone_pose.quaternion = [0.0, 0.707107, 0.0, 0.707107]
        
        vpd_str = bone_pose.to_vpd_format()
        
        # 生成された文字列の検証
        self.assertIn("Bone5{左腕", vpd_str)
        self.assertIn("1.000000,2.000000,3.000000", vpd_str)
        self.assertIn("0.000000,0.707107,0.000000,0.707107", vpd_str)


class TestVpdHeader(unittest.TestCase):
    """VpdHeaderクラスのテスト"""
    
    def test_init(self):
        """初期化のテスト"""
        header = VpdHeader()
        self.assertEqual(header.signature, "Vocaloid Pose Data file")
        self.assertEqual(header.parent_file, "")
        self.assertEqual(header.bone_count, 0)
    
    def test_str_representation(self):
        """文字列表現のテスト"""
        header = VpdHeader()
        header.parent_file = "model.osm"
        header.bone_count = 10
        
        str_repr = str(header)
        self.assertIn("VPD Header", str_repr)
        self.assertIn("model.osm", str_repr)
        self.assertIn("10", str_repr)


if __name__ == '__main__':
    unittest.main()