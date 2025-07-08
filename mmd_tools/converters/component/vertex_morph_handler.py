"""
VertexMorphHandler: 頂点モーフの変換処理

このモジュールは、PMD/PMXの頂点モーフをMayaのblendShapeに変換する機能を提供します。
"""

from typing import Any, Dict

from maya import cmds
from maya.api import OpenMaya as om

from mmd_tools.core import maya_utils
from mmd_tools.core.pmx_data.morph import PmxMorphType
from .base_morph_handler import BaseMorphHandler


class VertexMorphHandler(BaseMorphHandler):
    """頂点モーフ（PMD/PMX）の変換を処理"""

    def can_handle(self, morph_data: Any) -> bool:
        if hasattr(morph_data, "morph_type"):
            # PMX
            return morph_data.morph_type == PmxMorphType.VertexMorph
        else:
            # PMD (type 1-4 are all vertex morphs)
            return hasattr(morph_data, "vertices") and morph_data.morph_type > 0

    def convert(self, morph_data: Any, mesh_node: str, **kwargs) -> Dict[str, Any]:
        """頂点モーフをblendShapeターゲットに変換"""
        try:
            # 入力検証
            if not self.validate_input(morph_data, mesh_node):
                return {"success": False, "error": "Input validation failed"}

            # モーフターゲットメッシュを作成
            target_mesh = self._create_morph_target(morph_data, mesh_node)

            # blendShapeノードに追加
            blend_shape_result = self._add_to_blendshape(
                target_mesh, mesh_node, morph_data
            )

            # クリーンアップ
            self._cleanup_temp_objects(target_mesh)

            return {
                "success": True,
                "blend_shape_node": blend_shape_result["blend_shape_node"],
                "target_index": blend_shape_result["target_index"],
                "morph_name": morph_data.name,
            }

        except Exception as e:
            self.logger.error(f"Failed to convert vertex morph {morph_data.name}: {e}")
            return {"success": False, "error": str(e)}

    def _create_morph_target(self, morph_data: Any, base_mesh: str) -> str:
        """モーフターゲットメッシュを作成"""
        # メッシュを複製
        target_mesh = cmds.duplicate(base_mesh)[0]  # type: ignore
        target_mesh = cmds.rename(target_mesh, f"{base_mesh}_morph_temp")

        # 頂点位置を変更（OpenMaya API 2.0使用）
        self._apply_vertex_offsets(target_mesh, morph_data)

        return target_mesh

    def _apply_vertex_offsets(self, mesh_node: str, morph_data: Any):
        """頂点オフセットを適用（OpenMaya API 2.0使用）"""
        # DAGパスを取得
        sel_list = om.MSelectionList()
        sel_list.add(mesh_node)
        dag_path = sel_list.getDagPath(0)

        # MFnMeshを取得
        mesh_fn = om.MFnMesh(dag_path)

        # 現在の頂点位置を取得
        points = mesh_fn.getPoints(om.MSpace.kObject)

        # モーフオフセットを適用
        if hasattr(morph_data, "offsets"):  # PMX
            for offset in morph_data.offsets:
                if "vertex_index" in offset and "position_offset" in offset:
                    vertex_index = offset["vertex_index"]
                    offset_pos = offset["position_offset"]
                    if vertex_index < len(points):
                        points[vertex_index] += om.MVector(
                            offset_pos[0], offset_pos[1], offset_pos[2]
                        )
        else:  # PMD
            for vertex_index, offset_pos in morph_data.vertices:
                if vertex_index < len(points):
                    points[vertex_index] += om.MVector(
                        offset_pos[0], offset_pos[1], offset_pos[2]
                    )

        # 変更された頂点位置を設定
        mesh_fn.setPoints(points, om.MSpace.kObject)

    def _add_to_blendshape(
        self, target_mesh: str, base_mesh: str, morph_data: Any
    ) -> Dict[str, Any]:
        """blendShapeノードにターゲットを追加"""
        morph_name = maya_utils.sanitize_text(morph_data.name)

        # 既存のblendShapeノードを検索
        blend_shape_node = self._find_or_create_blendshape_node(base_mesh)

        # ターゲットを追加
        cmds.blendShape(  # type: ignore
            blend_shape_node, edit=True, target=(base_mesh, -1, target_mesh, 1.0)
        )

        # 現在のターゲット数を取得
        target_indices = cmds.getAttr(f"{blend_shape_node}.weight", size=True)  # type: ignore
        target_index = target_indices - 1

        # ターゲットの名前を設定
        cmds.aliasAttr(  # type: ignore
            morph_name, f"{blend_shape_node}.weight[{target_index}]"
        )

        return {
            "blend_shape_node": blend_shape_node,
            "target_index": target_index,
        }

    def _find_or_create_blendshape_node(self, mesh_node: str) -> str:
        """既存のblendShapeノードを検索または新規作成"""
        return maya_utils.find_or_create_blendshape_node(mesh_node)

    def _cleanup_temp_objects(self, temp_mesh: str):
        """一時オブジェクトをクリーンアップ"""
        maya_utils.cleanup_temp_objects(temp_mesh)
