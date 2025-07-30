"""
VMDエクスポート機能のテストスクリプト
読み込み → 書き込み → 再読み込みのラウンドトリップテストを実行
"""
import os
import sys
import tempfile

# プロジェクトのルートディレクトリをパスに追加
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

from mmd_tools.core.vmd_parser import VmdParser
from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame
from mmd_tools.core.vmd_data.morph_frame import VmdMorphFrame
from mmd_tools.core.vmd_data.camera_frame import VmdCameraFrame


def test_vmd_round_trip():
    """VMDファイルの読み込み→書き込み→再読み込みテスト"""
    
    # テスト用VMDファイルのパス
    test_vmd_path = os.path.join(project_root, "tests", "data", "basic_motion.vmd")
    
    if not os.path.exists(test_vmd_path):
        print(f"テストファイルが見つかりません: {test_vmd_path}")
        # 別の場所を探す
        test_vmd_path = os.path.join(project_root, "tests", "data", "for_unit_test", "test_motion.vmd")
        if not os.path.exists(test_vmd_path):
            print(f"代替テストファイルも見つかりません: {test_vmd_path}")
            return False
    
    print(f"テストファイルを読み込み中: {test_vmd_path}")
    
    # 1. オリジナルのVMDファイルを読み込む
    parser1 = VmdParser()
    parser1.parse_file(test_vmd_path)
    
    print(f"読み込み完了:")
    print(f"  - モデル名: {parser1.header.model_name}")
    print(f"  - ボーンフレーム数: {len(parser1.bone_frames)}")
    print(f"  - モーフフレーム数: {len(parser1.morph_frames)}")
    print(f"  - カメラフレーム数: {len(parser1.camera_frames)}")
    print(f"  - ライトフレーム数: {len(parser1.light_frames)}")
    
    # 2. 一時ファイルに書き込む
    with tempfile.NamedTemporaryFile(suffix=".vmd", delete=False) as tmp_file:
        tmp_path = tmp_file.name
    
    print(f"\n一時ファイルに書き込み中: {tmp_path}")
    parser1.write_file(tmp_path)
    print("書き込み完了")
    
    # 3. 書き込んだファイルを再度読み込む
    print("\n書き込んだファイルを再読み込み中...")
    parser2 = VmdParser()
    parser2.parse_file(tmp_path)
    
    print(f"再読み込み完了:")
    print(f"  - モデル名: {parser2.header.model_name}")
    print(f"  - ボーンフレーム数: {len(parser2.bone_frames)}")
    print(f"  - モーフフレーム数: {len(parser2.morph_frames)}")
    print(f"  - カメラフレーム数: {len(parser2.camera_frames)}")
    print(f"  - ライトフレーム数: {len(parser2.light_frames)}")
    
    # 4. データの一致を確認
    print("\nデータの一致を確認中...")
    
    # ヘッダー情報の比較
    assert parser1.header.model_name == parser2.header.model_name, "モデル名が一致しません"
    
    # フレーム数の比較
    assert len(parser1.bone_frames) == len(parser2.bone_frames), "ボーンフレーム数が一致しません"
    assert len(parser1.morph_frames) == len(parser2.morph_frames), "モーフフレーム数が一致しません"
    assert len(parser1.camera_frames) == len(parser2.camera_frames), "カメラフレーム数が一致しません"
    assert len(parser1.light_frames) == len(parser2.light_frames), "ライトフレーム数が一致しません"
    
    # 最初のボーンフレームデータの比較（サンプル）
    if parser1.bone_frames:
        bf1 = parser1.bone_frames[0]
        bf2 = parser2.bone_frames[0]
        assert bf1.bone_name == bf2.bone_name, "ボーン名が一致しません"
        assert bf1.frame_number == bf2.frame_number, "フレーム番号が一致しません"
        assert bf1.position == bf2.position, "位置が一致しません"
        assert bf1.rotation == bf2.rotation, "回転が一致しません"
    
    # 後片付け
    os.unlink(tmp_path)
    
    print("\n[SUCCESS] ラウンドトリップテスト成功！")
    return True


