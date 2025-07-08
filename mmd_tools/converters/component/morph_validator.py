"""
MorphValidator: モーフ変換の検証処理

このモジュールは、モーフデータの変換前後の検証を行う機能を提供します。
"""

import logging
from typing import Any, Dict, List

from maya import cmds

from mmd_tools.core import maya_utils
from mmd_tools.core.pmx_data.morph import PmxMorphType


class MorphValidator:
    """モーフ変換の検証を行うクラス"""

    def __init__(self):
        self.logger = self._setup_logger()

    def _setup_logger(self) -> logging.Logger:
        """ログ出力の設定"""
        return maya_utils.setup_logger("mmd_tools.MorphValidator")

    def validate_pmd_morph(self, morph_data: Any, mesh_node: str) -> bool:
        """PMDモーフデータの検証"""
        try:
            # 基本的な検証
            if not hasattr(morph_data, "name") or not morph_data.name:
                return False

            if not hasattr(morph_data, "vertices") or not morph_data.vertices:
                return False

            # メッシュの頂点数チェック
            mesh_vertex_count = cmds.polyEvaluate(mesh_node, vertex=True)  # type: ignore

            for vertex_index, _ in morph_data.vertices:
                if vertex_index >= mesh_vertex_count:
                    self.logger.warning(
                        f"Vertex index {vertex_index} exceeds mesh vertex count {mesh_vertex_count}"
                    )
                    return False

            return True

        except Exception as e:
            self.logger.error(f"Validation error for PMD morph: {e}")
            return False

    def validate_pmx_morph(self, morph_data: Any, mesh_node: str) -> bool:
        """PMXモーフデータの検証"""
        try:
            # 基本的な検証
            if not hasattr(morph_data, "name") or not morph_data.name:
                return False

            if not hasattr(morph_data, "morph_type"):
                return False

            # モーフタイプ別の詳細検証
            if morph_data.morph_type == PmxMorphType.VertexMorph:
                return self._validate_vertex_morph_data(morph_data, mesh_node)
            elif (
                PmxMorphType.UVMorph
                <= morph_data.morph_type
                <= PmxMorphType.AdditionalUVMorph4
            ):
                return self._validate_uv_morph_data(morph_data, mesh_node)
            elif morph_data.morph_type == PmxMorphType.MaterialMorph:
                return self._validate_material_morph_data(morph_data, mesh_node)
            elif morph_data.morph_type == PmxMorphType.GroupMorph:
                return self._validate_group_morph_data(morph_data, mesh_node)
            elif morph_data.morph_type == PmxMorphType.BoneMorph:
                return self._validate_bone_morph_data(morph_data, mesh_node)
            else:
                self.logger.warning(f"Unsupported morph type: {morph_data.morph_type}")
                return False

            return True

        except Exception as e:
            self.logger.error(f"Validation error for PMX morph: {e}")
            return False

    def _validate_vertex_morph_data(self, morph_data: Any, mesh_node: str) -> bool:
        """頂点モーフデータの検証"""
        if not hasattr(morph_data, "offsets") or not morph_data.offsets:
            return False

        mesh_vertex_count = cmds.polyEvaluate(mesh_node, vertex=True)  # type: ignore

        for offset in morph_data.offsets:
            if "vertex_index" in offset:
                vertex_index = offset["vertex_index"]
                if vertex_index >= mesh_vertex_count:
                    self.logger.warning(
                        f"Vertex index {vertex_index} exceeds mesh vertex count {mesh_vertex_count}"
                    )
                    return False

        return True

    def _validate_uv_morph_data(self, morph_data: Any, mesh_node: str) -> bool:
        """UVモーフデータの検証"""
        if not hasattr(morph_data, "offsets") or not morph_data.offsets:
            return False

        mesh_vertex_count = cmds.polyEvaluate(mesh_node, vertex=True)  # type: ignore

        for offset in morph_data.offsets:
            if "vertex_index" in offset:
                vertex_index = offset["vertex_index"]
                if vertex_index >= mesh_vertex_count:
                    self.logger.warning(
                        f"UV morph vertex index {vertex_index} exceeds mesh vertex count {mesh_vertex_count}"
                    )
                    return False

        return True

    def _validate_material_morph_data(self, morph_data: Any, mesh_node: str) -> bool:
        """マテリアルモーフデータの検証"""
        if not hasattr(morph_data, "offsets") or not morph_data.offsets:
            return False

        # メッシュに割り当てられているマテリアル数をチェック
        try:
            materials: List[str] = []
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

            material_count = len(set(materials))

            for offset in morph_data.offsets:
                if "material_index" in offset:
                    material_index = offset["material_index"]
                    if material_index >= material_count and material_index != -1:
                        self.logger.warning(
                            f"Material morph material index {material_index} exceeds material count {material_count}"
                        )
                        return False

        except Exception as e:
            self.logger.warning(f"Failed to validate material morph data: {e}")
            return False

        return True

    def _validate_group_morph_data(self, morph_data: Any, mesh_node: str) -> bool:
        """グループモーフデータの検証"""
        if not hasattr(morph_data, "offsets") or not morph_data.offsets:
            return False

        # グループモーフは基本的な構造チェックのみ
        for offset in morph_data.offsets:
            if "morph_index" not in offset or "morph_rate" not in offset:
                self.logger.warning("Invalid group morph offset structure")
                return False

        return True

    def _validate_bone_morph_data(self, morph_data: Any, mesh_node: str) -> bool:
        """ボーンモーフデータの検証"""
        if not hasattr(morph_data, "offsets") or not morph_data.offsets:
            return False

        # スキンクラスターのボーン数をチェック
        try:
            skin_clusters = cmds.listHistory(mesh_node, type="skinCluster")  # type: ignore
            bone_count = 0
            if skin_clusters:
                influences = cmds.skinCluster(  # type: ignore
                    skin_clusters[0], query=True, influence=True
                )
                bone_count = len(influences) if influences else 0

            for offset in morph_data.offsets:
                if "bone_index" in offset:
                    bone_index = offset["bone_index"]
                    if bone_index >= bone_count and bone_index != -1:
                        self.logger.warning(
                            f"Bone morph bone index {bone_index} exceeds bone count {bone_count}"
                        )
                        return False

        except Exception as e:
            self.logger.warning(f"Failed to validate bone morph data: {e}")
            return False

        return True

    def validate_conversion_results(
        self, results: List[Dict[str, Any]], mesh_node: str
    ) -> bool:
        """変換結果の検証"""
        try:
            for result in results:
                if not result.get("success", False):
                    self.logger.error(
                        f"Conversion failed: {result.get('error', 'Unknown error')}"
                    )
                    return False

            return True

        except Exception as e:
            self.logger.error(f"Failed to validate conversion results: {e}")
            return False
