"""
Maya MMD Tools プラグインのテストスクリプト
Mayaで実行してファイルトランスレーターが正しく動作するかテストします
"""

import maya.cmds as cmds
import maya.api.OpenMaya as om


def test_file_translator_registration():
    """ファイルトランスレーターが正しく登録されているかテスト"""
    
    print("=== MMD File Translator Test ===")
    
    # プラグインが読み込まれているかチェック
    try:
        if cmds.pluginInfo("plugin_main.py", query=True, loaded=True):
            print("✓ Plugin is loaded")
        else:
            print("✗ Plugin is not loaded")
            # プラグインを読み込む
            cmds.loadPlugin("plugin_main.py")
            print("✓ Plugin loaded successfully")
    except Exception as e:
        print(f"✗ Failed to check/load plugin: {e}")
        return False
    
    # ファイルトランスレーターが利用可能かチェック
    translators = cmds.translator(query=True, list=True)
    
    mmd_found = False
    vmd_found = False
    
    for translator in translators:
        if "MMD Model" in translator:
            mmd_found = True
            print(f"✓ Found MMD Model translator: {translator}")
        elif "MMD Motion" in translator:
            vmd_found = True
            print(f"✓ Found MMD Motion translator: {translator}")
    
    if not mmd_found:
        print("✗ MMD Model translator not found")
    if not vmd_found:
        print("✗ MMD Motion translator not found")
    
    # メニューがあるかチェック
    if cmds.menu('MMDToolsMenu', exists=True):
        print("✓ MMD Tools menu exists")
    else:
        print("✗ MMD Tools menu not found")
    
    return mmd_found and vmd_found


def test_import_dialog():
    """インポートダイアログをテスト"""
    
    print("\n=== Import Dialog Test ===")
    
    try:
        # インポートダイアログを開く（テスト用なのですぐ閉じる）
        # 実際にはユーザーがファイルを選択する
        result = cmds.fileDialog2(
            caption="Test MMD Import",
            fileFilter="MMD Model (*.pmx *.pmd);;MMD Motion (*.vmd)",
            dialogStyle=2,  # 0=Maya style, 1=OS style, 2=Maya style with preview
            fileMode=1      # 1=single file
        )
        
        if result:
            print(f"✓ File dialog returned: {result[0]}")
            # 実際のインポートはここでは行わない（テストファイルが必要）
        else:
            print("ℹ File dialog cancelled (normal for test)")
        
        return True
        
    except Exception as e:
        print(f"✗ File dialog error: {e}")
        return False


def run_all_tests():
    """全てのテストを実行"""
    
    print("Maya MMD Tools Plugin Test")
    print("=" * 40)
    
    test1_passed = test_file_translator_registration()
    test2_passed = test_import_dialog()
    
    print("\n=== Test Summary ===")
    print(f"File Translator Registration: {'PASS' if test1_passed else 'FAIL'}")
    print(f"Import Dialog: {'PASS' if test2_passed else 'FAIL'}")
    
    if test1_passed and test2_passed:
        print("\n✓ All tests passed!")
        print("You can now use File > Import to import MMD files (.pmx, .pmd, .vmd)")
    else:
        print("\n✗ Some tests failed. Check the plugin installation.")


if __name__ == "__main__":
    run_all_tests()
