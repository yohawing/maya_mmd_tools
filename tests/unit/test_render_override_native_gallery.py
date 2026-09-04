"""Unit tests for the C++ native image gallery publication helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.render_override.render_override_native_gallery as gallery
import tools.render_override.render_override_visual_gate as gate
import tools.render_override.render_override_vp2_ownership_e2e as vp2


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


def _native_case_fixture(tmp_path, roi=None):
    manifest = tmp_path / "manifest.json"
    model = tmp_path / "model.pmx"
    oracle = tmp_path / "oracle.png"
    manifest.write_text("{}", encoding="utf-8")
    model.write_bytes(b"fixture")
    gate.write_png_rgb(oracle, 4, 2, [(16, 16, 16)] * 8)
    case = {
        "name": "transparency-roi",
        "feature": "transparency",
        "frame": 0,
        "camera": {},
        "raw": {"assets": {"model": "model.pmx"}},
        "oracle_png": str(oracle),
    }
    if roi is not None:
        case["roi"] = roi
    return manifest, model, case


def _run_fake_native_case(
    monkeypatch, tmp_path, case, flip_runner, enforce_flip_thresholds=False
):
    plugin = tmp_path / "mmd_tools_cpp.mll"
    plugin.write_bytes(b"plugin")

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_subprocess_run(command, **_kwargs):
        output_dir = Path(command[command.index("--out-dir") + 1])
        native = output_dir / "native-capture.png"
        gate.write_png_rgb(native, 4, 2, [(16, 16, 16)] * 8)
        report = {
            "status": "pass",
            "parityMode": True,
            "captureOnly": True,
            "captures": {"ownership": str(native)},
        }
        (output_dir / "render_override_vp2_maya2024.json").write_text(
            json.dumps(report), encoding="utf-8"
        )
        return Completed()

    monkeypatch.setattr(gallery, "_resolve_mayapy", lambda _maya: Path("mayapy"))
    monkeypatch.setattr(gallery.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(gallery, "_default_flip_runner", flip_runner)
    return gallery._run_native_case(
        manifest_path=tmp_path / "manifest.json",
        case=case,
        maya="2024",
        plugin=plugin,
        output_root=tmp_path / "capture",
        gallery_output=tmp_path / "gallery",
        port=7800,
        timeout=1,
        flip_executable="flip.exe",
        enforce_flip_thresholds=enforce_flip_thresholds,
    )


def test_native_case_records_full_and_roi_flip_contract(tmp_path, monkeypatch):
    _manifest, _model, case = _native_case_fixture(
        tmp_path, roi={"x": 2, "y": 0, "width": 2, "height": 2}
    )
    calls = []
    low_metrics = {"mean": 0.01, "weighted_median": 0.005, "q3": 0.02, "max": 0.1}
    high_metrics = {"mean": 0.5, "weighted_median": 0.5, "q3": 0.5, "max": 1.0}

    def fake_flip_runner(**kwargs):
        calls.append(kwargs)
        basename = kwargs["basename"]
        work_dir = Path(kwargs["work_dir"])
        work_dir.mkdir(parents=True, exist_ok=True)
        error_map = work_dir / (basename + ".png")
        gate.write_png_rgb(error_map, 2 if basename == "roi" else 4, 2, [(0, 0, 0)] * (4 if basename == "roi" else 8))
        metrics = high_metrics if basename == "roi" else low_metrics
        return {
            "status": "pass",
            "returncode": 0,
            "metrics": metrics,
            "error_map_path": str(error_map),
            "text": "",
        }

    result = _run_fake_native_case(
        monkeypatch,
        tmp_path,
        case,
        fake_flip_runner,
        enforce_flip_thresholds=True,
    )

    assert result["status"] == "fail"
    assert result["parity"]["status"] == "fail"
    assert "FLIP ROI threshold exceeded" in result["reason"]
    assert result["full"]["metrics"] == low_metrics
    assert result["full"]["thresholdEvaluation"]["status"] == "pass"
    assert result["full"]["errorMap"].endswith("flip-error-native.png")
    assert result["roiComparison"]["bounds"] == {
        "x": 2,
        "y": 0,
        "width": 2,
        "height": 2,
    }
    assert result["roiComparison"]["metrics"] == high_metrics
    assert result["roiComparison"]["thresholdEvaluation"]["status"] == "fail"
    assert result["roiComparison"]["errorMap"].endswith("flip-error-roi-native.png")
    assert [call["basename"] for call in calls] == ["native", "roi"]
    assert gate.read_png_rgb(calls[0]["reference"])[:2] == (4, 2)
    assert gate.read_png_rgb(calls[1]["reference"])[:2] == (2, 2)


def test_native_case_keeps_report_only_thresholds_and_marks_missing_roi(tmp_path, monkeypatch):
    manifest, _model, case = _native_case_fixture(tmp_path)
    calls = []

    def high_flip_runner(**kwargs):
        calls.append(kwargs)
        work_dir = Path(kwargs["work_dir"])
        work_dir.mkdir(parents=True, exist_ok=True)
        error_map = work_dir / "native.png"
        gate.write_png_rgb(error_map, 4, 2, [(0, 0, 0)] * 8)
        return {
            "status": "pass",
            "returncode": 0,
            "metrics": {"mean": 0.5, "weighted_median": 0.5, "q3": 0.5, "max": 1.0},
            "error_map_path": str(error_map),
        }

    result = _run_fake_native_case(monkeypatch, tmp_path, case, high_flip_runner)

    assert result["status"] == "pass"
    assert result["parity"]["status"] == "unreviewed"
    assert result["full"]["thresholdEvaluation"]["status"] == "fail"
    assert result["roiComparison"] == {
        "status": "unavailable",
        "reason": "manifest case has no ROI contract",
        "metrics": gate.parse_flip_metrics(""),
        "threshold": gallery.FLIP_THRESHOLDS["transparency"]["roi"],
        "thresholdEvaluation": None,
        "errorMap": None,
    }
    assert len(calls) == 1


def test_native_case_fails_report_only_when_flip_comparison_fails(tmp_path, monkeypatch):
    _manifest, _model, case = _native_case_fixture(tmp_path)

    def failed_flip_runner(**_kwargs):
        return {"status": "fail", "reason": "FLIP unavailable", "metrics": {}}

    result = _run_fake_native_case(monkeypatch, tmp_path, case, failed_flip_runner)

    assert result["status"] == "fail"
    assert result["parity"]["status"] == "fail"
    assert result["reason"] == "FLIP unavailable"


def test_native_gallery_cli_passes_visual_gate_roi_overrides(monkeypatch):
    captured = {}

    def fake_run_gallery(**kwargs):
        captured.update(kwargs)
        return {"exitCode": 0}

    monkeypatch.setattr(gallery, "run_gallery", fake_run_gallery)

    assert gallery.main(
        ["--manifest", "manifest.json", "--roi-case", "fixture=1,2,3,4"]
    ) == 0
    assert captured["roi_overrides"] == {
        "fixture": {"x": 1, "y": 2, "width": 3, "height": 4}
    }

    with pytest.raises(SystemExit):
        gallery.main(["--manifest", "manifest.json", "--roi-case", "fixture=1,2"])


def test_native_gallery_rejects_unknown_roi_override_before_clearing(tmp_path, monkeypatch):
    plugin = tmp_path / "mmd_tools_cpp.mll"
    plugin.write_bytes(b"plugin")
    monkeypatch.setattr(
        gallery,
        "load_manifest_cases",
        lambda _manifest: ({}, [{"name": "known"}]),
    )
    monkeypatch.setattr(
        gallery,
        "_clear_native_gallery",
        lambda _gallery: pytest.fail("stale gallery was cleared"),
    )

    with pytest.raises(ValueError, match="unknown ROI override case.*typo"):
        gallery.run_gallery(
            manifest_path=tmp_path / "manifest.json",
            maya="2024",
            output_root=tmp_path / "capture",
            gallery_output=tmp_path / "gallery",
            plugin_path=plugin,
            roi_overrides={"typo": {"x": 0, "y": 0, "width": 1, "height": 1}},
        )


@pytest.mark.parametrize(
    "roi",
    [
        {"x": None, "y": 0, "width": 1, "height": 1},
        {"x": 3, "y": 0, "width": 2, "height": 1},
    ],
)
def test_native_case_reports_invalid_roi_without_subprocess(tmp_path, monkeypatch, roi):
    _manifest, _model, case = _native_case_fixture(tmp_path, roi=roi)
    plugin = tmp_path / "mmd_tools_cpp.mll"
    plugin.write_bytes(b"plugin")
    calls = []

    def fail_subprocess(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("invalid ROI launched native capture")

    monkeypatch.setattr(gallery.subprocess, "run", fail_subprocess)

    result = gallery._run_native_case(
        manifest_path=tmp_path / "manifest.json",
        case=case,
        maya="2024",
        plugin=plugin,
        output_root=tmp_path / "capture",
        gallery_output=tmp_path / "gallery",
        port=7800,
        timeout=1,
    )

    assert result["status"] == "fail"
    assert "invalid ROI" in result["reason"]
    assert calls == []


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
    summary = diagnostics["summary"]
    assert summary["status"] == "degraded"
    assert summary["missingTextureCount"] == 1
    assert summary["issues"][0]["reason"] == "texture_path_empty"


def test_material_binding_diagnostics_summary_distinguishes_bind_failure(tmp_path):
    texture_path = tmp_path / "diffuse.png"
    texture_path.write_bytes(b"fixture")
    diagnostics = vp2._summarize_material_binding_diagnostics(
        {
            "version": 1,
            "status": "ready",
            "fallbackReason": "",
            "items": [
                {
                    "queueIndex": 4,
                    "materialIndex": 7,
                    "submeshIndex": 9,
                    "renderItemName": "mmdRenderQueue_Opaque_m7_s9_q4",
                    "mainTexturePath": str(texture_path),
                    "mainTextureRequested": True,
                    "mainTextureAcquired": True,
                    "mainTextureBindingSuccess": False,
                    "bindingSuccess": False,
                }
            ],
        }
    )

    summary = diagnostics["summary"]
    assert summary["status"] == "failed"
    assert summary["textureBindingFailureCount"] == 1
    assert summary["itemsWithIssues"] == 1
    texture_issue = next(
        issue for issue in summary["issues"] if issue["category"] == "texture"
    )
    assert texture_issue["reason"] == "texture_binding_failed"
    assert texture_issue["severity"] == "error"
    assert texture_issue["materialIndex"] == 7
    assert summary["textureBindings"][0]["pathExists"] is True


def test_material_binding_diagnostics_summary_classifies_missing_file(tmp_path):
    diagnostics = vp2._summarize_material_binding_diagnostics(
        {
            "status": "ready",
            "items": [
                {
                    "materialIndex": 3,
                    "mainTexturePath": str(tmp_path / "missing.png"),
                    "mainTextureRequested": True,
                    "mainTextureAcquired": False,
                    "mainTextureBindingSuccess": False,
                    "bindingSuccess": True,
                }
            ],
        }
    )

    summary = diagnostics["summary"]
    assert summary["status"] == "degraded"
    assert summary["missingTextureCount"] == 1
    assert summary["issues"][0]["reason"] == "file_not_found"
    assert summary["textureBindings"][0]["status"] == "requested_unavailable"


def test_material_binding_diagnostics_summary_surfaces_fallback_reason():
    diagnostics = vp2._summarize_material_binding_diagnostics(
        {
            "version": 1,
            "status": "failed",
            "fallbackReason": "failed to bind material shader to item0",
            "items": [],
        }
    )

    summary = diagnostics["summary"]
    assert summary["status"] == "failed"
    assert summary["fallbackReason"] == "failed to bind material shader to item0"
    assert summary["issues"][-1]["category"] == "fallback"
    assert summary["issues"][-1]["reason"] == summary["fallbackReason"]


def test_material_binding_diagnostics_ready_accepts_optional_texture_degradation():
    assert vp2._material_binding_diagnostics_ready(
        {"status": "ready", "summary": {"status": "ok"}}
    )
    assert vp2._material_binding_diagnostics_ready(
        {"status": "ready", "summary": {"status": "degraded"}}
    )
    assert not vp2._material_binding_diagnostics_ready(
        {"status": "ready", "summary": {"status": "failed"}}
    )
    assert not vp2._material_binding_diagnostics_ready(
        {"status": "unavailable", "summary": {"status": "failed"}}
    )


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

    class FailedCommands:
        @staticmethod
        def mmdRenderWitness(**_kwargs):
            return '{"version":1,"status":"failed","fallbackReason":"shader unavailable","items":[]}'

    failed = vp2._read_material_binding_diagnostics(
        FailedCommands(), "shape", lambda _message: None, max_attempts=1
    )
    assert failed["status"] == "failed"
    assert failed["fallbackReason"] == "shader unavailable"
    assert failed["summary"]["status"] == "failed"
    assert failed["summary"]["fallbackReason"] == "shader unavailable"


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
