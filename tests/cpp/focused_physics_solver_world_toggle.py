"""Verify that world OFF/ON resets the Python-owned solver after the C++ plug-in loads."""

from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "data" / "physics" / "test_hair_physics.pmx"
PYTHON_PLUGIN = ROOT / "mmd_tools" / "plugin_main.py"


def main() -> int:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    import maya.cmds as cmds
    import maya.standalone

    from mmd_tools.core.mmd_parser import parse_pmx_file
    from tests.cpp.smoke_runtime_node import _find_plugin_path
    from mmd_tools.io.pmx_importer import import_pmx_file

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
        if "mmdPhysicsSolver" in registered_nodes:
            raise RuntimeError(
                "C++ plugin must not register the Python-owned mmdPhysicsSolver"
            )
        os.environ["MMD_TOOLS_SKIP_SHADER_OVERRIDE"] = "1"
        cmds.loadPlugin(str(PYTHON_PLUGIN), quiet=True)
        pmx = parse_pmx_file(str(FIXTURE))
        root = import_pmx_file(
            pmx,
            str(FIXTURE),
            options={"import_physics": True, "create_mmd_shaders": False},
        )
        solver_nodes = cmds.ls(type="mmdPhysicsSolver") or []
        world_nodes = cmds.ls(type="mmdPhysicsWorldShape") or []
        if len(solver_nodes) != 1 or len(world_nodes) != 1:
            raise RuntimeError(f"unexpected physics graph: solvers={solver_nodes}, worlds={world_nodes}")
        solver = solver_nodes[0]
        world = world_nodes[0]
        cmds.setAttr(f"{world}.enable", True)

        cmds.currentUnit(time="ntsc")
        for frame in range(6):
            cmds.currentTime(frame)
            _ = cmds.getAttr(f"{solver}.outStatus")

        cmds.setAttr(f"{world}.enable", False)
        cmds.setAttr(f"{world}.enable", True)
        # Do not query the solver or a driven joint while the world is OFF.
        status = cmds.getAttr(f"{solver}.outStatus")
        if status not in {"reset", "pose-updated"}:
            raise RuntimeError(
                "Expected reset before the next forward step after unevaluated "
                f"OFF/ON, got {status!r}"
            )

        cmds.currentTime(6)
        next_status = cmds.getAttr(f"{solver}.outStatus")
        if next_status != "stepped":
            raise RuntimeError(
                f"Expected stepped after the OFF/ON reset, got {next_status!r}"
            )
        print(
            "OK: Python-owned solver reset before its next forward step after unevaluated "
            f"OFF/ON ({plugin_path}; reset_status={status})"
        )
        return 0
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
