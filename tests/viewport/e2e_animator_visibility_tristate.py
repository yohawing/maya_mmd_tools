"""Maya GUI E2E for Animator Toolset tri-state visibility and picker guards.

The Maya-side probe creates one small, namespaced MMD-shaped scene instead of
depending on a large external asset.  It opens the real Animator Toolset,
clicks every visibility button, exercises the presenter selection guard, then
checks scene callbacks and history readback after save/open and undo/redo.

Usage::

    python tests/viewport/e2e_animator_visibility_tristate.py --maya 2024
    python tests/viewport/e2e_animator_visibility_tristate.py --maya 2026
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.viewport.maya_e2e_harness import run_maya_e2e

COMMAND_PORT = 7774
COMPLETION_MARKER = "//-- ANIMATOR_VISIBILITY_TRISTATE_E2E_DONE --//"
TEST_TIMEOUT = 240.0

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _write_report(path: Path, report: dict[str, Any]) -> None:
    """Write a deterministic JSON report, creating its parent first."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _load_plugins(cmds: Any) -> None:
    """Load the native and Python plugins used by the real Animator window."""

    maya_major = str(cmds.about(version=True)).split(".", 1)[0]
    cpp_plugin = _PROJECT_ROOT / "plug-ins" / maya_major / "Debug" / "mmd_tools_cpp.mll"
    if cpp_plugin.is_file():
        plugin_dir = str(cpp_plugin.parent)
        if plugin_dir not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = plugin_dir + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            _load_plugins._dll_handle = os.add_dll_directory(plugin_dir)
        if not cmds.pluginInfo(str(cpp_plugin), query=True, loaded=True):
            cmds.loadPlugin(str(cpp_plugin), quiet=True)
    plugin_path = _PROJECT_ROOT / "mmd_tools" / "plugin_main.py"
    if not cmds.pluginInfo(plugin_path.stem, query=True, loaded=True):
        cmds.loadPlugin(str(plugin_path), quiet=True)


def _process_events() -> None:
    """Drain Maya's Qt event queue without manually refreshing presenters."""

    try:
        from mmd_tools.ui.qt_compat import QApplication
        import maya.utils as maya_utils
        import maya.cmds as cmds

        app = QApplication.instance()
        if app is not None:
            # A Maya Undo/Redo event may enqueue the presenter's deferred
            # QTimer after the first event pass. Drain a few bounded passes so
            # the assertion observes the UI readback, without calling any
            # presenter refresh method directly.
            for _index in range(3):
                try:
                    maya_utils.processIdleEvents()
                except Exception:
                    pass
                try:
                    cmds.flushIdleQueue()
                except Exception:
                    pass
                app.processEvents()
                time.sleep(0.05)
            return
    except Exception:
        pass
    time.sleep(0.05)


def _add_string_attr(cmds: Any, node: str, name: str, value: str) -> None:
    if not cmds.attributeQuery(name, node=node, exists=True):
        cmds.addAttr(node, longName=name, dataType="string")
    cmds.setAttr(f"{node}.{name}", str(value), type="string")


def _add_int_attr(cmds: Any, node: str, name: str, value: int) -> None:
    if not cmds.attributeQuery(name, node=node, exists=True):
        cmds.addAttr(node, longName=name, attributeType="long")
    cmds.setAttr(f"{node}.{name}", int(value))


def _node_uuid(cmds: Any, node: str) -> str:
    values = cmds.ls(node, uuid=True) or []
    if len(values) != 1:
        raise RuntimeError(f"expected one UUID for {node!r}, got {values!r}")
    return str(values[0])


