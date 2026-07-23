"""Focused tests for the compact local visual-regression gallery."""

import importlib.util
import json
import os
from pathlib import Path


_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "visual-regression" / "generate_gallery.py"
_SPEC = importlib.util.spec_from_file_location("visual_regression_gallery", _SCRIPT)
assert _SPEC and _SPEC.loader
gallery = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gallery)


def _write_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n")


def _write_report(run: Path, name: str, actual: str) -> Path:
    run.mkdir(parents=True, exist_ok=True)
    report = run / gallery.REPORT_NAME
    report.write_text(json.dumps({"results": [{"name": name, "actual_png": actual}]}), encoding="utf-8")
    return report


def test_gallery_selects_newest_existing_actual_per_fixture(tmp_path):
    root = tmp_path / "visual-regression"
    old_run = root / "old"
    new_run = root / "new"
    fallback_run = root / "fallback"
    missing_run = root / "missing"
    missing_fixture_run = root / "missing-fixture"
    old_image = old_run / "fixture-a" / "actual-old.png"
    new_image = new_run / "fixture-a" / "actual-new.png"
    fallback_image = fallback_run / "fixture-b" / "actual-valid.png"
    _write_png(old_image)
    _write_png(new_image)
    _write_png(fallback_image)
    old_report = _write_report(old_run, "fixture-a", "fixture-a/actual-old.png")
    new_report = _write_report(new_run, "fixture-a", "fixture-a/actual-new.png")
    fallback_report = _write_report(fallback_run, "fixture-b", "fixture-b/actual-valid.png")
    missing_report = _write_report(missing_run, "fixture-b", "fixture-b/actual-removed.png")
    _write_report(missing_fixture_run, "fixture-c", "fixture-c/actual-removed.png")
    os.utime(old_report, ns=(10, 10))
    os.utime(fallback_report, ns=(20, 20))
    os.utime(new_report, ns=(30, 30))
    os.utime(missing_report, ns=(40, 40))

    output = root / "gallery.html"
    assert gallery.generate_gallery(root, output) == (2, 2)
    page = output.read_text(encoding="utf-8")
    assert "fixture-a" in page and "actual-new.png" in page
    assert "actual-old.png" not in page
    # The newest fixture-b candidate is missing, so the newest valid image is
    # retained; fixture-c has no valid actual and is omitted.
    assert "fixture-b" in page and "actual-valid.png" in page
    assert "fixture-c" not in page
    assert page.count("<h2>") == 2 and page.count("<img ") == 2
    assert "status" not in page and "NMAE" not in page and "chroma" not in page
    assert "Missing" not in page and "reference" not in page and "diff" not in page


def test_comparison_json_mtime_can_win_over_capture_report(tmp_path):
    root = tmp_path / "visual-regression"
    run = root / "run"
    report_image = run / "fixture" / "report.png"
    comparison_image = run / "fixture" / "comparison.png"
    _write_png(report_image)
    _write_png(comparison_image)
    report = _write_report(run, "fixture", "fixture/report.png")
    comparison = run / gallery.COMPARISON_NAME
    comparison.write_text(
        json.dumps({"results": [{"name": "fixture", "actual": "fixture/comparison.png"}]}),
        encoding="utf-8",
    )
    os.utime(report, ns=(10, 10))
    os.utime(comparison, ns=(20, 20))

    output = root / "gallery.html"
    assert gallery.generate_gallery(root, output) == (1, 1)
    page = output.read_text(encoding="utf-8")
    assert "comparison.png" in page and "report.png" not in page


def test_malformed_json_is_ignored_and_windows_urls_are_browser_safe(tmp_path):
    root = tmp_path / "visual-regression"
    broken = root / "broken"
    broken.mkdir(parents=True)
    (broken / gallery.REPORT_NAME).write_text("{not-json", encoding="utf-8")
    source = tmp_path / "reference images" / "日本語 #1.png"
    source.parent.mkdir(parents=True)
    _write_png(source)
    assert gallery._coerce_path(source.as_uri(), broken) == source
    assert gallery._asset_url(source, root / "gallery.html") == (
        "../reference%20images/%E6%97%A5%E6%9C%AC%E8%AA%9E%20%231.png"
    )

    output = root / "gallery.html"
    assert gallery.generate_gallery(root, output) == (0, 0)
    assert "<h2>" not in output.read_text(encoding="utf-8")
