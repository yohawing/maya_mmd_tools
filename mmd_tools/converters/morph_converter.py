"""
MMDのモーフデータをMayaのblendShapeに変換するモジュール。

このモジュールは、PMD/PMXファイルのモーフデータを解析し、
Mayaのブレンドシェイプシステムに変換する機能を提供します。
"""

import json
from typing import Any, Dict, List, Union

from maya import cmds
from maya.api import OpenMaya as om

from mmd_tools.core import maya_utils
from mmd_tools.core.pmx_data.morph import PmxMorphType


class MorphConverter:
    """MMDのモーフデータをMayaのblendShapeに変換するクラス"""

    def __init__(self):
        from mmd_tools import settings
        from mmd_tools.core.logger import get_logger

        self.settings = settings.get("import.morph", {})
        self.logger = get_logger(__name__)

    def convert_pmd_morphs(self, pmd_data, mesh_node: Union[str, List[str]]) -> Dict[str, Any]:
        """
        PMDのモーフデータをMayaのブレンドシェイプに変換する。

        Args:
            pmd_data: 解析されたPMDデータオブジェクト
            mesh_node (str or list): ブレンドシェイプを適用するMayaのメッシュノード名、またはそのリスト。

        Returns:
            Dict[str, Any]: 変換結果の辞書
        """
        if not self.settings.get("import_morphs", True):
            return {"success": True, "morphs_converted": 0}

        mesh_nodes = [mesh_node] if isinstance(mesh_node, str) else (mesh_node or [])

        results = []
        blend_shape_nodes = []

        for mn in mesh_nodes:
            for morph in pmd_data.morphs:
                # ベースモーフはスキップ
                if morph.morph_type == 0:
                    self.logger.debug("Skipping base morph")
                    continue

                try:
                    self.logger.debug(f"Converting morph: {morph.name}, type: {morph.morph_type}")
                    result = self._convert_vertex_morph_pmd(morph, mn)
                    if result["success"]:
                        results.append(result)
                        if result["blend_shape_node"] not in blend_shape_nodes:
                            blend_shape_nodes.append(result["blend_shape_node"])
                        self.logger.info(f"Successfully converted morph: {morph.name}")
                except Exception as e:
                    # エラーをログに記録して次のモーフへ
                    self.logger.warning(f"Failed to convert morph {morph.name}: {e}")
                    pass

        return {
            "success": True,
            "morphs_converted": len(results),
            "total_morphs": len(pmd_data.morphs) - 1,  # ベースモーフを除く
            "blend_shape_nodes": blend_shape_nodes,
            "results": results,
        }

    def convert_pmx_morphs(self, pmx_data, mesh_node: Union[str, List[str]]) -> Dict[str, Any]:
        """
        PMXのモーフデータをMayaのブレンドシェイプに変換する。

        Args:
            pmx_data: 解析されたPMXデータオブジェクト
            mesh_node (str or list): ブレンドシェイプを適用するMayaのメッシュノード名、またはそのリスト。

        Returns:
            Dict[str, Any]: 変換結果の辞書
        """
        if not self.settings.get("import_morphs", True):
            return {"success": True, "morphs_converted": 0}

        mesh_nodes = [mesh_node] if isinstance(mesh_node, str) else (mesh_node or [])

        results = []
        blend_shape_nodes = []
        bone_morph_nodes = []
        material_morph_nodes = []
        converted_bone_morphs = set()
        converted_material_morphs = set()

        for mn in mesh_nodes:
            for morph in pmx_data.morphs:
                try:
                    if morph.morph_type == PmxMorphType.VertexMorph:
                        self.logger.debug(f"Converting vertex morph: {morph.name}")
                        result = self._convert_vertex_morph_pmx(morph, mn)
                        if result["success"]:
                            results.append(result)
                            if result["blend_shape_node"] not in blend_shape_nodes:
                                blend_shape_nodes.append(result["blend_shape_node"])
                            self.logger.info(f"Successfully converted morph: {morph.name}")
                    elif morph.morph_type == PmxMorphType.BoneMorph and morph.name not in converted_bone_morphs:
                        self.logger.debug(f"Converting bone morph metadata: {morph.name}")
                        result = self._convert_bone_morph_pmx(morph)
                        if result["success"]:
                            converted_bone_morphs.add(morph.name)
                            results.append(result)
                            bone_morph_nodes.append(result["morph_node"])
                            self.logger.info(f"Successfully imported bone morph metadata: {morph.name}")
                    elif (
                        morph.morph_type == PmxMorphType.MaterialMorph
                        and morph.name not in converted_material_morphs
                    ):
                        self.logger.debug(f"Converting material morph metadata: {morph.name}")
                        result = self._convert_material_morph_pmx(morph)
                        if result["success"]:
                            converted_material_morphs.add(morph.name)
                            results.append(result)
                            material_morph_nodes.append(result["morph_node"])
                            self.logger.info(f"Successfully imported material morph metadata: {morph.name}")
                except Exception as e:
                    # エラーをログに記録して次のモーフへ
                    self.logger.warning(f"Failed to convert morph {morph.name}: {e}")
                    pass

        return {
            "success": True,
            "morphs_converted": len(results),
            "total_morphs": len(pmx_data.morphs),
            "blend_shape_nodes": blend_shape_nodes,
            "bone_morph_nodes": bone_morph_nodes,
            "material_morph_nodes": material_morph_nodes,
            "results": results,
        }

    def collect_morphs_from_scene_for_export(self) -> List[Dict[str, Any]]:
        """シーン内の network モーフノードから exporter 用の morph dict を収集する。"""
        morphs = []

        for morph_node in cmds.ls(type="network") or []:
            try:
                if not cmds.attributeQuery("mmd_morph_type", node=morph_node, exists=True):
                    continue

                morph_type = cmds.getAttr(f"{morph_node}.mmd_morph_type")
                if morph_type not in {"bone", "material"}:
                    continue

                offsets_attr = (
                    "mmd_bone_morph_offsets_json"
                    if morph_type == "bone"
                    else "mmd_material_morph_offsets_json"
                )
                if not cmds.attributeQuery(offsets_attr, node=morph_node, exists=True):
                    self.logger.warning(
                        f"skip morph node {morph_node}: missing {offsets_attr} attribute"
                    )
                    continue

                try:
                    offsets_json = cmds.getAttr(f"{morph_node}.{offsets_attr}")
                    offsets = json.loads(offsets_json) if offsets_json else []
                except (TypeError, json.JSONDecodeError) as e:
                    self.logger.warning(f"skip morph node {morph_node}: invalid JSON in {offsets_attr}: {e}")
                    continue

                if not isinstance(offsets, list):
                    self.logger.warning(
                        f"skip morph node {morph_node}: offsets data must be list, got {type(offsets).__name__}"
                    )
                    continue

                morph_name = ""
                if cmds.attributeQuery("mmd_morph_name", node=morph_node, exists=True):
                    morph_name = cmds.getAttr(f"{morph_node}.mmd_morph_name") or ""

                name_english = ""
                if cmds.attributeQuery("mmd_morph_name_en", node=morph_node, exists=True):
                    name_english = cmds.getAttr(f"{morph_node}.mmd_morph_name_en") or ""

                panel = 0
                if cmds.attributeQuery("mmd_morph_panel", node=morph_node, exists=True):
                    panel = int(cmds.getAttr(f"{morph_node}.mmd_morph_panel"))

                morphs.append(
                    {
                        "type": morph_type,
                        "name": morph_name,
                        "name_english": name_english,
                        "panel": panel,
                        "offsets": offsets,
                    }
                )
            except Exception as e:
                self.logger.warning(f"skip morph node {morph_node}: {e}")

        return morphs

    def _convert_vertex_morph_pmd(self, morph, mesh_node: str) -> Dict[str, Any]:
        """PMD頂点モーフの変換"""
        # モーフ名をMaya互換に変換
        morph_name = maya_utils.sanitize_text(morph.get_name())

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
            "morph_name": morph.get_name(),
            "blend_shape_node": blend_shape_node,
            "target_index": target_index,
        }

    def _convert_bone_morph_pmx(self, morph) -> Dict[str, Any]:
        """PMXボーンモーフをMayaのnetwork nodeとしてインポートする。

        ここでは joint 変形へは接続せず、VMD morph frame がキー化できる
        `weight` と、後段評価用の offset metadata だけを作る。
        """
        morph_name = morph.get_name()
        safe_name = maya_utils.sanitize_text(morph_name)
        node_name = f"{safe_name}_boneMorph"

        if cmds.objExists(node_name):
            morph_node = node_name
        else:
            morph_node = cmds.createNode("network", name=node_name)

        if not cmds.attributeQuery("weight", node=morph_node, exists=True):
            cmds.addAttr(
                morph_node,
                longName="weight",
                attributeType="double",
                minValue=0.0,
                maxValue=1.0,
                defaultValue=0.0,
                keyable=True,
            )

        offsets = []
        for offset in getattr(morph, "offsets", []):
            if "bone_index" not in offset:
                continue
            offsets.append(
                {
                    "bone_index": int(offset["bone_index"]),
                    "translation": [float(v) for v in offset.get("translation", (0.0, 0.0, 0.0))],
                    "rotation": [float(v) for v in offset.get("rotation", (0.0, 0.0, 0.0, 1.0))],
                }
            )

        maya_utils.set_custom_attributes(
            morph_node,
            {
                "mmd_morph_name": str(morph_name),
                "mmd_morph_name_en": str(getattr(morph, "name_english", "")),
                "mmd_morph_type": "bone",
                "mmd_morph_panel": int(getattr(morph, "panel", 0)),
                "mmd_bone_morph_offset_count": len(offsets),
                "mmd_bone_morph_offsets_json": json.dumps(offsets, ensure_ascii=False, separators=(",", ":")),
            },
        )

        return {
            "success": True,
            "morph_name": morph_name,
            "morph_node": morph_node,
            "morph_type": "bone",
            "offset_count": len(offsets),
        }

    def _convert_material_morph_pmx(self, morph) -> Dict[str, Any]:
        """PMXマテリアルモーフをMayaのnetwork nodeとしてインポートする。

        shader parameter へは接続せず、VMD morph frame がキー化できる
        `weight` と、後段評価用の offset metadata だけを作る。
        """
        morph_name = morph.get_name()
        safe_name = maya_utils.sanitize_text(morph_name)
        node_name = f"{safe_name}_materialMorph"

        if cmds.objExists(node_name):
            morph_node = node_name
        else:
            morph_node = cmds.createNode("network", name=node_name)

        if not cmds.attributeQuery("weight", node=morph_node, exists=True):
            cmds.addAttr(
                morph_node,
                longName="weight",
                attributeType="double",
                minValue=0.0,
                maxValue=1.0,
                defaultValue=0.0,
                keyable=True,
            )

        offsets = []
        for offset in getattr(morph, "offsets", []):
            if "material_index" not in offset:
                continue
            offsets.append(
                {
                    "material_index": int(offset["material_index"]),
                    "operation_type": int(offset.get("operation_type", 0)),
                    "diffuse": self._json_float_list(offset.get("diffuse", (0.0, 0.0, 0.0, 0.0))),
                    "specular": self._json_float_list(offset.get("specular", (0.0, 0.0, 0.0))),
                    "specular_coefficient": float(offset.get("specular_coefficient", 0.0)),
                    "ambient": self._json_float_list(offset.get("ambient", (0.0, 0.0, 0.0))),
                    "edge_color": self._json_float_list(offset.get("edge_color", (0.0, 0.0, 0.0, 0.0))),
                    "edge_size": float(offset.get("edge_size", 0.0)),
                    "texture_factor": self._json_float_list(offset.get("texture_factor", (0.0, 0.0, 0.0, 0.0))),
                    "sphere_texture_factor": self._json_float_list(
                        offset.get("sphere_texture_factor", (0.0, 0.0, 0.0, 0.0))
                    ),
                    "toon_texture_factor": self._json_float_list(
                        offset.get("toon_texture_factor", (0.0, 0.0, 0.0, 0.0))
                    ),
                }
            )

        maya_utils.set_custom_attributes(
            morph_node,
            {
                "mmd_morph_name": str(morph_name),
                "mmd_morph_name_en": str(getattr(morph, "name_english", "")),
                "mmd_morph_type": "material",
                "mmd_morph_panel": int(getattr(morph, "panel", 0)),
                "mmd_material_morph_offset_count": len(offsets),
                "mmd_material_morph_offsets_json": json.dumps(offsets, ensure_ascii=False, separators=(",", ":")),
            },
        )

        return {
            "success": True,
            "morph_name": morph_name,
            "morph_node": morph_node,
            "morph_type": "material",
            "offset_count": len(offsets),
        }

    @staticmethod
    def _json_float_list(values) -> List[float]:
        """JSON metadata 用に数値列を float list へ正規化する。"""
        return [float(v) for v in values]

    def _convert_vertex_morph_pmx(self, morph, mesh_node: str) -> Dict[str, Any]:
        """PMX頂点モーフの変換"""
        # モーフ名をMaya互換に変換
        morph_name = maya_utils.sanitize_text(morph.get_name())

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
            "morph_name": morph.get_name(),
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
                points[vertex_index] += om.MVector(offset_pos[0], offset_pos[1], offset_pos[2])

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
                        points[vertex_index] += om.MVector(offset_pos[0], offset_pos[1], offset_pos[2])

        # 変更された頂点位置を設定
        mesh_fn.setPoints(points, om.MSpace.kObject)
