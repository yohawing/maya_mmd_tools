"""
Maya APIのモック機能を提供するモジュール

このモジュールは、Maya環境なしでユニットテストを実行するための
モックオブジェクトとヘルパー関数を提供します。
"""

from unittest.mock import MagicMock, Mock
from typing import Dict, List, Optional, Any, Tuple
import sys


class MayaMockBase:
    """Maya APIモックの基底クラス"""
    
    def __init__(self):
        self._scene_objects: Dict[str, Dict[str, Any]] = {}
        self._selected_objects: List[str] = []
        self._current_time: float = 0.0
        self._playback_range: Tuple[float, float] = (0.0, 100.0)
        self._fps: float = 30.0
    
    def reset(self):
        """モックの状態をリセット"""
        self._scene_objects.clear()
        self._selected_objects.clear()
        self._current_time = 0.0
        self._playback_range = (0.0, 100.0)
        self._fps = 30.0


class CmdsMock(MayaMockBase):
    """maya.cmdsモジュールのモック"""
    
    def __init__(self):
        super().__init__()
        self._keyframes: Dict[str, Dict[str, List[Tuple[float, float]]]] = {}
    
    # Object creation
    def joint(self, **kwargs) -> str:
        """ジョイントを作成"""
        name = kwargs.get("name", f"joint{len(self._scene_objects) + 1}")
        position = kwargs.get("position", (0, 0, 0))
        
        self._scene_objects[name] = {
            "type": "joint",
            "position": position,
            "rotation": (0, 0, 0),
            "scale": (1, 1, 1),
            "parent": None,
            "children": [],
        }
        
        return name
    
    def polyCube(self, **kwargs) -> List[str]:
        """キューブメッシュを作成"""
        name = kwargs.get("name", f"pCube{len(self._scene_objects) + 1}")
        
        self._scene_objects[name] = {
            "type": "mesh",
            "vertices": [],
            "faces": [],
            "uvs": [],
            "normals": [],
            "parent": None,
            "children": [],
        }
        
        shape_name = f"{name}Shape"
        self._scene_objects[shape_name] = {
            "type": "shape",
            "parent": name,
        }
        
        return [name, shape_name]
    
    def group(self, *objects, **kwargs) -> str:
        """グループを作成"""
        name = kwargs.get("name", kwargs.get("n", f"group{len(self._scene_objects) + 1}"))
        
        self._scene_objects[name] = {
            "type": "transform",
            "parent": None,
            "children": list(objects),
        }
        
        for obj in objects:
            if obj in self._scene_objects:
                self._scene_objects[obj]["parent"] = name
        
        return name
    
    # Object query
    def ls(self, *args, **kwargs) -> List[str]:
        """シーン内のオブジェクトをリスト"""
        type_filter = kwargs.get("type")
        selection = kwargs.get("selection", kwargs.get("sl", False))
        
        if selection:
            return self._selected_objects.copy()
        
        result = []
        for name, obj in self._scene_objects.items():
            if type_filter:
                if obj.get("type") == type_filter:
                    result.append(name)
            else:
                result.append(name)
        
        return result
    
    def objExists(self, obj: str) -> bool:
        """オブジェクトの存在を確認"""
        return obj in self._scene_objects
    
    def nodeType(self, obj: str) -> Optional[str]:
        """ノードタイプを取得"""
        if obj in self._scene_objects:
            return self._scene_objects[obj].get("type")
        return None
    
    # Attribute operations
    def getAttr(self, attr: str) -> Any:
        """アトリビュートを取得"""
        obj_name, attr_name = attr.split(".", 1)
        
        if obj_name not in self._scene_objects:
            raise ValueError(f"Object {obj_name} does not exist")
        
        obj = self._scene_objects[obj_name]
        
        # 基本的なトランスフォーム属性
        if attr_name == "translateX":
            return obj.get("position", (0, 0, 0))[0]
        elif attr_name == "translateY":
            return obj.get("position", (0, 0, 0))[1]
        elif attr_name == "translateZ":
            return obj.get("position", (0, 0, 0))[2]
        elif attr_name == "rotateX":
            return obj.get("rotation", (0, 0, 0))[0]
        elif attr_name == "rotateY":
            return obj.get("rotation", (0, 0, 0))[1]
        elif attr_name == "rotateZ":
            return obj.get("rotation", (0, 0, 0))[2]
        elif attr_name == "scaleX":
            return obj.get("scale", (1, 1, 1))[0]
        elif attr_name == "scaleY":
            return obj.get("scale", (1, 1, 1))[1]
        elif attr_name == "scaleZ":
            return obj.get("scale", (1, 1, 1))[2]
        
        return 0.0
    
    def setAttr(self, attr: str, *values, **kwargs) -> None:
        """アトリビュートを設定"""
        obj_name, attr_name = attr.split(".", 1)
        
        if obj_name not in self._scene_objects:
            raise ValueError(f"Object {obj_name} does not exist")
        
        obj = self._scene_objects[obj_name]
        
        # 基本的なトランスフォーム属性
        if attr_name.startswith("translate"):
            pos = list(obj.get("position", [0, 0, 0]))
            if attr_name == "translateX":
                pos[0] = values[0]
            elif attr_name == "translateY":
                pos[1] = values[0]
            elif attr_name == "translateZ":
                pos[2] = values[0]
            obj["position"] = tuple(pos)
        elif attr_name.startswith("rotate"):
            rot = list(obj.get("rotation", [0, 0, 0]))
            if attr_name == "rotateX":
                rot[0] = values[0]
            elif attr_name == "rotateY":
                rot[1] = values[0]
            elif attr_name == "rotateZ":
                rot[2] = values[0]
            obj["rotation"] = tuple(rot)
        elif attr_name.startswith("scale"):
            scale = list(obj.get("scale", [1, 1, 1]))
            if attr_name == "scaleX":
                scale[0] = values[0]
            elif attr_name == "scaleY":
                scale[1] = values[0]
            elif attr_name == "scaleZ":
                scale[2] = values[0]
            obj["scale"] = tuple(scale)
    
    # Animation
    def setKeyframe(self, obj: str, **kwargs) -> None:
        """キーフレームを設定"""
        attribute = kwargs.get("attribute", kwargs.get("at"))
        value = kwargs.get("value", kwargs.get("v"))
        time = kwargs.get("time", kwargs.get("t", self._current_time))
        
        if obj not in self._keyframes:
            self._keyframes[obj] = {}
        
        if attribute not in self._keyframes[obj]:
            self._keyframes[obj][attribute] = []
        
        self._keyframes[obj][attribute].append((time, value))
    
    def currentTime(self, time: Optional[float] = None) -> float:
        """現在時間を取得/設定"""
        if time is not None:
            self._current_time = time
        return self._current_time
    
    def playbackOptions(self, **kwargs) -> Dict[str, Any]:
        """プレイバックオプションを取得/設定"""
        if "minTime" in kwargs:
            self._playback_range = (kwargs["minTime"], self._playback_range[1])
        if "maxTime" in kwargs:
            self._playback_range = (self._playback_range[0], kwargs["maxTime"])
        if "fps" in kwargs:
            self._fps = kwargs["fps"]
        
        return {
            "minTime": self._playback_range[0],
            "maxTime": self._playback_range[1],
            "fps": self._fps,
        }
    
    # Parent/Child
    def parent(self, child: str, parent: str, **kwargs) -> None:
        """親子関係を設定"""
        if child in self._scene_objects and parent in self._scene_objects:
            # 既存の親から削除
            old_parent = self._scene_objects[child].get("parent")
            if old_parent and old_parent in self._scene_objects:
                children = self._scene_objects[old_parent].get("children", [])
                if child in children:
                    children.remove(child)
            
            # 新しい親に追加
            self._scene_objects[child]["parent"] = parent
            if "children" not in self._scene_objects[parent]:
                self._scene_objects[parent]["children"] = []
            self._scene_objects[parent]["children"].append(child)
    
    # Selection
    def select(self, *objects, **kwargs) -> None:
        """オブジェクトを選択"""
        if kwargs.get("clear", kwargs.get("cl", False)):
            self._selected_objects.clear()
        
        if kwargs.get("add", False):
            self._selected_objects.extend(objects)
        else:
            self._selected_objects = list(objects)
    
    # File operations
    def file(self, **kwargs) -> Optional[str]:
        """ファイル操作のモック"""
        if kwargs.get("new"):
            self.reset()
        return None


