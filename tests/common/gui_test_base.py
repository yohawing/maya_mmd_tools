"""GUI テスト用のベースクラスとユーティリティ。"""

import unittest
import functools
import maya.cmds as cmds


def skip_if_no_gui(func):
    """GUIが利用できない場合はテストをスキップするデコレーター"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if cmds.about(batch=True):
            raise unittest.SkipTest("GUI environment required")
        return func(*args, **kwargs)
    return wrapper


def requires_gui(cls):
    """クラス全体にGUI要求を適用するデコレーター"""
    for attr_name in dir(cls):
        attr = getattr(cls, attr_name)
        if callable(attr) and attr_name.startswith('test_'):
            setattr(cls, attr_name, skip_if_no_gui(attr))
    return cls


class GuiTestBase(unittest.TestCase):
    """GUI テスト用のベースクラス"""
    
    @classmethod
    def setUpClass(cls):
        """クラスレベルのセットアップ"""
        if cmds.about(batch=True):
            raise unittest.SkipTest("GUI environment required for this test class")
        super().setUpClass()
    
    def setUp(self):
        """各テストの前処理"""
        # 既存のウィンドウをクリーンアップ
        self._cleanup_windows()
        
    def tearDown(self):
        """各テストの後処理"""
        # ウィンドウをクリーンアップ
        self._cleanup_windows()
        
    def _cleanup_windows(self):
        """開いているウィンドウをクリーンアップ"""
        # Maya MMD Toolsのウィンドウを探して削除
        all_windows = cmds.lsUI(windows=True)
        for window in all_windows:
            if window.startswith('MayaMMDTools') or window.startswith('mmdTools'):
                try:
                    cmds.deleteUI(window, window=True)
                except:
                    pass