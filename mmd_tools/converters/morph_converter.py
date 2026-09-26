"""
MMDのモーフデータをMayaのblendShapeに変換するモジュール。

このモジュールは、PMD/PMXファイルのモーフデータを解析し、
Mayaのブレンドシェイプシステムに変換する機能を提供します。
"""

import json
import math
import time
from typing import Any, Dict, List, Optional, Set, Union

from maya import cmds
from maya.api import OpenMaya as om

from mmd_tools.core import maya_attribute_utils, maya_mesh_utils, maya_name_utils, settings_keys as setting_keys
from mmd_tools.core.constants import (
    ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON,
    ATTR_MMD_FLIP_MORPH_OFFSETS_JSON,
    ATTR_MMD_IMPULSE_MORPH_OFFSETS_JSON,
    ATTR_MMD_MATERIAL_INDEX,
    ATTR_MMD_SOURCE_VERTEX_INDICES,
    ATTR_MMD_SOURCE_TO_LOCAL_INDICES,
    ATTR_MMD_UV_MORPH_OFFSETS_JSON,
)
from mmd_tools.core.morph_metadata_reader import PMX_MORPH_TYPE_NAMES
from mmd_tools.core.morph_topology import (
    TOPOLOGY_VERSION,
    compute_group_topology,
    inspect_group_topology,
    MorphTopologyError,
    parse_raw_offsets_json,
    serialize_group_topology,
)
from mmd_tools.core.pmx_data.morph import PmxMorphType
from mmd_tools.core.morph_delta_mapping import (
    MorphDeltaMappingError,
    collect_morph_delta,
    map_collected_morph_delta,
    map_morph_deltas_to_local,
)
from mmd_tools.converters.morph_scene_metadata import (
    iter_morph_network_metadata,
    read_blendshape_morph_entry_strings,
)

_OPT_IMPORT_MORPHS = "import_morphs"


def pmx_vertex_offset_to_maya_tuple(position_offset, scale: float = 1.0) -> tuple[float, float, float]:
    """Convert one PMX vertex delta to Maya object-space coordinates."""
    return (
        float(position_offset[0]) * float(scale),
        float(position_offset[1]) * float(scale),
        -float(position_offset[2]) * float(scale),
    )


def _is_group_morph_payload(morph: Dict[str, Any]) -> bool:
    """Return whether a morph payload expands other morph weights.

    PMX Flip morph offsets use the same morph-index/rate contract as Group
    morph offsets.  Keeping both types in this predicate is important when a
    model-root collection restores PMX table order from provenance indices.
    """
    morph_type = morph.get("type", morph.get("morph_type"))
    if isinstance(morph_type, str):
        return morph_type.lower() in {"group", "flip"}
    return morph_type in {
        PmxMorphType.GroupMorph,
        int(PmxMorphType.GroupMorph),
        PmxMorphType.FlipMorph,
        int(PmxMorphType.FlipMorph),
    }


