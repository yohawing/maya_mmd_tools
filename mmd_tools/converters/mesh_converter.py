import os
import time

from maya import cmds
from typing import Dict, List, Optional, Tuple, Union

from mmd_tools.core.settings import settings
from mmd_tools.core import settings_keys as setting_keys
from mmd_tools.core import maya_attribute_utils, maya_material_utils, maya_mesh_utils, maya_name_utils, maya_scene_utils, maya_viewport_utils
from mmd_tools.core.logger import get_logger
from mmd_tools.core.texture_path_cache import (
    build_texture_path_diagnostics,
    build_texture_source_candidates,
    classify_unreadable_file_texture_path,
    find_resolvable_source,
    is_unreadable_file_texture_path,
    resolve_texture_to_cache,
)
from mmd_tools.core.pmx_data import PmxData
from mmd_tools.core.pmx_data.morph import PmxMorphType
from mmd_tools.core.constants import (
    ATTR_MMD_SHARED_TOON_FLAG,
    ATTR_MMD_SPHERE_TEXTURE_INDEX,
    ATTR_MMD_TEXTURE_INDEX,
    ATTR_MMD_TEXTURE_CACHE_PATH,
    ATTR_MMD_TEXTURE_UNRESOLVED,
    ATTR_MMD_TOON_TEXTURE_INDEX,
    GEOMETRY_GROUP,
    ATTR_MMD_MATERIAL,
    ATTR_MMD_MATERIAL_NAME,
    ATTR_MMD_MATERIAL_NAME_EN,
    ATTR_MMD_DIFFUSE_COLOR,
    ATTR_MMD_SPECULAR_COLOR,
    ATTR_MMD_AMBIENT_COLOR,
    ATTR_MMD_SHININESS,
    ATTR_MMD_SPHERE_MODE,
    ATTR_MMD_MEMO,
    ATTR_MMD_EDGE_FLAG,
    ATTR_MMD_DRAW_FLAGS,
    ATTR_MMD_EDGE_COLOR,
    ATTR_MMD_EDGE_SIZE,
    ATTR_MMD_SHADER_OUTLINE_ENABLED,
    ATTR_MMD_MATERIAL_INDEX,
    ATTR_MMD_SOURCE_VERTEX_INDICES,
)
from mmd_tools.converters.mesh_material_properties import (
    PMX_DOUBLE_SIDED_DRAW_FLAG as _PMX_DOUBLE_SIDED_DRAW_FLAG,
    material_has_outline as _material_has_outline,
    material_is_double_sided as _material_is_double_sided,
)
from mmd_tools.converters.material_shader_parameters import (
    ATTR_MMD_EDGE_ALPHA,
    ATTR_MMD_DIFFUSE_ALPHA,
    iter_hardware_shader_values,
    material_base_parameter_values,
)
from mmd_tools.converters.mesh_texture_resolve import (
    resolve_pmx_toon_texture_path as _resolve_pmx_toon_texture_path,
    resolve_texture_path as _resolve_texture_path,
)
from mmd_tools.converters.material_morph_runtime import (
    BACKEND_DX11,
    BACKEND_STANDARD,
    detect_effective_vp2_draw_api,
    resolve_mmd_shader_backend,
)

LOGGER = get_logger(__name__)

_ALPHA_CAPABLE_TEXTURE_EXTENSIONS = {".png", ".tga", ".tif", ".tiff", ".dds"}
_SHADER_BACKEND_WARNED = set()


def _maya_node_exists(name: str) -> bool:
    """Best-effort object existence check used by deterministic allocators."""
    try:
        return bool(cmds.objExists(name))
    except Exception:
        return False


# DX11 transparency handling modes (drives technique selection).
TRANSPARENCY_MODE_OPAQUE = "opaque"
TRANSPARENCY_MODE_CUTOUT = "cutout"
TRANSPARENCY_MODE_BLEND = "blend"
TRANSPARENCY_MODES = (TRANSPARENCY_MODE_OPAQUE, TRANSPARENCY_MODE_CUTOUT, TRANSPARENCY_MODE_BLEND)
_OPAQUE_MATERIAL_ALPHA_THRESHOLD = 0.999
_ATTR_MMD_DOUBLE_SIDED = "mmdDoubleSided"
_MATERIAL_NODE_FAMILY_SUFFIXES = (
    "",
    "SG",
    "_file",
    "_place2dTexture",
    "_diffuseMultiply",
    "_ambientMultiply",
    "_opacityMultiply",
    "_texture",
    "_sphere_texture",
    "_toon_texture",
    "_materialMorphEval",
)
_DX11_TECHNIQUE_BY_RENDERING = {
    (TRANSPARENCY_MODE_OPAQUE, False): "MMDTechnique",
    (TRANSPARENCY_MODE_CUTOUT, False): "MMDTechnique",
    (TRANSPARENCY_MODE_BLEND, False): "MMDTechniqueTranslucent",
    (TRANSPARENCY_MODE_OPAQUE, True): "MMDTechniqueDoubleSided",
    (TRANSPARENCY_MODE_CUTOUT, True): "MMDTechniqueDoubleSided",
    (TRANSPARENCY_MODE_BLEND, True): "MMDTechniqueTranslucentDoubleSided",
}


def _normalized_material_opacity(material) -> float:
    """Return clamped PMX opacity, snapping near-opaque values to exactly one."""
    try:
        opacity = float(material.diffuse[3]) if len(material.diffuse) > 3 else 1.0
    except (AttributeError, TypeError, ValueError):
        return 1.0
    opacity = min(1.0, max(0.0, opacity))
    if opacity >= _OPAQUE_MATERIAL_ALPHA_THRESHOLD:
        return 1.0
    return opacity


def _ensure_shader_plugin(plugin_name) -> bool:
    """Return whether a shader plugin is already loaded without changing Maya state."""
    try:
        return bool(cmds.pluginInfo(plugin_name, query=True, loaded=True))
    except Exception:
        LOGGER.debug("Shader plugin '%s' is unavailable or not loaded", plugin_name, exc_info=True)
        return False


def _warn_shader_backend_once(key, message) -> None:
    """Emit a shader backend warning at most once per Maya Python session."""
    if key in _SHADER_BACKEND_WARNED:
        return
    _SHADER_BACKEND_WARNED.add(key)
    cmds.warning(message)


def effective_mmd_shader_backend() -> str:
    """Return the backend compatible with the live VP2 device.

    The persistent preference is never mutated here.  A stale explicit hardware
    preference is corrected only for this operation and warned once.
    """
    configured = str(
        settings.get(setting_keys.IMPORT_MODEL_MMD_SHADER_BACKEND, "auto") or "auto"
    ).strip().lower()
    vp2_api = detect_effective_vp2_draw_api()
    resolved = resolve_mmd_shader_backend(configured, vp2_api)
    if configured not in {"auto", resolved}:
        _warn_shader_backend_once(
            f"backend-corrected:{configured}:{vp2_api}:{resolved}",
            "MMD shader backend '%s' is incompatible with the active VP2 API "
            "'%s'; using '%s' for this operation without changing the saved preference."
            % (configured, vp2_api, resolved),
        )
    return resolved


_MIGRATED_HARDWARE_ATTRS = (
    "DiffuseColorRGB",
    "DiffuseColorA",
    "SpecularColor",
    "Shininess",
    "AmbientColor",
    "ToonCoordinateOffset",
    "EdgeSize",
    "Opacity",
    "SphereMode",
    "MainTexture",
    "SphereTexture",
    "ToonTexture",
    "HasMainTexture",
    "HasSphereTexture",
    "HasToonTexture",
    "MainTextureMultiply",
    "MainTextureAdd",
    "SphereTextureMultiply",
    "SphereTextureAdd",
    "ToonTextureMultiply",
    "ToonTextureAdd",
    "MMDLightDirection",
    "MMDLightColor",
)
_MIGRATED_MMD_ATTRS = (
    ATTR_MMD_MATERIAL,
    ATTR_MMD_MATERIAL_INDEX,
    ATTR_MMD_MATERIAL_NAME,
    ATTR_MMD_MATERIAL_NAME_EN,
    ATTR_MMD_DIFFUSE_COLOR,
    ATTR_MMD_DIFFUSE_ALPHA,
    ATTR_MMD_AMBIENT_COLOR,
    ATTR_MMD_SPECULAR_COLOR,
    ATTR_MMD_SHININESS,
    ATTR_MMD_TOON_TEXTURE_INDEX,
    ATTR_MMD_EDGE_FLAG,
    ATTR_MMD_SPHERE_MODE,
    ATTR_MMD_SPHERE_TEXTURE_INDEX,
    ATTR_MMD_TEXTURE_INDEX,
    ATTR_MMD_DRAW_FLAGS,
    ATTR_MMD_EDGE_COLOR,
    ATTR_MMD_EDGE_ALPHA,
    ATTR_MMD_EDGE_SIZE,
    ATTR_MMD_SHADER_OUTLINE_ENABLED,
    ATTR_MMD_MEMO,
    ATTR_MMD_SHARED_TOON_FLAG,
    "mmd_texture_path",
    "mmd_sphere_path",
    "mmdTransparencyMode",
    _ATTR_MMD_DOUBLE_SIDED,
)


def _copy_shader_attr_value(source, target, attr_name) -> None:
    """Copy one compatible value or incoming connection during backend replacement."""
    if not (
        cmds.attributeQuery(attr_name, node=source, exists=True)
        and cmds.attributeQuery(attr_name, node=target, exists=True)
    ):
        return
    source_plug = f"{source}.{attr_name}"
    target_plug = f"{target}.{attr_name}"
    incoming = cmds.listConnections(source_plug, source=True, destination=False, plugs=True) or []
    if incoming:
        cmds.connectAttr(incoming[0], target_plug, force=True)
        return
    value = cmds.getAttr(source_plug)
    attr_type = cmds.getAttr(source_plug, type=True)
    if value is None:
        return
    if isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], (list, tuple)):
        value = value[0]
    if attr_type == "string":
        cmds.setAttr(target_plug, value, type="string")
    elif isinstance(value, (list, tuple)):
        cmds.setAttr(target_plug, *value, type=attr_type)
    else:
        cmds.setAttr(target_plug, value)


def _copy_shader_backend_state(source, target) -> None:
    """Copy the small authored/connection contract shared by both MMD effects."""
    for attr_name in _MIGRATED_MMD_ATTRS:
        try:
            if not cmds.attributeQuery(attr_name, node=source, exists=True):
                continue
            if not cmds.attributeQuery(attr_name, node=target, exists=True):
                value = cmds.getAttr(f"{source}.{attr_name}")
                if isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], (list, tuple)):
                    value = value[0]
                maya_attribute_utils.set_custom_attributes(target, {attr_name: value})
            _copy_shader_attr_value(source, target, attr_name)
        except Exception:
            LOGGER.debug("Could not migrate custom shader attr '%s'", attr_name, exc_info=True)
    for attr_name in _MIGRATED_HARDWARE_ATTRS:
        try:
            _copy_shader_attr_value(source, target, attr_name)
        except Exception:
            LOGGER.debug("Could not migrate hardware shader attr '%s'", attr_name, exc_info=True)
    if cmds.nodeType(target) == "standardSurface":
        try:
            if cmds.attributeQuery("MainTexture", node=source, exists=True):
                incoming = cmds.listConnections(
                    f"{source}.MainTexture", source=True, destination=False, plugs=True
                ) or []
                if incoming:
                    cmds.connectAttr(incoming[0], f"{target}.baseColor", force=True)
        except Exception:
            LOGGER.debug("Could not migrate the main texture to standardSurface", exc_info=True)


def _create_backend_replacement(source, backend):
    if backend == BACKEND_STANDARD:
        return cmds.shadingNode("standardSurface", asShader=True, name=f"{source}__standard")
    node_type = "dx11Shader" if backend == BACKEND_DX11 else "GLSLShader"
    plugin_name = "dx11Shader" if backend == BACKEND_DX11 else "glslShader"
    if not _ensure_shader_plugin(plugin_name):
        raise RuntimeError(f"Required Maya shader plugin '{plugin_name}' is unavailable")
    replacement = None
    try:
        replacement = cmds.shadingNode(node_type, asShader=True, name=f"{source}__{backend}")
        shader_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "shaders")
        shader_name = "MMDShader.fx" if backend == BACKEND_DX11 else "MMDShader.ogsfx"
        shader_path = os.path.normpath(os.path.join(shader_dir, shader_name))
        if not _set_shader_attribute_checked(replacement, "shader", shader_path, "string"):
            raise RuntimeError(f"Could not assign {shader_name} to {node_type}")
        if backend == BACKEND_DX11:
            _ensure_dx11_uniform_attributes(replacement)
            _set_shader_attribute_checked(
                replacement,
                "DevicePixelRatio",
                maya_viewport_utils.get_device_pixel_ratio(),
                "float",
            )
            mode = get_transparency_mode(source)
            edge_enabled = False
            if cmds.attributeQuery(ATTR_MMD_SHADER_OUTLINE_ENABLED, node=source, exists=True):
                edge_enabled = bool(cmds.getAttr(f"{source}.{ATTR_MMD_SHADER_OUTLINE_ENABLED}"))
            technique = _technique_for_transparency(mode, edge_enabled, _shader_is_double_sided(source))
        else:
            _ensure_mmd_shader_uniform_attributes(replacement)
            technique = "Main"
        if not _set_shader_attribute_checked(replacement, "technique", technique, "string"):
            raise RuntimeError(f"Could not select {node_type} technique '{technique}'")
        return replacement
    except Exception:
        _delete_shader_node(replacement)
        raise