def test_create_simple_vmd():
    """簡単なVMDファイルを作成してエクスポートするテスト"""
    
    print("\n簡単なVMDファイルを作成中...")
    
    # 新しいVMDパーサーインスタンスを作成
    parser = VmdParser()
    
    # ヘッダー情報を設定
    parser.header.magic = b"Vocaloid Motion Data"
    parser.header.model_name = "TestModel"
    
    # ボーンフレームを追加（簡単な動き）
    for i in range(0, 30, 10):
        frame = VmdBoneFrame()
        frame.bone_name = "Center"
        frame.frame_number = i
        frame.position = [0.0, float(i) * 0.1, 0.0]  # Y方向に動く
        frame.rotation = [0.0, 0.0, 0.0, 1.0]  # 単位クォータニオン
        # 補間データ（デフォルト線形補間）
        frame.interpolation = b'\x14\x14\x14\x14' * 16  # 64バイト
        parser.bone_frames.append(frame)
    
    # モーフフレームを追加
    frame = VmdMorphFrame()
    frame.morph_name = "smile"
    frame.frame_number = 0
    frame.weight = 0.0
    parser.morph_frames.append(frame)
    
    frame = VmdMorphFrame()
    frame.morph_name = "smile"
    frame.frame_number = 30
    frame.weight = 1.0
    parser.morph_frames.append(frame)
    
    # カメラフレームを追加
    frame = VmdCameraFrame()
    frame.frame_number = 0
    frame.distance = -10.0
    frame.position = [0.0, 10.0, 0.0]
    frame.rotation = [0.0, 0.0, 0.0]
    frame.interpolation = b'\x14\x14\x14\x14' * 6  # 24バイト
    frame.view_angle = 30
    frame.perspective = 0
    parser.camera_frames.append(frame)
    
    # 一時ファイルに書き込む
    with tempfile.NamedTemporaryFile(suffix=".vmd", delete=False) as tmp_file:
        tmp_path = tmp_file.name
    
    print(f"作成したモーションをファイルに書き込み中: {tmp_path}")
    parser.write_file(tmp_path)
    print("書き込み完了")
    
    # 書き込んだファイルを読み込んで確認
    print("\n書き込んだファイルを読み込み中...")
    parser2 = VmdParser()
    parser2.parse_file(tmp_path)
    
    print(f"読み込み完了:")
    print(f"  - モデル名: {parser2.header.model_name}")
    print(f"  - ボーンフレーム数: {len(parser2.bone_frames)}")
    print(f"  - モーフフレーム数: {len(parser2.morph_frames)}")
    print(f"  - カメラフレーム数: {len(parser2.camera_frames)}")
    
    # データの検証
    assert parser2.header.model_name == "TestModel", "モデル名が正しく保存されていません"
    assert len(parser2.bone_frames) == 3, "ボーンフレーム数が正しくありません"
    assert len(parser2.morph_frames) == 2, "モーフフレーム数が正しくありません"
    assert len(parser2.camera_frames) == 1, "カメラフレーム数が正しくありません"
    
    # 後片付け
    os.unlink(tmp_path)
    
    print("\n[SUCCESS] 簡単なVMDファイル作成テスト成功！")
    return True


if __name__ == "__main__":
    print("VMDエクスポート機能のテストを開始します...\n")
    
    # ラウンドトリップテスト
    try:
        test_vmd_round_trip()
    except Exception as e:
        print(f"\n[FAILED] ラウンドトリップテスト失敗: {e}")
        import traceback
        traceback.print_exc()
    
    # 簡単なVMD作成テスト
    try:
        test_create_simple_vmd()
    except Exception as e:
        print(f"\n[FAILED] VMD作成テスト失敗: {e}")
        import traceback
        traceback.print_exc()
    
    print("\nテスト完了")