"""Maya standalone correctness and performance smoke for native morph writes."""

from __future__ import annotations

import json
import os
import statistics
import struct
import time
from pathlib import Path

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

from tests.common.maya_plugin_setup import load_mmd_tools_plugin  # noqa: E402


def call(updates):
    payload = json.dumps({"version": 1, "updates": updates}, separators=(",", ":"))
    return json.loads(cmds.mmdAuthoringSetMorphWeights(payload=payload))


def raw_call(payload):
    return json.loads(cmds.mmdAuthoringSetMorphWeights(payload=payload))


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round((len(ordered) - 1) * fraction)))]


repo_root = Path(__file__).resolve().parents[2]
load_mmd_tools_plugin(repo_root, required_node_types=("mmdMorphController",), cmds_module=cmds)
cmds.loadPlugin(plugin, quiet=True)
assert callable(getattr(cmds, "mmdAuthoringSetMorphWeights", None))
cmds.file(new=True, force=True)

controller = cmds.createNode("mmdMorphController", name="weightSmokeController")
controller_plugs = []
for index in range(512):
    plug = "{}.inputWeight[{}]".format(controller, index)
    cmds.setAttr(plug, 0.0)
    controller_plugs.append(plug)

base = cmds.polyCube(name="weightSmokeBase")[0]
target = cmds.duplicate(base, name="weightSmokeTarget")[0]
cmds.move(0.1, "{}.vtx[0]".format(target), relative=True, objectSpace=True)
blend_shape = cmds.blendShape(target, base, name="weightSmokeBlendShape")[0]
blend_plug = "{}.weight[0]".format(blend_shape)

updates = [
    {"plug": controller_plugs[0], "value": 0.75},
    {"plug": blend_plug, "value": 0.3},
]
result = call(updates)
assert result["ok"] is True, result
blend_canonical = struct.unpack("f", struct.pack("f", 0.3))[0]
assert result["values"] == [0.75, blend_canonical], result
assert cmds.getAttr(controller_plugs[0]) == 0.75
assert cmds.getAttr(blend_plug) == blend_canonical
cmds.undo()
assert cmds.getAttr(controller_plugs[0]) == 0.0
assert cmds.getAttr(blend_plug) == 0.0
cmds.redo()
assert cmds.getAttr(controller_plugs[0]) == 0.75
assert cmds.getAttr(blend_plug) == blend_canonical

before = [cmds.getAttr(item["plug"]) for item in updates]
bad = call([updates[0], {"plug": "{}.translateX".format(base), "value": 1.0}])
assert bad["ok"] is False and bad["error"]["code"] == "plug_not_allowed", bad
assert [cmds.getAttr(item["plug"]) for item in updates] == before
locked = controller_plugs[1]
cmds.setAttr(locked, lock=True)
bad = call([{"plug": controller_plugs[0], "value": 0.1}, {"plug": locked, "value": 0.2}])
assert bad["ok"] is False and bad["error"]["code"] == "plug_not_settable", bad
assert cmds.getAttr(controller_plugs[0]) == before[0]
cmds.setAttr(locked, lock=False)
cmds.setAttr("{}.inputWeight".format(controller), lock=True)
parent_locked = call([{"plug": controller_plugs[0], "value": 0.1}])
assert parent_locked["ok"] is False and parent_locked["error"]["code"] == "plug_not_settable", parent_locked
cmds.setAttr("{}.inputWeight".format(controller), lock=False)

# A one-shot Maya callback changes the second target after preflight but before
# its write, exercising the command's reverse rollback rather than only its
# up-front validation.
cmds.setAttr(controller_plugs[0], 0.0)
cmds.setAttr(controller_plugs[1], 0.0)
selection = om.MSelectionList()
selection.add(controller)
controller_object = selection.getDependNode(0)
callback_fired = [False]


def lock_second_after_first(msg, plug, _other_plug, _client_data):
    if (
        not callback_fired[0]
        and msg & om.MNodeMessage.kAttributeSet
        and plug.isElement
        and plug.partialName(useLongNames=True) == "inputWeight[0]"
    ):
        callback_fired[0] = True
        cmds.setAttr(controller_plugs[1], lock=True)


