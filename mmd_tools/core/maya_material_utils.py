"""Maya material and MMD texture provenance helpers."""

import os

from maya import cmds

from mmd_tools.core import maya_attribute_utils
from mmd_tools.core.constants import (
    ATTR_MMD_ORIGINAL_TEXTURE_PATH,
    ATTR_MMD_SOURCE_MODEL_PATH,
    ATTR_MMD_TEXTURE_CACHE_PATH,
    ATTR_MMD_TEXTURE_UNRESOLVED,
)

from .logger import get_logger
from . import maya_name_utils
from .texture_path_cache import (
    classify_texture_resolution,
    decode_original_texture_path,
    is_unreadable_file_texture_path,
    resolve_texture_to_cache,
)

logger = get_logger(__name__)

DX11_TEXTURE_SLOTS = {
    "_texture": ("MainTexture", "HasMainTexture"),
    "_sphere_texture": ("SphereTexture", "HasSphereTexture"),
    "_toon_texture": ("ToonTexture", "HasToonTexture"),
}

ATTR_MMD_TEXTURE_SOURCE_KIND = "mmd_texture_source_kind"
ATTR_MMD_SHARED_TOON_ID = "mmd_shared_toon_id"


def sanitize_texture_path(texture_path, texture_dir):
    """テクスチャパスをMaya用にサニタイズする。"""
    if not texture_path:
        return None

    if not os.path.isabs(texture_path):
        full_texture_path = os.path.join(texture_dir, texture_path)
    else:
        full_texture_path = texture_path

    full_texture_path = os.path.normpath(full_texture_path)
    if not os.path.exists(full_texture_path):
        # Maya's Windows stdout can remain cp932 even when the PMX path and
        # metadata are Unicode.  Logging through the Maya-safe handler keeps a
        # missing non-ASCII texture diagnostic from aborting the model import.
        logger.warning("Texture file not found: %s", full_texture_path)
        return None

    return full_texture_path


def mark_mmd_texture_file_node(
    file_node,
    original_path,
    model_path,
    unresolved=False,
    source_kind="pmx_texture",
    shared_toon_id="",
):
    """Store MMD texture provenance on a Maya file node."""
    attrs = {
        ATTR_MMD_ORIGINAL_TEXTURE_PATH: "" if original_path is None else os.fspath(original_path),
        ATTR_MMD_SOURCE_MODEL_PATH: model_path or "",
        ATTR_MMD_TEXTURE_UNRESOLVED: bool(unresolved),
        ATTR_MMD_TEXTURE_SOURCE_KIND: source_kind or "pmx_texture",
    }
    if shared_toon_id:
        attrs[ATTR_MMD_SHARED_TOON_ID] = shared_toon_id
    maya_attribute_utils.set_custom_attributes(file_node, attrs)


def get_mmd_original_texture_path(file_node):
    """Return the original PMX texture path stored on a Maya file node."""
    value = maya_attribute_utils.get_attribute(file_node, ATTR_MMD_ORIGINAL_TEXTURE_PATH)
    return decode_original_texture_path(value)


def is_mmd_file_node_unreadable(file_node):
    """Return whether an MMD file node currently points at an unreadable texture."""
    texture_path = maya_attribute_utils.get_attribute(file_node, "fileTextureName")
    return is_unreadable_file_texture_path(texture_path)