def ensure_material_shader_backend(shader: str) -> str:
    """Replace a mismatched MMD hardware shader for a presenter Apply operation.

    This is deliberately local to the material being edited; it is not a scene
    migration.  The replacement keeps the original node name, shading-engine
    assignment, common uniforms, texture inputs, and MMD custom attributes.
    """
    current_type = cmds.nodeType(shader)
    if current_type not in {"dx11Shader", "GLSLShader"}:
        return shader
    backend = effective_mmd_shader_backend()
    expected_type = "dx11Shader" if backend == BACKEND_DX11 else "GLSLShader"
    if backend == BACKEND_STANDARD:
        expected_type = "standardSurface"
    plugin_name = "dx11Shader" if backend == BACKEND_DX11 else "glslShader"
    if backend != BACKEND_STANDARD and not _ensure_shader_plugin(plugin_name):
        _warn_shader_backend_once(
            f"{backend}-plugin-not-loaded-update",
            f"{expected_type} plugin '{plugin_name}' is not loaded; replacing the edited hardware material with standardSurface for visibility.",
        )
        backend = BACKEND_STANDARD
        expected_type = "standardSurface"
    if current_type == expected_type:
        return shader

    replacement = None
    legacy = None
    destinations = []
    try:
        try:
            replacement = _create_backend_replacement(shader, backend)
        except Exception as exc:
            _warn_shader_backend_once(
                f"{backend}-replacement-failed",
                f"Could not create the active {expected_type} material ({exc}); using standardSurface for visibility.",
            )
            backend = BACKEND_STANDARD
            expected_type = "standardSurface"
            replacement = _create_backend_replacement(shader, backend)
        _copy_shader_backend_state(shader, replacement)
        destinations = cmds.listConnections(
            f"{shader}.outColor", source=False, destination=True, plugs=True
        ) or []
        legacy = cmds.rename(shader, f"{shader}__legacy_{current_type}")
        replacement = cmds.rename(replacement, shader)
        for destination in destinations:
            if destination.endswith(".surfaceShader"):
                cmds.connectAttr(f"{replacement}.outColor", destination, force=True)
    except Exception:
        if replacement and cmds.objExists(replacement):
            _delete_shader_node(replacement)
        if legacy and cmds.objExists(legacy) and not cmds.objExists(shader):
            cmds.rename(legacy, shader)
        if cmds.objExists(shader):
            for destination in destinations:
                if destination.endswith(".surfaceShader"):
                    cmds.connectAttr(f"{shader}.outColor", destination, force=True)
        raise
    _delete_shader_node(legacy)
    _warn_shader_backend_once(
        f"material-replaced:{current_type}:{expected_type}",
        f"Replaced incompatible {current_type} material with {expected_type} for the active VP2 API.",
    )
    return replacement


def _validate_shader_node(shader, expected_node_type):
    """Return (ok, reason) for a newly-created shader node."""
    if not shader or not cmds.objExists(shader):
        return False, "node was not created"

    actual_node_type = cmds.nodeType(shader)
    if actual_node_type != expected_node_type:
        return False, f"expected {expected_node_type}, got {actual_node_type}"

    if not cmds.attributeQuery("outColor", node=shader, exists=True):
        return False, "missing outColor attribute"

    return True, ""


def _delete_shader_node(shader) -> None:
    """Delete a rejected shader node without masking the original fallback path."""
    if not shader or not cmds.objExists(shader):
        return
    try:
        cmds.delete(shader)
    except Exception:
        LOGGER.debug("Failed to delete rejected shader node '%s'", shader, exc_info=True)


def _set_shader_attribute_checked(shader, attr_name, attr_value, attr_type) -> bool:
    """Set a shader attr through attribute utils and verify the attr did not silently fail."""
    if not cmds.attributeQuery(attr_name, node=shader, exists=True):
        LOGGER.debug("Shader '%s' has no '%s' attribute", shader, attr_name)
        return False

    try:
        maya_attribute_utils.set_attribute(shader, attr_name, attr_value, attr_type)
    except Exception:
        LOGGER.debug("Failed to set shader attribute '%s.%s'", shader, attr_name, exc_info=True)
        return False

    try:
        if not cmds.attributeQuery(attr_name, node=shader, exists=True):
            return False
        if attr_type in {"str", "string"}:
            return cmds.getAttr(f"{shader}.{attr_name}") == attr_value
    except Exception:
        LOGGER.debug("Failed to validate shader attribute '%s.%s'", shader, attr_name, exc_info=True)
        return False

    return True


def _set_mesh_double_sided(mesh_transform_or_shape, enabled: bool) -> None:
    """Set Maya mesh shape doubleSided from an MMD material draw flag.

    Use full DAG paths because material-split imports can briefly contain an
    unparented and a parented shape with the same short name.  OpenMaya's
    selection lookup must not choose between those paths while the mesh is
    being attached to the model hierarchy.
    """
    mesh_shapes = cmds.listRelatives(
        mesh_transform_or_shape,
        shapes=True,
        type="mesh",
        fullPath=True,
    ) or []
    for shape in mesh_shapes:
        maya_attribute_utils.set_attribute(shape, "doubleSided", 1 if enabled else 0, "bool")


def _classify_material_transparency(material, texture_path=None) -> str:
    """Simple diffuse-alpha-only fallback classification.

    Used only when the accurate per-material UV-region classification
    (``_precompute_transparency_modes`` -> :mod:`texture_alpha`) is unavailable
    (e.g. PMD, or texture decode failure). Texture-extension-based cutout
    detection is intentionally NOT done here: MMD atlases are routinely saved as
    32-bit RGBA even when opaque, so the extension/header is not a reliable
    signal. Defaulting such materials to opaque is the safe choice; the user can
    override per material in the Material tab.

    - ``blend``  : diffuse alpha < 1 (genuinely translucent).
    - ``opaque`` : everything else.
    """
    opacity = material.diffuse[3] if hasattr(material, "diffuse") and len(material.diffuse) > 3 else 1.0
    if opacity < 0.999:
        return TRANSPARENCY_MODE_BLEND
    return TRANSPARENCY_MODE_OPAQUE


def _technique_for_transparency(mode: str, edge_enabled: bool, double_sided: bool = False) -> str:
    """Select the DX11 technique for alpha mode and sidedness.

    ``edge_enabled`` remains accepted for scene/UI compatibility.  Opaque and
    cutout materials share the depth-writing techniques; genuinely blended
    materials use the explicit translucent technique so VP2 can composite them
    instead of discarding their authored alpha through a no-blend state.
    """
    mode = mode if mode in TRANSPARENCY_MODES else TRANSPARENCY_MODE_OPAQUE
    return _DX11_TECHNIQUE_BY_RENDERING[(mode, bool(double_sided))]


def _dx11_rendering_from_technique(technique: str) -> Tuple[str, bool, bool]:
    """Return legacy rendering metadata while treating edge as always enabled."""
    if "Translucent" in technique:
        mode = TRANSPARENCY_MODE_BLEND
    elif "Transparent" in technique:
        mode = TRANSPARENCY_MODE_CUTOUT
    else:
        mode = TRANSPARENCY_MODE_OPAQUE
    edge_enabled = "NoEdge" not in technique
    double_sided = technique.endswith("DoubleSided")
    return mode, edge_enabled, double_sided


def _shader_technique(shader: str) -> str:
    """Return a dx11Shader technique string if the attribute is present."""
    if cmds.attributeQuery("technique", node=shader, exists=True):
        return cmds.getAttr(f"{shader}.technique") or ""
    return ""


def _draw_flags_double_sided_from_node(node: str) -> Optional[bool]:
    """Read MMD draw flags from a node if present and parseable."""
    if not cmds.attributeQuery(ATTR_MMD_DRAW_FLAGS, node=node, exists=True):
        return None
    try:
        draw_flags = cmds.getAttr(f"{node}.{ATTR_MMD_DRAW_FLAGS}")
        return bool(int(draw_flags) & _PMX_DOUBLE_SIDED_DRAW_FLAG)
    except (TypeError, ValueError):
        return None


def _store_shader_double_sided_attr(shader: str, enabled: bool) -> None:
    """Persist dx11Shader double-sided state for UI re-application."""
    if not cmds.attributeQuery(_ATTR_MMD_DOUBLE_SIDED, node=shader, exists=True):
        maya_attribute_utils.set_custom_attributes(shader, {_ATTR_MMD_DOUBLE_SIDED: bool(enabled)})
    else:
        maya_attribute_utils.set_attribute(shader, _ATTR_MMD_DOUBLE_SIDED, bool(enabled), "bool")


def _shader_is_double_sided(shader: str, technique: Optional[str] = None) -> bool:
    """Return a dx11Shader's double-sided state, preferring authored draw flags."""
    draw_flags_state = _draw_flags_double_sided_from_node(shader)
    if draw_flags_state is not None:
        return draw_flags_state
    if cmds.attributeQuery(_ATTR_MMD_DOUBLE_SIDED, node=shader, exists=True):
        return bool(cmds.getAttr(f"{shader}.{_ATTR_MMD_DOUBLE_SIDED}"))
    technique = technique if technique is not None else _shader_technique(shader)
    _, _, double_sided = _dx11_rendering_from_technique(technique)
    return double_sided


def _material_uses_transparency(material, texture_path=None) -> bool:
    """Return whether the material is alpha-handled (cutout or blend) at all."""
    return _classify_material_transparency(material, texture_path) != TRANSPARENCY_MODE_OPAQUE


def _store_transparency_mode_attr(shader: str, mode: str) -> None:
    """Persist the chosen transparency mode on the shader (read by the UI)."""
    if not cmds.attributeQuery("mmdTransparencyMode", node=shader, exists=True):
        cmds.addAttr(shader, longName="mmdTransparencyMode", dataType="string")
    cmds.setAttr(f"{shader}.mmdTransparencyMode", mode, type="string")


def get_transparency_mode(shader: str) -> str:
    """Return the shader's stored transparency mode (defaults to opaque)."""
    if cmds.attributeQuery("mmdTransparencyMode", node=shader, exists=True):
        value = cmds.getAttr(f"{shader}.mmdTransparencyMode")
        if value in TRANSPARENCY_MODES:
            return value
    # Fall back to inferring from the currently assigned technique.
    mode, _, _ = _dx11_rendering_from_technique(_shader_technique(shader))
    return mode


def apply_transparency_mode(shader: str, mode: str) -> str:
    """Re-apply a transparency mode to an existing dx11Shader (UI entry point).

    Opaque/cutout modes retain depth-writing rendering, while blend selects the
    explicit translucent technique and its alpha-blended, depth-read-only pass.
    """
    if mode not in TRANSPARENCY_MODES:
        raise ValueError(f"Unknown transparency mode: {mode!r}")
    technique = _shader_technique(shader)
    _, edge_enabled, _ = _dx11_rendering_from_technique(technique)
    double_sided = _shader_is_double_sided(shader, technique)
    new_technique = _technique_for_transparency(mode, edge_enabled, double_sided)
    cmds.setAttr(f"{shader}.technique", new_technique, type="string")
    _store_transparency_mode_attr(shader, mode)
    _store_shader_double_sided_attr(shader, double_sided)
    return new_technique


def get_shader_outline_enabled(shader: str) -> bool:
    """Return whether the mandatory edge pass is enabled for this material."""
    if cmds.attributeQuery(ATTR_MMD_SHADER_OUTLINE_ENABLED, node=shader, exists=True):
        return bool(cmds.getAttr(f"{shader}.{ATTR_MMD_SHADER_OUTLINE_ENABLED}"))
    technique = _shader_technique(shader)
    _, edge_enabled, _ = _dx11_rendering_from_technique(technique)
    return bool(technique) and edge_enabled


def apply_shader_outline(shader: str, enabled: bool, edge_size: Optional[float] = None) -> str:
    """Keep the mandatory outline pass while applying its authored size."""
    mode = get_transparency_mode(shader)
    double_sided = _shader_is_double_sided(shader)
    new_technique = _technique_for_transparency(mode, True, double_sided)
    cmds.setAttr(f"{shader}.technique", new_technique, type="string")
    _store_shader_double_sided_attr(shader, double_sided)
    if cmds.attributeQuery("EdgeSize", node=shader, exists=True):
        if not enabled:
            cmds.setAttr(f"{shader}.EdgeSize", 0.0)
        elif edge_size is not None:
            cmds.setAttr(f"{shader}.EdgeSize", max(0.0, min(2.0, float(edge_size))))
    if not cmds.attributeQuery(ATTR_MMD_SHADER_OUTLINE_ENABLED, node=shader, exists=True):
        maya_attribute_utils.set_custom_attributes(shader, {ATTR_MMD_SHADER_OUTLINE_ENABLED: bool(enabled)})
    else:
        maya_attribute_utils.set_attribute(shader, ATTR_MMD_SHADER_OUTLINE_ENABLED, bool(enabled), "bool")
    return new_technique


