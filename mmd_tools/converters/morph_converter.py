"""
MMDのモーフデータをMayaのblendShapeに変換するモジュール。

このモジュールは、PMD/PMXファイルのモーフデータを解析し、
Mayaのブレンドシェイプシステムに変換する機能を提供します。

このモジュールは、以下のコンポーネントから構成されています：
- 各種モーフハンドラー（頂点、UV、マテリアル、グループ、ボーン）
- モーフコンバーターファクトリー
- モーフバリデーター
- メインのMorphConverterクラス
"""

# componentディレクトリからの各種ハンドラーとユーティリティクラスのインポート
from .component.base_morph_handler import BaseMorphHandler
from .component.vertex_morph_handler import VertexMorphHandler
from .component.uv_morph_handler import UVMorphHandler
from .component.material_morph_handler import MaterialMorphHandler
from .component.group_morph_handler import GroupMorphHandler
from .component.bone_morph_handler import BoneMorphHandler
from .component.morph_converter_factory import MorphConverterFactory
from .component.morph_validator import MorphValidator

# 公開APIの定義
__all__ = [
    "BaseMorphHandler",
    "VertexMorphHandler",
    "UVMorphHandler",
    "MaterialMorphHandler",
    "GroupMorphHandler",
    "BoneMorphHandler",
    "MorphConverterFactory",
    "MorphValidator",
    "MorphConverter",
]


class MorphConverter:
    """MMDのモーフデータをMayaのブレンドシェイプに変換するメインクラス"""

    def __init__(self):
        from mmd_tools import settings

        morph_settings = settings.get("import.morph", {})
        self.settings = morph_settings
        self.factory = MorphConverterFactory()
        self.validator = MorphValidator()
        self.blend_shape_nodes = {}  # メッシュノードごとのblendShapeノード管理
        self.logger = self._setup_logger()

    def _setup_logger(self):
        """ログ出力の設定"""
        from mmd_tools.core import maya_utils

        return maya_utils.setup_logger("mmd_tools.MorphConverter")

    def convert_pmd_morphs(self, pmd_data, mesh_node: str):
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
                        self.logger.warning(
                            f"Validation failed for morph: {morph.name}"
                        )
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

    def convert_pmx_morphs(self, pmx_data, mesh_node: str):
        """
        PMXのモーフデータをMayaのブレンドシェイプに変換する。

        Args:
            pmx_data: 解析されたPMXデータオブジェクト。
            mesh_node (str): ブレンドシェイプを適用するMayaのメッシュノードの名前。

        Returns:
            Dict[str, Any]: 変換結果の辞書
        """
        from mmd_tools.core.pmx_data.morph import PmxMorphType

        if not self.settings.get("import_morphs", True):
            self.logger.info("Morph import is disabled in settings")
            return {"success": True, "morphs_converted": 0}

        try:
            results = []
            successful_conversions = 0

            # プログレス初期化
            total_morphs = len(pmx_data.morphs)
            self.logger.info(f"Converting {total_morphs} PMX morphs")

            # 全モーフタイプを対応
            for i, morph in enumerate(pmx_data.morphs):
                try:
                    # 入力検証
                    if not self.validator.validate_pmx_morph(morph, mesh_node):
                        self.logger.warning(
                            f"Validation failed for morph: {morph.name}"
                        )
                        continue

                    # 適切なハンドラーを取得
                    try:
                        handler = self.factory.get_handler(morph, self.settings)
                    except ValueError as e:
                        self.logger.warning(
                            f"No handler available for morph {morph.name} (type: {morph.morph_type}): {e}"
                        )
                        continue

                    # 変換実行
                    result = handler.convert(morph, mesh_node)

                    if result["success"]:
                        results.append(result)
                        successful_conversions += 1

                        # blendShapeノードの管理（頂点モーフの場合のみ）
                        if morph.morph_type == PmxMorphType.VertexMorph:
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

    def _manage_blendshape_node(self, result, mesh_node: str):
        """blendShapeノードの管理"""
        if mesh_node not in self.blend_shape_nodes:
            self.blend_shape_nodes[mesh_node] = []

        if "blend_shape_node" in result:
            blend_shape_node = result["blend_shape_node"]
            if blend_shape_node not in self.blend_shape_nodes[mesh_node]:
                self.blend_shape_nodes[mesh_node].append(blend_shape_node)
