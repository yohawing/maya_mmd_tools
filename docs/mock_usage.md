# Maya モック機能の使用方法

## 概要

このドキュメントでは、Maya環境なしでユニットテストを実行するためのモック機能の使用方法について説明します。

## モックの構成

### 基本モジュール

#### `maya_mock.py`
Maya APIの基本的なモック実装を提供します。

- **MayaMockBase**: モックの基底クラス
- **CmdsMock**: `maya.cmds`モジュールのモック
- **OpenMayaMock**: `maya.api.OpenMaya`モジュールのモック
- **MayaMockSetup**: モックのセットアップユーティリティ

#### `maya_mock_helpers.py`
テストで頻繁に使用されるMayaオブジェクトの作成を簡単にするヘルパー関数を提供します。

- **MayaMockFactory**: MMD関連のオブジェクトを作成するファクトリクラス
- **AnimationMockHelper**: アニメーション関連のヘルパー

## 基本的な使用方法

### モックのセットアップ

```python
import unittest
from unittest.mock import MagicMock
import sys

# Mayaモジュールをモック化（インポート前に必須）
sys.modules["maya"] = MagicMock()
sys.modules["maya.cmds"] = MagicMock()
sys.modules["maya.api"] = MagicMock()
sys.modules["maya.api.OpenMaya"] = MagicMock()

# テスト対象のモジュールをインポート
from mmd_tools.converters.some_converter import SomeConverter

# モックヘルパーをインポート
from tests.common.maya_mock import MayaMockSetup
from tests.common.maya_mock_helpers import MayaMockFactory
```

### テストクラスでの使用

```python
class TestMyConverter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """詳細なMayaモックをセットアップ"""
        cls.maya, cls.cmds, cls.om = MayaMockSetup.setup_maya_mocks()
    
    @classmethod
    def tearDownClass(cls):
        """モックをクリーンアップ"""
        MayaMockSetup.teardown_maya_mocks()
    
    def setUp(self):
        """各テストのセットアップ"""
        self.cmds.reset()  # シーンをリセット
```

## モック機能の詳細

### オブジェクト作成

```python
# ジョイントを作成
joint = self.cmds.joint(name="test_joint", position=(1, 2, 3))

# メッシュを作成
mesh, shape = self.cmds.polyCube(name="test_cube")

# グループを作成
group = self.cmds.group(joint1, joint2, name="test_group")
```

### アトリビュート操作

```python
# アトリビュートを設定
self.cmds.setAttr("test_joint.translateX", 5.0)
self.cmds.setAttr("test_joint.rotateY", 45.0)

# アトリビュートを取得
tx = self.cmds.getAttr("test_joint.translateX")
ry = self.cmds.getAttr("test_joint.rotateY")
```

### アニメーション

```python
# 現在時間を設定
self.cmds.currentTime(10)

# キーフレームを設定
self.cmds.setKeyframe("test_joint", attribute="translateX", value=5.0)

# プレイバックオプション
self.cmds.playbackOptions(minTime=1, maxTime=100, fps=24)
```

## ヘルパー関数の使用

### MMDボーン階層の作成

```python
# MMD標準ボーン階層を作成
bone_mapping = MayaMockFactory.create_mmd_bone_hierarchy()

# bone_mappingは以下のような辞書を返す:
# {
#     "センター": "center",
#     "上半身": "upper_body",
#     "頭": "head",
#     ...
# }
```

### IKセットアップ

```python
# IKセットアップを作成
ik_info = MayaMockFactory.create_mmd_ik_setup(bone_mapping)

# ik_infoは以下のような情報を含む:
# {
#     "left_leg_ik": {
#         "handle": "ankle_L_ikHandle",
#         "controller": "ankle_L_ikCtrl",
#         "start": "leg_L",
#         "end": "ankle_L"
#     },
#     ...
# }
```

### メッシュとマテリアル

```python
# メッシュを作成
mesh_info = MayaMockFactory.create_mmd_mesh("body")

# マテリアルを作成
material_info = MayaMockFactory.create_material(
    "body_material",
    color=(1.0, 0.8, 0.7),
    texture="body_texture.png"
)

# ブレンドシェイプを作成
blend_shape = MayaMockFactory.create_blend_shape(
    mesh_info["mesh"],
    "smile",
    [(0, (0.1, 0.1, 0)), (1, (0.1, 0.1, 0))]
)
```

### アニメーション

```python
# アニメーションカーブを作成
keys = [(0, 0.0), (10, 5.0), (20, 0.0)]
anim_curve = AnimationMockHelper.create_animation_curve(
    "test_joint", "translateX", keys
)

# VMDアニメーションデータから作成
bone_frames = {
    "センター": [
        {"frame_number": 0, "position": (0, 0, 0), "rotation": (0, 0, 0)},
        {"frame_number": 30, "position": (0, 2, 0), "rotation": (0, 0.1, 0)},
    ]
}
created_curves = AnimationMockHelper.create_vmd_animation(
    bone_mapping, bone_frames
)
```

### 完全なシーンの作成

```python
# すべての要素を含むシーンを作成
scene_info = create_mock_scene()

# scene_infoは以下を含む:
# - bone_mapping: ボーン階層
# - ik_info: IKセットアップ
# - mesh_info: メッシュ
# - material_info: マテリアル
# - blend_shape: ブレンドシェイプ
```

## ベストプラクティス

### 1. 早期モック化
Mayaモジュールは、テスト対象のモジュールをインポートする前にモック化する必要があります。

### 2. シーンのリセット
各テストの前に`cmds.reset()`を呼び出して、クリーンな状態から開始します。

### 3. 適切なクリーンアップ
テストクラスの終了時に`MayaMockSetup.teardown_maya_mocks()`を呼び出します。

### 4. 実装の詳細に依存しない
モックの内部実装に依存せず、公開されたAPIのみを使用します。

### 5. 統合テストとの使い分け
- **ユニットテスト**: ビジネスロジックのテストにモックを使用
- **統合テスト**: 実際のMaya環境での動作確認

## トラブルシューティング

### ImportError
```python
# 間違い: インポート後にモック化
from mmd_tools.some_module import SomeClass
sys.modules["maya"] = MagicMock()  # 遅すぎる！

# 正しい: インポート前にモック化
sys.modules["maya"] = MagicMock()
from mmd_tools.some_module import SomeClass
```

### AttributeError
モックされていないメソッドを呼び出している可能性があります。必要に応じて`maya_mock.py`に実装を追加してください。

### 状態の汚染
前のテストの状態が残っている場合は、`setUp`メソッドで`cmds.reset()`を確実に呼び出してください。

## 拡張方法

### 新しいcmdsメソッドの追加

`maya_mock.py`の`CmdsMock`クラスに新しいメソッドを追加します:

```python
def newMethod(self, **kwargs):
    """新しいメソッドのモック実装"""
    # 実装を追加
    return "result"
```

### 新しいOpenMayaクラスの追加

`maya_mock.py`の`OpenMayaMock`クラスに新しいクラスを追加します:

```python
class MNewClass:
    """新しいクラスのモック"""
    def __init__(self):
        pass
```

### 新しいヘルパー関数の追加

`maya_mock_helpers.py`に新しいヘルパー関数を追加します:

```python
@staticmethod
def create_new_object():
    """新しいオブジェクトを作成"""
    # 実装を追加
    return object_info
```