def _create_synthetic_model(cmds: Any) -> dict[str, str]:
    """Create a compact model root, display groups, center joint and CR group."""

    from mmd_tools.adapters.maya_cmds_adapter import MayaCmdsAdapter
    from mmd_tools.core.constants import ATTR_MMD_MODEL_NAME, ATTR_MMD_MODEL_NAME_EN
    from mmd_tools.core.mmd_control_rig_builder import (
        CONTROL_RIG_CONTROL_OWNED,
        CONTROL_RIG_EDIT,
        CONTROL_RIG_METADATA_SCHEMA,
        CONTROL_RIG_METADATA_VERSION,
    )
    from mmd_tools.core.visibility_state import ensure_visibility_attrs, sync_visibility_connections

    cmds.file(new=True, force=True)
    cmds.namespace(setNamespace=":")
    namespace = "visSynthetic"
    if not cmds.namespace(exists=namespace):
        cmds.namespace(add=namespace)
    cmds.namespace(setNamespace=":")
    root = str(cmds.group(empty=True, name=f"{namespace}:Model_root"))
    _add_string_attr(cmds, root, ATTR_MMD_MODEL_NAME, "Visibility Synthetic")
    _add_string_attr(cmds, root, ATTR_MMD_MODEL_NAME_EN, "Visibility Synthetic")
    geometry = str(cmds.group(empty=True, name=f"{namespace}:Geometry", parent=root))
    skeleton = str(cmds.group(empty=True, name=f"{namespace}:Skeleton", parent=root))
    physics = str(cmds.group(empty=True, name=f"{namespace}:Physics", parent=root))

    cube, _history = cmds.polyCube(name=f"{namespace}:DisplayCube", width=2.0, height=2.0, depth=2.0)
    cube = str(cmds.parent(cube, geometry)[0])
    cmds.select(clear=True)
    joint = str(cmds.joint(name=f"{namespace}:Center_JNT"))
    joint = str(cmds.parent(joint, skeleton)[0])
    _add_string_attr(cmds, joint, "mmd_bone_name", "センター")
    _add_string_attr(cmds, joint, "mmd_bone_name_en", "Center")
    _add_int_attr(cmds, joint, "mmd_bone_index", 0)
    _add_int_attr(cmds, joint, "mmd_bone_flags", 0)
    collider, _collider_history = cmds.polySphere(name=f"{namespace}:Collider", radius=0.25)
    collider = str(cmds.parent(collider, physics)[0])

    adapter = MayaCmdsAdapter(cmds_module=cmds)
    ensure_visibility_attrs(adapter, root)
    cmds.setAttr(f"{root}.mmd_show_physics_colliders", True)
    sync_visibility_connections(adapter, root)

    # A minimal UUID-owned Control Rig hierarchy is enough for the real
    # presenter to resolve the Control Rig visibility boundary and picker
    # ownership.  Include curve shapes in ``nodes`` so topology validation is
    # exact, matching the production builder's metadata contract.
    control_group = str(cmds.group(empty=True, name=f"{namespace}:Controls", parent=root))
    zero = str(cmds.group(empty=True, name=f"{namespace}:center_ZERO", parent=control_group))
    control, _shape = cmds.circle(name=f"{namespace}:center_CTRL", normal=(0.0, 1.0, 0.0), radius=1.0)
    control = str(cmds.parent(control, zero)[0])
    selection_set = str(cmds.sets(empty=True, name=f"{namespace}:Controls_SET"))
    cmds.sets(control, add=selection_set)
    dag_nodes = [
        control_group,
        *[str(node) for node in (cmds.listRelatives(control_group, allDescendents=True, fullPath=True) or [])],
    ]
    nodes = [{"uuid": _node_uuid(cmds, node), "name": node} for node in dag_nodes]
    nodes.append({"uuid": _node_uuid(cmds, selection_set), "name": selection_set})
    metadata = {
        "schema": CONTROL_RIG_METADATA_SCHEMA,
        "version": CONTROL_RIG_METADATA_VERSION,
        "state": CONTROL_RIG_EDIT,
        "owner": CONTROL_RIG_CONTROL_OWNED,
        "displayReferenceTime": 0.0,
        "modelRootUuid": _node_uuid(cmds, root),
        "controlGroupUuid": _node_uuid(cmds, control_group),
        "selectionSetUuid": _node_uuid(cmds, selection_set),
        "nodes": nodes,
        "controls": {"center": _node_uuid(cmds, control)},
        "zeroGroups": {"center": _node_uuid(cmds, zero)},
        "bindings": {
            "center": {
                "jointUuid": _node_uuid(cmds, joint),
                "authoredPlugRefs": [],
                "ikSolverUuids": [],
            }
        },
    }
    _add_string_attr(cmds, root, "mmd_control_rig_json", json.dumps(metadata, ensure_ascii=False, sort_keys=True))
    cmds.select(clear=True)
    return {
        "root": root,
        "geometry": geometry,
        "skeleton": skeleton,
        "physics": physics,
        "cube": cube,
        "joint": joint,
        "collider": collider,
        "control_group": control_group,
        "zero": zero,
        "control": control,
        "selection_set": selection_set,
    }


