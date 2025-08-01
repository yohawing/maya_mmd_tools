"""
Maya Namespace機能の調査スクリプト
"""
from maya import cmds

def test_namespace_functions():
    """Mayaのnamespace関連機能を調査"""
    print("=== Maya Namespace 機能調査 ===\n")
    
    # 1. 現在のnamespaceを確認
    current_ns = cmds.namespaceInfo(currentNamespace=True)
    print(f"現在のNamespace: {current_ns}")
    
    # 2. 存在するnamespaceのリスト
    all_namespaces = cmds.namespaceInfo(listOnlyNamespaces=True, recurse=True)
    print(f"\n存在するNamespace一覧:")
    for ns in all_namespaces:
        print(f"  - {ns}")
    
    # 3. namespaceの作成
    print("\n--- Namespace作成テスト ---")
    test_ns = "testModel"
    if cmds.namespace(exists=test_ns):
        print(f"Namespace '{test_ns}' は既に存在します")
    else:
        cmds.namespace(add=test_ns)
        print(f"Namespace '{test_ns}' を作成しました")
    
    # 4. ネストしたnamespaceの作成
    nested_ns = "testModel:subModel"
    if not cmds.namespace(exists=nested_ns):
        cmds.namespace(add="subModel", parent="testModel")
        print(f"ネストされたNamespace '{nested_ns}' を作成しました")
    
    # 5. namespaceを設定してオブジェクトを作成
    cmds.namespace(set=test_ns)
    cube = cmds.polyCube(name="testCube")[0]
    print(f"\nNamespace内に作成されたオブジェクト: {cube}")
    
    # 6. ルートnamespaceに戻る
    cmds.namespace(set=":")
    
    # 7. namespace内のオブジェクトを検索
    print("\n--- Namespace内のオブジェクト検索 ---")
    objects_in_ns = cmds.ls(f"{test_ns}:*", type="transform")
    print(f"{test_ns} 内のオブジェクト:")
    for obj in objects_in_ns:
        print(f"  - {obj}")
    
    # 8. namespace付きオブジェクトの操作
    print("\n--- Namespace付きオブジェクトの操作 ---")
    if cmds.objExists(f"{test_ns}:testCube"):
        cmds.setAttr(f"{test_ns}:testCube.translateY", 5)
        print(f"{test_ns}:testCube のY座標を5に設定")
    
    # 9. namespaceの移動
    print("\n--- Namespaceの移動 ---")
    if not cmds.namespace(exists="newParent"):
        cmds.namespace(add="newParent")
    # namespaceを別のnamespaceの下に移動
    cmds.namespace(moveNamespace=[test_ns, "newParent"])
    print(f"Namespace '{test_ns}' を 'newParent' の下に移動しました")
    
    # 10. namespace削除時の注意点
    print("\n--- Namespace削除の制約 ---")
    print("注意: namespaceに含まれるオブジェクトがある場合、")
    print("      namespace削除にはmergeNamespaceWithParentオプションが必要")

def test_namespace_with_references():
    """リファレンスとnamespaceの関係を調査"""
    print("\n\n=== リファレンスとNamespaceの関係 ===")
    
    # リファレンスされたファイルは自動的にnamespaceを持つ
    print("リファレンスファイルをロードすると、")
    print("デフォルトでファイル名ベースのnamespaceが作成されます")
    print("例: model.ma -> model:* のnamespaceが自動作成")

def check_namespace_restrictions():
    """namespace使用時の制約を確認"""
    print("\n\n=== Namespace使用時の制約 ===")
    
    print("1. namespace名に使用できない文字:")
    print("   - スペース")
    print("   - 特殊文字 (@, #, $, %, &, など)")
    print("   - 数字で始まる名前")
    
    print("\n2. 予約されたnamespace:")
    print("   - UI")
    print("   - shared")
    
    print("\n3. パフォーマンスへの影響:")
    print("   - 深くネストしたnamespaceは検索パフォーマンスに影響")
    print("   - 大量のnamespaceは管理が複雑になる")

if __name__ == "__main__":
    test_namespace_functions()
    test_namespace_with_references()
    check_namespace_restrictions()