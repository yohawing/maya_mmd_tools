"""Backend/device contracts for the Maya visual-regression launcher."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from tests.viewport.visual_regression_capture import (
    _build_maya_code,
    _camera_plan_for_case,
    _device_matches_backend,
    _load_cases,
    _preflight_command_port,
    _validate_camera_motion_data,
    _vp2_override,
)
from tools.nox.common import _has_flag, _option, _options
from tools.nox.maya_sessions import run_visual_regression


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


def test_maya_payload_opt_in_self_shadow_probe_is_explicit(tmp_path):
    source = _build_maya_code(
        project_root=tmp_path,
        cases=[],
        shader_fx=Path("shader.fx"),
        output_dir=tmp_path,
        log_path=tmp_path / "capture.log",
        width=64,
        height=64,
        compare=False,
        debug_lambert_control=False,
        hide_orig_shapes=False,
        shader_backend="dx11",
        enable_mmd_self_shadow=True,
    )

    compile(source, "<maya-visual-regression>", "exec")
    assert "_enable_mmd_self_shadow = bool(_payload.get(\"enable_mmd_self_shadow\", False))" in source
    assert "_configure_mmd_self_shadow_inputs(" in source
    assert 'light_controller, "post-panel"' in source
    assert '"UseShadows", "ShadowStrength", "ShadowBias", "Light0ShadowMap", "Light0Matrix"' in source
    assert '"displayLights", "shadows",' in source


def test_manifest_camera_plan_preserves_existing_camera_parameters():
    manifest_camera = {"position": [9, 8, 7], "target": [0, 1, 0], "fov": 22}
    plan = _camera_plan_for_case({"name": "manifest-case", "camera": manifest_camera})

    assert plan == {"source": "manifest", "parameters": manifest_camera}


def test_camera_motion_case_selects_maya_vmd_provenance(tmp_path):
    manifest_path = tmp_path / "fixture.render.json"
    manifest_path.write_text(
        json.dumps(
            {
                "defaults": {"camera": {"position": [1, 2, 3], "target": [0, 1, 0], "fov": 25}},
                "cases": [
                    {
                        "name": "camera-motion",
                        "frames": [23],
                        "assets": {"model": "model.pmx", "cameraMotion": "camera.vmd"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with mock.patch("tests.viewport.visual_regression_capture.subprocess.run") as run:
        _, cases = _load_cases(manifest_path, [], [], 0)
    run.assert_not_called()
    camera_motion = (tmp_path / "camera.vmd").resolve()
    plan = _camera_plan_for_case(cases[0])

    assert plan == {
        "source": "maya-vmd-camera-import",
        "vmd": str(camera_motion),
        "frame": 23,
    }


def test_checked_in_vmd_camera_render_smoke_uses_real_assets():
    """The GUI smoke fixture must exercise a PMX and a production camera VMD."""
    manifest_path = Path("tests/viewport/camera_vmd_render_smoke.json").resolve()

    _, cases = _load_cases(manifest_path, [], [], 0)

    assert len(cases) == 1
    case = cases[0]
    assert Path(case["model"]).is_file()
    assert Path(case["camera_motion"]).is_file()
    assert _camera_plan_for_case(case)["source"] == "maya-vmd-camera-import"


def test_camera_motion_vmd_fails_closed_without_camera_frames(tmp_path):
    with pytest.raises(RuntimeError, match="no camera frames"):
        _validate_camera_motion_data(SimpleNamespace(camera_frames=[]), tmp_path / "invalid.vmd")


def test_camera_motion_vmd_accepts_camera_frames(tmp_path):
    assert _validate_camera_motion_data(SimpleNamespace(camera_frames=[object()]), tmp_path / "camera.vmd") == 1


def test_maya_payload_uses_production_vmd_camera_import(tmp_path):
    source = _build_maya_code(
        project_root=tmp_path,
        cases=[
            {
                "name": "camera-motion",
                "frame": 12,
                "camera": {"position": [1, 2, 3], "target": [0, 1, 0], "fov": 25},
                "camera_motion": str(tmp_path / "camera.vmd"),
            }
        ],
        shader_fx=tmp_path / "shader.fx",
        output_dir=tmp_path,
        log_path=tmp_path / "capture.log",
        width=64,
        height=64,
        compare=False,
        debug_lambert_control=False,
        hide_orig_shapes=False,
        shader_backend="dx11",
    )
    compile(source, "<maya-visual-regression>", "exec")
    assert "VmdData" in source
    assert "VmdConverter" in source
    assert "scene_animation_only=True" in source
    assert '"maya-vmd-camera-import"' in source
    assert "import_light_animation = False" in source
    assert "mmd-anim" not in source


def test_pmm_assets_use_manifest_camera_and_light_without_mmd_anim(tmp_path):
    manifest_path = tmp_path / "fixture.render.json"
    manifest_path.write_text(
        json.dumps(
            {
                "defaults": {
                    "camera": {"position": [9, 8, 7], "target": [0, 1, 0], "fov": 22},
                    "light": {"direction": [0.5, -1, 0.5], "color": [1, 1, 1]},
                },
                "cases": [
                    {
                        "name": "pmm-case",
                        "kind": "static-render",
                        "frames": [0],
                        "assets": {"model": "model.pmx", "pmm": "scene.pmm"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with mock.patch("tests.viewport.visual_regression_capture.subprocess.run") as run:
        _, cases = _load_cases(manifest_path, [], [], 0)
    run.assert_not_called()

    assert cases[0]["camera"] == {"position": [9, 8, 7], "target": [0, 1, 0], "fov": 22}
    assert cases[0]["light"] == {"direction": [0.5, -1, 0.5], "color": [1, 1, 1]}
    assert cases[0]["camera_motion"] is None
    assert not any("mmd-anim" in value for value in cases[0].values() if isinstance(value, str))


def test_nox_visual_regression_does_not_forward_removed_camera_options():
    session = mock.Mock()
    session.posargs = [
        "--manifest",
        "render.json",
        "--camera-source",
        "pmm-candidate",
        "--mmd-anim",
        "mmd-anim-test",
        "--no-compare",
    ]
    session.error.side_effect = AssertionError
    run_visual_regression(
        session,
        posargs=session.posargs,
        option=_option,
        options=_options,
        has_flag=_has_flag,
        default_maya_version="2024",
        require_build_path=lambda _session, value, _name: Path("F:/repo") / value,
        python_executable="python.exe",
    )

    capture_args = session.run.call_args.args
    assert "--camera-source" not in capture_args
    assert "--mmd-anim" not in capture_args


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


def test_maya_payload_records_data_only_self_shadow_caster_selection(tmp_path):
    source = _build_maya_code(
        project_root=tmp_path,
        cases=[],
        shader_fx=Path("shader.fx"),
        output_dir=tmp_path,
        log_path=tmp_path / "capture.log",
        width=64,
        height=64,
        compare=False,
        debug_lambert_control=False,
        hide_orig_shapes=False,
        shader_backend="dx11",
        display_textures=True,
    )

    compile(source, "<maya-visual-regression>", "exec")
    assert "discover_self_shadow_caster_components" in source
    assert 'debug_actions["selfShadowCasterSelection"]' in source
