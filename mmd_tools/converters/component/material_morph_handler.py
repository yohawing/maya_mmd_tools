"""
MaterialMorphHandler: 材質モーフの変換処理

このモジュールは、PMXの材質モーフをMayaのマテリアルアニメーションに変換する機能を提供します。
"""

from typing import Any, Dict, List

from maya import cmds

from mmd_tools.core import maya_utils
from mmd_tools.core.pmx_data.morph import PmxMorphType
from .base_morph_handler import BaseMorphHandler


class MaterialMorphHandler(BaseMorphHandler):
    """材質モーフ（PMX専用）の変換を処理"""

    def can_handle(self, morph_data: Any) -> bool:
        if hasattr(morph_data, "morph_type"):
            return morph_data.morph_type == PmxMorphType.MaterialMorph
        return False

    def convert(self, morph_data: Any, mesh_node: str, **kwargs) -> Dict[str, Any]:
        """材質モーフをMayaのマテリアルアニメーションに変換"""
        try:
            # 入力検証
            if not self.validate_input(morph_data, mesh_node):
                return {"success": False, "error": "Input validation failed"}

            # マテリアルごとにアニメーション可能なアトリビュートを作成
            animation_nodes: List[str] = []

            for offset in morph_data.offsets:
                material_animation = self._setup_material_animation(offset, mesh_node)
                animation_nodes.extend(material_animation)

            return {
                "success": True,
                "animation_nodes": animation_nodes,
                "morph_name": morph_data.name,
            }

        except Exception as e:
            self.logger.error(
                f"Failed to convert material morph {morph_data.name}: {e}"
            )
            return {"success": False, "error": str(e)}

    def _setup_material_animation(
        self, offset: Dict[str, Any], mesh_node: str
    ) -> List[str]:
        """マテリアルアニメーションをセットアップ"""
        animation_nodes: List[str] = []

        try:
            material_index = offset.get("material_index", -1)
            operation_type = offset.get("operation_type", 0)  # 0=乗算, 1=加算

            # メッシュに割り当てられているマテリアルを取得
            materials = self._get_mesh_materials(mesh_node)

            if material_index >= 0 and material_index < len(materials):
                material_node = materials[material_index]

                # 各マテリアルプロパティのアニメーション用アトリビュートを作成
                properties = [
                    "diffuse",
                    "specular",
                    "ambient",
                    "edge_color",
                    "texture_tint_color",
                    "sphere_texture_tint_color",
                    "toon_texture_tint_color",
                ]

                for prop in properties:
                    if prop in offset:
                        anim_node = self._create_material_animation_attribute(
                            material_node, prop, offset[prop], operation_type
                        )
                        if anim_node:
                            animation_nodes.append(anim_node)

        except Exception as e:
            self.logger.warning(f"Failed to setup material animation: {e}")

        return animation_nodes

    def _get_mesh_materials(self, mesh_node: str) -> List[str]:
        """メッシュに割り当てられているマテリアルを取得"""
        materials: List[str] = []
        try:
            shading_engines = cmds.listConnections(  # type: ignore
                mesh_node, type="shadingEngine"
            )
            if shading_engines:
                for sg in set(shading_engines):
                    material_connections = cmds.listConnections(  # type: ignore
                        f"{sg}.surfaceShader"
                    )
                    if material_connections:
                        materials.extend(material_connections)
        except Exception as e:
            self.logger.warning(f"Failed to get mesh materials: {e}")

        return list(set(materials))  # 重複を除去

    def _create_material_animation_attribute(
        self,
        material_node: str,
        property_name: str,
        values: List[float],
        operation_type: int,
    ) -> str:
        """マテリアルアニメーション用のアトリビュートを作成"""
        try:
            attr_name = f"{property_name}_morph"

            # アトリビュートが存在しない場合は作成
            if not cmds.attributeQuery(attr_name, node=material_node, exists=True):  # type: ignore
                cmds.addAttr(  # type: ignore
                    material_node,
                    longName=attr_name,
                    attributeType="float3",
                    keyable=True,
                    defaultValue=(0.0, 0.0, 0.0),
                )

            # エクスプレッション作成（実際の実装では、より高度な制御が必要）
            expression_name = f"{material_node}_{property_name}_morph_expr"

            # 簡単なエクスプレッションの例
            expression_code = f"""
            // Material morph for {property_name}
            // Operation type: {operation_type} (0=multiply, 1=add)
            """

            # 実際のマテリアルプロパティの接続は、材質の種類に応じて実装
            self.logger.info(f"Created material animation attribute: {attr_name}")

            return attr_name

        except Exception as e:
            self.logger.warning(f"Failed to create material animation attribute: {e}")
            return ""
