"""Host-neutral contracts for the local asset roundtrip runner."""

import os
import hashlib
import json
from types import SimpleNamespace

import pytest

from tools.local_asset_roundtrip import (
    _metric_snapshot,
    _classify_failure,
    _allowed_warning_codes,
    _assert_execute_warnings,
    _export_request,
    _load_manifest,
    _motion_evaluation_frames,
    _require_import_success,
    _repetitions,
    _select_cases,
    _vmd_mode_c_semantic_diff,
    _vmd_edit_track_witness,
    _vmd_payload,
    _vmd_payload_diff,
)


def test_metric_snapshot_exposes_rss_on_windows():
    metrics = _metric_snapshot()

    assert set(metrics) == {"rss_bytes", "peak_rss_bytes"}
    if os.name == "nt":
        assert metrics["rss_bytes"] is not None
        assert metrics["peak_rss_bytes"] is not None


def _bone(interpolation=b"\x14" * 64):
    return SimpleNamespace(
        bone_name="センター",
        frame_number=0,
        position=(0.0, 0.0, 0.0),
        rotation=(0.0, 0.0, 0.0, 1.0),
        interpolation=interpolation,
    )


def test_vmd_payload_diff_requires_key_times_and_raw_interpolation():
    data = SimpleNamespace(
        header=SimpleNamespace(model_name="model"),
        bone_frames=[_bone()],
        morph_frames=[],
        camera_frames=[],
        light_frames=[],
        shadow_frames=[],
        ik_show_hide_frames=[],
    )

    payload = _vmd_payload(data)
    assert _vmd_payload_diff(payload, payload) == []

    changed = _vmd_payload(SimpleNamespace(**{**data.__dict__, "bone_frames": [_bone(b"\x15" * 64)]}))
    assert "bone[0].interpolation differs" in _vmd_payload_diff(payload, changed)


def test_profile_selects_one_dense_and_one_sparse_case_without_cartesian_pairs():
    cases = [
        {"name": "pmx_only", "classification": "pmx_only", "pmx": "model.pmx"},
        {"name": "dense_motion", "classification": "dense", "pmx": "model.pmx", "vmd": "dense.vmd"},
        {"name": "sparse_motion", "classification": "sparse", "pmx": "model.pmx", "vmd": "sparse.vmd"},
    ]

    selected = _select_cases(cases, profile="dense-hang-and-sparse-interpolation")

    assert [case["name"] for case in selected] == ["dense_motion", "sparse_motion"]


def test_dense_repetitions_are_cold_one_plus_warm_three():
    assert _repetitions({"classification": "dense"}, 1, 3) == 4
    assert _repetitions({"classification": "sparse"}, 1, 3) == 1
    with pytest.raises(ValueError):
        _select_cases([{"name": "dense", "classification": "dense"}], profile="dense-hang-and-sparse-interpolation")


def test_mode_c_semantics_allows_dense_key_inflation_but_requires_tracks():
    source = {"bone": [{"name": "センター", "frame": 0}], "morph": [], "camera": [], "light": [], "shadow": [], "ik": []}
    exported = {"bone": [{"name": "センター", "frame": 0}, {"name": "センター", "frame": 1}], "morph": [], "camera": [], "light": [], "shadow": [], "ik": []}
    assert _vmd_mode_c_semantic_diff(source, exported) == []
    assert _vmd_mode_c_semantic_diff(source, {**exported, "bone": []})


def test_exported_vmd_witness_requires_edited_frame_and_morph_value():
    payload = {
        "bone": [{"name": "センター", "frame": 12, "rotation": [0.0, 0.0, 0.1, 1.0]}],
        "morph": [{"name": "笑顔", "frame": 12, "value": 0.25}],
    }
    adjustment = {
        "frame": 12,
        "bone": {"track_name": "センター", "track_names": ["センター"]},
        "morph": {"track_name": "笑顔", "track_names": ["笑顔"], "after": 0.25},
    }
    witness = _vmd_edit_track_witness(payload, adjustment)
    assert witness["bone"]["track_name"] == "センター"
    assert witness["morph"]["value"] == 0.25
    with pytest.raises(AssertionError, match="missing edited bone"):
        _vmd_edit_track_witness({"bone": [], "morph": []}, adjustment)


