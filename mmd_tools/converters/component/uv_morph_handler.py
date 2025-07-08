"""
UVMorphHandler: UVモーフの変換処理

このモジュールは、PMXのUVモーフをMayaのUVアニメーションシステムに変換する機能を提供します。
"""

from typing import Any, Dict, List

from maya import cmds

from mmd_tools.core import maya_utils
from mmd_tools.core.pmx_data.morph import PmxMorphType
from .base_morph_handler import BaseMorphHandler


class UVMorphHandler(BaseMorphHandler):
    """UVモーフ（PMX専用）の変換を処理"""

    def can_handle(self, morph_data: Any) -> bool:
        if hasattr(morph_data, "morph_type"):
            return (
                PmxMorphType.UVMorph
                <= morph_data.morph_type
                <= PmxMorphType.AdditionalUVMorph4
            )
        return False

    def convert(self, morph_data: Any, mesh_node: str, **kwargs) -> Dict[str, Any]:
        """UVモーフをMayaのUVアニメーションシステムに変換"""
        try:
            # 入力検証
            if not self.validate_input(morph_data, mesh_node):
                return {"success": False, "error": "Input validation failed"}

            # UVセットを作成
            uv_set_result = self._create_uv_morph_target(morph_data, mesh_node)

            # アニメーション用のセットアップ
            animation_setup = self._setup_uv_animation(
                mesh_node, uv_set_result, morph_data
            )

            return {
                "success": True,
                "uv_set_name": uv_set_result["uv_set_name"],
                "animation_nodes": animation_setup,
                "morph_name": morph_data.name,
            }

        except Exception as e:
            self.logger.error(f"Failed to convert UV morph {morph_data.name}: {e}")
            return {"success": False, "error": str(e)}

    def _create_uv_morph_target(
        self, morph_data: Any, mesh_node: str
    ) -> Dict[str, Any]:
        """UVモーフターゲットを作成"""
        uv_set_name = maya_utils.sanitize_text(f"{morph_data.name}_uvs")

        # 新しいUVセットを作成
        cmds.polyUVSet(mesh_node, create=True, uvSet=uv_set_name)  # type: ignore

        # 現在のUVセットをコピー
        cmds.polyUVSet(mesh_node, copy=True, uvSet=uv_set_name)  # type: ignore

        # UVオフセットを適用
        self._apply_uv_offsets(mesh_node, morph_data, uv_set_name)

        return {"uv_set_name": uv_set_name}

    def _apply_uv_offsets(self, mesh_node: str, morph_data: Any, uv_set_name: str):
        """UVオフセットを適用"""
        # アクティブなUVセットを設定
        cmds.polyUVSet(mesh_node, currentUVSet=True, uvSet=uv_set_name)  # type: ignore

        # UVオフセットを適用
        for offset in morph_data.offsets:
            if "vertex_index" in offset and "uv_offset" in offset:
                vertex_index = offset["vertex_index"]
                uv_offset = offset["uv_offset"]

                # 頂点のUV座標を取得
                try:
                    uv_values = cmds.polyEditUV(  # type: ignore
                        f"{mesh_node}.map[{vertex_index}]",
                        query=True,
                        uValue=True,
                        vValue=True,
                    )

                    if uv_values and len(uv_values) >= 2:
                        new_u = uv_values[0] + uv_offset[0]
                        new_v = uv_values[1] + uv_offset[1]

                        # UVオフセットを適用
                        cmds.polyEditUV(  # type: ignore
                            f"{mesh_node}.map[{vertex_index}]",
                            uValue=new_u,
                            vValue=new_v,
                        )

                except Exception as e:
                    self.logger.warning(
                        f"Failed to apply UV offset to vertex {vertex_index}: {e}"
                    )
                    continue

    def _setup_uv_animation(
        self, mesh_node: str, uv_set_result: Dict[str, Any], morph_data: Any
    ) -> List[str]:
        """UVアニメーション用のセットアップ"""
        animation_nodes = []

        # UV切り替え用のアトリビュートを作成
        morph_name = maya_utils.sanitize_text(morph_data.name)
        attr_name = f"{morph_name}_weight"

        if not cmds.attributeQuery(attr_name, node=mesh_node, exists=True):  # type: ignore
            cmds.addAttr(  # type: ignore
                mesh_node,
                longName=attr_name,
                attributeType="float",
                min=0.0,
                max=1.0,
                defaultValue=0.0,
                keyable=True,
            )

        # blendShapeのような動作をするためのセットアップ
        # 注意: MayaのUVモーフは複雑なので、単純化した実装
        # 実際の使用では、より高度なUVアニメーションシステムが必要

        return animation_nodes
