"""Maya standalone parity smoke for native Material value writes."""

from __future__ import annotations

from dataclasses import replace
import json
import math
import os
from pathlib import Path
import time

plugin = os.environ.get("MMD_TOOLS_CPP_PLUGIN")
if not plugin:
    version = os.environ.get("MAYA_VERSION", "2024")
    config = os.environ.get("MMD_TOOLS_CPP_CONFIG", "Debug")
    plugin = os.path.abspath(os.path.join("plug-ins", version, config, "mmd_tools_cpp.mll"))
os.environ["PATH"] = os.path.dirname(plugin) + os.pathsep + os.environ.get("PATH", "")
_dll_directory = os.add_dll_directory(os.path.dirname(plugin)) if hasattr(os, "add_dll_directory") else None

import maya.standalone  # noqa: E402

maya.standalone.initialize(name="python")
from maya import cmds  # noqa: E402
from maya.api import OpenMaya as om  # noqa: E402

from mmd_tools.adapters.maya_authoring_factory import build_maya_authoring_composition  # noqa: E402
from mmd_tools.core.model_authoring_spec import MmdMaterialSpec  # noqa: E402
from tests.common.maya_plugin_setup import load_mmd_tools_plugin  # noqa: E402
from tools.smoke.authoring_command_support import MayaCommandRecorder  # noqa: E402


FIELDS = (
    ("n1", ("name_english",)),
    ("n4", ("name_english", "memo", "edge_size", "specular_coefficient")),
    (
        "n8",
        (
            "name_english",
            "memo",
            "edge_size",
            "specular_coefficient",
            "diffuse",
            "ambient",
            "edge_color",
            "draw_flags",
        ),
    ),
)


def variant(material, fields, phase):
    odd = bool(phase % 2)
    values = {
        "name_english": "Native A" if odd else "Native B",
        "memo": "memo-a" if odd else "memo-b",
        "edge_size": 1.125 if odd else 1.375,
        "specular_coefficient": 5.25 if odd else 7.75,
        "diffuse": (0.31, 0.42, 0.53, 0.64) if odd else (0.61, 0.52, 0.43, 0.74),
        "ambient": (0.12, 0.23, 0.34) if odd else (0.32, 0.21, 0.14),
        "edge_color": (0.11, 0.22, 0.33, 0.44) if odd else (0.41, 0.32, 0.23, 0.14),
        "draw_flags": int(material.draw_flags) ^ 0x10,
    }
    return replace(material, **{field: values[field] for field in fields})


def stable(value):
    if isinstance(value, dict):
        return {key: stable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [stable(item) for item in value]
    if isinstance(value, float):
        return round(value, 6)
    return value


def equal(left, right):
    return stable(left.to_mapping()) == stable(right.to_mapping())


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))]


repo_root = Path(__file__).resolve().parents[2]
load_mmd_tools_plugin(repo_root, cmds_module=cmds)
cmds.loadPlugin(plugin, quiet=True)
assert callable(getattr(cmds, "mmdAuthoringSetMaterialValues", None))
cmds.file(new=True, force=True)
cmds.undoInfo(state=True)
root = str(cmds.ls(cmds.createNode("transform", name="materialValueSmoke_root"), long=True)[0])
composition = build_maya_authoring_composition(cmds)
source = MmdMaterialSpec(
    "Material",
    name_english="Material",
    index=0,
    diffuse=(0.8, 0.7, 0.6, 1.0),
    specular=(0.1, 0.2, 0.3),
    specular_coefficient=5.0,
    ambient=(0.2, 0.2, 0.2),
    draw_flags=0,
    edge_color=(0.0, 0.0, 0.0, 1.0),
    edge_size=1.0,
    memo="initial",
)
bound, shader, _group = composition.material_authoring.create_material(root, source)
coordinator = composition.coordinator
current = coordinator.read_material_value(root, 0, shader)

# One exact N8 semantic/Undo/Redo witness through the forced native path.
os.environ["MMD_AUTHORING_MATERIAL_VALUE_MODE"] = "native"
target = variant(current, FIELDS[-1][1], 1)
actual = coordinator.apply_material_value_patch(root, target)
assert equal(actual, target)
assert equal(coordinator.read_material_value(root, 0, shader), target)
cmds.undo()
assert equal(coordinator.read_material_value(root, 0, shader), current)
cmds.redo()
assert equal(coordinator.read_material_value(root, 0, shader), target)
current = coordinator.read_material_value(root, 0, shader)


