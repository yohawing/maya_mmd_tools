import os
import time
import json

from maya import cmds
from typing import Tuple, Union, List, Optional

from mmd_tools.core.settings import settings
from mmd_tools.core import settings_keys as setting_keys
from mmd_tools.core import maya_material_utils, maya_utils
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
    ATTR_MMD_MORPH_GROUP_SPLIT_MESH,
    ATTR_MMD_VERTEX_MORPH_NAMES_JSON,
)
from mmd_tools.converters.mesh_material_properties import (
    PMX_DOUBLE_SIDED_DRAW_FLAG as _PMX_DOUBLE_SIDED_DRAW_FLAG,
    material_is_double_sided as _material_is_double_sided,
)
from mmd_tools.converters.mesh_texture_resolve import (
    resolve_pmx_toon_texture_path as _resolve_pmx_toon_texture_path,
    resolve_texture_path as _resolve_texture_path,
)

LOGGER = get_logger(__name__)

_ALPHA_CAPABLE_TEXTURE_EXTENSIONS = {".png", ".tga", ".tif", ".tiff", ".dds"}
_SHADER_PLUGIN_LOAD_CACHE = {}
_SHADER_BACKEND_WARNED = set()


# DX11 transparency handling modes (drives technique selection).
TRANSPARENCY_MODE_OPAQUE = "opaque"
TRANSPARENCY_MODE_CUTOUT = "cutout"
TRANSPARENCY_MODE_BLEND = "blend"
TRANSPARENCY_MODES = (TRANSPARENCY_MODE_OPAQUE, TRANSPARENCY_MODE_CUTOUT, TRANSPARENCY_MODE_BLEND)
_ATTR_MMD_DOUBLE_SIDED = "mmdDoubleSided"
_DX11_TECHNIQUE_BY_RENDERING = {
    (TRANSPARENCY_MODE_OPAQUE, True, False): "MMDTechnique",
    (TRANSPARENCY_MODE_CUTOUT, True, False): "MMDTechniqueTransparent",
    (TRANSPARENCY_MODE_BLEND, True, False): "MMDTechniqueTranslucent",
    (TRANSPARENCY_MODE_OPAQUE, False, False): "MMDTechniqueNoEdge",
    (TRANSPARENCY_MODE_CUTOUT, False, False): "MMDTechniqueNoEdgeTransparent",
    (TRANSPARENCY_MODE_BLEND, False, False): "MMDTechniqueNoEdgeTranslucent",
    (TRANSPARENCY_MODE_OPAQUE, True, True): "MMDTechniqueDoubleSided",
    (TRANSPARENCY_MODE_CUTOUT, True, True): "MMDTechniqueTransparentDoubleSided",
    (TRANSPARENCY_MODE_BLEND, True, True): "MMDTechniqueTranslucentDoubleSided",
    (TRANSPARENCY_MODE_OPAQUE, False, True): "MMDTechniqueNoEdgeDoubleSided",
    (TRANSPARENCY_MODE_CUTOUT, False, True): "MMDTechniqueNoEdgeTransparentDoubleSided",
    (TRANSPARENCY_MODE_BLEND, False, True): "MMDTechniqueNoEdgeTranslucentDoubleSided",
}
_DX11_RENDERING_BY_TECHNIQUE = {name: key for key, name in _DX11_TECHNIQUE_BY_RENDERING.items()}


def _ensure_shader_plugin(plugin_name) -> bool:
    """Load a Maya shader plugin once and cache whether it is available."""
    if plugin_name in _SHADER_PLUGIN_LOAD_CACHE:
        return _SHADER_PLUGIN_LOAD_CACHE[plugin_name]

    try:
        cmds.loadPlugin(plugin_name, quiet=True)
    except Exception:
        LOGGER.debug("Shader plugin '%s' is unavailable", plugin_name, exc_info=True)
        _SHADER_PLUGIN_LOAD_CACHE[plugin_name] = False
    else:
        _SHADER_PLUGIN_LOAD_CACHE[plugin_name] = True
    return _SHADER_PLUGIN_LOAD_CACHE[plugin_name]


def _warn_shader_backend_once(key, message) -> None:
    """Emit a shader backend warning at most once per Maya Python session."""
    if key in _SHADER_BACKEND_WARNED:
        return
    _SHADER_BACKEND_WARNED.add(key)
    cmds.warning(message)


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
    """Set a shader attr through maya_utils and verify the attr did not silently fail."""
    if not cmds.attributeQuery(attr_name, node=shader, exists=True):
        LOGGER.debug("Shader '%s' has no '%s' attribute", shader, attr_name)
        return False

    try:
        maya_utils.set_attribute(shader, attr_name, attr_value, attr_type)
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
    """Set Maya mesh shape doubleSided from an MMD material draw flag."""
    mesh_shapes = cmds.listRelatives(mesh_transform_or_shape, shapes=True, type="mesh") or []
    for shape in mesh_shapes:
        maya_utils.set_attribute(shape, "doubleSided", 1 if enabled else 0, "bool")


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
    """Map dx11Shader rendering state to a technique name."""
    mode = mode if mode in TRANSPARENCY_MODES else TRANSPARENCY_MODE_OPAQUE
    return _DX11_TECHNIQUE_BY_RENDERING[(mode, bool(edge_enabled), bool(double_sided))]