class OpenMayaMock:
    """maya.api.OpenMayaモジュールのモック"""
    
    class MVector:
        """MVectorクラスのモック"""
        def __init__(self, x=0.0, y=0.0, z=0.0):
            self.x = x
            self.y = y
            self.z = z
        
        def __repr__(self):
            return f"MVector({self.x}, {self.y}, {self.z})"
    
    class MQuaternion:
        """MQuaternionクラスのモック"""
        def __init__(self, x=0.0, y=0.0, z=0.0, w=1.0):
            self.x = x
            self.y = y
            self.z = z
            self.w = w
        
        def asEulerRotation(self):
            """オイラー角に変換（簡易実装）"""
            euler = OpenMayaMock.MEulerRotation()
            # 実際の変換ロジックは省略
            return euler
    
    class MEulerRotation:
        """MEulerRotationクラスのモック"""
        def __init__(self, x=0.0, y=0.0, z=0.0):
            self.x = x
            self.y = y
            self.z = z
        
        def reorderIt(self, order):
            """回転順序を変更（モック実装）"""
            pass
    
    class MMatrix:
        """MMatrixクラスのモック"""
        def __init__(self):
            self.data = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    
    class MTransformationMatrix:
        """MTransformationMatrixクラスのモック"""
        def __init__(self):
            self._translation = (0, 0, 0)
            self._rotation = (0, 0, 0)
            self._scale = (1, 1, 1)
        
        def setTranslation(self, vector, space):
            self._translation = (vector.x, vector.y, vector.z)
        
        def setRotation(self, quaternion):
            self._rotation = quaternion
        
        def setScale(self, scale, space):
            self._scale = scale
    
    # 回転順序の定数
    kXYZ = 0
    kYZX = 1
    kZXY = 2
    kXZY = 3
    kYXZ = 4
    kZYX = 5
    
    # スペースの定数
    kWorld = 0
    kObject = 1
    kTransform = 2


