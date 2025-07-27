"""
MMDのモーフデータをMayaのblendShapeに変換するモジュール。

このモジュールは、PMD/PMXファイルのモーフデータを解析し、
Mayaのブレンドシェイプシステムに変換する機能を提供します。
"""

from typing import Any, Dict, List, Optional, Set, Tuple

from maya import cmds
from maya.api import OpenMaya as om

from mmd_tools.core import maya_utils
from mmd_tools.core.logger import get_logger
from mmd_tools.core.pmx_data.morph import PmxMorphType

logger = get_logger(__name__)


class MorphConverter:
    """MMDのモーフデータをMayaのblendShapeに変換するクラス"""

    def __init__(self):
        from mmd_tools import settings

        self.settings = settings.get("import.morph", {})
        self.blend_shape_node = None  # 単一のブレンドシェイプノードを保持
        self.morph_categories = {}  # モーフカテゴリ情報を保持

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

        # 単一のブレンドシェイプノードを作成
        self._ensure_blend_shape_node(mesh_node)
        
        results = []
        
        # モーフを一括処理するためのバッチデータを準備
        morph_batch_data = []
        
        for morph in pmd_data.morphs:
            # ベースモーフはスキップ
            if morph.morph_type == 0:
                continue
            
            # カテゴリ情報を記録
            self._categorize_morph(morph.name, morph.morph_type)
            
            # バッチデータに追加
            morph_batch_data.append(morph)
        
        # スパースターゲットを使用してバッチ処理
        if morph_batch_data:
            results = self._batch_create_sparse_targets_pmd(mesh_node, morph_batch_data)

        # カテゴリ情報をカスタムアトリビュートとして保存
        if self.blend_shape_node and self.morph_categories:
            self._save_categories_to_node()
        
        return {
            "success": True,
            "morphs_converted": len(results),
            "total_morphs": len(pmd_data.morphs) - 1,  # ベースモーフを除く
            "blend_shape_nodes": [self.blend_shape_node] if self.blend_shape_node else [],
            "results": results,
            "morph_categories": self.morph_categories,
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

        # 単一のブレンドシェイプノードを作成
        self._ensure_blend_shape_node(mesh_node)
        
        results = []
        
        # モーフを一括処理するためのバッチデータを準備
        morph_batch_data = []
        
        for morph in pmx_data.morphs:
            # 現在は頂点モーフのみ対応
            if morph.morph_type == PmxMorphType.VertexMorph:
                # カテゴリ情報を記録
                self._categorize_morph(morph.name, morph.morph_type.value)
                
                # バッチデータに追加
                morph_batch_data.append(morph)
        
        # スパースターゲットを使用してバッチ処理
        if morph_batch_data:
            results = self._batch_create_sparse_targets_pmx(mesh_node, morph_batch_data)

        # カテゴリ情報をカスタムアトリビュートとして保存
        if self.blend_shape_node and self.morph_categories:
            self._save_categories_to_node()

        return {
            "success": True,
            "morphs_converted": len(results),
            "total_morphs": len(pmx_data.morphs),
            "blend_shape_nodes": [self.blend_shape_node] if self.blend_shape_node else [],
            "results": results,
            "morph_categories": self.morph_categories,
        }

    def _convert_vertex_morph_pmd(self, morph, mesh_node: str) -> Dict[str, Any]:
        """PMD頂点モーフの変換"""
        # モーフ名をMaya互換に変換
        morph_name = maya_utils.sanitize_text(morph.name)

        # メッシュを複製してターゲットを作成
        target_mesh = cmds.duplicate(mesh_node)[0]
        target_mesh = cmds.rename(target_mesh, f"{morph_name}_target")

        # ターゲットメッシュを非表示
        maya_utils.set_attribute(target_mesh, "visibility", 0, "bool")

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
        maya_utils.set_attribute(target_mesh, "visibility", 0, "bool")

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
    
    def _ensure_blend_shape_node(self, mesh_node: str):
        """単一のブレンドシェイプノードを確保する"""
        if not self.blend_shape_node:
            # 既存のブレンドシェイプノードを検索
            self.blend_shape_node = maya_utils.find_or_create_blendshape_node(mesh_node)
            logger.info(f"Using blend shape node: {self.blend_shape_node}")
    
    def _categorize_morph(self, morph_name: str, morph_type: int):
        """モーフをカテゴリに分類する"""
        # 日本語名パターンから推測
        category = "other"
        
        # 眉
        if any(keyword in morph_name for keyword in ["眉", "まゆ", "brow", "eyebrow"]):
            category = "eyebrow"
        # 目
        elif any(keyword in morph_name for keyword in ["目", "め", "eye", "瞳"]):
            category = "eye"
        # 口
        elif any(keyword in morph_name for keyword in ["口", "くち", "mouth", "lip", "歯"]):
            category = "mouth"
        # その他の表情
        elif any(keyword in morph_name for keyword in ["頬", "ほほ", "cheek", "blush"]):
            category = "cheek"
        
        if category not in self.morph_categories:
            self.morph_categories[category] = []
        
        self.morph_categories[category].append(morph_name)
    
    def _batch_create_sparse_targets_pmd(self, mesh_node: str, morphs: List) -> List[Dict[str, Any]]:
        """PMDモーフをバッチ処理でスパースターゲットとして作成"""
        results = []
        
        # ベースメッシュの頂点位置を取得
        base_points = self._get_mesh_points(mesh_node)
        
        for i, morph in enumerate(morphs):
            try:
                # モーフ名をMaya互換に変換
                morph_name = maya_utils.sanitize_text(morph.name)
                
                # スパースターゲット用のデルタデータを準備
                vertex_indices = []
                deltas = []
                
                for vertex_index, offset_pos in morph.vertices:
                    if vertex_index < len(base_points):
                        # 実際にオフセットがある頂点のみ記録
                        if any(abs(v) > 0.0001 for v in offset_pos):
                            vertex_indices.append(vertex_index)
                            deltas.append(offset_pos)
                
                # スパースターゲットを作成
                if vertex_indices:
                    target_index = self._add_sparse_target(
                        mesh_node, morph_name, vertex_indices, deltas
                    )
                    
                    results.append({
                        "success": True,
                        "morph_name": morph.name,
                        "blend_shape_node": self.blend_shape_node,
                        "target_index": target_index,
                        "vertex_count": len(vertex_indices),
                    })
                    
                    logger.debug(f"Created sparse target '{morph_name}' with {len(vertex_indices)} vertices")
                
            except Exception as e:
                logger.warning(f"Failed to create sparse target for morph '{morph.name}': {e}")
        
        return results
    
    def _batch_create_sparse_targets_pmx(self, mesh_node: str, morphs: List) -> List[Dict[str, Any]]:
        """PMXモーフをバッチ処理でスパースターゲットとして作成"""
        results = []
        
        # ベースメッシュの頂点位置を取得
        base_points = self._get_mesh_points(mesh_node)
        
        for i, morph in enumerate(morphs):
            try:
                # モーフ名をMaya互換に変換
                morph_name = maya_utils.sanitize_text(morph.name)
                
                # スパースターゲット用のデルタデータを準備
                vertex_indices = []
                deltas = []
                
                if hasattr(morph, "offsets"):
                    for offset in morph.offsets:
                        if "vertex_index" in offset and "position_offset" in offset:
                            vertex_index = offset["vertex_index"]
                            offset_pos = offset["position_offset"]
                            
                            if vertex_index < len(base_points):
                                # 実際にオフセットがある頂点のみ記録
                                if any(abs(v) > 0.0001 for v in offset_pos):
                                    vertex_indices.append(vertex_index)
                                    deltas.append(offset_pos)
                
                # スパースターゲットを作成
                if vertex_indices:
                    target_index = self._add_sparse_target(
                        mesh_node, morph_name, vertex_indices, deltas
                    )
                    
                    results.append({
                        "success": True,
                        "morph_name": morph.name,
                        "blend_shape_node": self.blend_shape_node,
                        "target_index": target_index,
                        "vertex_count": len(vertex_indices),
                    })
                    
                    logger.debug(f"Created sparse target '{morph_name}' with {len(vertex_indices)} vertices")
                
            except Exception as e:
                logger.warning(f"Failed to create sparse target for morph '{morph.name}': {e}")
        
        return results
    
    def _get_mesh_points(self, mesh_node: str) -> List[om.MPoint]:
        """メッシュの頂点位置を取得"""
        sel_list = om.MSelectionList()
        sel_list.add(mesh_node)
        dag_path = sel_list.getDagPath(0)
        mesh_fn = om.MFnMesh(dag_path)
        return mesh_fn.getPoints(om.MSpace.kObject)
    
    def _add_sparse_target(self, mesh_node: str, target_name: str, 
                          vertex_indices: List[int], deltas: List[Tuple[float, float, float]]) -> int:
        """スパースターゲットをブレンドシェイプに追加"""
        # 現在のターゲット数を取得
        target_count = cmds.blendShape(self.blend_shape_node, query=True, target=True)
        target_index = len(target_count) if target_count else 0
        
        # 一時的にターゲットメッシュを作成（スパース情報を設定するため）
        temp_target = cmds.duplicate(mesh_node, name=f"{target_name}_temp")[0]
        
        # 頂点を移動
        for i, vertex_idx in enumerate(vertex_indices):
            delta = deltas[i]
            cmds.xform(
                f"{temp_target}.vtx[{vertex_idx}]",
                relative=True,
                translation=delta
            )
        
        # ブレンドシェイプにターゲットを追加
        cmds.blendShape(
            self.blend_shape_node,
            edit=True,
            target=(mesh_node, target_index, temp_target, 1.0)
        )
        
        # ターゲット名を設定
        cmds.aliasAttr(target_name, f"{self.blend_shape_node}.w[{target_index}]")
        
        # 一時メッシュを削除
        cmds.delete(temp_target)
        
        return target_index
    
    def _save_categories_to_node(self):
        """モーフカテゴリ情報をblendShapeノードにカスタムアトリビュートとして保存"""
        if not self.blend_shape_node or not self.morph_categories:
            return
            
        # カテゴリ情報をJSON形式で保存
        import json
        categories_json = json.dumps(self.morph_categories, ensure_ascii=False)
        
        # カスタムアトリビュートとして保存
        maya_utils.set_custom_attributes(self.blend_shape_node, {
            "morphCategories": categories_json
        })
        
    def get_morph_categories_report(self) -> str:
        """モーフカテゴリのレポートを生成"""
        if not self.morph_categories:
            return "No morphs categorized."
        
        report = "Morph Categories:\n"
        for category, morphs in self.morph_categories.items():
            report += f"\n{category.upper()} ({len(morphs)} morphs):\n"
            for morph in morphs[:5]:  # 最初の5つだけ表示
                report += f"  - {morph}\n"
            if len(morphs) > 5:
                report += f"  ... and {len(morphs) - 5} more\n"
        
        return report
