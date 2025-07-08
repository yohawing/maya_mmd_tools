
"""
MMDのモーフデータをMayaのblendShapeに変換するモジュール。

このモジュールは、PMD/PMXファイルのモーフデータを解析し、
Mayaのブレンドシェイプシステムに変換する機能を提供します。
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List

from maya import cmds
from maya.api import OpenMaya as om

from mmd_tools import settings
from mmd_tools.core import maya_utils
from mmd_tools.core.pmx_data.morph import PmxMorphType


class BaseMorphHandler(ABC):
    """モーフハンドラーの基底クラス"""

    def __init__(self, settings_dict: dict):
        self.settings = settings_dict
        self.logger = self._setup_logger()

    def _setup_logger(self) -> logging.Logger:
        """ログ出力の設定"""
        logger = logging.getLogger(f"mmd_tools.{self.__class__.__name__}")
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        return logger

    @abstractmethod
    def can_handle(self, morph_data: Any) -> bool:
        """このハンドラーが対象のモーフタイプを処理できるかチェック"""
        pass

    @abstractmethod
    def convert(self, morph_data: Any, mesh_node: str, **kwargs) -> Dict[str, Any]:
        """モーフデータをMayaのblendShapeに変換"""
        pass

    def validate_input(self, morph_data: Any, mesh_node: str) -> bool:
        """入力データの検証"""
        # 基本的な検証
        if not hasattr(morph_data, "name") or not morph_data.name:
            self.logger.warning("Morph data has no name")
            return False

        if not cmds.objExists(mesh_node):
            self.logger.error(f"Mesh node {mesh_node} does not exist")
            return False

        return True

    def _sanitize_name(self, name: str) -> str:
        """Maya互換の名前に変換"""
        return maya_utils.sanitize_text(name)


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
        morph_name = self._sanitize_name(morph_data.name)

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
        # メッシュに接続されているblendShapeノードを検索
        blend_shapes = cmds.listHistory(mesh_node, type="blendShape")  # type: ignore
        if blend_shapes:
            return blend_shapes[0]

        # 新しいblendShapeノードを作成
        blend_shape_node = cmds.blendShape(mesh_node)[0]  # type: ignore
        return blend_shape_node

    def _cleanup_temp_objects(self, temp_mesh: str):
        """一時オブジェクトをクリーンアップ"""
        if cmds.objExists(temp_mesh):  # type: ignore
            cmds.delete(temp_mesh)  # type: ignore


class MorphConverterFactory:
    """モーフタイプに応じたハンドラーを提供するファクトリー"""

    def __init__(self):
        self._handlers = {}
        self._register_default_handlers()

    def _register_default_handlers(self):
        """デフォルトハンドラーの登録"""
        self.register_handler("vertex", VertexMorphHandler)

    def register_handler(self, morph_type: str, handler_class):
        """カスタムハンドラーの登録"""
        self._handlers[morph_type] = handler_class

    def get_handler(self, morph_data: Any, settings_dict: dict) -> BaseMorphHandler:
        """適切なハンドラーを取得"""
        for handler_class in self._handlers.values():
            handler = handler_class(settings_dict)
            if handler.can_handle(morph_data):
                return handler
        raise ValueError(f"No handler found for morph type: {type(morph_data)}")


class MorphValidator:
    """モーフ変換の検証を行うクラス"""

    def __init__(self):
        self.logger = self._setup_logger()

    def _setup_logger(self) -> logging.Logger:
        """ログ出力の設定"""
        logger = logging.getLogger("mmd_tools.MorphValidator")
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        return logger

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

            # 頂点モーフの場合は詳細検証
            if morph_data.morph_type == PmxMorphType.VertexMorph:
                return self._validate_vertex_morph_data(morph_data, mesh_node)

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

    def validate_conversion_results(
        self, results: List[Dict[str, Any]], mesh_node: str
    ):
        """変換結果の検証とレポート"""
        successful = len([r for r in results if r.get("success", False)])
        total = len(results)

        self.logger.info(f"Morph conversion completed: {successful}/{total} successful")

        if successful < total:
            failed = total - successful
            self.logger.warning(f"{failed} morphs failed to convert")


class MorphConverter:
    """MMDのモーフデータをMayaのブレンドシェイプに変換するメインクラス"""

    def __init__(self):
        morph_settings = settings.get("import.morph", {})
        self.settings = morph_settings
        self.factory = MorphConverterFactory()
        self.validator = MorphValidator()
        self.blend_shape_nodes = {}  # メッシュノードごとのblendShapeノード管理
        self.logger = self._setup_logger()

    def _setup_logger(self) -> logging.Logger:
        """ログ出力の設定"""
        logger = logging.getLogger("mmd_tools.MorphConverter")
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        return logger

    def convert_pmd_morphs(self, pmd_data, mesh_node: str) -> Dict[str, Any]:
        """
        PMDのモーフデータをMayaのブレンドシェイプに変換する。

        Args:
            pmd_data: 解析されたPMDデータオブジェクト。
            mesh_node (str): ブレンドシェイプを適用するMayaのメッシュノードの名前。

        Returns:
            Dict[str, Any]: 変換結果の辞書
        """
        if not self.settings.get("import_morphs", True):
            self.logger.info("Morph import is disabled in settings")
            return {"success": True, "morphs_converted": 0}

        try:
            results = []
            successful_conversions = 0

            # プログレス初期化
            total_morphs = len(pmd_data.morphs)
            self.logger.info(f"Converting {total_morphs} PMD morphs")

            for i, morph in enumerate(pmd_data.morphs):
                try:
                    # ベースモーフはスキップ
                    if morph.morph_type == 0:
                        continue

                    # 入力検証
                    if not self.validator.validate_pmd_morph(morph, mesh_node):
                        self.logger.warning(f"Validation failed for morph: {morph.name}")
                        continue

                    # 適切なハンドラーを取得
                    handler = self.factory.get_handler(morph, self.settings)

                    # 変換実行
                    result = handler.convert(morph, mesh_node)

                    if result["success"]:
                        results.append(result)
                        successful_conversions += 1

                        # blendShapeノードの管理
                        self._manage_blendshape_node(result, mesh_node)

                except Exception as e:
                    self.logger.error(f"Failed to convert morph {morph.name}: {e}")
                    continue

            # 最終検証
            self.validator.validate_conversion_results(results, mesh_node)

            return {
                "success": True,
                "morphs_converted": successful_conversions,
                "total_morphs": total_morphs,
                "blend_shape_nodes": self.blend_shape_nodes.get(mesh_node, []),
                "results": results,
            }

        except Exception as e:
            self.logger.error(f"Failed to convert PMD morphs: {e}")
            return {"success": False, "error": str(e)}

    def convert_pmx_morphs(self, pmx_data, mesh_node: str) -> Dict[str, Any]:
        """
        PMXのモーフデータをMayaのブレンドシェイプに変換する。

        Args:
            pmx_data: 解析されたPMXデータオブジェクト。
            mesh_node (str): ブレンドシェイプを適用するMayaのメッシュノードの名前。

        Returns:
            Dict[str, Any]: 変換結果の辞書
        """
        if not self.settings.get("import_morphs", True):
            self.logger.info("Morph import is disabled in settings")
            return {"success": True, "morphs_converted": 0}

        try:
            results = []
            successful_conversions = 0

            # プログレス初期化
            total_morphs = len(pmx_data.morphs)
            self.logger.info(f"Converting {total_morphs} PMX morphs")

            # 現在は頂点モーフのみ対応
            for i, morph in enumerate(pmx_data.morphs):
                try:
                    # 頂点モーフ以外はスキップ
                    if morph.morph_type != PmxMorphType.VertexMorph:
                        continue

                    # 入力検証
                    if not self.validator.validate_pmx_morph(morph, mesh_node):
                        self.logger.warning(f"Validation failed for morph: {morph.name}")
                        continue

                    # 適切なハンドラーを取得
                    handler = self.factory.get_handler(morph, self.settings)

                    # 変換実行
                    result = handler.convert(morph, mesh_node)

                    if result["success"]:
                        results.append(result)
                        successful_conversions += 1

                        # blendShapeノードの管理
                        self._manage_blendshape_node(result, mesh_node)

                except Exception as e:
                    self.logger.error(f"Failed to convert morph {morph.name}: {e}")
                    continue

            # 最終検証
            self.validator.validate_conversion_results(results, mesh_node)

            return {
                "success": True,
                "morphs_converted": successful_conversions,
                "total_morphs": total_morphs,
                "blend_shape_nodes": self.blend_shape_nodes.get(mesh_node, []),
                "results": results,
            }

        except Exception as e:
            self.logger.error(f"Failed to convert PMX morphs: {e}")
            return {"success": False, "error": str(e)}

    def _manage_blendshape_node(self, result: Dict[str, Any], mesh_node: str):
        """blendShapeノードの管理"""
        if mesh_node not in self.blend_shape_nodes:
            self.blend_shape_nodes[mesh_node] = []

        if "blend_shape_node" in result:
            blend_shape_node = result["blend_shape_node"]
            if blend_shape_node not in self.blend_shape_nodes[mesh_node]:
                self.blend_shape_nodes[mesh_node].append(blend_shape_node)
