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

from mmd_tools.core import maya_attribute_utils, maya_mesh_utils, maya_name_utils, settings_keys as setting_keys
from mmd_tools.core.constants import (
    ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON,
    ATTR_MMD_BONE_MORPH_OFFSETS_RAW_JSON,
    ATTR_MMD_MATERIAL_INDEX,
    ATTR_MMD_SOURCE_VERTEX_INDICES,
)
from mmd_tools.core.pmx_data.morph import PmxMorphType
from mmd_tools.converters.morph_scene_metadata import (
    iter_morph_network_metadata,
    read_blendshape_morph_entry_strings,
)

_OPT_IMPORT_MORPHS = "import_morphs"


def _is_group_morph_payload(morph: Dict[str, Any]) -> bool:
    """Return whether an exporter morph payload represents a GroupMorph."""
    morph_type = morph.get("type", morph.get("morph_type"))
    if isinstance(morph_type, str):
        return morph_type.lower() == "group"
    return morph_type == PmxMorphType.GroupMorph or morph_type == int(PmxMorphType.GroupMorph)


def _order_morphs_by_index_if_grouped(
    morphs: List[Dict[str, Any]],
    *,
    strip_index: bool = False,
    require_contiguous: bool = False,
) -> List[Dict[str, Any]]:
    """Restore PMX morph order when a GroupMorph makes indices observable.

    ``index`` is an internal provenance field.  A GroupMorph offset references
    the PMX morph table, so silently accepting an incomplete or duplicated
    index mapping would produce a structurally valid but semantically wrong
    export.  Network-only payloads may omit vertex morphs and therefore use
    non-contiguous indices; the model-root merge can require a complete
    zero-based map with ``require_contiguous``.
    """
    ordered = list(morphs)
    if any(_is_group_morph_payload(morph) for morph in ordered):
        indices = []
        for position, morph in enumerate(ordered):
            if "index" not in morph:
                raise ValueError(f"Cannot restore PMX morph order: morph {position} is missing index")
            index = morph["index"]
            if isinstance(index, bool) or not isinstance(index, int):
                raise ValueError(
                    f"Cannot restore PMX morph order: morph {position} index must be a non-bool integer"
                )
            if index < 0:
                raise ValueError(f"Cannot restore PMX morph order: morph {position} index must be non-negative")
            indices.append(index)

        if len(set(indices)) != len(indices):
            raise ValueError(f"Cannot restore PMX morph order: duplicate morph indices {indices}")
        if require_contiguous:
            expected = set(range(len(ordered)))
            if set(indices) != expected:
                raise ValueError(
                    f"Cannot restore PMX morph order: expected indices {sorted(expected)}, got {sorted(indices)}"
                )
        ordered.sort(key=lambda morph: morph["index"])

    if strip_index:
        return [{key: value for key, value in morph.items() if key != "index"} for morph in ordered]
    return ordered


def _scene_name_set() -> Set[str]:
    """Return conservative leaf-name reservations from the current scene."""
    try:
        nodes = cmds.ls() or []
    except Exception:
        return set()
    names = set()
    for node in nodes:
        leaf = str(node).rsplit("|", 1)[-1]
        names.add(leaf)
        names.add(leaf.rsplit(":", 1)[-1])
    return names


