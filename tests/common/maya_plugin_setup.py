"""Shared production-plugin setup for mayapy release-gate runners."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable


REQUIRED_MMD_NODE_TYPES = (
    "mmdMorphController",
    "mmdAppend",
    "mmdCcdIk",
    "mmdPhysicsWorldShape",
    "mmdRigidBodyShape",
    "mmdPhysicsJointShape",
    "mmdPhysicsSolver",
    "mmdPhysicsBoneDriver",
)


def _canonical_loaded_plugin(cmds_module: Any, plugin_path: Path) -> str | None:
    """Return the loaded plugin name whose resolved path is canonical."""
    for name in cmds_module.pluginInfo(query=True, listPlugins=True) or []:
        try:
            if not cmds_module.pluginInfo(name, query=True, loaded=True):
                continue
            loaded_path = Path(
                cmds_module.pluginInfo(name, query=True, path=True)
            ).resolve()
        except Exception:
            continue
        if loaded_path == plugin_path:
            return str(name)
    return None


def load_mmd_tools_plugin(
    repo_root: str | Path,
    *,
    required_node_types: Iterable[str] = REQUIRED_MMD_NODE_TYPES,
    cmds_module: Any = None,
) -> Path:
    """Load the canonical plugin entrypoint and verify required node types."""
    if cmds_module is None:
        from maya import cmds as cmds_module

    plugin_path = (Path(repo_root) / "mmd_tools" / "plugin_main.py").resolve()
    if not plugin_path.is_file():
        raise RuntimeError(f"mmd_tools plugin entrypoint not found: {plugin_path}")

    plugin_name = _canonical_loaded_plugin(cmds_module, plugin_path)
    if plugin_name is None:
        cmds_module.loadPlugin(str(plugin_path), quiet=True)
        plugin_name = _canonical_loaded_plugin(cmds_module, plugin_path)
    if plugin_name is None:
        raise RuntimeError(f"mmd_tools plugin did not remain loaded: {plugin_path}")

    registered = set(cmds_module.allNodeTypes() or [])
    missing = sorted(set(required_node_types) - registered)
    if missing:
        raise RuntimeError(
            "mmd_tools plugin registration incomplete; "
            f"missing node types: {', '.join(missing)}; plugin: {plugin_path}"
        )
    return plugin_path
