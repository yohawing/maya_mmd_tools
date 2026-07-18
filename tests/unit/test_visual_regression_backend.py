"""Backend/device contracts for the Maya visual-regression launcher."""

from pathlib import Path

from tests.viewport.visual_regression_capture import (
    _build_maya_code,
    _device_matches_backend,
    _is_outline_visual_case,
    _validate_outline_backend,
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


def test_only_outline_fixture_uses_outline_capture_setup():
    assert _is_outline_visual_case({"name": "fixture-render-generated-visual-mmd-outline-normal-silhouette"})
    assert not _is_outline_visual_case({"name": "fixture-render-generated-visual-mmd-diffuse-lit-box"})


def test_glsl_rejects_explicit_outline_fixture_before_launch():
    cases = [{"name": "fixture-render-generated-visual-mmd-outline-normal-silhouette"}]
    _validate_outline_backend(cases, "dx11")
    try:
        _validate_outline_backend(cases, "glsl")
    except RuntimeError as exc:
        assert "no production outline pass" in str(exc)
    else:
        raise AssertionError("GLSL outline fixture must fail closed")


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
