"""Focused regressions for deleting the final live morph contribution."""

from __future__ import annotations

from types import SimpleNamespace

from mmd_tools.converters import bone_morph_runtime, material_morph_runtime


def test_bone_builder_removes_owned_accumulator_when_contributions_become_empty(
    monkeypatch,
) -> None:
    removed = []
    monkeypatch.setattr(
        bone_morph_runtime.cmds,
        "objExists",
        lambda node: node in {"|root", "|root|joint", "accum"},
    )
    monkeypatch.setattr(
        bone_morph_runtime,
        "_collect_joints_by_bone_index",
        lambda _root: {0: "|root|joint"},
    )
    monkeypatch.setattr(bone_morph_runtime, "_iter_bone_morph_nodes", lambda _root: iter(()))
    monkeypatch.setattr(
        bone_morph_runtime,
        "_collect_contributions_by_joint",
        lambda *_args: {},
    )
    monkeypatch.setattr(
        bone_morph_runtime,
        "_collect_existing_accumulators",
        lambda: {"|root|joint": "accum", "|other|joint": "foreign"},
    )
    monkeypatch.setattr(
        bone_morph_runtime,
        "_remove_accumulator",
        lambda joint, node: removed.append((joint, node)),
    )

    result = bone_morph_runtime.build_bone_morph_graph("|root")

    assert result["success"] is True
    assert result["skipped"] == ["no_bone_morph_contributions"]
    assert removed == [("|root|joint", "accum")]


def test_material_builder_removes_owned_evaluator_when_contributions_become_empty(
    monkeypatch,
) -> None:
    removed = []
    monkeypatch.setattr(material_morph_runtime.cmds, "objExists", lambda node: node == "|root")
    monkeypatch.setattr(
        material_morph_runtime,
        "_collect_shaders_by_material_index",
        lambda _root: {0: "shader"},
    )
    monkeypatch.setattr(material_morph_runtime, "_iter_material_morph_nodes", lambda _root: iter(()))
    monkeypatch.setattr(
        material_morph_runtime,
        "_collect_contributions_by_shader",
        lambda *_args: {},
    )
    monkeypatch.setattr(
        material_morph_runtime,
        "_collect_existing_evaluators",
        lambda: {"shader": "evaluator", "foreignShader": "foreignEvaluator"},
    )
    monkeypatch.setattr(
        material_morph_runtime,
        "_remove_evaluator",
        lambda shader, node: removed.append((shader, node)),
    )

    result = material_morph_runtime.build_material_morph_graph("|root")

    assert result["success"] is True
    assert result["skipped"] == ["no_material_morph_contributions"]
    assert removed == [("shader", "evaluator")]


def test_material_cleanup_restores_upstream_connection_before_delete(monkeypatch) -> None:
    disconnected = []
    connected = []
    deleted = []

    def list_connections(plug, **kwargs):
        if plug == "eval.outputDiffuse" and kwargs.get("d"):
            return ["file1.colorGain"]
        if plug == "eval.baseDiffuse" and kwargs.get("s"):
            return ["anim.output"]
        return []

    fake_cmds = SimpleNamespace(
        nodeType=lambda _node: "standardSurface",
        listConnections=list_connections,
        disconnectAttr=lambda source, destination: disconnected.append((source, destination)),
        delete=lambda node: deleted.append(node),
    )
    monkeypatch.setattr(material_morph_runtime, "cmds", fake_cmds)
    monkeypatch.setattr(
        material_morph_runtime,
        "resolve_shader_color_route",
        lambda _shader: SimpleNamespace(attr_name="baseColor"),
    )
    monkeypatch.setattr(
        material_morph_runtime,
        "_connect_if_needed",
        lambda source, destination, **_kwargs: connected.append((source, destination)),
    )

    material_morph_runtime._remove_evaluator("shader", "eval")

    assert ("eval.outputDiffuse", "file1.colorGain") in disconnected
    assert ("anim.output", "eval.baseDiffuse") in disconnected
    assert connected == [("anim.output", "file1.colorGain")]
    assert deleted == ["eval"]
