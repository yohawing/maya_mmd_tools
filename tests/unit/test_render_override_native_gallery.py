"""Unit tests for the C++ native image gallery publication helpers."""

from __future__ import annotations

from pathlib import Path

import tools.render_override_native_gallery as gallery
import tools.render_override_visual_gate as gate


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

    case_dir = gallery._publish_case(
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