def raw(payload):
    return json.loads(cmds.mmdAuthoringSetMaterialValues(payload=json.dumps(payload, separators=(",", ":"))))


def raw_text(payload):
    return json.loads(cmds.mmdAuthoringSetMaterialValues(payload=payload))


name_plug = "{}.mmd_material_name_en".format(shader)
memo_plug = "{}.mmd_memo".format(shader)
base_payload = {
    "version": 1,
    "root": root,
    "shader": shader,
    "material_index": 0,
    "updates": [{"field": "name_english", "value": "valid"}],
}
assert raw({**base_payload, "updates": [{"field": "unknown", "value": 1}]})["error"]["code"] == "field_not_allowed"
cmds.setAttr("{}.mmd_material_name_en".format(shader), lock=True)
locked = raw(base_payload)
assert locked["ok"] is False and locked["error"]["code"] == "plug_not_settable", locked
cmds.setAttr("{}.mmd_material_name_en".format(shader), lock=False)
assert raw({**base_payload, "updates": base_payload["updates"] * 2})["error"]["code"] == "duplicate_field"
duplicate_json = raw_text(
    '{"version":1,"version":1,"root":'
    + json.dumps(root)
    + ',"shader":'
    + json.dumps(shader)
    + ',"material_index":0,"updates":[{"field":"name_english","value":"x"}]}'
)
assert duplicate_json["error"]["code"] == "duplicate_json_key", duplicate_json
wrong_index = raw({**base_payload, "material_index": 99})
assert wrong_index["error"]["code"] == "material_not_owned", wrong_index
nonfinite = raw({**base_payload, "updates": [{"field": "edge_size", "value": float("nan")}]})
assert nonfinite["error"]["code"] in {"invalid_json", "invalid_value"}, nonfinite
registry = cmds.listConnections(
    "{}.mmd_model_registry".format(root), source=True, destination=False
)[0]
forged_root = str(cmds.ls(cmds.createNode("transform", name="forgedMaterial_root"), long=True)[0])
cmds.addAttr(forged_root, longName="mmd_model_registry", attributeType="message")
cmds.connectAttr("{}.message".format(registry), "{}.mmd_model_registry".format(forged_root))
ownership_preimage = (cmds.getAttr(name_plug), cmds.getAttr(memo_plug))
cross_root = raw({**base_payload, "root": forged_root})
assert cross_root["error"]["code"] == "material_not_owned", cross_root
assert (cmds.getAttr(name_plug), cmds.getAttr(memo_plug)) == ownership_preimage
schema_plug = "{}.mmd_model_registry_schema".format(registry)
cmds.setAttr(schema_plug, "invalid", type="string")
bad_schema = raw(base_payload)
assert bad_schema["error"]["code"] == "material_not_owned", bad_schema
assert (cmds.getAttr(name_plug), cmds.getAttr(memo_plug)) == ownership_preimage
cmds.setAttr(schema_plug, "1", type="string")
member_array = "{}.materialMembers".format(registry)
indices = cmds.getAttr(member_array, multiIndices=True) or []
duplicate_member = "{}[{}]".format(member_array, max(indices, default=-1) + 1)
cmds.connectAttr("{}.message".format(shader), duplicate_member)
duplicate_ownership = raw(base_payload)
assert duplicate_ownership["error"]["code"] == "material_not_owned", duplicate_ownership
assert (cmds.getAttr(name_plug), cmds.getAttr(memo_plug)) == ownership_preimage
cmds.disconnectAttr("{}.message".format(shader), duplicate_member)
sparse_valid = raw(
    {**base_payload, "updates": [{"field": "name_english", "value": ownership_preimage[0]}]}
)
assert sparse_valid["ok"] is True, sparse_valid
cmds.removeMultiInstance(duplicate_member, b=True)
cmds.undoInfo(stateWithoutFlush=False)
disabled_preimage = coordinator.read_material_value(root, 0, shader)
try:
    coordinator.apply_material_value_patch(
        root, replace(disabled_preimage, name_english="must-not-write")
    )
except Exception as error:
    assert "undo must be enabled" in str(error)
else:
    raise AssertionError("native production route accepted disabled Maya undo")
assert equal(coordinator.read_material_value(root, 0, shader), disabled_preimage)
cmds.undoInfo(stateWithoutFlush=True)

# Lock the second scalar after the first write.  The command must restore the
# first value in reverse order and leave no partial semantic state.
before_pair = (cmds.getAttr(name_plug), cmds.getAttr(memo_plug))
selection = om.MSelectionList()
selection.add(shader)
shader_object = selection.getDependNode(0)
callback_fired = [False]


