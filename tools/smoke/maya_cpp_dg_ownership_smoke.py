"""Verify normal startup gives native DG nodes to the C++ plug-in.

The smoke executes the production ``userSetup.py`` setup function inside Maya
standalone, records the plug-in owner of each shared DG node type, and checks
that scene serialization plus plug-in reload preserve that ownership.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import sys


ROOT = Path(__file__).resolve().parents[2]
NODE_TYPES = ("mmdAppend", "mmdCcdIk", "mmdPhysicsBoneDriver")


def _run_startup_setup(cmds) -> None:
    """Run the production setup without leaving its deferred timer queued."""
    original_eval_deferred = cmds.evalDeferred
    try:
        cmds.evalDeferred = lambda *_args, **_kwargs: None
        namespace = runpy.run_path(str(ROOT / "userSetup.py"))
    finally:
        cmds.evalDeferred = original_eval_deferred
    namespace["mmd_tools_setup"]()


def _ownership_report(cmds) -> dict[str, dict[str, str]]:
    """Return one plug-in owner and resolved path for every required node."""
    owners: dict[str, list[dict[str, str]]] = {node_type: [] for node_type in NODE_TYPES}
    for plugin in cmds.pluginInfo(query=True, listPlugins=True) or []:
        registered = cmds.pluginInfo(plugin, query=True, dependNode=True) or []
        for node_type in NODE_TYPES:
            if node_type in registered:
                owners[node_type].append(
                    {
                        "plugin": str(plugin),
                        "path": str(cmds.pluginInfo(plugin, query=True, path=True)),
                    }
                )

    report: dict[str, dict[str, str]] = {}
    for node_type, matches in owners.items():
        if len(matches) != 1:
            raise RuntimeError(
                f"{node_type} must have exactly one plug-in owner, got {matches}"
            )
        owner = matches[0]
        if not owner["plugin"].startswith("mmd_tools_cpp"):
            raise RuntimeError(f"{node_type} is not C++-owned: {owner}")
        report[node_type] = owner
    return report


def _verify_scene_roundtrip(cmds, scene_path: Path) -> None:
    """Save and reopen one scene containing all shared node types."""
    node_names = {
        node_type: cmds.createNode(node_type, name=f"ownership_{node_type}")
        for node_type in NODE_TYPES
    }
    scene_path.parent.mkdir(parents=True, exist_ok=True)
    cmds.file(rename=str(scene_path))
    cmds.file(save=True, type="mayaAscii", force=True)
    cmds.file(new=True, force=True)
    cmds.file(str(scene_path), open=True, force=True)
    for node_type, node in node_names.items():
        if not cmds.objExists(node) or cmds.nodeType(node) != node_type:
            raise RuntimeError(f"scene roundtrip lost {node_type}: {node}")


def _verify_undo_redo(cmds) -> None:
    """Verify Maya can undo and redo creation of every shared node type."""
    cmds.file(new=True, force=True)
    for node_type in NODE_TYPES:
        node = cmds.createNode(node_type, name=f"undo_{node_type}")
        cmds.undo()
        if cmds.objExists(node):
            raise RuntimeError(f"undo did not remove {node_type}: {node}")
        cmds.redo()
        if not cmds.objExists(node) or cmds.nodeType(node) != node_type:
            raise RuntimeError(f"redo did not restore {node_type}: {node}")
        cmds.delete(node)


def _plugin_for_path_fragment(cmds, fragment: str) -> str:
    for plugin in cmds.pluginInfo(query=True, listPlugins=True) or []:
        path = str(cmds.pluginInfo(plugin, query=True, path=True))
        if fragment in path.replace("\\", "/"):
            return str(plugin)
    raise RuntimeError(f"loaded plug-in path does not contain {fragment!r}")


def _verify_reload(cmds) -> dict[str, dict[str, str]]:
    """Unload both project plug-ins, rerun startup, and verify ownership."""
    cmds.file(new=True, force=True)
    python_plugin = _plugin_for_path_fragment(cmds, "/plug-ins/mmd_tools_plugin.py")
    cpp_plugin = _plugin_for_path_fragment(cmds, "/mmd_tools_cpp.")
    cmds.unloadPlugin(python_plugin, force=True)
    cmds.unloadPlugin(cpp_plugin, force=True)
    _run_startup_setup(cmds)
    return _ownership_report(cmds)


def main() -> int:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    os.environ["MMD_TOOLS_ROOT"] = str(ROOT)
    os.environ["MMD_TOOLS_CPP_AUTOLOAD"] = "1"
    os.environ["MMD_TOOLS_SKIP_SHADER_OVERRIDE"] = "1"

    import maya.cmds as cmds
    import maya.standalone

    maya.standalone.initialize(name="python")
    try:
        _run_startup_setup(cmds)
        initial = _ownership_report(cmds)
        maya_version = str(cmds.about(version=True)).split()[0]
        report_dir = ROOT / "build" / "reports" / "cpp_dg_ownership" / maya_version
        _verify_scene_roundtrip(cmds, report_dir / "scene_roundtrip.ma")
        _verify_undo_redo(cmds)
        reloaded = _verify_reload(cmds)
        report = {
            "status": "pass",
            "maya": maya_version,
            "initialOwners": initial,
            "reloadedOwners": reloaded,
            "sceneRoundtrip": "pass",
            "undoRedo": "pass",
        }
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "ownership.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print("CPP_DG_OWNERSHIP_REPORT=" + json.dumps(report, sort_keys=True))
        print(f"OK: wrote {report_path}")
        return 0
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
