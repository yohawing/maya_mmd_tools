"""Focused regression for native mmdCcdIk local-axis solver dispatch.

The C++ node must dispatch local-axis descriptors through
``mmd_runtime_ik_chain_create_v2``. The v1 primitive ignores them and yields a
materially different limited-link solve.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


def _plugin_path() -> Path:
    """Resolve the selected C++ plugin build."""
    explicit = os.environ.get("MMD_TOOLS_CPP_PLUGIN")
    if explicit:
        path = Path(explicit)
        if path.is_file():
            return path
        raise FileNotFoundError(path)
    return ROOT / "plug-ins" / os.environ.get("MAYA_VERSION", "2024") / os.environ.get(
        "MMD_TOOLS_CPP_CONFIG", "Debug"
    ) / "mmd_tools_cpp.mll"


def _chain() -> dict[str, Any]:
    """Return a limited chain where v1 and v2 local-axis solves diverge."""
    axes = (
        (-0.0875502040693235, -0.9961601085003453, 0.0),
        (-0.7971650150554019, -0.6037614916270503, 0.0),
        (0.7942128170326055, -0.607639696910211, 0.0),
    )
    bones = [{"parent_slot": -1, "rest_position": [0.0, 0.0, 0.0]}]
    for index, axis_x in enumerate(axes, start=1):
        bones.append(
            {
                "parent_slot": index - 1,
                "rest_position": [0.0, 1.0, 0.0],
                "local_axis": {"x": list(axis_x), "z": [0.0, 0.0, 1.0]},
            }
        )
    limit_min = [-1.0, -0.5, -0.2]
    limit_max = [0.5, 0.7, 0.3]
    return {
        "bones": bones,
        "links": [
            {"bone_slot": 2, "has_angle_limit": True, "angle_limit_min": limit_min, "angle_limit_max": limit_max},
            {"bone_slot": 1, "has_angle_limit": True, "angle_limit_min": limit_min, "angle_limit_max": limit_max},
        ],
        "targetBoneSlot": 3,
        "iterationCount": 60,
        "limitAngle": 2.0,
    }


def _expected_euler(chain: dict[str, Any], goal: list[float], *, use_local_axis: bool) -> list[float]:
    """Solve through the runtime wrapper and return Maya Euler output."""
    import maya.api.OpenMaya as om

    from mmd_tools.core.native.mmd_anim_runtime import MmdIkChain

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
        output, _stats = solver.solve(
            positions=[0.0] * (len(bones) * 3),
            rotations=[0.0, 0.0, 0.0, 1.0] * len(bones),
            goal=goal,
        )
    finally:
        solver.free()

    values = []
    for index in range(len(chain["links"])):
        offset = index * 4
        quat = om.MQuaternion(-output[offset], -output[offset + 1], output[offset + 2], output[offset + 3])
        euler = quat.asEulerRotation()
        values.extend(math.degrees(value) for value in (euler.x, euler.y, euler.z))
    return values


def _output(cmds: Any, node: str) -> list[float]:
    """Read outputRotate in link order."""
    return [
        float(cmds.getAttr(f"{node}.outputRotate[{index}].outputRotateElement{axis}"))
        for index in range(2)
        for axis in "XYZ"
    ]


def main() -> int:
    """Run the standalone local-axis v2 dispatch regression."""
    import maya.cmds as cmds
    import maya.standalone

    plugin_path = _plugin_path()
    if not plugin_path.is_file():
        raise FileNotFoundError(plugin_path)
    os.environ["PATH"] = str(plugin_path.parent) + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(plugin_path.parent))

    maya.standalone.initialize(name="python")
    try:
        cmds.loadPlugin(str(plugin_path), quiet=True)
        chain = _chain()
        goal_mmd = [-1.380110916790359, 0.19954528703876973, -0.39363594205970065]
        expected_v2 = _expected_euler(chain, goal_mmd, use_local_axis=True)
        expected_v1 = _expected_euler(chain, goal_mmd, use_local_axis=False)
        delta = max(abs(lhs - rhs) for lhs, rhs in zip(expected_v1, expected_v2))
        if delta <= 1.0e-2:
            raise RuntimeError(f"fixture does not distinguish v1/v2: delta={delta:.8f}")
        node = cmds.createNode("mmdCcdIk", name="focused_ccdik_local_axis_parity")
        try:
            cmds.setAttr(f"{node}.chainJson", json.dumps(chain), type="string")
            cmds.setAttr(f"{node}.enabled", True)
            cmds.setAttr(f"{node}.goal", goal_mmd[0], goal_mmd[1], -goal_mmd[2], type="double3")
            actual = _output(cmds, node)
            max_error = max(abs(lhs - rhs) for lhs, rhs in zip(actual, expected_v2))
            if max_error > 1.0e-4:
                raise RuntimeError(
                    f"mmdCcdIk local-axis output did not use v2 solver: max_error={max_error:.8f}; "
                    f"actual={actual}; expected_v2={expected_v2}; expected_v1={expected_v1}"
                )

            # VMD export/reimport may move an Euler channel by less than one
            # microdegree.  Both values must enter native CCD as the same
            # canonical float pose instead of selecting divergent iterations.
            source_degrees = (0.35377899, -7.01622930, 6.63296383)
            fresh_degrees = (0.35377896, -7.01622999, 6.63296390)
            cmds.setAttr(f"{node}.inputRotate[1]", *source_degrees, type="double3")
            source_output = _output(cmds, node)
            cmds.setAttr(f"{node}.inputRotate[1]", *fresh_degrees, type="double3")
            fresh_output = _output(cmds, node)
            stability_error = max(
                abs(lhs - rhs) for lhs, rhs in zip(source_output, fresh_output)
            )
            if stability_error > 1.0e-8:
                raise RuntimeError(
                    "sub-microdegree input noise changed native CCD output: "
                    f"max_error={stability_error:.12f}; "
                    f"source={source_output}; fresh={fresh_output}"
                )

            # Position channels and the goal also cross the double-to-float
            # boundary.  Keep two one-ULP-scale variants just inside the same
            # 2e-6 quantization cells and require bit-stable solver output.
            # These Dorothy frame-90 witnesses straddle the narrower 1e-6
            # boundary, so the regression also locks the chosen grid size.
            source_translate = (3.916799545288086, 1.7541426420211792, -4.164727210998535)
            fresh_translate = (3.9167994260787964, 1.7541426420211792, -4.164727210998535)
            source_goal = (1.3019449673220151, 1.7247169986367226, -6.0536792278289795)
            fresh_goal = (1.3019448518753052, 1.7247169986367226, -6.053679287433624)
            cmds.setAttr(f"{node}.inputTranslate[1]", *source_translate, type="double3")
            cmds.setAttr(f"{node}.goal", *source_goal, type="double3")
            source_position_output = _output(cmds, node)
            cmds.setAttr(f"{node}.inputTranslate[1]", *fresh_translate, type="double3")
            cmds.setAttr(f"{node}.goal", *fresh_goal, type="double3")
            fresh_position_output = _output(cmds, node)
            position_stability_error = max(
                abs(lhs - rhs)
                for lhs, rhs in zip(source_position_output, fresh_position_output)
            )
            if position_stability_error > 1.0e-8:
                raise RuntimeError(
                    "one-ULP position/goal noise changed native CCD output: "
                    f"max_error={position_stability_error:.12f}; "
                    f"source={source_position_output}; fresh={fresh_position_output}"
                )

            cmds.setAttr(f"{node}.inputRotate[2]", 12.0, -7.0, 3.0, type="double3")
            cmds.setAttr(f"{node}.inputRotate[1]", -4.0, 9.0, 6.0, type="double3")
            cmds.setAttr(f"{node}.enabled", False)
            disabled_quaternions = cmds.getAttr(f"{node}.outputMmdLinkQuaternions") or []
            if len(disabled_quaternions) != len(chain["links"]) * 4:
                raise RuntimeError(
                    "disabled mmdCcdIk did not preserve raw MMD link quaternions: "
                    f"values={disabled_quaternions}"
                )
            print(
                "OK: mmdCcdIk local-axis v2 dispatch parity "
                f"(max_error={max_error:.8f}, v1_v2_delta={delta:.8f}, "
                f"stability_error={stability_error:.12f}, "
                f"position_stability_error={position_stability_error:.12f})"
            )
        finally:
            cmds.delete(node)
    finally:
        maya.standalone.uninitialize()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
