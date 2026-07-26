"""Focused Maya smoke for mmdCcdIk goal compound dirty propagation.

The regression primes every public IK result, changes only ``goalY``, and
compares the result with a freshly-created node.  OutputRotate child plugs are
read one at a time (Z first) so a stale sibling cannot be hidden by a parent
compound read that happens to trigger recomputation.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, List, Tuple


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


def _flatten(values: Any) -> List[float]:
    """Flatten Maya's scalar/tuple/nested tuple attribute result."""
    if values is None:
        return []
    if isinstance(values, (int, float, bool)):
        return [float(values)]
    if not isinstance(values, (tuple, list)):
        return [float(values)]
    result: List[float] = []
    for value in values:
        result.extend(_flatten(value))
    return result


def _snapshot(cmds: Any, node: str) -> Tuple[List[List[float]], List[float], List[float], bool]:
    """Read all public outputs without reading the outputRotate parent."""
    output_rotate: List[List[float]] = []
    for index in range(2):
        # Z is intentionally read first.  Before the fix, goalY only dirtied
        # outputRotateY, so this sibling retained its cached pass-through value.
        output_z = float(cmds.getAttr(f"{node}.outputRotate[{index}].outputRotateElementZ"))
        output_x = float(cmds.getAttr(f"{node}.outputRotate[{index}].outputRotateElementX"))
        output_y = float(cmds.getAttr(f"{node}.outputRotate[{index}].outputRotateElementY"))
        output_rotate.append([output_x, output_y, output_z])
    output_link_angles = _flatten(cmds.getAttr(f"{node}.outputLinkAngles"))
    output_link_rotates = _flatten(cmds.getAttr(f"{node}.outputLinkRotates"))
    solved = bool(cmds.getAttr(f"{node}.solved"))
    return output_rotate, output_link_angles, output_link_rotates, solved


def _assert_close(actual: Any, expected: Any, label: str) -> None:
    """Fail with a compact diagnostic when snapshots differ."""
    actual_values = _flatten(actual)
    expected_values = _flatten(expected)
    if len(actual_values) != len(expected_values):
        raise RuntimeError(
            f"{label} length mismatch: expected {expected_values}, got {actual_values}"
        )
    if any(
        abs(actual_value - expected_value) > 1.0e-4
        for actual_value, expected_value in zip(actual_values, expected_values)
    ):
        raise RuntimeError(
            f"{label} stayed stale: expected {expected_values}, got {actual_values}"
        )


def main() -> int:
    """Run the focused standalone Maya regression."""
    import maya.cmds as cmds
    import maya.standalone

    plugin_path = _plugin_path()
    os.environ["PATH"] = str(plugin_path.parent) + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(plugin_path.parent))

    maya.standalone.initialize(name="python")
    try:
        cmds.loadPlugin(str(plugin_path), quiet=True)
        dirty_chain = {
            "bones": [
                {"parent_slot": -1, "rest_position": [0.0, 0.0, 0.0]},
                {"parent_slot": 0, "rest_position": [1.0, 0.0, 0.0]},
                {"parent_slot": 1, "rest_position": [1.0, 0.0, 0.0]},
            ],
            "links": [{"bone_slot": 0}, {"bone_slot": 1}],
            "targetBoneSlot": 2,
            "iterationCount": 40,
            "limitAngle": math.pi,
        }

        dirty_node = cmds.createNode("mmdCcdIk", name="ccdikGoalDirtySmoke")
        fresh_node = cmds.createNode("mmdCcdIk", name="ccdikGoalDirtyFreshSmoke")
        try:
            for node in (dirty_node, fresh_node):
                cmds.setAttr(f"{node}.chainJson", json.dumps(dirty_chain), type="string")
                cmds.setAttr(f"{node}.enabled", True)

            # FK target is (2, 0, 0), so this primes pass-through outputs and
            # solved=False before any goal child changes.
            cmds.setAttr(f"{dirty_node}.goal", 2.0, 0.0, 0.0, type="double3")
            before = _snapshot(cmds, dirty_node)
            if before[3] is not False:
                raise RuntimeError(f"expected initial solved=False, got {before[3]}")

            # Change exactly one compound child.  No dgdirty or output read is
            # used to force recomputation before the snapshot below.
            cmds.setAttr(f"{dirty_node}.goalY", 0.5)
            after = _snapshot(cmds, dirty_node)

            cmds.setAttr(f"{fresh_node}.goal", 2.0, 0.5, 0.0, type="double3")
            fresh = _snapshot(cmds, fresh_node)

            _assert_close(after[0], fresh[0], "outputRotate")
            _assert_close(after[1], fresh[1], "outputLinkAngles")
            _assert_close(after[2], fresh[2], "outputLinkRotates")
            if after[3] is not True or after[3] != fresh[3]:
                raise RuntimeError(f"solved stayed stale: expected {fresh[3]}, got {after[3]}")
            if before == after:
                raise RuntimeError("goalY edit did not change any observed output")
            print("OK: mmdCcdIk goal child dirty propagation refreshed all outputs")
        finally:
            cmds.delete(dirty_node, fresh_node)
    finally:
        maya.standalone.uninitialize()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
