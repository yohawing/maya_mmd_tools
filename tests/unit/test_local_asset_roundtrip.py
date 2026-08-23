"""Host-neutral contracts for the local asset roundtrip runner."""

import os
import hashlib
import json
import sys
import types
from types import SimpleNamespace

import pytest

from tools.local_asset_roundtrip import (
    VMD_EXPORT_BAKE_TIMELINE_POSE_TOLERANCE,
    _bounded_edit_value,
    _metric_snapshot,
    _classify_failure,
    _allowed_warning_codes,
    _assert_execute_warnings,
    _capture_ik_import_witness,
    _export_write_budget_evidence,
    _export_request,
    _load_manifest,
    _motion_evaluation_frames,
    _motion_phase_evidence,
    _bake_timeline_track_boundary_diff,
    _export_diagnostics_sink,
    _import_options,
    _import_model_action,
    _resolve_morph_controller_input_plug,
    parse_args,
    _require_import_success,
    _repetitions,
    _run_warm_vmd_export_samples,
    _skip_warm_vmd_export_samples,
    _streamed_bake_timeline_track_boundary_diff,
    _run_worker,
    _compare_morph_structure,
    _compare_motion_morph_witness_values,
    _select_cases,
    _summary_markdown,
    _worker_failure_classification,
    _vmd_bake_timeline_semantic_diff,
    _vmd_edit_track_witness,
    _vmd_payload,
    _vmd_payload_diff,
)


def _uv_morph_oracle(source_index, local_source_indices, uv_offset=None):
    return {
        "morphs": [
            {
                "index": 0,
                "name": "uv",
                "name_en": "uv",
                "type": "uv",
                "panel": 4,
                "offsets": [
                    {
                        "vertex_index": source_index,
                        "uv_offset": uv_offset or [0.1, 0.2, 0.0, 0.0],
                    }
                ],
            }
        ],
        "vertex_meshes": [
            {
                "vertex_count": len(local_source_indices),
                "source_vertex_indices": local_source_indices,
            }
        ],
        "unsupported_types": [],
    }


def test_morph_structure_compares_uv_offsets_by_scene_vertex_after_weld():
    source = _uv_morph_oracle(70890, [0, 70890])
    exported = _uv_morph_oracle(1, [0, 1])

    assert _compare_morph_structure(source, exported) == []


def test_morph_structure_rejects_uv_offset_bound_to_different_scene_vertex():
    source = _uv_morph_oracle(70890, [0, 70890])
    exported = _uv_morph_oracle(0, [0, 1])

    assert _compare_morph_structure(source, exported) == ["morphs[0].offsets differs"]


def test_morph_structure_rejects_uv_offset_missing_from_scene_provenance():
    source = _uv_morph_oracle(70890, [0])
    exported = _uv_morph_oracle(0, [0])

    assert _compare_morph_structure(source, exported) == [
        "morphs[0].offsets[0] references missing vertex 70890"
    ]


def test_bounded_edit_value_reverses_at_attribute_limits():
    assert _bounded_edit_value(1.0, 0.05, 0.0, 1.0) == pytest.approx(0.95)
    assert _bounded_edit_value(0.0, -0.05, 0.0, 1.0) == pytest.approx(0.05)
    assert _bounded_edit_value(0.5, 0.05, 0.0, 1.0) == pytest.approx(0.55)


def test_bounded_edit_value_rejects_degenerate_range():
    with pytest.raises(ValueError, match="cannot change"):
        _bounded_edit_value(1.0, 0.05, 1.0, 1.0)


def test_bake_timeline_pose_tolerance_covers_vmd_ccd_reconstruction_only():
    assert 0.0099 < VMD_EXPORT_BAKE_TIMELINE_POSE_TOLERANCE
    assert 0.0101 > VMD_EXPORT_BAKE_TIMELINE_POSE_TOLERANCE


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


def _empty_vmd_data(**sections):
    defaults = {
        "header": SimpleNamespace(model_name="model"),
        "bone_frames": [],
        "morph_frames": [],
        "camera_frames": [],
        "light_frames": [],
        "shadow_frames": [],
        "ik_show_hide_frames": [],
    }
    defaults.update(sections)
    return SimpleNamespace(**defaults)


