"""Stage a saved legacy model and exercise its explicit, reversible migration."""


def check_legacy_migration(cmds, root, proxies, sources, shaders, output_dir):
    from mmd_tools.converters.export_scene_collector import _collect_mmd_material_dict
    from mmd_tools.converters.material_morph_runtime import build_material_morph_graph
    from mmd_tools.converters.mesh_converter import _create_backend_replacement
    from mmd_tools.converters.render_preview_migration import migrate_model_render_preview, _copy_mmd_attributes

    cmds.loadPlugin("dx11Shader", quiet=True)
    for shader in shaders.values():
        legacy = _create_backend_replacement(shader, "dx11")
        _copy_mmd_attributes(shader, legacy)
        files = cmds.listConnections(shader + ".baseColor", s=True, d=False, type="file") or []
        if files:
            assert len(files) == 1
            cmds.connectAttr(files[0] + ".outColor", legacy + ".MainTexture", force=True)
        assert _collect_mmd_material_dict(legacy) == _collect_mmd_material_dict(shader)
        for attr in ("outColor", "message"):
            for destination in cmds.listConnections(shader + "." + attr, s=False, d=True, plugs=True) or []:
                cmds.connectAttr(legacy + "." + attr, destination, force=True)
        old = cmds.rename(shader, shader + "__fixture")
        cmds.rename(legacy, shader)
        cmds.delete(old)
    for proxy, source in zip(proxies, sources):
        source_parent = cmds.listRelatives(source, parent=True, fullPath=True)[0]
        proxy_parent = cmds.listRelatives(proxy, parent=True, fullPath=True)[0]
        proxy = cmds.parent(proxy, source_parent, shape=True, relative=True)[0]
        cmds.delete(proxy_parent)
        cmds.connectAttr(proxy + ".sourceVisibility", source + ".visibility", force=True)
    graph = build_material_morph_graph(root)
    assert graph["success"], graph
    scene = str(output_dir / "legacy.ma")
    cmds.file(rename=scene)
    cmds.file(save=True, type="mayaAscii", force=True)
    cmds.file(scene, open=True, force=True)
    legacy_ids = {shader: cmds.ls(shader, uuid=True) for shader in shaders.values()}
    result = migrate_model_render_preview(root)
    assert result == {"materials": len(shaders), "sources": len(sources)}
    assert all(cmds.nodeType(shader) == "standardSurface" for shader in shaders.values())
    assert all(not cmds.listConnections(source + ".visibility", s=True, d=False) for source in sources)
    cmds.undo()
    assert all(cmds.ls(shader, uuid=True) == identity for shader, identity in legacy_ids.items())
    assert all(cmds.nodeType(shader) == "dx11Shader" for shader in shaders.values())
    cmds.redo()
    return result
