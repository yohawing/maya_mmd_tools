"""Focused tests for the local visual-regression HTML gallery."""

import importlib.util
import json
from pathlib import Path


_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "visual-regression" / "generate_gallery.py"
_SPEC = importlib.util.spec_from_file_location("visual_regression_gallery", _SCRIPT)
assert _SPEC and _SPEC.loader
gallery = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gallery)


def _write_png(path: Path) -> None:
    # The gallery only links images, so a minimal signature is enough here.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n")


def test_generate_gallery_joins_capture_and_comparison_results(tmp_path):
    root = tmp_path / "build" / "visual-regression"
    run = root / "maya-2024-dx11"
    fixture = run / "fixture-box"
    actual = fixture / "actual-frame-0.png"
    diff = fixture / "diff-frame-0.png"
    reference = tmp_path / "GoldenOracle" / "fixture-box" / "frame-0.png"
    _write_png(actual)
    _write_png(diff)
    _write_png(reference)
    (run / gallery.REPORT_NAME).write_text(
        json.dumps(
            {
                "kind": "maya-dx11-visual-regression-report",
                "shader_backend": "dx11",
                "maya_version": "2024",
                "results": [{"name": "fixture-box", "ok": True, "actual_png": "fixture-box/actual-frame-0.png", "oracle_png": str(reference)}],
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    (run / gallery.COMPARISON_NAME).write_text(
        json.dumps(
            {
                "status": "pass",
                "results": [
                    {
                        "name": "fixture-box",
                        "status": "pass",
                        "reference": str(reference),
                        "actual": str(actual),
                        "metrics": {"normalized_mean_absolute_error": 0.01234567, "chroma_ratio": 0.91},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    output = root / "gallery.html"
    assert gallery.generate_gallery(root, output) == (1, 1)
    page = output.read_text(encoding="utf-8")
    assert "maya-2024-dx11" in page
    assert "dx11" in page and "2024" in page
    assert "fixture-box" in page and "0.012346" in page and "0.910000" in page
    assert "actual-frame-0.png" in page and "diff-frame-0.png" in page and "frame-0.png" in page
    assert "\\" not in page


def test_gallery_tolerates_missing_comparison_and_malformed_report(tmp_path):
    root = tmp_path / "visual-regression"
    complete = root / "comparison-only"
    complete.mkdir(parents=True)
    (complete / gallery.COMPARISON_NAME).write_text(
        json.dumps({"status": "fail", "results": [{"name": "fixture-missing", "status": "fail", "failures": ["missing actual"]}]}),
        encoding="utf-8",
    )
    broken = root / "broken"
    broken.mkdir(parents=True)
    (broken / gallery.REPORT_NAME).write_text("{not-json", encoding="utf-8")

    output = root / "gallery.html"
    assert gallery.generate_gallery(root, output) == (2, 1)
    page = output.read_text(encoding="utf-8")
    assert "comparison-only" in page and "fixture-missing" in page
    assert "broken" in page and "JSONDecodeError" in page
    assert "Missing: visual-regression-report.json" in page


def test_gallery_uses_placeholder_for_stale_image_paths(tmp_path):
    root = tmp_path / "visual-regression"
    run = root / "stale-images"
    run.mkdir(parents=True)
    (run / gallery.REPORT_NAME).write_text(
        json.dumps(
            {
                "results": [
                    {
                        "name": "fixture-missing",
                        "ok": True,
                        "actual_png": "fixture-missing/deleted.png",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    output = root / "gallery.html"
    assert gallery.generate_gallery(root, output) == (1, 1)
    page = output.read_text(encoding="utf-8")
    assert "deleted.png" not in page
    assert page.count('<div class="placeholder">missing</div>') == 3


def test_asset_url_uses_forward_slashes_and_quotes_special_characters(tmp_path):
    html_path = tmp_path / "gallery.html"
    source = tmp_path / "reference images" / "日本語 #1.png"
    url = gallery._asset_url(source, html_path)
    assert url == "reference%20images/%E6%97%A5%E6%9C%AC%E8%AA%9E%20%231.png"


def test_file_uri_is_resolved_relative_to_report_directory(tmp_path):
    run = tmp_path / "run"
    source = run / "fixture" / "image.png"
    uri = source.as_uri()
    assert gallery._coerce_path(uri, run) == source
