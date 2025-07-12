"""
Maya APIモックのヘルパー関数とファクトリクラス

このモジュールは、テストで頻繁に使用されるMayaオブジェクトの
作成を簡単にするヘルパー関数を提供します。
"""

from typing import List, Dict, Tuple, Optional, Any
import math
import sys


class MayaMockFactory:
    """テスト用のMayaオブジェクトを作成するファクトリクラス"""
    
    @staticmethod
    def create_mmd_bone_hierarchy() -> Dict[str, str]:
        """MMD標準ボーン階層を作成
        
        Returns:
            ボーン名とMayaジョイント名のマッピング
        """
        cmds = sys.modules.get("maya.cmds")
        if not cmds:
            raise RuntimeError("Maya mocks are not set up")
        
        # MMD標準ボーン構造
        bone_structure = [
            ("センター", None, (0, 8, 0)),
            ("上半身", "センター", (0, 10, 0)),
            ("首", "上半身", (0, 14, 0)),
            ("頭", "首", (0, 16, 0)),
            ("左肩", "上半身", (-1, 14, 0)),
            ("左腕", "左肩", (-3, 14, 0)),
            ("左ひじ", "左腕", (-5, 14, 0)),
            ("左手首", "左ひじ", (-7, 14, 0)),
            ("右肩", "上半身", (1, 14, 0)),
            ("右腕", "右肩", (3, 14, 0)),
            ("右ひじ", "右腕", (5, 14, 0)),
            ("右手首", "右ひじ", (7, 14, 0)),
            ("下半身", "センター", (0, 6, 0)),
            ("左足", "下半身", (-1, 4, 0)),
            ("左ひざ", "左足", (-1, 2, 0)),
            ("左足首", "左ひざ", (-1, 0, 0)),
            ("右足", "下半身", (1, 4, 0)),
            ("右ひざ", "右足", (1, 2, 0)),
            ("右足首", "右ひざ", (1, 0, 0)),
        ]
        
        bone_mapping = {}
        maya_joints = {}
        
        for mmd_name, parent_mmd_name, position in bone_structure:
            # Mayaジョイント名を作成（英語名）
            maya_name = _get_maya_joint_name(mmd_name)
            
            # ジョイントを作成
            joint = cmds.joint(name=maya_name, position=position)
            
            # 親がある場合は親子関係を設定
            if parent_mmd_name and parent_mmd_name in bone_mapping:
                parent_maya_name = bone_mapping[parent_mmd_name]
                cmds.parent(joint, parent_maya_name)
            
            bone_mapping[mmd_name] = maya_name
            maya_joints[maya_name] = joint
        
        return bone_mapping
    
    @staticmethod
    def create_mmd_ik_setup(bone_mapping: Dict[str, str]) -> Dict[str, Any]:
        """MMD IKセットアップを作成
        
        Args:
            bone_mapping: MMDボーン名とMayaジョイント名のマッピング
        
        Returns:
            IKハンドルとコントローラの情報
        """
        cmds = sys.modules.get("maya.cmds")
        if not cmds:
            raise RuntimeError("Maya mocks are not set up")
        
        ik_info = {}
        
        # 左足IK
        if "左足首" in bone_mapping and "左足" in bone_mapping:
            ik_handle = f"{bone_mapping['左足首']}_ikHandle"
            ik_ctrl = f"{bone_mapping['左足首']}_ikCtrl"
            
            # モックでIKハンドルを表現
            cmds._scene_objects[ik_handle] = {
                "type": "ikHandle",
                "startJoint": bone_mapping["左足"],
                "endEffector": bone_mapping["左足首"],
                "parent": None,
                "children": [],
            }
            
            # IKコントローラ
            cmds._scene_objects[ik_ctrl] = {
                "type": "transform",
                "position": cmds._scene_objects[bone_mapping["左足首"]]["position"],
                "rotation": (0, 0, 0),
                "scale": (1, 1, 1),
                "parent": None,
                "children": [ik_handle],
            }
            
            ik_info["left_leg_ik"] = {
                "handle": ik_handle,
                "controller": ik_ctrl,
                "start": bone_mapping["左足"],
                "end": bone_mapping["左足首"],
            }
        
        # 右足IK
        if "右足首" in bone_mapping and "右足" in bone_mapping:
            ik_handle = f"{bone_mapping['右足首']}_ikHandle"
            ik_ctrl = f"{bone_mapping['右足首']}_ikCtrl"
            
            cmds._scene_objects[ik_handle] = {
                "type": "ikHandle",
                "startJoint": bone_mapping["右足"],
                "endEffector": bone_mapping["右足首"],
                "parent": None,
                "children": [],
            }
            
            cmds._scene_objects[ik_ctrl] = {
                "type": "transform",
                "position": cmds._scene_objects[bone_mapping["右足首"]]["position"],
                "rotation": (0, 0, 0),
                "scale": (1, 1, 1),
                "parent": None,
                "children": [ik_handle],
            }
            
            ik_info["right_leg_ik"] = {
                "handle": ik_handle,
                "controller": ik_ctrl,
                "start": bone_mapping["右足"],
                "end": bone_mapping["右足首"],
            }
        
        return ik_info
    
    @staticmethod
    def create_mmd_mesh(name: str = "mmd_model") -> Dict[str, Any]:
        """MMDモデル用のメッシュを作成
        
        Args:
            name: メッシュ名
        
        Returns:
            メッシュ情報
        """
        cmds = sys.modules.get("maya.cmds")
        if not cmds:
            raise RuntimeError("Maya mocks are not set up")
        
        # 簡単な立方体メッシュを作成
        vertices = [
            (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
            (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
        ]
        
        faces = [
            [0, 1, 2, 3],  # 前面
            [4, 5, 6, 7],  # 後面
            [0, 1, 5, 4],  # 下面
            [2, 3, 7, 6],  # 上面
            [0, 3, 7, 4],  # 左面
            [1, 2, 6, 5],  # 右面
        ]
        
        mesh, shape = cmds.polyCube(name=name)
        
        # メッシュデータを設定
        if mesh in cmds._scene_objects:
            cmds._scene_objects[mesh]["vertices"] = vertices
            cmds._scene_objects[mesh]["faces"] = faces
            cmds._scene_objects[mesh]["uvs"] = [(0, 0), (1, 0), (1, 1), (0, 1)] * 6
            cmds._scene_objects[mesh]["normals"] = [(0, 0, 1)] * len(vertices)
        
        # スキンクラスタのモック
        skin_cluster = f"{name}_skinCluster"
        cmds._scene_objects[skin_cluster] = {
            "type": "skinCluster",
            "geometry": mesh,
            "influences": [],
            "weights": {},
        }
        
        return {
            "mesh": mesh,
            "shape": shape,
            "skin_cluster": skin_cluster,
            "vertices": vertices,
            "faces": faces,
        }
    
    @staticmethod
    def create_material(name: str, color: Tuple[float, float, float] = (0.5, 0.5, 0.5),
                       texture: Optional[str] = None) -> Dict[str, Any]:
        """マテリアルを作成
        
        Args:
            name: マテリアル名
            color: 拡散色 (R, G, B)
            texture: テクスチャファイル名
        
        Returns:
            マテリアル情報
        """
        cmds = sys.modules.get("maya.cmds")
        if not cmds:
            raise RuntimeError("Maya mocks are not set up")
        
        # シェーダーノード
        shader = f"{name}_shader"
        cmds._scene_objects[shader] = {
            "type": "lambert",
            "color": color,
            "transparency": (0, 0, 0),
            "ambientColor": (0, 0, 0),
            "incandescence": (0, 0, 0),
            "diffuse": 0.8,
        }
        
        # シェーディンググループ
        shading_group = f"{name}SG"
        cmds._scene_objects[shading_group] = {
            "type": "shadingEngine",
            "surfaceShader": shader,
            "members": [],
        }
        
        material_info = {
            "shader": shader,
            "shading_group": shading_group,
            "color": color,
        }
        
        # テクスチャがある場合
        if texture:
            file_node = f"{name}_file"
            place2d = f"{name}_place2dTexture"
            
            cmds._scene_objects[file_node] = {
                "type": "file",
                "fileTextureName": texture,
                "outColor": color,
            }
            
            cmds._scene_objects[place2d] = {
                "type": "place2dTexture",
                "repeatU": 1.0,
                "repeatV": 1.0,
                "offsetU": 0.0,
                "offsetV": 0.0,
            }
            
            material_info["texture"] = file_node
            material_info["place2d"] = place2d
        
        return material_info
    
    @staticmethod
    def create_blend_shape(base_mesh: str, target_name: str, 
                          vertex_deltas: List[Tuple[int, Tuple[float, float, float]]]) -> str:
        """ブレンドシェイプ（モーフ）を作成
        
        Args:
            base_mesh: ベースメッシュ名
            target_name: ターゲット名
            vertex_deltas: 頂点インデックスと移動量のリスト
        
        Returns:
            ブレンドシェイプノード名
        """
        cmds = sys.modules.get("maya.cmds")
        if not cmds:
            raise RuntimeError("Maya mocks are not set up")
        
        blend_shape = f"{base_mesh}_blendShape"
        
        if blend_shape not in cmds._scene_objects:
            cmds._scene_objects[blend_shape] = {
                "type": "blendShape",
                "base_mesh": base_mesh,
                "targets": {},
                "weights": {},
            }
        
        # ターゲットを追加
        cmds._scene_objects[blend_shape]["targets"][target_name] = {
            "vertex_deltas": vertex_deltas,
            "weight": 0.0,
        }
        
        # ウェイトアトリビュート
        cmds._scene_objects[blend_shape]["weights"][target_name] = 0.0
        
        return blend_shape


class AnimationMockHelper:
    """アニメーション関連のモックヘルパー"""
    
    @staticmethod
    def create_animation_curve(obj: str, attr: str, keys: List[Tuple[float, float]]) -> str:
        """アニメーションカーブを作成
        
        Args:
            obj: オブジェクト名
            attr: アトリビュート名
            keys: (時間, 値) のリスト
        
        Returns:
            アニメーションカーブノード名
        """
        cmds = sys.modules.get("maya.cmds")
        if not cmds:
            raise RuntimeError("Maya mocks are not set up")
        
        # キーフレームを設定
        for time, value in keys:
            cmds.currentTime(time)
            cmds.setKeyframe(obj, attribute=attr, value=value, time=time)
        
        # アニメーションカーブノードのモック
        anim_curve = f"{obj}_{attr}_animCurve"
        cmds._scene_objects[anim_curve] = {
            "type": "animCurveTU",  # Time-Unit curve
            "keys": keys,
            "input": "time",
            "output": f"{obj}.{attr}",
        }
        
        return anim_curve
    
    @staticmethod
    def create_vmd_animation(bone_mapping: Dict[str, str], 
                           bone_frames: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[str]]:
        """VMDアニメーションデータからMayaアニメーションを作成
        
        Args:
            bone_mapping: MMDボーン名とMayaジョイント名のマッピング
            bone_frames: ボーン名ごとのフレームデータ
        
        Returns:
            作成されたアニメーションカーブの情報
        """
        cmds = sys.modules.get("maya.cmds")
        if not cmds:
            raise RuntimeError("Maya mocks are not set up")
        
        created_curves = {}
        
        for mmd_bone_name, frames in bone_frames.items():
            if mmd_bone_name not in bone_mapping:
                continue
            
            maya_joint = bone_mapping[mmd_bone_name]
            created_curves[maya_joint] = []
            
            # 各アトリビュートのキーを準備
            tx_keys = []
            ty_keys = []
            tz_keys = []
            rx_keys = []
            ry_keys = []
            rz_keys = []
            
            for frame in frames:
                time = frame["frame_number"]
                pos = frame.get("position", (0, 0, 0))
                rot = frame.get("rotation", (0, 0, 0))
                
                tx_keys.append((time, pos[0]))
                ty_keys.append((time, pos[1]))
                tz_keys.append((time, pos[2]))
                rx_keys.append((time, math.degrees(rot[0])))
                ry_keys.append((time, math.degrees(rot[1])))
                rz_keys.append((time, math.degrees(rot[2])))
            
            # アニメーションカーブを作成
            if tx_keys:
                curve = AnimationMockHelper.create_animation_curve(maya_joint, "translateX", tx_keys)
                created_curves[maya_joint].append(curve)
            if ty_keys:
                curve = AnimationMockHelper.create_animation_curve(maya_joint, "translateY", ty_keys)
                created_curves[maya_joint].append(curve)
            if tz_keys:
                curve = AnimationMockHelper.create_animation_curve(maya_joint, "translateZ", tz_keys)
                created_curves[maya_joint].append(curve)
            if rx_keys:
                curve = AnimationMockHelper.create_animation_curve(maya_joint, "rotateX", rx_keys)
                created_curves[maya_joint].append(curve)
            if ry_keys:
                curve = AnimationMockHelper.create_animation_curve(maya_joint, "rotateY", ry_keys)
                created_curves[maya_joint].append(curve)
            if rz_keys:
                curve = AnimationMockHelper.create_animation_curve(maya_joint, "rotateZ", rz_keys)
                created_curves[maya_joint].append(curve)
        
        return created_curves


# Helper functions
def _get_maya_joint_name(mmd_name: str) -> str:
    """MMDボーン名からMayaジョイント名を生成
    
    Args:
        mmd_name: MMDボーン名
    
    Returns:
        Mayaジョイント名
    """
    # 簡単な変換テーブル
    name_map = {
        "センター": "center",
        "上半身": "upper_body",
        "首": "neck",
        "頭": "head",
        "左肩": "shoulder_L",
        "左腕": "arm_L",
        "左ひじ": "elbow_L",
        "左手首": "wrist_L",
        "右肩": "shoulder_R",
        "右腕": "arm_R",
        "右ひじ": "elbow_R",
        "右手首": "wrist_R",
        "下半身": "lower_body",
        "左足": "leg_L",
        "左ひざ": "knee_L",
        "左足首": "ankle_L",
        "右足": "leg_R",
        "右ひざ": "knee_R",
        "右足首": "ankle_R",
    }
    
    return name_map.get(mmd_name, mmd_name.replace(" ", "_"))


def create_mock_scene() -> Dict[str, Any]:
    """完全なモックシーンを作成
    
    Returns:
        シーン情報
    """
    cmds = sys.modules.get("maya.cmds")
    if not cmds:
        raise RuntimeError("Maya mocks are not set up")
    
    # 新規シーン
    cmds.file(new=True)
    
    # ボーン階層を作成
    bone_mapping = MayaMockFactory.create_mmd_bone_hierarchy()
    
    # IKセットアップ
    ik_info = MayaMockFactory.create_mmd_ik_setup(bone_mapping)
    
    # メッシュを作成
    mesh_info = MayaMockFactory.create_mmd_mesh("body")
    
    # マテリアルを作成
    material_info = MayaMockFactory.create_material("body_material", 
                                                   color=(1.0, 0.8, 0.7),
                                                   texture="body_texture.png")
    
    # ブレンドシェイプを作成
    blend_shape = MayaMockFactory.create_blend_shape(
        mesh_info["mesh"],
        "smile",
        [(0, (0.1, 0.1, 0)), (1, (0.1, 0.1, 0)), (2, (-0.1, 0.1, 0)), (3, (-0.1, 0.1, 0))]
    )
    
    return {
        "bone_mapping": bone_mapping,
        "ik_info": ik_info,
        "mesh_info": mesh_info,
        "material_info": material_info,
        "blend_shape": blend_shape,
    }