def _is_degenerate_face(indices):
    """Return True if a triangle has duplicate vertex indices (zero-area)."""
    return len(set(indices)) < len(indices)


def bind_dx11_texture_file_node(shader, file_node, texture_attr, has_attr):
    """Compatibility wrapper for the shared dx11 texture slot binder."""
    return maya_material_utils.bind_dx11_texture_file_node(
        shader,
        file_node,
        texture_attr,
        has_attr,
        cmds_module=cmds,
        set_attribute_func=maya_attribute_utils.set_attribute,
    )


def _ensure_mmd_shader_uniform_attributes(shader_node, include_device_pixel_ratio=False):
    """MMD シェーダーで uniform 属性がない場合に補完する。

    Maya の standalone 環境では dx11Shader / GLSLShader が OGSFX/uniform を
    自動生成しないことがあるため、事前に属性を作成しておく。
    """
    import maya.api.OpenMaya as om

    uniforms = [
        # Legacy compound kept for dx11 standalone/tests; HLSL/OGSFX RGB morph
        # targets use DiffuseColorRGB + DiffuseColorA (alpha preserved separate).
        ("DiffuseColor", om.MFnNumericData.kDouble, 4, True, (0.8, 0.8, 0.8, 1.0)),
        ("DiffuseColorRGB", om.MFnNumericData.kDouble, 3, True, (0.8, 0.8, 0.8)),
        ("DiffuseColorA", om.MFnNumericData.kDouble, 1, False, 1.0),
        ("SpecularColor", om.MFnNumericData.kDouble, 3, True, (0.5, 0.5, 0.5)),
        ("AmbientColor", om.MFnNumericData.kDouble, 3, True, (0.3, 0.3, 0.3)),
        # Explicit ramp calibration shared by the DX11 and OGSFX effects.
        ("ToonCoordinateOffset", om.MFnNumericData.kDouble, 1, False, 0.55),
        ("EdgeColor", om.MFnNumericData.kDouble, 4, True, (0.0, 0.0, 0.0, 1.0)),
        ("EdgeColorRGB", om.MFnNumericData.kDouble, 3, True, (0.0, 0.0, 0.0)),
        ("EdgeColorA", om.MFnNumericData.kDouble, 1, False, 1.0),
        ("Shininess", om.MFnNumericData.kDouble, 1, False, 20.0),
        ("EdgeSize", om.MFnNumericData.kDouble, 1, False, 1.0),
        ("SphereMode", om.MFnNumericData.kLong, 1, False, 0),
        ("Opacity", om.MFnNumericData.kDouble, 1, False, 1.0),
        ("HasMainTexture", om.MFnNumericData.kLong, 1, False, 0),
        ("HasSphereTexture", om.MFnNumericData.kLong, 1, False, 0),
        ("HasToonTexture", om.MFnNumericData.kLong, 1, False, 0),
        # MMD ライト（コントローラ駆動の唯一の光源）。GUI では dx11Shader が .fx
        # から自動生成するが、standalone/テストでは生成されないため補完しておき、
        # 後段のコントローラ結線が失敗しないようにする。
        ("MMDLightDirection", om.MFnNumericData.kDouble, 3, False, (-0.5, -1.0, -1.0)),
        ("MMDLightColor", om.MFnNumericData.kDouble, 3, True, (154.0 / 255.0,) * 3),
        ("MmdControllerLightVector", om.MFnNumericData.kDouble, 3, False, (-0.5, -1.0, -1.0)),
        ("MmdControllerLightRgb", om.MFnNumericData.kDouble, 3, True, (154.0 / 255.0,) * 3),
    ]
    if include_device_pixel_ratio:
        uniforms.append(("DevicePixelRatio", om.MFnNumericData.kDouble, 1, False, 1.0))

    try:
        sel = om.MSelectionList()
        sel.add(shader_node)
        node = sel.getDependNode(0)
    except Exception as e:
        LOGGER.warning(
            "Failed to setup fallback uniform attributes for shader '%s': %s",
            shader_node,
            e,
            exc_info=True,
        )
        return

    dep_fn = om.MFnDependencyNode(node)
    for name, child_type, child_count, is_color, default_value in uniforms:
        if dep_fn.hasAttribute(name):
            continue

        try:
            if child_count == 1:
                attr_fn = om.MFnNumericAttribute()
                attr = attr_fn.create(name, name, child_type, default_value)
                attr_fn.keyable = True
                attr_fn.storable = True
                attr_fn.writable = True
                dep_fn.addAttribute(attr)
            elif child_count <= 3:
                children = []
                for i in range(child_count):
                    child_fn = om.MFnNumericAttribute()
                    child = child_fn.create(
                        f"{name}{i}",
                        f"{name}{i}",
                        child_type,
                        default_value[i],
                    )
                    child_fn.keyable = True
                    child_fn.storable = True
                    child_fn.writable = True
                    children.append(child)

                parent_fn = om.MFnNumericAttribute()
                parent = parent_fn.create(name, name, *children)
                parent_fn.keyable = True
                parent_fn.storable = True
                parent_fn.writable = True
                if is_color:
                    parent_fn.usedAsColor = True
                dep_fn.addAttribute(parent)
            else:
                parent_fn = om.MFnCompoundAttribute()
                parent = parent_fn.create(name, name)
                parent_fn.keyable = True
                parent_fn.storable = True
                parent_fn.writable = True
                if is_color:
                    parent_fn.usedAsColor = True

                for i in range(child_count):
                    child_fn = om.MFnNumericAttribute()
                    child = child_fn.create(f"{name}{i}", f"{name}{i}", child_type, default_value[i])
                    child_fn.keyable = True
                    child_fn.storable = True
                    child_fn.writable = True
                    parent_fn.addChild(child)
                dep_fn.addAttribute(parent)
        except Exception as e:
            # 属性作成に失敗しても後続の set_attribute が失敗しないように継続するが、
            # 失敗は必ずログに残す。
            LOGGER.warning(
                "Failed to create fallback uniform attribute '%s' on shader '%s': %s",
                name,
                shader_node,
                e,
                exc_info=True,
            )


def _ensure_dx11_uniform_attributes(shader_node):
    """Backward-compatible alias for dynamic uniform attr creation."""
    _ensure_mmd_shader_uniform_attributes(shader_node, include_device_pixel_ratio=True)


_GLSL_DIFFUSE_CONTRACT_MARKER = "mmdDiffuseRgbContractVersion"


def _has_glsl_diffuse_contract_marker(shader_node):
    try:
        return cmds.attributeQuery(_GLSL_DIFFUSE_CONTRACT_MARKER, node=shader_node, exists=True) and bool(
            cmds.getAttr(f"{shader_node}.{_GLSL_DIFFUSE_CONTRACT_MARKER}")
        )
    except Exception:
        return False


def _mark_glsl_diffuse_contract(shader_node):
    """Mark GLSL diffuse RGB/A data as authoritative and migration-complete."""
    try:
        if not cmds.attributeQuery(_GLSL_DIFFUSE_CONTRACT_MARKER, node=shader_node, exists=True):
            cmds.addAttr(shader_node, longName=_GLSL_DIFFUSE_CONTRACT_MARKER, attributeType="long", defaultValue=0)
        cmds.setAttr(f"{shader_node}.{_GLSL_DIFFUSE_CONTRACT_MARKER}", 1)
        return True
    except Exception:
        LOGGER.warning("Failed to mark GLSL diffuse contract for '%s'", shader_node, exc_info=True)
        return False


def _migrate_legacy_glsl_diffuse(shader_node, legacy):
    """Transactionally write and verify the split GLSL diffuse contract."""
    if len(legacy) < 4:
        return False
    rgb_plug = f"{shader_node}.DiffuseColorRGB"
    alpha_plug = f"{shader_node}.DiffuseColorA"
    if not _is_dx11_generated_uniform_writable(rgb_plug) or not _is_dx11_generated_uniform_writable(alpha_plug):
        return False
    try:
        original_rgb = list(cmds.getAttr(rgb_plug)[0])
        original_alpha = float(cmds.getAttr(alpha_plug))
        marker_existed = cmds.attributeQuery(_GLSL_DIFFUSE_CONTRACT_MARKER, node=shader_node, exists=True)
        original_marker_value = (
            cmds.getAttr(f"{shader_node}.{_GLSL_DIFFUSE_CONTRACT_MARKER}") if marker_existed else None
        )
    except Exception:
        LOGGER.warning("Could not snapshot legacy GLSL diffuse for '%s'; migration skipped", shader_node)
        return False

    def rollback():
        rollback_ok = True
        try:
            cmds.setAttr(rgb_plug, original_rgb[0], original_rgb[1], original_rgb[2], type="double3")
        except Exception:
            rollback_ok = False
        try:
            cmds.setAttr(alpha_plug, original_alpha)
        except Exception:
            rollback_ok = False
        try:
            marker_exists_now = cmds.attributeQuery(
                _GLSL_DIFFUSE_CONTRACT_MARKER, node=shader_node, exists=True
            )
            if marker_existed:
                if not marker_exists_now:
                    raise RuntimeError("pre-existing marker attribute was removed")
                cmds.setAttr(f"{shader_node}.{_GLSL_DIFFUSE_CONTRACT_MARKER}", original_marker_value)
            elif marker_exists_now:
                cmds.deleteAttr(f"{shader_node}.{_GLSL_DIFFUSE_CONTRACT_MARKER}")
        except Exception:
            rollback_ok = False
        if not rollback_ok:
            LOGGER.error(
                "Rollback failed after legacy GLSL diffuse migration error for '%s'; scene may contain partial changes",
                shader_node,
            )

    try:
        cmds.setAttr(rgb_plug, legacy[0], legacy[1], legacy[2], type="double3")
        cmds.setAttr(alpha_plug, legacy[3])
        rgb = list(cmds.getAttr(rgb_plug)[0])
        alpha = float(cmds.getAttr(alpha_plug))
        expected = [float(value) for value in legacy[:3]]
        if len(rgb) != 3 or any(abs(float(actual) - wanted) > 1e-6 for actual, wanted in zip(rgb, expected)):
            raise RuntimeError("RGB read-back mismatch")
        if abs(alpha - float(legacy[3])) > 1e-6:
            raise RuntimeError("alpha read-back mismatch")
        if not _mark_glsl_diffuse_contract(shader_node):
            raise RuntimeError("contract marker write failed")
        return True
    except Exception:
        LOGGER.warning("Failed to migrate legacy GLSL diffuse for '%s'", shader_node, exc_info=True)
        rollback()
        return False


def _legacy_glsl_diffuse_is_driven(shader_node):
    """Return whether legacy diffuse data has inputs that cannot be frozen safely."""
    attrs = ["DiffuseColor", "DiffuseColorR", "DiffuseColorG", "DiffuseColorB", "DiffuseColorA"]
    for attr in attrs:
        try:
            if not cmds.attributeQuery(attr, node=shader_node, exists=True):
                continue
            if cmds.listConnections(
                f"{shader_node}.{attr}", source=True, destination=False, plugs=True
            ) or []:
                return True
        except Exception:
            # Unknown connection state is not safe for destructive migration.
            return True
    return False


def _glsl_split_diffuse_matches(shader_node, rgb_expected, alpha_expected):
    """Verify writable, unconnected GLSL RGB/A plugs contain expected values."""
    rgb_plug = f"{shader_node}.DiffuseColorRGB"
    alpha_plug = f"{shader_node}.DiffuseColorA"
    if not _is_dx11_generated_uniform_writable(rgb_plug) or not _is_dx11_generated_uniform_writable(alpha_plug):
        return False
    try:
        rgb = list(cmds.getAttr(rgb_plug)[0])
        alpha = float(cmds.getAttr(alpha_plug))
        expected = [float(value) for value in rgb_expected]
        return (
            len(rgb) == 3
            and all(abs(float(actual) - wanted) <= 1e-6 for actual, wanted in zip(rgb, expected))
            and abs(alpha - float(alpha_expected)) <= 1e-6
        )
    except Exception:
        return False


def _is_dx11_generated_uniform_writable(plug):
    """Return False for generated dx11Shader attrs driven by Maya/VP2 internals."""
    try:
        if cmds.getAttr(plug, lock=True):
            LOGGER.debug("Skipping locked dx11Shader generated uniform '%s'", plug)
            return False
    except Exception:
        pass
    try:
        if cmds.listConnections(plug, source=True, destination=False, plugs=True) or []:
            LOGGER.debug("Skipping connected dx11Shader generated uniform '%s'", plug)
            return False
    except Exception:
        pass
    return True