def _group_state(cmds: Any, group: str) -> dict[str, Any]:
    """Read the actual Maya plugs that define a tri-state state."""

    return {
        "visibility": bool(cmds.getAttr(f"{group}.visibility")),
        "overrideEnabled": bool(cmds.getAttr(f"{group}.overrideEnabled")),
        "overrideDisplayType": int(cmds.getAttr(f"{group}.overrideDisplayType")),
    }


def _button_state(button: Any) -> str:
    return str(getattr(button, "visibility_state", ""))


def _click_cycle(cmds: Any, window: Any, key: str) -> dict[str, Any]:
    """Perform three real Qt clicks and record UI + plug readback."""

    button = window.animation_tab.vis_checkboxes[key]
    if not button.isEnabled():
        raise RuntimeError(f"visibility button {key} is disabled")
    expected = ("reference", "hidden", "visible")
    rows = []
    group = window.animation_presenter._control_rig_group(window.app_state.current_model_root) if key == "control_rig" else window.animation_presenter.maya_adapter.list_relatives(window.app_state.current_model_root, children=True, type="transform", fullPath=True)
    if key != "control_rig":
        short = {"mesh": "Geometry", "joints": "Skeleton", "colliders": "Physics"}[key]
        matches = [node for node in group if str(node).rsplit("|", 1)[-1].rsplit(":", 1)[-1] == short]
        if len(matches) != 1:
            raise RuntimeError(f"could not resolve {key} group: {matches!r}")
        group = str(matches[0])
    for expected_state in expected:
        button.click()
        _process_events()
        state = _button_state(button)
        raw = _group_state(cmds, str(group))
        rows.append({"expected": expected_state, "ui": state, "plugs": raw, "passed": state == expected_state})
    if not all(row["passed"] for row in rows):
        raise RuntimeError(f"{key} tri-state cycle failed: {rows!r}")
    return {"group": str(group), "rows": rows}


def _set_button_state(cmds: Any, window: Any, key: str, desired: str) -> None:
    button = window.animation_tab.vis_checkboxes[key]
    for _index in range(4):
        if _button_state(button) == desired:
            return
        button.click()
        _process_events()
    raise RuntimeError(f"could not set {key} to {desired}, got {_button_state(button)}")


