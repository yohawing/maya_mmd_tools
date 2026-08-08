"""Unit tests for the C++ native image gallery publication helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

import tools.render_override_native_gallery as gallery
import tools.render_override_visual_gate as gate
import tools.render_override_vp2_ownership_e2e as vp2


def test_publish_case_retains_oracle_native_flip_error_map(tmp_path, monkeypatch):
    oracle = tmp_path / "oracle.png"
    native = tmp_path / "native-capture.png"
    gate.write_png_rgb(oracle, 2, 2, [(16, 16, 16)] * 4)
    gate.write_png_rgb(native, 2, 2, [(32, 32, 32)] * 4)
    calls = []

    def fake_flip_runner(**kwargs):
        calls.append(kwargs)
        work_dir = Path(kwargs["work_dir"])
        work_dir.mkdir(parents=True, exist_ok=True)
        error_map = work_dir / "native.png"
        gate.write_png_rgb(error_map, 2, 2, [(255, 0, 0)] * 4)
        return {"status": "pass", "error_map_path": str(error_map)}

    monkeypatch.setattr(gallery, "_default_flip_runner", fake_flip_runner)

    case_dir, _comparison = gallery._publish_case(
        tmp_path / "gallery",
        "fixture-case",
        oracle,
        native,
        flip_executable="flip.exe",
    )

    assert (case_dir / "reference.png").is_file()
    assert (case_dir / "native.png").is_file()
    assert (case_dir / "flip-error-native.png").is_file()
    assert calls[0]["reference"] == case_dir / "reference.png"
    assert calls[0]["actual"] == case_dir / "native.png"
    assert calls[0]["flip_executable"] == "flip.exe"

    gallery._write_html(
        {"cases": [{"name": "fixture-case", "oracleStatus": "available"}]},
        tmp_path / "gallery",
    )
    document = (tmp_path / "gallery" / "index.html").read_text(encoding="utf-8")
    assert document.count("flip-error-native.png") == 2


def test_material_binding_diagnostics_parse_valid_json_and_preserve_fields():
    class Commands:
        @staticmethod
        def mmdRenderWitness(**kwargs):
            assert kwargs == {"node": "mmdRenderShape1", "json": True}
            return (
                '{"version":1,"status":"ready","items":['
                '{"materialIndex":2,"submeshIndex":3,'
                '"mainTextureRequested":true,"mainTextureAcquired":false,'
                '"mainTextureBindingSuccess":false,"bindingSuccess":true}'
                ']}'
            )

    diagnostics = vp2._read_material_binding_diagnostics(
        Commands(), "mmdRenderShape1", lambda _message: None
    )

    assert diagnostics["status"] == "ready"
    item = diagnostics["items"][0]
    assert item["materialIndex"] == 2
    assert item["submeshIndex"] == 3
    assert item["mainTextureRequested"] is True
    assert item["mainTextureAcquired"] is False
    assert item["mainTextureBindingSuccess"] is False
    assert item["bindingSuccess"] is True


def test_material_binding_diagnostics_retries_empty_and_invalid_results():
    class EventuallyReadyCommands:
        def __init__(self):
            self.responses = [
                "",
                "[]",
                '{"version":1,"status":"ready","items":[{"bindingSuccess":true}]}',
            ]
            self.refresh_count = 0

        def mmdRenderWitness(self, **kwargs):
            assert kwargs == {"node": "shape", "json": True}
            return self.responses.pop(0)

        def refresh(self, **kwargs):
            assert kwargs == {"force": True}
            self.refresh_count += 1

    commands = EventuallyReadyCommands()
    diagnostics = vp2._read_material_binding_diagnostics(
        commands,
        "shape",
        lambda _message: None,
        max_attempts=4,
        poll_seconds=0.0,
    )

    assert diagnostics["status"] == "ready"
    assert diagnostics["items"] == [{"bindingSuccess": True}]
    assert commands.refresh_count == 2


def test_material_binding_diagnostics_report_unavailable_and_invalid_results():
    class UnavailableCommands:
        @staticmethod
        def mmdRenderWitness(**_kwargs):
            raise RuntimeError("legacy witness command")

    unavailable = vp2._read_material_binding_diagnostics(
        UnavailableCommands(), "shape", lambda _message: None, max_attempts=1
    )
    assert unavailable["status"] == "unavailable"
    assert unavailable["items"] == []

    class InvalidCommands:
        @staticmethod
        def mmdRenderWitness(**_kwargs):
            return "[1, 2, 3]"

    invalid = vp2._read_material_binding_diagnostics(
        InvalidCommands(), "shape", lambda _message: None, max_attempts=1
    )
    assert invalid["status"] == "invalid"
    assert invalid["items"] == []


def test_require_requested_plugin_rejects_different_canonical_binary(tmp_path):
    requested = tmp_path / "requested" / "mmd_tools_cpp.mll"
    loaded = tmp_path / "autoloaded" / "mmd_tools_cpp.mll"

    class Commands:
        @staticmethod
        def pluginInfo(plugin, **kwargs):
            if kwargs == {"query": True, "loaded": True}:
                return plugin == "mmd_tools_cpp"
            if kwargs == {"query": True, "path": True}:
                assert plugin == "mmd_tools_cpp"
                return str(loaded)
            raise AssertionError((plugin, kwargs))

    with pytest.raises(RuntimeError, match="differs from requested --plugin"):
        vp2._require_requested_plugin(Commands(), str(requested), lambda _message: None)


def test_require_requested_plugin_records_matching_canonical_binary(tmp_path):
    requested = tmp_path / "requested" / "mmd_tools_cpp.mll"
    log_messages = []

    class Commands:
        @staticmethod
        def pluginInfo(plugin, **kwargs):
            if kwargs == {"query": True, "loaded": True}:
                return plugin in {str(requested.resolve()), "mmd_tools_cpp"}
            if kwargs == {"query": True, "path": True}:
                assert plugin == "mmd_tools_cpp"
                return str(requested)
            raise AssertionError((plugin, kwargs))

    assert vp2._require_requested_plugin(
        Commands(), str(requested), log_messages.append
    ) == requested.resolve()
    assert any("loaded canonical plugin" in message for message in log_messages)
