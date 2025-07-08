"""
BoneMorphHandler: ボーンモーフの変換処理

このモジュールは、PMXのボーンモーフをMayaのボーンアニメーションに変換する機能を提供します。
"""

from typing import Any, Dict, List

from maya import cmds

from mmd_tools.core.pmx_data.morph import PmxMorphType
from .base_morph_handler import BaseMorphHandler


class BoneMorphHandler(BaseMorphHandler):
    """ボーンモーフ（PMX専用）の変換を処理"""

    def can_handle(self, morph_data: Any) -> bool:
        if hasattr(morph_data, "morph_type"):
            return morph_data.morph_type == PmxMorphType.BoneMorph
        return False

    def convert(self, morph_data: Any, mesh_node: str, **kwargs) -> Dict[str, Any]:
        """ボーンモーフをMayaのボーンアニメーションに変換"""
        try:
            # 入力検証
            if not self.validate_input(morph_data, mesh_node):
                return {"success": False, "error": "Input validation failed"}

            # ボーンアニメーション用のセットアップ
            bone_animations: List[Dict[str, Any]] = []

            for offset in morph_data.offsets:
                bone_animation = self._setup_bone_animation(offset, mesh_node)
                if bone_animation:
                    bone_animations.append(bone_animation)

            return {
                "success": True,
                "bone_animations": bone_animations,
                "morph_name": morph_data.name,
            }

        except Exception as e:
            self.logger.error(f"Failed to convert bone morph {morph_data.name}: {e}")
            return {"success": False, "error": str(e)}

    def _setup_bone_animation(
        self, offset: Dict[str, Any], mesh_node: str
    ) -> Dict[str, Any]:
        """ボーンアニメーションをセットアップ"""
        try:
            bone_index = offset.get("bone_index", -1)
            translation = offset.get("translation", [0.0, 0.0, 0.0])
            rotation = offset.get("rotation", [0.0, 0.0, 0.0, 1.0])

            # ボーンインデックスからボーンノードを取得
            # 注意: 実際の実装では、ボーンインデックスからMayaのjointノードを
            # 見つける必要がありますが、これはプロジェクト固有の実装になります
            bone_node = self._find_bone_by_index(bone_index, mesh_node)

            if bone_node:
                # ボーンにモーフ用のアトリビュートを追加
                morph_attrs = self._create_bone_morph_attributes(
                    bone_node, translation, rotation
                )

                return {
                    "bone_node": bone_node,
                    "bone_index": bone_index,
                    "morph_attributes": morph_attrs,
                    "translation": translation,
                    "rotation": rotation,
                }

        except Exception as e:
            self.logger.warning(f"Failed to setup bone animation: {e}")

        return {}

    def _find_bone_by_index(self, bone_index: int, mesh_node: str) -> str:
        """ボーンインデックスからボーンノードを取得"""
        # この実装は簡略化されています
        # 実際の実装では、メッシュに関連付けられたスケルトンから
        # 対応するボーンを見つける必要があります

        try:
            # スキンクラスターを取得
            skin_clusters = cmds.listHistory(mesh_node, type="skinCluster")  # type: ignore
            if skin_clusters:
                influences = cmds.skinCluster(  # type: ignore
                    skin_clusters[0], query=True, influence=True
                )
                if influences and bone_index < len(influences):
                    return influences[bone_index]

        except Exception as e:
            self.logger.warning(f"Failed to find bone by index {bone_index}: {e}")

        return ""

    def _create_bone_morph_attributes(
        self, bone_node: str, translation: List[float], rotation: List[float]
    ) -> Dict[str, str]:
        """ボーンにモーフ用のアトリビュートを追加"""
        morph_attrs: Dict[str, str] = {}

        try:
            # 平行移動用のアトリビュート
            if not cmds.attributeQuery("morphTranslate", node=bone_node, exists=True):  # type: ignore
                cmds.addAttr(  # type: ignore
                    bone_node,
                    longName="morphTranslate",
                    attributeType="float3",
                    keyable=True,
                    defaultValue=(0.0, 0.0, 0.0),
                )
                cmds.addAttr(  # type: ignore
                    bone_node,
                    longName="morphTranslateX",
                    attributeType="float",
                    parent="morphTranslate",
                    keyable=True,
                )
                cmds.addAttr(  # type: ignore
                    bone_node,
                    longName="morphTranslateY",
                    attributeType="float",
                    parent="morphTranslate",
                    keyable=True,
                )
                cmds.addAttr(  # type: ignore
                    bone_node,
                    longName="morphTranslateZ",
                    attributeType="float",
                    parent="morphTranslate",
                    keyable=True,
                )

            # 回転用のアトリビュート
            if not cmds.attributeQuery("morphRotate", node=bone_node, exists=True):  # type: ignore
                cmds.addAttr(  # type: ignore
                    bone_node,
                    longName="morphRotate",
                    attributeType="float3",
                    keyable=True,
                    defaultValue=(0.0, 0.0, 0.0),
                )
                cmds.addAttr(  # type: ignore
                    bone_node,
                    longName="morphRotateX",
                    attributeType="float",
                    parent="morphRotate",
                    keyable=True,
                )
                cmds.addAttr(  # type: ignore
                    bone_node,
                    longName="morphRotateY",
                    attributeType="float",
                    parent="morphRotate",
                    keyable=True,
                )
                cmds.addAttr(  # type: ignore
                    bone_node,
                    longName="morphRotateZ",
                    attributeType="float",
                    parent="morphRotate",
                    keyable=True,
                )

            morph_attrs["translate"] = f"{bone_node}.morphTranslate"
            morph_attrs["rotate"] = f"{bone_node}.morphRotate"

        except Exception as e:
            self.logger.warning(f"Failed to create bone morph attributes: {e}")

        return morph_attrs