def test_vmd_payload_diff_reports_current_scene_motion_changes():
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


def test_dense_repetitions_run_one_full_roundtrip_then_export_only_warm_samples():
    assert _repetitions({"classification": "dense"}, 1, 3) == 1
    assert _repetitions({"classification": "sparse"}, 1, 3) == 1
    with pytest.raises(ValueError):
        _select_cases([{"name": "dense", "classification": "dense"}], profile="dense-hang-and-sparse-interpolation")


def test_summary_explicitly_reports_cold_and_warm_export_samples():
    summary = _summary_markdown(
        {
            "status": "pass",
            "maya": "2024",
            "run_id": "test",
            "profile": None,
            "manifest": "manifest.json",
            "export_write_budget_sec": 60.0,
            "cases": [
                {
                    "name": "dense",
                    "classification": "dense",
                    "status": "pass",
                    "warm_runs": 3,
                    "runs": [
                        {
                            "status": "pass",
                            "result": {
                                "export_samples": {
                                    "cold": [{"status": "pass"}],
                                    "warm": [
                                        {"status": "pass"},
                                        {"status": "pass"},
                                        {"status": "pass"},
                                    ],
                                }
                            },
                        }
                    ],
                }
            ],
        }
    )

    assert "Export samples" in summary
    assert "cold=1/1, warm=3/3" in summary


def test_dense_warm_samples_use_distinct_outputs_and_the_source_target(monkeypatch, tmp_path):
    phase_requests = []
    export_requests = []

    class _Workflow:
        def validate(self, request):
            phase_requests.append(("validate", request))
            return SimpleNamespace(error=None, report=SimpleNamespace(issues=[]))

        def execute(self, request, acknowledge_warnings=False):
            phase_requests.append(("execute", request, acknowledge_warnings))
            return SimpleNamespace(succeeded=True, error=None, report=None)

    context = SimpleNamespace(
        phases=[],
        export_write_budget_violations=[],
        export_write_budget_sec=60.0,
    )

    def fake_phase(worker_context, name, function):
        result = function()
        worker_context.phases.append({"name": name, "wall_sec": 0.01, "status": "passed"})
        return result

    monkeypatch.setattr("tools.local_asset_roundtrip._phase", fake_phase)
    monkeypatch.setattr(
        "tools.local_asset_roundtrip._export_request",
        lambda output, report_dir, **kwargs: export_requests.append(
            {"output": output, "report_dir": report_dir, **kwargs}
        )
        or {"output": output},
    )
    monkeypatch.setattr(
        "tools.local_asset_roundtrip._allowed_warning_codes",
        lambda validation, export_format: ([], []),
    )
    monkeypatch.setattr(
        "tools.local_asset_roundtrip._assert_execute_warnings",
        lambda result, export_format: [],
    )

    samples = _run_warm_vmd_export_samples(
        {"name": "dense", "classification": "dense"},
        tmp_path,
        context,
        _Workflow(),
        "|edited_source_root",
        0,
        20,
        "rabbit",
        3,
    )

    assert [sample["status"] for sample in samples] == ["pass", "pass", "pass"]
    assert [sample["output"] for sample in samples] == [
        str(tmp_path / "motion-warm-01.vmd"),
        str(tmp_path / "motion-warm-02.vmd"),
        str(tmp_path / "motion-warm-03.vmd"),
    ]
    assert {request["target_model"] for request in export_requests} == {"|edited_source_root"}
    assert [request["output"].name for request in export_requests] == [
        "motion-warm-01.vmd",
        "motion-warm-02.vmd",
        "motion-warm-03.vmd",
    ]
    assert len([item for item in phase_requests if item[0] == "execute"]) == 3
    assert all((tmp_path / f"warm-export-{index:02d}.json").is_file() for index in range(1, 4))


