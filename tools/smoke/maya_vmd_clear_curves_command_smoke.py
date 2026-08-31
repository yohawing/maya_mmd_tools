"""Maya standalone smoke for the destructive native VMD curve clear command."""

from __future__ import annotations

import json
import os

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


def call(plugs):
    payload = json.dumps({"version": 1, "plugs": plugs}, separators=(",", ":"))
    return json.loads(cmds.mmdVmdClearCurves(payload=payload))


def key_count(plug):
    return int(cmds.keyframe(plug, query=True, keyframeCount=True) or 0)


cmds.loadPlugin(plugin, quiet=True)
assert callable(getattr(cmds, "mmdVmdClearCurves", None))
cmds.file(new=True, force=True)

node = cmds.createNode("transform", name="vmdClearCurvesSmoke")
node_path = cmds.ls(node, long=True)[0]
translate = node_path + ".translate"
translate_x = node_path + ".translateX"
for frame in (1, 2, 3):
    cmds.setKeyframe(translate_x, time=frame, value=1.0)
result = call([translate_x])
assert result["ok"] is True and result["mutated"] is True, result
assert result["plugs"] == [{"plug": translate_x, "removed_count": 3}], result
assert result["curve_count"] == 1 and result["removed_count"] == 3, result
assert key_count(translate_x) == 0

# UTF-8 request text survives the native JSON -> MString error boundary.
unicode_plug = "|不在モデル|不在ノード.translateX"
result = call([unicode_plug])
assert result["ok"] is False and result["phase"] == "prepare", result
assert result["plugs"] == [{"plug": unicode_plug, "removed_count": 0}], result

# A compound request expands to existing children and removes each curve once.
for child in ("translateX", "translateY", "translateZ"):
    for frame in (1, 2):
        cmds.setKeyframe(node_path + "." + child, time=frame, value=2.0)
result = call([translate])
assert result["ok"] is True and result["curve_count"] == 3, result
assert result["removed_count"] == 6, result
assert all(key_count(node_path + "." + child) == 0 for child in ("translateX", "translateY", "translateZ"))

# One curve shared by children of the same requested compound is counted once.
shared_node = cmds.createNode("transform", name="vmdClearSharedCurveSmoke")
shared_path = cmds.ls(shared_node, long=True)[0]
shared_translate = shared_path + ".translate"
shared_curve = cmds.createNode("animCurveTL")
cmds.setKeyframe(shared_curve, time=1, value=4.0)
cmds.connectAttr(shared_curve + ".output", shared_path + ".translateX", force=True)
cmds.connectAttr(shared_curve + ".output", shared_path + ".translateY", force=True)
result = call([shared_translate])
assert result["ok"] is True and result["curve_count"] == 1, result
assert result["removed_count"] == 1, result
assert result["plugs"] == [{"plug": shared_translate, "removed_count": 1}], result

# Prepare rejects malformed/duplicate payloads without touching a seeded key.
for frame in (1, 2):
    cmds.setKeyframe(translate_x, time=frame, value=3.0)
before = key_count(translate_x)
duplicate = json.loads(cmds.mmdVmdClearCurves(payload='{"version":1,"plugs":[],"extra":1}'))
assert duplicate["ok"] is False and duplicate["phase"] == "prepare", duplicate
assert duplicate["mutated"] is False and key_count(translate_x) == before

# A later locked curve aborts the whole batch before the earlier safe curve mutates.
safe_node = cmds.createNode("transform", name="vmdClearPreflightSafe")
safe_plug = cmds.ls(safe_node, long=True)[0] + ".translateX"
cmds.setKeyframe(safe_plug, time=1, value=5.0)
safe_before = key_count(safe_plug)
curve = cmds.listConnections(translate_x, source=True, destination=False, type="animCurve")[0]
cmds.lockNode(curve, lock=True)
locked = call([safe_plug, translate_x])
assert locked["ok"] is False and locked["phase"] == "prepare", locked
assert "locked_curve" in locked["reason"], locked
assert locked["mutated"] is False and key_count(translate_x) == before
assert key_count(safe_plug) == safe_before
assert set(locked) == {
    "version",
    "command",
    "ok",
    "phase",
    "mutated",
    "plugs",
    "curve_count",
    "removed_count",
    "reason",
}, locked
assert locked["plugs"] == [
    {"plug": safe_plug, "removed_count": 0},
    {"plug": translate_x, "removed_count": 0},
], locked

print("VMD CLEAR CURVES COMMAND SMOKE PASS")
