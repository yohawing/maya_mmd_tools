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

import json
from pathlib import Path
from typing import Optional

from mmd_tools.core.constants import (
    ATTR_MMD_MATERIAL_NAME,
    ATTR_MMD_MATERIAL_NAME_EN,
    ATTR_MMD_MATERIAL,
    ATTR_MMD_MATERIAL_INDEX,
    ATTR_MMD_MODEL_NAME,
    ATTR_MMD_MODEL_NAME_EN,
    ATTR_MMD_COMMENT,
    ATTR_MMD_COMMENT_EN,
    ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON,
    ATTR_MMD_AMBIENT_COLOR,
    ATTR_MMD_DIFFUSE_COLOR,
    ATTR_MMD_EDGE_COLOR,
    ATTR_MMD_EDGE_SIZE,
    ATTR_MMD_PMX_SOFT_BODY_COUNT,
    ATTR_MMD_SHININESS,
    ATTR_MMD_SPECULAR_COLOR,
    GEOMETRY_GROUP,
    SCENE_ROOT_SUFFIX,
)
from mmd_tools.core import cpp_plugin_locator, maya_name_utils
from mmd_tools.core.logger import get_logger
from mmd_tools.core.native.native_pmx_parser import parse_pmx_native
from mmd_tools.converters.material_shader_parameters import (
    ATTR_MMD_DIFFUSE_ALPHA,
    ATTR_MMD_EDGE_ALPHA,
)

logger = get_logger(__name__)

# Kept as a module attribute so tests can patch it without importing native code.
MmdParsedModel = None
_FAST_NATIVE_PMX_UNSET = object()


# ---------------------------------------------------------------------------
# Candidate discovery  (mirrors tests/cpp/smoke_runtime_node.py)
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[2]  # project root

def _mmd_parsed_model_class():
    """Resolve the native parsed-model wrapper only when the fast path needs it."""
    global MmdParsedModel
    if MmdParsedModel is None:
        from mmd_tools.core.native import MmdParsedModel as _MmdParsedModel

        MmdParsedModel = _MmdParsedModel
    return MmdParsedModel


def _candidate_plugin_paths() -> list[Path]:
    """Return candidates from the canonical native plug-in locator."""
    return cpp_plugin_locator.plugin_candidate_paths(
        [ROOT], maya_version=_running_maya_major_version()
    )


def _running_maya_major_version() -> str:
    """Return the active Maya major version without requiring an env var."""
    return cpp_plugin_locator.running_maya_major_version(default="2024")


# ---------------------------------------------------------------------------
# Plugin loading helpers
# ---------------------------------------------------------------------------


def _setup_plugin_directory(plugin_dir: Path) -> None:
    """Add *plugin_dir* to ``PATH`` (and ``add_dll_directory`` on Windows)."""
    cpp_plugin_locator.prepare_plugin_directory(plugin_dir / "mmd_tools_cpp.mll")


