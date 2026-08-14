"""Pure-Python contract checks for the Authoring performance benchmark."""

from tools.authoring_performance_contract import (
    MAX_ADAPTER_CALLS,
    _CallRecorder,
    adapter_call_scope,
    case_limit_errors,
    distribution,
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