def test_motion_phase_evidence_reports_boundaries_and_edit_to_first_file():
    context = SimpleNamespace(
        phases=[
            {"name": "motion_adjustment", "wall_sec": 2.0},
            {"name": "edited_motion_oracle", "wall_sec": 3.0},
            {"name": "export_bake_timeline", "wall_sec": 4.0},
            {"name": "export_write", "wall_sec": 6.0},
            {"name": "exported_parse", "wall_sec": 7.0},
        ],
    )
    evidence = _motion_phase_evidence(
        context,
        {"phase_timing": {"name": "export_bake_timeline", "wall_sec": 4.0}},
        context.phases[2:4],
    )

    assert evidence["export_bake_timeline"]["wall_sec"] == 4.0
    assert evidence["cold_export"]["wall_sec"] == 6.0
    assert evidence["edit_to_first_file"]["wall_sec"] == 15.0
    assert evidence["edit_to_first_file"]["method"].startswith("sum recorded phase wall_sec")


def test_export_diagnostics_sink_publishes_atomic_live_snapshot(tmp_path):
    path = tmp_path / "export-diagnostics.live.json"
    sink = _export_diagnostics_sink(path, "dense")

    started = json.loads(path.read_text(encoding="utf-8"))
    assert started["phase"] == "export_bake_timeline"
    assert started["snapshot"]["status"] == "started"

    sink({"native_sampler": {"status": "sampling_chunk", "chunk_index": 2}})
    current = json.loads(path.read_text(encoding="utf-8"))
    assert current["case"] == "dense"
    assert current["snapshot"]["native_sampler"]["chunk_index"] == 2
    assert not list(tmp_path.glob("*.tmp"))


def test_motion_morph_witness_uses_tolerance_without_relaxing_frame_keys():
    assert _compare_motion_morph_witness_values(
        {"0": 0.05, "10": 0.25},
        {"0": 0.050000000745, "10": 0.24999999},
    ) == []
    assert _compare_motion_morph_witness_values({"0": 0.05}, {"1": 0.05})
    assert _compare_motion_morph_witness_values({"0": 0.05}, {"0": 0.051})


def test_dense_worker_runs_full_case_once_and_passes_warm_count(monkeypatch, tmp_path):
    config_path = tmp_path / "worker-config.json"
    result_path = tmp_path / "worker-result.json"
    checkpoint = tmp_path / "phase-status.json"
    case = {"name": "dense", "classification": "dense", "vmd": "motion.vmd"}
    config_path.write_text(
        json.dumps(
            {
                "case": case,
                "out_dir": str(tmp_path / "case"),
                "repetitions": 4,
                "warm_runs": 3,
            }
        ),
        encoding="utf-8",
    )
    calls = []

    def fake_run_vmd_case(case_value, out_dir, context, *, warm_runs=0):
        calls.append((case_value, out_dir, warm_runs))
        return {
            "status": "pass",
            "export_samples": {
                "cold": [{"status": "pass"}],
                "warm": [{"status": "pass"}] * warm_runs,
            },
        }

    monkeypatch.setattr("tools.local_asset_roundtrip._initialize_maya", lambda: None)
    monkeypatch.setattr("tools.local_asset_roundtrip._run_vmd_case", fake_run_vmd_case)

    assert _run_worker(config_path, result_path, checkpoint, 60.0) == 0
    document = json.loads(result_path.read_text(encoding="utf-8"))
    assert len(calls) == 1
    assert calls[0][2] == 3
    assert len(document["runs"]) == 1
    assert document["warm_runs"] == 3
    assert document["status"] == "pass"


def test_cold_budget_failure_records_warm_samples_as_skipped(monkeypatch, tmp_path):
    context = SimpleNamespace(export_write_budget_sec=60.0)
    cold_budget = {
        "phase": "export_write",
        "classification": "performance_timeout",
        "expected_sec": 60.0,
        "actual_sec": 114.0,
    }

    samples = _skip_warm_vmd_export_samples(tmp_path, context, 3, cold_budget)

    assert [sample["status"] for sample in samples] == ["skipped"] * 3
    assert [sample["skip_reason"] for sample in samples] == [
        "skipped_due_to_cold_budget"
    ] * 3
    assert all(sample["failure_classification"] == "performance_timeout" for sample in samples)
    assert all(not sample["output_written"] for sample in samples)
    assert all((tmp_path / f"warm-export-{index:02d}.json").is_file() for index in range(1, 4))