def lock_memo_after_name(msg, plug, _other_plug, _client_data):
    if (
        not callback_fired[0]
        and msg & om.MNodeMessage.kAttributeSet
        and plug.partialName(useLongNames=True) == "mmd_material_name_en"
    ):
        callback_fired[0] = True
        cmds.setAttr(memo_plug, lock=True)


callback_id = om.MNodeMessage.addAttributeChangedCallback(shader_object, lock_memo_after_name)
mid_write = raw(
    {
        **base_payload,
        "updates": [
            {"field": "name_english", "value": "mid-write"},
            {"field": "memo", "value": "must-not-stick"},
        ],
    }
)
om.MMessage.removeCallback(callback_id)
assert mid_write["ok"] is False and mid_write["error"]["code"] == "write_or_verify_failed", mid_write
assert (cmds.getAttr(name_plug), cmds.getAttr(memo_plug)) == before_pair
cmds.setAttr(memo_plug, lock=False)

# An Undo-time second-write failure must restore the already-undone first
# field to the command's target.  Maya reports the failed Undo, while the
# scene remains at the exact post-command state.
undo_target = raw(
    {
        **base_payload,
        "updates": [
            {"field": "name_english", "value": "undo-target"},
            {"field": "memo", "value": "undo-target-memo"},
        ],
    }
)
assert undo_target["ok"] is True, undo_target
target_pair = (cmds.getAttr(name_plug), cmds.getAttr(memo_plug))
callback_fired[0] = False
callback_id = om.MNodeMessage.addAttributeChangedCallback(shader_object, lock_memo_after_name)
try:
    cmds.undo()
except RuntimeError:
    pass
om.MMessage.removeCallback(callback_id)
assert callback_fired[0] is True
assert (cmds.getAttr(name_plug), cmds.getAttr(memo_plug)) == target_pair
cmds.setAttr(memo_plug, lock=False)
cmds.flushUndo()

report = {"schema_version": 1, "maya": str(cmds.about(version=True)), "cases": []}
for label, fields in FIELDS:
    timings = {}
    calls = {}
    for mode in ("python", "native"):
        os.environ["MMD_AUTHORING_MATERIAL_VALUE_MODE"] = mode
        samples = []
        state = coordinator.read_material_value(root, 0, shader)
        phase_offset = 1 if state.name_english == "Native B" else 0
        for index in range(10):  # same 3 cold + 7 warm smoke protocol
            if index < 3:
                coordinator = build_maya_authoring_composition(cmds).coordinator
            before = state
            after = variant(before, fields, phase_offset + index)
            started = time.perf_counter_ns()
            state = coordinator.apply_material_value_patch(root, after)
            samples.append(time.perf_counter_ns() - started)
            assert equal(coordinator.read_material_value(root, 0, shader), after)
            cmds.undo()
            undone = coordinator.read_material_value(root, 0, shader)
            assert equal(undone, before), (label, mode, index, stable(before.to_mapping()), stable(undone.to_mapping()))
            cmds.redo()
            state = coordinator.read_material_value(root, 0, shader)
            assert equal(state, after)
        warm = samples[3:]
        timings[mode] = {"p50_ns": percentile(warm, 0.50), "p95_ns": percentile(warm, 0.95)}

        recorder = MayaCommandRecorder(cmds)
        recorder.install()
        recorder.begin()
        state = coordinator.apply_material_value_patch(root, variant(state, fields, phase_offset + 10))
        calls[mode] = recorder.end()["maya_call_count"]
        recorder.restore()
    improved = (
        timings["native"]["p50_ns"] < timings["python"]["p50_ns"]
        and timings["native"]["p95_ns"] < timings["python"]["p95_ns"]
    )
    report["cases"].append(
        {"name": "material_value_{}".format(label), "timing": timings, "calls": calls, "native_improved_both": improved}
    )

report["default_decision"] = (
    "native" if all(case["native_improved_both"] for case in report["cases"]) else "python"
)
out_dir = repo_root / "build" / "reports" / "material_value_command"
out_dir.mkdir(parents=True, exist_ok=True)
(out_dir / "maya{}.json".format(os.environ.get("MAYA_VERSION", "unknown"))).write_text(
    json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
print("MATERIAL VALUE COMMAND METRICS " + json.dumps(report, sort_keys=True))
print("MATERIAL VALUE COMMAND SMOKE PASS")