class MorphConverter:
    """MMDのモーフデータをMayaのblendShapeに変換するクラス"""

    def __init__(self, scale: float = 1.0):
        from mmd_tools import settings
        from mmd_tools.core.logger import get_logger

        self.settings = settings.get(setting_keys.IMPORT_MORPH, {})
        self.logger = get_logger(__name__)
        self.scale = float(scale)
        self.profile = {}
        self._morph_node_name_used = _scene_name_set()

    def _add_profile_time(self, key: str, start: float) -> None:
        """Accumulate timing in the converter profile."""
        self.profile[key] = round(float(self.profile.get(key, 0.0)) + time.perf_counter() - start, 6)

    def validate_runtime_requirements(self, pmx_data) -> None:
        """Fail before scene mutation when the morph controller type is unavailable.

        Args:
            pmx_data: Parsed PMX-compatible data containing a ``morphs`` list.

        Raises:
            RuntimeError: If morph import needs the plugin-owned controller node
                but the Maya node type is not registered.
        """
        if not self.settings.get(_OPT_IMPORT_MORPHS, True):
            return
        if not getattr(pmx_data, "morphs", None):
            return
        if "mmdMorphController" not in (cmds.allNodeTypes() or []):
            raise RuntimeError(
                "Required node type 'mmdMorphController' is unavailable. "
                "Load or reload the maya_mmd_tools plugin before importing a PMX with morphs."
            )

    def convert_pmx_morphs(self, pmx_data, mesh_node: Union[str, List[str]]) -> Dict[str, Any]:
        """
        PMXのモーフデータをMayaのブレンドシェイプに変換する。

        Args:
            pmx_data: 解析されたPMXデータオブジェクト
            mesh_node (str or list): ブレンドシェイプを適用するMayaのメッシュノード名、またはそのリスト。

        Returns:
            Dict[str, Any]: 変換結果の辞書
        """
        if not self.settings.get(_OPT_IMPORT_MORPHS, True):
            return {"success": True, "morphs_converted": 0}

        mesh_nodes = [mesh_node] if isinstance(mesh_node, str) else (mesh_node or [])

        results = []
        blend_shape_nodes = []
        bone_morph_nodes = []
        group_morph_nodes = []
        material_morph_nodes = []
        converted_bone_morphs = set()
        converted_group_morphs = set()
        converted_material_morphs = set()
        material_vertex_sets = self._build_pmx_material_vertex_sets(pmx_data)
        skipped_vertex_morphs_by_material = 0

        for mn in mesh_nodes:
            mesh_material_index = self._get_mesh_material_index(mn)
            visible_vertex_indices = (
                material_vertex_sets.get(mesh_material_index)
                if mesh_material_index is not None
                else None
            )
            template_ctx = {}
            try:
                for morph_index, morph in enumerate(pmx_data.morphs):
                    try:
                        if morph.morph_type == PmxMorphType.VertexMorph:
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
                            result = self._convert_vertex_morph_pmx(
                                morph, mn, morph_index=morph_index, template_ctx=template_ctx
                            )
                            if result["success"]:
                                results.append(result)
                                if result["blend_shape_node"] not in blend_shape_nodes:
                                    blend_shape_nodes.append(result["blend_shape_node"])
                                self.logger.debug(f"Successfully converted morph: {morph.name}")
                        elif morph.morph_type == PmxMorphType.BoneMorph and morph.name not in converted_bone_morphs:
                            self.logger.debug(f"Converting bone morph metadata: {morph.name}")
                            result = self._convert_bone_morph_pmx(morph, morph_index)
                            if result["success"]:
                                converted_bone_morphs.add(morph.name)
                                results.append(result)
                                bone_morph_nodes.append(result["morph_node"])
                                self.logger.debug(f"Successfully imported bone morph metadata: {morph.name}")
                        elif morph.morph_type == PmxMorphType.GroupMorph and morph.name not in converted_group_morphs:
                            self.logger.debug(f"Converting group morph metadata: {morph.name}")
                            result = self._convert_group_morph_pmx(morph, morph_index)
                            if result["success"]:
                                converted_group_morphs.add(morph.name)
                                results.append(result)
                                group_morph_nodes.append(result["morph_node"])
                                self.logger.debug(f"Successfully imported group morph metadata: {morph.name}")
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
                                self.logger.debug(f"Successfully imported material morph metadata: {morph.name}")
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
            "group_morph_nodes": group_morph_nodes,
            "material_morph_nodes": material_morph_nodes,
            "vertex_morphs_skipped_by_material": skipped_vertex_morphs_by_material,
            "results": results,
        }

    def build_morph_controller(self, pmx_data, root_group: str, morph_result: Dict[str, Any]) -> Optional[str]:
        """Create one fixed-topology controller and connect all supported morph leaves."""
        if not morph_result.get("total_morphs"):
            return None
        root_leaf = root_group.split("|")[-1].rsplit(":", 1)[-1]
        controller_name = maya_name_utils.sanitize_unique_name(
            f"{root_leaf}_morphController",
            self._morph_node_name_used,
            fallback="morphController",
        )
        controller = cmds.createNode("mmdMorphController", name=controller_name)
        cmds.addAttr(root_group, longName="mmd_morph_controller", attributeType="message")
        cmds.connectAttr(f"{controller}.message", f"{root_group}.mmd_morph_controller")

        existing_aliases: Set[str] = set(cmds.listAttr(controller) or [])
        existing_aliases.update(cmds.listAttr(controller, shortNames=True) or [])
        for morph_index, morph in enumerate(pmx_data.morphs):
            input_plug = f"{controller}.inputWeight[{morph_index}]"
            cmds.setAttr(input_plug, 0.0)
            cmds.setAttr(input_plug, keyable=True)

            raw_name = self._raw_morph_name(morph)
            display_name = raw_name or str(getattr(morph, "name_english", "") or "")
            alias = maya_name_utils.sanitize_unique_name(
                display_name,
                existing_aliases,
                fallback=f"morph_{morph_index}",
            )
            cmds.aliasAttr(alias, input_plug)

        groups = {
            index: morph
            for index, morph in enumerate(pmx_data.morphs)
            if morph.morph_type == PmxMorphType.GroupMorph
        }
        group_rates: Dict[int, Dict[int, float]] = {}

        def expand(source: int, current: int, rate: float, path: Set[int]) -> None:
            for offset in getattr(groups[current], "offsets", []):
                try:
                    target = int(offset["morph_index"])
                    next_rate = rate * float(offset.get("morph_rate", 0.0))
                except (KeyError, TypeError, ValueError):
                    continue
                if target in path:
                    continue
                sources = group_rates.setdefault(target, {})
                sources[source] = sources.get(source, 0.0) + next_rate
                if target in groups:
                    expand(source, target, next_rate, path | {target})

        for group_index in groups:
            expand(group_index, group_index, 1.0, {group_index})
        topology = {
            str(target): [[source, rate] for source, rate in sorted(sources.items())]
            for target, sources in sorted(group_rates.items())
        }
        cmds.setAttr(f"{controller}.topologyVersion", 1, lock=True)
        cmds.setAttr(
            f"{controller}.groupTopology",
            json.dumps(topology, separators=(",", ":")),
            type="string",
            lock=True,
        )

        destinations: Dict[int, Set[str]] = {}
        for blend_shape in morph_result.get("blend_shape_nodes", []):
            for weight_index, entry in read_blendshape_morph_entry_strings(blend_shape).items():
                if "index" in entry:
                    destinations.setdefault(int(entry["index"]), set()).add(
                        f"{blend_shape}.weight[{int(weight_index)}]"
                    )
        for morph_node in morph_result.get("bone_morph_nodes", []) + morph_result.get("material_morph_nodes", []):
            index = int(cmds.getAttr(f"{morph_node}.mmd_morph_index"))
            destinations.setdefault(index, set()).add(f"{morph_node}.weight")
        for leaf_index, leaf_destinations in sorted(destinations.items()):
            for destination in sorted(leaf_destinations):
                cmds.connectAttr(f"{controller}.outputWeight[{leaf_index}]", destination, force=True)
        morph_result["morph_controller"] = controller
        return controller

    def _convert_group_morph_pmx(self, morph, morph_index: int = 0) -> Dict[str, Any]:
        """PMXグループモーフをMayaのnetwork nodeとしてインポートする。"""
        morph_name = morph.get_name()
        morph_node = self._create_or_get_morph_network_node(morph_name, "group")

        offsets = []
        for offset in getattr(morph, "offsets", []):
            if "morph_index" not in offset:
                continue
            offsets.append(
                {
                    "morph_index": int(offset["morph_index"]),
                    "morph_rate": float(offset.get("morph_rate", 0.0)),
                }
            )

        maya_attribute_utils.set_custom_attributes(
            morph_node,
            {
                "mmd_morph_name": str(morph_name),
                "mmd_morph_name_en": str(getattr(morph, "name_english", "")),
                "mmd_morph_type": "group",
                "mmd_morph_index": int(morph_index),
                "mmd_morph_panel": int(getattr(morph, "panel", 0)),
                "mmd_group_morph_offset_count": len(offsets),
                "mmd_group_morph_offsets_json": json.dumps(offsets, ensure_ascii=False, separators=(",", ":")),
            },
        )

        return {
            "success": True,
            "morph_name": morph_name,
            "morph_node": morph_node,
            "morph_type": "group",
            "offset_count": len(offsets),
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
            source_indices = maya_attribute_utils.get_int_array_attribute(mesh_node, ATTR_MMD_SOURCE_VERTEX_INDICES)
            if not source_indices:
                return None
            return {source_index: local_index for local_index, source_index in enumerate(source_indices)}
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

    def collect_morphs_from_scene_for_export(
        self,
        root_group: Optional[str] = None,
        *,
        require_contiguous: bool = True,
    ) -> List[Dict[str, Any]]:
        """Collect exporter morph dicts from network nodes owned by a model root.

        Args:
            root_group: Optional model root used to scope the network query.
                ``None`` preserves the legacy scene-wide query for callers that
                do not have a model ownership boundary.
            require_contiguous: Require a complete zero-based PMX morph index
                table. Model-root collection sets this to ``False`` while
                collecting the network subset before vertex morphs are merged.
        """
        morphs = []
        offsets_attrs = {
            "bone": "mmd_bone_morph_offsets_json",
            "group": "mmd_group_morph_offsets_json",
            "material": "mmd_material_morph_offsets_json",
        }

        for metadata in iter_morph_network_metadata(
            root_group=root_group,
            morph_types={"bone", "group", "material"},
        ):
            morph_node = metadata.node
            try:
                offsets_attr = offsets_attrs.get(metadata.morph_type)
                if offsets_attr is None:
                    self.logger.warning(
                        f"skip morph node {morph_node}: unsupported morph type {metadata.morph_type!r}"
                    )
                    continue
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

                morph_payload = {
                    "type": metadata.morph_type,
                    "name": metadata.name,
                    "name_english": metadata.name_english,
                    "panel": metadata.panel,
                    "offsets": offsets,
                }
                if metadata.index is not None:
                    morph_payload["index"] = metadata.index
                morphs.append(morph_payload)
            except Exception as e:
                self.logger.warning(f"skip morph node {morph_node}: {e}")

        return _order_morphs_by_index_if_grouped(
            morphs,
            require_contiguous=require_contiguous,
        )

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
        return maya_name_utils.sanitize_unique_name(base_alias, existing)

    @staticmethod
    def _unique_blendshape_alias_from_existing(base_alias: str, existing: Set[str]) -> str:
        """既に取得済みの alias 集合から一意な alias を返す。"""
        return maya_name_utils.sanitize_unique_name(base_alias, existing)

    def _load_blendshape_morph_names(self, blend_shape_node: str) -> Dict[str, Dict[str, object]]:
        """blendShape ノードの weight index → 生モーフ名 JSON を読み込む。"""
        return read_blendshape_morph_entry_strings(blend_shape_node, ensure_attr=True)

    def _flush_vertex_morph_name_mapping(self, template_ctx: Dict[str, Any]) -> None:
        """vertex morph ループで蓄積した morph name mapping を一括保存する。"""
        blend_shape_node = template_ctx.get("blend_shape_node")
        names = template_ctx.get("morph_name_mapping")
        if not template_ctx.get("morph_name_mapping_dirty") or not blend_shape_node or not names:
            return
        start = time.perf_counter()
        maya_attribute_utils.write_json_attr(blend_shape_node, ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON, names)
        self._add_profile_time("morph_name_store_sec", start)
        template_ctx["morph_name_mapping_dirty"] = False

    def _store_blendshape_morph_name(
        self, blend_shape_node: str, target_index: int, raw_name: str, morph_index: int
    ) -> None:
        """blendShape ノードに weight index → 生モーフ名 の対応を JSON で保存する。"""
        if not raw_name:
            return
        names = read_blendshape_morph_entry_strings(blend_shape_node, ensure_attr=True)
        names[str(target_index)] = {"name": str(raw_name), "index": int(morph_index)}
        maya_attribute_utils.write_json_attr(blend_shape_node, ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON, names)

    def _convert_bone_morph_pmx(self, morph, morph_index: int = 0) -> Dict[str, Any]:
        """PMXボーンモーフをMayaのnetwork nodeとしてインポートする。

        ここでは joint 変形へは接続せず、VMD morph frame がキー化できる
        `weight` と、後段評価用の offset metadata だけを作る。
        """
        morph_name = morph.get_name()
        morph_node = self._create_or_get_morph_network_node(morph_name, "bone")

        offsets = []
        raw_offsets = []
        for offset in getattr(morph, "offsets", []):
            if "bone_index" not in offset:
                continue
            raw_offset = {
                "bone_index": int(offset["bone_index"]),
                "translation": [float(v) for v in offset.get("translation", (0.0, 0.0, 0.0))],
                "rotation": [float(v) for v in offset.get("rotation", (0.0, 0.0, 0.0, 1.0))],
            }
            raw_offsets.append(raw_offset)
            offsets.append(
                {
                    **raw_offset,
                    "translation": [value * self.scale for value in raw_offset["translation"]],
                }
            )

        maya_attribute_utils.set_custom_attributes(
            morph_node,
            {
                "mmd_morph_name": str(morph_name),
                "mmd_morph_name_en": str(getattr(morph, "name_english", "")),
                "mmd_morph_type": "bone",
                "mmd_morph_index": int(morph_index),
                "mmd_morph_panel": int(getattr(morph, "panel", 0)),
                "mmd_bone_morph_offset_count": len(offsets),
                "mmd_bone_morph_offsets_json": json.dumps(offsets, ensure_ascii=False, separators=(",", ":")),
                ATTR_MMD_BONE_MORPH_OFFSETS_RAW_JSON: json.dumps(
                    raw_offsets, ensure_ascii=False, separators=(",", ":")
                ),
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
        morph_node = self._create_or_get_morph_network_node(morph_name, "material")

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

        maya_attribute_utils.set_custom_attributes(
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

    def _create_or_get_morph_network_node(self, morph_name: str, morph_kind: str) -> str:
        """Create or reuse a PMX morph network node with a keyable weight attr."""
        node_name = maya_name_utils.sanitize_unique_name(
            f"{morph_name}_{morph_kind}Morph",
            self._morph_node_name_used,
            fallback=f"morph_{morph_kind}",
        )
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
        return morph_node

    @staticmethod
    def _json_float_list(values) -> List[float]:
        """JSON metadata 用に数値列を float list へ正規化する。"""
        return [float(v) for v in values]

    def _convert_vertex_morph_pmx(
        self,
        morph,
        mesh_node: str,
        *,
        morph_index: int = -1,
        template_ctx: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """PMX頂点モーフの変換

        template_ctx を渡すと、メッシュ複製を使い回して高速化する。
        初回呼び出し時に template_ctx へ内部状態を書き込むので、
        呼び出し元は空 dict を渡してループ終了後に cleanup_vertex_morph_template() を呼ぶこと。
        """
        raw_name = self._raw_morph_name(morph)
        morph_name = maya_name_utils.sanitize_text(morph.get_name())

        if template_ctx is not None:
            if "target_mesh" not in template_ctx:
                target_mesh = cmds.duplicate(mesh_node)[0]
                template_name = maya_name_utils.sanitize_unique_name(
                    "_morph_template",
                    self._morph_node_name_used,
                    fallback="morph_template",
                )
                target_mesh = cmds.rename(target_mesh, template_name)
                maya_attribute_utils.set_attribute(target_mesh, "visibility", 0, "bool")
                sel = om.MSelectionList()
                sel.add(target_mesh)
                dag = sel.getDagPath(0)
                mesh_fn = om.MFnMesh(dag)
                template_ctx["target_mesh"] = target_mesh
                template_ctx["dag_path"] = dag
                template_ctx["mesh_fn"] = mesh_fn
                template_ctx["base_points"] = mesh_fn.getPoints(om.MSpace.kObject)
                template_ctx["source_to_local"] = self._get_mesh_source_vertex_map(mesh_node)
                template_ctx["blend_shape_node"] = maya_mesh_utils.find_or_create_blendshape_node(mesh_node)
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
                template_ctx["base_points"], morph, template_ctx["source_to_local"], self.scale,
            )
            template_ctx["mesh_fn"].setPoints(target_points, om.MSpace.kObject)
            self._add_profile_time("target_points_sec", target_points_start)

            target_mesh = cmds.duplicate(template_ctx["target_mesh"])[0]
            target_name = maya_name_utils.sanitize_unique_name(
                f"{morph_name}_target",
                self._morph_node_name_used,
                fallback=f"morph_{morph_index}_target",
            )
            target_mesh = cmds.rename(target_mesh, target_name)
            maya_attribute_utils.set_attribute(target_mesh, "visibility", 0, "bool")
            blend_shape_node = template_ctx["blend_shape_node"]
            target_index = template_ctx["next_target_index"]
            template_ctx["next_target_index"] = target_index + 1
        else:
            target_mesh = cmds.duplicate(mesh_node)[0]
            target_name = maya_name_utils.sanitize_unique_name(
                f"{morph_name}_target",
                self._morph_node_name_used,
                fallback=f"morph_{morph_index}_target",
            )
            target_mesh = cmds.rename(target_mesh, target_name)
            maya_attribute_utils.set_attribute(target_mesh, "visibility", 0, "bool")
            source_to_local = self._get_mesh_source_vertex_map(mesh_node)
            target_points_start = time.perf_counter()
            self._apply_vertex_offsets_pmx(target_mesh, morph, source_to_local=source_to_local)
            self._add_profile_time("target_points_sec", target_points_start)
            blend_shape_node = maya_mesh_utils.find_or_create_blendshape_node(mesh_node)
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
            alias = maya_name_utils.sanitize_unique_name(
                morph_name,
                existing_aliases,
                fallback=f"morph_{morph_index}",
            )
        else:
            existing = self._existing_blendshape_aliases(blend_shape_node)
            alias = maya_name_utils.sanitize_unique_name(
                morph_name,
                existing,
                fallback=f"morph_{morph_index}",
            )
        alias_start = time.perf_counter()
        cmds.aliasAttr(alias, f"{blend_shape_node}.w[{target_index}]")
        self._add_profile_time("alias_sec", alias_start)
        if existing_aliases is not None:
            existing_aliases.add(alias)

        if raw_name and template_ctx is not None and "morph_name_mapping" in template_ctx:
            template_ctx["morph_name_mapping"][str(target_index)] = {
                "name": str(raw_name),
                "index": int(morph_index),
            }
            template_ctx["morph_name_mapping_dirty"] = True
        else:
            morph_name_store_start = time.perf_counter()
            self._store_blendshape_morph_name(
                blend_shape_node, target_index, raw_name, morph_index
            )
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
        scale: float = 1.0,
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
                points[vi] += MorphConverter._pmx_vertex_offset_to_maya_vector(pos, scale)
        return points

    @staticmethod
    def _pmx_vertex_offset_to_maya_vector(position_offset, scale: float = 1.0) -> om.MVector:
        """Return a PMX vertex morph offset converted into Maya mesh space."""
        return om.MVector(
            float(position_offset[0]) * scale,
            float(position_offset[1]) * scale,
            -float(position_offset[2]) * scale,
        )

    @staticmethod
    def cleanup_vertex_morph_template(template_ctx: Dict[str, Any]) -> None:
        """テンプレートメッシュを削除する。"""
        target = template_ctx.get("target_mesh")
        if target and cmds.objExists(target):
            cmds.delete(target)

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
                        points[vertex_index] += self._pmx_vertex_offset_to_maya_vector(offset_pos, self.scale)

        # 変更された頂点位置を設定
        mesh_fn.setPoints(points, om.MSpace.kObject)
