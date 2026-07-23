"""Contracts that keep MMD effects on the active VP2 shader backend."""

from types import SimpleNamespace
from unittest import mock

from tests.common.maya_stub import install_maya_stub

install_maya_stub()

from mmd_tools.converters import material_morph_runtime, mesh_converter  # noqa: E402


def test_backend_resolver_follows_effective_vp2_api():
    resolve = material_morph_runtime.resolve_mmd_shader_backend
    assert resolve("dx11", material_morph_runtime.VP2_API_DIRECTX11) == "dx11"
    assert resolve("glsl", material_morph_runtime.VP2_API_DIRECTX11) == "dx11"
    assert resolve("glsl", material_morph_runtime.VP2_API_OPENGL_CORE) == "glsl"
    assert resolve("dx11", material_morph_runtime.VP2_API_OPENGL) == "glsl"
    assert resolve("not-a-backend", material_morph_runtime.VP2_API_DIRECTX11) == "dx11"
    assert resolve("dx11", material_morph_runtime.VP2_API_UNKNOWN) == "standard"
    assert resolve("standard", material_morph_runtime.VP2_API_DIRECTX11) == "standard"


def test_effective_backend_corrects_stale_preference_at_runtime_only():
    mesh_converter._SHADER_BACKEND_WARNED.clear()
    settings = mock.Mock()
    settings.get.return_value = "glsl"
    with mock.patch.object(mesh_converter, "settings", settings), mock.patch.object(
        mesh_converter, "detect_effective_vp2_draw_api", return_value="directx11"
    ), mock.patch.object(mesh_converter.cmds, "warning") as warning:
        assert mesh_converter.effective_mmd_shader_backend() == "dx11"
        assert mesh_converter.effective_mmd_shader_backend() == "dx11"

    settings.set.assert_not_called()
    warning.assert_called_once()


def test_material_creation_never_tries_glsl_on_directx11():
    converter = object.__new__(mesh_converter.MeshConverter)
    material = SimpleNamespace(get_name=lambda: "backend_guard")
    settings = mock.Mock()
    settings.get.return_value = True
    with mock.patch.object(mesh_converter, "settings", settings), mock.patch.object(
        mesh_converter, "effective_mmd_shader_backend", return_value="dx11"
    ), mock.patch.object(mesh_converter, "_ensure_shader_plugin", return_value=True), mock.patch.object(
        mesh_converter.cmds, "shadingNode", return_value="backend_guard"
    ) as shading_node, mock.patch.object(
        mesh_converter.cmds, "objExists", return_value=True
    ), mock.patch.object(
        mesh_converter.cmds, "nodeType", return_value="dx11Shader"
    ), mock.patch.object(
        mesh_converter.cmds, "attributeQuery", return_value=True
    ), mock.patch.object(converter, "_setup_dx11_shader") as setup_dx11, mock.patch.object(
        converter, "_setup_glsl_shader"
    ) as setup_glsl:
        result = converter._create_material(material, material_index=0)

    assert result == "backend_guard"
    shading_node.assert_called_once_with("dx11Shader", asShader=True, name="backend_guard")
    setup_dx11.assert_called_once()
    setup_glsl.assert_not_called()


def test_shader_plugin_probe_never_loads_or_changes_plugin_state():
    with mock.patch.object(mesh_converter.cmds, "pluginInfo", return_value=False) as plugin_info, mock.patch.object(
        mesh_converter.cmds, "loadPlugin", create=True
    ) as load_plugin:
        assert not mesh_converter._ensure_shader_plugin("dx11Shader")

    plugin_info.assert_called_once_with("dx11Shader", query=True, loaded=True)
    load_plugin.assert_not_called()


def test_material_creation_falls_back_to_standard_when_dx11_plugin_is_not_loaded():
    converter = object.__new__(mesh_converter.MeshConverter)
    material = SimpleNamespace(get_name=lambda: "backend_guard")
    settings = mock.Mock()
    settings.get.return_value = True
    with mock.patch.object(mesh_converter, "settings", settings), mock.patch.object(
        mesh_converter, "effective_mmd_shader_backend", return_value="dx11"
    ), mock.patch.object(mesh_converter, "_ensure_shader_plugin", return_value=False), mock.patch.object(
        mesh_converter.cmds, "shadingNode", return_value="backend_guard"
    ) as shading_node, mock.patch.object(converter, "_setup_standard_shader") as setup_standard:
        result = converter._create_material(material, material_index=0)

    assert result == "backend_guard"
    shading_node.assert_called_once_with("standardSurface", asShader=True, name="backend_guard")
    setup_standard.assert_called_once()


