"""
Fast mesh import path using the compiled C++ mmdFastLoad command.

This module is an explicit opt-in: when enabled, :func:`fast_import`
attempts to load the compiled C++ plugin (mmd_tools_cpp.mll / .bundle / .so)
and call ``cmds.mmdFastLoad(f=filepath, n=base_name, s=scale)`` for *pmx*
files.  If the plugin is unavailable or the command fails, ``None`` is
returned and a clear fallback reason is logged ― callers should fall
through to the full Python importer.

Candidate plugin paths follow the same layout as
``tests/cpp/smoke_runtime_node.py``.
"""

from __future__ import annotations

import os
import sys
import json
from pathlib import Path
from typing import Optional

from mmd_tools.core.constants import (
    ATTR_MMD_BONE_INDEX,
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_BONE_NAME_EN,
    ATTR_MMD_MATERIAL_NAME,
    ATTR_MMD_MATERIAL_NAME_EN,
    ATTR_MMD_MODEL_NAME,
    ATTR_MMD_MODEL_NAME_EN,
    ATTR_MMD_COMMENT,
    ATTR_MMD_COMMENT_EN,
    ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON,
)
from mmd_tools.core import maya_mesh_utils, maya_name_utils
from mmd_tools.core.logger import get_logger
from mmd_tools.core.native.native_pmx_parser import parse_pmx_native

logger = get_logger(__name__)

# Kept as a module attribute so tests can patch it without importing native code.
MmdParsedModel = None


class _FastSkinData:
    """Parsed bone and skin data needed by the fast skeleton/skin path."""

    def __init__(
        self,
        bones: list[dict],
        skin_indices: list[tuple[int, int, int, int]],
        skin_weights: list[tuple[float, float, float, float]],
    ):
        self.bones = bones
        self.skin_indices = skin_indices
        self.skin_weights = skin_weights

# ---------------------------------------------------------------------------
# Candidate discovery  (mirrors tests/cpp/smoke_runtime_node.py)
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[2]  # project root

_PLUGIN_EXTENSIONS = [".mll", ".bundle", ".so"]


def _mmd_parsed_model_class():
    """Resolve the native parsed-model wrapper only when the fast path needs it."""
    global MmdParsedModel
    if MmdParsedModel is None:
        from mmd_tools.core.native import MmdParsedModel as _MmdParsedModel

        MmdParsedModel = _MmdParsedModel
    return MmdParsedModel


def _candidate_plugin_paths() -> list[Path]:
    """Return candidate paths for the compiled C++ plugin artifact.

    The ``MMD_TOOLS_CPP_PLUGIN`` environment variable, if set, takes
    precedence and is returned as the sole candidate.
    """
    explicit = os.environ.get("MMD_TOOLS_CPP_PLUGIN")
    if explicit:
        return [Path(explicit)]

    version = os.environ.get("MAYA_VERSION", "2024")
    config = os.environ.get("MMD_TOOLS_CPP_CONFIG", "Debug")
    configs = [config]
    if config != "Release":
        configs.append("Release")
    if config != "Debug":
        configs.append("Debug")

    paths: list[Path] = []
    for cfg in configs:
        for suffix in _PLUGIN_EXTENSIONS:
            paths.append(ROOT / "plug-ins" / version / cfg / f"mmd_tools_cpp{suffix}")
    return paths


# ---------------------------------------------------------------------------
# Plugin loading helpers
# ---------------------------------------------------------------------------