def _set_dx11_color_uniform(shader_node, attr_name, values):
    """Set dx11Shader color uniforms across Maya's generated child attrs.

    Maya GUI dx11Shader exposes HLSL float4 colors as a mixed compound plus
    generated attrs such as DiffuseColorRGB/DiffuseColorA.  Setting only the
    parent compound can leave the generated RGB attr at black on real VP2.
    """
    color = list(values)
    if len(color) < 3:
        return

    rgb = color[:3]
    alpha = color[3] if len(color) > 3 else None
    attr_type = "double4" if alpha is not None else "double3"

    if cmds.attributeQuery(attr_name, node=shader_node, exists=True):
        maya_attribute_utils.set_attribute(shader_node, attr_name, color, attr_type)

    rgb_attr = f"{attr_name}RGB"
    if cmds.attributeQuery(rgb_attr, node=shader_node, exists=True):
        rgb_plug = "{}.{}".format(shader_node, rgb_attr)
        rgb_child_plugs = [
            "{}.{}{}".format(shader_node, attr_name, suffix)
            for suffix in ("R", "G", "B")
            if cmds.attributeQuery(f"{attr_name}{suffix}", node=shader_node, exists=True)
        ]
        if _is_dx11_generated_uniform_writable(rgb_plug) and all(
            _is_dx11_generated_uniform_writable(child_plug) for child_plug in rgb_child_plugs
        ):
            try:
                cmds.setAttr(rgb_plug, rgb[0], rgb[1], rgb[2], type="double3")
            except Exception:
                LOGGER.warning(
                    "Failed to set dx11Shader RGB uniform '%s'",
                    rgb_plug,
                    exc_info=True,
                )

    for suffix, value in zip(("R", "G", "B"), rgb):
        child_attr = f"{attr_name}{suffix}"
        if cmds.attributeQuery(child_attr, node=shader_node, exists=True):
            child_plug = "{}.{}".format(shader_node, child_attr)
            if not _is_dx11_generated_uniform_writable(child_plug):
                continue
            try:
                cmds.setAttr(child_plug, value)
            except Exception:
                LOGGER.debug("Failed to set dx11Shader child uniform '%s'", child_plug, exc_info=True)

    alpha_attr = f"{attr_name}A"
    if alpha is not None and cmds.attributeQuery(alpha_attr, node=shader_node, exists=True):
        alpha_plug = "{}.{}".format(shader_node, alpha_attr)
        if not _is_dx11_generated_uniform_writable(alpha_plug):
            return
        try:
            cmds.setAttr(alpha_plug, alpha)
        except Exception:
            LOGGER.debug("Failed to set dx11Shader alpha uniform '%s'", alpha_plug, exc_info=True)


def migrate_legacy_glsl_diffuse_contracts():
    """Migrate only unambiguous legacy MMD GLSL nodes in the current scene."""
    migrated = 0
    for shader in cmds.ls(type="GLSLShader") or []:
        if not shader or not cmds.objExists(shader) or cmds.nodeType(shader) != "GLSLShader":
            continue
        if not (
            not _has_glsl_diffuse_contract_marker(shader)
            and cmds.attributeQuery(ATTR_MMD_MATERIAL, node=shader, exists=True)
            and not cmds.attributeQuery(ATTR_MMD_DIFFUSE_COLOR, node=shader, exists=True)
            and not cmds.attributeQuery("Opacity", node=shader, exists=True)
            and cmds.attributeQuery("DiffuseColor", node=shader, exists=True)
            and cmds.attributeQuery("DiffuseColorRGB", node=shader, exists=True)
            and cmds.attributeQuery("DiffuseColorA", node=shader, exists=True)
        ):
            continue
        if _legacy_glsl_diffuse_is_driven(shader):
            LOGGER.warning(
                "Skipping automatic legacy GLSL diffuse migration for driven shader '%s'; manual migration required",
                shader,
            )
            continue
        try:
            legacy = list(cmds.getAttr(f"{shader}.DiffuseColor")[0])
            if _migrate_legacy_glsl_diffuse(shader, legacy):
                migrated += 1
        except Exception:
            LOGGER.warning("Failed to migrate legacy GLSL DiffuseColor for '%s'", shader, exc_info=True)
    return migrated


def sync_dx11_generated_uniforms(shader_nodes=None):
    """Synchronize generated dx11Shader effect attrs after import.

    In Maya GUI, dx11Shader creates attrs like DiffuseColorRGB only after the
    .fx file has been evaluated by VP2.  During material construction those
    attrs may not exist yet, so this post-import pass copies the MMD custom
    attributes into the generated effect attrs once they are present.
    """
    synced = 0
    if shader_nodes is None:
        synced += migrate_legacy_glsl_diffuse_contracts()
    shaders = (
        list(shader_nodes)
        if shader_nodes is not None
        else list(cmds.ls(type="dx11Shader") or []) + list(cmds.ls(type="GLSLShader") or [])
    )
    for shader in shaders:
        if not shader or not cmds.objExists(shader) or cmds.nodeType(shader) not in ("dx11Shader", "GLSLShader"):
            continue
        if cmds.attributeQuery(ATTR_MMD_DIFFUSE_COLOR, node=shader, exists=True):
            try:
                diffuse = list(cmds.getAttr(f"{shader}.{ATTR_MMD_DIFFUSE_COLOR}")[0])
                # PMX alpha is authored in the split diffuse-alpha custom
                # attribute.  Opacity is only a neutral runtime multiplier;
                # using it as the legacy DiffuseColorA source would reapply
                # the material alpha in ``texColor.a * DiffuseColorA * Opacity``.
                if cmds.attributeQuery(ATTR_MMD_DIFFUSE_ALPHA, node=shader, exists=True):
                    diffuse_alpha = float(cmds.getAttr(f"{shader}.{ATTR_MMD_DIFFUSE_ALPHA}"))
                elif cmds.attributeQuery("DiffuseColorA", node=shader, exists=True):
                    diffuse_alpha = float(cmds.getAttr(f"{shader}.DiffuseColorA"))
                else:
                    diffuse_alpha = 1.0
                    if cmds.attributeQuery("Opacity", node=shader, exists=True):
                        diffuse_alpha = float(cmds.getAttr(f"{shader}.Opacity"))
                if cmds.attributeQuery("Opacity", node=shader, exists=True):
                    incoming_opacity = cmds.listConnections(
                        f"{shader}.Opacity", source=True, destination=False, plugs=True
                    ) or []
                    if not incoming_opacity:
                        cmds.setAttr(f"{shader}.Opacity", 1.0)
                _set_dx11_color_uniform(shader, "DiffuseColor", diffuse + [diffuse_alpha])
                if cmds.nodeType(shader) == "GLSLShader" and _glsl_split_diffuse_matches(
                    shader, diffuse, diffuse_alpha
                ):
                    _mark_glsl_diffuse_contract(shader)
                synced += 1
            except Exception:
                LOGGER.warning("Failed to sync dx11 DiffuseColor uniforms for '%s'", shader, exc_info=True)

        if cmds.attributeQuery(ATTR_MMD_EDGE_COLOR, node=shader, exists=True):
            try:
                edge_color = list(cmds.getAttr(f"{shader}.{ATTR_MMD_EDGE_COLOR}")[0])
                edge_alpha = 1.0
                if cmds.attributeQuery(ATTR_MMD_EDGE_ALPHA, node=shader, exists=True):
                    edge_alpha = float(cmds.getAttr(f"{shader}.{ATTR_MMD_EDGE_ALPHA}"))
                elif cmds.attributeQuery("EdgeColorA", node=shader, exists=True):
                    edge_alpha = float(cmds.getAttr(f"{shader}.EdgeColorA"))
                _set_dx11_color_uniform(shader, "EdgeColor", edge_color + [edge_alpha])
            except Exception:
                LOGGER.warning("Failed to sync dx11 EdgeColor uniforms for '%s'", shader, exc_info=True)

        if (
            cmds.attributeQuery(ATTR_MMD_SPHERE_MODE, node=shader, exists=True)
            and cmds.attributeQuery("SphereMode", node=shader, exists=True)
        ):
            try:
                sphere_mode = int(cmds.getAttr(f"{shader}.{ATTR_MMD_SPHERE_MODE}"))
                cmds.setAttr(f"{shader}.SphereMode", sphere_mode)
                synced += 1
            except Exception:
                LOGGER.warning("Failed to sync dx11 SphereMode uniform for '%s'", shader, exc_info=True)

        if (
            cmds.attributeQuery(ATTR_MMD_EDGE_SIZE, node=shader, exists=True)
            and cmds.attributeQuery("EdgeSize", node=shader, exists=True)
        ):
            try:
                if cmds.attributeQuery(ATTR_MMD_SHADER_OUTLINE_ENABLED, node=shader, exists=True):
                    outline_enabled = bool(cmds.getAttr(f"{shader}.{ATTR_MMD_SHADER_OUTLINE_ENABLED}"))
                else:
                    outline_enabled = get_shader_outline_enabled(shader)
                edge_size = float(cmds.getAttr(f"{shader}.{ATTR_MMD_EDGE_SIZE}")) if outline_enabled else 0.0
                cmds.setAttr(f"{shader}.EdgeSize", edge_size)
                synced += 1
            except Exception:
                LOGGER.warning("Failed to sync dx11 EdgeSize uniform for '%s'", shader, exc_info=True)

    return synced


