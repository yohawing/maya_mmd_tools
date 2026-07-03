"""Drag this file into Maya to install Maya MMD Tools.

Maya calls ``onMayaDroppedPythonFile`` when a Python file with that function is
dropped into the viewport.  The installer copies this package into the user's
Maya modules folder, writes a `.mod` file pointing at the installed copy, then
loads the Python plug-in for the current session.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Callable, Optional, Sequence, Set


MODULE_NAME = "maya_mmd_tools"
EXCLUDED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "build",
    "dist",
    "docs-dev",
    ".ai",
}
EXCLUDED_FILE_SUFFIXES = {".pyc", ".pyo"}
EXCLUDED_RELATIVE_DIRS = {
    Path("external") / "mmd-anim" / "target",
}


def _maya_version() -> str:
    from maya import cmds

    version = str(cmds.about(version=True))
    return version.split()[0]


def _module_dir() -> Path:
    from maya import cmds

    return Path(cmds.internalVar(userAppDir=True)) / "modules"


def _installed_root(module_dir: Path) -> Path:
    return module_dir / MODULE_NAME


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


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


def _discover_maya_versions(root: Path, current_version: Optional[str] = None) -> Sequence[str]:
    versions = set()
    plugins_dir = root / "plug-ins"
    if plugins_dir.exists():
        for child in plugins_dir.iterdir():
            if child.is_dir() and child.name.isdigit() and (child / "Release").is_dir():
                versions.add(child.name)

    if not versions and current_version:
        versions.add(current_version)

    return tuple(sorted(versions, key=lambda value: int(value) if value.isdigit() else value))


def _module_text(root: Path, maya_versions: Sequence[str], package_version: str) -> str:
    root_path = root.resolve().as_posix()
    blocks = []
    for maya_version in maya_versions:
        blocks.append(
            "\n".join(
                [
                    f"+ MAYAVERSION:{maya_version} maya_mmd_tools {package_version} {root_path}",
                    "scripts: .",
                    "plug-ins: plug-ins",
                    "icons: resources/icons",
                    "MMD_TOOLS_ROOT:= .",
                    "PYTHONPATH +:= .",
                ]
            )
        )
    return "\n\n".join(blocks) + "\n"


def _is_excluded_relative_dir(relative_path: Path) -> bool:
    return any(relative_path == excluded for excluded in EXCLUDED_RELATIVE_DIRS)


def _copy_ignore(source_root: Path, target_root: Path) -> Callable[[str, list], Set[str]]:
    def ignore(directory: str, names: list) -> Set[str]:
        ignored = set()
        directory_path = Path(directory)
        for name in names:
            path = directory_path / name
            resolved_path = path.resolve()
            try:
                relative_path = path.resolve().relative_to(source_root)
            except ValueError:
                relative_path = Path(name)

            if resolved_path == target_root or _is_relative_to(resolved_path, target_root):
                ignored.add(name)
            elif path.is_dir() and (name in EXCLUDED_DIR_NAMES or _is_excluded_relative_dir(relative_path)):
                ignored.add(name)
            elif path.is_file() and path.suffix in EXCLUDED_FILE_SUFFIXES:
                ignored.add(name)
        return ignored

    return ignore


def _verify_install_target(target: Path, module_dir: Path) -> None:
    resolved_module_dir = module_dir.resolve()
    resolved_target = target.resolve()
    if resolved_target.parent != resolved_module_dir or resolved_target.name != MODULE_NAME:
        raise RuntimeError(f"Refusing to remove unsafe install target: {resolved_target}")


def _remove_existing_install(target: Path, module_dir: Path) -> None:
    if not target.exists():
        return
    _verify_install_target(target, module_dir)
    for child in target.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(str(child))
        else:
            child.unlink()


def _copy_tree_contents(source_root: Path, target_root: Path) -> None:
    ignore = _copy_ignore(source_root, target_root)
    target_root.mkdir(parents=True, exist_ok=True)
    ignored = ignore(str(source_root), [child.name for child in source_root.iterdir()])
    for child in source_root.iterdir():
        if child.name in ignored:
            continue
        destination = target_root / child.name
        if child.is_dir() and not child.is_symlink():
            shutil.copytree(str(child), str(destination), ignore=ignore)
        else:
            shutil.copy2(str(child), str(destination))


def _copy_package(source_root: Path, module_dir: Path) -> Path:
    installed_root = _installed_root(module_dir).resolve()
    source_root = source_root.resolve()
    if source_root == installed_root:
        return installed_root

    _remove_existing_install(installed_root, module_dir)
    _copy_tree_contents(source_root, installed_root)
    return installed_root


def install(root: Optional[Path] = None) -> Path:
    """Copy the package and install the module file for bundled Maya versions."""
    source_root = (root or Path(__file__).resolve().parent).resolve()
    module_dir = _module_dir()
    module_dir.mkdir(parents=True, exist_ok=True)
    installed_root = _copy_package(source_root, module_dir)
    maya_versions = _discover_maya_versions(installed_root, _maya_version())
    mod_path = module_dir / "maya_mmd_tools.mod"
    mod_path.write_text(
        _module_text(installed_root, maya_versions, _package_version(installed_root)),
        encoding="utf-8",
    )

    installed_root_path = str(installed_root)
    if installed_root_path not in sys.path:
        sys.path.insert(0, installed_root_path)

    from maya import cmds

    plugin_path = installed_root / "plug-ins" / "mmd_tools_plugin.py"

    def _load_plugin():
        try:
            # Maya only reads .mod files (and populates MAYA_PLUG_IN_PATH) at
            # startup, so the freshly written module is not on the plug-in path
            # yet. Load by absolute path to work in the current session.
            try:
                already_loaded = bool(
                    cmds.pluginInfo("mmd_tools_plugin.py", query=True, loaded=True)
                )
            except Exception:
                already_loaded = False
            if not already_loaded:
                cmds.loadPlugin(str(plugin_path))
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

    installed_root = _installed_root(mod_path.parent)
    maya_versions = _discover_maya_versions(installed_root, _maya_version())
    cmds.confirmDialog(
        title="Maya MMD Tools",
        message=(
            "Installed Maya MMD Tools.\n\n"
            f"Copied files to:\n{installed_root}\n\n"
            f"Created module file:\n{mod_path}\n\n"
            f"Enabled Maya versions: {', '.join(maya_versions)}\n\n"
            "Restart Maya if the menu is not visible."
        ),
        button=["OK"],
        defaultButton="OK",
    )


if __name__ == "__main__":
    print(install())