def test_worker_fails_closed_when_warm_samples_are_skipped(monkeypatch, tmp_path):
    config_path = tmp_path / "worker-config.json"
    result_path = tmp_path / "worker-result.json"
    checkpoint = tmp_path / "phase-status.json"
    config_path.write_text(
        json.dumps(
            {
                "case": {"name": "dense", "classification": "dense", "vmd": "motion.vmd"},
                "out_dir": str(tmp_path / "case"),
                "repetitions": 1,
                "warm_runs": 3,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("tools.local_asset_roundtrip._initialize_maya", lambda: None)
    monkeypatch.setattr(
        "tools.local_asset_roundtrip._run_vmd_case",
        lambda case, out_dir, context, *, warm_runs=0: {
            "status": "pass",
            "export_samples": {
                "cold": [{"status": "fail"}],
                "warm": [
                    {
                        "status": "skipped",
                        "skip_reason": "skipped_due_to_cold_budget",
                        "failure_classification": "performance_timeout",
                        "error": "cold export_write exceeded budget",
                    }
                ]
                * warm_runs,
            },
        },
    )

    assert _run_worker(config_path, result_path, checkpoint, 60.0) == 1
    document = json.loads(result_path.read_text(encoding="utf-8"))
    assert document["status"] == "fail"
    assert document["runs"][0]["failure_classification"] == "performance_timeout"


def test_bake_timeline_semantics_allows_dense_key_inflation_but_requires_tracks():
    source = {"bone": [{"name": "センター", "frame": 0}], "morph": [], "camera": [], "light": [], "shadow": [], "ik": []}
    exported = {"bone": [{"name": "センター", "frame": 0}, {"name": "センター", "frame": 1}], "morph": [], "camera": [], "light": [], "shadow": [], "ik": []}
    assert _vmd_bake_timeline_semantic_diff(source, exported) == []
    assert _vmd_bake_timeline_semantic_diff(source, {**exported, "bone": []})


def test_bake_timeline_track_boundaries_exclude_only_model_unmatched_source_tracks():
    center = _bone()
    center.bone_name = "センター"
    unmatched = _bone()
    unmatched.bone_name = "別モデル専用"
    source = _vmd_payload(_empty_vmd_data(bone_frames=[center, unmatched]))
    collected = _vmd_payload(_empty_vmd_data(bone_frames=[center]))
    exported = _vmd_payload(_empty_vmd_data(bone_frames=[center]))

    failures = _bake_timeline_track_boundary_diff(
        source,
        collected,
        exported,
        {"bone": {"センター"}, "morph": set()},
    )

    assert failures == {"source_to_collected": [], "collected_to_export": []}


def test_bake_timeline_track_boundaries_reject_supported_physics_track_lost_in_collection():
    center = _bone()
    center.bone_name = "センター"
    physics = _bone()
    physics.bone_name = "右胸"
    source = _vmd_payload(_empty_vmd_data(bone_frames=[center, physics]))
    collected = _vmd_payload(_empty_vmd_data(bone_frames=[center]))

    failures = _bake_timeline_track_boundary_diff(
        source,
        collected,
        collected,
        {"bone": {"センター", "右胸"}, "morph": set()},
    )

    assert failures["source_to_collected"] == [
        "bone required tracks missing: ['右胸']"
    ]
    assert failures["collected_to_export"] == []


def test_bake_timeline_track_boundaries_accept_only_exact_committed_omissions():
    from mmd_tools.validation.snapshot import fingerprint_payload

    center = _bone()
    center.bone_name = "センター"
    physics = _bone()
    physics.bone_name = "右胸"
    source = _vmd_payload(_empty_vmd_data(bone_frames=[center, physics]))
    collected = _vmd_payload(_empty_vmd_data(bone_frames=[center]))
    identities = [["bone", "右胸"]]
    commitment = {"count": 1, "fingerprint": fingerprint_payload(identities)}

    accepted = _bake_timeline_track_boundary_diff(
        source,
        collected,
        collected,
        {"bone": {"センター", "右胸"}, "morph": set()},
        source_omission_commitment=commitment,
    )
    rejected = _bake_timeline_track_boundary_diff(
        source,
        collected,
        collected,
        {"bone": {"センター", "右胸"}, "morph": set()},
        source_omission_commitment={"count": 1, "fingerprint": "wrong"},
    )

    assert accepted["source_to_collected"] == []
    assert rejected["source_to_collected"][0].startswith(
        "source omission commitment does not exactly match"
    )


def test_bake_timeline_track_boundaries_reject_collected_authored_track_lost_by_writer():
    authored = _bone()
    authored.bone_name = "センター"
    source = _vmd_payload(_empty_vmd_data(bone_frames=[authored]))
    collected = _vmd_payload(_empty_vmd_data(bone_frames=[authored]))
    exported = _vmd_payload(_empty_vmd_data())

    failures = _bake_timeline_track_boundary_diff(
        source,
        collected,
        exported,
        {"bone": {"センター"}, "morph": set()},
    )

    assert failures["source_to_collected"] == []
    assert failures["collected_to_export"] == [
        "bone.count expected=1 actual=0"
    ]


def _dense_bake_timeline_payload():
    return {
        "model_name": "model",
        "bone": [
            {
                "name": "センター",
                "frame": frame,
                "position": [float(frame), 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0, 1.0],
                "interpolation": [20] * 64,
            }
            for frame in range(3)
        ],
        "morph": [
            {"name": "笑顔", "frame": frame, "value": frame * 0.1}
            for frame in range(3)
        ],
        "camera": [],
        "light": [],
        "shadow": [],
        "ik": [],
    }


def test_bake_timeline_track_boundaries_accept_identical_dense_collected_payload():
    payload = _dense_bake_timeline_payload()

    failures = _bake_timeline_track_boundary_diff(
        payload,
        payload,
        payload,
        {"bone": {"センター"}, "morph": {"笑顔"}},
    )

    assert failures == {"source_to_collected": [], "collected_to_export": []}


def test_streamed_bake_timeline_boundary_accepts_dense_output_with_matching_counts():
    source = {
        **_dense_bake_timeline_payload(),
        "bone": [_dense_bake_timeline_payload()["bone"][0]],
        "morph": [],
    }
    exported = _dense_bake_timeline_payload()

    failures = _streamed_bake_timeline_track_boundary_diff(
        source,
        exported,
        {"bone": {"センター"}, "morph": set()},
        {
            "bones": len(exported["bone"]),
            "morphs": len(exported["morph"]),
            "cameras": 0,
            "lights": 0,
            "shadows": 0,
            "ik": 0,
        },
    )

    assert failures == {"source_to_export": [], "collector_to_export": []}


def test_streamed_bake_timeline_boundary_rejects_collector_count_mismatch():
    payload = _dense_bake_timeline_payload()

    failures = _streamed_bake_timeline_track_boundary_diff(
        payload,
        payload,
        {"bone": {"センター"}, "morph": {"笑顔"}},
        {
            "bones": len(payload["bone"]) + 1,
            "morphs": len(payload["morph"]),
            "cameras": 0,
            "lights": 0,
            "shadows": 0,
            "ik": 0,
        },
    )

    assert failures["source_to_export"] == []
    assert failures["collector_to_export"] == ["bone.count expected=4 actual=3"]


@pytest.mark.parametrize(
    ("section", "index", "field", "replacement", "expected_failure"),
    [
        ("bone", 1, "frame", 7, "bone[1].frame differs"),
        ("bone", 1, "position", [1.25, 0.0, 0.0], "bone[1].position differs"),
        ("bone", 1, "rotation", [0.0, 0.0, 0.1, 0.995], "bone[1].rotation differs"),
        ("bone", 1, "interpolation", [21] * 64, "bone[1].interpolation differs"),
        ("morph", 1, "value", 0.25, "morph[1].value differs"),
    ],
)
def test_bake_timeline_track_boundaries_reject_writer_value_changes_with_same_tracks(
    section, index, field, replacement, expected_failure
):
    collected = _dense_bake_timeline_payload()
    exported = {
        **collected,
        section: [dict(item) for item in collected[section]],
    }
    exported[section][index][field] = replacement

    failures = _bake_timeline_track_boundary_diff(
        collected,
        collected,
        exported,
        {"bone": {"センター"}, "morph": {"笑顔"}},
    )

    assert failures["source_to_collected"] == []
    assert expected_failure in failures["collected_to_export"]


def test_bake_timeline_track_boundaries_reject_writer_count_change_with_same_track_name():
    collected = _dense_bake_timeline_payload()
    exported = {
        **collected,
        "bone": [dict(item) for item in collected["bone"][:-1]],
    }

    failures = _bake_timeline_track_boundary_diff(
        collected,
        collected,
        exported,
        {"bone": {"センター"}, "morph": {"笑顔"}},
    )

    assert failures["source_to_collected"] == []
    assert failures["collected_to_export"] == [
        "bone.count expected=3 actual=2"
    ]


def test_bake_timeline_ik_semantics_canonicalizes_state_order_only():
    source = {
        "bone": [],
        "morph": [],
        "camera": [],
        "light": [],
        "shadow": [],
        "ik": [{"frame": 0, "visible": 1, "states": [["右足IK", 0], ["左足IK", 1]]}],
    }
    reordered = {
        **source,
        "ik": [{"frame": 0, "visible": 1, "states": [["左足IK", True], ["右足IK", False]]}],
    }

    assert _vmd_bake_timeline_semantic_diff(source, reordered) == []


@pytest.mark.parametrize(
    ("change", "expected_fragment"),
    [
        ({"frame": 1}, "frame differs"),
        ({"visible": 0}, "visible differs"),
        ({"states": [["左足IK", 0], ["右足IK", 0]]}, "state_values differs"),
        ({"states": [["左足IK", 1], ["別IK", 0]]}, "state_names differ"),
        ({"states": [["左足IK", 1], ["左足IK", 0]]}, "duplicate IK state name"),
    ],
)
def test_bake_timeline_ik_semantics_rejects_non_order_changes(change, expected_fragment):
    source_item = {"frame": 0, "visible": 1, "states": [["左足IK", 1], ["右足IK", 0]]}
    actual_item = {**source_item, **change}
    source = {"bone": [], "morph": [], "camera": [], "light": [], "shadow": [], "ik": [source_item]}
    actual = {**source, "ik": [actual_item]}

    failures = _vmd_bake_timeline_semantic_diff(source, actual)

    assert any(expected_fragment in failure for failure in failures)


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


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("AssertionError: VMD semantic mismatch: ik track semantics differ", "semantic_mismatch"),
        ("exported VMD missing edited morph track at frame 100", "edit_failed"),
        ("ImportModelAction did not complete cleanly", "import_failed"),
    ],
)
def test_failure_classification_prefers_nested_error_over_fresh_import_phase(error, expected):
    assert _classify_failure(error=error, phase="fresh_import_oracle") == expected


def test_worker_failure_classification_propagates_nested_run_evidence():
    document = {
        "status": "fail",
        "failure_classification": "environment_blocked",
        "runs": [
            {
                "status": "fail",
                "failure_classification": "semantic_mismatch",
                "phase_timing": [{"name": "fresh_import_oracle"}],
            }
        ],
    }

    assert _worker_failure_classification(document) == "semantic_mismatch"


def test_export_write_budget_is_strictly_fail_closed():
    assert _export_write_budget_evidence(
        [{"name": "export_write", "wall_sec": 60.0}],
        60.0,
    ) is None
    evidence = _export_write_budget_evidence(
        [{"name": "export_write", "wall_sec": 60.001}],
        60.0,
    )
    assert evidence == {
        "phase": "export_write",
        "classification": "performance_timeout",
        "expected_sec": 60.0,
        "actual_sec": 60.001,
    }


def test_export_write_budget_is_configurable_from_cli():
    assert parse_args([]).export_write_budget_sec == 60.0
    assert parse_args(["--export-write-budget-sec", "75"]).export_write_budget_sec == 75.0


def test_import_options_match_production_rig_defaults():
    options = _import_options()

    assert options["setup_rig"] is True
    assert options["setup_bone_orientation"] is True


class _FakeMorphCmds:
    def __init__(self, controllers):
        self.controllers = list(controllers)
        self.calls = []

    def objExists(self, node):
        return node == "|model"

    def attributeQuery(self, attribute, node, exists=False):
        if not exists:
            raise AssertionError("fake only supports existence queries")
        return (node == "|model" and attribute == "mmd_morph_controller") or (
            node == "|controller" and attribute == "inputWeight"
        )

    def listConnections(self, plug, **kwargs):
        assert plug == "|model.mmd_morph_controller"
        return list(self.controllers)

    def nodeType(self, node):
        return "mmdMorphController" if node == "|controller" else "network"

    def getAttr(self, plug, **kwargs):
        self.calls.append((plug, kwargs))
        if plug == "|controller.inputWeight":
            return [2, 7]
        if ".weight" in plug:
            raise AssertionError("semantic downstream node.weight must not be selected")
        raise AssertionError(f"unexpected fake getAttr call: {plug}")


def test_morph_witness_resolves_model_owned_controller_input_by_index():
    fake_cmds = _FakeMorphCmds(["|controller"])

    plug = _resolve_morph_controller_input_plug("|model", 7, fake_cmds)

    assert plug == "|controller.inputWeight[7]"
    assert fake_cmds.calls == [("|controller.inputWeight", {"multiIndices": True})]


@pytest.mark.parametrize(
    ("controllers", "index", "message"),
    [
        ([], 7, "exactly one connection"),
        (["|controller", "|controller2"], 7, "exactly one connection"),
        (["|controller"], -1, "index is invalid"),
        (["|controller"], 3, "index is missing"),
    ],
)
def test_morph_witness_rejects_ambiguous_or_invalid_controller_input(
    controllers, index, message
):
    fake_cmds = _FakeMorphCmds(controllers)

    with pytest.raises(ValueError, match=message):
        _resolve_morph_controller_input_plug("|model", index, fake_cmds)


class _FakeIkCmds:
    def __init__(self, nodes):
        self.nodes = list(nodes)

    def objExists(self, node):
        return node == "|model"

    def nodeType(self, node):
        return "transform"

    def listRelatives(self, root, **kwargs):
        assert root == "|model"
        return ["|model|joint"]

    def ls(self, node=None, **kwargs):
        if kwargs.get("type") == "mmdCcdIk":
            return list(self.nodes)
        return [node] if node else []

    def listConnections(self, node, **kwargs):
        assert kwargs.get("type") == "joint"
        return ["|model|joint"]

    def attributeQuery(self, attribute, node, exists=False):
        assert exists is True
        return attribute in {"mmd_ik_bone_name", "enabled"} and node in self.nodes

    def getAttr(self, plug):
        if plug.endswith(".mmd_ik_bone_name"):
            return "足IK"
        if plug.endswith(".enabled"):
            return True
        raise AssertionError(f"unexpected fake IK getAttr call: {plug}")

    def keyframe(self, plug, **kwargs):
        assert plug.endswith(".enabled")
        assert kwargs == {"query": True, "timeChange": True}
        return [0.0, 12.0]


def test_ik_import_witness_captures_root_owned_nodes_names_and_enabled_keys():
    frames = [SimpleNamespace(ik_states=[("足IK", 1)])]
    witness = _capture_ik_import_witness(
        "|model",
        frames,
        _FakeIkCmds(["|model|solver_mmdCcdIk"]),
    )

    assert witness["names"] == ["足IK"]
    assert witness["required_names"] == ["足IK"]
    assert witness["nodes"][0]["enabled_key_times"] == [0.0, 12.0]


@pytest.mark.parametrize(
    ("nodes", "message"),
    [
        ([], "no root-owned mmdCcdIk"),
        (["|model|solver_mmdCcdIk"], "required tracks unresolved"),
    ],
)
def test_ik_import_witness_rejects_missing_or_unresolved_tracks(nodes, message):
    frames = [SimpleNamespace(ik_states=[("missingIK", 1)])]

    with pytest.raises(ValueError, match=message):
        _capture_ik_import_witness("|model", frames, _FakeIkCmds(nodes))


def test_import_action_contract_rejects_partial_or_warning_results():
    clean = SimpleNamespace(outcome="success", warnings=[], root_node="|root")
    assert _require_import_success(clean, "ImportModelAction", require_root=True) == "|root"
    partial = SimpleNamespace(outcome="partial", warnings=["texture"], root_node="|root")
    with pytest.raises(RuntimeError, match="did not complete cleanly"):
        _require_import_success(partial, "ImportModelAction", require_root=True)


def test_import_action_contract_explicitly_allows_only_missing_texture_warnings():
    missing_texture = {
        "reason": "missing_file",
        "resolvable": False,
        "original_path": "textures/body.png",
    }
    acknowledged = []
    partial = SimpleNamespace(
        outcome="partial",
        warnings=[missing_texture],
        root_node="|root",
    )

    assert (
        _require_import_success(
            partial,
            "ImportModelAction",
            require_root=True,
            allow_missing_texture_warnings=True,
            acknowledged_warnings=acknowledged,
        )
        == "|root"
    )
    assert acknowledged == [missing_texture]

    mixed = SimpleNamespace(
        outcome="partial",
        warnings=[missing_texture, {"reason": "invalid_material"}],
        root_node="|root",
    )
    with pytest.raises(RuntimeError, match="did not complete cleanly"):
        _require_import_success(
            mixed,
            "ImportModelAction",
            require_root=True,
            allow_missing_texture_warnings=True,
        )


class _FakeImportModelAction:
    """Injectable production-action stand-in for root identity tests."""

    def __init__(self, result):
        self._result = result

    def execute(self, _request):
        return self._result


class _FakeMayaCmds:
    def __init__(self, matches):
        self.matches = matches
        self.calls = []

    def ls(self, node, **kwargs):
        self.calls.append((node, kwargs))
        assert kwargs == {"long": True}
        return self.matches


def _patch_import_model_root_resolution(monkeypatch, matches):
    import mmd_tools.actions.import_model_action as import_model_action

    fake_cmds = _FakeMayaCmds(matches)
    fake_maya = types.ModuleType("maya")
    fake_maya.cmds = fake_cmds
    monkeypatch.setitem(sys.modules, "maya", fake_maya)
    result = SimpleNamespace(
        succeeded=True,
        outcome="success",
        warnings=[],
        root_node="model",
    )
    monkeypatch.setattr(
        import_model_action,
        "ImportModelAction",
        lambda: _FakeImportModelAction(result),
    )
    return fake_cmds


def test_import_model_action_canonicalizes_short_root_to_unique_long_path(monkeypatch, tmp_path):
    fake_cmds = _patch_import_model_root_resolution(monkeypatch, ["|asset|model"])

    assert _import_model_action(tmp_path / "model.pmx") == "|asset|model"
    assert fake_cmds.calls == [("model", {"long": True})]


@pytest.mark.parametrize(
    "matches",
    [([], "not a unique Maya DAG path"), (["|a|model", "|b|model"], "not a unique Maya DAG path")],
)
def test_import_model_action_fails_closed_when_root_is_missing_or_ambiguous(
    monkeypatch, tmp_path, matches
):
    _patch_import_model_root_resolution(monkeypatch, matches[0])

    with pytest.raises(RuntimeError, match=matches[1]):
        _import_model_action(tmp_path / "model.pmx")


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
    assert options["export_strategy"] == "bake_timeline"
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


def test_vmd_export_rejects_unexpected_warning_codes():
    allowed = SimpleNamespace(
        report=SimpleNamespace(
            issues=[
                SimpleNamespace(code="OTHER_WARNING", severity="warning"),
            ]
        )
    )
    assert _allowed_warning_codes(allowed, "vmd") == ([], ["OTHER_WARNING"])
    with pytest.raises(RuntimeError, match="unexpected execute warnings"):
        _assert_execute_warnings(allowed, "pmx")
