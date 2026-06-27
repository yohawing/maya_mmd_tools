"""
MMDのモーフデータをMayaのblendShapeに変換するモジュール。

このモジュールは、PMD/PMXファイルのモーフデータを解析し、
Mayaのブレンドシェイプシステムに変換する機能を提供します。
"""

import json
import time
from typing import Any, Dict, List, Optional, Set, Union

from maya import cmds
from maya.api import OpenMaya as om

from mmd_tools.core import maya_utils
from mmd_tools.core.constants import (
    ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON,
    ATTR_MMD_MATERIAL_INDEX,
    ATTR_MMD_MORPH_GROUP_SPLIT_MESH,
    ATTR_MMD_SOURCE_VERTEX_INDICES,
    ATTR_MMD_VERTEX_MORPH_NAMES_JSON,
)
from mmd_tools.core.pmx_data.morph import PmxMorphType


class MorphConverter:
    """MMDのモーフデータをMayaのblendShapeに変換するクラス"""

    def __init__(self):
        from mmd_tools import settings
        from mmd_tools.core.logger import get_logger

        self.settings = settings.get("import.morph", {})
        self.logger = get_logger(__name__)
        self.profile = {}

    def _add_profile_time(self, key: str, start: float) -> None:
        """Accumulate timing in the converter profile."""
        self.profile[key] = round(float(self.profile.get(key, 0.0)) + time.perf_counter() - start, 6)

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
        material_vertex_sets = self._build_pmx_material_vertex_sets(pmx_data)
        skipped_vertex_morphs_by_material = 0
        skipped_vertex_morphs_by_group = 0

        for mn in mesh_nodes:
            mesh_material_index = self._get_mesh_material_index(mn)
            visible_vertex_indices = (
                material_vertex_sets.get(mesh_material_index)
                if mesh_material_index is not None
                else None
            )
            allowed_vertex_morph_names = self._get_mesh_vertex_morph_name_filter(mn)
            template_ctx = {}
            try:
                for morph_index, morph in enumerate(pmx_data.morphs):
                    try:
                        if morph.morph_type == PmxMorphType.VertexMorph:
                            morph_name = morph.get_name()
                            if allowed_vertex_morph_names is not None and morph_name not in allowed_vertex_morph_names:
                                skipped_vertex_morphs_by_group += 1
                                self.logger.debug(
                                    "Skipping vertex morph %s for morph group split mesh %s",
                                    morph_name,
                                    mn,
                                )
                                continue
                            if visible_vertex_indices is not None and not self._vertex_morph_affects_vertices(
                                morph,
                                visible_vertex_indices,
                            ):
                                skipped_vertex_morphs_by_material += 1
                                self.logger.debug(
                                    "Skipping vertex morph %s for material split mesh %s (material_index=%s)",
                                    morph.get_name(),
                                    mn,
                                    mesh_material_index,
                                )
                                continue
                            self.logger.debug(f"Converting vertex morph: {morph.name}")
                            result = self._convert_vertex_morph_pmx(morph, mn, template_ctx=template_ctx)
                            if result["success"]:
                                results.append(result)
                                if result["blend_shape_node"] not in blend_shape_nodes:
                                    blend_shape_nodes.append(result["blend_shape_node"])
                                self.logger.info(f"Successfully converted morph: {morph.name}")
                        elif morph.morph_type == PmxMorphType.BoneMorph and morph.name not in converted_bone_morphs:
                            self.logger.debug(f"Converting bone morph metadata: {morph.name}")
                            result = self._convert_bone_morph_pmx(morph, morph_index)
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
                            result = self._convert_material_morph_pmx(morph, morph_index)
                            if result["success"]:
                                converted_material_morphs.add(morph.name)
                                results.append(result)
                                material_morph_nodes.append(result["morph_node"])
                                self.logger.info(f"Successfully imported material morph metadata: {morph.name}")
                    except Exception as e:
                        self.logger.warning(f"Failed to convert morph {morph.name}: {e}")
            finally:
                self._flush_vertex_morph_name_mapping(template_ctx)
                self.cleanup_vertex_morph_template(template_ctx)

        return {
            "success": True,
            "morphs_converted": len(results),
            "total_morphs": len(pmx_data.morphs),
            "blend_shape_nodes": blend_shape_nodes,
            "bone_morph_nodes": bone_morph_nodes,
            "material_morph_nodes": material_morph_nodes,
            "vertex_morphs_skipped_by_material": skipped_vertex_morphs_by_material,
            "vertex_morphs_skipped_by_group": skipped_vertex_morphs_by_group,
            "results": results,
        }

    def _build_pmx_material_vertex_sets(self, pmx_data) -> Dict[int, Set[int]]:
        """Return vertex indices referenced by each PMX material face range."""
        material_vertex_sets: Dict[int, Set[int]] = {}
        face_offset = 0
        faces = getattr(pmx_data, "faces", []) or []
        for material_index, material in enumerate(getattr(pmx_data, "materials", []) or []):
            num_material_faces = int(getattr(material, "face_count", 0) or 0) // 3
            vertices: Set[int] = set()
            for face in faces[face_offset : face_offset + num_material_faces]:
                vertices.update(int(idx) for idx in getattr(face, "indices", []) or [])
            material_vertex_sets[material_index] = vertices
            face_offset += num_material_faces
        return material_vertex_sets

    def _get_mesh_material_index(self, mesh_node: str) -> Optional[int]:
        """Return material index for a material-split mesh, or None for unified meshes."""
        try:
            if not cmds.objExists(mesh_node):
                return None
            if not cmds.attributeQuery("mmd_material_split_mesh", node=mesh_node, exists=True):
                return None
            if not bool(cmds.getAttr(f"{mesh_node}.mmd_material_split_mesh")):
                return None
            if not cmds.attributeQuery(ATTR_MMD_MATERIAL_INDEX, node=mesh_node, exists=True):
                return None
            return int(cmds.getAttr(f"{mesh_node}.{ATTR_MMD_MATERIAL_INDEX}"))
        except Exception:
            return None

    def _get_mesh_source_vertex_map(self, mesh_node: str) -> Optional[Dict[int, int]]:
        """Return original PMX vertex index to local mesh vertex index mapping."""
        try:
            if not cmds.objExists(mesh_node):
                return None
            if not cmds.attributeQuery(ATTR_MMD_SOURCE_VERTEX_INDICES, node=mesh_node, exists=True):
                return None
            source_indices = maya_utils.get_int_array_attribute(mesh_node, ATTR_MMD_SOURCE_VERTEX_INDICES)
            if not source_indices:
                return None
            return {source_index: local_index for local_index, source_index in enumerate(source_indices)}
        except Exception:
            return None

    def _get_mesh_vertex_morph_name_filter(self, mesh_node: str) -> Optional[Set[str]]:
        """Return allowed vertex morph names for a morph-group split mesh."""
        try:
            if not cmds.objExists(mesh_node):
                return None
            if not cmds.attributeQuery(ATTR_MMD_MORPH_GROUP_SPLIT_MESH, node=mesh_node, exists=True):
                return None
            if not bool(cmds.getAttr(f"{mesh_node}.{ATTR_MMD_MORPH_GROUP_SPLIT_MESH}")):
                return None
            if not cmds.attributeQuery(ATTR_MMD_VERTEX_MORPH_NAMES_JSON, node=mesh_node, exists=True):
                return set()
            raw_names = cmds.getAttr(f"{mesh_node}.{ATTR_MMD_VERTEX_MORPH_NAMES_JSON}") or "[]"
            names = json.loads(raw_names)
            if not isinstance(names, list):
                return set()
            return {str(name) for name in names}
        except Exception:
            return None

    def _vertex_morph_affects_vertices(self, morph, vertex_indices: Set[int]) -> bool:
        """Return True when a vertex morph touches at least one visible vertex."""
        for offset in getattr(morph, "offsets", []) or []:
            try:
                vertex_index = int(offset.get("vertex_index"))
            except Exception:
                continue
            if vertex_index in vertex_indices:
                return True
        return False

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

    @staticmethod
    def _raw_morph_name(morph) -> str:
        """VMD/PMX が参照する生のモーフ名（PmxMorph.name 相当）を返す。

        VmdConverter のランタイムベイクは PmxMorph.name でモーフを引くため、
        alias (sanitize 済み) ではなくこの生名を権威キーとして保存・登録する。
        """
        raw = getattr(morph, "name", "") or ""
        if raw:
            return str(raw)
        getter = getattr(morph, "get_name", None)
        if callable(getter):
            return str(getter() or "")
        return ""

    def _existing_blendshape_aliases(self, blend_shape_node: str) -> Set[str]:
        """blendShape ノードに既に割り当て済みの alias 名集合を返す。"""
        flat = cmds.aliasAttr(blend_shape_node, query=True) or []
        # aliasAttr -q はフラットな [alias, attr, alias, attr, ...] を返す
        return set(flat[0::2])

    def _unique_blendshape_alias(self, blend_shape_node: str, base_alias: str) -> str:
        """blendShape ノード内で一意な alias を返す。

        sanitize_text は lossy なため、異なるモーフが同一 ASCII に化けて
        aliasAttr が衝突し片方が到達不能になることがある。数値サフィックスで回避する。
        """
        existing = self._existing_blendshape_aliases(blend_shape_node)
        if base_alias not in existing:
            return base_alias
        index = 1
        while f"{base_alias}_{index}" in existing:
            index += 1
        return f"{base_alias}_{index}"

    @staticmethod
    def _unique_blendshape_alias_from_existing(base_alias: str, existing: Set[str]) -> str:
        """既に取得済みの alias 集合から一意な alias を返す。"""
        if base_alias not in existing:
            return base_alias
        index = 1
        while f"{base_alias}_{index}" in existing:
            index += 1
        return f"{base_alias}_{index}"

    def _load_blendshape_morph_names(self, blend_shape_node: str) -> Dict[str, str]:
        """blendShape ノードの weight index → 生モーフ名 JSON を読み込む。"""
        if not cmds.attributeQuery(ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON, node=blend_shape_node, exists=True):
            cmds.addAttr(blend_shape_node, longName=ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON, dataType="string")
            return {}
        try:
            raw = cmds.getAttr(f"{blend_shape_node}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}") or "{}"
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return {str(k): str(v) for k, v in parsed.items()}
        except (TypeError, ValueError):
            pass
        return {}

    def _flush_vertex_morph_name_mapping(self, template_ctx: Dict[str, Any]) -> None:
        """vertex morph ループで蓄積した morph name mapping を一括保存する。"""
        blend_shape_node = template_ctx.get("blend_shape_node")
        names = template_ctx.get("morph_name_mapping")
        if not template_ctx.get("morph_name_mapping_dirty") or not blend_shape_node or not names:
            return
        start = time.perf_counter()
        cmds.setAttr(
            f"{blend_shape_node}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}",
            json.dumps(names, ensure_ascii=False, separators=(",", ":")),
            type="string",
        )
        self._add_profile_time("morph_name_store_sec", start)
        template_ctx["morph_name_mapping_dirty"] = False

    def _store_blendshape_morph_name(self, blend_shape_node: str, target_index: int, raw_name: str) -> None:
        """blendShape ノードに weight index → 生モーフ名 の対応を JSON で保存する。"""
        if not raw_name:
            return
        names: Dict[str, str] = {}
        if cmds.attributeQuery(ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON, node=blend_shape_node, exists=True):
            try:
                raw = cmds.getAttr(f"{blend_shape_node}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}") or "{}"
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    names = {str(k): str(v) for k, v in parsed.items()}
            except (TypeError, ValueError):
                names = {}
        else:
            cmds.addAttr(blend_shape_node, longName=ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON, dataType="string")
        names[str(target_index)] = str(raw_name)
        cmds.setAttr(
            f"{blend_shape_node}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}",
            json.dumps(names, ensure_ascii=False, separators=(",", ":")),
            type="string",
        )

    def _convert_vertex_morph_pmd(self, morph, mesh_node: str) -> Dict[str, Any]:
        """PMD頂点モーフの変換"""
        # モーフ名をMaya互換に変換
        raw_name = self._raw_morph_name(morph)
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

        # ターゲットの名前を設定（衝突時は一意化）。生名は別途保存し正確なマッピングを保証する。
        alias = self._unique_blendshape_alias(blend_shape_node, morph_name)
        cmds.aliasAttr(alias, f"{blend_shape_node}.w[{target_index}]")
        self._store_blendshape_morph_name(blend_shape_node, target_index, raw_name)

        return {
            "success": True,
            "morph_name": morph.get_name(),
            "blend_shape_node": blend_shape_node,
            "target_index": target_index,
            "alias": alias,
        }

    def _convert_bone_morph_pmx(self, morph, morph_index: int = 0) -> Dict[str, Any]:
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
                "mmd_morph_index": int(morph_index),
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

    def _convert_material_morph_pmx(self, morph, morph_index: int = 0) -> Dict[str, Any]:
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
                "mmd_morph_index": int(morph_index),
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

    def _convert_vertex_morph_pmx(
        self, morph, mesh_node: str, *, template_ctx: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """PMX頂点モーフの変換

        template_ctx を渡すと、メッシュ複製を使い回して高速化する。
        初回呼び出し時に template_ctx へ内部状態を書き込むので、
        呼び出し元は空 dict を渡してループ終了後に cleanup_vertex_morph_template() を呼ぶこと。
        """
        raw_name = self._raw_morph_name(morph)
        morph_name = maya_utils.sanitize_text(morph.get_name())

        if template_ctx is not None:
            if "target_mesh" not in template_ctx:
                target_mesh = cmds.duplicate(mesh_node)[0]
                target_mesh = cmds.rename(target_mesh, "_morph_template")
                maya_utils.set_attribute(target_mesh, "visibility", 0, "bool")
                sel = om.MSelectionList()
                sel.add(target_mesh)
                dag = sel.getDagPath(0)
                mesh_fn = om.MFnMesh(dag)
                template_ctx["target_mesh"] = target_mesh
                template_ctx["dag_path"] = dag
                template_ctx["mesh_fn"] = mesh_fn
                template_ctx["base_points"] = mesh_fn.getPoints(om.MSpace.kObject)
                template_ctx["source_to_local"] = self._get_mesh_source_vertex_map(mesh_node)
                template_ctx["blend_shape_node"] = maya_utils.find_or_create_blendshape_node(mesh_node)
                template_ctx["next_target_index"] = 0
                template_ctx["existing_aliases"] = self._existing_blendshape_aliases(
                    template_ctx["blend_shape_node"],
                )
                template_ctx["morph_name_mapping"] = self._load_blendshape_morph_names(
                    template_ctx["blend_shape_node"],
                )
                template_ctx["morph_name_mapping_dirty"] = False

            # base_points から Python コピー → オフセット適用 → 1回の setPoints
            # リセット用 setPoints + getPoints を完全に排除
            target_points_start = time.perf_counter()
            target_points = self._compute_target_points(
                template_ctx["base_points"], morph, template_ctx["source_to_local"],
            )
            template_ctx["mesh_fn"].setPoints(target_points, om.MSpace.kObject)
            self._add_profile_time("target_points_sec", target_points_start)

            target_mesh = cmds.duplicate(template_ctx["target_mesh"])[0]
            target_mesh = cmds.rename(target_mesh, f"{morph_name}_target")
            maya_utils.set_attribute(target_mesh, "visibility", 0, "bool")
            blend_shape_node = template_ctx["blend_shape_node"]
            target_index = template_ctx["next_target_index"]
            template_ctx["next_target_index"] = target_index + 1
        else:
            target_mesh = cmds.duplicate(mesh_node)[0]
            target_mesh = cmds.rename(target_mesh, f"{morph_name}_target")
            maya_utils.set_attribute(target_mesh, "visibility", 0, "bool")
            source_to_local = self._get_mesh_source_vertex_map(mesh_node)
            target_points_start = time.perf_counter()
            self._apply_vertex_offsets_pmx(target_mesh, morph, source_to_local=source_to_local)
            self._add_profile_time("target_points_sec", target_points_start)
            blend_shape_node = maya_utils.find_or_create_blendshape_node(mesh_node)
            target_count = cmds.blendShape(blend_shape_node, query=True, target=True)
            target_index = len(target_count) if target_count else 0

        blendshape_add_start = time.perf_counter()
        try:
            cmds.blendShape(
                blend_shape_node,
                edit=True,
                target=(mesh_node, target_index, target_mesh, 1.0),
            )
        except Exception:
            if target_mesh and cmds.objExists(target_mesh):
                cmds.delete(target_mesh)
            raise
        self._add_profile_time("blendshape_add_sec", blendshape_add_start)
        if target_mesh and cmds.objExists(target_mesh):
            cmds.delete(target_mesh)

        existing_aliases = template_ctx.get("existing_aliases") if template_ctx is not None else None
        if existing_aliases is not None:
            alias = self._unique_blendshape_alias_from_existing(morph_name, existing_aliases)
        else:
            alias = self._unique_blendshape_alias(blend_shape_node, morph_name)
        alias_start = time.perf_counter()
        cmds.aliasAttr(alias, f"{blend_shape_node}.w[{target_index}]")
        self._add_profile_time("alias_sec", alias_start)
        if existing_aliases is not None:
            existing_aliases.add(alias)

        if template_ctx is not None and "morph_name_mapping" in template_ctx:
            if raw_name:
                template_ctx["morph_name_mapping"][str(target_index)] = str(raw_name)
                template_ctx["morph_name_mapping_dirty"] = True
        else:
            morph_name_store_start = time.perf_counter()
            self._store_blendshape_morph_name(blend_shape_node, target_index, raw_name)
            self._add_profile_time("morph_name_store_sec", morph_name_store_start)

        return {
            "success": True,
            "morph_name": morph.get_name(),
            "blend_shape_node": blend_shape_node,
            "target_index": target_index,
            "alias": alias,
        }

    @staticmethod
    def _compute_target_points(
        base_points: om.MPointArray,
        morph,
        source_to_local: Optional[Dict[int, int]],
    ) -> om.MPointArray:
        """base_points + morph offsets → 新しい MPointArray を返す（メッシュ操作なし）。"""
        points = om.MPointArray(base_points)
        n_points = len(points)
        if not hasattr(morph, "offsets"):
            return points
        for offset in morph.offsets:
            if "vertex_index" not in offset or "position_offset" not in offset:
                continue
            src_vi = int(offset["vertex_index"])
            if source_to_local is not None:
                vi = source_to_local.get(src_vi)
                if vi is None:
                    continue
            else:
                vi = src_vi
            if vi < n_points:
                pos = offset["position_offset"]
                points[vi] += MorphConverter._pmx_vertex_offset_to_maya_vector(pos)
        return points

    @staticmethod
    def _pmx_vertex_offset_to_maya_vector(position_offset) -> om.MVector:
        """Return a PMX vertex morph offset converted into Maya mesh space."""
        return om.MVector(position_offset[0], position_offset[1], -position_offset[2])

    @staticmethod
    def cleanup_vertex_morph_template(template_ctx: Dict[str, Any]) -> None:
        """テンプレートメッシュを削除する。"""
        target = template_ctx.get("target_mesh")
        if target and cmds.objExists(target):
            cmds.delete(target)

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

    def _apply_vertex_offsets_pmx(self, mesh_node: str, morph, source_to_local: Optional[Dict[int, int]] = None):
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
                    source_vertex_index = int(offset["vertex_index"])
                    if source_to_local is not None:
                        if source_vertex_index not in source_to_local:
                            continue
                        vertex_index = source_to_local[source_vertex_index]
                    else:
                        vertex_index = source_vertex_index
                    offset_pos = offset["position_offset"]
                    if vertex_index < len(points):
                        points[vertex_index] += self._pmx_vertex_offset_to_maya_vector(offset_pos)

        # 変更された頂点位置を設定
        mesh_fn.setPoints(points, om.MSpace.kObject)