callback_id = om.MNodeMessage.addAttributeChangedCallback(
    controller_object, lock_second_after_first
)
mid_write = call(
    [
        {"plug": controller_plugs[0], "value": 0.33},
        {"plug": controller_plugs[1], "value": 0.44},
    ]
)
om.MMessage.removeCallback(callback_id)
assert mid_write["ok"] is False and mid_write["error"]["code"] == "write_or_verify_failed", mid_write
assert cmds.getAttr(controller_plugs[0]) == 0.0
assert cmds.getAttr(controller_plugs[1]) == 0.0
cmds.setAttr(controller_plugs[1], lock=False)

assert call([updates[0], updates[0]])["error"]["code"] == "duplicate_plug"
assert call([{"plug": controller_plugs[0], "value": True}])["error"]["code"] == "invalid_update"
assert call([{"plug": controller_plugs[0], "value": float("nan")}])["error"]["code"] in {
    "invalid_json",
    "invalid_value",
}
huge = call([{"plug": controller_plugs[0], "value": 10**400}])
assert huge["ok"] is False and huge["error"]["code"] in {
    "invalid_json",
    "invalid_value",
}, huge
unknown_top = raw_call(
    json.dumps({"version": 1, "updates": [updates[0]], "unknown": 1})
)
assert unknown_top["ok"] is False and unknown_top["error"]["code"] == "invalid_payload", unknown_top

for namespace in ("weightLeft", "weightRight"):
    cmds.namespace(add=namespace)
    duplicate = cmds.createNode(
        "mmdMorphController", name="{}:duplicateWeight".format(namespace)
    )
    cmds.setAttr("{}.inputWeight[0]".format(duplicate), 0.0)
ambiguous = call([{"plug": "*:duplicateWeight.inputWeight[0]", "value": 0.9}])
assert ambiguous["ok"] is False and ambiguous["error"]["code"] == "ambiguous_or_missing_plug", ambiguous
assert cmds.getAttr("weightLeft:duplicateWeight.inputWeight[0]") == 0.0
assert cmds.getAttr("weightRight:duplicateWeight.inputWeight[0]") == 0.0

# Compare the transport actually chosen by Python today. The report is a
# policy witness; it intentionally does not turn noisy wall-clock timing into
# a correctness failure.
for _ in range(5):
    cmds.setAttr(controller_plugs[0], 0.25)
    call([{"plug": controller_plugs[0], "value": 0.25}])
python_times = []
native_times = []
for sample in range(120):
    value = (sample % 100) / 100.0
    started = time.perf_counter()
    cmds.setAttr(controller_plugs[0], value)
    cmds.getAttr(controller_plugs[0])
    python_times.append((time.perf_counter() - started) * 1000.0)
    started = time.perf_counter()
    call([{"plug": controller_plugs[0], "value": value}])
    native_times.append((time.perf_counter() - started) * 1000.0)

scales = {}
for count in (1, 32, 128, 512):
    batch = [{"plug": plug, "value": 0.0} for plug in controller_plugs[:count]]
    python_started = time.perf_counter()
    for update in batch:
        cmds.setAttr(update["plug"], update["value"])
        cmds.getAttr(update["plug"])
    python_ms = (time.perf_counter() - python_started) * 1000.0
    native_started = time.perf_counter()
    batch_result = call(batch)
    native_ms = (time.perf_counter() - native_started) * 1000.0
    assert batch_result["ok"] is True, batch_result
    scales[str(count)] = {"python_ms": python_ms, "native_ms": native_ms}

metrics = {
    "maya": os.environ.get("MAYA_VERSION", "unknown"),
    "samples": 120,
    "single": {
        "python_p50_ms": statistics.median(python_times),
        "python_p95_ms": percentile(python_times, 0.95),
        "native_p50_ms": statistics.median(native_times),
        "native_p95_ms": percentile(native_times, 0.95),
    },
    "reset_scale": scales,
}
metrics["single"]["native_improved_both"] = (
    metrics["single"]["native_p50_ms"] < metrics["single"]["python_p50_ms"]
    and metrics["single"]["native_p95_ms"] < metrics["single"]["python_p95_ms"]
)
print("MORPH WEIGHT COMMAND METRICS " + json.dumps(metrics, sort_keys=True))
print("MORPH WEIGHT COMMAND SMOKE PASS")
