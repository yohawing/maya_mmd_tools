"""Run the native mmdAppend foot-D bind/JO/skin regression in mayapy.

The regular integration runner loads the Python fallback node first.  This
focused entry point loads the compiled C++ plug-in explicitly so the native
``sourceMmdLinkQuaternions`` bridge and bind-space matrices are exercised.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _plugin_path() -> Path:
    explicit = os.environ.get("MMD_TOOLS_CPP_PLUGIN")
    if explicit:
        return Path(explicit)
    version = os.environ.get("MAYA_VERSION", "2024")
    config = os.environ.get("MMD_TOOLS_CPP_CONFIG", "Debug")
    return ROOT / "plug-ins" / version / config / "mmd_tools_cpp.mll"


def main() -> int:
    """Initialize Maya, load the C++ node plug-in, and run one regression."""
    import maya.standalone

    maya.standalone.initialize(name="python")
    try:
        import maya.cmds as cmds

        plugin = _plugin_path()
        os.environ.setdefault("MAYA_SKIP_USERSETUP_PY", "1")
        os.environ["PATH"] = str(plugin.parent) + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(plugin.parent))
        cmds.loadPlugin(str(plugin), quiet=True)

        from tests.integration.test_append_ik_jo_space import TestMmdAppendJointOrient

        suite = unittest.TestSuite(
            [
                TestMmdAppendJointOrient(
                    "test_native_foot_d_quaternion_bind_and_jo_aware_skin_contract"
                )
            ]
        )
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    sys.exit(main())
