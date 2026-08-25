from __future__ import annotations

from copy import deepcopy

import pytest

from tools.maya_cpp_patch_candidates_probe import (
    MayaCommandRecorder,
    _command_targets,
    build_decision,
    count_distribution,
    distribution,
)


class _FakeCmds:
    def getAttr(self, plug):
        return f"value:{plug}"

    def setAttr(self, plug, value, **_kwargs):
        return (plug, value)

    def listConnections(self, node, **_kwargs):
        return [node]


def test_distribution_uses_nearest_rank_percentiles():
    row = distribution([7, 1, 5, 3])
    assert row["p50_ns"] == 3
    assert row["p95_ns"] == 7
    assert row["mean_ns"] == 4.0


def test_command_targets_extract_attribute_query_plug():
    nodes, plugs = _command_targets("attributeQuery", ("mmd_name",), {"node": "|root"})
    assert nodes == {"|root"}
    assert plugs == {"|root.mmd_name"}


def test_command_targets_extract_add_attr_long_name_as_write_plug():
    nodes, plugs = _command_targets(
        "addAttr", ("shader",), {"longName": "mmd_outline", "attributeType": "bool"}
    )
    assert nodes == {"shader"}
    assert plugs == {"shader.mmd_outline"}


def test_command_recorder_counts_maya_boundary_and_graph_calls():
    cmds = _FakeCmds()
    recorder = MayaCommandRecorder(cmds)
    recorder.install()
    try:
        recorder.begin()
        cmds.getAttr("shader.mmd_name")
        cmds.setAttr("shader.mmd_name", "new", type="string")
        cmds.listConnections("shader")
        summary = recorder.end()
    finally:
        recorder.restore()

    assert summary["maya_call_count"] == 3
    assert summary["maya_calls_by_method"] == {
        "getAttr": 1,
        "listConnections": 1,
        "setAttr": 1,
    }
    assert summary["graph_discovery_call_count"] == 1
    assert summary["target_nodes"] == ["shader"]
    assert summary["target_plugs"] == ["shader.mmd_name"]
    assert summary["transaction_plug_count"] == 1
    assert summary["write_plug_count"] == 1
    assert summary["write_plugs"] == ["shader.mmd_name"]


def _case(name, plugs, calls):
    samples = []
    for index in range(10):
        elapsed = 150 + index if index < 3 else 100 + index
        samples.append(
            {
                "index": index,
                "temperature": "cold" if index < 3 else "warm",
                "elapsed_ns": elapsed,
                "status": "pass",
                "error": None,
                "maya_call_count": calls,
                "maya_calls_by_method": {"getAttr": calls},
                "target_node_count": 1,
                "target_plug_count": plugs,
                "transaction_plug_count": plugs,
                "write_plug_count": 1,
            }
        )
    cold = samples[:3]
    warm = samples[3:]
    return {
        "name": name,
        "status": "pass",
        "failures": 0,
        "cold_iterations": 3,
        "warm_iterations": 7,
        "semantic_field_count": 1,
        "cold_timing_ns": distribution([sample["elapsed_ns"] for sample in cold]),
        "warm_timing_ns": distribution([sample["elapsed_ns"] for sample in warm]),
        "warm_maya_calls": count_distribution(
            [sample["maya_call_count"] for sample in warm]
        ),
        "observed_target_node_count": 1,
        "observed_target_plug_count": plugs,
        "observed_transaction_plug_count": plugs,
        "observed_write_plug_count": 1,
        "undo_boundary": "one_action_one_undo_redo",
        "semantic_parity": "exact_preimage_and_target",
        "samples": samples,
    }


def _report(version, *, status="pass", material_plugs=32, material_calls=256):
    return {
        "schema_version": 2,
        "maya_version": version,
        "status": status,
        "fixture": "F:/fixture.pmx",
        "model": {"selected_shader_type": "dx11Shader"},
        "measurement": {
            "call_boundary": "raw maya.cmds Python API calls",
            "wall_clock": "production action with maya.cmds wrappers fully uninstalled",
            "vp2_override": "VirtualDeviceDx11",
        },
        "cases": [
            _case("material_value_n1", material_plugs, material_calls),
            _case("material_value_n4", material_plugs, material_calls),
            _case("material_value_n8", material_plugs, material_calls),
            _case("material_value_outline_n7", material_plugs, material_calls),
            _case("display_json_n1", 1, 12),
            _case("info_string_n1", 1, 10),
        ],
    }


