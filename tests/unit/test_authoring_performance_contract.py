"""Pure-Python contract checks for the Authoring performance benchmark."""

from tools.authoring_performance_contract import (
    MAX_ADAPTER_CALLS,
    SCALING_CASES,
    SCALING_OPERATIONS,
    _CallRecorder,
    adapter_call_scope,
    case_limit_errors,
    count_distribution,
    distribution,
    evaluate_scaling_gate,
    narrow_contract_errors,
    summarize_calls,
)


def test_display_budget_matches_atomic_transaction_baseline():
    assert MAX_ADAPTER_CALLS["display_apply"] == 16


def test_distribution_reports_nearest_rank_p50_and_p95():
    result = distribution([5, 1, 4, 2, 3])

    assert result["count"] == 5
    assert result["p50_ns"] == 3
    assert result["p95_ns"] == 5
    assert result["status"] == "measured"


def test_adapter_scope_distinguishes_targeted_and_broad_queries():
    targeted = adapter_call_scope("list_connections", ("|root|shader",), {"source": True})
    broad = adapter_call_scope("ls", ("*_root",), {"long": True})

    assert targeted == {"tokens": ["|root|shader"], "broad_collection": False}
    assert broad == {"tokens": ["*_root"], "broad_collection": True}


def test_summarize_calls_marks_bone_and_material_aggregate_scans():
    result = summarize_calls(
        [
            {
                "category": "adapter",
                "method": "list_relatives",
                "args": ("|root",),
                "kwargs": {"allDescendents": True, "type": "joint"},
                "node_tokens": ["|root"],
            },
            {
                "category": "adapter",
                "method": "list_connections",
                "args": ("|root_registry.materialMembers",),
                "kwargs": {"source": True},
                "node_tokens": ["|root_registry.materialMembers"],
            },
        ],
        allowed_nodes={"|root"},
        created_nodes=(),
        known_nodes={"|root"},
    )

    assert result["aggregate_scan_calls"] == [
        {"kind": "bone", "method": "list_relatives"},
        {"kind": "material", "method": "list_connections"},
    ]


def test_summarize_calls_reports_read_spec_and_unexpected_nodes():
    calls = [
        {"category": "coordinator", "method": "read_spec", "args": (), "kwargs": {}},
        {
            "category": "adapter",
            "method": "set_attr",
            "args": ("|root|target.value", 1.0),
            "kwargs": {},
            "node_tokens": ["|root|target.value"],
        },
        {
            "category": "adapter",
            "method": "get_attr",
            "args": ("|root|other.value",),
            "kwargs": {},
            "node_tokens": ["|root|other.value"],
        },
    ]

    result = summarize_calls(
        calls,
        allowed_nodes={"|root|target"},
        created_nodes=(),
        known_nodes={"|root|target", "|root|other"},
    )

    assert result["read_spec_calls"] == 1
    assert result["adapter_calls_by_method"] == {"get_attr": 1, "set_attr": 1}
    assert result["unexpected_nodes"] == ["|root|other"]


def test_recorder_preserves_deleted_targets_and_rejects_unexpected_creations():
    known = {"doomed"}

    class FakeAdapter:
        def delete(self, node):
            known.remove(node)

        def create_node(self, _node_type):
            known.add("created")
            return "created"

    recorder = _CallRecorder()
    recorder.wrap_adapter_class(FakeAdapter)
    recorder.begin()
    adapter = FakeAdapter()
    adapter.delete("doomed")
    adapter.create_node("network")
    calls = recorder.end()
    recorder.restore()

    result = summarize_calls(
        calls,
        allowed_nodes={"doomed"},
        created_nodes={"created"},
        known_nodes={"doomed", "created"},
    )

    assert result["touched_nodes"] == ["created", "doomed"]
    assert result["unexpected_created_nodes"] == ["created"]
    assert result["unexpected_nodes"] == ["created"]


def test_summarize_calls_allows_declared_target_to_be_recreated():
    calls = [
        {
            "category": "adapter",
            "method": "create_node",
            "args": ("file",),
            "kwargs": {},
            "node_tokens": ["material_file"],
        }
    ]

    result = summarize_calls(
        calls,
        allowed_nodes={"material_file"},
        created_nodes={"material_file"},
        known_nodes={"material_file"},
    )

    assert result["created_nodes"] == ["material_file"]
    assert result["unexpected_created_nodes"] == []
    assert result["unexpected_nodes"] == []


