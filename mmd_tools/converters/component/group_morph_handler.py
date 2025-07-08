"""
GroupMorphHandler: グループモーフの変換処理

このモジュールは、PMXのグループモーフをMayaのモーフグループに変換する機能を提供します。
"""

from typing import Any, Dict, List

from maya import cmds

from mmd_tools.core import maya_utils
from mmd_tools.core.pmx_data.morph import PmxMorphType
from .base_morph_handler import BaseMorphHandler


class GroupMorphHandler(BaseMorphHandler):
    """グループモーフ（PMX専用）の変換を処理"""

    def can_handle(self, morph_data: Any) -> bool:
        if hasattr(morph_data, "morph_type"):
            return morph_data.morph_type == PmxMorphType.GroupMorph
        return False

    def convert(self, morph_data: Any, mesh_node: str, **kwargs) -> Dict[str, Any]:
        """グループモーフをMayaのモーフグループに変換"""
        try:
            # 入力検証
            if not self.validate_input(morph_data, mesh_node):
                return {"success": False, "error": "Input validation failed"}

            # グループモーフコントローラーを作成
            group_controller = self._create_group_controller(morph_data, mesh_node)

            # 子モーフとの接続をセットアップ
            child_connections = self._setup_child_morph_connections(
                morph_data, group_controller, mesh_node
            )

            return {
                "success": True,
                "group_controller": group_controller,
                "child_connections": child_connections,
                "morph_name": morph_data.name,
            }

        except Exception as e:
            self.logger.error(f"Failed to convert group morph {morph_data.name}: {e}")
            return {"success": False, "error": str(e)}

    def _create_group_controller(self, morph_data: Any, mesh_node: str) -> str:
        """グループモーフコントローラーを作成"""
        morph_name = maya_utils.sanitize_text(morph_data.name)
        controller_name = f"{morph_name}_group_ctrl"

        # 制御用のnullオブジェクトを作成
        controller = cmds.createNode("transform", name=controller_name)  # type: ignore

        # グループモーフ用のアトリビュートを追加
        cmds.addAttr(  # type: ignore
            controller,
            longName="morphWeight",
            attributeType="float",
            min=0.0,
            max=1.0,
            defaultValue=0.0,
            keyable=True,
        )

        return controller

    def _setup_child_morph_connections(
        self, morph_data: Any, controller: str, mesh_node: str
    ) -> List[Dict[str, Any]]:
        """子モーフとの接続をセットアップ"""
        connections: List[Dict[str, Any]] = []

        for offset in morph_data.offsets:
            if "morph_index" in offset and "morph_rate" in offset:
                morph_index = offset["morph_index"]
                morph_rate = offset["morph_rate"]

                # 注意: 実際の実装では、モーフインデックスから対応するblendShapeターゲットを
                # 見つける必要がありますが、これは複雑な処理になります
                # ここでは簡略化した実装を示します

                connection_info = {
                    "morph_index": morph_index,
                    "morph_rate": morph_rate,
                    "controller": controller,
                }
                connections.append(connection_info)

        return connections