def _setup_plugin_directory(plugin_dir: Path) -> None:
    """Add *plugin_dir* to ``PATH`` (and ``add_dll_directory`` on Windows)."""
    env_path = os.environ.get("PATH", "")
    str_dir = str(plugin_dir)
    if str_dir not in env_path:
        os.environ["PATH"] = str_dir + os.pathsep + env_path

    if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(str_dir)
        except OSError:
            pass  # already added or not applicable


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fast_import(
    filepath: str,
    base_name: str = "mmd_fast_model",
    scale: float = 1.0,
    mesh_only: bool = True,
    include_morphs: bool = True,
) -> Optional[str]:
    """Attempt fast PMX import via the compiled C++ ``mmdFastLoad`` command.

    Parameters
    ----------
    filepath:
        Path to the ``.pmx`` file to load.
    base_name:
        Base name passed as ``n=`` to ``mmdFastLoad``.
    scale:
        Scale factor passed as ``s=`` to ``mmdFastLoad``.
    mesh_only:
        If True (default), only mesh geometry is imported.
        If False, a basic Maya skeleton (joints) and skinCluster are
        also created from the mmd-anim parsed metadata.
    include_morphs:
        If True, asks the C++ command to create PMX vertex morph
        blendShape targets. Non-vertex morph types are not created by the
        fast path.

    Returns
    -------
    The transform (root group) node name on success, or ``None`` on failure.
    """
    # --- locate plugin ----------------------------------------------------
    plugin_path: Optional[Path] = None
    for p in _candidate_plugin_paths():
        if p.exists():
            plugin_path = p
            break

    if plugin_path is None:
        candidates = "\n".join(str(p) for p in _candidate_plugin_paths())
        logger.debug(
            "C++ plugin not found – falling back to Python importer. "
            "Checked paths:\n%s",
            candidates,
        )
        return None

    # --- add plugin directory to library search paths ---------------------
    _setup_plugin_directory(plugin_path.parent)

    # --- import Maya commands ---------------------------------------------
    try:
        import maya.cmds as cmds
    except ImportError:
        logger.debug("maya.cmds not available – falling back to Python importer.")
        return None

    # --- load plugin (idempotent) -----------------------------------------
    try:
        cmds.loadPlugin(str(plugin_path), quiet=True)
    except RuntimeError as exc:
        logger.debug("Failed to load C++ plugin: %s – falling back.", exc)
        return None

    # --- verify the command exists ----------------------------------------
    if not hasattr(cmds, "mmdFastLoad"):
        logger.debug(
            "cmds.mmdFastLoad not found after plugin load – falling back."
        )
        return None

    # --- run fast load ----------------------------------------------------
    try:
        result = cmds.mmdFastLoad(f=filepath, n=base_name, s=scale, mo=include_morphs)
    except RuntimeError as exc:
        logger.debug("mmdFastLoad failed: %s – falling back to Python importer.", exc)
        return None

    if not result or len(result) < 1:
        logger.debug(
            "mmdFastLoad returned empty result – falling back to Python importer."
        )
        return None

    if not isinstance(result, (list, tuple)):
        logger.debug(
            "mmdFastLoad returned unexpected type %s – falling back.",
            type(result).__name__,
        )
        return None

    # result is [transform, mesh]  (smoke_runtime_node.py convention)
    transform_node = str(result[0])
    mesh_node = str(result[1]) if len(result) >= 2 else None

    metadata = _apply_basic_materials(filepath, mesh_node, cmds) if mesh_node else None
    _apply_fast_root_metadata(filepath, transform_node, metadata, cmds)
    if include_morphs and mesh_node:
        _apply_fast_morph_metadata(filepath, mesh_node, cmds)

    if not mesh_only and mesh_node:
        # attempt skeleton + skin; any failure falls back to mesh-only result
        try:
            _apply_fast_skeleton_skin(filepath, mesh_node, transform_node, base_name, cmds)
        except Exception as exc:
            logger.debug("Fast skeleton/skin failed (%s); returning mesh root only", exc)

    logger.debug("Fast import succeeded: transform node = %s", transform_node)
    return transform_node


def _apply_basic_materials(filepath: str, mesh_node: str, cmds_module) -> Optional[dict]:
    """Assign materials and return the parsed metadata for root attributes."""
    try:
        pmx_bytes = Path(filepath).read_bytes()
        parsed_model_cls = _mmd_parsed_model_class()
        parsed = parsed_model_cls.from_pmx_bytes(pmx_bytes)
        if parsed is None:
            logger.debug("Native parsed-model metadata unavailable; skipping fast material assignment")
            return None
        try:
            metadata_text = parsed.metadata_json
            material_groups = parsed.material_groups or []
        finally:
            parsed.free()

        if not metadata_text or not material_groups:
            logger.debug("No parsed material metadata/groups; skipping fast material assignment")
            return json.loads(metadata_text) if metadata_text else None

        metadata = json.loads(metadata_text)
        materials = metadata.get("materials") or []
        used_names = _scene_name_set(cmds_module)
        for start_index, index_count, material_index in material_groups:
            if material_index >= len(materials) or index_count <= 0:
                continue

            material = materials[material_index]
            shader = _create_standard_material(material, material_index, cmds_module, used_names)
            if not shader:
                continue

            face_start = int(start_index) // 3
            face_end = (int(start_index) + int(index_count)) // 3 - 1
            if face_end < face_start:
                continue

            cmds_module.sets(f"{mesh_node}.f[{face_start}:{face_end}]", edit=True, forceElement=f"{shader}SG")
        return metadata
    except Exception as exc:
        logger.debug("Fast material assignment skipped: %s", exc)
        return None


