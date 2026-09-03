"""Real-Maya smoke for explicit Group/Flip topology repair and Undo."""

from __future__ import annotations

import json

from maya import cmds, standalone

from mmd_tools.adapters.maya_cmds_adapter import MayaCmdsAdapter
from mmd_tools.adapters.maya_scene_metadata_backend import MayaSceneMetadataBackend


def _string(node: str, name: str, value: str) -> None:
    cmds.addAttr(node, longName=name, dataType="string")
    cmds.setAttr(f"{node}.{name}", value, type="string")


def _long(node: str, name: str, value: int) -> None:
    cmds.addAttr(node, longName=name, attributeType="long")
    cmds.setAttr(f"{node}.{name}", value)


def _morph(name: str, index: int, morph_type: str, offsets: list) -> str:
    node = cmds.createNode("network", name=name)
    _string(node, "mmd_morph_name", name)
    _string(node, "mmd_morph_name_en", name)
    _string(node, "mmd_morph_type", morph_type)
    _long(node, "mmd_morph_index", index)
    _long(node, "mmd_morph_panel", 4)
    attr = {
        "group": "mmd_group_morph_offsets_json",
        "bone": "mmd_bone_morph_offsets_json",
    }[morph_type]
    _string(node, attr, json.dumps(offsets, separators=(",", ":")))
    return node


def run() -> dict:
    standalone.initialize(name="python")
    cmds.file(new=True, force=True)
    root = cmds.group(empty=True, name="topologyRepairRoot")
    registry = cmds.createNode("network", name="topologyRepairRegistry")
    group = _morph(
        "topologyRepairGroup", 0, "group", [{"morph_index": 1, "morph_rate": 0.5}]
    )
    leaf = _morph("topologyRepairLeaf", 1, "bone", [])
    controller = cmds.createNode("network", name="topologyRepairController")

    cmds.addAttr(root, longName="mmd_model_registry", attributeType="message")
    cmds.addAttr(root, longName="mmd_morph_controller", attributeType="message")
    _string(registry, "mmd_model_registry_schema", "1")
    cmds.addAttr(registry, longName="modelRoot", attributeType="message")
    cmds.addAttr(registry, longName="morphMembers", attributeType="message", multi=True)
    _long(controller, "topologyVersion", 1)
    _string(controller, "groupTopology", "{}")
    cmds.connectAttr(f"{registry}.message", f"{root}.mmd_model_registry")
    cmds.connectAttr(f"{root}.message", f"{registry}.modelRoot")
    cmds.connectAttr(f"{group}.message", f"{registry}.morphMembers[0]")
    cmds.connectAttr(f"{leaf}.message", f"{registry}.morphMembers[1]")
    cmds.connectAttr(f"{controller}.message", f"{root}.mmd_morph_controller")

    backend = MayaSceneMetadataBackend(MayaCmdsAdapter())
    before = backend.inspect_morph_topology(root)
    expected = '{"1":[[0,0.5]]}'
    backend.begin_morph_topology_repair(root, expected)
    backend.apply_morph_topology_repair(root, expected)
    backend.commit_morph_topology_repair(root, expected)
    after = backend.inspect_morph_topology(root)
    cmds.undo()
    undone = cmds.getAttr(f"{controller}.groupTopology")
    undone_locks = (
        cmds.getAttr(f"{controller}.topologyVersion", lock=True),
        cmds.getAttr(f"{controller}.groupTopology", lock=True),
    )
    cmds.redo()
    redone = cmds.getAttr(f"{controller}.groupTopology")
    redone_locks = (
        cmds.getAttr(f"{controller}.topologyVersion", lock=True),
        cmds.getAttr(f"{controller}.groupTopology", lock=True),
    )
    report = {
        "before": [item.code for item in before.diagnostics],
        "after_valid": after.valid,
        "undone": undone,
        "redone": redone,
        "undone_locks": undone_locks,
        "redone_locks": redone_locks,
    }
    if report != {
        "before": ["stale"],
        "after_valid": True,
        "undone": "{}",
        "redone": expected,
        "undone_locks": (False, False),
        "redone_locks": (True, True),
    }:
        raise RuntimeError(f"morph topology repair smoke failed: {report!r}")
    print("MORPH TOPOLOGY REPAIR SMOKE PASS " + json.dumps(report, sort_keys=True))
    return report


if __name__ == "__main__":
    try:
        run()
    finally:
        standalone.uninitialize()