def find_material_texture_file_node(material):
    """Find the base texture file node used by a material, if any."""

    def find_upstream_file_node(plug, visited=None):
        """Walk utility nodes to find a file node driving a material input."""
        if visited is None:
            visited = set()
        node = plug.split(".", 1)[0]
        if node in visited:
            return None
        visited.add(node)
        if cmds.nodeType(node) == "file":
            return node
        if cmds.nodeType(node) != "multiplyDivide":
            return None
        for input_attr in ("input1", "input1X", "input1Y", "input1Z", "input2", "input2X", "input2Y", "input2Z"):
            incoming = cmds.listConnections(
                f"{node}.{input_attr}",
                source=True,
                destination=False,
                plugs=True,
            ) or []
            for upstream in incoming:
                file_node = find_upstream_file_node(upstream, visited)
                if file_node:
                    return file_node
        return None

    shader_type = cmds.nodeType(material)
    texture_attrs = []
    if shader_type == "standardSurface":
        texture_attrs.append(f"{material}.baseColor")
    elif shader_type in {"dx11Shader", "GLSLShader"}:
        if cmds.attributeQuery("MainTexture", node=material, exists=True):
            texture_attrs.append(f"{material}.MainTexture")
        if cmds.attributeQuery("DiffuseTexture", node=material, exists=True):
            texture_attrs.append(f"{material}.DiffuseTexture")
    if cmds.attributeQuery("color", node=material, exists=True):
        texture_attrs.append(f"{material}.color")

    for attr in texture_attrs:
        connections = cmds.listConnections(
            attr,
            source=True,
            destination=False,
            plugs=True,
        ) or []
        for connection in connections:
            file_node = find_upstream_file_node(connection)
            if file_node:
                return file_node
    return None


def classify_mmd_texture_file_node(file_node):
    """Classify a Maya MMD file node for on-demand texture resolution."""
    if maya_attribute_utils.get_attribute(file_node, ATTR_MMD_TEXTURE_SOURCE_KIND) == "shared_toon":
        return None
    original_path = get_mmd_original_texture_path(file_node)
    model_path = maya_attribute_utils.get_attribute(file_node, ATTR_MMD_SOURCE_MODEL_PATH)
    file_texture_path = maya_attribute_utils.get_attribute(file_node, "fileTextureName") or ""
    if not model_path:
        return None
    return classify_texture_resolution(
        original_path=original_path,
        file_texture_path=file_texture_path,
        model_path=model_path,
    )


def resolve_mmd_texture_file_node(file_node, workspace_root=None):
    """Resolve one MMD file node into the workspace texture cache."""
    if maya_attribute_utils.get_attribute(file_node, ATTR_MMD_TEXTURE_SOURCE_KIND) == "shared_toon":
        return None
    if workspace_root is None:
        workspace_root = cmds.workspace(q=True, rootDirectory=True)
    original_path = get_mmd_original_texture_path(file_node)
    model_path = maya_attribute_utils.get_attribute(file_node, ATTR_MMD_SOURCE_MODEL_PATH)
    file_texture_path = maya_attribute_utils.get_attribute(file_node, "fileTextureName") or ""
    if not model_path:
        return None

    resolution = resolve_texture_to_cache(
        original_path=original_path,
        file_texture_path=file_texture_path,
        model_path=model_path,
        workspace_root=workspace_root,
    )
    if resolution.status == "resolved" and resolution.cache_path:
        maya_attribute_utils.set_attribute(file_node, "fileTextureName", resolution.cache_path, "string")
        maya_attribute_utils.set_custom_attributes(
            file_node,
            {
                ATTR_MMD_TEXTURE_CACHE_PATH: resolution.cache_path,
                ATTR_MMD_TEXTURE_UNRESOLVED: False,
            },
        )
    elif resolution.status == "unrecoverable":
        maya_attribute_utils.set_custom_attributes(file_node, {ATTR_MMD_TEXTURE_UNRESOLVED: True})
    return resolution


def bind_dx11_texture_file_node(shader, file_node, texture_attr, has_attr, cmds_module=None, set_attribute_func=None):
    """Bind a Maya file node to one dx11Shader texture slot."""
    destination_attr = f"{shader}.{texture_attr}"
    cmds_ref = cmds_module or cmds
    set_attr = set_attribute_func or maya_attribute_utils.set_attribute
    try:
        existing = cmds_ref.listConnections(
            f"{file_node}.outColor",
            source=False,
            destination=True,
            plugs=True,
        ) or []
        if not isinstance(existing, (list, tuple, set)) or destination_attr not in existing:
            cmds_ref.connectAttr(f"{file_node}.outColor", destination_attr, force=True)
        if cmds_ref.attributeQuery(has_attr, node=shader, exists=True):
            set_attr(shader, has_attr, 1, "long")
        return True
    except Exception as exc:
        logger.warning(
            "Failed to bind dx11 texture file node '%s' to '%s': %s",
            file_node,
            destination_attr,
            exc,
        )
        return False


