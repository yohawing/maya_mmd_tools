"""Focused contracts for the active native authoring-command smokes."""

from __future__ import annotations

from tools.smoke.authoring_command_support import (
    COLD_ITERATIONS,
    MayaCommandRecorder,
    _command_targets,
    distribution,
    measure_case,
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


def test_measure_case_keeps_cold_warm_and_characterization_contract():
    cmds = _FakeCmds()
    recorder = MayaCommandRecorder(cmds)
    state = {"index": None}
    verified = []

    def action(index):
        state["index"] = index
        cmds.getAttr("shader.field{}".format(index))

    def verify_target(index):
        assert state["index"] == index

    def verify_undo_redo(index):
        verified.append(index)

    report = measure_case(
        name="support_contract",
        recorder=recorder,
        action=action,
        verify_target=verify_target,
        verify_undo_redo=verify_undo_redo,
        iterations=2,
        semantic_field_count=1,
        prepare_cold=lambda: None,
    )

    assert report["status"] == "pass"
    assert report["cold_iterations"] == COLD_ITERATIONS
    assert report["warm_iterations"] == 2
    assert len(report["samples"]) == COLD_ITERATIONS + 2
    assert report["warm_maya_calls"]["count"] == 2
    assert all(sample["maya_call_count"] == 1 for sample in report["samples"])
    assert len(verified) == 2 * (COLD_ITERATIONS + 2)