def test_existing_glsl_material_is_replaced_before_directx11_apply():
    cmds = mock.Mock()
    cmds.nodeType.return_value = "GLSLShader"
    cmds.listConnections.return_value = ["backend_guardSG.surfaceShader"]
    cmds.rename.side_effect = ["backend_guard__legacy_GLSLShader", "backend_guard"]
    cmds.objExists.return_value = False
    with mock.patch.object(mesh_converter, "cmds", cmds), mock.patch.object(
        mesh_converter, "effective_mmd_shader_backend", return_value="dx11"
    ), mock.patch.object(mesh_converter, "_ensure_shader_plugin", return_value=True), mock.patch.object(
        mesh_converter, "_create_backend_replacement", return_value="backend_guard__dx11"
    ) as create_replacement, mock.patch.object(
        mesh_converter, "_copy_shader_backend_state"
    ) as copy_state, mock.patch.object(mesh_converter, "_delete_shader_node") as delete_node, mock.patch.object(
        mesh_converter, "_warn_shader_backend_once"
    ):
        result = mesh_converter.ensure_material_shader_backend("backend_guard")

    assert result == "backend_guard"
    create_replacement.assert_called_once_with("backend_guard", "dx11")
    copy_state.assert_called_once_with("backend_guard", "backend_guard__dx11")
    cmds.connectAttr.assert_called_once_with(
        "backend_guard.outColor", "backend_guardSG.surfaceShader", force=True
    )
    delete_node.assert_called_once_with("backend_guard__legacy_GLSLShader")


def test_hardware_fallback_preserves_main_texture_and_material_identity():
    for source_type in ("dx11Shader", "GLSLShader"):
        cmds = mock.Mock()
        replacement = "backend_guard__standard"

        def node_type(node):
            return source_type if node == "backend_guard" else "standardSurface"

        def attribute_query(attr, node, exists):
            assert exists
            return (node == "backend_guard" and attr == "MainTexture") or (
                node == replacement and attr == "baseColor"
            )

        def list_connections(plug, **_kwargs):
            if plug == "backend_guard.MainTexture":
                return ["backend_guard_file.outColor"]
            if plug == "backend_guard.outColor":
                return ["backend_guardSG.surfaceShader"]
            return []

        cmds.nodeType.side_effect = node_type
        cmds.attributeQuery.side_effect = attribute_query
        cmds.listConnections.side_effect = list_connections
        cmds.shadingNode.return_value = replacement
        cmds.rename.side_effect = [f"backend_guard__legacy_{source_type}", "backend_guard"]
        cmds.objExists.return_value = False
        settings = mock.Mock()

        with mock.patch.object(mesh_converter, "cmds", cmds), mock.patch.object(
            mesh_converter, "settings", settings
        ), mock.patch.object(
            mesh_converter, "effective_mmd_shader_backend", return_value="dx11"
        ), mock.patch.object(
            mesh_converter, "_ensure_shader_plugin", return_value=False
        ), mock.patch.object(mesh_converter, "_warn_shader_backend_once"):
            result = mesh_converter.ensure_material_shader_backend("backend_guard")

        assert result == "backend_guard"
        cmds.shadingNode.assert_called_once_with(
            "standardSurface", asShader=True, name="backend_guard__standard"
        )
        assert mock.call(
            "backend_guard_file.outColor", f"{replacement}.baseColor", force=True
        ) in cmds.connectAttr.call_args_list
        assert mock.call(
            "backend_guard.outColor", "backend_guardSG.surfaceShader", force=True
        ) in cmds.connectAttr.call_args_list
        assert cmds.rename.call_args_list == [
            mock.call("backend_guard", f"backend_guard__legacy_{source_type}"),
            mock.call(replacement, "backend_guard"),
        ]
        cmds.loadPlugin.assert_not_called()
        settings.set.assert_not_called()


def test_dx11_replacement_assigns_fx_and_valid_technique_only():
    cmds = mock.Mock()
    cmds.shadingNode.return_value = "backend_guard__dx11"
    cmds.attributeQuery.return_value = False
    with mock.patch.object(mesh_converter, "cmds", cmds), mock.patch.object(
        mesh_converter, "_ensure_shader_plugin", return_value=True
    ), mock.patch.object(mesh_converter, "_set_shader_attribute_checked", return_value=True) as set_checked, mock.patch.object(
        mesh_converter, "_ensure_dx11_uniform_attributes"
    ), mock.patch.object(mesh_converter, "get_transparency_mode", return_value="opaque"):
        result = mesh_converter._create_backend_replacement("backend_guard", "dx11")

    assert result == "backend_guard__dx11"
    shader_call = next(call for call in set_checked.call_args_list if call.args[1] == "shader")
    assert shader_call.args[2].endswith("MMDShader.fx")
    assert not shader_call.args[2].endswith(".ogsfx")
    technique_call = next(call for call in set_checked.call_args_list if call.args[1] == "technique")
    assert technique_call.args[2] == "MMDTechnique"