def _dx11_texture_slot_from_attr(attr_name):
    for texture_attr, has_attr in DX11_TEXTURE_SLOTS.values():
        if attr_name == texture_attr:
            return texture_attr, has_attr
    return None


def _connected_dx11_texture_slot(file_node):
    connections = cmds.listConnections(
        f"{file_node}.outColor",
        source=False,
        destination=True,
        plugs=True,
    ) or []
    if not isinstance(connections, (list, tuple, set)):
        return None
    for plug in connections:
        if "." not in plug:
            continue
        shader, attr_name = plug.rsplit(".", 1)
        slot = _dx11_texture_slot_from_attr(attr_name)
        if slot and cmds.objExists(shader):
            return shader, slot[0], slot[1]
    return None


def _infer_dx11_texture_slot_from_file_node(file_node):
    sorted_slots = sorted(DX11_TEXTURE_SLOTS.items(), key=lambda item: len(item[0]), reverse=True)
    for suffix, (texture_attr, has_attr) in sorted_slots:
        if not file_node.endswith(suffix):
            continue
        shader = file_node[: -len(suffix)]
        if cmds.objExists(shader):
            return shader, texture_attr, has_attr
    return None


def rebind_resolved_mmd_dx11_texture(file_node):
    """Reconnect one resolved MMD file node to its dx11Shader texture slot."""
    if not cmds.attributeQuery(ATTR_MMD_ORIGINAL_TEXTURE_PATH, node=file_node, exists=True):
        return {"status": "skipped", "reason": "not_mmd_texture_file_node"}

    target = _connected_dx11_texture_slot(file_node) or _infer_dx11_texture_slot_from_file_node(file_node)
    if not target:
        return {"status": "skipped", "reason": "dx11_texture_slot_not_found"}

    shader, texture_attr, has_attr = target
    if cmds.nodeType(shader) != "dx11Shader":
        return {"status": "skipped", "reason": "not_dx11_shader"}
    if not cmds.attributeQuery(texture_attr, node=shader, exists=True):
        return {"status": "skipped", "reason": "texture_attr_missing"}
    if not cmds.attributeQuery(has_attr, node=shader, exists=True):
        return {"status": "skipped", "reason": "has_attr_missing"}

    if not bind_dx11_texture_file_node(shader, file_node, texture_attr, has_attr):
        return {
            "status": "failed",
            "reason": "connect_failed",
            "shader": shader,
            "texture_attr": texture_attr,
            "has_attr": has_attr,
        }

    return {
        "status": "rebound",
        "reason": "connected",
        "shader": shader,
        "texture_attr": texture_attr,
        "has_attr": has_attr,
    }


def rebind_resolved_scene_mmd_dx11_textures(results):
    """Rebind resolved scene texture results and annotate each result."""
    rebound = 0
    skipped = 0
    failed = 0
    for result in results:
        if getattr(result, "status", None) != "resolved":
            continue
        file_node = getattr(result, "file_node", None)
        if not file_node:
            setattr(result, "rebind_status", "skipped")
            setattr(result, "rebind_reason", "missing_file_node")
            skipped += 1
            continue
        rebind = rebind_resolved_mmd_dx11_texture(file_node)
        setattr(result, "rebind_status", rebind["status"])
        setattr(result, "rebind_reason", rebind["reason"])
        for key in ("shader", "texture_attr", "has_attr"):
            if key in rebind:
                setattr(result, f"rebind_{key}", rebind[key])
        if rebind["status"] == "rebound":
            rebound += 1
        elif rebind["status"] == "failed":
            failed += 1
        else:
            skipped += 1
    return {"rebound": rebound, "skipped": skipped, "failed": failed}


