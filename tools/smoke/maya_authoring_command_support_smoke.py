"""Maya standalone smoke for the narrow native Authoring mutation command."""

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


def _call(updates):
    return json.loads(cmds.mmdAuthoringSetAttrs(payload=json.dumps({"version": 1, "updates": updates})))


def _raw(payload):
    return json.loads(cmds.mmdAuthoringSetAttrs(payload=payload))


def _lock_without_history(plug, locked):
    cmds.undoInfo(stateWithoutFlush=False)
    try:
        cmds.setAttr(plug, lock=locked)
    finally:
        cmds.undoInfo(stateWithoutFlush=True)


cmds.loadPlugin(plugin, quiet=True)
assert callable(getattr(cmds, "mmdAuthoringSetAttrs", None))
cmds.file(new=True, force=True)

node = cmds.createNode("transform", name="authoringWitness")
cmds.addAttr(node, longName="mmdAuthoringWitnessBool", attributeType="bool")
cmds.addAttr(node, longName="mmdAuthoringWitnessInt", attributeType="long")
cmds.addAttr(node, longName="mmdAuthoringWitnessDouble", attributeType="double")
cmds.addAttr(node, longName="mmdAuthoringWitnessString", dataType="string")
canonical = cmds.ls(node, long=True)[0]
cmds.setAttr(canonical + ".mmdAuthoringWitnessString", "before", type="string")
updates = [
    {"plug": canonical + ".mmdAuthoringWitnessBool", "type": "bool", "value": True},
    {"plug": canonical + ".mmdAuthoringWitnessInt", "type": "int", "value": 7},
    {"plug": canonical + ".mmdAuthoringWitnessDouble", "type": "double", "value": 2.5},
    {"plug": canonical + ".mmdAuthoringWitnessString", "type": "string", "value": "日本語"},
]
for update in updates:
    single = _call([update])
    assert single["ok"] is True, (update, single)
    cmds.undo()
result = _call(updates)
assert result["ok"] is True, result
assert [cmds.getAttr(item["plug"]) for item in updates] == [True, 7, 2.5, "日本語"]
cmds.undo()
undone = [cmds.getAttr(item["plug"]) for item in updates]
assert undone == [False, 0, 0.0, "before"], undone
cmds.redo()
assert [cmds.getAttr(item["plug"]) for item in updates] == [True, 7, 2.5, "日本語"]

_lock_without_history(updates[0]["plug"], True)
cmds.undo()
assert [cmds.getAttr(item["plug"]) for item in updates] == [True, 7, 2.5, "日本語"]
_lock_without_history(updates[0]["plug"], False)
cmds.flushUndo()
cmds.undoInfo(stateWithoutFlush=False)
try:
    cmds.setAttr(updates[0]["plug"], False)
    cmds.setAttr(updates[1]["plug"], 0)
    cmds.setAttr(updates[2]["plug"], 0.0)
    cmds.setAttr(updates[3]["plug"], "before", type="string")
finally:
    cmds.undoInfo(stateWithoutFlush=True)
assert _call(updates)["ok"] is True
cmds.undo()
assert [cmds.getAttr(item["plug"]) for item in updates] == [False, 0, 0.0, "before"]
_lock_without_history(updates[0]["plug"], True)
cmds.redo()
assert [cmds.getAttr(item["plug"]) for item in updates] == [False, 0, 0.0, "before"]
_lock_without_history(updates[0]["plug"], False)
cmds.flushUndo()
assert _call(updates)["ok"] is True
assert [cmds.getAttr(item["plug"]) for item in updates] == [True, 7, 2.5, "日本語"]

before = [cmds.getAttr(item["plug"]) for item in updates]
bad = _call([updates[0], {"plug": canonical + ".translateX", "type": "double", "value": 9.0}])
assert bad["ok"] is False and bad["error"]["code"] == "plug_not_allowed"
assert [cmds.getAttr(item["plug"]) for item in updates] == before

huge_version = _raw('{"version":4294967297,"updates":[]}')
assert huge_version["ok"] is False and huge_version["error"]["code"] == "invalid_payload"
huge_int = _call([{**updates[1], "value": 4294967297}])
assert huge_int["ok"] is False and huge_int["error"]["code"] == "value_type_mismatch"
duplicate_key = _raw('{"version":1,"version":1,"updates":[]}')
assert duplicate_key["ok"] is False and duplicate_key["error"]["code"] == "duplicate_json_key"
assert [cmds.getAttr(item["plug"]) for item in updates] == before

duplicate = _call([updates[0], updates[0]])
assert duplicate["ok"] is False and duplicate["error"]["code"] == "duplicate_plug"
assert duplicate["command"] == "mmdAuthoringSetAttrs" and duplicate["phase"] == "prepare"
assert [cmds.getAttr(item["plug"]) for item in updates] == before

cmds.setAttr(updates[1]["plug"], lock=True)
locked = _call([updates[0], updates[1]])
assert locked["ok"] is False and locked["error"]["code"] == "plug_not_settable"
assert [cmds.getAttr(item["plug"]) for item in updates] == before
cmds.setAttr(updates[1]["plug"], lock=False)

type_error = _call([{**updates[2], "type": "string", "value": "2.5"}])
assert type_error["ok"] is False and type_error["error"]["code"] == "value_type_mismatch"
assert cmds.getAttr(updates[2]["plug"]) == 2.5

left = cmds.createNode("transform", name="left")
right = cmds.createNode("transform", name="right")
for parent in (left, right):
    child = cmds.createNode("transform", name="duplicate", parent=parent)
    cmds.addAttr(child, longName="mmdAuthoringWitnessBool", attributeType="bool")
ambiguous = _call([{"plug": "duplicate.mmdAuthoringWitnessBool", "type": "bool", "value": True}])
assert ambiguous["ok"] is False and ambiguous["error"]["code"] == "ambiguous_or_missing_node"
assert cmds.getAttr("|left|duplicate.mmdAuthoringWitnessBool") is False
assert cmds.getAttr("|right|duplicate.mmdAuthoringWitnessBool") is False

print("AUTHORING COMMAND SUPPORT SMOKE PASS")
