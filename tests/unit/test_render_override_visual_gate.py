"""Contract tests for the Maya-independent RO-0 visual gate helpers."""

from __future__ import annotations

import json
from pathlib import Path

import tools.render_override_visual_gate as gate


FLIP_TEXT = """Mean: 0.010000
Weighted median: 0.005000
1st weighted quartile: 0.001000
3rd weighted quartile: 0.020000
Min: 0.000000
Max: 0.100000
"""

HIGH_FLIP_TEXT = """Mean: 0.500000
Weighted median: 0.500000
1st weighted quartile: 0.500000
3rd weighted quartile: 0.500000
Min: 0.000000
Max: 1.000000
"""


def _write_manifest(tmp_path: Path, cases):
    manifest = {
        "schemaVersion": 1,
        "defaults": {
            "frame": 0,
            "image": {"width": 2, "height": 2},
            "compare": {"epsilon": 0.003},
        },
        "cases": cases,
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _rgb_png(path: Path, value=32):
    gate.write_png_rgb(path, 2, 2, [(value, value, value)] * 4)


def _capture_factory(calls=None, value=32):
    def capture(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        capture_dir = kwargs["capture_dir"]
        capture_dir.mkdir(parents=True, exist_ok=True)
        results = []
        for case in kwargs["cases"]:
            reference = capture_dir / (case["name"] + "-reference.png")
            actual = capture_dir / (case["name"] + "-maya.png")
            _rgb_png(reference, value)
            _rgb_png(actual, value)
            results.append(
                {
                    "name": case["name"],
                    "oracle_png": str(reference),
                    "actual_png": str(actual),
                }
            )
        report = capture_dir / "visual-regression-report.json"
        report.write_text(json.dumps({"results": results, "errors": []}), encoding="utf-8")
        log = capture_dir / "maya_visual_regression.log"
        log.write_text("Maya capture stub\n", encoding="utf-8")
        return {
            "status": "pass",
            "returncode": 0,
            "report_path": str(report),
            "log_path": str(log),
            "stdout": "stub stdout",
            "stderr": "",
        }

    return capture


def _fake_flip(calls=None):
    def flip(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        work_dir = kwargs["work_dir"]
        work_dir.mkdir(parents=True, exist_ok=True)
        text_path = work_dir / (kwargs["basename"] + ".txt")
        text_path.write_text(FLIP_TEXT, encoding="utf-8")
        error_map = work_dir / (kwargs["basename"] + ".png")
        _rgb_png(error_map, 0)
        return {
            "status": "pass",
            "returncode": 0,
            "text": FLIP_TEXT,
            "text_path": str(text_path),
            "error_map_path": str(error_map),
            "command": ["flip", kwargs["basename"]],
            "stdout": "",
            "stderr": "",
            "metrics": gate.parse_flip_metrics(FLIP_TEXT),
        }

    return flip


def _partial_capture_with_error(**kwargs):
    result = _capture_factory()(**kwargs)
    report_path = Path(result["report_path"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    failed_name = kwargs["cases"][1]["name"]
    failed_item = next(item for item in report["results"] if item["name"] == failed_name)
    Path(failed_item["oracle_png"]).unlink()
    Path(failed_item["actual_png"]).unlink()
    report["results"] = [item for item in report["results"] if item["name"] != failed_name]
    report["errors"] = [{"name": failed_name, "error": "fixture import failed"}]
    report_path.write_text(json.dumps(report), encoding="utf-8")
    result["status"] = "fail"
    result["returncode"] = 1
    return result


def test_manifest_classification_and_strict_selection(tmp_path):
    path = _write_manifest(
        tmp_path,
        [
            {"name": "alpha-overlap", "metadata": {"tags": ["alpha"]}},
            {"name": "outline-silhouette", "feature": "outline"},
            {"name": "shadow", "selfShadow": True},
        ],
    )
    _, cases = gate.load_manifest_cases(path)
    assert [case["feature"] for case in cases] == ["transparency", "outline", "self-shadow"]
    assert gate.select_cases(cases, "outline")[0]["name"] == "outline-silhouette"
    assert [case["name"] for case in gate.select_cases(cases, "all")] == [
        "alpha-overlap",
        "outline-silhouette",
        "shadow",
    ]
    try:
        gate.select_cases(cases, "outline", ["alpha-overlap"])
    except ValueError as error:
        assert "crosses feature" in str(error)
    else:
        raise AssertionError("cross-feature selection must fail closed")


def test_all_mode_generates_one_image_first_gallery_for_every_case(tmp_path):
    path = _write_manifest(
        tmp_path,
        [
            {"name": "alpha-case", "metadata": {"tags": ["alpha"]}},
            {"name": "generic-case"},
            {"name": "shadow-case", "selfShadow": True},
        ],
    )
    output = tmp_path / "latest"
    capture_calls = []
    flip_calls = []
    summary = gate.run_gate(
        path,
        "all",
        output_dir=output,
        project_root=tmp_path,
        capture_runner=_capture_factory(capture_calls),
        flip_runner=_fake_flip(flip_calls),
    )

    assert summary["status"] == "unreviewed"
    assert [case["name"] for case in summary["cases"]] == [
        "alpha-case",
        "generic-case",
        "shadow-case",
    ]
    assert summary["cases"][1]["feature"] == "unclassified"
    assert summary["cases"][2]["status"] == "unavailable"
    assert len(capture_calls) == 1
    assert [case["name"] for case in capture_calls[0]["cases"]] == [
        "alpha-case",
        "generic-case",
    ]
    assert len(flip_calls) == 2

    document = (output / "index.html").read_text(encoding="utf-8")
    assert document.count('class="case-card') == 3
    assert 'class="gallery"' in document
    assert "grid-template-columns:repeat(3,minmax(0,1fr))" in document
    assert ".gallery figure{margin:0;background:#fff}" in document
    assert "<table" not in document


def test_partial_capture_only_fails_the_case_without_a_capture(tmp_path):
    path = _write_manifest(
        tmp_path,
        [
            {"name": "captured-case", "feature": "outline"},
            {"name": "failed-case", "feature": "outline"},
        ],
    )
    summary = gate.run_gate(
        path,
        "outline",
        output_dir=tmp_path / "latest",
        project_root=tmp_path,
        capture_runner=_partial_capture_with_error,
        flip_runner=_fake_flip(),
    )

    assert [case["status"] for case in summary["cases"]] == ["unreviewed", "fail"]
    assert summary["cases"][0]["full"]["status"] == "pass"
    assert "fixture import failed" in summary["cases"][1]["errors"]


def test_flip_threshold_contract_is_separate_from_manifest_epsilon():
    assert gate.FLIP_THRESHOLD_CONTRACT["mode"] == "report-only"
    calibration = gate.FLIP_THRESHOLD_CONTRACT["calibration"]
    assert calibration["status"] == "evidence-gap"
    assert calibration["distributionInput"]["knownGood"]["sampleCount"] == 0
    assert calibration["distributionInput"]["knownGood"]["distribution"] is None
    assert calibration["distributionInput"]["knownBad"]["status"] == "test-only"
    assert calibration["distributionInput"]["knownBad"]["distribution"] is None
    assert gate.FLIP_THRESHOLD_CONTRACT["features"]["outline"]["full"]["mean"] != 0.003
    assert gate.parse_flip_metrics(FLIP_TEXT) == {
        "mean": 0.01,
        "weighted_median": 0.005,
        "q1": 0.001,
        "q3": 0.02,
        "min": 0.0,
        "max": 0.1,
    }
    assert gate.parse_flip_metrics("Mean: 0.1") ["q3"] is None


def test_fixed_output_is_replaced_and_html_contains_artifacts(tmp_path):
    oracle = tmp_path / "oracle"
    oracle.mkdir()
    case = {"name": "outline-case", "feature": "outline", "oracle": {"path": "oracle/case.jsonl"}}
    path = _write_manifest(tmp_path, [case])
    output = tmp_path / "latest"
    capture_calls = []
    flip_calls = []
    summary = gate.run_gate(
        path,
        "outline",
        output_dir=output,
        project_root=tmp_path,
        capture_runner=_capture_factory(capture_calls),
        flip_runner=_fake_flip(flip_calls),
    )
    assert summary["status"] == "unreviewed"
    assert summary["exitCode"] == 0
    assert len(capture_calls) == 1
    assert len(flip_calls) == 1
    assert (output / "index.html").is_file()
    assert (output / "summary.json").is_file()
    assert (output / "maya.log").is_file()
    assert (output / "cases/outline-case/reference.png").is_file()
    assert (output / "cases/outline-case/maya.png").is_file()
    assert (output / "cases/outline-case/flip-error.png").is_file()
    assert "reference.png" in (output / "index.html").read_text(encoding="utf-8")
    assert not (output / "cases/outline-case/.flip-full").exists()

    (output / "stale.txt").write_text("stale", encoding="utf-8")
    gate.run_gate(
        path,
        "outline",
        output_dir=output,
        project_root=tmp_path,
        capture_runner=_capture_factory(),
        flip_runner=_fake_flip(),
    )
    assert not (output / "stale.txt").exists()
    assert not (output / "cases/outline-case/.flip-full").exists()


def test_roi_is_compared_separately_and_recorded(tmp_path):
    case = {
        "name": "transparency-roi",
        "feature": "transparency",
        "roi": {"x": 0, "y": 0, "width": 1, "height": 1},
        "oracle": {"path": "oracle/case.jsonl"},
    }
    path = _write_manifest(tmp_path, [case])
    flip_calls = []
    summary = gate.run_gate(
        path,
        "transparency",
        output_dir=tmp_path / "latest",
        project_root=tmp_path,
        capture_runner=_capture_factory(),
        flip_runner=_fake_flip(flip_calls),
    )
    result = summary["cases"][0]
    assert len(flip_calls) == 2
    assert result["roiComparison"]["status"] == "pass"
    assert result["roiComparison"]["bounds"] == {"x": 0, "y": 0, "width": 1, "height": 1}
    assert (tmp_path / "latest/cases/transparency-roi/flip-roi.txt").is_file()
    assert (tmp_path / "latest/cases/transparency-roi/flip-error-roi.png").is_file()
    assert not (tmp_path / "latest/cases/transparency-roi/.roi").exists()


def test_roi_override_is_recorded_and_negative_control_exceeds_contract(tmp_path):
    path = _write_manifest(
        tmp_path,
        [{"name": "transparency-negative", "feature": "transparency"}],
    )

    def negative_flip(**kwargs):
        result = _fake_flip()(**kwargs)
        result["text"] = HIGH_FLIP_TEXT
        result["metrics"] = gate.parse_flip_metrics(HIGH_FLIP_TEXT)
        return result

    summary = gate.run_gate(
        path,
        "transparency",
        output_dir=tmp_path / "latest",
        project_root=tmp_path,
        roi_overrides={"transparency-negative": {"x": 0, "y": 0, "width": 1, "height": 1}},
        capture_runner=_capture_factory(),
        flip_runner=negative_flip,
    )

    result = summary["cases"][0]
    assert summary["roiOverrides"]["transparency-negative"]["width"] == 1
    assert result["roi"] == {"x": 0, "y": 0, "width": 1, "height": 1}
    assert result["full"]["thresholdEvaluation"]["status"] == "fail"
    assert result["roiComparison"]["thresholdEvaluation"]["status"] == "fail"
    assert summary["gateMode"] == "report-only"
    assert summary["status"] == "unreviewed"
    assert summary["exitCode"] == 0


def test_threshold_gate_is_explicit_opt_in_and_fails_negative_control(tmp_path):
    path = _write_manifest(
        tmp_path,
        [{"name": "transparency-negative", "feature": "transparency", "roi": {"x": 0, "y": 0, "width": 1, "height": 1}}],
    )

    def negative_flip(**kwargs):
        result = _fake_flip()(**kwargs)
        result["text"] = HIGH_FLIP_TEXT
        result["metrics"] = gate.parse_flip_metrics(HIGH_FLIP_TEXT)
        return result

    summary = gate.run_gate(
        path,
        "transparency",
        output_dir=tmp_path / "latest",
        project_root=tmp_path,
        capture_runner=_capture_factory(),
        flip_runner=negative_flip,
        enforce_thresholds=True,
    )

    assert summary["gateMode"] == "threshold"
    assert summary["thresholdGate"] == {"enabled": True, "failClosed": True}
    assert summary["status"] == "fail"
    assert summary["exitCode"] == 1
    assert summary["cases"][0]["status"] == "fail"
    assert any("threshold exceeded" in error for error in summary["cases"][0]["errors"])


def test_threshold_gate_reports_pass_only_after_complete_flip_evidence(tmp_path):
    path = _write_manifest(tmp_path, [{"name": "outline-case", "feature": "outline"}])
    summary = gate.run_gate(
        path,
        "outline",
        output_dir=tmp_path / "latest",
        project_root=tmp_path,
        capture_runner=_capture_factory(),
        flip_runner=_fake_flip(),
        enforce_thresholds=True,
    )

    assert summary["status"] == "pass"
    assert summary["exitCode"] == 0
    assert summary["cases"][0]["status"] == "pass"
    assert summary["cases"][0]["passDiagnostics"]["reportOnly"] is False
    assert summary["cases"][0]["passDiagnostics"]["thresholdEnforced"] is True


def test_threshold_gate_fails_closed_for_missing_capture_oracle_and_flip(tmp_path):
    path = _write_manifest(
        tmp_path,
        [{"name": "outline-case", "feature": "outline", "oracle": {"path": "oracle/case.jsonl"}}],
    )

    def missing_capture(**kwargs):
        return {"status": "fail", "reason": "capture missing", "report_path": None}

    missing_capture_summary = gate.run_gate(
        path,
        "outline",
        output_dir=tmp_path / "missing-capture" / "latest",
        project_root=tmp_path,
        capture_runner=missing_capture,
        enforce_thresholds=True,
    )
    assert missing_capture_summary["status"] == "fail"
    assert missing_capture_summary["capture"]["status"] == "fail"
    assert "selected case is missing from Maya capture report" in missing_capture_summary["cases"][0]["errors"]

    def missing_oracle(**kwargs):
        result = _capture_factory()(**kwargs)
        report_path = Path(result["report_path"])
        report = json.loads(report_path.read_text(encoding="utf-8"))
        oracle_path = Path(report["results"][0]["oracle_png"])
        oracle_path.unlink()
        report["results"][0].pop("oracle_png")
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return result

    missing_oracle_summary = gate.run_gate(
        path,
        "outline",
        output_dir=tmp_path / "missing-oracle" / "latest",
        project_root=tmp_path,
        capture_runner=missing_oracle,
        flip_runner=_fake_flip(),
        enforce_thresholds=True,
    )
    assert missing_oracle_summary["status"] == "fail"
    assert any("missing GoldenOracle PNG" in error for error in missing_oracle_summary["cases"][0]["errors"])

    def missing_flip(**kwargs):
        return {"status": "fail", "reason": "FLIP missing", "metrics": gate.parse_flip_metrics("")}

    missing_flip_summary = gate.run_gate(
        path,
        "outline",
        output_dir=tmp_path / "missing-flip" / "latest",
        project_root=tmp_path,
        capture_runner=_capture_factory(),
        flip_runner=missing_flip,
        enforce_thresholds=True,
    )
    assert missing_flip_summary["status"] == "fail"
    assert "FLIP missing" in missing_flip_summary["cases"][0]["errors"]


def test_cli_threshold_gate_is_opt_in():
    base = ["--manifest", "manifest.json", "--feature", "outline"]
    assert gate._parse_args(base).enforce_thresholds is False
    assert gate._parse_args(base + ["--enforce-flip-threshold"]).enforce_thresholds is True
    assert gate._parse_args(base + ["--enforce-thresholds"]).enforce_thresholds is True


def test_self_shadow_is_explicitly_not_gated_and_does_not_call_capture(tmp_path):
    path = _write_manifest(
        tmp_path,
        [{"name": "shadow-case", "selfShadow": True, "oracle": {"path": "oracle/case.jsonl"}}],
    )
    calls = []

    def unexpected_capture(**kwargs):
        calls.append(kwargs)
        raise AssertionError("self-shadow RO-0 must not call Maya capture")

    summary = gate.run_gate(
        path,
        "self-shadow",
        output_dir=tmp_path / "latest",
        project_root=tmp_path,
        capture_runner=unexpected_capture,
    )
    assert summary["status"] == "not-gated"
    assert summary["exitCode"] != 0
    assert summary["cases"][0]["status"] == "unavailable"
    assert summary["cases"][0]["oracleStatus"] == "unavailable"
    assert calls == []


def test_missing_flip_report_is_a_failure(tmp_path):
    path = _write_manifest(
        tmp_path,
        [{"name": "outline-case", "feature": "outline", "oracle": {"path": "oracle/case.jsonl"}}],
    )

    def failed_flip(**kwargs):
        return {"status": "fail", "reason": "FLIP missing", "metrics": gate.parse_flip_metrics("")}

    summary = gate.run_gate(
        path,
        "outline",
        output_dir=tmp_path / "latest",
        project_root=tmp_path,
        capture_runner=_capture_factory(),
        flip_runner=failed_flip,
    )
    assert summary["status"] == "fail"
    assert summary["exitCode"] != 0
    assert summary["cases"][0]["status"] == "fail"