def resolve_mmd_material_texture(material, workspace_root=None):
    """Resolve the selected material's base texture file node, if present."""
    file_node = find_material_texture_file_node(material)
    if not file_node:
        return None
    return resolve_mmd_texture_file_node(file_node, workspace_root=workspace_root)


def resolve_scene_mmd_textures(workspace_root=None, file_nodes=None):
    """Resolve broken MMD file nodes, optionally restricted to a model-owned set."""
    results = []
    candidates = (cmds.ls(type="file") or []) if file_nodes is None else file_nodes
    for file_node in candidates:
        if not cmds.attributeQuery(ATTR_MMD_ORIGINAL_TEXTURE_PATH, node=file_node, exists=True):
            continue
        classification = classify_mmd_texture_file_node(file_node)
        if classification and classification.status == "resolvable":
            resolution = resolve_mmd_texture_file_node(file_node, workspace_root=workspace_root)
            if resolution is not None and not getattr(resolution, "file_node", None):
                resolution.file_node = file_node
            results.append(resolution)
        elif classification:
            classification.file_node = file_node
            results.append(classification)
    rebind_summary = rebind_resolved_scene_mmd_dx11_textures(results)
    if rebind_summary["rebound"]:
        cmds.refresh(force=True)
    return results


def create_material(name, color, texture_path=None, texture_dir="", model_path=None):
    """Mayaシーンにマテリアルを作成します。"""
    sanitized_name = maya_name_utils.sanitize_text(name)
    shader = cmds.shadingNode("lambert", asShader=True, name=sanitized_name)
    maya_attribute_utils.set_attribute(shader, "color", color[:3], "double3")
    transparency = 1.0 - color[3]
    maya_attribute_utils.set_attribute(shader, "transparency", [transparency, transparency, transparency], "double3")

    maya_attribute_utils.set_custom_attributes(shader, {"mmd_material_name": name})

    if texture_path:
        full_texture_path = os.path.normpath(os.path.join(texture_dir, texture_path))
        file_node = cmds.shadingNode("file", asTexture=True, name=sanitized_name + "_file")
        place_uv_node = cmds.shadingNode(
            "place2dTexture",
            asUtility=True,
            name=sanitized_name + "_place2dTexture",
        )
        cmds.connectAttr(place_uv_node + ".outUV", file_node + ".uvCoord")
        cmds.connectAttr(file_node + ".outColor", shader + ".color")

        maya_attribute_utils.set_attribute(file_node, "fileTextureName", full_texture_path, "string")
        source_model_path = model_path or os.path.join(texture_dir, "_mmd_tools_legacy_model.pmd")
        mark_mmd_texture_file_node(
            file_node,
            texture_path,
            source_model_path,
            unresolved=not os.path.exists(full_texture_path),
        )
        if not os.path.exists(full_texture_path):
            cmds.warning(f"Texture file not found: {full_texture_path}")

    return shader


def assign_material(mesh_name, shader_node):
    """メッシュにマテリアルを割り当てます。"""
    sanitized_shader_name = shader_node + "SG"
    sg_name = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=sanitized_shader_name)
    cmds.connectAttr(shader_node + ".outColor", f"{sg_name}.surfaceShader", force=True)
    cmds.sets(mesh_name, edit=True, forceElement=sg_name)


def assign_material_to_faces(mesh_name, shader_node, face_selection):
    """メッシュの特定の面にマテリアルを割り当てます。"""
    if not cmds.objExists(shader_node):
        logger.error(f"Shader node '{shader_node}' does not exist")
        return

    sanitized_shader_name = shader_node + "SG"
    sg_name = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=sanitized_shader_name)

    shader_type = cmds.nodeType(shader_node)
    if cmds.attributeQuery("outColor", node=shader_node, exists=True):
        cmds.connectAttr(shader_node + ".outColor", f"{sg_name}.surfaceShader", force=True)
    elif shader_type == "dx11Shader":
        cmds.connectAttr(shader_node + ".message", f"{sg_name}.surfaceShader", force=True)
    else:
        logger.error("Shader node '%s' has no outColor attribute", shader_node)
        return

    cmds.sets(face_selection, edit=True, forceElement=sg_name)
