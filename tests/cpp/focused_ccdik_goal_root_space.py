"""Focused regression for root-relative mmdCcdIk goalWorldMatrix evaluation.

The native IK input pose is model-root relative, while a connected controller
worldMatrix includes the imported ``*_root`` transform.  A controller below a
translated model root must therefore produce the same solver output as an
equivalent top-level controller at the root-relative position.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, List


ROOT = Path(__file__).resolve().parents[2]


def _plugin_path() -> Path:
    """Resolve the C++ plugin for the selected Maya/config pair."""
    explicit = os.environ.get("MMD_TOOLS_CPP_PLUGIN")
    if explicit:
        path = Path(explicit)
        if path.exists():
            return path
        raise FileNotFoundError(path)

    version = os.environ.get("MAYA_VERSION", "2024")
    config = os.environ.get("MMD_TOOLS_CPP_CONFIG", "Debug")
    candidate = ROOT / "plug-ins" / version / config / "mmd_tools_cpp.mll"
    if candidate.exists():
        return candidate
    raise FileNotFoundError(candidate)


def _translation_matrix(x: float, y: float, z: float) -> List[float]:
    """Return a row-major Maya matrix with only translation authored."""
    return [
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        x, y, z, 1.0,
    ]


def _chain() -> dict[str, Any]:
    """Build a small bind-space chain whose target is off the rest line."""
    bones = []
    for slot, position in enumerate(((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0))):
        parent = slot - 1
        bones.append(
            {
                "parent_slot": parent,
                "rest_position": [position[0] - (1.0 if parent >= 0 else 0.0), position[1], position[2]],
                "maya_rest_translate": [position[0] - (1.0 if parent >= 0 else 0.0), position[1], position[2]],
                "maya_bind_world_matrix": _translation_matrix(*position),
                "no_orient_bind_world_matrix": _translation_matrix(*position),
                "joint_orient_deg": [0.0, 0.0, 0.0],
            }
        )
    return {
        "bones": bones,
        "links": [{"bone_slot": 1}, {"bone_slot": 0}],
        "targetBoneSlot": 2,
        "iterationCount": 40,
        "limitAngle": math.pi,
    }


def _snapshot(cmds: Any, node: str) -> List[float]:
    """Read all link Euler output children in link order."""
    values: List[float] = []
    for index in range(2):
        for axis in "XYZ":
            values.append(float(cmds.getAttr(f"{node}.outputRotate[{index}].outputRotateElement{axis}")))
    return values


def main() -> int:
    """Run the standalone native root-space regression."""
    import maya.cmds as cmds
    import maya.standalone

    plugin_path = _plugin_path()
    os.environ["PATH"] = str(plugin_path.parent) + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(plugin_path.parent))

    maya.standalone.initialize(name="python")
    try:
        cmds.loadPlugin(str(plugin_path), quiet=True)
        chain_json = json.dumps(_chain())
        root_relative_goal = (1.5, 0.5, 0.0)

        root = cmds.createNode("transform", name="focused_ccdik_goal_root")
        nested_goal = cmds.createNode("transform", name="focused_ccdik_goal_nested")
        top_goal = cmds.createNode("transform", name="focused_ccdik_goal_top")
        nested_node = cmds.createNode("mmdCcdIk", name="focused_ccdik_goal_nested_node")
        top_node = cmds.createNode("mmdCcdIk", name="focused_ccdik_goal_top_node")
        try:
            cmds.setAttr(f"{root}.translateX", 5.0)
            cmds.parent(nested_goal, root)
            cmds.setAttr(f"{nested_goal}.translateX", root_relative_goal[0])
            cmds.setAttr(f"{nested_goal}.translateY", root_relative_goal[1])
            cmds.setAttr(f"{top_goal}.translateX", root_relative_goal[0])
            cmds.setAttr(f"{top_goal}.translateY", root_relative_goal[1])

            for node, goal in ((nested_node, nested_goal), (top_node, top_goal)):
                cmds.setAttr(f"{node}.chainJson", chain_json, type="string")
                cmds.setAttr(f"{node}.enabled", True)
                cmds.connectAttr(f"{goal}.worldMatrix[0]", f"{node}.goalWorldMatrix", force=True)

            nested = _snapshot(cmds, nested_node)
            top = _snapshot(cmds, top_node)
            max_error = max((abs(lhs - rhs) for lhs, rhs in zip(nested, top)), default=0.0)
            if max_error > 1.0e-4:
                raise RuntimeError(
                    "goalWorldMatrix was not normalized by the model root: "
                    f"max_error={max_error:.8f}, nested={nested}, top={top}"
                )
            print(f"OK: mmdCcdIk root-relative goalWorldMatrix parity (max_error={max_error:.8f})")
        finally:
            cmds.delete(nested_node, top_node, nested_goal, top_goal, root)
    finally:
        maya.standalone.uninitialize()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