def _dx11_rendering_from_technique(technique: str) -> Tuple[str, bool, bool]:
    """Return transparency mode, edge flag, and double-sided flag from a technique name."""
    rendering = _DX11_RENDERING_BY_TECHNIQUE.get(technique)
    if rendering:
        return rendering
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
        maya_utils.set_custom_attributes(shader, {_ATTR_MMD_DOUBLE_SIDED: bool(enabled)})
    else:
        maya_utils.set_attribute(shader, _ATTR_MMD_DOUBLE_SIDED, bool(enabled), "bool")


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

    Keeps the shader's current edge (NoEdge) variant. Returns the technique set.
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
    """Return whether the dx11Shader technique currently renders outlines."""
    technique = _shader_technique(shader)
    _, edge_enabled, _ = _dx11_rendering_from_technique(technique)
    return bool(technique) and edge_enabled


def apply_shader_outline(shader: str, enabled: bool, edge_size: Optional[float] = None) -> str:
    """Enable/disable the dx11Shader outline pass while preserving transparency mode."""
    mode = get_transparency_mode(shader)
    double_sided = _shader_is_double_sided(shader)
    new_technique = _technique_for_transparency(mode, enabled, double_sided)
    cmds.setAttr(f"{shader}.technique", new_technique, type="string")
    _store_shader_double_sided_attr(shader, double_sided)
    if edge_size is not None and cmds.attributeQuery("EdgeSize", node=shader, exists=True):
        cmds.setAttr(f"{shader}.EdgeSize", max(0.0, min(2.0, float(edge_size))) if enabled else 0.0)
    if not cmds.attributeQuery(ATTR_MMD_SHADER_OUTLINE_ENABLED, node=shader, exists=True):
        maya_utils.set_custom_attributes(shader, {ATTR_MMD_SHADER_OUTLINE_ENABLED: bool(enabled)})
    else:
        maya_utils.set_attribute(shader, ATTR_MMD_SHADER_OUTLINE_ENABLED, bool(enabled), "bool")
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
        set_attribute_func=maya_utils.set_attribute,
    )


def _ensure_mmd_shader_uniform_attributes(shader_node):
    """MMD シェーダーで uniform 属性がない場合に補完する。

    Maya の standalone 環境では dx11Shader / GLSLShader が OGSFX/uniform を
    自動生成しないことがあるため、事前に属性を作成しておく。
    """
    import maya.api.OpenMaya as om

    uniforms = [
        ("DiffuseColor", om.MFnNumericData.kDouble, 4, True, (0.8, 0.8, 0.8, 1.0)),
        ("SpecularColor", om.MFnNumericData.kDouble, 3, True, (0.5, 0.5, 0.5)),
        ("AmbientColor", om.MFnNumericData.kDouble, 3, True, (0.3, 0.3, 0.3)),
        ("EdgeColor", om.MFnNumericData.kDouble, 4, True, (0.0, 0.0, 0.0, 1.0)),
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
        ("MMDLightDirection", om.MFnNumericData.kDouble, 3, False, (0.5, -1.0, 0.5)),
        ("MMDLightColor", om.MFnNumericData.kDouble, 3, True, (1.0, 1.0, 1.0)),
    ]

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
    _ensure_mmd_shader_uniform_attributes(shader_node)


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
        maya_utils.set_attribute(shader_node, attr_name, color, attr_type)

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


