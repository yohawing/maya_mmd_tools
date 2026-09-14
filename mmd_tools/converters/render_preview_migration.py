"""Explicit migration of one legacy MMD model to editable stock VP2."""

import json

from maya import cmds, mel
from maya.api import OpenMaya as om

from mmd_tools.converters.material_morph_runtime import (
    _collect_shaders_by_material_index,
    build_material_morph_graph,
)
from mmd_tools.converters.mesh_converter import _MIGRATED_MMD_ATTRS
from mmd_tools.core.maya_mesh_utils import separate_render_proxy

_CANONICAL_ATTRIBUTES = frozenset(_MIGRATED_MMD_ATTRS)


def _copy_mmd_attributes(source, target):
    """Use Maya's own addAttr serialization to preserve dynamic attribute types."""
    selection = om.MSelectionList()
    selection.add(source)
    node = om.MFnDependencyNode(selection.getDependNode(0))
    names = []
    for name in cmds.listAttr(source, userDefined=True) or []:
        parents = cmds.attributeQuery(name, node=source, listParent=True) or []
        root = parents[0] if parents else name
        if root.startswith("mmd_") or root in _CANONICAL_ATTRIBUTES:
            names.append(name)
            if not cmds.attributeQuery(name, node=target, exists=True):
                command = om.MFnAttribute(node.attribute(name)).getAddAttrCmd(True).strip().rstrip(";")
                mel.eval(command + " " + json.dumps(target) + ";")
    for name in names:
        if cmds.attributeQuery(name, node=source, listParent=True):
            continue
        kind = cmds.getAttr(source + "." + name, type=True)
        if kind == "message":
            continue
        value = cmds.getAttr(source + "." + name)
        if kind == "string":
            cmds.setAttr(target + "." + name, value or "", type="string")
        elif kind in {"double3", "float3"}:
            cmds.setAttr(target + "." + name, *value[0], type=kind)
        elif kind in {"bool", "long", "short", "byte", "enum", "double", "float"}:
            cmds.setAttr(target + "." + name, value)
        else:
            raise RuntimeError(f"Unsupported MMD attribute for migration: {source}.{name} ({kind})")
    return set(names)


def migrate_model_render_preview(root):
    """Migrate explicitly, preserving assignments and canonical values in one Undo."""
    if not cmds.undoInfo(query=True, state=True):
        raise RuntimeError("Enable Maya Undo before migrating an existing model")
    shaders = _collect_shaders_by_material_index(root)
    legacy_shaders = [s for s in shaders.values() if cmds.nodeType(s) in {"dx11Shader", "GLSLShader"}]
    shapes = cmds.listRelatives(root, allDescendents=True, fullPath=True) or []
    proxies = [shape for shape in shapes if cmds.nodeType(shape) == "mmdRenderShape"]
    visibility_links = []
    for proxy in proxies:
        input_source = cmds.connectionInfo(proxy + ".inputMesh", sourceFromDestination=True)
        source = input_source.rsplit(".", 1)[0] if input_source else ""
        if not source or cmds.nodeType(source) != "mesh":
            raise RuntimeError(f"Expected one editable source mesh for {proxy}")
        destination = source + ".visibility"
        if cmds.isConnected(proxy + ".sourceVisibility", destination):
            visibility_links.append((proxy, source))
    if not legacy_shaders and not visibility_links:
        return {"materials": 0, "sources": 0}
    mutated = False
    cmds.undoInfo(openChunk=True, chunkName="Migrate MMD Render Preview")
    try:
        old_nodes = []
        for source in legacy_shaders:
            replacement = cmds.shadingNode("standardSurface", asShader=True, name=source + "__standard")
            mutated = True
            names = _copy_mmd_attributes(source, replacement)
            for attr, value in (("base", 1.0), ("metalness", 0.0), ("specular", .2), ("specularRoughness", .6)):
                cmds.setAttr(replacement + "." + attr, value)
            # The shared toon is identified by its table index. Native Render
            # resolves that built-in texture itself; an explicit path is custom toon metadata.
            if cmds.attributeQuery("mmd_shared_toon_flag", node=replacement, exists=True) and cmds.getAttr(replacement + ".mmd_shared_toon_flag"):
                for attr in ("mmd_toon_path", "mmd_resolved_toon_texture_path"):
                    if cmds.attributeQuery(attr, node=replacement, exists=True):
                        cmds.setAttr(replacement + "." + attr, "", type="string")
            cmds.setAttr(replacement + ".specularColor", *cmds.getAttr(source + ".specular_color")[0], type="double3")
            cmds.setAttr(replacement + ".emission", 1.0)
            cmds.setAttr(replacement + ".emissionColor", *cmds.getAttr(source + ".ambient_color")[0], type="double3")
            connections = cmds.listConnections(source, source=True, destination=True, plugs=True, connections=True) or []
            for local, other in zip(connections[::2], connections[1::2]):
                attr = local.split(".", 1)[1]
                if attr not in names and attr not in {"message", "outColor"}:
                    continue
                target_plug = replacement + "." + attr
                if cmds.connectionInfo(local, isDestination=True):
                    cmds.connectAttr(other, target_plug, force=True)
                else:
                    cmds.connectAttr(target_plug, other, force=True)
            files = cmds.listConnections(source + ".MainTexture", source=True, destination=False, type="file") or []
            if files:
                texture = files[0]
                destinations = cmds.listConnections(texture + ".outColor", source=False, destination=True, plugs=True) or []
                if len(destinations) > 1:
                    texture = cmds.duplicate(texture, inputConnections=True, name=source + "_previewTexture")[0]
                cmds.connectAttr(texture + ".outColor", replacement + ".baseColor", force=True)
                ambient = cmds.shadingNode("multiplyDivide", asUtility=True, name=source + "_ambientMultiply")
                cmds.setAttr(ambient + ".input2", *cmds.getAttr(source + ".ambient_color")[0], type="double3")
                cmds.connectAttr(texture + ".outColor", ambient + ".input1")
                cmds.connectAttr(ambient + ".output", replacement + ".emissionColor")
            old_nodes.append(cmds.rename(source, source + "__legacy"))
            cmds.rename(replacement, source)
        for proxy, source in visibility_links:
            cmds.disconnectAttr(proxy + ".sourceVisibility", source + ".visibility")
            mutated = True
            cmds.setAttr(source + ".visibility", True)
            source_parent = cmds.listRelatives(source, parent=True, fullPath=True)[0]
            proxy_parent = cmds.listRelatives(proxy, parent=True, fullPath=True)[0]
            if source_parent == proxy_parent:
                parents = cmds.listRelatives(source_parent, parent=True, fullPath=True) or []
                separate_render_proxy(source_parent, parents[0] if parents else None)
        graph = build_material_morph_graph(root)
        if not graph["success"]:
            raise RuntimeError(f"Could not bind migrated material graph: {graph['skipped']}")
        if old_nodes:
            cmds.delete(old_nodes)
    except Exception:
        cmds.undoInfo(closeChunk=True)
        if mutated:
            cmds.undo()
        raise
    else:
        cmds.undoInfo(closeChunk=True)
    return {"materials": len(legacy_shaders), "sources": len(visibility_links)}