def _require_dx11_for_vp2_ownership(cmds) -> None:
    """Reject a confirmed OpenGL VP2 device before native ownership import.

    ``mmdRenderShape`` currently owns draw data only on DirectX 11.  Unknown
    device information is deliberately allowed so non-Maya test doubles and
    Maya sessions where ``ogs`` is unavailable retain the existing behavior.
    """
    ogs = getattr(cmds, "ogs", None)
    if not callable(ogs):
        return

    try:
        raw = ogs(deviceInformation=True)
    except Exception:
        return

    if isinstance(raw, str):
        device_lines = raw.splitlines()
    elif isinstance(raw, (list, tuple)) and all(
        isinstance(part, str) for part in raw
    ):
        device_lines = [
            line for part in raw for line in part.splitlines()
        ]
    else:
        # In particular, do not interpret MagicMock string representations as
        # real Maya device diagnostics.
        return

    api_line = next(
        (
            line.strip()
            for line in device_lines
            if line.strip().lower().replace(" ", "").startswith("api:")
        ),
        "",
    )
    api_lowered = api_line.lower()
    if any(
        token in api_lowered
        for token in ("directx v.11", "directx11", "direct3d11", "dx11", "d3d11")
    ):
        return
    device_text = " ".join(device_lines)
    if api_line or any(
        token in device_text.lower()
        for token in (
            "opengl",
            "open gl",
            "openglcore",
            "glcore",
            "core profile",
            "virtualdevicegl",
        )
    ):
        active_api = (api_line or "OpenGL").rstrip(".")
        raise RuntimeError(
            "C++ VP2 RenderOverride requires DirectX 11, but Maya is using "
            f"{active_api}. "
            "In Maya Preferences, open Display > Viewport 2.0, set Rendering "
            "engine to DirectX 11, then restart Maya before importing the model."
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fast_import(
    filepath: str,
    base_name: str = "mmd_fast_model",
    scale: float = 1.0,
    mesh_only: bool = True,
    include_morphs: bool = True,
    vp2_ownership: bool = False,
    options: Optional[dict] = None,
    progress_callback=None,
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
        If False, native geometry uses the ordinary PMX authoring pipeline,
        including skeleton, skin, morphs, physics and model metadata.
    include_morphs:
        If True, asks the C++ command to create PMX vertex morph
        blendShape targets. With VP2 ownership, the Python bridge also creates
        material morph authoring nodes and binds native material values and
        alpha through the existing evaluator.
    vp2_ownership:
        If True, asks the C++ command to create the opt-in ``mmdRenderShape``
        alongside an ordinary source mesh and let the VP2 geometry override
        own the draw data. Material, morph, and skeleton post-processing use
        that source mesh while the proxy stays render-only.

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

    # --- import Maya commands ---------------------------------------------
    try:
        import maya.cmds as cmds
    except ImportError:
        logger.debug("maya.cmds not available – falling back to Python importer.")
        return None

    # --- load plugin (idempotent, exact path) ------------------------------
    try:
        if not cpp_plugin_locator.is_plugin_loaded(plugin_path, cmds):
            # Keep the compatibility seam for callers/tests that need to
            # observe directory preparation, while the actual loaded-path
            # check and load operation remain canonical.
            _setup_plugin_directory(plugin_path.parent)
            cpp_plugin_locator.load_plugin(plugin_path, cmds, prepare=False)
    except RuntimeError as exc:
        logger.debug("Failed to load C++ plugin: %s – falling back.", exc)
        return None

    # --- verify the command exists ----------------------------------------
    if not hasattr(cmds, "mmdFastLoad"):
        logger.debug(
            "cmds.mmdFastLoad not found after plugin load – falling back."
        )
        return None

    if vp2_ownership:
        _require_dx11_for_vp2_ownership(cmds)

    if not mesh_only:
        # Keep the ordinary authoring contract in one place. Only geometry
        # construction is replaced by mmdFastLoad; bones, morphs, physics and
        # ownership use the same converters as a regular PMX import.
        from mmd_tools.io.mmd_importer import (
            _record_physics_compatibility_warnings,
            _scoped_settings_override,
        )
        from mmd_tools.io.pmx_importer import import_pmx_file, _require_effective_import_scale
        from mmd_tools.core import settings, settings_keys

        scale = _require_effective_import_scale(scale)
        try:
            pmx = parse_pmx_native(filepath)
        except Exception as exc:
            logger.debug("Fast native PMX metadata unavailable: %s", exc)
            return None
        if pmx is None:
            return None
        command_args = {"f": filepath, "n": base_name, "s": scale, "mo": False}
        split = bool((options or {}).get(
            "separate_meshes_by_material",
            settings.get(settings_keys.IMPORT_MODEL_SEPARATE_MESHES_BY_MATERIAL, False),
        ))
        if split:
            command_args["sp"] = True
        if vp2_ownership:
            command_args["vp2Ownership"] = True
        try:
            native_mesh = cmds.mmdFastLoad(**command_args)
        except RuntimeError as exc:
            logger.debug("Fast native geometry unavailable: %s", exc)
            return None
        expected = 1 if split else (3 if vp2_ownership else 2)
        if not isinstance(native_mesh, (list, tuple)) or len(native_mesh) != expected:
            raise RuntimeError("mmdFastLoad returned an invalid geometry result")
        if options is not None:
            _record_physics_compatibility_warnings(pmx, options)
        import_options = dict(options or {})
        import_options.update({
            "import_morphs": include_morphs,
            "_cpp_fast_load_geometry": native_mesh,
            "use_cpp_vp2_ownership": vp2_ownership,
        })
        with _scoped_settings_override(import_options):
            return import_pmx_file(
                pmx, filepath, scale, import_options, progress_callback=progress_callback
            )

    # --- run fast load ----------------------------------------------------
    try:
        command_args = {
            "f": filepath,
            "n": base_name,
            "s": scale,
            "mo": include_morphs,
        }
        # Keep the flag absent for the normal fast-load path so an older
        # plugin binary remains compatible.  The VP2 path is an explicit
        # opt-in and therefore requires a plugin that supports the flag.
        if vp2_ownership:
            command_args["vp2Ownership"] = True
        result = cmds.mmdFastLoad(**command_args)
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

    def _cleanup_vp2_result_root() -> None:
        """Best-effort cleanup for a VP2 result that fails its ABI contract."""
        try:
            cmds.delete(str(result[0]))
        except Exception as exc:
            # The contract rejection is still the primary outcome; cleanup
            # failures must not mask it or turn fallback into an exception.
            logger.debug("Failed to clean up rejected VP2 root: %s", exc)

    if vp2_ownership:
        if len(result) != 3:
            logger.debug(
                "VP2 mmdFastLoad returned %d nodes; expected [root, sourceMesh, renderShape]",
                len(result),
            )
            if result:
                _cleanup_vp2_result_root()
            return None
        render_shape_result = str(result[2])
        try:
            render_shape_type = cmds.nodeType(render_shape_result)
        except Exception as exc:
            logger.debug(
                "VP2 render shape node lookup failed for %s: %s",
                render_shape_result,
                exc,
            )
            _cleanup_vp2_result_root()
            return None
        if render_shape_type != "mmdRenderShape":
            logger.debug(
                "VP2 mmdFastLoad returned %s as render shape (expected mmdRenderShape)",
                render_shape_type,
            )
            _cleanup_vp2_result_root()
            return None

    # Normal result is [transform, mesh].  VP2 uses [transform, sourceMesh,
    # renderShape], so all existing post-processing continues to target the
    # second element in both routes.
    transform_node = str(result[0])
    mesh_node = str(result[1]) if len(result) >= 2 else None

    shared_native_kwargs = {}
    if vp2_ownership and include_morphs:
        try:
            shared_native_pmx = parse_pmx_native(filepath)
        except Exception as exc:
            logger.debug("Fast shared native PMX parse unavailable: %s", exc)
            shared_native_pmx = None
        shared_native_kwargs = {"native_pmx": shared_native_pmx}

    metadata = (
        _apply_basic_materials(filepath, mesh_node, cmds, **shared_native_kwargs)
        if mesh_node
        else None
    )
    transform_node, mesh_node = _organize_fast_dag(
        transform_node,
        mesh_node,
        metadata,
        base_name,
        cmds,
        filepath=filepath,
        **shared_native_kwargs,
    )
    _apply_fast_root_metadata(
        filepath, transform_node, metadata, cmds, **shared_native_kwargs
    )
    model_registry = None
    try:
        from mmd_tools.core.model_registry import ensure_model_registry

        registered = ensure_model_registry(transform_node)
        if isinstance(registered, str):
            model_registry = registered
    except Exception as exc:
        logger.warning("Fast import ownership registry unavailable for %s: %s", transform_node, exc)
    blend_shape_nodes = []
    if include_morphs and mesh_node:
        blend_shape_nodes = (
            _apply_fast_morph_metadata(
                filepath, mesh_node, cmds, **shared_native_kwargs
            )
            or []
        )
    if vp2_ownership and include_morphs and mesh_node:
        runtime_result = _apply_fast_material_morph_runtime(
            filepath,
            transform_node,
            model_registry=model_registry,
            blend_shape_nodes=blend_shape_nodes,
            **shared_native_kwargs,
        )
        if not isinstance(runtime_result, dict) or not runtime_result.get("success", False):
            _cleanup_fast_vp2_runtime(transform_node, runtime_result, model_registry, cmds)
            skipped = runtime_result.get("skipped", []) if isinstance(runtime_result, dict) else []
            reason = "; ".join(str(item) for item in skipped) or "material morph runtime failed"
            raise RuntimeError(f"Fast VP2 material morph runtime failed: {reason}")

    logger.debug("Fast import succeeded: transform node = %s", transform_node)
    return transform_node


def _organize_fast_dag(
    source_transform: str,
    source_mesh: Optional[str],
    metadata: Optional[dict],
    base_name: str,
    cmds_module,
    *,
    filepath: Optional[str] = None,
    native_pmx=_FAST_NATIVE_PMX_UNSET,
) -> tuple[str, Optional[str]]:
    """Place a FastLoad mesh below the ordinary model/Geometry boundary.

    ``mmdFastLoad`` deliberately creates just the source mesh transform so its
    native command can own construction and undo.  The Python import contract,
    however, exposes a model root with a direct ``Geometry`` child.  Add that
    lightweight authoring boundary here, before root metadata and skeleton
    post-processing run.  The mesh and optional ``mmdRenderShape`` remain
    together because the proxy is a child shape of the source transform.

    The command wrapper is also used by headless callers with deliberately
    partial ``maya.cmds`` fakes.  If the returned transform cannot be resolved
    to a real DAG node, preserve the historical result instead of making that
    fallback path fail.
    """
    source_paths = cmds_module.ls(source_transform, long=True) or []
    if not source_paths or not isinstance(source_paths[0], str):
        return source_transform, source_mesh

    model_name = _fast_model_scene_name(
        metadata, base_name, filepath=filepath, native_pmx=native_pmx
    )
    root_group = cmds_module.group(
        empty=True,
        name=f"{model_name}{SCENE_ROOT_SUFFIX}",
    )
    geometry_group = cmds_module.group(
        empty=True,
        name=GEOMETRY_GROUP,
        parent=root_group,
    )
    # This mirrors MeshConverter: Geometry is owned by the model root but
    # does not inherit its transform a second time once skinning is present.
    cmds_module.setAttr(f"{geometry_group}.inheritsTransform", False)

    mesh_transform = cmds_module.rename(source_paths[0], f"{model_name}_mesh")
    cmds_module.parent(mesh_transform, geometry_group, absolute=True)
    mesh_shapes = cmds_module.listRelatives(
        mesh_transform,
        shapes=True,
        noIntermediate=True,
        type="mesh",
        fullPath=True,
    ) or []
    mesh_shape = str(mesh_shapes[0]) if mesh_shapes and isinstance(mesh_shapes[0], str) else source_mesh
    return str(root_group), mesh_shape


def _cleanup_fast_vp2_runtime(
    root_node: str,
    runtime_result: Optional[dict],
    model_registry: Optional[str],
    cmds_module,
) -> None:
    """Delete only the model-owned nodes from a failed VP2 fast import."""
    owned_nodes = []
    if isinstance(runtime_result, dict):
        owned_nodes.extend(runtime_result.get("material_morph_nodes", []) or [])
        graph = runtime_result.get("material_morph_graph")
        if isinstance(graph, dict):
            owned_nodes.extend(graph.get("evaluator_nodes", []) or [])
        controller = runtime_result.get("morph_controller")
        if controller:
            owned_nodes.append(controller)

    try:
        controllers = cmds_module.listConnections(
            f"{root_node}.mmd_morph_controller",
            source=True,
            destination=False,
        ) or []
        for controller in controllers:
            if cmds_module.nodeType(controller) == "mmdMorphController":
                owned_nodes.append(controller)
    except Exception:
        logger.debug("Failed to find failed-import morph controller for %s", root_node, exc_info=True)

    if model_registry:
        owned_nodes.append(model_registry)
    owned_nodes.append(root_node)

    seen = set()
    for node in owned_nodes:
        if not isinstance(node, str) or not node or node in seen:
            continue
        seen.add(node)
        try:
            if cmds_module.objExists(node):
                cmds_module.delete(node)
        except Exception:
            logger.debug("Failed to clean failed VP2 import node %s", node, exc_info=True)


def _fast_model_scene_name(
    metadata: Optional[dict],
    fallback: str,
    *,
    filepath: Optional[str] = None,
    native_pmx=_FAST_NATIVE_PMX_UNSET,
) -> str:
    """Return the same safe PMX header name used by the Python root builder."""
    header = metadata.get("metadata") if isinstance(metadata, dict) else None
    if isinstance(header, dict):
        raw_name = header.get("englishName") or header.get("name")
        if raw_name:
            return maya_name_utils.sanitize_text(raw_name)
    if filepath:
        try:
            pmx = (
                parse_pmx_native(filepath)
                if native_pmx is _FAST_NATIVE_PMX_UNSET
                else native_pmx
            )
            header = getattr(pmx, "header", None)
            raw_name = (
                getattr(header, "model_name_english", "")
                or getattr(header, "model_name", "")
            )
            if raw_name:
                return maya_name_utils.sanitize_text(raw_name)
        except Exception as exc:
            logger.debug("Fast DAG header parse skipped: %s", exc)
    return maya_name_utils.sanitize_text(fallback)


def _apply_basic_materials(
    filepath: str,
    mesh_node: str,
    cmds_module,
    *,
    native_pmx=_FAST_NATIVE_PMX_UNSET,
) -> Optional[dict]:
    """Assign materials and return the parsed metadata for root attributes."""
    metadata = None
    material_groups = None
    try:
        pmx_bytes = Path(filepath).read_bytes()
        parsed_model_cls = _mmd_parsed_model_class()
        parsed = parsed_model_cls.from_pmx_bytes(pmx_bytes)
        if parsed is not None:
            try:
                metadata_text = parsed.metadata_json
                material_groups = parsed.material_groups or []
            finally:
                parsed.free()
            if metadata_text:
                metadata = json.loads(metadata_text)
        else:
            logger.debug("Native parsed-model metadata unavailable; trying current native PMX parser")
    except Exception as exc:
        logger.debug("Parsed-model material metadata unavailable: %s", exc)

    if not metadata or not material_groups:
        native_material_data = _load_native_fast_material_data(
            filepath, native_pmx=native_pmx
        )
        if native_material_data is not None:
            metadata, material_groups = native_material_data

    if not metadata or not material_groups:
        logger.debug("Fast material metadata/groups unavailable; skipping material assignment")
        return metadata

    try:
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


def _load_native_fast_material_data(
    filepath: str,
    *,
    native_pmx=_FAST_NATIVE_PMX_UNSET,
):
    """Build fast material metadata from the current native PMX parser ABI."""
    try:
        pmx = (
            parse_pmx_native(filepath)
            if native_pmx is _FAST_NATIVE_PMX_UNSET
            else native_pmx
        )
        if pmx is None:
            return None
        header = getattr(pmx, "header", None)
        native_materials = list(getattr(pmx, "materials", []) or [])
        if not native_materials:
            return None

        materials = []
        material_groups = []
        start_index = 0
        for material_index, native_material in enumerate(native_materials):
            diffuse = tuple(getattr(native_material, "diffuse", (1.0, 1.0, 1.0, 1.0)))
            specular = tuple(getattr(native_material, "specular", (0.5, 0.5, 0.5)))
            materials.append(
                {
                    "name": str(getattr(native_material, "name", "") or ""),
                    "englishName": str(getattr(native_material, "name_english", "") or ""),
                    "diffuse": list(diffuse[:4]),
                    "specular": list(specular[:3]),
                    "ambient": list(
                        tuple(getattr(native_material, "ambient", (0.0, 0.0, 0.0)))[:3]
                    ),
                    "specularPower": float(
                        getattr(native_material, "specular_coefficient", 0.0) or 0.0
                    ),
                    "edgeColor": list(
                        tuple(getattr(native_material, "edge_color", (0.0, 0.0, 0.0, 1.0)))[:4]
                    ),
                    "edgeSize": float(
                        getattr(native_material, "edge_size", 0.0) or 0.0
                    ),
                }
            )
            face_count = max(0, int(getattr(native_material, "face_count", 0) or 0))
            if face_count:
                material_groups.append((start_index, face_count, material_index))
                start_index += face_count

        metadata = {
            "metadata": {
                "name": str(getattr(header, "model_name", "") or ""),
                "englishName": str(getattr(header, "model_name_english", "") or ""),
                "comment": str(getattr(header, "comment", "") or ""),
                "englishComment": str(getattr(header, "comment_english", "") or ""),
                "counts": {
                    "softBodies": len(getattr(pmx, "soft_bodies", []) or []),
                },
            },
            "materials": materials,
        }
        return metadata, material_groups
    except Exception as exc:
        logger.debug("Native PMX material metadata fallback skipped: %s", exc)
        return None


def _find_fast_blend_shapes(mesh_node: str, cmds_module) -> list[str]:
    """Return existing blendShape nodes in the source mesh history."""
    blend_shapes = []
    try:
        for history_node in cmds_module.listHistory(mesh_node, pruneDagObjects=True) or []:
            if cmds_module.nodeType(history_node) == "blendShape" and history_node not in blend_shapes:
                blend_shapes.append(history_node)
    except Exception as exc:
        logger.debug("Fast blendShape discovery skipped: %s", exc)
    return blend_shapes


def _apply_fast_morph_metadata(
    filepath: str,
    mesh_node: str,
    cmds_module,
    *,
    native_pmx=_FAST_NATIVE_PMX_UNSET,
) -> list[str]:
    """Replace C++ vertex-morph aliases and persist their raw PMX mapping.

    ``mmdFastLoad`` intentionally only has the C++ byte-level sanitizer.  The
    Python fast wrapper is the common naming boundary, so it can apply the
    shared Unicode dictionary and retain the original PMX names used by VMD
    and export paths.  This helper is deliberately transactional: all source
    metadata, target ordering, aliases, and JSON are validated before the
    first Maya mutation.  If a Maya mutation fails, already-applied aliases
    and the metadata attribute are restored on a best-effort basis.
    """
    blend_shapes = _find_fast_blend_shapes(mesh_node, cmds_module)
    try:
        source = _load_fast_morph_source(filepath, native_pmx=native_pmx)
        if source is None:
            return blend_shapes

        if not blend_shapes:
            return blend_shapes
        if len(blend_shapes) != 1:
            logger.debug("Fast morph metadata skipped: expected one blendShape, got %d", len(blend_shapes))
            return blend_shapes

        blend_shape = blend_shapes[0]
        weight_count = int(cmds_module.blendShape(blend_shape, query=True, weightCount=True) or 0)
        if weight_count == 0:
            return blend_shapes

        candidates = _fast_vertex_morph_candidates(source)
        if candidates is None or len(candidates) != weight_count:
            logger.debug(
                "Fast morph metadata skipped: C++ target count %d does not match parsed candidates %s",
                weight_count,
                None if candidates is None else len(candidates),
            )
            return blend_shapes

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
        return blend_shapes
    except Exception as exc:
        logger.debug("Fast morph metadata skipped: %s", exc)
        return blend_shapes


def _apply_fast_material_morph_runtime(
    filepath: str,
    root_node: str,
    *,
    model_registry: Optional[str] = None,
    blend_shape_nodes=None,
    native_pmx=_FAST_NATIVE_PMX_UNSET,
) -> dict:
    """Build material morph metadata/controller and bind native values."""
    result = {
        "success": True,
        "material_morph_nodes": [],
        "morph_controller": None,
        "material_morph_graph": None,
        "native_alpha": None,
        "skipped": [],
    }

    try:
        pmx = (
            parse_pmx_native(filepath)
            if native_pmx is _FAST_NATIVE_PMX_UNSET
            else native_pmx
        )
        if pmx is None:
            result["success"] = False
            result["skipped"].append("native_pmx_unavailable")
            return result

        from mmd_tools.converters import MorphConverter
        from mmd_tools.converters.material_morph_runtime import build_material_morph_graph
        from mmd_tools.core.pmx_data.morph import PmxMorphType
        from mmd_tools.io.model_import_pipeline import ModelImportPipeline

        converter = MorphConverter()
        converter.validate_runtime_requirements(pmx)
        material_nodes = result["material_morph_nodes"]
        material_morph_count = 0
        conversion_failed = False
        for morph_index, morph in enumerate(getattr(pmx, "morphs", []) or []):
            if morph.morph_type != PmxMorphType.MaterialMorph:
                continue
            material_morph_count += 1
            converted = converter._convert_material_morph_pmx(
                morph,
                morph_index=morph_index,
            )
            if isinstance(converted, dict) and converted.get("success") and converted.get("morph_node"):
                material_nodes.append(converted["morph_node"])
            else:
                conversion_failed = True
                result["success"] = False
                result["skipped"].append(f"material_morph_conversion_failed:{morph_index}")

        if material_morph_count == 0:
            result["skipped"].append("no_material_morphs")
            return result
        if conversion_failed:
            return result

        morph_result = {
            "success": True,
            "morphs_converted": len(material_nodes),
            "total_morphs": len(getattr(pmx, "morphs", []) or []),
            "blend_shape_nodes": list(blend_shape_nodes or []),
            "bone_morph_nodes": [],
            "group_morph_nodes": [],
            "material_morph_nodes": material_nodes,
            "uv_morph_nodes": [],
            "flip_impulse_morph_nodes": [],
            "vertex_morph_nodes": [],
            "results": [],
        }
        pipeline = ModelImportPipeline(
            logger=logger,
            filepath=filepath,
            scale=1.0,
            options={"import_morphs": True},
        )
        pipeline.connect_morph_nodes_to_root(
            root_node,
            morph_result,
            model_registry=model_registry,
        )
        result["morph_controller"] = converter.build_morph_controller(
            pmx,
            root_node,
            morph_result,
        )
        result["material_morph_graph"] = build_material_morph_graph(root_node)
        native_alpha = result["material_morph_graph"].get("native_alpha")
        result["native_alpha"] = native_alpha
        native_alpha_success = (
            native_alpha.get("success", False)
            if isinstance(native_alpha, dict)
            else bool(native_alpha)
            and all(item.get("success", False) for item in native_alpha)
        )
        result["success"] = bool(
            result["morph_controller"]
            and result["material_morph_graph"].get("success", False)
            and native_alpha_success
        )
        result["skipped"].extend(result["material_morph_graph"].get("skipped", []))
        if isinstance(native_alpha, dict):
            result["skipped"].extend(native_alpha.get("skipped", []))
        else:
            for native_result in native_alpha or []:
                result["skipped"].extend(native_result.get("skipped", []))
        return result
    except Exception as exc:
        result["success"] = False
        result["skipped"].append(f"material_morph_runtime_failed:{exc}")
        logger.warning(
            "Fast native material morph bridge skipped for %s: %s",
            root_node,
            exc,
            exc_info=True,
        )
        return result


def _load_fast_morph_source(
    filepath: str,
    *,
    native_pmx=_FAST_NATIVE_PMX_UNSET,
) -> Optional[dict]:
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

    return _load_fast_morph_source_native(filepath, native_pmx=native_pmx)


def _load_fast_morph_source_native(
    filepath: str,
    *,
    native_pmx=_FAST_NATIVE_PMX_UNSET,
) -> Optional[dict]:
    """Convert the native PMX object into the fast morph source schema."""
    try:
        pmx = (
            parse_pmx_native(filepath)
            if native_pmx is _FAST_NATIVE_PMX_UNSET
            else native_pmx
        )
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
    *,
    native_pmx=_FAST_NATIVE_PMX_UNSET,
) -> None:
    """Preserve PMX metadata on fast-import roots."""
    header = metadata.get("metadata") if isinstance(metadata, dict) else None
    soft_body_count = _fast_soft_body_count(metadata)
    if not isinstance(header, dict):
        try:
            pmx = (
                parse_pmx_native(filepath)
                if native_pmx is _FAST_NATIVE_PMX_UNSET
                else native_pmx
            )
            header = getattr(pmx, "header", None)
            if soft_body_count is None and pmx is not None:
                soft_body_count = len(getattr(pmx, "soft_bodies", []) or [])
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
    if soft_body_count is not None:
        _set_fast_long_attr(cmds_module, root_node, ATTR_MMD_PMX_SOFT_BODY_COUNT, soft_body_count)


def _fast_soft_body_count(metadata: Optional[dict]) -> Optional[int]:
    """Read PMX soft-body count from native parsed-model metadata."""
    if not isinstance(metadata, dict):
        return None
    candidates = [metadata, metadata.get("metadata")]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        counts = candidate.get("counts")
        if not isinstance(counts, dict) or "softBodies" not in counts:
            continue
        value = counts["softBodies"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return value
    return None


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
        else:
            alpha = 1.0

        specular = material.get("specular") or []
        if len(specular) >= 3:
            cmds_module.setAttr(f"{shader}.specularColor", float(specular[0]), float(specular[1]), float(specular[2]), type="double3")

        ambient = material.get("ambient") or [0.0, 0.0, 0.0]
        shininess = material.get(
            "specularPower",
            material.get("specular_coefficient", material.get("shininess", 0.0)),
        )
        edge_color = material.get("edgeColor", material.get("edge_color")) or [
            0.0, 0.0, 0.0, 1.0
        ]
        edge_alpha = float(edge_color[3]) if len(edge_color) > 3 else float(
            material.get("edgeAlpha", material.get("edge_alpha", 1.0))
        )
        edge_size = material.get("edgeSize", material.get("edge_size", 0.0))

        _set_fast_string_attr(cmds_module, shader, ATTR_MMD_MATERIAL_NAME, material.get("name") or "")
        _set_fast_string_attr(cmds_module, shader, ATTR_MMD_MATERIAL_NAME_EN, material.get("englishName") or "")
        _set_fast_long_attr(cmds_module, shader, ATTR_MMD_MATERIAL, 1)
        _set_fast_long_attr(cmds_module, shader, ATTR_MMD_MATERIAL_INDEX, material_index)
        _set_fast_double_attr(cmds_module, shader, ATTR_MMD_DIFFUSE_ALPHA, alpha)
        _set_fast_double3_attr(cmds_module, shader, ATTR_MMD_DIFFUSE_COLOR, diffuse[:3])
        _set_fast_double3_attr(cmds_module, shader, ATTR_MMD_SPECULAR_COLOR, specular[:3])
        _set_fast_double_attr(cmds_module, shader, ATTR_MMD_SHININESS, shininess)
        _set_fast_double3_attr(cmds_module, shader, ATTR_MMD_AMBIENT_COLOR, ambient[:3])
        _set_fast_double3_attr(cmds_module, shader, ATTR_MMD_EDGE_COLOR, edge_color[:3])
        _set_fast_double_attr(cmds_module, shader, ATTR_MMD_EDGE_ALPHA, edge_alpha)
        _set_fast_double_attr(cmds_module, shader, ATTR_MMD_EDGE_SIZE, edge_size)

        return str(shader)
    except Exception as exc:
        logger.debug("Failed to create fast material %s: %s", raw_name, exc)
        return None


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


def _set_fast_long_attr(cmds_module, node: str, attr: str, value: int) -> None:
    """Best-effort integer metadata write for fast-import roots."""
    try:
        if not cmds_module.attributeQuery(attr, node=node, exists=True):
            cmds_module.addAttr(node, longName=attr, attributeType="long")
        cmds_module.setAttr(f"{node}.{attr}", int(value))
    except Exception as exc:
        logger.debug("Failed to preserve fast-path integer metadata %s.%s: %s", node, attr, exc)


def _set_fast_double_attr(cmds_module, node: str, attr: str, value: float) -> None:
    """Best-effort floating-point metadata write for fast-import roots."""
    try:
        if not cmds_module.attributeQuery(attr, node=node, exists=True):
            cmds_module.addAttr(node, longName=attr, attributeType="double")
        cmds_module.setAttr(f"{node}.{attr}", float(value))
    except Exception as exc:
        logger.debug("Failed to preserve fast-path floating metadata %s.%s: %s", node, attr, exc)


def _set_fast_double3_attr(cmds_module, node: str, attr: str, value) -> None:
    """Best-effort RGB metadata write for fast-import material authorship."""
    try:
        if not cmds_module.attributeQuery(attr, node=node, exists=True):
            cmds_module.addAttr(node, longName=attr, attributeType="double3")
            for axis in "XYZ":
                cmds_module.addAttr(
                    node,
                    longName=f"{attr}{axis}",
                    attributeType="double",
                    parent=attr,
                )
        components = tuple(float(component) for component in value[:3])
        if len(components) == 3:
            cmds_module.setAttr(f"{node}.{attr}", *components, type="double3")
    except Exception as exc:
        logger.debug("Failed to preserve fast-path RGB metadata %s.%s: %s", node, attr, exc)