def run_probe(log_path: str, report_path: str, scene_path: str) -> None:
    """Execute all assertions inside an isolated real Maya GUI process."""

    import maya.cmds as cmds

    log_file = Path(log_path)
    report_file = Path(report_path)
    report: dict[str, Any] = {
        "kind": "animator-visibility-tristate-e2e",
        "status": "error",
        "mayaVersion": None,
        "scene": {},
        "ui": {},
        "picker": {},
        "lifecycle": {},
        "history": {},
        "checks": {},
        "errors": [],
    }

    def log(message: str) -> None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(f"{message}\n")
        try:
            print(message)
        except Exception:
            pass

    window = None
    deferred_history = False
    finalized = False

    def _finish() -> None:
        """Close the real window and publish the report exactly once."""

        nonlocal finalized
        if finalized:
            return
        finalized = True
        try:
            from mmd_tools.plugin_main import close_animator_toolset

            close_animator_toolset()
        except Exception:
            pass
        _write_report(report_file, report)
        log("RESULT_JSON: " + json.dumps(report, ensure_ascii=False, sort_keys=True))
        log(COMPLETION_MARKER)

    try:
        report["mayaVersion"] = str(cmds.about(version=True))
        _load_plugins(cmds)
        model = _create_synthetic_model(cmds)
        report["scene"] = model

        from mmd_tools.plugin_main import open_animator_toolset

        window = open_animator_toolset(dockable=False)
        _process_events()
        combo = window.animation_tab.model_combo
        model_count = int(combo.count())
        selected_root = str(window.app_state.current_model_root or "")
        if model_count != 1 or selected_root != model["root"]:
            raise RuntimeError(f"synthetic model was not selected: count={model_count}, root={selected_root!r}")

        cycles = {}
        for key in ("mesh", "joints", "colliders", "control_rig"):
            cycles[key] = _click_cycle(cmds, window, key)
        report["ui"] = {
            "windowVisible": bool(window.isVisible()),
            "modelCount": model_count,
            "selectedRoot": selected_root,
            "cycles": cycles,
        }

        # Guard both MMD joint and UUID-owned control selection through the
        # same presenter entry point used by picker/display actions.
        presenter = window.animation_presenter
        cmds.select(clear=True)
        _set_button_state(cmds, window, "joints", "reference")
        accepted_joint = [str(node) for node in presenter._select_nodes([model["joint"]])]
        joint_selection = [str(node) for node in (cmds.ls(selection=True, long=True) or [])]
        _set_button_state(cmds, window, "joints", "visible")
        _set_button_state(cmds, window, "control_rig", "hidden")
        cmds.select(clear=True)
        accepted_control = [str(node) for node in presenter._select_nodes([model["control"]])]
        control_selection = [str(node) for node in (cmds.ls(selection=True, long=True) or [])]
        report["picker"] = {
            "joint": {"accepted": accepted_joint, "selection": joint_selection, "blocked": not accepted_joint and not joint_selection},
            "control": {"accepted": accepted_control, "selection": control_selection, "blocked": not accepted_control and not control_selection},
        }

        # Leave the mesh in Reference state, save, and rely on Maya's scene
        # callback to refresh the open Animator window after a new/open cycle.
        _set_button_state(cmds, window, "control_rig", "visible")
        _set_button_state(cmds, window, "mesh", "reference")
        scene_file = Path(scene_path)
        scene_file.parent.mkdir(parents=True, exist_ok=True)
        cmds.file(rename=str(scene_file))
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(new=True, force=True)
        cmds.file(str(scene_file), open=True, force=True)
        _process_events()
        reopened_root = str(window.app_state.current_model_root or "")
        reopened_state = _button_state(window.animation_tab.vis_checkboxes["mesh"])
        report["lifecycle"]["saveOpen"] = {
            "reopenedRoot": reopened_root,
            "comboCount": int(window.animation_tab.model_combo.count()),
            "meshState": reopened_state,
            "passed": reopened_root == model["root"] and reopened_state == "reference",
        }
        if not report["lifecycle"]["saveOpen"]["passed"]:
            raise RuntimeError(f"scene callback did not restore Animator state: {report['lifecycle']['saveOpen']!r}")

        # Create a fresh post-open visibility edit first; opening a Maya scene
        # intentionally clears the prior undo queue. Undo/redo are then driven
        # through executeDeferred hops because a commandPort probe is one Maya
        # command and Maya only runs scriptJob callbacks at idle boundaries.
        # No presenter refresh method is called by this probe.
        mesh_button = window.animation_tab.vis_checkboxes["mesh"]
        mesh_button.click()
        _process_events()
        if _button_state(mesh_button) != "hidden":
            raise RuntimeError(f"post-open edit did not reach hidden: {_button_state(mesh_button)}")

        from maya import utils as maya_utils

        def _deferred_failure() -> None:
            report["errors"].append(traceback.format_exc())
            log("EXCEPTION:\n" + report["errors"][-1])
            _finish()

        def _history_redo_assert() -> None:
            try:
                _process_events()
                redo_state = _button_state(mesh_button)
                redo_raw = _group_state(cmds, model["geometry"])
                report["history"]["redo"] = {
                    "ui": redo_state,
                    "plugs": redo_raw,
                    "passed": redo_state == "hidden" and not redo_raw["visibility"],
                }
                report["checks"] = {
                    "windowVisible": bool(window.isVisible()),
                    "allCategoriesCycle": all(
                        all(row["passed"] for row in value["rows"])
                        for value in cycles.values()
                    ),
                    "jointPickerBlocked": bool(report["picker"]["joint"]["blocked"]),
                    "controlPickerBlocked": bool(report["picker"]["control"]["blocked"]),
                    "saveOpenReadback": bool(report["lifecycle"]["saveOpen"]["passed"]),
                    "undoReadback": bool(report["history"]["undo"]["passed"]),
                    "redoReadback": bool(report["history"]["redo"]["passed"]),
                }
                if not all(report["checks"].values()):
                    raise RuntimeError(f"one or more visibility E2E checks failed: {report['checks']!r}")
                report["status"] = "pass"
                log("PASS: Animator tri-state visibility lifecycle E2E")
            except Exception:
                _deferred_failure()
                return
            _finish()

        def _history_redo_phase() -> None:
            """Apply Redo at a fresh idle boundary (Maya 2024 needs this)."""

            try:
                cmds.redo()
                maya_utils.executeDeferred(_history_redo_assert)
            except Exception:
                _deferred_failure()

        def _history_undo_assert() -> None:
            try:
                _process_events()
                undo_state = _button_state(mesh_button)
                undo_raw = _group_state(cmds, model["geometry"])
                report["history"]["undo"] = {
                    "ui": undo_state,
                    "plugs": undo_raw,
                    "passed": undo_state == "reference" and undo_raw["visibility"],
                }
                maya_utils.executeDeferred(_history_redo_phase)
            except Exception:
                _deferred_failure()

        def _history_undo_phase() -> None:
            try:
                cmds.undo()
                maya_utils.executeDeferred(_history_undo_assert)
            except Exception:
                _deferred_failure()

        maya_utils.executeDeferred(_history_undo_phase)
        deferred_history = True
        return
    except Exception:
        report["errors"].append(traceback.format_exc())
        log("EXCEPTION:\n" + traceback.format_exc())
    finally:
        if not deferred_history:
            _finish()