def _order_morphs_by_index_if_grouped(
    morphs: List[Dict[str, Any]],
    *,
    strip_index: bool = False,
    require_contiguous: bool = False,
) -> List[Dict[str, Any]]:
    """Restore PMX morph order when Group/Flip references make indices observable.

    ``index`` is an internal provenance field.  Group and Flip offsets
    reference the PMX morph table, so silently accepting an incomplete or
    duplicated index mapping would produce a structurally valid but
    semantically wrong export.  Network-only payloads may omit vertex morphs
    and therefore use non-contiguous indices; the model-root merge can require
    a complete zero-based map with ``require_contiguous``.
    """
    ordered = list(morphs)
    if require_contiguous or any(_is_group_morph_payload(morph) for morph in ordered):
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
        self.progress_callback = None
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

    def convert_pmx_morphs(
        self, pmx_data, mesh_node: Union[str, List[str]], progress_callback=None,
    ) -> Dict[str, Any]:
        """
        PMXのモーフデータをMayaのブレンドシェイプに変換する。

        Args:
            pmx_data: 解析されたPMXデータオブジェクト
            mesh_node (str or list): ブレンドシェイプを適用するMayaのメッシュノード名、またはそのリスト。
            progress_callback: Optional local percentage callback (0 through 100).

        Returns:
            Dict[str, Any]: 変換結果の辞書
        """
        if progress_callback is None:
            progress_callback = getattr(self, "progress_callback", None)
        if not self.settings.get(_OPT_IMPORT_MORPHS, True):
            return {"success": True, "morphs_converted": 0}

        mesh_nodes = [mesh_node] if isinstance(mesh_node, str) else (mesh_node or [])

        results = []
        blend_shape_nodes = []
        bone_morph_nodes = []
        group_morph_nodes = []
        material_morph_nodes = []
        uv_morph_nodes = []
        flip_impulse_morph_nodes = []
        vertex_morph_nodes = []
        converted_bone_morphs = set()
        converted_group_morphs = set()
        converted_material_morphs = set()
        converted_uv_morphs = set()
        converted_flip_impulse_morphs = set()
        material_vertex_sets = self._build_pmx_material_vertex_sets(pmx_data)
        skipped_vertex_morphs_by_material = 0
        empty_vertex_morphs_converted = set()

        # Keep one semantic metadata node for every PMX vertex morph before
        # touching any mesh.  The blendShape target is the sole authority for
        # vertex offsets; this network node carries only the stable PMX
        # name/index/panel/type binding metadata used by the controller and
        # authoring registry.
        vertex_morph_metadata = []
        vertex_morph_sources = {}
        vertex_morph_deltas = {}
        for morph_index, morph in enumerate(pmx_data.morphs):
            if morph.morph_type != PmxMorphType.VertexMorph:
                continue
            offsets = self._normalize_vertex_morph_offsets(morph, morph_index)
            vertex_morph_metadata.append((morph_index, morph))
            # Include explicit zero/cancelling offsets: they still own targets.
            vertex_morph_sources[morph_index] = {offset["vertex_index"] for offset in offsets}

        for morph_index, morph in vertex_morph_metadata:
            morph_name = self._raw_morph_name(morph)
            morph_node = self._create_or_get_morph_network_node(morph_name, "vertex")
            maya_attribute_utils.set_custom_attributes(
                morph_node,
                {
                    "mmd_morph_name": str(morph_name),
                    "mmd_morph_name_en": str(getattr(morph, "name_english", "")),
                    "mmd_morph_type": "vertex",
                    "mmd_morph_index": int(morph_index),
                    "mmd_morph_panel": int(getattr(morph, "panel", 0)),
                },
            )
            vertex_morph_nodes.append(morph_node)

        total_work = max(1, len(mesh_nodes) * len(pmx_data.morphs))
        last_progress = -1
        for mesh_index, mn in enumerate(mesh_nodes):
            mesh_material_index = self._get_mesh_material_index(mn)
            visible_vertex_indices = (
                material_vertex_sets.get(mesh_material_index)
                if mesh_material_index is not None
                else None
            )
            mesh_ctx = {}
            try:
                for morph_index, morph in enumerate(pmx_data.morphs):
                    progress = 100 * (mesh_index * len(pmx_data.morphs) + morph_index) // total_work
                    if progress_callback is not None and progress != last_progress:
                        last_progress = progress
                        try:
                            progress_callback(progress)
                        except Exception:
                            self.logger.debug("Progress callback failed", exc_info=True)
                    try:
                        if morph.morph_type == PmxMorphType.VertexMorph:
                            is_empty_vertex_morph = not getattr(morph, "offsets", None)
                            if (
                                visible_vertex_indices is not None
                                and is_empty_vertex_morph
                                and morph_index in empty_vertex_morphs_converted
                            ):
                                skipped_vertex_morphs_by_material += 1
                                continue
                            if (
                                visible_vertex_indices is not None
                                and not is_empty_vertex_morph
                                and visible_vertex_indices.isdisjoint(vertex_morph_sources[morph_index])
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
                            if morph_index not in vertex_morph_deltas:
                                # Preserve the mapping path's inferred source bound;
                                # this optimization does not add parser validation.
                                source_count = max(vertex_morph_sources[morph_index], default=-1) + 1
                                vertex_morph_deltas[morph_index] = collect_morph_delta(
                                    morph, morph_index, source_count,
                                )
                            result = self._convert_vertex_morph_pmx(
                                morph, mn, morph_index=morph_index, mesh_ctx=mesh_ctx,
                                source_deltas=vertex_morph_deltas[morph_index],
                            )
                            if result["success"]:
                                results.append(result)
                                if visible_vertex_indices is not None and is_empty_vertex_morph:
                                    empty_vertex_morphs_converted.add(morph_index)
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
                        elif PmxMorphType.UVMorph <= morph.morph_type <= PmxMorphType.AdditionalUVMorph4:
                            if morph_index in converted_uv_morphs:
                                continue
                            self.logger.debug(f"Converting UV morph metadata: {morph.name}")
                            result = self._convert_uv_morph_pmx(morph, morph_index)
                            if result["success"]:
                                converted_uv_morphs.add(morph_index)
                                results.append(result)
                                uv_morph_nodes.append(result["morph_node"])
                                self.logger.debug(f"Successfully imported UV morph metadata: {morph.name}")
                        elif morph.morph_type in (PmxMorphType.FlipMorph, PmxMorphType.ImpulseMorph):
                            if morph_index in converted_flip_impulse_morphs:
                                continue
                            self.logger.debug(f"Converting PMX 2.1 morph metadata: {morph.name}")
                            result = self._convert_flip_impulse_morph_pmx(morph, morph_index)
                            if result["success"]:
                                converted_flip_impulse_morphs.add(morph_index)
                                results.append(result)
                                flip_impulse_morph_nodes.append(result["morph_node"])
                                self.logger.debug(f"Successfully imported PMX 2.1 morph metadata: {morph.name}")
                    except MorphDeltaMappingError:
                        raise
                    except Exception as e:
                        self.logger.warning(f"Failed to convert morph {morph.name}: {e}")
            finally:
                self._flush_vertex_morph_name_mapping(mesh_ctx)

        return {
            "success": True,
            "morphs_converted": len(results),
            "total_morphs": len(pmx_data.morphs),
            "blend_shape_nodes": blend_shape_nodes,
            "bone_morph_nodes": bone_morph_nodes,
            "group_morph_nodes": group_morph_nodes,
            "material_morph_nodes": material_morph_nodes,
            "uv_morph_nodes": uv_morph_nodes,
            "flip_impulse_morph_nodes": flip_impulse_morph_nodes,
            "vertex_morph_nodes": vertex_morph_nodes,
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

        topology_rows = []
        for index, morph in enumerate(pmx_data.morphs):
            morph_type = {
                PmxMorphType.GroupMorph: "group",
                PmxMorphType.FlipMorph: "flip",
            }.get(morph.morph_type, "leaf")
            topology_rows.append({
                "index": index,
                "morph_type": morph_type,
                "offsets": tuple(getattr(morph, "offsets", ())),
            })
        topology = compute_group_topology(topology_rows)
        cmds.setAttr(f"{controller}.topologyVersion", TOPOLOGY_VERSION, lock=True)
        cmds.setAttr(
            f"{controller}.groupTopology",
            serialize_group_topology(topology),
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
        for morph_node in (
            morph_result.get("bone_morph_nodes", [])
            + morph_result.get("material_morph_nodes", [])
            + morph_result.get("uv_morph_nodes", [])
            + morph_result.get("flip_impulse_morph_nodes", [])
        ):
            index = int(cmds.getAttr(f"{morph_node}.mmd_morph_index"))
            destinations.setdefault(index, set()).add(f"{morph_node}.weight")
        for leaf_index, leaf_destinations in sorted(destinations.items()):
            for destination in sorted(leaf_destinations):
                cmds.connectAttr(f"{controller}.outputWeight[{leaf_index}]", destination, force=True)
        morph_result["morph_controller"] = controller
        return controller

    def _convert_group_morph_pmx(self, morph, morph_index: int = 0) -> Dict[str, Any]:
        """PMXグループモーフをMayaのnetwork nodeとしてインポートする。"""
        morph_name = self._raw_morph_name(morph)
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
        """Return original PMX vertex index to local mesh vertex index mapping.

        New meshes store a source-sized array so several PMX sources can point
        to one Maya vertex and material-split meshes can mark absent sources
        with ``-1``. Older scenes retain the local-to-source compatibility
        array and are still readable.
        """
        if not cmds.objExists(mesh_node):
            return None
        if cmds.attributeQuery(ATTR_MMD_SOURCE_TO_LOCAL_INDICES, node=mesh_node, exists=True):
            try:
                source_to_local = maya_attribute_utils.get_int_array_attribute(
                    mesh_node,
                    ATTR_MMD_SOURCE_TO_LOCAL_INDICES,
                )
                local_count = int(cmds.polyEvaluate(mesh_node, vertex=True))
            except Exception as exc:
                raise MorphDeltaMappingError(
                    f"failed to read source-to-local mapping: {exc}"
                ) from exc
            try:
                valid = (
                    bool(source_to_local)
                    and local_count >= 0
                    and all(-1 <= local_index < local_count for local_index in source_to_local)
                    and {local_index for local_index in source_to_local if local_index >= 0}
                    == set(range(local_count))
                )
            except (TypeError, ValueError):
                valid = False
            if not valid:
                raise MorphDeltaMappingError("source-to-local mapping is invalid")
            return {
                source_index: int(local_index)
                for source_index, local_index in enumerate(source_to_local)
                if int(local_index) >= 0
            }
        try:
            if not cmds.attributeQuery(ATTR_MMD_SOURCE_VERTEX_INDICES, node=mesh_node, exists=True):
                return None
            source_indices = maya_attribute_utils.get_int_array_attribute(mesh_node, ATTR_MMD_SOURCE_VERTEX_INDICES)
            if not source_indices:
                return None
            return {source_index: local_index for local_index, source_index in enumerate(source_indices)}
        except Exception:
            return None

    @staticmethod
    def _normalize_vertex_morph_offsets(morph, morph_index: int) -> List[Dict[str, Any]]:
        """Validate and normalize PMX vertex offsets for semantic metadata.

        The PMX offsets are kept in source space.  Unlike the blendShape path,
        this metadata path must not coerce malformed values: booleans, missing
        fields, non-finite numbers, and malformed vectors are rejected so the
        importer cannot silently publish a lossy semantic record.
        """
        raw_offsets = getattr(morph, "offsets", None)
        if not isinstance(raw_offsets, (list, tuple)):
            raise ValueError(f"Vertex morph {morph_index} offsets must be a list")

        offsets: List[Dict[str, Any]] = []
        for offset_index, offset in enumerate(raw_offsets):
            valid = (
                isinstance(offset, dict)
                and set(offset) == {"vertex_index", "position_offset"}
                and isinstance(offset.get("vertex_index"), int)
                and not isinstance(offset.get("vertex_index"), bool)
                and offset["vertex_index"] >= 0
                and isinstance(offset.get("position_offset"), (list, tuple))
                and len(offset["position_offset"]) == 3
                and all(
                    isinstance(component, (int, float))
                    and not isinstance(component, bool)
                    and math.isfinite(float(component))
                    for component in offset["position_offset"]
                )
            )
            if not valid:
                raise ValueError(f"Vertex morph {morph_index} offset {offset_index} is invalid")
            normalized_position = [float(component) for component in offset["position_offset"]]

            offsets.append(
                {
                    "vertex_index": offset["vertex_index"],
                    "position_offset": normalized_position,
                }
            )
        return offsets

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
            "uv": ATTR_MMD_UV_MORPH_OFFSETS_JSON,
            "additional_uv1": ATTR_MMD_UV_MORPH_OFFSETS_JSON,
            "additional_uv2": ATTR_MMD_UV_MORPH_OFFSETS_JSON,
            "additional_uv3": ATTR_MMD_UV_MORPH_OFFSETS_JSON,
            "additional_uv4": ATTR_MMD_UV_MORPH_OFFSETS_JSON,
            "flip": ATTR_MMD_FLIP_MORPH_OFFSETS_JSON,
            "impulse": ATTR_MMD_IMPULSE_MORPH_OFFSETS_JSON,
        }

        for metadata in iter_morph_network_metadata(
            root_group=root_group,
            morph_types={
                "bone",
                "group",
                "material",
                "uv",
                "additional_uv1",
                "additional_uv2",
                "additional_uv3",
                "additional_uv4",
                "flip",
                "impulse",
            },
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
                    if metadata.morph_type in {"group", "flip"}:
                        raise ValueError(f"missing {offsets_attr} attribute")
                    self.logger.warning(
                        f"skip morph node {morph_node}: missing {offsets_attr} attribute"
                    )
                    continue

                try:
                    offsets_json = cmds.getAttr(f"{morph_node}.{offsets_attr}")
                    offsets = (
                        parse_raw_offsets_json(offsets_json)
                        if metadata.morph_type in {"group", "flip"}
                        else json.loads(offsets_json) if offsets_json else []
                    )
                except (TypeError, json.JSONDecodeError, MorphTopologyError) as e:
                    if metadata.morph_type in {"group", "flip"}:
                        raise ValueError(f"invalid JSON in {offsets_attr}: {e}") from e
                    self.logger.warning(f"skip morph node {morph_node}: invalid JSON in {offsets_attr}: {e}")
                    continue

                if not isinstance(offsets, list):
                    if metadata.morph_type in {"group", "flip"}:
                        raise ValueError(
                            f"{offsets_attr} data must be list, got {type(offsets).__name__}"
                        )
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
                if metadata.morph_type in {"group", "flip"}:
                    raise ValueError(
                        f"morph_topology:malformed:{morph_node}:{e}"
                    ) from e
                self.logger.warning(f"skip morph node {morph_node}: {e}")

        return _order_morphs_by_index_if_grouped(
            morphs,
            require_contiguous=require_contiguous,
        )

    @staticmethod
    def validate_controller_topology_for_export(
        root_group: str, morphs: List[Dict[str, Any]]
    ) -> None:
        """Fail export closed when the derived controller cache is invalid."""
        if not cmds.attributeQuery("mmd_morph_controller", node=root_group, exists=True):
            return
        controllers = cmds.listConnections(
            f"{root_group}.mmd_morph_controller", source=True, destination=False
        ) or []
        if len(controllers) != 1:
            raise ValueError("morph_topology:malformed:controller ownership is ambiguous")
        controller = controllers[0]
        inspection = inspect_group_topology(
            morphs,
            cmds.getAttr(f"{controller}.topologyVersion"),
            cmds.getAttr(f"{controller}.groupTopology"),
        )
        if inspection.diagnostics:
            diagnostic = inspection.diagnostics[0]
            raise ValueError(f"morph_topology:{diagnostic.code}:{diagnostic.detail}")

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

    def _flush_vertex_morph_name_mapping(self, mesh_ctx: Dict[str, Any]) -> None:
        """vertex morph ループで蓄積した morph name mapping を一括保存する。"""
        blend_shape_node = mesh_ctx.get("blend_shape_node")
        names = mesh_ctx.get("morph_name_mapping")
        if not mesh_ctx.get("morph_name_mapping_dirty") or not blend_shape_node or not names:
            return
        start = time.perf_counter()
        maya_attribute_utils.write_json_attr(blend_shape_node, ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON, names)
        self._add_profile_time("morph_name_store_sec", start)
        mesh_ctx["morph_name_mapping_dirty"] = False

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
        morph_name = self._raw_morph_name(morph)
        morph_node = self._create_or_get_morph_network_node(morph_name, "bone")

        offsets = []
        for offset in getattr(morph, "offsets", []):
            if "bone_index" not in offset:
                continue
            effective_offset = {
                "bone_index": int(offset["bone_index"]),
                "translation": [
                    float(v) * self.scale
                    for v in offset.get("translation", (0.0, 0.0, 0.0))
                ],
                "rotation": [float(v) for v in offset.get("rotation", (0.0, 0.0, 0.0, 1.0))],
            }
            offsets.append(effective_offset)

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
        morph_name = self._raw_morph_name(morph)
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

    def _convert_uv_morph_pmx(self, morph, morph_index: int = 0) -> Dict[str, Any]:
        """Import PMX UV morphs as raw semantic metadata on a network node.

        Maya does not evaluate these offsets in this slice.  The source vertex
        index and all four offset components are kept in PMX space so the
        exporter can write them back without inventing a UV animation path.
        """
        morph_type_value = int(morph.morph_type)
        morph_type = PMX_MORPH_TYPE_NAMES.get(morph_type_value)
        if morph_type_value < int(PmxMorphType.UVMorph) or morph_type_value > int(PmxMorphType.AdditionalUVMorph4):
            raise ValueError(f"unsupported UV morph type: {morph_type_value}")
        if morph_type is None:
            raise ValueError(f"unknown UV morph type: {morph_type_value}")

        morph_name = self._raw_morph_name(morph)
        offsets = []
        for offset_index, offset in enumerate(getattr(morph, "offsets", []) or []):
            if not isinstance(offset, dict):
                raise ValueError(f"UV morph offset {offset_index} must be a mapping")
            if "vertex_index" not in offset:
                raise ValueError(f"UV morph offset {offset_index} is missing vertex_index")
            if "uv_offset" not in offset:
                raise ValueError(f"UV morph offset {offset_index} is missing uv_offset")
            vertex_index = offset["vertex_index"]
            if isinstance(vertex_index, bool) or not isinstance(vertex_index, int) or vertex_index < 0:
                raise ValueError(f"UV morph offset {offset_index} vertex_index must be a non-negative integer")
            uv_offset = offset["uv_offset"]
            if not isinstance(uv_offset, (list, tuple)) or len(uv_offset) != 4:
                raise ValueError(f"UV morph offset {offset_index} uv_offset must contain exactly four values")
            normalized_offset = []
            for component in uv_offset:
                if isinstance(component, bool) or not isinstance(component, (int, float)):
                    raise ValueError(f"UV morph offset {offset_index} uv_offset must contain real numbers")
                component = float(component)
                if not math.isfinite(component):
                    raise ValueError(f"UV morph offset {offset_index} uv_offset must contain finite numbers")
                normalized_offset.append(component)
            offsets.append({"vertex_index": vertex_index, "uv_offset": normalized_offset})

        morph_node = self._create_or_get_morph_network_node(morph_name, morph_type)
        maya_attribute_utils.set_custom_attributes(
            morph_node,
            {
                "mmd_morph_name": str(morph_name),
                "mmd_morph_name_en": str(getattr(morph, "name_english", "")),
                "mmd_morph_type": morph_type,
                "mmd_morph_index": int(morph_index),
                "mmd_morph_panel": int(getattr(morph, "panel", 0)),
                "mmd_uv_morph_offset_count": len(offsets),
                ATTR_MMD_UV_MORPH_OFFSETS_JSON: json.dumps(
                    offsets,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
        )
        return {
            "success": True,
            "morph_name": morph_name,
            "morph_node": morph_node,
            "morph_type": morph_type,
            "offset_count": len(offsets),
        }

    def _convert_flip_impulse_morph_pmx(self, morph, morph_index: int = 0) -> Dict[str, Any]:
        """Import PMX 2.1 Flip/Impulse offsets as raw network metadata.

        Maya does not evaluate these PMX 2.1 effects in this slice. Their
        references and vectors remain in PMX space so the exporter can write
        them back without silently converting them to an unrelated morph.
        """
        morph_type_value = int(morph.morph_type)
        if morph_type_value == int(PmxMorphType.FlipMorph):
            morph_type = "flip"
            offsets_attr = ATTR_MMD_FLIP_MORPH_OFFSETS_JSON
        elif morph_type_value == int(PmxMorphType.ImpulseMorph):
            morph_type = "impulse"
            offsets_attr = ATTR_MMD_IMPULSE_MORPH_OFFSETS_JSON
        else:
            raise ValueError(f"unsupported PMX 2.1 morph type: {morph_type_value}")

        offsets = []
        for offset_index, offset in enumerate(getattr(morph, "offsets", []) or []):
            if not isinstance(offset, dict):
                raise ValueError(f"{morph_type} morph offset {offset_index} must be a mapping")
            if morph_type == "flip":
                reference_key = "morph_index"
                vector_keys = ()
                scalar_key = "flip_rate"
            else:
                reference_key = "rigid_body_index"
                vector_keys = ("impulse", "torque")
                scalar_key = None

            reference = offset.get(reference_key)
            if isinstance(reference, bool) or not isinstance(reference, int) or reference < 0:
                raise ValueError(
                    f"{morph_type} morph offset {offset_index} {reference_key} must be a non-negative integer"
                )
            normalized = {reference_key: reference}
            if morph_type == "impulse" and "is_local" in offset:
                is_local = offset["is_local"]
                if isinstance(is_local, bool) or not isinstance(is_local, int) or is_local not in (0, 1):
                    raise ValueError(
                        f"{morph_type} morph offset {offset_index} is_local must be 0 or 1"
                    )
                normalized["is_local"] = is_local
            if scalar_key is not None:
                scalar = offset.get(scalar_key)
                if isinstance(scalar, bool) or not isinstance(scalar, (int, float)):
                    raise ValueError(f"{morph_type} morph offset {offset_index} {scalar_key} must be a real number")
                scalar = float(scalar)
                if not math.isfinite(scalar):
                    raise ValueError(f"{morph_type} morph offset {offset_index} {scalar_key} must be finite")
                normalized[scalar_key] = scalar
            for vector_key in vector_keys:
                vector = offset.get(vector_key)
                if not isinstance(vector, (list, tuple)) or len(vector) != 3:
                    raise ValueError(
                        f"{morph_type} morph offset {offset_index} {vector_key} must contain exactly three values"
                    )
                normalized_vector = []
                for component in vector:
                    if isinstance(component, bool) or not isinstance(component, (int, float)):
                        raise ValueError(
                            f"{morph_type} morph offset {offset_index} {vector_key} must contain real numbers"
                        )
                    component = float(component)
                    if not math.isfinite(component):
                        raise ValueError(
                            f"{morph_type} morph offset {offset_index} {vector_key} must contain finite numbers"
                        )
                    normalized_vector.append(component)
                normalized[vector_key] = normalized_vector
            offsets.append(normalized)

        morph_name = self._raw_morph_name(morph)
        morph_node = self._create_or_get_morph_network_node(morph_name, morph_type)
        maya_attribute_utils.set_custom_attributes(
            morph_node,
            {
                "mmd_morph_name": str(morph_name),
                "mmd_morph_name_en": str(getattr(morph, "name_english", "")),
                "mmd_morph_type": morph_type,
                "mmd_morph_index": int(morph_index),
                "mmd_morph_panel": int(getattr(morph, "panel", 0)),
                f"mmd_{morph_type}_morph_offset_count": len(offsets),
                offsets_attr: json.dumps(offsets, ensure_ascii=False, separators=(",", ":")),
            },
        )
        return {
            "success": True,
            "morph_name": morph_name,
            "morph_node": morph_node,
            "morph_type": morph_type,
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
        mesh_ctx: Optional[Dict[str, Any]] = None,
        source_deltas: Optional[Dict[int, tuple]] = None,
    ) -> Dict[str, Any]:
        """Create sparse PMX targets; the optional context caches per-mesh state."""
        raw_name = self._raw_morph_name(morph)
        morph_name = maya_name_utils.sanitize_text(morph.get_name())
        context = mesh_ctx if mesh_ctx is not None else {}
        if "local_count" not in context:
            selection = om.MSelectionList()
            selection.add(maya_mesh_utils.resolve_mesh_shape(mesh_node))
            dag = selection.getDagPath(0)
            context["local_count"] = om.MFnMesh(dag).numVertices
            context["source_to_local"] = self._get_mesh_source_vertex_map(mesh_node)
            blend_shape_node = maya_mesh_utils.find_or_create_blendshape_node(mesh_node)
            geometries = cmds.blendShape(blend_shape_node, query=True, geometry=True) or []
            geometry_indices = cmds.blendShape(blend_shape_node, query=True, geometryIndices=True) or []
            context["geometry_index"] = next(
                index for geometry, index in zip(geometries, geometry_indices)
                if dag.fullPathName() in (cmds.ls(geometry, long=True) or [])
            )
            context["blend_shape_node"] = blend_shape_node
            context["next_target_index"] = max(cmds.getAttr(f"{blend_shape_node}.weight", multiIndices=True) or [], default=-1) + 1
            context["existing_aliases"] = self._existing_blendshape_aliases(blend_shape_node)
            context["morph_name_mapping"] = self._load_blendshape_morph_names(blend_shape_node)
            context["morph_name_mapping_dirty"] = False

        target_points_start = time.perf_counter()
        if source_deltas is None:
            mapped = self._mapped_vertex_morph_deltas(
                morph, morph_index, context["source_to_local"], context["local_count"],
            )
        else:
            mapped = map_collected_morph_delta(
                source_deltas, morph_index, context["source_to_local"], context["local_count"],
            )
        components = []
        points = []
        for index in sorted(mapped):
            # Preserve the authored PMX delta directly, as the morph editor does.
            # Avoid mesh/tweak round-trips and their cumulative float32 rounding.
            delta = pmx_vertex_offset_to_maya_tuple(mapped[index], self.scale)
            if any(delta):
                components.append(f"vtx[{index}]")
                points.append((*delta, 1.0))
        self._add_profile_time("target_points_sec", target_points_start)

        blend_shape_node = context["blend_shape_node"]
        target_index = context["next_target_index"]
        item = (
            f"{blend_shape_node}.inputTarget[{context['geometry_index']}]"
            f".inputTargetGroup[{target_index}].inputTargetItem[6000]"
        )
        blendshape_add_start = time.perf_counter()
        if not points:
            # Register an editable empty target without duplicating the mesh.
            cmds.blendShape(blend_shape_node, edit=True, target=(mesh_node, target_index, mesh_node, 1.0))
            geometry_plug = f"{item}.inputGeomTarget"
            sources = cmds.listConnections(geometry_plug, source=True, destination=False, plugs=True) or []
            if len(sources) != 1:
                raise RuntimeError(f"Empty vertex target {item!r} has no unique geometry source")
            cmds.disconnectAttr(sources[0], geometry_plug)
            weight = f"{blend_shape_node}.weight[{target_index}]"
            if cmds.aliasAttr(weight, query=True):
                cmds.aliasAttr(weight, remove=True)
        maya_mesh_utils.write_vertex_target_deltas(item, components, points)
        cmds.setAttr(f"{blend_shape_node}.weight[{target_index}]", 0.0)
        context["next_target_index"] = target_index + 1
        self._add_profile_time("blendshape_add_sec", blendshape_add_start)

        existing_aliases = context["existing_aliases"]
        alias = maya_name_utils.sanitize_unique_name(
            morph_name, existing_aliases, fallback=f"morph_{morph_index}",
        )
        alias_start = time.perf_counter()
        cmds.aliasAttr(alias, f"{blend_shape_node}.w[{target_index}]")
        self._add_profile_time("alias_sec", alias_start)
        existing_aliases.add(alias)

        if raw_name and mesh_ctx is not None and "morph_name_mapping" in mesh_ctx:
            mesh_ctx["morph_name_mapping"][str(target_index)] = {
                "name": str(raw_name),
                "index": int(morph_index),
            }
            mesh_ctx["morph_name_mapping_dirty"] = True
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
    def _mapped_vertex_morph_deltas(
        morph,
        morph_index: int,
        source_to_local: Optional[Dict[int, int]],
        local_count: int,
    ) -> Dict[int, tuple]:
        """Map sparse PMX deltas once per Maya vertex.

        A UV-seam weld may map several PMX sources to one local vertex. Those
        sources are safe only when this morph's accumulated deltas are exactly
        equal; applying both would double the deformation, while choosing one
        would hide a malformed conflict.
        """
        return map_morph_deltas_to_local(
            morph,
            morph_index,
            source_to_local,
            local_count,
        )