class MayaMockSetup:
    """Mayaモックをセットアップするユーティリティクラス"""
    
    @staticmethod
    def setup_maya_mocks():
        """Maya関連のモジュールをモックでセットアップ"""
        # maya.cmdsのモック
        cmds_mock = CmdsMock()
        maya_mock = MagicMock()
        maya_mock.cmds = cmds_mock
        
        # maya.api.OpenMayaのモック
        om_mock = OpenMayaMock()
        maya_mock.api = MagicMock()
        maya_mock.api.OpenMaya = om_mock
        
        # システムモジュールに登録
        sys.modules["maya"] = maya_mock
        sys.modules["maya.cmds"] = cmds_mock
        sys.modules["maya.api"] = maya_mock.api
        sys.modules["maya.api.OpenMaya"] = om_mock
        
        return maya_mock, cmds_mock, om_mock
    
    @staticmethod
    def teardown_maya_mocks():
        """Mayaモックをクリーンアップ"""
        modules_to_remove = [
            "maya",
            "maya.cmds",
            "maya.api",
            "maya.api.OpenMaya",
        ]
        
        for module in modules_to_remove:
            if module in sys.modules:
                del sys.modules[module]


# Test helper functions
def create_mock_joint_hierarchy(joint_names: List[str]) -> Dict[str, str]:
    """モックジョイント階層を作成するヘルパー関数
    
    Args:
        joint_names: ジョイント名のリスト（親から子の順）
    
    Returns:
        ジョイント名とその親のマッピング
    """
    cmds = sys.modules.get("maya.cmds")
    if not cmds:
        raise RuntimeError("Maya mocks are not set up")
    
    hierarchy = {}
    parent = None
    
    for name in joint_names:
        joint = cmds.joint(name=name)
        if parent:
            cmds.parent(joint, parent)
        hierarchy[joint] = parent
        parent = joint
    
    return hierarchy


def create_mock_mesh(name: str, vertices: List[Tuple[float, float, float]], 
                    faces: List[List[int]]) -> str:
    """モックメッシュを作成するヘルパー関数
    
    Args:
        name: メッシュ名
        vertices: 頂点座標のリスト
        faces: 面インデックスのリスト
    
    Returns:
        作成されたメッシュ名
    """
    cmds = sys.modules.get("maya.cmds")
    if not cmds:
        raise RuntimeError("Maya mocks are not set up")
    
    mesh, shape = cmds.polyCube(name=name)
    
    # メッシュデータを設定
    if mesh in cmds._scene_objects:
        cmds._scene_objects[mesh]["vertices"] = vertices
        cmds._scene_objects[mesh]["faces"] = faces
    
    return mesh