def sync_dx11_generated_uniforms(shader_nodes=None):
    """Synchronize generated dx11Shader effect attrs after import.

    In Maya GUI, dx11Shader creates attrs like DiffuseColorRGB only after the
    .fx file has been evaluated by VP2.  During material construction those
    attrs may not exist yet, so this post-import pass copies the MMD custom
    attributes into the generated effect attrs once they are present.
    """
    synced = 0
    shaders = list(shader_nodes) if shader_nodes is not None else (cmds.ls(type="dx11Shader") or [])
    for shader in shaders:
        if not shader or not cmds.objExists(shader) or cmds.nodeType(shader) != "dx11Shader":
            continue
        if cmds.attributeQuery(ATTR_MMD_DIFFUSE_COLOR, node=shader, exists=True):
            try:
                diffuse = list(cmds.getAttr(f"{shader}.{ATTR_MMD_DIFFUSE_COLOR}")[0])
                opacity = 1.0
                if cmds.attributeQuery("Opacity", node=shader, exists=True):
                    opacity = float(cmds.getAttr(f"{shader}.Opacity"))
                _set_dx11_color_uniform(shader, "DiffuseColor", diffuse + [opacity])
                synced += 1
            except Exception:
                LOGGER.warning("Failed to sync dx11 DiffuseColor uniforms for '%s'", shader, exc_info=True)

        if cmds.attributeQuery(ATTR_MMD_EDGE_COLOR, node=shader, exists=True):
            try:
                edge_color = list(cmds.getAttr(f"{shader}.{ATTR_MMD_EDGE_COLOR}")[0])
                edge_alpha = 1.0
                if cmds.attributeQuery("EdgeColorA", node=shader, exists=True):
                    edge_alpha = float(cmds.getAttr(f"{shader}.EdgeColorA"))
                _set_dx11_color_uniform(shader, "EdgeColor", edge_color + [edge_alpha])
            except Exception:
                LOGGER.warning("Failed to sync dx11 EdgeColor uniforms for '%s'", shader, exc_info=True)

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
        self.has_dx11_shaders = False
        self.unresolved_texture_count = 0
        self.unresolved_textures = []
        self.model_filepath = pmx_filepath
        self.scale = float(scale)
        # material_index -> transparency mode ("opaque"/"cutout"/"blend"),
        # precomputed from per-material UV-region texture alpha (atlas-safe).
        self._transparency_modes = {}
        self.profile = {
            "mesh_create_sec": 0.0,
            "material_create_sec": 0.0,
            "material_assign_sec": 0.0,
            "parent_sec": 0.0,
            "created_mesh_count": 0,
            "source_vertex_count": 0,
            "mesh_vertex_slots_estimated": 0,
            "face_count": 0,
            "material_count_processed": 0,
            "unresolved_texture_count": 0,
            "unresolved_textures": [],
        }
        if pmx_filepath:
            self.texture_dir = os.path.dirname(pmx_filepath)

    def _mmd_vertex_to_maya(self, position) -> Tuple[float, float, float]:
        """Convert a PMX vertex position to Maya object space with import scale."""
        return (
            float(position[0]) * self.scale,
            float(position[1]) * self.scale,
            -float(position[2]) * self.scale,
        )

    def _add_profile_time(self, key: str, start: float) -> None:
        """Accumulate timing in the converter profile."""
        self.profile[key] = round(float(self.profile.get(key, 0.0)) + time.perf_counter() - start, 6)

    def _record_created_shader(self, shader: str) -> None:
        """Record a created shader and whether it is a real dx11Shader node."""
        self.created_shaders.append(shader)
        try:
            if shader and cmds.objExists(shader) and cmds.nodeType(shader) == "dx11Shader":
                self.has_dx11_shaders = True
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
        if not settings.get(setting_keys.IMPORT_MODEL_CREATE_MMD_SHADERS):
            return
        # Default is opaque: texture-based cutout/blend auto-classification is
        # opt-in, because diffuse-texture alpha cannot reliably tell an occluding
        # translucent material (hair) from a see-through one (skirt frill) -- that
        # is a semantic choice the user makes per material in the Material tab.
        # When off, _setup_dx11_shader falls back to diffuse-alpha only
        # (alpha < 1 -> blend, otherwise opaque).
        if not settings.get(setting_keys.IMPORT_MODEL_AUTO_CLASSIFY_TRANSPARENCY, False):
            return
        texture_dir = getattr(self, "texture_dir", None)
        if not texture_dir:
            return
        try:
            from . import texture_alpha
        except Exception:
            return

        opaque_threshold = int(settings.get(setting_keys.IMPORT_MODEL_TRANSPARENCY_OPAQUE_THRESHOLD, 255))
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
                opacity = (
                    material.diffuse[3]
                    if hasattr(material, "diffuse") and len(material.diffuse) > 3
                    else 1.0
                )
                if opacity < 0.999:
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
                    resolved, triangles, alpha_cache=alpha_cache, opaque_threshold=opaque_threshold
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

        # マテリアルごとの透過モードを先に算出（使用UV領域のテクスチャαを見る）。
        self._precompute_transparency_modes(all_vertices, all_faces, all_materials, all_textures)

        # ジオメトリグループを作成
        geo_group = cmds.group(empty=True, name=GEOMETRY_GROUP, parent=root_group)

        # 設定からマテリアルごとのメッシュ分割設定を取得
        separate_by_material = settings.get(setting_keys.IMPORT_MODEL_SEPARATE_MESHES_BY_MATERIAL, False)
        split_by_morph_groups = settings.get(setting_keys.IMPORT_MODEL_SPLIT_MESHES_BY_MORPH_GROUPS, False)

        if separate_by_material:
            created_mesh = self._create_material_split_meshes(
                model_name,
                all_vertices,
                all_faces,
                all_materials,
                all_textures,
                geo_group,
                is_pmd=False,
            )
        elif split_by_morph_groups:
            created_mesh = self._create_morph_group_split_meshes(
                model_name,
                all_vertices,
                all_faces,
                all_materials,
                all_textures,
                pmx_data.morphs,
                geo_group,
            )
        else:
            created_mesh = self._create_unified_mesh(
                model_name,
                all_vertices,
                all_faces,
                all_materials,
                all_textures,
                geo_group,
            )

        maya_utils.select_objects(geo_group)
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
        parented = maya_utils.parent_objects(mesh_transform, parent_group)
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
        mesh_name = maya_utils.sanitize_text(model_name) + "_mesh"

        # 全ての頂点と面を直接使用 z*= -1
        vertices = [self._mmd_vertex_to_maya(v.position) for v in all_vertices]
        normals = [v.normal for v in all_vertices]
        normals = [(n[0], n[1], -n[2]) for n in normals]

        uvs = []
        for vertex in all_vertices:
            # flip V
            vertex.uv = (vertex.uv[0], 1.0 - vertex.uv[1])
            uvs.extend(vertex.uv)  # UVデータをフラットなリストとして追加

        # 面データを作成
        face_connects = []
        face_counts = []
        face_uv_connects = []
        material_face_ranges = []

        # 全ての面を収集
        face_offset = 0
        for i, material in enumerate(all_materials):
            num_material_faces = material.face_count // 3
            if num_material_faces == 0:
                continue

            start_face = len(face_counts)
            for j in range(face_offset, face_offset + num_material_faces):
                face = all_faces[j]
                reverced_indices = face.indices[::-1]  # PMXの面は逆順なので反転
                if _is_degenerate_face(reverced_indices):
                    continue
                face_connects.extend(reverced_indices)
                face_counts.append(len(reverced_indices))
                # UVインデックスは頂点インデックスと同じ
                face_uv_connects.extend(reverced_indices)

            end_face = len(face_counts)
            material_face_ranges.append((material, start_face, end_face))
            face_offset += num_material_faces

        # 統合メッシュを作成
        create_start = time.perf_counter()
        created_mesh = maya_utils.create_mesh_with_uvs(
            name=mesh_name,
            vertices=vertices,
            face_counts=face_counts,
            face_connects=face_connects,
            uvs=uvs,
            face_uv_connects=face_uv_connects,
            normals=normals,
        )
        self._add_profile_time("mesh_create_sec", create_start)
        self.profile["created_mesh_count"] += 1
        self.profile["source_vertex_count"] = len(vertices)
        self.profile["mesh_vertex_slots_estimated"] += len(vertices)
        self.profile["face_count"] += len(face_counts)

        # マテリアルを作成して、適切な面に割り当てる
        for i, (material, start_face, end_face) in enumerate(material_face_ranges):
            if start_face == end_face:
                continue

            # マテリアル名をサニタイズ
            # material_name = maya_utils.sanitize_text(material.name)

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
            maya_utils.assign_material_to_faces(created_mesh, shader, face_selection)
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
            maya_utils.set_viewport_backface_culling(False)

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

        # 全頂点座標 (z*= -1)
        vertices = [self._mmd_vertex_to_maya(v.position) for v in all_vertices]
        normals = [v.normal for v in all_vertices]
        normals = [(n[0], n[1], -n[2]) for n in normals]

        # 全UV (flip V)
        uvs = []
        for vertex in all_vertices:
            vertex.uv = (vertex.uv[0], 1.0 - vertex.uv[1])
            uvs.extend(vertex.uv)

        mesh_names = []
        face_offset = 0

        for i, material in enumerate(all_materials):
            num_material_faces = material.face_count // 3
            if num_material_faces == 0:
                continue

            # この material の face 範囲だけ切り出す
            sub_face_connects = []
            sub_face_counts = []
            sub_face_uv_connects = []
            source_vertex_indices = []
            source_to_local = {}

            def get_local_vertex_index(source_index):
                source_index = int(source_index)
                if is_pmd:
                    return source_index
                if source_index not in source_to_local:
                    source_to_local[source_index] = len(source_vertex_indices)
                    source_vertex_indices.append(source_index)
                return source_to_local[source_index]

            for j in range(face_offset, face_offset + num_material_faces):
                face = all_faces[j]
                reverced_indices = face.indices[::-1]
                if _is_degenerate_face(reverced_indices):
                    continue
                local_indices = [get_local_vertex_index(index) for index in reverced_indices]
                sub_face_connects.extend(local_indices)
                sub_face_counts.append(len(reverced_indices))
                sub_face_uv_connects.extend(local_indices)

            if is_pmd:
                mesh_vertices = vertices
                mesh_normals = normals
                mesh_uvs = uvs
            else:
                mesh_vertices = [vertices[index] for index in source_vertex_indices]
                mesh_normals = [normals[index] for index in source_vertex_indices]
                mesh_uvs = []
                for index in source_vertex_indices:
                    mesh_uvs.extend(uvs[index * 2 : index * 2 + 2])

            # マテリアル名からメッシュ名生成
            mat_name = material.get_name() if material.get_name() else f"material_{i}"
            sub_mesh_name = maya_utils.sanitize_text(f"{model_name}_{mat_name}_mesh")

            create_start = time.perf_counter()
            created_mesh = maya_utils.create_mesh_with_uvs(
                name=sub_mesh_name,
                vertices=mesh_vertices,
                face_counts=sub_face_counts,
                face_connects=sub_face_connects,
                uvs=mesh_uvs,
                face_uv_connects=sub_face_uv_connects,
                normals=mesh_normals,
            )
            self._add_profile_time("mesh_create_sec", create_start)
            self.profile["created_mesh_count"] += 1
            self.profile["source_vertex_count"] = len(vertices)
            self.profile["mesh_vertex_slots_estimated"] += len(mesh_vertices)
            self.profile["face_count"] += len(sub_face_counts)
            _set_mesh_double_sided(created_mesh, _material_is_double_sided(material))
            maya_utils.set_custom_attributes(
                created_mesh,
                {
                    ATTR_MMD_MATERIAL_INDEX: i,
                    "mmd_material_split_mesh": True,
                },
            )
            if not is_pmd:
                maya_utils.add_typed_attribute(created_mesh, ATTR_MMD_SOURCE_VERTEX_INDICES, "longArray")
                maya_utils.set_attribute(
                    created_mesh,
                    ATTR_MMD_SOURCE_VERTEX_INDICES,
                    source_vertex_indices,
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
            maya_utils.assign_material_to_faces(
                created_mesh, shader, f"{created_mesh}.f[0:{num_material_faces - 1}]"
            )
            self._add_profile_time("material_assign_sec", assign_start)

            # グループに追加
            parent_start = time.perf_counter()
            created_mesh = self._parent_mesh_to_group(created_mesh, geo_group)
            self._add_profile_time("parent_sec", parent_start)
            mesh_names.append(created_mesh)

            face_offset += num_material_faces

        # MMDモデル表示用にバックフェイスカリングを無効化（設定に応じて）
        disable_backface_culling = settings.get(setting_keys.IMPORT_MODEL_DISABLE_BACKFACE_CULLING, True)
        if disable_backface_culling:
            maya_utils.set_viewport_backface_culling(False)

        return mesh_names

    def _create_morph_group_split_meshes(
        self,
        model_name,
        all_vertices,
        all_faces,
        all_materials,
        all_textures,
        all_morphs,
        geo_group,
    ):
        """Create compact PMX meshes grouped by identical vertex morph material sets."""
        if not all_vertices or len(all_vertices) == 0:
            self.logger.warning(f"Mesh has zero vertices; skipping mesh creation: {model_name}")
            return []

        material_vertex_sets = self._build_material_vertex_sets(all_faces, all_materials)

        vertex_to_materials = {}
        for material_index, vertex_indices in material_vertex_sets.items():
            for vi in vertex_indices:
                vertex_to_materials.setdefault(vi, set()).add(material_index)

        morph_names_by_material_set = {}
        touched_materials = set()

        for morph in all_morphs or []:
            if getattr(morph, "morph_type", None) != PmxMorphType.VertexMorph:
                continue

            morph_materials = set()
            for offset in getattr(morph, "offsets", []) or []:
                try:
                    vertex_index = int(offset.get("vertex_index"))
                except Exception:
                    continue
                morph_materials.update(vertex_to_materials.get(vertex_index, set()))

            if not morph_materials:
                continue

            key = tuple(sorted(morph_materials))
            morph_names_by_material_set.setdefault(key, []).append(morph.get_name())
            touched_materials.update(morph_materials)

        if not morph_names_by_material_set:
            self.logger.info("No vertex morph material groups found; falling back to unified mesh import.")
            return self._create_unified_mesh(
                model_name,
                all_vertices,
                all_faces,
                all_materials,
                all_textures,
                geo_group,
            )

        material_sets = [(key, morph_names) for key, morph_names in sorted(morph_names_by_material_set.items())]
        static_materials = tuple(
            i for i, material in enumerate(all_materials) if material.face_count > 0 and i not in touched_materials
        )
        if static_materials:
            material_sets.append((static_materials, []))

        mesh_names = []
        for group_index, (material_indices, vertex_morph_names) in enumerate(material_sets):
            suffix = f"morphGroup_{group_index}" if vertex_morph_names else "morphGroup_static"
            mesh_name = maya_utils.sanitize_text(f"{model_name}_{suffix}_mesh")
            created_mesh = self._create_compact_material_subset_mesh(
                mesh_name,
                all_vertices,
                all_faces,
                all_materials,
                all_textures,
                geo_group,
                material_indices,
                material_index_attr=None,
                extra_attrs={
                    ATTR_MMD_MORPH_GROUP_SPLIT_MESH: True,
                    ATTR_MMD_VERTEX_MORPH_NAMES_JSON: json.dumps(
                        vertex_morph_names,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            )
            if created_mesh:
                mesh_names.append(created_mesh)

        disable_backface_culling = settings.get(setting_keys.IMPORT_MODEL_DISABLE_BACKFACE_CULLING, True)
        if disable_backface_culling:
            maya_utils.set_viewport_backface_culling(False)

        return mesh_names

    def _build_material_vertex_sets(self, all_faces, all_materials):
        """Return source vertex indices referenced by each material face range."""
        material_vertex_sets = {}
        face_offset = 0
        for i, material in enumerate(all_materials):
            num_material_faces = material.face_count // 3
            vertices = set()
            for j in range(face_offset, face_offset + num_material_faces):
                face = all_faces[j]
                vertices.update(int(index) for index in face.indices)
            material_vertex_sets[i] = vertices
            face_offset += num_material_faces
        return material_vertex_sets

    def _create_compact_material_subset_mesh(
        self,
        mesh_name,
        all_vertices,
        all_faces,
        all_materials,
        all_textures,
        geo_group,
        material_indices,
        material_index_attr=None,
        extra_attrs=None,
    ):
        """Create a compact PMX mesh from one or more material face ranges."""
        vertices = [self._mmd_vertex_to_maya(v.position) for v in all_vertices]
        normals = [(v.normal[0], v.normal[1], -v.normal[2]) for v in all_vertices]
        uvs = []
        for vertex in all_vertices:
            flipped_uv = (vertex.uv[0], 1.0 - vertex.uv[1])
            uvs.extend(flipped_uv)

        material_face_offsets = []
        face_offset = 0
        for material in all_materials:
            num_material_faces = material.face_count // 3
            material_face_offsets.append((face_offset, num_material_faces))
            face_offset += num_material_faces

        source_vertex_indices = []
        source_to_local = {}
        face_counts = []
        face_connects = []
        face_uv_connects = []
        local_material_ranges = []

        def get_local_vertex_index(source_index):
            source_index = int(source_index)
            if source_index not in source_to_local:
                source_to_local[source_index] = len(source_vertex_indices)
                source_vertex_indices.append(source_index)
            return source_to_local[source_index]

        for material_index in material_indices:
            material = all_materials[material_index]
            source_face_start, num_material_faces = material_face_offsets[material_index]
            if num_material_faces == 0:
                continue

            local_face_start = len(face_counts)
            for j in range(source_face_start, source_face_start + num_material_faces):
                face = all_faces[j]
                reversed_indices = face.indices[::-1]
                if _is_degenerate_face(reversed_indices):
                    continue
                local_indices = [get_local_vertex_index(index) for index in reversed_indices]
                face_connects.extend(local_indices)
                face_counts.append(len(reversed_indices))
                face_uv_connects.extend(local_indices)
            local_face_end = len(face_counts)
            if local_face_start != local_face_end:
                local_material_ranges.append((material_index, material, local_face_start, local_face_end))

        if not face_counts:
            return None

        mesh_vertices = [vertices[index] for index in source_vertex_indices]
        mesh_normals = [normals[index] for index in source_vertex_indices]
        mesh_uvs = []
        for index in source_vertex_indices:
            mesh_uvs.extend(uvs[index * 2 : index * 2 + 2])

        create_start = time.perf_counter()
        created_mesh = maya_utils.create_mesh_with_uvs(
            name=mesh_name,
            vertices=mesh_vertices,
            face_counts=face_counts,
            face_connects=face_connects,
            uvs=mesh_uvs,
            face_uv_connects=face_uv_connects,
            normals=mesh_normals,
        )
        self._add_profile_time("mesh_create_sec", create_start)
        self.profile["created_mesh_count"] += 1
        self.profile["source_vertex_count"] = len(vertices)
        self.profile["mesh_vertex_slots_estimated"] += len(mesh_vertices)
        self.profile["face_count"] += len(face_counts)

        attrs = dict(extra_attrs or {})
        if material_index_attr is not None:
            attrs[ATTR_MMD_MATERIAL_INDEX] = material_index_attr
            attrs["mmd_material_split_mesh"] = True
        if attrs:
            maya_utils.set_custom_attributes(created_mesh, attrs)
        maya_utils.add_typed_attribute(created_mesh, ATTR_MMD_SOURCE_VERTEX_INDICES, "longArray")
        maya_utils.set_attribute(created_mesh, ATTR_MMD_SOURCE_VERTEX_INDICES, source_vertex_indices, "longArray")

        if len(local_material_ranges) == 1:
            _set_mesh_double_sided(created_mesh, _material_is_double_sided(local_material_ranges[0][1]))
        else:
            # A subset with multiple materials is still a combined shape:
            # per-material culling cannot be represented by one doubleSided
            # attribute, so leave it to the viewport backface override.
            pass

        for material_index, material, start_face, end_face in local_material_ranges:
            texture_path = None
            raw_texture_path = None
            if all_textures and material.texture_index != -1:
                raw_texture_path = all_textures[material.texture_index]
                texture_path = maya_material_utils.sanitize_texture_path(raw_texture_path, self.texture_dir)

            material_start = time.perf_counter()
            shader = self._create_material(
                material=material,
                texture_path=texture_path,
                all_textures=all_textures,
                is_pmd=False,
                material_index=material_index,
                original_texture_path=raw_texture_path,
            )
            self._add_profile_time("material_create_sec", material_start)
            self._record_created_shader(shader)
            self.profile["material_count_processed"] += 1

            assign_start = time.perf_counter()
            maya_utils.assign_material_to_faces(created_mesh, shader, f"{created_mesh}.f[{start_face}:{end_face - 1}]")
            self._add_profile_time("material_assign_sec", assign_start)

        parent_start = time.perf_counter()
        created_mesh = self._parent_mesh_to_group(created_mesh, geo_group)
        self._add_profile_time("parent_sec", parent_start)
        return created_mesh

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
        sanitized_name = maya_utils.sanitize_text(material.get_name())
        # 名前が空の場合はデフォルト名を使用
        if not sanitized_name:
            sanitized_name = f"material_{material_index if material_index is not None else 0}"

        # create_mmd_shaders設定を確認
        create_mmd_shaders = settings.get(setting_keys.IMPORT_MODEL_CREATE_MMD_SHADERS)

        if create_mmd_shaders:
            backend = str(settings.get(setting_keys.IMPORT_MODEL_MMD_SHADER_BACKEND, "auto")).lower()
            if backend not in {"auto", "dx11", "glsl", "standard"}:
                cmds.warning(f"Unknown mmd_shader_backend '{backend}', fallback to auto.")
                backend = "auto"

            if backend != "standard":
                backend_order = ["dx11", "glsl"] if backend == "auto" else [backend]

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
            custom_attrs[ATTR_MMD_SHADER_OUTLINE_ENABLED] = False
        else:
            custom_attrs[ATTR_MMD_SPHERE_MODE] = int(material.sphere_mode)
            custom_attrs[ATTR_MMD_SPHERE_TEXTURE_INDEX] = material.sphere_texture_index
            custom_attrs[ATTR_MMD_TEXTURE_INDEX] = material.texture_index
            custom_attrs[ATTR_MMD_DRAW_FLAGS] = int(material.draw_flag)
            custom_attrs[ATTR_MMD_EDGE_COLOR] = material.edge_color[:3]
            custom_attrs[ATTR_MMD_EDGE_SIZE] = material.edge_size
            custom_attrs[ATTR_MMD_SHADER_OUTLINE_ENABLED] = False
            custom_attrs[_ATTR_MMD_DOUBLE_SIDED] = _material_is_double_sided(material)
            custom_attrs[ATTR_MMD_MEMO] = material.memo
            custom_attrs[ATTR_MMD_SHARED_TOON_FLAG] = int(material.shared_toon_flag)

        maya_utils.set_custom_attributes(
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
    ):
        """標準のstandardSurfaceシェーダーを設定"""

        # マテリアル名をサニタイズ（テクスチャノード名に使用）
        sanitized_name = maya_utils.sanitize_text(material.name if material.name else "material")

        # 基本色設定（Diffuse）
        maya_utils.set_attribute(shader, "baseColor", material.diffuse[:3], "double3")

        # AlphaをOpacityに変換（StandardSurfaceではopacityを使用）
        maya_utils.set_attribute(
            shader,
            "opacity",
            (material.diffuse[3], material.diffuse[3], material.diffuse[3]),
            "double3",
        )

        # スペキュラー設定（MMDのspecularをStandardSurfaceにマッピング）
        if hasattr(material, "specular"):
            # スペキュラー色
            maya_utils.set_attribute(shader, "specularColor", material.specular[:3], "double3")

            # スペキュラー係数の取得（PMDとPMXで異なる）
            specular_coef = None
            if hasattr(material, "specular_coefficient"):
                specular_coef = material.specular_coefficient
            elif hasattr(material, "specular_power"):
                specular_coef = material.specular_power

            if specular_coef is not None:
                maya_utils.set_attribute(shader, "specularColor", material.specular[:3], "double3")

        # アンビエント設定（StandardSurfaceでは間接光の強度として使用）
        if hasattr(material, "ambient"):
            # アンビエント色の平均値を間接光の強度として使用
            ambient_intensity = (material.ambient[0] + material.ambient[1] + material.ambient[2]) / 3.0
            # エミッションとして微弱に設定（アンビエント光の表現）
            maya_utils.set_attribute(shader, "emission", ambient_intensity * 0.1, "float")
            maya_utils.set_attribute(shader, "emissionColor", material.ambient[:3], "double3")

        # 非金属マテリアルとして設定（MMDは基本的に非金属）
        maya_utils.set_attribute(shader, "metalness", 0.0, "float")

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
                place_uv_node = cmds.shadingNode(
                    "place2dTexture",
                    asUtility=True,
                    name=sanitized_name + "_place2dTexture",
                )
                # 標準的なUV接続
                cmds.connectAttr(place_uv_node + ".outUV", file_node + ".uvCoord")
                cmds.connectAttr(file_node + ".outColor", shader + ".baseColor")

                maya_utils.set_attribute(file_node, "fileTextureName", file_texture_path, "string")
                maya_material_utils.mark_mmd_texture_file_node(
                    file_node,
                    raw_texture_path,
                    self.model_filepath,
                    unresolved=unresolved,
                )
                if cache_path:
                    maya_utils.set_custom_attributes(
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

        setup_ok &= _set_shader_attribute_checked(shader, "DiffuseColor", material.diffuse, "double4")

        if hasattr(material, "specular"):
            _set_shader_attribute_checked(shader, "SpecularColor", material.specular[:3], "double3")

        if hasattr(material, "ambient"):
            _set_shader_attribute_checked(shader, "AmbientColor", material.ambient[:3], "double3")

        shininess = None
        if hasattr(material, "specular_coefficient"):
            shininess = material.specular_coefficient
        elif hasattr(material, "specular_power"):
            shininess = material.specular_power
        if shininess is not None:
            _set_shader_attribute_checked(shader, "Shininess", shininess, "float")

        edge_color = [0.0, 0.0, 0.0, 1.0]
        if hasattr(material, "edge_color"):
            edge_color = list(material.edge_color)
            if len(edge_color) == 3:
                edge_color.append(1.0)
        setup_ok &= _set_shader_attribute_checked(shader, "EdgeColor", edge_color, "double4")
        setup_ok &= _set_shader_attribute_checked(shader, "EdgeSize", 0.0, "float")

        sphere_mode = getattr(material, "sphere_mode", 0)
        setup_ok &= _set_shader_attribute_checked(shader, "SphereMode", int(sphere_mode), "long")

        opacity = material.diffuse[3] if hasattr(material, "diffuse") and len(material.diffuse) > 3 else 1.0
        setup_ok &= _set_shader_attribute_checked(shader, "Opacity", opacity, "float")

        self._apply_custom_attributes(
            shader,
            material,
            all_textures,
            is_pmd,
            material_index,
            texture_path,
            None,
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
        maya_utils.set_attribute(file_node, "fileTextureName", file_texture_path, "string")
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
            maya_utils.set_custom_attributes(
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
        maya_utils.set_attribute(shader, "shader", shader_fx_path, "string")

        # mayapy standalone では dx11Shader が .fx ファイルから uniform 属性を
        # 自動生成しないため、事前に動的アトリビュートとして作成しておく
        _ensure_dx11_uniform_attributes(shader)

        # Prefer the accurate per-material UV-region classification computed up
        # front; fall back to the simple diffuse-alpha rule if unavailable.
        mode = self._transparency_modes.get(material_index)
        if mode is None:
            mode = _classify_material_transparency(material, texture_path)
        double_sided = _material_is_double_sided(material)
        technique = _technique_for_transparency(mode, False, double_sided)
        cmds.setAttr(f"{shader}.technique", technique, type="string")
        _store_transparency_mode_attr(shader, mode)
        _store_shader_double_sided_attr(shader, double_sided)

        # 基本色設定（Diffuse）
        _set_dx11_color_uniform(shader, "DiffuseColor", material.diffuse)
        opacity = material.diffuse[3] if hasattr(material, "diffuse") and len(material.diffuse) > 3 else 1.0
        maya_utils.set_attribute(shader, "Opacity", opacity, "float")

        # スペキュラー設定
        if hasattr(material, "specular"):
            maya_utils.set_attribute(
                shader,
                "SpecularColor",
                material.specular[:3],
                "double3",
            )

        # スペキュラー係数の設定（PMDとPMXで異なる）
        specular_coef = None
        if hasattr(material, "specular_coefficient"):
            specular_coef = material.specular_coefficient
        elif hasattr(material, "specular_power"):
            specular_coef = material.specular_power

        if specular_coef is not None:
            maya_utils.set_attribute(shader, "Shininess", specular_coef, "float")

        # アンビエント設定
        if hasattr(material, "ambient"):
            maya_utils.set_attribute(
                shader,
                "AmbientColor",
                material.ambient[:3],
                "double3",
            )

        # エッジ設定（PMXのみ）
        if not is_pmd:
            # エッジ色
            _set_dx11_color_uniform(shader, "EdgeColor", material.edge_color)
        maya_utils.set_attribute(shader, "EdgeSize", 0.0, "float")

        # スフィアモード設定
        sphere_mode = getattr(material, "sphere_mode", 0)
        maya_utils.set_attribute(shader, "SphereMode", int(sphere_mode), "long")

        for texture_flag in ("HasMainTexture", "HasSphereTexture", "HasToonTexture"):
            if cmds.attributeQuery(texture_flag, node=shader, exists=True):
                maya_utils.set_attribute(shader, texture_flag, 0, "long")

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
