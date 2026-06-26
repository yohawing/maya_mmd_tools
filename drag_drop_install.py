"""Drag this file into Maya to install Maya MMD Tools.

Maya calls ``onMayaDroppedPythonFile`` when a Python file with that function is
dropped into the viewport.  The installer writes a `.mod` file pointing at this
folder, then loads the Python plug-in for the current session.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional


def _maya_version() -> str:
    from maya import cmds

    version = str(cmds.about(version=True))
    return version.split()[0]


def _module_dir() -> Path:
    from maya import cmds

    return Path(cmds.internalVar(userAppDir=True)) / "modules"


def _package_version(root: Path) -> str:
    package_root = str(root.resolve())
    added_path = False
    if package_root not in sys.path:
        sys.path.insert(0, package_root)
        added_path = True
    try:
        from mmd_tools import __version__

        return __version__
    finally:
        if added_path:
            try:
                sys.path.remove(package_root)
            except ValueError:
                pass


def _module_text(root: Path, maya_version: str, package_version: str) -> str:
    root_path = root.resolve().as_posix()
    return "\n".join(
        [
            f"+ MAYAVERSION:{maya_version} maya_mmd_tools {package_version} {root_path}",
            "scripts: .",
            "plug-ins: plug-ins",
            "icons: resources/icons",
            "MMD_TOOLS_ROOT:= .",
            "PYTHONPATH +:= .",
            "",
        ]
    )


def install(root: Optional[Path] = None) -> Path:
    """Install the module file for the current Maya version."""
    root = (root or Path(__file__).resolve().parent).resolve()
    module_dir = _module_dir()
    module_dir.mkdir(parents=True, exist_ok=True)
    mod_path = module_dir / "maya_mmd_tools.mod"
    mod_path.write_text(_module_text(root, _maya_version(), _package_version(root)), encoding="utf-8")

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from maya import cmds

    def _load_plugin():
        try:
            if not cmds.pluginInfo("mmd_tools_plugin.py", query=True, loaded=True):
                cmds.loadPlugin("mmd_tools_plugin.py")
            from mmd_tools.plugin_main import install_mmd_menu

            install_mmd_menu()
            cmds.inViewMessage(
                amg="Maya MMD Tools installed. Restart Maya if the menu does not appear.",
                pos="midCenter",
                fade=True,
            )
        except Exception as exc:
            cmds.warning(f"Maya MMD Tools install completed, but plug-in load failed: {exc}")

    cmds.evalDeferred(_load_plugin)
    return mod_path


def onMayaDroppedPythonFile(*_args):
    """Maya viewport drop entry point."""
    mod_path = install()
    from maya import cmds

    cmds.confirmDialog(
        title="Maya MMD Tools",
        message=f"Installed Maya MMD Tools module:\n{mod_path}\n\nRestart Maya if the menu is not visible.",
        button=["OK"],
        defaultButton="OK",
    )


if __name__ == "__main__":
    print(install())
