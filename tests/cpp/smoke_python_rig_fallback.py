"""Smoke test for the Python rig-node fallback path in Maya standalone.

This script intentionally does not load ``mmd_tools_cpp``.  It loads the
Python Maya plug-in, imports a real PMX through the normal importer, and
verifies that rig construction falls back to the Python ``mmdCcdIk`` /
``mmdAppend`` node types.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "tests" / "data" / "mmt_test_model.pmx"
PYTHON_PLUGIN = ROOT / "mmd_tools" / "plugin_main.py"


def _plugin_loaded(plugin_fragment: str) -> bool:
    import maya.cmds as cmds

    loaded = cmds.pluginInfo(query=True, listPlugins=True) or []
    return any(plugin_fragment in str(name) for name in loaded)


def _ls_type_if_registered(cmds, node_type: str) -> list[str]:
    if node_type not in (cmds.allNodeTypes() or []):
        return []
    return cmds.ls(type=node_type) or []


def main() -> int:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    import maya.cmds as cmds
    import maya.standalone

    maya.standalone.initialize(name="python")
    try:
        if _plugin_loaded("mmd_tools_cpp"):
            raise RuntimeError("mmd_tools_cpp must not be loaded for the Python fallback smoke")

        os.environ["MMD_TOOLS_SKIP_SHADER_OVERRIDE"] = "1"
        cmds.loadPlugin(str(PYTHON_PLUGIN), quiet=True)
        if not _plugin_loaded("plugin_main"):
            raise RuntimeError(f"failed to load Python plugin: {PYTHON_PLUGIN}")
        if _plugin_loaded("mmd_tools_cpp"):
            raise RuntimeError("loading the Python plugin unexpectedly loaded mmd_tools_cpp")

        from mmd_tools.converters.rig_converter import RigConverter
        from mmd_tools.core import settings
        from mmd_tools.io.mmd_importer import import_mmd_file

        if settings.get("import.native.use_cpp_rig_nodes", False):
            raise RuntimeError("Python fallback smoke expects use_cpp_rig_nodes=False by default")
        rig_converter = RigConverter()
        if rig_converter._append_node_type() != "mmdAppend":
            raise RuntimeError(
                "RigConverter should use Python append nodes without mmd_tools_cpp, "
                f"got {rig_converter._append_node_type()}"
            )
        if rig_converter._ccd_ik_node_type() != "mmdCcdIk":
            raise RuntimeError(
                "RigConverter should use Python IK nodes without mmd_tools_cpp, "
                f"got {rig_converter._ccd_ik_node_type()}"
            )

        root = import_mmd_file(
            str(MODEL),
            options={
                "setup_rig": True,
                "setup_bone_orientation": True,
                "use_cpp_fast_load": False,
                "import_physics": False,
                "auto_resolve_textures": False,
            },
        )
        if not root or not cmds.objExists(root):
            raise RuntimeError(f"real-model Python fallback import failed: {root!r}")

        ik_nodes = cmds.ls(type="mmdCcdIk") or []
        append_nodes = cmds.ls(type="mmdAppend") or []
        if not ik_nodes:
            raise RuntimeError("Python fallback import did not create any mmdCcdIk nodes")
        if not append_nodes:
            raise RuntimeError("Python fallback import did not create any mmdAppend nodes")
        if _ls_type_if_registered(cmds, "mmdCcdIkNode") or _ls_type_if_registered(cmds, "mmdAppendNode"):
            raise RuntimeError("Python fallback import created C++ prototype rig nodes")

        multi_link_nodes = []
        for node in ik_nodes:
            chain = json.loads(cmds.getAttr(f"{node}.chainJson") or "{}")
            if len(chain.get("links") or []) >= 2:
                multi_link_nodes.append(node)
        if not multi_link_nodes:
            raise RuntimeError(f"Python fallback import created no multi-link IK nodes: {ik_nodes}")

        print(
            "OK: Python fallback rig import created Python rig nodes "
            f"({len(ik_nodes)} IK, {len(append_nodes)} append; multi-link={multi_link_nodes})"
        )
        return 0
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
