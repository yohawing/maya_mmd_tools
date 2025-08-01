"""
Namespace実装の動作確認スクリプト
"""

import os
import sys

# プロジェクトルートをPythonパスに追加
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from maya import cmds
from mmd_tools.core.namespace_utils import NamespaceUtils
from mmd_tools.core import settings
from mmd_tools.io.mmd_importer import import_mmd_file


def test_namespace_utils():
    """NamespaceUtilsの基本動作確認"""
    print("\n=== NamespaceUtils動作確認 ===")
    
    # 1. namespace生成テスト
    print("\n1. Namespace生成テスト")
    test_names = ["初音ミク", "01_model", "test@model", "", "123"]
    for name in test_names:
        generated = NamespaceUtils.generate_namespace(name)
        print(f"  {name} -> {generated}")
    
    # 2. 重複チェックテスト
    print("\n2. 重複チェックテスト")
    base_name = "TestModel"
    unique1 = NamespaceUtils.ensure_unique_namespace(base_name)
    print(f"  1回目: {unique1}")
    
    # namespace作成
    if not cmds.namespace(exists=unique1):
        cmds.namespace(add=unique1)
    
    unique2 = NamespaceUtils.ensure_unique_namespace(base_name)
    print(f"  2回目: {unique2}")
    
    # 3. context managerテスト
    print("\n3. Context Managerテスト")
    with NamespaceUtils.namespace_context("ContextTest") as ns:
        print(f"  Context内namespace: {ns}")
        cube = cmds.polyCube(name="testCube")[0]
        print(f"  作成されたオブジェクト: {cube}")
    
    # 4. クリーンアップ
    print("\n4. クリーンアップ")
    for ns in ["TestModel", "ContextTest"]:
        if cmds.namespace(exists=ns):
            NamespaceUtils.cleanup_namespace(ns, force=True)
            print(f"  {ns} をクリーンアップしました")


def test_import_with_namespace():
    """Namespace付きインポートのテスト"""
    print("\n\n=== Namespace付きインポートテスト ===")
    
    # テストデータのパスを確認
    test_data_dir = os.path.join(project_root, "tests", "test_data")
    test_files = [
        os.path.join(test_data_dir, "simple_cube.pmx"),
        os.path.join(test_data_dir, "simple_model.pmd"),
    ]
    
    # 利用可能なテストファイルを探す
    available_file = None
    for test_file in test_files:
        if os.path.exists(test_file):
            available_file = test_file
            break
    
    if not available_file:
        print("テストファイルが見つかりません")
        print(f"探索パス: {test_files}")
        return
    
    print(f"\nテストファイル: {available_file}")
    
    # 新規シーンを作成
    cmds.file(new=True, force=True)
    
    # 1. Namespace有効でインポート
    print("\n1. Namespace有効でインポート")
    settings.set("import.general.use_namespace", True)
    
    options = {
        "use_namespace": True,
        "scale": 1.0,
    }
    
    root1 = import_mmd_file(available_file, options=options)
    if root1:
        print(f"  インポート成功: {root1}")
        ns1 = NamespaceUtils.get_namespace_from_node(root1)
        print(f"  Namespace: {ns1}")
    else:
        print("  インポート失敗")
    
    # 2. 同じファイルを再度インポート（連番テスト）
    print("\n2. 同じファイルを再度インポート")
    root2 = import_mmd_file(available_file, options=options)
    if root2:
        print(f"  インポート成功: {root2}")
        ns2 = NamespaceUtils.get_namespace_from_node(root2)
        print(f"  Namespace: {ns2}")
    else:
        print("  インポート失敗")
    
    # 3. Namespace一覧
    print("\n3. 現在のNamespace一覧")
    namespaces = NamespaceUtils.list_model_namespaces()
    for ns in namespaces:
        objects = cmds.ls(f"{ns}:*", type="transform")
        print(f"  - {ns}: {len(objects)}個のオブジェクト")
    
    # 4. Namespace無効でインポート
    print("\n4. Namespace無効でインポート")
    settings.set("import.general.use_namespace", False)
    
    options["use_namespace"] = False
    root3 = import_mmd_file(available_file, options=options)
    if root3:
        print(f"  インポート成功: {root3}")
        if ":" in root3:
            print("  警告: Namespaceが含まれています")
        else:
            print("  OK: Namespaceなし")
    else:
        print("  インポート失敗")


def main():
    """メイン実行関数"""
    print("Maya MMD Tools - Namespace実装動作確認")
    print("=" * 50)
    
    try:
        test_namespace_utils()
        test_import_with_namespace()
        
        print("\n\n動作確認完了")
        
    except Exception as e:
        print(f"\nエラーが発生しました: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()