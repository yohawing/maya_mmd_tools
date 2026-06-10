"""Smoke test for loading the C++ runtime node in Maya.

This script intentionally has no pytest dependency. It is launched by mayapy
from Nox or by hand, initializes Maya standalone, loads the compiled plugin,
and verifies that the mmdRuntimeInstance node can be created.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
NODE_TYPE = "mmdRuntimeInstance"


def _candidate_plugin_paths() -> list[Path]:
    """Return possible C++ plugin artifact paths."""
    explicit = os.environ.get("MMD_TOOLS_CPP_PLUGIN")
    if explicit:
        return [Path(explicit)]

    version = os.environ.get("MAYA_VERSION", "2024")
    config = os.environ.get("MMD_TOOLS_CPP_CONFIG", "Debug")
    extensions = [".mll", ".bundle", ".so"]
    configs = [config]
    if config != "Release":
        configs.append("Release")
    if config != "Debug":
        configs.append("Debug")

    paths: list[Path] = []
    for cfg in configs:
        for suffix in extensions:
            paths.append(ROOT / "plug-ins" / version / cfg / f"mmd_tools_cpp{suffix}")
    return paths


def _find_plugin_path() -> Path:
    """Find the compiled C++ plugin artifact."""
    for path in _candidate_plugin_paths():
        if path.exists():
            return path

    candidates = "\n".join(str(path) for path in _candidate_plugin_paths())
    raise FileNotFoundError(f"mmd_tools_cpp plugin was not found. Checked:\n{candidates}")


def main() -> int:
    """Run the Maya standalone smoke check."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    import maya.cmds as cmds
    import maya.standalone

    plugin_path = _find_plugin_path()
    os.environ["PATH"] = str(plugin_path.parent) + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(plugin_path.parent))

    maya.standalone.initialize(name="python")
    try:
        cmds.loadPlugin(str(plugin_path), quiet=True)
        node = cmds.createNode(NODE_TYPE)
        if not cmds.objExists(node):
            raise RuntimeError(f"Failed to create node: {NODE_TYPE}")

        for attr in ("time", "pmxData", "vmdData", "worldMatrices", "morphWeights", "ikEnabled"):
            if not cmds.attributeQuery(attr, node=node, exists=True):
                raise RuntimeError(f"Missing attribute {attr!r} on {node}")

        print(f"OK: loaded {plugin_path}")
        print(f"OK: created {node} ({NODE_TYPE})")
        return 0
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