class MeshConverter:
    """
    MMDのメッシュデータをMayaのメッシュノードに変換するクラス。
    """

    def __init__(self, pmx_filepath="", scale: float = 1.0):
        """
        コンストラクタ。

        Args:
            pmx_filepath (str): 読み込むPMXファイルのパス。
            scale (float): PMX座標からMaya座標へ変換する際のインポートスケール。
        """
        self.logger = get_logger(__name__)
        self.created_shaders = []
        self.created_texture_file_nodes = []
        self.has_dx11_shaders = False
        self.has_glsl_shaders = False
        self.unresolved_texture_count = 0
        self.unresolved_textures = []
        self.model_filepath = pmx_filepath
        self.scale = float(scale)
        # material_index -> transparency mode ("opaque"/"cutout"/"blend"),
        # precomputed from per-material UV-region texture alpha (atlas-safe).
        self._transparency_modes = {}
        self._material_name_by_index = {}
        self._material_name_used = self._scene_name_set()
        self._material_name_scene_check = True
        # The optional C++ command is preferred when the native plugin is
        # already loaded.  The O(V+F) Python topology builder remains the
        # compatibility path for installations that only load the Python
        # plugin.
        self._use_cpp_uv_weld = False
        self.profile = {
            "mesh_create_sec": 0.0,
            "material_create_sec": 0.0,
            "material_assign_sec": 0.0,
            "parent_sec": 0.0,
            "created_mesh_count": 0,
            "source_vertex_count": 0,
            "mesh_vertex_slots_estimated": 0,
            "uv_welded_vertex_count": 0,
            "face_count": 0,
            "material_count_processed": 0,
            "unresolved_texture_count": 0,
            "unresolved_textures": [],
        }
        if pmx_filepath:
            self.texture_dir = os.path.dirname(pmx_filepath)

    @staticmethod
    def _scene_name_set():
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

    def _allocate_material_name(self, material, material_index=None):
        """Allocate one safe base for a material and all derived node names."""
        key = material_index if material_index is not None else id(material)
        existing = getattr(self, "_material_name_by_index", None)
        if existing is None:
            existing = self._material_name_by_index = {}
        if key in existing:
            return existing[key]

        used = getattr(self, "_material_name_used", None)
        check_scene = bool(getattr(self, "_material_name_scene_check", False))
        if used is None:
            used = self._material_name_used = set()
        raw_name = material.get_name() if hasattr(material, "get_name") else getattr(material, "name", "")
        fallback = f"material_{material_index if material_index is not None else len(existing)}"
        while True:
            candidate = maya_name_utils.sanitize_unique_name(raw_name, used, fallback=fallback)
            if not any(
                candidate + suffix in used
                or (check_scene and _maya_node_exists(candidate + suffix))
                for suffix in _MATERIAL_NODE_FAMILY_SUFFIXES[1:]
            ):
                break

        used.update(candidate + suffix for suffix in _MATERIAL_NODE_FAMILY_SUFFIXES)
        existing[key] = candidate
        return candidate

    def _mmd_vertex_to_maya(self, position) -> Tuple[float, float, float]:
        """Convert a PMX vertex position to Maya object space with import scale."""
        return (
            float(position[0]) * self.scale,
            float(position[1]) * self.scale,
            -float(position[2]) * self.scale,
        )

    def _cpp_uv_weld_command_available(self) -> bool:
        """Return whether the optional native UV-weld command is loaded."""
        if not self.model_filepath:
            return False
        try:
            return callable(getattr(cmds, "mmdWeldUvSeamVertices", None))
        except Exception:
            return False

    def _run_cpp_uv_weld(self, mesh_node: str, source_vertex_indices) -> Optional[int]:
        """Run the C++ topology command and return merged vertex count.

        The source-index attribute is written before entering C++ so material
        split meshes can map their compact local vertices back to PMX source
        vertices.  The command updates the same attribute after welding; skin
        and morph converters then consume that mapping without another Python
        vertex walk.
        """
        if not self._use_cpp_uv_weld:
            return None

        try:
            if not cmds.attributeQuery(ATTR_MMD_SOURCE_VERTEX_INDICES, node=mesh_node, exists=True):
                maya_attribute_utils.add_typed_attribute(
                    mesh_node,
                    ATTR_MMD_SOURCE_VERTEX_INDICES,
                    "longArray",
                )
            maya_attribute_utils.set_attribute(
                mesh_node,
                ATTR_MMD_SOURCE_VERTEX_INDICES,
                [int(index) for index in source_vertex_indices],
                "longArray",
            )
            result = cmds.mmdWeldUvSeamVertices(
                m=mesh_node,
                f=self.model_filepath,
            )
            if isinstance(result, (list, tuple)) and len(result) >= 3:
                old_count = int(result[1])
                new_count = int(result[2])
                return max(0, old_count - new_count)
            return 0
        except Exception as exc:
            self.logger.warning(
                "C++ UV seam weld failed for '%s'; keeping the native-created topology: %s",
                mesh_node,
                exc,
            )
            return None

    @staticmethod
    def _vertex_deformation_key(vertex) -> tuple:
        """Return the PMX data that must remain per Maya vertex.

        UVs and authored normals are intentionally excluded. Maya can assign
        UVs and normals per face corner, while skin weights are stored per
        geometric vertex. Keeping the deformation payload in the key prevents
        a topology weld from changing the imported skin result.
        """
        return (
            int(getattr(vertex, "weight_transform_type", 0)),
            tuple(int(index) for index in getattr(vertex, "bone_indices", []) or []),
            tuple(float(weight) for weight in getattr(vertex, "bone_weights", []) or []),
            tuple(float(value) for value in getattr(vertex, "sdef_c", ()) or ()),
            tuple(float(value) for value in getattr(vertex, "sdef_r0", ()) or ()),
            tuple(float(value) for value in getattr(vertex, "sdef_r1", ()) or ()),
        )

    def _build_vertex_weld_keys(self, all_vertices, all_faces, all_materials, morphs) -> Dict[int, tuple]:
        """Build conservative keys for welding UV-split PMX vertices.

        PMX stores UVs on vertices, so a UV seam commonly duplicates the same
        position. Maya supports per-face-vertex UV assignments and therefore
        does not need those geometric duplicates. Vertices with authored
        vertex morphs are kept separate until a source-to-local fan-out map is
        available; merging them here would otherwise make a morph target one
        of the seam copies only.
        """
        material_sets = [set() for _ in all_vertices]
        face_offset = 0
        for material_index, material in enumerate(all_materials or []):
            face_count = max(0, int(getattr(material, "face_count", 0) or 0) // 3)
            for face in all_faces[face_offset : face_offset + face_count]:
                for raw_index in getattr(face, "indices", ()) or ():
                    source_index = int(raw_index)
                    if 0 <= source_index < len(material_sets):
                        material_sets[source_index].add(material_index)
            face_offset += face_count

        morph_vertex_indices = set()
        for morph in morphs or []:
            if getattr(morph, "morph_type", None) != PmxMorphType.VertexMorph:
                continue
            for offset in getattr(morph, "offsets", ()) or ():
                try:
                    morph_vertex_indices.add(int(offset["vertex_index"]))
                except (KeyError, TypeError, ValueError):
                    continue

        keys = {}
        for source_index, vertex in enumerate(all_vertices):
            if source_index in morph_vertex_indices:
                # A unique key keeps every morph-bearing source vertex local.
                keys[source_index] = ("morph_source", source_index)
                continue

            additional_uvs = tuple(
                tuple(float(value) for value in uv)
                for uv in getattr(vertex, "additional_uvs", ()) or ()
            )
            keys[source_index] = (
                "weldable_source",
                tuple(float(value) for value in vertex.position),
                self._vertex_deformation_key(vertex),
                float(getattr(vertex, "edge_magnification", 1.0)),
                additional_uvs,
                tuple(sorted(material_sets[source_index])),
            )
        return keys

    def _build_maya_mesh_data(
        self,
        all_vertices,
        face_records,
        active_source_indices=None,
        weld_keys=None,
    ) -> dict:
        """Build Maya topology with independent per-corner UV connections.

        ``face_records`` contains ``(material_index, face)`` pairs. Source
        vertices with the same conservative weld key share one geometric Maya
        vertex, while each source vertex keeps its own UV connection and
        authored normal on every face corner.
        """
        if weld_keys is None:
            weld_keys = {
                source_index: ("source", source_index)
                for source_index in range(len(all_vertices))
            }

        candidates = list(active_source_indices) if active_source_indices is not None else list(range(len(all_vertices)))
        local_source_indices = []
        local_by_key = {}
        source_to_local = {}
        source_to_uv = {}
        uv_by_key = {}
        uvs = []

        def add_source(source_index):
            source_index = int(source_index)
            if source_index in source_to_local:
                return source_to_local[source_index]
            if source_index < 0 or source_index >= len(all_vertices):
                raise IndexError(f"PMX vertex index out of range: {source_index}")

            weld_key = weld_keys.get(source_index, ("source", source_index))
            if weld_key in local_by_key:
                local_index = local_by_key[weld_key]
            else:
                local_index = len(local_source_indices)
                local_by_key[weld_key] = local_index
                local_source_indices.append(source_index)
            source_to_local[source_index] = local_index

            uv = all_vertices[source_index].uv
            uv_key = (float(uv[0]), 1.0 - float(uv[1]))
            if uv_key not in uv_by_key:
                uv_index = len(uvs) // 2
                uv_by_key[uv_key] = uv_index
                uvs.extend(uv_key)
            else:
                uv_index = uv_by_key[uv_key]
            source_to_uv[source_index] = uv_index
            return local_index

        for source_index in candidates:
            add_source(source_index)

        face_connects = []
        face_counts = []
        face_uv_connects = []
        face_normals = []
        material_face_ranges = {}
        for material_index, face in face_records:
            source_indices = [int(index) for index in (getattr(face, "indices", ()) or ())][::-1]
            local_indices = [add_source(source_index) for source_index in source_indices]
            if _is_degenerate_face(local_indices):
                continue

            face_connects.extend(local_indices)
            face_counts.append(len(local_indices))
            face_uv_connects.extend(source_to_uv[source_index] for source_index in source_indices)
            face_normals.extend(
                (
                    float(all_vertices[source_index].normal[0]),
                    float(all_vertices[source_index].normal[1]),
                    -float(all_vertices[source_index].normal[2]),
                )
                for source_index in source_indices
            )
            start, _ = material_face_ranges.get(material_index, (len(face_counts) - 1, len(face_counts) - 1))
            material_face_ranges[material_index] = (start, len(face_counts))

        return {
            "vertices": [self._mmd_vertex_to_maya(all_vertices[source_index].position) for source_index in local_source_indices],
            "normals": face_normals,
            "uvs": uvs,
            "face_counts": face_counts,
            "face_connects": face_connects,
            "face_uv_connects": face_uv_connects,
            "source_vertex_indices": local_source_indices,
            "source_to_local": source_to_local,
            "material_face_ranges": material_face_ranges,
            "welded_vertex_count": len(candidates) - len(local_source_indices),
        }

    def _add_profile_time(self, key: str, start: float) -> None:
        """Accumulate timing in the converter profile."""
        self.profile[key] = round(float(self.profile.get(key, 0.0)) + time.perf_counter() - start, 6)

    def _record_created_shader(self, shader: str) -> None:
        """Record a created shader and its hardware backend."""
        self.created_shaders.append(shader)
        try:
            if shader and cmds.objExists(shader):
                shader_type = cmds.nodeType(shader)
                if shader_type == "dx11Shader":
                    self.has_dx11_shaders = True
                elif shader_type == "GLSLShader":
                    self.has_glsl_shaders = True
        except Exception as exc:
            self.logger.debug("Failed to record created shader %s: %s", shader, exc)

    def _record_unresolved_texture_issue(
        self,
        *,
        file_node,
        shader,
        material,
        original_path,
        current_path,
    ) -> dict:
        """Record one texture issue in a Qt-independent import report."""

        reason = classify_unreadable_file_texture_path(current_path) or "unreadable_path"
        source, source_reason = find_resolvable_source(original_path, self.model_filepath)
        issue = {
            "file_node": file_node,
            "material": shader,
            "material_name": getattr(material, "name", "") or shader,
            "original_path": os.fspath(original_path) if original_path else "",
            "current_path": os.fspath(current_path) if current_path else "",
            "reason": reason,
            "resolvable": source is not None,
            "source_path": str(source) if source is not None else "",
            "search_candidates": build_texture_source_candidates(original_path, self.model_filepath),
            "path_diagnostics": build_texture_path_diagnostics(
                original_path=original_path,
                file_texture_path=current_path,
                model_path=self.model_filepath,
            ),
        }
        if source is None and source_reason:
            issue["source_reason"] = source_reason
        self.unresolved_textures.append(issue)
        self.unresolved_texture_count = len(self.unresolved_textures)
        self.profile["unresolved_texture_count"] = self.unresolved_texture_count
        self.profile["unresolved_textures"] = list(self.unresolved_textures)
        return issue

    def _precompute_transparency_modes(self, all_vertices, all_faces, all_materials, all_textures):
        """Classify each material's transparency from its used UV-region texture alpha.

        Atlas-safe: rasterizes every material's UV triangles and samples texture
        alpha only where the material actually maps, so an opaque sub-region of an
        otherwise transparent atlas (and vice versa) is classified correctly.
        Results are stored in ``self._transparency_modes`` keyed by material index.
        Best-effort: any failure leaves a material unclassified and the simple
        diffuse-alpha fallback in ``_setup_dx11_shader`` applies.
        """
        self._transparency_modes = {}
        # Every material is classified automatically. This keeps opaque
        # standardSurface materials out of VP2's transparent queue and selects
        # the appropriate hardware-shader technique without a user-facing mode.
        texture_dir = getattr(self, "texture_dir", None)
        if not texture_dir:
            return
        try:
            from . import texture_alpha
        except Exception:
            return

        classify_start = time.perf_counter()
        alpha_cache = {}
        cursor = 0
        for material_index, material in enumerate(all_materials):
            try:
                triangle_count = int(getattr(material, "face_count", 0)) // 3
            except Exception:
                triangle_count = 0
            start_tri, end_tri = cursor, cursor + triangle_count
            cursor = end_tri
            try:
                opacity = _normalized_material_opacity(material)
                if opacity < 1.0:
                    self._transparency_modes[material_index] = TRANSPARENCY_MODE_BLEND
                    continue

                tex_index = int(getattr(material, "texture_index", -1))
                if not all_textures or tex_index < 0 or tex_index >= len(all_textures):
                    self._transparency_modes[material_index] = TRANSPARENCY_MODE_OPAQUE
                    continue

                resolved = os.path.normpath(os.path.join(texture_dir, all_textures[tex_index]))
                triangles = []
                for tri in range(start_tri, end_tri):
                    face = all_faces[tri]
                    i0, i1, i2 = face.indices
                    uv0 = all_vertices[i0].uv
                    uv1 = all_vertices[i1].uv
                    uv2 = all_vertices[i2].uv
                    triangles.append((uv0[0], uv0[1], uv1[0], uv1[1], uv2[0], uv2[1]))
                self._transparency_modes[material_index] = texture_alpha.classify_material(
                    resolved, triangles, alpha_cache=alpha_cache
                )
            except Exception:
                self.logger.debug("Failed to classify transparency (material %s)", material_index, exc_info=True)

        self._add_profile_time("transparency_classify_sec", classify_start)

    def convert_pmx_mesh(self, pmx_data: PmxData, root_group: str) -> Tuple[str, Union[str, List[str]]]:
        """
        PMXのメッシュデータをMayaのメッシュノードに変換する。

        Args:
            pmx_data (pmx_parser.PmxParser): 解析されたPMXデータオブジェクト。
            root_group (str): ルートグループの名前。

        Returns:
            str: 作成されたMayaメッシュをまとめるグループノードの名前。
            str: 作成されたMayaメッシュノードの名前（分割時はリスト）。
        """
        model_name = pmx_data.header.get_name()
        all_vertices = pmx_data.vertices
        all_faces = pmx_data.faces
        all_materials = pmx_data.materials
        all_textures = pmx_data.textures
        self._use_cpp_uv_weld = self._cpp_uv_weld_command_available()
        if self._use_cpp_uv_weld:
            # Keep the source topology intact until the C++ command has read
            # the PMX deformation payload and rebuilt Maya's mesh.
            weld_keys = {
                source_index: ("source", source_index)
                for source_index in range(len(all_vertices))
            }
        else:
            weld_keys = self._build_vertex_weld_keys(
                all_vertices,
                all_faces,
                all_materials,
                getattr(pmx_data, "morphs", None),
            )

        # マテリアルごとの透過モードを先に算出（使用UV領域のテクスチャαを見る）。
        self._precompute_transparency_modes(all_vertices, all_faces, all_materials, all_textures)

        # ジオメトリグループを作成
        geo_group = cmds.group(empty=True, name=GEOMETRY_GROUP, parent=root_group)
        # Keep Geometry under the model root for ownership/visibility, but do
        # not apply the root transform a second time.  SkinCluster evaluates
        # joint.worldMatrix against bindPreMatrix; inheriting the model-root
        # transform here as well causes root translation to double on meshes.
        cmds.setAttr(f"{geo_group}.inheritsTransform", False)

        # 設定からマテリアルごとのメッシュ分割設定を取得
        separate_by_material = settings.get(setting_keys.IMPORT_MODEL_SEPARATE_MESHES_BY_MATERIAL, False)

        if separate_by_material:
            created_mesh = self._create_material_split_meshes(
                model_name,
                all_vertices,
                all_faces,
                all_materials,
                all_textures,
                geo_group,
                is_pmd=False,
                weld_keys=weld_keys,
            )
        else:
            created_mesh = self._create_unified_mesh(
                model_name,
                all_vertices,
                all_faces,
                all_materials,
                all_textures,
                geo_group,
                weld_keys=weld_keys,
            )

        maya_scene_utils.select_objects(geo_group)
        return geo_group, created_mesh

    def _created_world_mesh_uuid(self, mesh_transform: str) -> Optional[str]:
        """Return the UUID for a freshly-created world-root mesh transform."""
        if not mesh_transform:
            return None

        world_path = mesh_transform if mesh_transform.startswith("|") else f"|{mesh_transform}"
        matches = cmds.ls(world_path, uuid=True) or []
        if matches:
            return matches[0]

        matches = cmds.ls(mesh_transform, uuid=True) or []
        return matches[0] if len(matches) == 1 else None

    def _resolve_mesh_long_path(self, mesh_uuid: Optional[str], fallback: Optional[str] = None) -> Optional[str]:
        """Resolve a mesh transform to its current long DAG path."""
        if mesh_uuid:
            matches = cmds.ls(mesh_uuid, long=True) or []
            if matches:
                return matches[0]
        if fallback:
            matches = cmds.ls(fallback, long=True) or []
            if matches:
                return matches[0]
        return fallback

    def _parent_mesh_to_group(self, mesh_transform: str, parent_group: str) -> str:
        """Parent a freshly-created mesh and return its long DAG path."""
        mesh_uuid = self._created_world_mesh_uuid(mesh_transform)
        parented = maya_scene_utils.parent_objects(mesh_transform, parent_group)
        fallback = parented[0] if parented else mesh_transform
        return self._resolve_mesh_long_path(mesh_uuid, fallback) or mesh_transform

    def _create_unified_mesh(
        self,
        model_name,
        all_vertices,
        all_faces,
        all_materials,
        all_textures,
        model_group,
        is_pmd=False,
        weld_keys=None,
    ):
        """
        全てのメッシュを統合した単一のメッシュを作成する。

        Args:
            model_name (str): モデル名
            all_vertices (list): 全ての頂点データ
            all_faces (list): 全ての面データ
            all_materials (list): 全てのマテリアルデータ
            all_textures (list): 全てのテクスチャデータ
            model_group (str): 親グループの名前
        Returns:
            str: 作成されたメッシュノードの名前
        """

        # 頂点数がゼロの場合は警告を出して処理を中断
        if not all_vertices or len(all_vertices) == 0:
            self.logger.warning(f"Mesh has zero vertices; skipping mesh creation: {model_name}")
            return None

        # 統合メッシュの名前を設定
        mesh_name = maya_name_utils.sanitize_text(model_name) + "_mesh"

        if weld_keys is None:
            weld_keys = self._build_vertex_weld_keys(all_vertices, all_faces, all_materials, None)

        # Build the topology before MFnMesh.create(). UV seams remain in the
        # per-corner UV connections; only safe coincident source vertices are
        # shared by the Maya geometry.
        face_records = []
        face_offset = 0
        for i, material in enumerate(all_materials):
            num_material_faces = material.face_count // 3
            face_records.extend(
                (i, face)
                for face in all_faces[face_offset : face_offset + num_material_faces]
            )
            face_offset += num_material_faces

        mesh_data = self._build_maya_mesh_data(
            all_vertices,
            face_records,
            weld_keys=weld_keys,
        )

        # 統合メッシュを作成
        create_start = time.perf_counter()
        created_mesh = maya_mesh_utils.create_mesh_with_uvs(
            name=mesh_name,
            vertices=mesh_data["vertices"],
            face_counts=mesh_data["face_counts"],
            face_connects=mesh_data["face_connects"],
            uvs=mesh_data["uvs"],
            face_uv_connects=mesh_data["face_uv_connects"],
            normals=mesh_data["normals"],
        )
        self._add_profile_time("mesh_create_sec", create_start)
        self.profile["created_mesh_count"] += 1
        self.profile["source_vertex_count"] = len(all_vertices)
        self.profile["mesh_vertex_slots_estimated"] += len(mesh_data["vertices"])
        native_welded_count = self._run_cpp_uv_weld(
            created_mesh,
            mesh_data["source_vertex_indices"],
        )
        self.profile["uv_welded_vertex_count"] += (
            native_welded_count
            if native_welded_count is not None
            else mesh_data["welded_vertex_count"]
        )
        self.profile["face_count"] += len(mesh_data["face_counts"])

        if (
            not self._use_cpp_uv_weld
            and len(mesh_data["source_vertex_indices"]) != len(all_vertices)
        ):
            maya_attribute_utils.add_typed_attribute(created_mesh, ATTR_MMD_SOURCE_VERTEX_INDICES, "longArray")
            maya_attribute_utils.set_attribute(
                created_mesh,
                ATTR_MMD_SOURCE_VERTEX_INDICES,
                mesh_data["source_vertex_indices"],
                "longArray",
            )

        # マテリアルを作成して、適切な面に割り当てる
        for i, material in enumerate(all_materials):
            start_face, end_face = mesh_data["material_face_ranges"].get(i, (0, 0))
            if start_face == end_face:
                continue

            # マテリアル名をサニタイズ
            # material_name = maya_name_utils.sanitize_text(material.name)

            # テクスチャパスを取得
            texture_path = None
            raw_texture_path = None
            if all_textures:
                if material.texture_index != -1:
                    raw_texture_path = all_textures[material.texture_index]
                    texture_path = maya_material_utils.sanitize_texture_path(raw_texture_path, self.texture_dir)

            # マテリアルを作成
            material_start = time.perf_counter()
            shader = self._create_material(
                material=material,
                texture_path=texture_path,
                all_textures=all_textures,
                is_pmd=is_pmd,
                material_index=i,
                original_texture_path=raw_texture_path,
            )
            self._add_profile_time("material_create_sec", material_start)
            self._record_created_shader(shader)
            self.profile["material_count_processed"] += 1

            # 面の範囲を選択してマテリアルを割り当て
            face_selection = f"{created_mesh}.f[{start_face}:{end_face - 1}]"
            assign_start = time.perf_counter()
            maya_material_utils.assign_material_to_faces(created_mesh, shader, face_selection)
            self._add_profile_time("material_assign_sec", assign_start)

        # A unified shape can contain multiple materials, so per-material
        # double-sided/single-sided culling cannot be represented by one shape
        # attribute. Use separate_meshes_by_material for strict MMD draw flags.

        # 作成したメッシュをグループに追加
        parent_start = time.perf_counter()
        created_mesh = self._parent_mesh_to_group(created_mesh, model_group)
        self._add_profile_time("parent_sec", parent_start)

        # MMDモデル表示用にバックフェイスカリングを無効化（設定に応じて）
        disable_backface_culling = settings.get(setting_keys.IMPORT_MODEL_DISABLE_BACKFACE_CULLING, True)
        if disable_backface_culling:
            maya_viewport_utils.set_viewport_backface_culling(False)

        return created_mesh

    def _create_material_split_meshes(
        self,
        model_name,
        all_vertices,
        all_faces,
        all_materials,
        all_textures,
        geo_group,
        is_pmd=False,
        weld_keys=None,
    ):
        """
        マテリアルごとに分割したメッシュを作成する。
        PMX の sub-mesh は該当 material の face が参照する頂点だけを保持し、
        mmd_source_vertex_indices に元 PMX vertex index を保存する。PMD は既存互換のため全頂点を保持する。

        Args:
            model_name (str): モデル名
            all_vertices (list): 全ての頂点データ
            all_faces (list): 全ての面データ
            all_materials (list): 全てのマテリアルデータ
            all_textures (list): 全てのテクスチャデータ
            geo_group (str): 親グループの名前
            is_pmd (bool): PMDファイルかどうか

        Returns:
            list: 作成されたメッシュノードの名前のリスト
        """
        if not all_vertices or len(all_vertices) == 0:
            self.logger.warning(f"Mesh has zero vertices; skipping mesh creation: {model_name}")
            return []

        if weld_keys is None:
            weld_keys = self._build_vertex_weld_keys(all_vertices, all_faces, all_materials, None)

        mesh_names = []
        face_offset = 0

        for i, material in enumerate(all_materials):
            num_material_faces = material.face_count // 3
            if num_material_faces == 0:
                continue

            material_faces = all_faces[face_offset : face_offset + num_material_faces]
            face_records = [(i, face) for face in material_faces]
            if is_pmd:
                active_source_indices = range(len(all_vertices))
            else:
                active_source_indices = []
                active_source_set = set()
                for face in material_faces:
                    for raw_index in getattr(face, "indices", ()) or ():
                        source_index = int(raw_index)
                        if source_index not in active_source_set:
                            active_source_set.add(source_index)
                            active_source_indices.append(source_index)

            mesh_data = self._build_maya_mesh_data(
                all_vertices,
                face_records,
                active_source_indices=active_source_indices,
                weld_keys=weld_keys,
            )
            face_offset += num_material_faces
            if not mesh_data["face_counts"]:
                continue

            # マテリアル名からメッシュ名生成
            mat_name = material.get_name() if material.get_name() else f"material_{i}"
            sub_mesh_name = maya_name_utils.sanitize_text(f"{model_name}_{mat_name}_mesh")

            create_start = time.perf_counter()
            created_mesh = maya_mesh_utils.create_mesh_with_uvs(
                name=sub_mesh_name,
                vertices=mesh_data["vertices"],
                face_counts=mesh_data["face_counts"],
                face_connects=mesh_data["face_connects"],
                uvs=mesh_data["uvs"],
                face_uv_connects=mesh_data["face_uv_connects"],
                normals=mesh_data["normals"],
            )
            self._add_profile_time("mesh_create_sec", create_start)
            self.profile["created_mesh_count"] += 1
            self.profile["source_vertex_count"] = len(all_vertices)
            self.profile["mesh_vertex_slots_estimated"] += len(mesh_data["vertices"])
            native_welded_count = self._run_cpp_uv_weld(
                created_mesh,
                mesh_data["source_vertex_indices"],
            )
            self.profile["uv_welded_vertex_count"] += (
                native_welded_count
                if native_welded_count is not None
                else mesh_data["welded_vertex_count"]
            )
            self.profile["face_count"] += len(mesh_data["face_counts"])
            _set_mesh_double_sided(created_mesh, _material_is_double_sided(material))
            maya_attribute_utils.set_custom_attributes(
                created_mesh,
                {
                    ATTR_MMD_MATERIAL_INDEX: i,
                    "mmd_material_split_mesh": True,
                },
            )
            if not is_pmd and not self._use_cpp_uv_weld:
                maya_attribute_utils.add_typed_attribute(created_mesh, ATTR_MMD_SOURCE_VERTEX_INDICES, "longArray")
                maya_attribute_utils.set_attribute(
                    created_mesh,
                    ATTR_MMD_SOURCE_VERTEX_INDICES,
                    mesh_data["source_vertex_indices"],
                    "longArray",
                )

            # テクスチャパスを取得
            texture_path = None
            raw_texture_path = None
            if all_textures:
                if material.texture_index != -1:
                    raw_texture_path = all_textures[material.texture_index]
                    texture_path = maya_material_utils.sanitize_texture_path(raw_texture_path, self.texture_dir)

            # マテリアルを作成 (全体割当)
            material_start = time.perf_counter()
            shader = self._create_material(
                material=material,
                texture_path=texture_path,
                all_textures=all_textures,
                is_pmd=is_pmd,
                material_index=i,
                original_texture_path=raw_texture_path,
            )
            self._add_profile_time("material_create_sec", material_start)
            self._record_created_shader(shader)
            self.profile["material_count_processed"] += 1

            # 全 face にマテリアルを割り当て
            assign_start = time.perf_counter()
            maya_material_utils.assign_material_to_faces(
                created_mesh, shader, f"{created_mesh}.f[0:{len(mesh_data['face_counts']) - 1}]"
            )
            self._add_profile_time("material_assign_sec", assign_start)

            # グループに追加
            parent_start = time.perf_counter()
            created_mesh = self._parent_mesh_to_group(created_mesh, geo_group)
            self._add_profile_time("parent_sec", parent_start)
            mesh_names.append(created_mesh)

        # MMDモデル表示用にバックフェイスカリングを無効化（設定に応じて）
        disable_backface_culling = settings.get(setting_keys.IMPORT_MODEL_DISABLE_BACKFACE_CULLING, True)
        if disable_backface_culling:
            maya_viewport_utils.set_viewport_backface_culling(False)

        return mesh_names

    def _create_material(
        self,
        material,
        texture_path=None,
        all_textures=None,
        is_pmd=False,
        material_index=None,
        original_texture_path=None,
    ):
        """
        MMDマテリアルデータからMayaマテリアルを作成します。

        Args:
            material: MMDマテリアルオブジェクト（PMX/PMD）
            texture_path (str, optional): テクスチャファイルのパス。
            all_textures (list, optional): 全てのテクスチャパスリスト（PMXのみ）
            is_pmd (bool): PMDファイルかどうか

        Returns:
            str: 作成されたシェーダーノード名。
        """
        sanitized_name = self._allocate_material_name(material, material_index)

        # create_mmd_shaders設定を確認
        create_mmd_shaders = settings.get(setting_keys.IMPORT_MODEL_CREATE_MMD_SHADERS)

        if create_mmd_shaders:
            backend = effective_mmd_shader_backend()

            if backend != "standard":
                backend_order = [backend]

                for target in backend_order:
                    shader = None
                    plugin_name = "dx11Shader" if target == "dx11" else "glslShader"
                    node_type = "dx11Shader" if target == "dx11" else "GLSLShader"
                    if not _ensure_shader_plugin(plugin_name):
                        _warn_shader_backend_once(
                            f"{target}-plugin-unavailable",
                            f"{node_type} plugin '{plugin_name}' is unavailable. Trying next shader backend.",
                        )
                        if backend != "auto":
                            break
                        continue

                    if target == "dx11":
                        try:
                            shader = cmds.shadingNode("dx11Shader", asShader=True, name=sanitized_name)
                            ok, reason = _validate_shader_node(shader, "dx11Shader")
                            if not ok:
                                _warn_shader_backend_once(
                                    "dx11-node-invalid",
                                    f"Rejected dx11Shader node: {reason}. Trying next shader backend.",
                                )
                                _delete_shader_node(shader)
                                if backend != "auto":
                                    break
                                continue

                            self._setup_dx11_shader(
                                shader,
                                material,
                                texture_path,
                                all_textures,
                                is_pmd,
                                material_index,
                                original_texture_path,
                            )
                            ok, reason = _validate_shader_node(shader, "dx11Shader")
                            if not ok:
                                _warn_shader_backend_once(
                                    "dx11-node-invalid-after-setup",
                                    f"Rejected configured dx11Shader node: {reason}. Trying next shader backend.",
                                )
                                _delete_shader_node(shader)
                                if backend != "auto":
                                    break
                                continue
                            return shader
                        except Exception as e:
                            _warn_shader_backend_once(
                                "dx11-create-failed",
                                f"Failed to create dx11Shader: {e}. Trying next shader backend.",
                            )
                            _delete_shader_node(shader)
                            if backend != "auto":
                                break

                    if target == "glsl":
                        try:
                            shader = cmds.shadingNode("GLSLShader", asShader=True, name=sanitized_name)
                            ok, reason = _validate_shader_node(shader, "GLSLShader")
                            if not ok:
                                _warn_shader_backend_once(
                                    "glsl-node-invalid",
                                    f"Rejected GLSLShader node: {reason}. Falling back.",
                                )
                                _delete_shader_node(shader)
                                if backend != "auto":
                                    break
                                continue

                            setup_ok = self._setup_glsl_shader(
                                shader,
                                material,
                                texture_path,
                                all_textures,
                                is_pmd,
                                material_index,
                                original_texture_path,
                            )
                            ok, reason = _validate_shader_node(shader, "GLSLShader")
                            if not setup_ok or not ok:
                                reason = reason or "shader setup failed"
                                _warn_shader_backend_once(
                                    "glsl-setup-failed",
                                    f"Rejected configured GLSLShader node: {reason}. Falling back.",
                                )
                                _delete_shader_node(shader)
                                if backend != "auto":
                                    break
                                continue
                            return shader
                        except Exception as e:
                            _warn_shader_backend_once(
                                "glsl-create-failed",
                                f"Failed to create GLSLShader: {e}. Falling back.",
                            )
                            _delete_shader_node(shader)
                            if backend != "auto":
                                break

            if backend != "standard":
                _warn_shader_backend_once(
                    "standard-fallback",
                    "Falling back to standardSurface for MMD material creation.",
                )

        # 標準のstandardSurfaceを使用
        shader = cmds.shadingNode("standardSurface", asShader=True, name=sanitized_name)
        self._setup_standard_shader(
            shader,
            material,
            texture_path,
            all_textures,
            is_pmd,
            material_index,
            original_texture_path,
            sanitized_name,
        )

        return shader

    def _apply_custom_attributes(
        self,
        shader,
        material,
        all_textures,
        is_pmd,
        material_index=None,
        texture_path=None,
        sphere_texture_path=None,
    ):
        """カスタムアトリビュートを適用する共通処理"""
        # mmd_materialフラグを追加（このマテリアルがMMDマテリアルであることを示す）

        custom_attrs = {
            ATTR_MMD_MATERIAL: 1,  # MMDマテリアルであることを示すフラグ
            ATTR_MMD_MATERIAL_INDEX: material.material_index,
            ATTR_MMD_MATERIAL_NAME: material.name,
            ATTR_MMD_DIFFUSE_COLOR: material.diffuse[:3],
            ATTR_MMD_DIFFUSE_ALPHA: (
                float(material.diffuse[3]) if len(material.diffuse) > 3 else 1.0
            ),
            ATTR_MMD_AMBIENT_COLOR: material.ambient[:3],
            ATTR_MMD_TOON_TEXTURE_INDEX: material.toon_texture_index,
        }

        # テクスチャパスを保存
        if texture_path:
            custom_attrs["mmd_texture_path"] = texture_path
        if sphere_texture_path:
            custom_attrs["mmd_sphere_path"] = sphere_texture_path

        # スペキュラー関連の属性
        if hasattr(material, "specular"):
            custom_attrs[ATTR_MMD_SPECULAR_COLOR] = material.specular[:3]

        # 光沢度（PMDとPMXで属性名が異なる）
        if hasattr(material, "specular_power"):
            custom_attrs[ATTR_MMD_SHININESS] = material.specular_power
        elif hasattr(material, "specular_coefficient"):
            custom_attrs[ATTR_MMD_SHININESS] = material.specular_coefficient

        # PMDとPMXで異なる属性への対応
        if hasattr(material, "name_english"):
            custom_attrs[ATTR_MMD_MATERIAL_NAME_EN] = material.name_english
        else:
            custom_attrs[ATTR_MMD_MATERIAL_NAME_EN] = ""

        if is_pmd:
            custom_attrs[ATTR_MMD_EDGE_FLAG] = int(material.edge_flag)
            custom_attrs[ATTR_MMD_SHADER_OUTLINE_ENABLED] = _material_has_outline(material, is_pmd=True)
        else:
            custom_attrs[ATTR_MMD_SPHERE_MODE] = int(material.sphere_mode)
            custom_attrs[ATTR_MMD_SPHERE_TEXTURE_INDEX] = material.sphere_texture_index
            custom_attrs[ATTR_MMD_TEXTURE_INDEX] = material.texture_index
            custom_attrs[ATTR_MMD_DRAW_FLAGS] = int(material.draw_flag)
            custom_attrs[ATTR_MMD_EDGE_COLOR] = material.edge_color[:3]
            custom_attrs[ATTR_MMD_EDGE_ALPHA] = (
                float(material.edge_color[3]) if len(material.edge_color) > 3 else 1.0
            )
            custom_attrs[ATTR_MMD_EDGE_SIZE] = material.edge_size
            custom_attrs[ATTR_MMD_SHADER_OUTLINE_ENABLED] = _material_has_outline(material)
            custom_attrs[_ATTR_MMD_DOUBLE_SIDED] = _material_is_double_sided(material)
            custom_attrs[ATTR_MMD_MEMO] = material.memo
            custom_attrs[ATTR_MMD_SHARED_TOON_FLAG] = int(material.shared_toon_flag)

        maya_attribute_utils.set_custom_attributes(
            shader,
            custom_attrs,
        )

    def _setup_standard_shader(
        self,
        shader,
        material,
        texture_path,
        all_textures,
        is_pmd,
        material_index=None,
        original_texture_path=None,
        material_name=None,
    ):
        """標準のstandardSurfaceシェーダーを設定"""

        # マテリアル名をサニタイズ（テクスチャノード名に使用）
        sanitized_name = material_name or maya_name_utils.sanitize_text(material.name if material.name else "material")

        # 基本色設定（Diffuse）
        maya_attribute_utils.set_attribute(shader, "baseColor", material.diffuse[:3], "double3")

        # AlphaをOpacityに変換（StandardSurfaceではopacityを使用）
        material_opacity = _normalized_material_opacity(material)
        maya_attribute_utils.set_attribute(
            shader,
            "opacity",
            (material_opacity, material_opacity, material_opacity),
            "double3",
        )

        # スペキュラー設定（MMDのspecularをStandardSurfaceにマッピング）
        if hasattr(material, "specular"):
            # スペキュラー色
            maya_attribute_utils.set_attribute(shader, "specularColor", material.specular[:3], "double3")

            # スペキュラー係数の取得（PMDとPMXで異なる）
            specular_coef = None
            if hasattr(material, "specular_coefficient"):
                specular_coef = material.specular_coefficient
            elif hasattr(material, "specular_power"):
                specular_coef = material.specular_power

            if specular_coef is not None:
                maya_attribute_utils.set_attribute(shader, "specularColor", material.specular[:3], "double3")

        # アンビエント設定（StandardSurfaceでは加算発光項として使用）
        if hasattr(material, "ambient"):
            # StandardSurface has no PMX-style ambient color input. The
            # texture path below drives an ambient*texture emission term;
            # keep the weight explicit so the fallback follows the MMD parity
            # equation instead of reducing ambient to a weak average.
            maya_attribute_utils.set_attribute(shader, "emission", 1.0, "float")

        # 非金属マテリアルとして設定（MMDは基本的に非金属）
        maya_attribute_utils.set_attribute(shader, "metalness", 0.0, "float")

        # カスタムアトリビュートを適用
        self._apply_custom_attributes(shader, material, all_textures, is_pmd, material_index, texture_path)

        # テクスチャの設定
        raw_texture_path = original_texture_path or texture_path
        if raw_texture_path:
            # テクスチャパスを解決
            full_texture_path = _resolve_texture_path(self.texture_dir, texture_path or raw_texture_path)
            if full_texture_path:
                file_texture_path = full_texture_path
                unresolved = is_unreadable_file_texture_path(full_texture_path)
                cache_path = None
                if unresolved and settings.get(setting_keys.IMPORT_MODEL_AUTO_RESOLVE_TEXTURES, True):
                    resolution = resolve_texture_to_cache(
                        original_path=raw_texture_path,
                        file_texture_path=full_texture_path,
                        model_path=self.model_filepath,
                        workspace_root=cmds.workspace(q=True, rootDirectory=True),
                    )
                    if resolution.status == "resolved" and resolution.cache_path:
                        file_texture_path = resolution.cache_path
                        cache_path = resolution.cache_path
                        unresolved = False

                file_node = cmds.shadingNode("file", asTexture=True, name=sanitized_name + "_file")
                self.created_texture_file_nodes.append(file_node)
                place_uv_node = cmds.shadingNode(
                    "place2dTexture",
                    asUtility=True,
                    name=sanitized_name + "_place2dTexture",
                )

                # Keep the file node in the graph even while its path is
                # unresolved. Texture repair walks the diffuse multiply
                # utility to find and update this node in place.
                cmds.connectAttr(place_uv_node + ".outUV", file_node + ".uvCoord")

                transparency_mode = self._transparency_modes.get(
                    material_index, TRANSPARENCY_MODE_OPAQUE
                )
                if transparency_mode != TRANSPARENCY_MODE_OPAQUE:
                    # Preserve PMX diffuse alpha while applying per-pixel
                    # texture alpha. Opaque materials deliberately have no
                    # opacity connection so VP2 keeps them in the opaque queue.
                    opacity_multiply = cmds.shadingNode(
                        "multiplyDivide",
                        asUtility=True,
                        name=sanitized_name + "_opacityMultiply",
                    )
                    maya_attribute_utils.set_attribute(opacity_multiply, "operation", 1, "long")
                    maya_attribute_utils.set_attribute(
                        opacity_multiply,
                        "input2X",
                        material_opacity,
                        "float",
                    )
                    cmds.connectAttr(file_node + ".outAlpha", opacity_multiply + ".input1X", force=True)
                    for channel in "RGB":
                        cmds.connectAttr(
                            opacity_multiply + ".outputX",
                            shader + f".opacity{channel}",
                            force=True,
                        )

                maya_attribute_utils.set_attribute(file_node, "fileTextureName", file_texture_path, "string")
                maya_material_utils.mark_mmd_texture_file_node(
                    file_node,
                    raw_texture_path,
                    self.model_filepath,
                    unresolved=unresolved,
                )
                if cache_path:
                    maya_attribute_utils.set_custom_attributes(
                        file_node,
                        {
                            ATTR_MMD_TEXTURE_CACHE_PATH: cache_path,
                            ATTR_MMD_TEXTURE_UNRESOLVED: False,
                        },
                    )
                if unresolved:
                    issue = self._record_unresolved_texture_issue(
                        file_node=file_node,
                        shader=shader,
                        material=material,
                        original_path=raw_texture_path,
                        current_path=full_texture_path,
                    )
                    if issue.get("reason") == "missing_file":
                        cmds.warning(f"Texture file not found: {full_texture_path}")
                    else:
                        cmds.warning(
                            f"Texture path needs resolution ({issue.get('reason', 'unreadable_path')}): "
                            f"{full_texture_path}"
                        )
                # Keep Maya's stock file-to-standardSurface contract intact so
                # VP2 can provide its normal untextured fallback when panel
                # texture display is disabled. file.colorGain applies the same
                # Texture * PMX Diffuse multiplication without an intermediate
                # arithmetic node on the baseColor connection.
                maya_attribute_utils.set_attribute(
                    file_node,
                    "colorGain",
                    tuple(float(value) for value in material.diffuse[:3]),
                    "double3",
                )
                cmds.connectAttr(file_node + ".outColor", shader + ".baseColor", force=True)

                if hasattr(material, "ambient"):
                    ambient_multiply = cmds.shadingNode(
                        "multiplyDivide",
                        asUtility=True,
                        name=sanitized_name + "_ambientMultiply",
                    )
                    maya_attribute_utils.set_attribute(ambient_multiply, "operation", 1, "long")
                    for channel, value in zip("XYZ", material.ambient[:3]):
                        maya_attribute_utils.set_attribute(
                            ambient_multiply,
                            f"input2{channel}",
                            float(value),
                            "float",
                        )
                    cmds.connectAttr(file_node + ".outColor", ambient_multiply + ".input1", force=True)
                    cmds.connectAttr(ambient_multiply + ".output", shader + ".emissionColor", force=True)

        elif hasattr(material, "ambient"):
            # No texture node exists, so ambient remains an additive constant.
            maya_attribute_utils.set_attribute(
                shader,
                "emissionColor",
                tuple(float(value) for value in material.ambient[:3]),
                "double3",
            )

    def _setup_glsl_shader(
        self,
        shader,
        material,
        texture_path,
        all_textures,
        is_pmd,
        material_index=None,
        original_texture_path=None,
    ):
        """GLSLShader を設定し、必須属性の設定に成功したか返す。"""
        shader_ogsfx_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "shaders",
            "MMDShader.ogsfx",
        )
        shader_ogsfx_path = os.path.normpath(shader_ogsfx_path)

        setup_ok = True
        setup_ok &= _set_shader_attribute_checked(shader, "shader", shader_ogsfx_path, "string")
        setup_ok &= _set_shader_attribute_checked(shader, "technique", "Main", "string")
        _ensure_mmd_shader_uniform_attributes(shader)

        # Both hardware backends use the same base-value contract. Diffuse alpha
        # stays separate so a later material-morph evaluator can own final RGB.
        for binding, value in iter_hardware_shader_values(
            material_base_parameter_values(material), "GLSLShader"
        ):
            setup_ok &= _set_shader_attribute_checked(shader, binding.attribute, value, binding.attribute_type)

        for texture_flag in ("HasMainTexture", "HasSphereTexture", "HasToonTexture"):
            if cmds.attributeQuery(texture_flag, node=shader, exists=True):
                maya_attribute_utils.set_attribute(shader, texture_flag, 0, "long")

        # OGSFX exposes the same texture-slot contract as the DX11 effect.  The
        # previous GLSL setup stopped after scalar uniforms, leaving every
        # material untextured even when the PMX texture paths were valid.
        self._connect_dx11_main_texture(shader, material, texture_path, original_texture_path)

        sphere_texture_path = None
        if not is_pmd and getattr(material, "sphere_texture_index", -1) >= 0:
            sphere_index = int(material.sphere_texture_index)
            if all_textures and sphere_index < len(all_textures):
                sphere_texture_path = all_textures[sphere_index]
                full_sphere_path = _resolve_texture_path(self.texture_dir, sphere_texture_path)
                self._connect_dx11_secondary_texture(
                    shader,
                    material,
                    sphere_texture_path,
                    full_sphere_path,
                    "SphereTexture",
                    "HasSphereTexture",
                    "_sphere_texture",
                    "Sphere",
                )

        if not is_pmd:
            full_toon_path = _resolve_pmx_toon_texture_path(self.texture_dir, material, all_textures)
            if full_toon_path and os.path.exists(full_toon_path):
                toon_original_path = ""
                toon_source_kind = "shared_toon"
                toon_shared_id = ""
                if (
                    getattr(material, "shared_toon_flag", 1) == 0
                    and all_textures
                    and 0 <= int(getattr(material, "toon_texture_index", -1)) < len(all_textures)
                ):
                    toon_original_path = all_textures[int(material.toon_texture_index)]
                    toon_source_kind = "pmx_texture"
                elif hasattr(material, "toon_texture_index"):
                    toon_shared_id = f"shared_toon:{int(material.toon_texture_index) + 1}"
                self._connect_dx11_secondary_texture(
                    shader,
                    material,
                    toon_original_path,
                    full_toon_path,
                    "ToonTexture",
                    "HasToonTexture",
                    "_toon_texture",
                    "Toon",
                    source_kind=toon_source_kind,
                    shared_toon_id=toon_shared_id,
                )

        self._apply_custom_attributes(
            shader,
            material,
            all_textures,
            is_pmd,
            material_index,
            texture_path,
            sphere_texture_path,
        )
        return setup_ok

    def _connect_dx11_secondary_texture(
        self,
        shader,
        material,
        original_path,
        full_texture_path,
        texture_attr,
        has_texture_attr,
        node_suffix,
        warning_label,
        source_kind="pmx_texture",
        shared_toon_id="",
    ):
        """Connect a readable or resolved secondary texture to a dx11Shader."""
        if not (
            full_texture_path
            and os.path.exists(full_texture_path)
            and cmds.attributeQuery(texture_attr, node=shader, exists=True)
        ):
            if full_texture_path:
                cmds.warning(f"{warning_label} texture file not found: {full_texture_path}")
            return

        file_texture_path = full_texture_path
        unresolved = is_unreadable_file_texture_path(full_texture_path)
        cache_path = None
        if unresolved and settings.get(setting_keys.IMPORT_MODEL_AUTO_RESOLVE_TEXTURES, True):
            resolution = resolve_texture_to_cache(
                original_path=original_path,
                file_texture_path=full_texture_path,
                model_path=self.model_filepath,
                workspace_root=cmds.workspace(q=True, rootDirectory=True),
            )
            if resolution.status == "resolved" and resolution.cache_path:
                file_texture_path = resolution.cache_path
                cache_path = resolution.cache_path
                unresolved = False

        file_node = cmds.shadingNode("file", asTexture=True, name=shader + node_suffix)
        self.created_texture_file_nodes.append(file_node)
        maya_attribute_utils.set_attribute(file_node, "fileTextureName", file_texture_path, "string")
        mark_kwargs = {"unresolved": unresolved}
        if source_kind != "pmx_texture" or shared_toon_id:
            mark_kwargs["source_kind"] = source_kind
            mark_kwargs["shared_toon_id"] = shared_toon_id
        maya_material_utils.mark_mmd_texture_file_node(
            file_node,
            original_path,
            self.model_filepath,
            **mark_kwargs,
        )
        if cache_path:
            maya_attribute_utils.set_custom_attributes(
                file_node,
                {
                    ATTR_MMD_TEXTURE_CACHE_PATH: cache_path,
                    ATTR_MMD_TEXTURE_UNRESOLVED: False,
                },
            )
        if unresolved:
            issue = self._record_unresolved_texture_issue(
                file_node=file_node,
                shader=shader,
                material=material,
                original_path=original_path,
                current_path=full_texture_path,
            )
            cmds.warning(
                f"{warning_label} texture path needs resolution "
                f"({issue.get('reason', 'unreadable_path')}): {full_texture_path}"
            )
            return

        if not bind_dx11_texture_file_node(shader, file_node, texture_attr, has_texture_attr):
            cmds.warning(f"Failed to connect {warning_label.lower()} texture to dx11Shader")

    def _connect_dx11_main_texture(
        self,
        shader,
        material,
        texture_path,
        original_texture_path=None,
    ):
        """Connect the main diffuse texture to a dx11Shader."""
        raw_texture_path = original_texture_path or texture_path
        if not raw_texture_path:
            return

        full_texture_path = _resolve_texture_path(self.texture_dir, texture_path or raw_texture_path)
        self._connect_dx11_secondary_texture(
            shader,
            material,
            raw_texture_path,
            full_texture_path,
            "MainTexture",
            "HasMainTexture",
            "_texture",
            "Main",
        )

    def _setup_dx11_shader(
        self,
        shader,
        material,
        texture_path,
        all_textures,
        is_pmd,
        material_index=None,
        original_texture_path=None,
    ):
        """dx11Shaderを設定"""

        # シェーダーファイルのパスを設定
        shader_fx_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "shaders", "MMDShader.fx")
        shader_fx_path = os.path.normpath(shader_fx_path)

        # dx11Shaderにエフェクトファイルを設定
        maya_attribute_utils.set_attribute(shader, "shader", shader_fx_path, "string")

        # mayapy standalone では dx11Shader が .fx ファイルから uniform 属性を
        # 自動生成しないため、事前に動的アトリビュートとして作成しておく
        _ensure_dx11_uniform_attributes(shader)
        # Prefer the accurate per-material UV-region classification computed up
        # front; fall back to the simple diffuse-alpha rule if unavailable.
        mode = self._transparency_modes.get(material_index)
        if mode is None:
            mode = _classify_material_transparency(material, texture_path)
        double_sided = _material_is_double_sided(material)
        technique = _technique_for_transparency(mode, True, double_sided)
        cmds.setAttr(f"{shader}.technique", technique, type="string")
        _store_transparency_mode_attr(shader, mode)
        _store_shader_double_sided_attr(shader, double_sided)

        base_values = material_base_parameter_values(material)
        for semantic in ("diffuse_rgb", "diffuse_alpha", "opacity", "specular", "specular_power", "ambient"):
            for binding, value in iter_hardware_shader_values(
                {semantic: base_values.get(semantic)}, "dx11Shader"
            ):
                if value is not None:
                    maya_attribute_utils.set_attribute(shader, binding.attribute, value, binding.attribute_type)

        # エッジ設定（PMXのみ）
        if not is_pmd:
            # エッジ色
            _set_dx11_color_uniform(shader, "EdgeColor", material.edge_color)
        outline_enabled = _material_has_outline(material, is_pmd=is_pmd)
        authored_edge_size = float(getattr(material, "edge_size", 1.0))
        edge_size = authored_edge_size if outline_enabled else 0.0
        maya_attribute_utils.set_attribute(shader, "EdgeSize", edge_size, "float")

        # スフィアモード設定
        sphere_mode = getattr(material, "sphere_mode", 0)
        maya_attribute_utils.set_attribute(shader, "SphereMode", int(sphere_mode), "long")

        for texture_flag in ("HasMainTexture", "HasSphereTexture", "HasToonTexture"):
            if cmds.attributeQuery(texture_flag, node=shader, exists=True):
                maya_attribute_utils.set_attribute(shader, texture_flag, 0, "long")

        # テクスチャ設定
        self._connect_dx11_main_texture(shader, material, texture_path, original_texture_path)

        # スフィアテクスチャ設定（PMXのみ）
        sphere_texture_path = None
        if not is_pmd and hasattr(material, "sphere_texture_index") and material.sphere_texture_index >= 0:
            if all_textures and material.sphere_texture_index < len(all_textures):
                sphere_texture_path = all_textures[material.sphere_texture_index]
                full_sphere_path = _resolve_texture_path(self.texture_dir, sphere_texture_path)

                if (
                    full_sphere_path
                    and os.path.exists(full_sphere_path)
                    and cmds.attributeQuery("SphereTexture", node=shader, exists=True)
                ):
                    self._connect_dx11_secondary_texture(
                        shader,
                        material,
                        sphere_texture_path,
                        full_sphere_path,
                        "SphereTexture",
                        "HasSphereTexture",
                        "_sphere_texture",
                        "Sphere",
                    )

        # Toon texture setting. PMX custom toon uses the regular texture table;
        # shared toon uses bundled toon01.bmp..toon10.bmp assets.
        if not is_pmd:
            full_toon_path = _resolve_pmx_toon_texture_path(self.texture_dir, material, all_textures)
            if full_toon_path and os.path.exists(full_toon_path) and cmds.attributeQuery("ToonTexture", node=shader, exists=True):
                toon_original_path = ""
                toon_source_kind = "shared_toon"
                toon_shared_id = ""
                if (
                    hasattr(material, "shared_toon_flag")
                    and hasattr(material, "toon_texture_index")
                    and int(material.shared_toon_flag) == 0
                    and all_textures
                    and 0 <= int(material.toon_texture_index) < len(all_textures)
                ):
                    toon_original_path = all_textures[int(material.toon_texture_index)]
                    toon_source_kind = "pmx_texture"
                elif hasattr(material, "toon_texture_index"):
                    toon_shared_id = f"shared_toon:{int(material.toon_texture_index) + 1}"
                self._connect_dx11_secondary_texture(
                    shader,
                    material,
                    toon_original_path,
                    full_toon_path,
                    "ToonTexture",
                    "HasToonTexture",
                    "_toon_texture",
                    "Toon",
                    source_kind=toon_source_kind,
                    shared_toon_id=toon_shared_id,
                )
            elif full_toon_path:
                cmds.warning(f"Toon texture file not found: {full_toon_path}")

        # カスタムアトリビュートを適用
        self._apply_custom_attributes(
            shader,
            material,
            all_textures,
            is_pmd,
            material_index,
            texture_path,
            sphere_texture_path,
        )
