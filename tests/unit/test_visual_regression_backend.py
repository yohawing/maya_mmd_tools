"""Backend/device contracts for the Maya visual-regression launcher."""

from pathlib import Path
from unittest import mock

import pytest

from tests.viewport.visual_regression_capture import (
    _build_maya_code,
    _device_matches_backend,
    _preflight_command_port,
    _vp2_override,
)


def test_default_vp2_device_matches_shader_backend():
    assert _vp2_override("dx11", "default") == "VirtualDeviceDx11"
    assert _vp2_override("glsl", "default") == "VirtualDeviceGLCore"


def test_explicit_vp2_device_is_preserved():
    assert _vp2_override("glsl", "gl") == "VirtualDeviceGL"
    assert _vp2_override("dx11", "glcore") == "VirtualDeviceGLCore"


def test_device_validation_is_backend_specific():
    assert _device_matches_backend("dx11", "API : DirectX V.11")
    assert not _device_matches_backend("dx11", "OpenGL Core Profile")
    assert _device_matches_backend("glsl", "OpenGL Core Profile")
    assert not _device_matches_backend("glsl", "API : DirectX V.11")


def test_open_port_requires_explicit_attach_opt_in():
    with mock.patch(
        "tests.viewport.visual_regression_capture.maya_commandport.is_port_open",
        return_value=True,
    ) as is_port_open:
        with pytest.raises(RuntimeError, match="--attach-existing"):
            _preflight_command_port(7721, attach_existing=False)
        is_port_open.assert_called_once_with(7721)


def test_explicit_attach_allows_open_port_without_probe():
    with mock.patch(
        "tests.viewport.visual_regression_capture.maya_commandport.is_port_open",
        return_value=True,
    ) as is_port_open:
        _preflight_command_port(7721, attach_existing=True)
        is_port_open.assert_not_called()


def test_maya_payload_compiles_for_both_backends(tmp_path):
    kwargs = {
        "project_root": tmp_path,
        "cases": [],
        "shader_fx": Path("shader.fx"),
        "output_dir": tmp_path,
        "log_path": tmp_path / "capture.log",
        "width": 64,
        "height": 64,
        "compare": False,
        "debug_lambert_control": False,
        "hide_orig_shapes": False,
    }
    for backend in ("dx11", "glsl"):
        compile(_build_maya_code(**kwargs, shader_backend=backend), "<maya-visual-regression>", "exec")


def test_maya_payload_carries_display_texture_state(tmp_path):
    kwargs = {
        "project_root": tmp_path,
        "cases": [],
        "shader_fx": Path("shader.fx"),
        "output_dir": tmp_path,
        "log_path": tmp_path / "capture.log",
        "width": 64,
        "height": 64,
        "compare": False,
        "debug_lambert_control": False,
        "hide_orig_shapes": False,
        "shader_backend": "dx11",
        "display_textures": False,
    }

    source = _build_maya_code(**kwargs)
    assert '_display_textures = bool(_payload.get("display_textures", True))' in source
    assert 'capture_panel = _setup_panel(camera, _display_textures)' in source


def test_maya_payload_carries_shader_plugin_lifecycle_diagnostics(tmp_path):
    kwargs = {
        "project_root": tmp_path,
        "cases": [],
        "shader_fx": Path("shader.fx"),
        "output_dir": tmp_path,
        "log_path": tmp_path / "capture.log",
        "width": 64,
        "height": 64,
        "compare": False,
        "debug_lambert_control": False,
        "hide_orig_shapes": False,
        "shader_backend": "dx11",
        "display_textures": True,
    }

    source = _build_maya_code(**kwargs)
    compile(source, "<maya-visual-regression>", "exec")
    assert '_shader_node_type = _payload["shader_node_type"]' in source
    assert '_shader_plugin_name = _payload["shader_plugin"]' in source
    assert "def _shader_plugin_diag():" in source
    assert 'cmds.pluginInfo(_shader_plugin_name, query=True' in source
    assert 'for key in ["loaded", "autoload", "registered", "path"]' in source
    assert 'report["shader_plugin"]["before"] = _shader_plugin_diag()' in source
    assert 'report["shader_plugin"]["after"] = _shader_plugin_diag()' in source
    assert source.count("cmds.loadPlugin(_shader_plugin_name, quiet=True)") == 1