def _apply_fast_morph_metadata(filepath: str, mesh_node: str, cmds_module) -> None:
    """Replace C++ vertex-morph aliases and persist their raw PMX mapping.

    ``mmdFastLoad`` intentionally only has the C++ byte-level sanitizer.  The
    Python fast wrapper is the common naming boundary, so it can apply the
    shared Unicode dictionary and retain the original PMX names used by VMD
    and export paths.  This helper is deliberately transactional: all source
    metadata, target ordering, aliases, and JSON are validated before the
    first Maya mutation.  If a Maya mutation fails, already-applied aliases
    and the metadata attribute are restored on a best-effort basis.
    """
    try:
        source = _load_fast_morph_source(filepath)
        if source is None:
            return

        blend_shapes = []
        for history_node in cmds_module.listHistory(mesh_node, pruneDagObjects=True) or []:
            if cmds_module.nodeType(history_node) == "blendShape":
                if history_node not in blend_shapes:
                    blend_shapes.append(history_node)
        if not blend_shapes:
            return
        if len(blend_shapes) != 1:
            logger.debug("Fast morph metadata skipped: expected one blendShape, got %d", len(blend_shapes))
            return

        blend_shape = blend_shapes[0]
        weight_count = int(cmds_module.blendShape(blend_shape, query=True, weightCount=True) or 0)
        if weight_count == 0:
            return

        candidates = _fast_vertex_morph_candidates(source)
        if candidates is None or len(candidates) != weight_count:
            logger.debug(
                "Fast morph metadata skipped: C++ target count %d does not match parsed candidates %s",
                weight_count,
                None if candidates is None else len(candidates),
            )
            return

        alias_plan = []
        used_names = set()
        raw_mapping = {}
        for weight_index, candidate in enumerate(candidates):
            raw_name = str(candidate.get("name") or "")
            alias = maya_name_utils.sanitize_unique_name(
                raw_name,
                used_names,
                fallback=f"morph_{weight_index}",
            )
            plug = f"{blend_shape}.weight[{weight_index}]"
            old_alias = cmds_module.aliasAttr(plug, query=True) or None
            alias_plan.append((plug, old_alias, alias))
            if raw_name:
                raw_mapping[str(weight_index)] = {
                    "name": raw_name,
                    "index": int(candidate["index"]),
                }

        serialized_mapping = (
            json.dumps(
                raw_mapping,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if raw_mapping
            else None
        )
        _commit_fast_morph_aliases(
            cmds_module,
            blend_shape,
            alias_plan,
            serialized_mapping,
        )
    except Exception as exc:
        logger.debug("Fast morph metadata skipped: %s", exc)


def _load_fast_morph_source(filepath: str) -> Optional[dict]:
    """Read parsed morph metadata and optional runtime vertex-morph spans."""
    parsed = None
    try:
        pmx_bytes = Path(filepath).read_bytes()
        parsed_model_cls = _mmd_parsed_model_class()
        parsed = parsed_model_cls.from_pmx_bytes(pmx_bytes)
    except Exception as exc:
        logger.debug("Parsed-model morph metadata unavailable: %s", exc)
    else:
        if parsed is not None:
            try:
                try:
                    metadata_text = parsed.metadata_json
                    vertex_count = int(getattr(parsed, "vertex_count", 0) or 0)
                    spans = getattr(parsed, "vertex_morph_spans", None)
                    names = getattr(parsed, "vertex_morph_names", None)
                finally:
                    parsed.free()

                if metadata_text and vertex_count > 0:
                    metadata = json.loads(metadata_text)
                    morphs = metadata.get("morphs") if isinstance(metadata, dict) else None
                    if isinstance(morphs, list):
                        return {
                            "morphs": morphs,
                            "vertex_count": vertex_count,
                            "spans": list(spans) if isinstance(spans, (list, tuple)) else None,
                            "names": list(names) if isinstance(names, (list, tuple)) else None,
                        }
            except Exception as exc:
                logger.debug("Parsed-model morph metadata extraction skipped: %s", exc)
        else:
            logger.debug("Parsed-model morph metadata unavailable; trying native PMX fallback")

    return _load_fast_morph_source_native(filepath)


def _load_fast_morph_source_native(filepath: str) -> Optional[dict]:
    """Convert the native PMX object into the fast morph source schema."""
    try:
        pmx = parse_pmx_native(filepath)
        if pmx is None:
            return None
        vertices = getattr(pmx, "vertices", None)
        morphs = getattr(pmx, "morphs", None)
        if isinstance(pmx, dict):
            vertices = pmx.get("vertices", vertices)
            morphs = pmx.get("morphs", morphs)
        if not isinstance(vertices, (list, tuple)) or not isinstance(morphs, (list, tuple)):
            return None

        converted_morphs = []
        for morph in morphs:
            morph_type = _fast_morph_field(morph, "morph_type", _fast_morph_field(morph, "type", ""))
            is_vertex = morph_type == "vertex"
            try:
                is_vertex = is_vertex or int(morph_type) == 1
            except (TypeError, ValueError):
                pass

            offsets = _fast_morph_field(morph, "offsets", None)
            if offsets is None:
                offsets = _fast_morph_field(morph, "vertexOffsets", [])
            vertex_offsets = []
            if is_vertex and isinstance(offsets, (list, tuple)):
                for offset in offsets:
                    vertex_index = _fast_morph_field(offset, "vertex_index", None)
                    if vertex_index is None:
                        vertex_index = _fast_morph_field(offset, "vertexIndex", None)
                    vertex_offsets.append({"vertexIndex": vertex_index})

            converted_morphs.append({
                "name": str(_fast_morph_field(morph, "name", "") or ""),
                "type": "vertex" if is_vertex else "other",
                "vertexOffsets": vertex_offsets,
            })

        return {
            "morphs": converted_morphs,
            "vertex_count": len(vertices),
            "spans": None,
            "names": None,
        }
    except Exception as exc:
        logger.debug("Native PMX morph metadata fallback skipped: %s", exc)
        return None


def _fast_morph_field(value, field: str, default=None):
    """Read a PMX morph field from either an object or a mapping."""
    if isinstance(value, dict):
        return value.get(field, default)
    return getattr(value, field, default)


def _fast_vertex_morph_candidates(source: dict) -> Optional[list[dict]]:
    """Mirror C++'s created-target filtering in PMX/global morph order."""
    morphs = source.get("morphs")
    vertex_count = int(source.get("vertex_count", 0) or 0)
    if not isinstance(morphs, list) or vertex_count <= 0:
        return None

    def candidate_for_index(global_index: int) -> Optional[dict]:
        if global_index < 0 or global_index >= len(morphs):
            return None
        morph = morphs[global_index]
        if not isinstance(morph, dict) or morph.get("type") != "vertex":
            return None
        offsets = morph.get("vertexOffsets")
        if not isinstance(offsets, list):
            return None
        has_valid_offset = False
        for offset in offsets:
            if not isinstance(offset, dict):
                continue
            try:
                vertex_index = int(offset.get("vertexIndex"))
            except (TypeError, ValueError):
                continue
            if 0 <= vertex_index < vertex_count:
                has_valid_offset = True
                break
        if not has_valid_offset:
            return None
        return {"name": str(morph.get("name") or ""), "index": global_index}

    spans = source.get("spans")
    if spans:
        candidates = []
        for span in spans:
            if not isinstance(span, (list, tuple)) or len(span) < 3:
                return None
            try:
                global_index = int(span[2])
            except (TypeError, ValueError):
                return None
            candidate = candidate_for_index(global_index)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    return [
        candidate
        for global_index in range(len(morphs))
        for candidate in [candidate_for_index(global_index)]
        if candidate is not None
    ]


def _commit_fast_morph_aliases(
    cmds_module,
    blend_shape: str,
    alias_plan: list[tuple[str, Optional[str], str]],
    serialized_mapping: Optional[str],
) -> None:
    """Apply alias/JSON changes with best-effort rollback on Maya failures."""
    attr = f"{blend_shape}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}"
    attr_exists = False
    old_mapping = None
    if serialized_mapping is not None:
        attr_exists = bool(cmds_module.attributeQuery(
            ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON,
            node=blend_shape,
            exists=True,
        ))
        old_mapping = cmds_module.getAttr(attr) if attr_exists else None
    applied = []
    try:
        for plug, old_alias, new_alias in alias_plan:
            # Record the intended operation before either Maya mutation so a
            # failure while removing/replacing the current alias can restore
            # that plug as well as earlier plugs.
            applied.append((plug, old_alias, new_alias))
            if old_alias:
                _remove_fast_alias(cmds_module, blend_shape, old_alias)
            cmds_module.aliasAttr(new_alias, plug)

        if serialized_mapping is not None:
            if not attr_exists:
                cmds_module.addAttr(
                    blend_shape,
                    longName=ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON,
                    dataType="string",
                )
            cmds_module.setAttr(attr, serialized_mapping, type="string")
    except Exception:
        for plug, old_alias, new_alias in reversed(applied):
            try:
                _remove_fast_alias(cmds_module, blend_shape, new_alias)
                if old_alias:
                    cmds_module.aliasAttr(old_alias, plug)
            except Exception as rollback_exc:
                logger.debug("Fast morph alias rollback failed for %s: %s", plug, rollback_exc)
        try:
            if serialized_mapping is not None:
                if attr_exists:
                    cmds_module.setAttr(attr, old_mapping or "", type="string")
                else:
                    cmds_module.deleteAttr(attr)
        except Exception as rollback_exc:
            logger.debug("Fast morph mapping rollback failed for %s: %s", attr, rollback_exc)
        raise


def _remove_fast_alias(cmds_module, blend_shape: str, alias: str) -> None:
    """Remove a Maya alias using the node-qualified form required by Maya."""
    cmds_module.aliasAttr(f"{blend_shape}.{alias}", remove=True)


def _apply_fast_root_metadata(
    filepath: str,
    root_node: str,
    metadata: Optional[dict],
    cmds_module,
) -> None:
    """Preserve PMX header names/comments on a successful fast-import root."""
    header = metadata.get("metadata") if isinstance(metadata, dict) else None
    if not isinstance(header, dict):
        try:
            pmx = parse_pmx_native(filepath)
            header = getattr(pmx, "header", None)
        except Exception as exc:
            logger.debug("Fast root metadata parse skipped: %s", exc)
            return
        if header is None:
            return
        values = {
            "name": getattr(header, "model_name", ""),
            "englishName": getattr(header, "model_name_english", ""),
            "comment": getattr(header, "comment", ""),
            "englishComment": getattr(header, "comment_english", ""),
        }
    else:
        values = header
    for attr, key in (
        (ATTR_MMD_MODEL_NAME, "name"),
        (ATTR_MMD_MODEL_NAME_EN, "englishName"),
        (ATTR_MMD_COMMENT, "comment"),
        (ATTR_MMD_COMMENT_EN, "englishComment"),
    ):
        _set_fast_string_attr(cmds_module, root_node, attr, values.get(key, "") or "")


def _create_standard_material(
    material: dict,
    material_index: int,
    cmds_module,
    used_names: Optional[set[str]] = None,
) -> Optional[str]:
    """Create a Maya standardSurface shader from parsed PMX material metadata."""
    raw_name = material.get("englishName") or material.get("name") or f"material_{material_index}"
    names = used_names if used_names is not None else _scene_name_set(cmds_module)
    shader_name = _allocate_fast_material_name(raw_name, material_index, names)

    try:
        shader = cmds_module.shadingNode("standardSurface", asShader=True, name=shader_name)
        shading_group = cmds_module.sets(
            renderable=True,
            noSurfaceShader=True,
            empty=True,
            name=f"{shader}SG",
        )
        cmds_module.connectAttr(f"{shader}.outColor", f"{shading_group}.surfaceShader", force=True)

        diffuse = material.get("diffuse") or [0.8, 0.8, 0.8, 1.0]
        if len(diffuse) >= 3:
            cmds_module.setAttr(f"{shader}.baseColor", float(diffuse[0]), float(diffuse[1]), float(diffuse[2]), type="double3")
        if len(diffuse) >= 4:
            alpha = float(diffuse[3])
            cmds_module.setAttr(f"{shader}.opacity", alpha, alpha, alpha, type="double3")

        specular = material.get("specular") or []
        if len(specular) >= 3:
            cmds_module.setAttr(f"{shader}.specularColor", float(specular[0]), float(specular[1]), float(specular[2]), type="double3")

        _set_fast_string_attr(cmds_module, shader, ATTR_MMD_MATERIAL_NAME, material.get("name") or "")
        _set_fast_string_attr(cmds_module, shader, ATTR_MMD_MATERIAL_NAME_EN, material.get("englishName") or "")

        return str(shader)
    except Exception as exc:
        logger.debug("Failed to create fast material %s: %s", raw_name, exc)
        return None


def _load_fast_skin_data(filepath: str) -> Optional[_FastSkinData]:
    """Load bones and skin weights for the fast skeleton/skin add-on path."""
    pmx_bytes = Path(filepath).read_bytes()
    parsed_model_cls = _mmd_parsed_model_class()
    parsed = parsed_model_cls.from_pmx_bytes(pmx_bytes)
    if parsed is not None:
        try:
            metadata_text = parsed.metadata_json
            skin_indices = parsed.skin_indices
            skin_weights = parsed.skin_weights
        finally:
            parsed.free()

        if metadata_text and skin_indices is not None and skin_weights is not None:
            metadata = json.loads(metadata_text)
            bones = metadata.get("bones") or metadata.get("skeleton", {}).get("bones") or []
            if bones:
                return _FastSkinData(list(bones), list(skin_indices), list(skin_weights))

        logger.debug("Parsed-model skin metadata incomplete; trying native PMX parser fallback")

    pmx = parse_pmx_native(filepath)
    if pmx is None:
        logger.debug("Native PMX parser fallback unavailable; skipping skeleton/skin")
        return None

    bones = [
        {
            "name": bone.name,
            "englishName": bone.name_english,
            "parentIndex": bone.parent_bone_index,
            "position": bone.position,
        }
        for bone in pmx.bones
    ]
    skin_indices, skin_weights = _skin_data_from_pmx_vertices(pmx.vertices)
    return _FastSkinData(bones, skin_indices, skin_weights)


def _skin_data_from_pmx_vertices(
    vertices,
) -> tuple[list[tuple[int, int, int, int]], list[tuple[float, float, float, float]]]:
    """Convert PmxVertex skinning fields to fixed four-influence tuples."""
    skin_indices: list[tuple[int, int, int, int]] = []
    skin_weights: list[tuple[float, float, float, float]] = []

    for vertex in vertices:
        indices = [int(i) for i in getattr(vertex, "bone_indices", [])[:4]]
        weights = [float(w) for w in getattr(vertex, "bone_weights", [])[:4]]
        mode = int(getattr(vertex, "weight_transform_type", 0))

        if mode == 0:
            weights = [1.0]
        elif mode in (1, 3):
            first = weights[0] if weights else 1.0
            weights = [first, 1.0 - first]
        elif mode in (2, 4):
            pass
        elif indices:
            weights = [1.0]

        while len(indices) < 4:
            indices.append(0)
        while len(weights) < 4:
            weights.append(0.0)

        skin_indices.append(tuple(indices[:4]))
        skin_weights.append(tuple(weights[:4]))

    return skin_indices, skin_weights


def _apply_fast_skeleton_skin(
    filepath: str,
    mesh_node: str,
    root_group: str,
    base_name: str,
    cmds_module,
) -> None:
    """Create basic Maya joints + skinCluster from mmd-anim parsed metadata.

    Bone positions come from ``metadata_json["bones"]`` (each entry has
    ``name``, ``englishName``, ``parentIndex``, ``position``).  Vertex skin
    data comes from ``MmdParsedModel.skin_indices`` / ``skin_weights``.

    On any error the function logs and returns; the caller is responsible
    for falling back to the mesh-only result.
    """
    skin_data = _load_fast_skin_data(filepath)
    if skin_data is None:
        return

    bones = skin_data.bones
    skin_indices = skin_data.skin_indices
    skin_weights = skin_data.skin_weights
    if not bones:
        logger.debug("No bones in metadata; skipping skeleton/skin")
        return

    # ---- build unique bone/joint names ----
    joint_names: list[str] = []
    used_names: set[str] = _scene_name_set(cmds_module)
    for b in bones:
        raw = b.get("englishName") or b.get("name") or f"bone_{len(joint_names)}"
        name = maya_name_utils.sanitize_unique_name(
            str(raw),
            used_names,
            fallback=f"bone_{len(joint_names)}",
        )
        joint_names.append(name)

    # ---- create skeleton group ----
    skeleton_group = cmds_module.group(
        empty=True,
        name=f"{base_name}_skeleton_fast",
        parent=root_group,
    )

    # ---- create all joints (initially at world origin) ----
    joints: list[str] = []
    for i, b in enumerate(bones):
        pos = b.get("position", [0.0, 0.0, 0.0])
        cmds_module.select(clear=True)
        jnt = cmds_module.joint(
            name=joint_names[i],
            position=(float(pos[0]), float(pos[1]), -float(pos[2])),
        )
        cmds_module.setAttr(f"{jnt}.segmentScaleCompensate", False)
        _tag_fast_joint_metadata(cmds_module, jnt, i, b)
        joints.append(jnt)

    # ---- parent joints according to parentIndex ----
    for i, b in enumerate(bones):
        parent_idx = b.get("parentIndex", -1)
        if 0 <= parent_idx < len(joints):
            try:
                cmds_module.parent(joints[i], joints[parent_idx], absolute=True)
            except Exception:
                pass

    # ---- parent root joints (parentIndex == -1) into skeleton group ----
    for i, b in enumerate(bones):
        parent_idx = b.get("parentIndex", -1)
        if parent_idx == -1 and cmds_module.objExists(joints[i]):
            try:
                cmds_module.parent(joints[i], skeleton_group, absolute=True)
            except Exception:
                pass

    # Parent/absolute operations establish the final local bind translation.
    # Persist that value for Animator Toolset Reset Pose, which intentionally
    # operates on selected joints instead of opening Rest Pose display mode.
    # Keep the VMD compatibility helper local so mesh-only fast import does
    # not import the full VMD scene-state dependency graph at module load.
    from mmd_tools.converters.vmd_import_state import store_bind_translate

    for joint in joints:
        try:
            translate = cmds_module.getAttr(f"{joint}.translate")[0]
            store_bind_translate(joint, translate, cmds_module=cmds_module)
        except Exception as exc:
            logger.debug("Failed to persist fast-path bind translate for %s: %s", joint, exc)

    # ---- create skinCluster ----
    if not cmds_module.objExists(mesh_node):
        logger.debug("Mesh node %s does not exist; skipping skinCluster", mesh_node)
        return

    used_bone_indices = sorted(
        {
            int(bone_index)
            for indices, weights in zip(skin_indices, skin_weights)
            for bone_index, weight in zip(indices, weights)
            if float(weight) > 0.0 and 0 <= int(bone_index) < len(joints)
        }
    )
    influence_pairs = [
        (bone_index, joints[bone_index])
        for bone_index in used_bone_indices
        if cmds_module.objExists(joints[bone_index])
    ]
    if not influence_pairs:
        logger.debug("No positive-weight joints for skinCluster; skipping")
        return
    influence_joints = [joint for _bone_index, joint in influence_pairs]

    # Evaluate authored-normal state before creating the deformer so Maya's
    # skinCluster initialization cannot alter the predicate's mesh snapshot.
    has_authored_normal_difference = maya_mesh_utils.has_materially_different_authored_normals(
        mesh_node
    )

    skin_cluster = cmds_module.skinCluster(
        influence_joints,
        mesh_node,
        toSelectedBones=True,
        normalizeWeights=2,
        maximumInfluences=4,
        name=f"{base_name}_skinCluster_fast",
    )[0]

    maya_mesh_utils.configure_authored_normal_skin_policy(
        skin_cluster,
        has_authored_normal_difference,
        cmds_module=cmds_module,
    )

    # ---- apply vertex weights ----
    n_verts = len(skin_indices)
    if n_verts == 0 or n_verts != len(skin_weights):
        logger.debug("Skin data vertex count mismatch (%d indices, %d weights); skipping weights",
                     n_verts, len(skin_weights) if skin_weights else 0)
        return

    influence_index_by_bone = {
        bone_index: influence_index
        for influence_index, (bone_index, _joint) in enumerate(influence_pairs)
    }

    # Build influence index -> weight for each vertex
    weights_list: list[list[float]] = []
    for v in range(n_verts):
        vw = [0.0] * len(influence_joints)
        idx4 = skin_indices[v]
        w4 = skin_weights[v]
        for k in range(4):
            bi = int(idx4[k])
            w = float(w4[k])
            if w > 0.0 and bi < len(joints):
                infl_idx = influence_index_by_bone.get(bi)
                if infl_idx is not None:
                    vw[infl_idx] = w
        weights_list.append(vw)

    try:
        maya_mesh_utils.apply_vertex_weights(skin_cluster, mesh_node, weights_list)
    except Exception as exc:
        logger.debug("Failed to apply vertex weights: %s", exc)


def _tag_fast_joint_metadata(cmds_module, joint: str, bone_index: int, bone: dict) -> None:
    """Attach MMD bone metadata expected by VMD/runtime paths."""
    attrs = (
        (ATTR_MMD_BONE_INDEX, "long", int(bone_index)),
        (ATTR_MMD_BONE_NAME, "string", str(bone.get("name") or "")),
        (ATTR_MMD_BONE_NAME_EN, "string", str(bone.get("englishName") or "")),
    )
    for attr, attr_type, value in attrs:
        try:
            if not cmds_module.attributeQuery(attr, node=joint, exists=True):
                if attr_type == "string":
                    cmds_module.addAttr(joint, longName=attr, dataType="string")
                else:
                    cmds_module.addAttr(joint, longName=attr, attributeType=attr_type)
            if attr_type == "string":
                cmds_module.setAttr(f"{joint}.{attr}", value, type="string")
            else:
                cmds_module.setAttr(f"{joint}.{attr}", value)
        except Exception:
            pass


def _sanitize_node_name(raw: str) -> str:
    """Return a Maya-safe name through the shared Unicode conversion policy."""
    return maya_name_utils.sanitize_text(raw)


def _scene_name_set(cmds_module) -> set[str]:
    """Collect existing Maya leaf names for deterministic fast-path allocation."""
    try:
        nodes = cmds_module.ls() or []
    except Exception:
        return set()
    names: set[str] = set()
    for node in nodes:
        leaf = str(node).rsplit("|", 1)[-1]
        names.add(leaf)
        names.add(leaf.rsplit(":", 1)[-1])
    return names


def _allocate_fast_material_name(raw_name, material_index: int, used_names: set[str]) -> str:
    """Allocate a safe shader/SG pair name for one parsed PMX material."""
    base = str(raw_name or f"material_{material_index}")
    while True:
        shader_name = maya_name_utils.sanitize_unique_name(
            f"{base}_fast",
            used_names,
            fallback=f"material_{material_index}_fast",
        )
        shading_group_name = f"{shader_name}SG"
        if shading_group_name not in used_names:
            used_names.add(shading_group_name)
            return shader_name


def _set_fast_string_attr(cmds_module, node: str, attr: str, value: str) -> None:
    """Best-effort raw metadata write that works with real Maya and test stubs."""
    try:
        if not cmds_module.attributeQuery(attr, node=node, exists=True):
            cmds_module.addAttr(node, longName=attr, dataType="string")
        cmds_module.setAttr(f"{node}.{attr}", str(value), type="string")
    except Exception as exc:
        logger.debug("Failed to preserve fast-path metadata %s.%s: %s", node, attr, exc)
