"""Focused Maya smoke for mmdCcdIk chain cache/output coherence.

The probe reads every public output independently, changes only the pose, then
changes the chain shape and finally supplies malformed JSON.  The shape checks
make stale native/config state observable while the independent reads exercise
the single-compute path that writes all related outputs.
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


def _read_snapshot(
    cmds: Any, node: str
) -> tuple[list[float], list[float], list[float], bool, int, float]:
    """Read all public outputs without reading outputRotate's parent plug."""
    indices = cmds.getAttr(f"{node}.outputRotate", multiIndices=True) or []
    output_rotate: list[float] = []
    for index in sorted(int(value) for value in indices):
        for axis in ("X", "Y", "Z"):
            output_rotate.append(
                float(cmds.getAttr(f"{node}.outputRotate[{index}].outputRotateElement{axis}"))
            )
    output_angle = float(cmds.getAttr(f"{node}.outputAngle"))
    output_link_angles = _flatten(cmds.getAttr(f"{node}.outputLinkAngles"))
    output_link_rotates = _flatten(cmds.getAttr(f"{node}.outputLinkRotates"))
    solved = bool(cmds.getAttr(f"{node}.solved"))
    return output_rotate, output_link_angles, output_link_rotates, solved, len(indices), output_angle


def _chain(link_count: int) -> dict[str, Any]:
    """Build a deterministic no-bind-matrix chain with the requested links."""
    bones = [
        {"parent_slot": -1, "rest_position": [0.0, 0.0, 0.0]},
    ]
    for _ in range(link_count):
        bones.append({"parent_slot": len(bones) - 1, "rest_position": [1.0, 0.0, 0.0]})
    return {
        "bones": bones,
        "links": [{"bone_slot": index} for index in range(link_count)],
        "targetBoneSlot": link_count,
        "iterationCount": 40,
        "limitAngle": math.pi,
    }


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
        node = cmds.createNode("mmdCcdIk", name="focused_ccdik_cache")
        try:
            cmds.setAttr(f"{node}.enabled", True)
            chain_a = _chain(2)
            cmds.setAttr(f"{node}.chainJson", json.dumps(chain_a), type="string")
            cmds.setAttr(f"{node}.goal", 1.5, 0.75, 0.0, type="double3")

            # Read each output family independently.  A single compute must
            # populate all families, regardless of which one is requested first.
            first = _read_snapshot(cmds, node)
            if first[4] != 2 or len(first[1]) != 2 or len(first[2]) != 6 or abs(first[5]) > 1e-9:
                raise RuntimeError(f"unexpected initial output shape: {first}")
            if abs(first[0][0]) + abs(first[0][1]) + abs(first[0][2]) <= 0.0:
                raise RuntimeError("initial outputRotate was not populated")

            # A pose-only edit must reuse the cached config/chain while
            # producing a fresh, coherent solve for every public output.
            cmds.setAttr(f"{node}.goal", 1.25, 0.5, 0.0, type="double3")
            second = _read_snapshot(cmds, node)
            if second[4] != 2 or len(second[1]) != 2 or len(second[2]) != 6 or abs(second[5]) > 1e-9:
                raise RuntimeError(f"pose edit changed cached output shape: {second}")
            if first[:3] == second[:3]:
                raise RuntimeError("pose edit did not refresh any public output")

            # Changing chainJson must rebuild exactly the new shape, with no
            # stale elements left from the previous two-link configuration.
            chain_b = _chain(3)
            cmds.setAttr(f"{node}.chainJson", json.dumps(chain_b), type="string")
            rebuilt = _read_snapshot(cmds, node)
            if rebuilt[4] != 3 or len(rebuilt[1]) != 3 or len(rebuilt[2]) != 9 or abs(rebuilt[5]) > 1e-9:
                raise RuntimeError(f"chain config replacement was incoherent: {rebuilt}")

            # Malformed JSON invalidates the prior cache and must not expose
            # its link arrays on the fallback path.
            cmds.setAttr(f"{node}.chainJson", "{malformed", type="string")
            malformed = _read_snapshot(cmds, node)
            if malformed[1] or malformed[2] or malformed[4] != 1 or abs(malformed[5]) > 1e-9:
                raise RuntimeError(f"malformed config retained stale outputs: {malformed}")

            print("OK: mmdCcdIk chain cache and single-compute output coherence")
        finally:
            cmds.delete(node)
    finally:
        maya.standalone.uninitialize()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
