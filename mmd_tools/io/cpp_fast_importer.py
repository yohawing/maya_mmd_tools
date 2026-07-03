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
)
from mmd_tools.core import maya_utils
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
        logger.info(
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
        logger.info("maya.cmds not available – falling back to Python importer.")
        return None

    # --- load plugin (idempotent) -----------------------------------------
    try:
        cmds.loadPlugin(str(plugin_path), quiet=True)
    except RuntimeError as exc:
        logger.info("Failed to load C++ plugin: %s – falling back.", exc)
        return None

    # --- verify the command exists ----------------------------------------
    if not hasattr(cmds, "mmdFastLoad"):
        logger.info(
            "cmds.mmdFastLoad not found after plugin load – falling back."
        )
        return None

    # --- run fast load ----------------------------------------------------
    try:
        result = cmds.mmdFastLoad(f=filepath, n=base_name, s=scale, mo=include_morphs)
    except RuntimeError as exc:
        logger.info("mmdFastLoad failed: %s – falling back to Python importer.", exc)
        return None

    if not result or len(result) < 1:
        logger.info(
            "mmdFastLoad returned empty result – falling back to Python importer."
        )
        return None

    if not isinstance(result, (list, tuple)):
        logger.info(
            "mmdFastLoad returned unexpected type %s – falling back.",
            type(result).__name__,
        )
        return None

    # result is [transform, mesh]  (smoke_runtime_node.py convention)
    transform_node = str(result[0])
    mesh_node = str(result[1]) if len(result) >= 2 else None

    if mesh_node:
        _apply_basic_materials(filepath, mesh_node, cmds)

    if not mesh_only and mesh_node:
        # attempt skeleton + skin; any failure falls back to mesh-only result
        try:
            _apply_fast_skeleton_skin(filepath, mesh_node, transform_node, base_name, cmds)
        except Exception as exc:
            logger.info("Fast skeleton/skin failed (%s); returning mesh root only", exc)

    logger.info("Fast import succeeded: transform node = %s", transform_node)
    return transform_node


def _apply_basic_materials(filepath: str, mesh_node: str, cmds_module) -> None:
    """Assign standardSurface materials using mmd-anim parsed metadata."""
    try:
        pmx_bytes = Path(filepath).read_bytes()
        parsed_model_cls = _mmd_parsed_model_class()
        parsed = parsed_model_cls.from_pmx_bytes(pmx_bytes)
        if parsed is None:
            logger.info("Native parsed-model metadata unavailable; skipping fast material assignment")
            return
        try:
            metadata_text = parsed.metadata_json
            material_groups = parsed.material_groups or []
        finally:
            parsed.free()

        if not metadata_text or not material_groups:
            logger.info("No parsed material metadata/groups; skipping fast material assignment")
            return

        metadata = json.loads(metadata_text)
        materials = metadata.get("materials") or []
        for start_index, index_count, material_index in material_groups:
            if material_index >= len(materials) or index_count <= 0:
                continue

            material = materials[material_index]
            shader = _create_standard_material(material, material_index, cmds_module)
            if not shader:
                continue

            face_start = int(start_index) // 3
            face_end = (int(start_index) + int(index_count)) // 3 - 1
            if face_end < face_start:
                continue

            cmds_module.sets(f"{mesh_node}.f[{face_start}:{face_end}]", edit=True, forceElement=f"{shader}SG")
    except Exception as exc:
        logger.info("Fast material assignment skipped: %s", exc)


def _create_standard_material(material: dict, material_index: int, cmds_module) -> Optional[str]:
    """Create a Maya standardSurface shader from parsed PMX material metadata."""
    raw_name = material.get("englishName") or material.get("name") or f"material_{material_index}"
    shader_name = _sanitize_node_name(str(raw_name)) or f"material_{material_index}"
    shader_name = f"{shader_name}_fast"

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

        return str(shader)
    except Exception as exc:
        logger.info("Failed to create fast material %s: %s", raw_name, exc)
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

        logger.info("Parsed-model skin metadata incomplete; trying native PMX parser fallback")

    pmx = parse_pmx_native(filepath)
    if pmx is None:
        logger.info("Native PMX parser fallback unavailable; skipping skeleton/skin")
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
        logger.info("No bones in metadata; skipping skeleton/skin")
        return

    # ---- build unique bone/joint names ----
    joint_names: list[str] = []
    used_names: set[str] = set()
    for b in bones:
        raw = b.get("englishName") or b.get("name") or f"bone_{len(joint_names)}"
        name = _sanitize_node_name(str(raw)) or f"bone_{len(joint_names)}"
        original = name
        counter = 1
        while name in used_names:
            name = f"{original}_{counter}"
            counter += 1
        used_names.add(name)
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

    # ---- create skinCluster ----
    if not cmds_module.objExists(mesh_node):
        logger.info("Mesh node %s does not exist; skipping skinCluster", mesh_node)
        return

    # filter to joints that still exist
    existing_joints = [j for j in joints if cmds_module.objExists(j)]
    if not existing_joints:
        logger.info("No valid joints for skinCluster; skipping")
        return

    skin_cluster = cmds_module.skinCluster(
        existing_joints,
        mesh_node,
        toSelectedBones=True,
        normalizeWeights=2,
        maximumInfluences=4,
        name=f"{base_name}_skinCluster_fast",
    )[0]

    # ---- apply vertex weights ----
    n_verts = len(skin_indices)
    if n_verts == 0 or n_verts != len(skin_weights):
        logger.info("Skin data vertex count mismatch (%d indices, %d weights); skipping weights",
                     n_verts, len(skin_weights) if skin_weights else 0)
        return

    # Build per-vertex weight arrays of len existing_joints
    # Map joint_names -> index in existing_joints
    joint_to_influence: dict[str, int] = {}
    for idx, jnt in enumerate(existing_joints):
        joint_to_influence[jnt] = idx

    # Build influence index -> weight for each vertex
    weights_list: list[list[float]] = []
    for v in range(n_verts):
        vw = [0.0] * len(existing_joints)
        idx4 = skin_indices[v]
        w4 = skin_weights[v]
        for k in range(4):
            bi = int(idx4[k])
            w = float(w4[k])
            if w > 0.0 and bi < len(joints):
                jnt_name = joints[bi]
                infl_idx = joint_to_influence.get(jnt_name)
                if infl_idx is not None:
                    vw[infl_idx] = w
        weights_list.append(vw)

    try:
        maya_utils.apply_vertex_weights(skin_cluster, mesh_node, weights_list)
    except Exception as exc:
        logger.info("Failed to apply vertex weights: %s", exc)


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
    """Return a conservative ASCII Maya node-name fragment."""
    out = []
    for ch in raw:
        if ch.isascii() and (ch.isalnum() or ch == "_"):
            out.append(ch)
        else:
            out.append("_")
    name = "".join(out).strip("_")
    if name and name[0].isdigit():
        name = f"m_{name}"
    return name
