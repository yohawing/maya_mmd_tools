"""Focused regression for native mmdCcdIk local-axis solver dispatch.

The C++ node must dispatch a chain containing local-axis descriptors through
``mmd_runtime_ik_chain_create_v2``.  The v1 primitive silently ignores those
descriptors and produces a materially different limited-link solve.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


def _plugin_path() -> Path:
    """Resolve the C++ plugin for the selected Maya/config pair."""
    explicit = os.environ.get("MMD_TOOLS_CPP_PLUGIN")
    if explicit:
        path = Path(explicit)
        if path.is_file():
            return path
        raise FileNotFoundError(path)

    version = os.environ.get("MAYA_VERSION", "2024")
    config = os.environ.get("MMD_TOOLS_CPP_CONFIG", "Debug")
    path = ROOT / "plug-ins" / version / config / "mmd_tools_cpp.mll"
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _chain() -> dict[str, Any]:
    """Return a deterministic limited chain where v1 and v2 diverge."""
    axis_x = (
        (-0.0875502040693235, -0.9961601085003453, 0.0),
        (-0.7971650150554019, -0.6037614916270503, 0.0),
        (0.7942128170326055, -0.607639696910211, 0.0),
    )
    bones: list[dict[str, Any]] = [{"parent_slot": -1, "rest_position": [0.0, 0.0, 0.0]}]
    for index, x_axis in enumerate(axis_x, start=1):
        bones.append(
            {
                "parent_slot": index - 1,
                "rest_position": [0.0, 1.0, 0.0],
                "local_axis": {"x": list(x_axis), "z": [0.0, 0.0, 1.0]},
            }
        )
    limit_min = [-1.0, -0.5, -0.2]
    limit_max = [0.5, 0.7, 0.3]
    links = [
        {
            "bone_slot": 2,
            "has_angle_limit": True,
            "angle_limit_min": limit_min,
            "angle_limit_max": limit_max,
        },
        {
            "bone_slot": 1,
            "has_angle_limit": True,
            "angle_limit_min": limit_min,
            "angle_limit_max": limit_max,
        },
    ]
    return {
        "bones": bones,
        "links": links,
        "targetBoneSlot": 3,
        "iterationCount": 60,
        "limitAngle": 2.0,
    }


def _expected_euler(chain: dict[str, Any], goal: list[float], use_local_axis: bool) -> list[float]:
    """Solve through the runtime wrapper and convert MMD quaternions to Maya Euler."""
    from mmd_tools.core.native.mmd_anim_runtime import MmdIkChain
    import maya.api.OpenMaya as om

    bones = chain["bones"] if use_local_axis else [dict(bone, local_axis=None) for bone in chain["bones"]]
    solver = MmdIkChain.create(
        bones=bones,
        target_bone_slot=chain["targetBoneSlot"],
        links=chain["links"],
        iteration_count=chain["iterationCount"],
        limit_angle=chain["limitAngle"],
    )
    if solver is None:
        raise RuntimeError("MmdIkChain.create failed")
    try:
        rotations = [0.0, 0.0, 0.0, 1.0] * len(bones)
        positions = [0.0] * (len(bones) * 3)
        output, _stats = solver.solve(positions=positions, rotations=rotations, goal=goal)
    finally:
        solver.free()

    values: list[float] = []
    for index in range(len(chain["links"])):
        offset = index * 4
        quaternion = om.MQuaternion(
            -output[offset],
            -output[offset + 1],
            output[offset + 2],
            output[offset + 3],
        )
        euler = quaternion.asEulerRotation()
        values.extend(math.degrees(value) for value in (euler.x, euler.y, euler.z))
    return values


def _output(cmds: Any, node: str) -> list[float]:
    """Read outputRotate children in link order."""
    values: list[float] = []
    for index in range(2):
        for axis in "XYZ":
            values.append(
                float(cmds.getAttr(f"{node}.outputRotate[{index}].outputRotateElement{axis}"))
            )
    return values


def main() -> int:
    """Run the standalone local-axis dispatch regression."""
    import maya.cmds as cmds
    import maya.standalone

    plugin_path = _plugin_path()
    os.environ["PATH"] = str(plugin_path.parent) + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(plugin_path.parent))

    maya.standalone.initialize(name="python")
    try:
        cmds.loadPlugin(str(plugin_path), quiet=True)
        chain = _chain()
        goal_mmd = [-1.380110916790359, 0.19954528703876973, -0.39363594205970065]
        # The public ``goal`` plug is Maya-space; the native primitive uses
        # MMD-space with the Z axis reflected.
        goal_maya = [goal_mmd[0], goal_mmd[1], -goal_mmd[2]]
        expected_v2 = _expected_euler(chain, goal_mmd, use_local_axis=True)
        expected_v1 = _expected_euler(chain, goal_mmd, use_local_axis=False)
        v1_v2_delta = max(abs(lhs - rhs) for lhs, rhs in zip(expected_v1, expected_v2))
        if v1_v2_delta <= 1.0e-2:
            raise RuntimeError(f"fixture does not distinguish v1/v2: delta={v1_v2_delta:.8f}")

        node = cmds.createNode("mmdCcdIk", name="focused_ccdik_local_axis_parity")
        try:
            cmds.setAttr(f"{node}.chainJson", json.dumps(chain), type="string")
            cmds.setAttr(f"{node}.enabled", True)
            cmds.setAttr(f"{node}.goal", *goal_maya, type="double3")
            actual = _output(cmds, node)
            max_error = max(abs(lhs - rhs) for lhs, rhs in zip(actual, expected_v2))
            if max_error > 1.0e-4:
                raise RuntimeError(
                    "mmdCcdIk local-axis output did not use v2 solver: "
                    f"max_error={max_error:.8f}, actual={actual}, expected_v2={expected_v2}, "
                    f"expected_v1={expected_v1}"
                )
            print(
                "OK: mmdCcdIk local-axis v2 dispatch parity "
                f"(max_error={max_error:.8f}, v1_v2_delta={v1_v2_delta:.8f})"
            )
        finally:
            cmds.delete(node)
    finally:
        maya.standalone.uninitialize()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
