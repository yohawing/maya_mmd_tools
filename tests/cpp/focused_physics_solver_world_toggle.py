"""Focused C++ solver regression for an unevaluated world OFF/ON cycle."""

from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "data" / "physics" / "test_hair_physics.pmx"


def main() -> int:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    import maya.cmds as cmds
    import maya.standalone

    from mmd_tools.core.mmd_parser import parse_pmx_file
    from tests.cpp.smoke_runtime_node import _find_plugin_path
    from tests.integration.test_physics_solver_node import (
        _create_joints_under_root,
        _store_pmx_payload,
    )

    plugin_path = _find_plugin_path()
    os.environ["PATH"] = str(plugin_path.parent) + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(plugin_path.parent))

    maya.standalone.initialize(name="python")
    try:
        cmds.loadPlugin(str(plugin_path), quiet=True)
        registered_nodes = cmds.pluginInfo(
            str(plugin_path), query=True, dependNode=True
        ) or []
        if "mmdPhysicsSolver" not in registered_nodes:
            raise RuntimeError(
                "C++ plugin did not register mmdPhysicsSolver; another plugin owns the type"
            )
        pmx_bytes = FIXTURE.read_bytes()
        pmx = parse_pmx_file(str(FIXTURE))
        root = cmds.group(empty=True, name="test_root")
        _create_joints_under_root(pmx.bones, root)
        _store_pmx_payload(root, pmx_bytes)

        solver = cmds.createNode("mmdPhysicsSolver", name="testSolver")
        cmds.connectAttr(f"{root}.message", f"{solver}.modelRoot")
        cmds.connectAttr("time1.outTime", f"{solver}.inTime")

        world = cmds.createNode("network", name="testWorldSettings")
        cmds.addAttr(world, longName="enable", attributeType="bool", defaultValue=True)
        cmds.addAttr(world, longName="resetGeneration", attributeType="long", defaultValue=0)
        cmds.addAttr(world, longName="outSettingsVersion", attributeType="long", defaultValue=0)
        cmds.connectAttr(f"{world}.message", f"{solver}.inWorldSettings")
        cmds.connectAttr(
            f"{world}.outSettingsVersion", f"{solver}.inWorldSettingsVersion"
        )

        cmds.currentUnit(time="ntsc")
        for frame in range(6):
            cmds.currentTime(frame)
            _ = cmds.getAttr(f"{solver}.outStatus")

        cmds.setAttr(f"{world}.enable", False)
        cmds.setAttr(f"{world}.outSettingsVersion", 1)
        cmds.setAttr(f"{world}.enable", True)
        cmds.setAttr(f"{world}.outSettingsVersion", 2)
        # Do not query the solver or a driven joint while the world is OFF.
        cmds.currentTime(6)
        status = cmds.getAttr(f"{solver}.outStatus")
        if status != "reset":
            raise RuntimeError(f"Expected reset after unevaluated OFF/ON, got {status!r}")
        print(f"OK: C++ solver reset after unevaluated OFF/ON ({plugin_path})")
        return 0
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