def test_decision_adopts_only_cross_version_expensive_multi_plug_paths():
    decision = build_decision([_report("2024"), _report("2026")])
    by_id = {row["id"]: row for row in decision["candidates"]}
    assert decision["status"] == "complete"
    assert by_id["material_value_batch_command"]["decision"] == "adopt_for_implementation"
    assert by_id["material_outline_batch_command"]["decision"] == "adopt_for_implementation"
    assert by_id["display_json_command"]["decision"] == "do_not_adopt"
    assert by_id["info_string_command"]["decision"] == "do_not_adopt"


def test_decision_fails_closed_without_both_versions():
    decision = build_decision([_report("2024")])
    assert decision["status"] == "incomplete"
    assert all(row["decision"] == "do_not_adopt" for row in decision["candidates"])


def test_decision_rejects_material_when_one_version_is_single_plug():
    decision = build_decision(
        [_report("2024"), _report("2026", material_plugs=1)]
    )
    by_id = {row["id"]: row for row in decision["candidates"]}
    assert by_id["material_value_batch_command"]["decision"] == "do_not_adopt"
    assert by_id["material_outline_batch_command"]["decision"] == "do_not_adopt"


@pytest.mark.parametrize(
    ("plugs", "calls", "expected"),
    [
        (15, 128, "do_not_adopt"),
        (16, 127, "do_not_adopt"),
        (16, 128, "adopt_for_implementation"),
    ],
)
def test_material_decision_uses_manifest_threshold_boundaries(plugs, calls, expected):
    decision = build_decision(
        [
            _report("2024", material_plugs=plugs, material_calls=calls),
            _report("2026", material_plugs=plugs, material_calls=calls),
        ]
    )
    by_id = {row["id"]: row for row in decision["candidates"]}
    assert by_id["material_value_batch_command"]["decision"] == expected
    assert by_id["material_outline_batch_command"]["decision"] == expected


@pytest.mark.parametrize(
    "mutate",
    [
        lambda report: report.update(schema_version=99),
        lambda report: report["cases"].pop(),
        lambda report: report["cases"][0].update(status="failed"),
        lambda report: report["cases"][0].update(failures=1),
        lambda report: report["cases"][0].update(semantic_parity="unchecked"),
        lambda report: report["cases"][0]["warm_timing_ns"].update(p95_ns=float("nan")),
        lambda report: report["cases"][0].update(observed_transaction_plug_count=-1),
        lambda report: report.update(cases=None),
        lambda report: report["cases"].__setitem__(0, "not-a-case"),
        lambda report: report["cases"][0].update(name=[]),
        lambda report: report["cases"][0].update(name={}),
        lambda report: report["cases"][0].pop("warm_timing_ns"),
        lambda report: report["cases"][0].pop("samples"),
        lambda report: report["cases"][0]["samples"][0].update(elapsed_ns=-1),
        lambda report: report["cases"][0]["warm_timing_ns"].update(p50_ns=999999),
        lambda report: report["cases"][0]["warm_maya_calls"].update(p50=999999),
        lambda report: report["cases"][0].update(observed_transaction_plug_count=16.1),
        lambda report: report["cases"][0]["samples"][0].update(maya_call_count=128.5),
        lambda report: report.update(errors=["failure"]),
        lambda report: report.update(traceback="stack"),
    ],
)
def test_decision_schema_mutations_fail_closed(mutate):
    report_2024 = _report("2024")
    report_2026 = _report("2026")
    mutate(report_2026)
    decision = build_decision([report_2024, report_2026])
    assert decision["status"] == "incomplete"
    assert decision["validation_errors"]
    assert all(row["decision"] == "do_not_adopt" for row in decision["candidates"])


def test_decision_duplicate_version_fails_closed_without_overwrite():
    reports = [_report("2024"), deepcopy(_report("2024"))]
    decision = build_decision(reports)
    assert decision["status"] == "incomplete"
    assert "duplicate maya_version: 2024" in decision["validation_errors"]


def test_decision_rejects_forged_transaction_plugs_that_exceed_targets():
    reports = [_report("2024", material_plugs=1), _report("2026", material_plugs=1)]
    for report in reports:
        for case in report["cases"][:4]:
            case["observed_transaction_plug_count"] = 32
            for sample in case["samples"]:
                sample["transaction_plug_count"] = 32
    decision = build_decision(reports)
    assert decision["status"] == "incomplete"
    assert all(row["decision"] == "do_not_adopt" for row in decision["candidates"])
