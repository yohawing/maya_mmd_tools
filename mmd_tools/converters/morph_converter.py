"""
MMDのモーフデータをMayaのblendShapeに変換するモジュール。

このモジュールは、PMD/PMXファイルのモーフデータを解析し、
Mayaのブレンドシェイプシステムに変換する機能を提供します。
"""

from typing import Any, Dict, List

from maya import cmds
from maya.api import OpenMaya as om

from mmd_tools.core import maya_utils
from mmd_tools.core.pmx_data.morph import PmxMorphType


class MorphConverter:
    """MMDのモーフデータをMayaのblendShapeに変換するクラス"""

    def __init__(self):
        from mmd_tools import settings

        self.settings = settings.get("import.morph", {})

    def convert_pmd_morphs(self, pmd_data, mesh_node: str) -> Dict[str, Any]:
        """
        PMDのモーフデータをMayaのブレンドシェイプに変換する。

        Args:
            pmd_data: 解析されたPMDデータオブジェクト
            mesh_node (str): ブレンドシェイプを適用するMayaのメッシュノード

        Returns:
            Dict[str, Any]: 変換結果の辞書
        """
        if not self.settings.get("import_morphs", True):
            return {"success": True, "morphs_converted": 0}

        results = []
        blend_shape_nodes = []

        for morph in pmd_data.morphs:
            # ベースモーフはスキップ
            if morph.morph_type == 0:
                continue

            try:
                result = self._convert_vertex_morph_pmd(morph, mesh_node)
                if result["success"]:
                    results.append(result)
                    if result["blend_shape_node"] not in blend_shape_nodes:
                        blend_shape_nodes.append(result["blend_shape_node"])
            except Exception:
                # エラーは無視して次のモーフへ
                pass

        return {
            "success": True,
            "morphs_converted": len(results),
            "total_morphs": len(pmd_data.morphs) - 1,  # ベースモーフを除く
            "blend_shape_nodes": blend_shape_nodes,
            "results": results,
        }

    def convert_pmx_morphs(self, pmx_data, mesh_node: str) -> Dict[str, Any]:
        """
        PMXのモーフデータをMayaのブレンドシェイプに変換する。

        Args:
            pmx_data: 解析されたPMXデータオブジェクト
            mesh_node (str): ブレンドシェイプを適用するMayaのメッシュノード

        Returns:
            Dict[str, Any]: 変換結果の辞書
        """
        if not self.settings.get("import_morphs", True):
            return {"success": True, "morphs_converted": 0}

        results = []
        blend_shape_nodes = []

        for morph in pmx_data.morphs:
            try:
                # 現在は頂点モーフのみ対応
                if morph.morph_type == PmxMorphType.VertexMorph:
                    result = self._convert_vertex_morph_pmx(morph, mesh_node)
                    if result["success"]:
                        results.append(result)
                        if result["blend_shape_node"] not in blend_shape_nodes:
                            blend_shape_nodes.append(result["blend_shape_node"])
            except Exception:
                # エラーは無視して次のモーフへ
                pass

        return {
            "success": True,
            "morphs_converted": len(results),
            "total_morphs": len(pmx_data.morphs),
            "blend_shape_nodes": blend_shape_nodes,
            "results": results,
        }

    def _convert_vertex_morph_pmd(self, morph, mesh_node: str) -> Dict[str, Any]:
        """PMD頂点モーフの変換"""
        # モーフ名をMaya互換に変換
        morph_name = maya_utils.sanitize_text(morph.name)

        # メッシュを複製してターゲットを作成
        target_mesh = cmds.duplicate(mesh_node)[0]
        target_mesh = cmds.rename(target_mesh, f"{morph_name}_target")

        # ターゲットメッシュを非表示
        cmds.setAttr(f"{target_mesh}.visibility", 0)

        # 頂点オフセットを適用
        self._apply_vertex_offsets_pmd(target_mesh, morph)

        # blendShapeノードを取得または作成
        blend_shape_node = maya_utils.find_or_create_blendshape_node(mesh_node)

        # 現在のターゲット数を取得
        target_count = cmds.blendShape(blend_shape_node, query=True, target=True)
        target_index = len(target_count) if target_count else 0

        # blendShapeにターゲットを追加
        cmds.blendShape(
            blend_shape_node,
            edit=True,
            target=(mesh_node, target_index, target_mesh, 1.0),
        )

        # ターゲットの名前を設定
        cmds.aliasAttr(morph_name, f"{blend_shape_node}.w[{target_index}]")

        return {
            "success": True,
            "morph_name": morph.name,
            "blend_shape_node": blend_shape_node,
            "target_index": target_index,
        }

    def _convert_vertex_morph_pmx(self, morph, mesh_node: str) -> Dict[str, Any]:
        """PMX頂点モーフの変換"""
        # モーフ名をMaya互換に変換
        morph_name = maya_utils.sanitize_text(morph.name)

        # メッシュを複製してターゲットを作成
        target_mesh = cmds.duplicate(mesh_node)[0]
        target_mesh = cmds.rename(target_mesh, f"{morph_name}_target")

        # ターゲットメッシュを非表示
        cmds.setAttr(f"{target_mesh}.visibility", 0)

        # 頂点オフセットを適用
        self._apply_vertex_offsets_pmx(target_mesh, morph)

        # blendShapeノードを取得または作成
        blend_shape_node = maya_utils.find_or_create_blendshape_node(mesh_node)

        # 現在のターゲット数を取得
        target_count = cmds.blendShape(blend_shape_node, query=True, target=True)
        target_index = len(target_count) if target_count else 0

        # blendShapeにターゲットを追加
        cmds.blendShape(
            blend_shape_node,
            edit=True,
            target=(mesh_node, target_index, target_mesh, 1.0),
        )

        # ターゲットの名前を設定
        cmds.aliasAttr(morph_name, f"{blend_shape_node}.w[{target_index}]")

        return {
            "success": True,
            "morph_name": morph.name,
            "blend_shape_node": blend_shape_node,
            "target_index": target_index,
        }

    def _apply_vertex_offsets_pmd(self, mesh_node: str, morph):
        """PMDの頂点オフセットを適用"""
        # MSelectionListを使用してDAGパスを取得
        sel_list = om.MSelectionList()
        sel_list.add(mesh_node)
        dag_path = sel_list.getDagPath(0)

        # MFnMeshを取得
        mesh_fn = om.MFnMesh(dag_path)

        # 現在の頂点位置を取得
        points = mesh_fn.getPoints(om.MSpace.kObject)

        # モーフオフセットを適用
        for vertex_index, offset_pos in morph.vertices:
            if vertex_index < len(points):
                points[vertex_index] += om.MVector(
                    offset_pos[0], offset_pos[1], offset_pos[2]
                )

        # 変更された頂点位置を設定
        mesh_fn.setPoints(points, om.MSpace.kObject)

    def _apply_vertex_offsets_pmx(self, mesh_node: str, morph):
        """PMXの頂点オフセットを適用"""
        # MSelectionListを使用してDAGパスを取得
        sel_list = om.MSelectionList()
        sel_list.add(mesh_node)
        dag_path = sel_list.getDagPath(0)

        # MFnMeshを取得
        mesh_fn = om.MFnMesh(dag_path)

        # 現在の頂点位置を取得
        points = mesh_fn.getPoints(om.MSpace.kObject)

        # モーフオフセットを適用
        if hasattr(morph, "offsets"):
            for offset in morph.offsets:
                if "vertex_index" in offset and "position_offset" in offset:
                    vertex_index = offset["vertex_index"]
                    offset_pos = offset["position_offset"]
                    if vertex_index < len(points):
                        points[vertex_index] += om.MVector(
                            offset_pos[0], offset_pos[1], offset_pos[2]
                        )

        # 変更された頂点位置を設定
        mesh_fn.setPoints(points, om.MSpace.kObject)