def main() -> int:
    """Launch Maya GUI, run the commandPort probe and return its status."""

    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    parser = argparse.ArgumentParser(description="Animator tri-state visibility Maya GUI E2E")
    parser.add_argument("--maya", default="2026")
    parser.add_argument("--port", type=int, default=COMMAND_PORT)
    parser.add_argument("--timeout", type=float, default=TEST_TIMEOUT)
    parser.add_argument("--out-dir", type=Path, default=_PROJECT_ROOT / "build" / "e2e")
    args = parser.parse_args()
    out_dir = args.out_dir.resolve()
    suffix = f"maya{args.maya}"
    report_path = out_dir / f"animator_visibility_tristate_e2e_{suffix}.json"
    log_path = out_dir / f"animator_visibility_tristate_e2e_{suffix}.log"
    scene_path = out_dir / f"animator_visibility_tristate_e2e_{suffix}.ma"
    command = (
        "import sys\n"
        "from pathlib import Path\n"
        f"project_root = Path(r'{_PROJECT_ROOT.as_posix()}')\n"
        "sys.path.insert(0, str(project_root)) if str(project_root) not in sys.path else None\n"
        "from tests.viewport.e2e_animator_visibility_tristate import run_probe\n"
        f"run_probe(r'{log_path.as_posix()}', r'{report_path.as_posix()}', r'{scene_path.as_posix()}')\n"
    )
    report = run_maya_e2e(
        project_root=_PROJECT_ROOT,
        version=str(args.maya),
        out_dir=out_dir,
        port=int(args.port),
        timeout=float(args.timeout),
        log_path=log_path,
        report_path=report_path,
        command=command,
        marker=COMPLETION_MARKER,
        send_label="<animator-visibility-tristate-e2e>",
        stale_paths=(log_path, report_path, scene_path),
        port_error=f"commandPort :{args.port} is already open; choose another --port",
        report_error=f"Animator visibility E2E report missing: {report_path}",
        log_ready=logger,
        warn_detached=True,
    )
    logger.info("Animator visibility E2E status: %s", report.get("status"))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