def test_narrow_contract_errors_fail_on_all_declared_scope_violations():
    summary = {
        "read_spec_calls": 1,
        "broad_collection_calls": ["ls"],
        "unexpected_nodes": ["|root|other"],
    }

    assert narrow_contract_errors("material_value_apply", summary) == [
        "narrow action called coordinator.read_spec",
        "narrow action performed broad collection enumeration",
        "narrow action touched or created nodes outside its declared target set",
    ]
    assert narrow_contract_errors("refresh_visible_material_tab", summary) == []


def test_case_limits_fail_closed_on_time_or_call_regression():
    timing = {"p95_ns": 1_600_000_000}

    errors = case_limit_errors("material_value_apply", timing, max_adapter_calls=276)

    assert errors == [
        "p95 1600.000 ms exceeds 1500.000 ms budget",
        "adapter calls 276 exceed 275 budget",
    ]


def _valid_scaling_gate():
    cases = []
    for index, configuration in enumerate(SCALING_CASES):
        operations = {}
        for operation in SCALING_OPERATIONS:
            samples = [
                {
                    "warmup": False,
                    "status": "measured",
                    "oracle_status": "pass",
                    "adapter_call_count": 4,
                    "adapter_calls_by_method": {"get_attr": 2, "list_connections": 2},
                }
                for _sample in range(3)
            ]
            timing = distribution([100_000_000, 110_000_000, 120_000_000])
            operations[operation] = {
                "status": "pass",
                "oracle_status": "pass",
                "warnings": [],
                "timing_ns": timing,
                "adapter_call_counts": count_distribution([4, 4, 4]),
                "adapter_method_histogram": {"get_attr": 6, "list_connections": 6},
                "aggregate_scan_calls": [],
                "read_spec_calls": 0,
                "samples": samples,
            }
        cases.append(
            {
                "name": configuration["name"],
                "status": "pass",
                "target_multipliers": {
                    "bones": configuration["bone_multiplier"],
                    "materials": configuration["material_multiplier"],
                },
                "counts": {
                    "bones": 118 * configuration["bone_multiplier"],
                    "materials": configuration["material_multiplier"],
                    "morphs": 1,
                },
                "operations": operations,
            }
        )
    return {
        "status": "measured",
        "expected_case_names": [configuration["name"] for configuration in SCALING_CASES],
        "baseline_counts": {"bones": 118, "materials": 1, "morphs": 1},
        "baseline_morph_count": 1,
        "p95_tolerance_ms": {"snapshot": 250.0, "refresh": 250.0},
        "cases": cases,
    }


def test_count_distribution_reports_call_count_p50_and_p95():
    result = count_distribution([5, 1, 4, 2, 3])

    assert result == {
        "count": 5,
        "min": 1,
        "p50": 3,
        "p95": 5,
        "max": 5,
        "status": "measured",
    }


def test_scaling_gate_requires_fixed_morphs_and_accepts_controlled_growth():
    result = evaluate_scaling_gate(_valid_scaling_gate())

    assert result["status"] == "pass"
    assert result["errors"] == []


def test_scaling_gate_rejects_nonconstant_calls_and_aggregate_scans():
    gate = _valid_scaling_gate()
    gate["cases"][1]["operations"]["snapshot"]["samples"][2]["adapter_call_count"] = 5
    gate["cases"][2]["operations"]["refresh"]["aggregate_scan_calls"] = [
        {"kind": "bone", "method": "list_relatives"}
    ]

    result = evaluate_scaling_gate(gate)

    assert result["status"] == "failed"
    assert any("call count is not constant" in error for error in result["errors"])
    assert any("aggregate Bone/Material scan" in error for error in result["errors"])


def test_scaling_gate_rejects_missing_oracle_and_p95_tolerance_breach():
    gate = _valid_scaling_gate()
    gate["cases"][0]["operations"]["refresh"]["oracle_status"] = "missing"
    gate["cases"][-1]["operations"]["snapshot"]["timing_ns"]["p95_ns"] = 400_000_000

    result = evaluate_scaling_gate(gate)

    assert result["status"] == "failed"
    assert any("oracle is missing or failed" in error for error in result["errors"])
    assert any("p95" in error and "baseline tolerance" in error for error in result["errors"])


def test_scaling_gate_rejects_morph_count_growth():
    gate = _valid_scaling_gate()
    gate["cases"][-1]["counts"]["morphs"] = 2

    result = evaluate_scaling_gate(gate)

    assert result["status"] == "failed"
    assert any("changed Morph count" in error for error in result["errors"])


def test_scaling_gate_rejects_not_run_report():
    result = evaluate_scaling_gate(
        {
            "status": "not_run",
            "expected_case_names": [configuration["name"] for configuration in SCALING_CASES],
            "cases": [],
        }
    )

    assert result["status"] == "failed"
    assert any("not passable" in error for error in result["errors"])
