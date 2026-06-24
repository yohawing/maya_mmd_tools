"""Probe live Rig IK bend direction in Maya.

The check intentionally verifies more than "the IK output changed".  A solver can
reach the controller while bending the knee through the wrong visual side, so the
probe checks both the TestModel knee's world bend side and local hinge sign.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else (ROOT / p).resolve()


def run_probe(pmx_path: Path, out_path: Path, threshold: float) -> int:
    from maya import cmds

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    try:
        cmds.loadPlugin(str(ROOT / "mmd_tools" / "plugin_main.py"), quiet=True)
    except Exception:
        # The Python MPx nodes may already be registered even if shader override
        # registration reports an error in mayapy.
        pass

    from mmd_tools.io.mmd_importer import import_mmd_file

    cmds.file(new=True, force=True)
    import_mmd_file(
        str(pmx_path),
        options={
            "setup_rig": True,
            "setup_bone_orientation": True,
            "import_physics": False,
            "create_mmd_shaders": False,
        },
    )

    node = "left_leg_ik_mmdCcdIk"
    controller = "left_leg_ik"
    knee = "left_knee"
    ankle = "left_ankle"

    rest_knee_world = cmds.xform(knee, q=True, ws=True, t=True)
    rest_ctrl = cmds.getAttr(f"{controller}.translate")[0]

    # Move the controller upward.  On this fixture the correct visual bend should
    # move the knee toward Maya +Z while keeping the left_knee hinge sign positive.
    cmds.setAttr(
        f"{controller}.translate",
        rest_ctrl[0],
        rest_ctrl[1] + 1.0,
        rest_ctrl[2],
        type="double3",
    )
    cmds.dgdirty(node)
    cmds.refresh(force=True)

    moved_knee_world = cmds.xform(knee, q=True, ws=True, t=True)
    moved_ankle_world = cmds.xform(ankle, q=True, ws=True, t=True)
    moved_ctrl_world = cmds.xform(controller, q=True, ws=True, t=True)
    ankle_distance = math.dist(moved_ankle_world, moved_ctrl_world)
    knee_delta_z = moved_knee_world[2] - rest_knee_world[2]
    knee_rotate = cmds.getAttr(f"{knee}.rotate")[0]

    # Neither check is sufficient alone: Z+ with a negative hinge sign and Z- with
    # a positive hinge sign are both visually wrong for this rig.
    knee_rotate_x = float(knee_rotate[0])
    passed = ankle_distance <= threshold and knee_delta_z > 0.0 and knee_rotate_x > 0.0
    lines = [
        "# IK Bend Direction Probe",
        "",
        f"- PMX: `{pmx_path}`",
        f"- node: `{node}`",
        f"- goalWorldMatrix: `{cmds.listConnections(f'{node}.goalWorldMatrix', s=True, d=False, plugs=True)}`",
        f"- rest knee world: `{tuple(round(v, 6) for v in rest_knee_world)}`",
        f"- moved knee world: `{tuple(round(v, 6) for v in moved_knee_world)}`",
        f"- knee delta Z: `{knee_delta_z:.6f}`",
        f"- knee rotate: `{tuple(round(v, 6) for v in knee_rotate)}`",
        f"- knee rotate X positive: `{knee_rotate_x > 0.0}`",
        f"- ankle/controller distance: `{ankle_distance:.6f}`",
        f"- threshold: `{threshold}`",
        f"- status: `{'passed' if passed else 'failed'}`",
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Report: {out_path}")
    print(f"Status: {'passed' if passed else 'failed'}")
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pmx", default="tests/data/mmt_test_model.pmx")
    parser.add_argument("--out", default="build/reports/ik_bend_direction_probe.md")
    parser.add_argument("--threshold", type=float, default=0.1)
    args = parser.parse_args()

    try:
        import maya.standalone

        maya.standalone.initialize(name="python")
    except Exception:
        pass

    return run_probe(_resolve(args.pmx), _resolve(args.out), args.threshold)


if __name__ == "__main__":
    raise SystemExit(main())