def test_motion_evaluation_frames_contains_oracles_and_edit_neighbors():
    assert _motion_evaluation_frames([0, 10], 5) == [0, 4, 5, 6, 10]


def test_failure_classification_is_fail_closed():
    assert _classify_failure(status="timeout") == "performance_timeout"
    assert _classify_failure(error="VMD validation blocked") == "validation_blocked"
    assert _classify_failure(error="fresh semantic oracle mismatch") == "semantic_mismatch"
    assert _classify_failure(status="crash") == "environment_blocked"


def test_import_action_contract_rejects_partial_or_warning_results():
    clean = SimpleNamespace(outcome="success", warnings=[], root_node="|root")
    assert _require_import_success(clean, "ImportModelAction", require_root=True) == "|root"
    partial = SimpleNamespace(outcome="partial", warnings=["texture"], root_node="|root")
    with pytest.raises(RuntimeError, match="did not complete cleanly"):
        _require_import_success(partial, "ImportModelAction", require_root=True)


def test_export_request_matches_export_tab_shape_and_disables_raw_mode(tmp_path):
    request = _export_request(
        tmp_path / "motion.vmd",
        tmp_path / "report",
        export_format="vmd",
        target_model="|mmd_root",
        start_frame=0,
        end_frame=10,
        case={"name": "motion"},
    )
    options = request.options
    assert options["require_target"] is True
    assert options["require_current_model"] is True
    assert options["current_model_root"] == "|mmd_root"
    assert options["authoring_semantics"] == "auto"
    assert options["vmd_mode"] == "C"
    assert "preserve_raw_bone_transforms" not in options


def test_manifest_requires_schema_hashes_oracle_frames_and_adjustment(tmp_path):
    asset = tmp_path / "model.pmx"
    asset.write_bytes(b"fixture")
    digest = hashlib.sha256(b"fixture").hexdigest()
    base = {
        "schema_version": 2,
        "cases": [{
            "name": "model",
            "kind": "pmx",
            "classification": "pmx_only",
            "pmx": str(asset),
            "pmx_sha256": digest,
            "oracle_frames": [0],
            "adjustment": {"comment_suffix": " [smoke]"},
        }],
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(base), encoding="utf-8")
    _, loaded = _load_manifest(manifest)
    assert loaded["hashes_verified"] is True
    assert loaded["cases"][0]["pmx_sha256"] == digest

    bad = dict(base)
    bad["cases"] = [dict(base["cases"][0], pmx_sha256="0" * 64)]
    manifest.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        _load_manifest(manifest)

    missing_recipe = dict(base)
    missing_recipe["cases"] = [dict(base["cases"][0], adjustment=None)]
    manifest.write_text(json.dumps(missing_recipe), encoding="utf-8")
    with pytest.raises(ValueError, match="adjustment_recipe"):
        _load_manifest(manifest)

    missing_oracle = dict(base)
    missing_oracle["cases"] = [{key: value for key, value in base["cases"][0].items() if key != "oracle_frames"}]
    manifest.write_text(json.dumps(missing_oracle), encoding="utf-8")
    with pytest.raises(ValueError, match="oracle_frames"):
        _load_manifest(manifest)


def test_only_mode_c_raw_loss_warning_is_acknowledgeable():
    allowed = SimpleNamespace(
        report=SimpleNamespace(
            issues=[
                SimpleNamespace(code="VMD_MODE_C_RAW_LOSS", severity="warning"),
                SimpleNamespace(code="OTHER_WARNING", severity="warning"),
            ]
        )
    )
    assert _allowed_warning_codes(allowed, "vmd") == (["VMD_MODE_C_RAW_LOSS"], ["OTHER_WARNING"])
    with pytest.raises(RuntimeError, match="unexpected execute warnings"):
        _assert_execute_warnings(allowed, "pmx")
