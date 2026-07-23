"""Interactive Maya GUI helper for the HumanIK Control Rig bake workflow.

Run this in Maya's Python Script Editor after opening the project::

    from tests.viewport import humanik_gui_demo as demo
    demo.run_fresh_scene_demo()  # Clears the current scene deliberately.

The helper imports the checked-in PMX/VMD fixture twice, sets up SOURCE and
TARGET, creates the TARGET Control Rig, starts the preview, and bakes SOURCE
motion to the Control Rig.  It opens the HumanIK editor and leaves the target
Control Rig selected for visual inspection/editing.

After editing a control in the viewport, return it to the MMD rig without
using the menu buttons::

    demo.bake_from_current_control_rig()

``run_round_trip_demo`` also adds a small deterministic edit to a live HIK IK
control and bakes it back.  This is intended for a quick visual smoke, not as
a replacement for ``humanik_bake_to_control_rig_probe.py``'s acceptance gate.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_DEFAULT_PMX = _PROJECT_ROOT / "tests" / "data" / "mmt_test_model.pmx"
_DEFAULT_VMD = _PROJECT_ROOT / "tests" / "data" / "mmt_test_model_test_motion.vmd"
_STATE: Dict[str, Any] = {}


def prepare_control_rig_demo(
    *,
    pmx_path: Optional[str] = None,
    vmd_path: Optional[str] = None,
    end: int = 30,
    reset_scene: bool = False,
) -> Dict[str, Any]:
    """Prepare an editable Control Rig from SOURCE VMD motion in Maya GUI.

    Args:
        pmx_path: Source and target PMX.  Defaults to the checked-in smoke fixture.
        vmd_path: SOURCE VMD.  Defaults to the matching smoke motion.
        end: Inclusive bake end frame.
        reset_scene: When ``True``, intentionally creates a new empty Maya scene.
            Use :func:`run_fresh_scene_demo` for this explicit convenience path.

    Returns:
        JSON-safe scene facts suitable for the Script Editor.

    Raises:
        RuntimeError: If the fixture, plugin, character definition, or native
            Control Rig bake cannot be prepared.
    """
    import maya.cmds as cmds
    import maya.mel as mel

    from mmd_tools.core.humanik_frontend import HumanIkFrontendSession
    from mmd_tools.io.mmd_importer import import_mmd_file
    from mmd_tools.ui import humanik_menu_actions

    pmx = Path(pmx_path).resolve() if pmx_path else _DEFAULT_PMX
    vmd = Path(vmd_path).resolve() if vmd_path else _DEFAULT_VMD
    if not pmx.is_file() or not vmd.is_file():
        raise RuntimeError(f"HumanIK GUI demo fixtures not found: pmx={pmx}, vmd={vmd}")
    if int(end) < 0:
        raise ValueError(f"Bake end must be non-negative: {end}")

    if reset_scene:
        cmds.file(new=True, force=True)
    _load_plugin(cmds)

    # setup_rig=False deliberately keeps this GUI smoke on the direct-joint
    # path.  The default MMD CCD IK fixture is expected to fail closed in
    # TARGET preview; this helper must not silence that ownership guard.
    source_root = _import_model(import_mmd_file, pmx)
    target_root = _import_model(import_mmd_file, pmx)
    session = HumanIkFrontendSession(cmds_module=cmds, mel_module=mel)

    source = session.setup_and_characterize(source_root)
    session.enter_source_mode(source_root)
    _import_motion(import_mmd_file, pmx, vmd, source_root)

    target = session.setup_and_characterize(target_root)
    session.create_control_rig(target_root)
    session.enter_target_mode(target_root)
    bake_result = session.bake_to_control_rig(0, int(end))

    humanik_menu_actions.set_humanik_session(session)
    cmds.select(target_root, replace=True)
    humanik_menu_actions.open_humanik_editor()
    editable_control = _select_first_hik_ik_node(mel, cmds, target.character)

    state = {
        "sourceRoot": source_root,
        "targetRoot": target_root,
        "sourceCharacter": source.character,
        "targetCharacter": target.character,
        "end": int(end),
        "editableControl": editable_control,
        "bakeTo": bake_result.to_dict(),
        "frontend": session.describe_frontend_state(target_root),
    }
    _STATE.clear()
    _STATE.update(state)
    _STATE["session"] = session
    print("HumanIK GUI demo ready. Edit the selected Control Rig, then run:")
    print("  from tests.viewport import humanik_gui_demo as demo; demo.bake_from_current_control_rig()")
    return _public_state()


def run_fresh_scene_demo(*, end: int = 30) -> Dict[str, Any]:
    """Prepare the demo in a new scene, explicitly discarding current scene state."""
    return prepare_control_rig_demo(end=end, reset_scene=True)


def bake_from_current_control_rig(*, start: int = 0, end: Optional[int] = None):
    """Bake the currently prepared Control Rig edit back to the target MMD rig."""
    from mmd_tools.ui import humanik_menu_actions

    _require_prepared_state()
    result = humanik_menu_actions.bake_from_control_rig(
        start=int(start),
        end=int(_STATE["end"] if end is None else end),
    )
    _STATE["bakeFrom"] = result.to_dict()
    print("HumanIK Control Rig edit baked back to the MMD rig.")
    return result


def run_round_trip_demo(*, end: int = 30) -> Dict[str, Any]:
    """Prepare, nudge one editable HIK control, and bake the edit back to MMD."""
    prepare_control_rig_demo(end=end, reset_scene=True)
    _nudge_selected_control()
    bake_from_current_control_rig(end=end)
    return _public_state()


def _load_plugin(cmds) -> None:
    plugin_path = _PROJECT_ROOT / "mmd_tools" / "plugin_main.py"
    if not cmds.pluginInfo(plugin_path.stem, query=True, loaded=True):
        cmds.loadPlugin(str(plugin_path), quiet=True)


def _import_model(import_mmd_file, pmx: Path) -> str:
    root = import_mmd_file(
        str(pmx),
        options={
            "use_namespace": True,
            "setup_rig": False,
            "import_physics": False,
            "create_mmd_shaders": False,
            "use_native_pmx_parse": False,
            "require_native_pmx_parse": False,
        },
    )
    if not root:
        raise RuntimeError(f"PMX import failed: {pmx}")
    return str(root)


def _import_motion(import_mmd_file, pmx: Path, vmd: Path, source_root: str) -> None:
    imported = import_mmd_file(
        str(vmd),
        options={
            "target_model": source_root,
            "pmx_path": str(pmx),
            "bake_mode": False,
            "clear_existing_motion": True,
            "use_native_pmx_parse": False,
            "require_native_pmx_parse": False,
        },
    )
    if not imported:
        raise RuntimeError(f"VMD import failed: {vmd}")


def _select_first_hik_ik_node(mel, cmds, character: str) -> Optional[str]:
    controls = [
        str(node)
        for node in (mel.eval(f'hikGetRigIkNodes("{character}")') or [])
        if node and cmds.objExists(str(node))
    ]
    if not controls:
        return None
    control = sorted(controls)[0]
    cmds.select(control, replace=True)
    return control


def _nudge_selected_control() -> Tuple[str, str]:
    """Apply a small frame-zero edit to one HIK IK control for the round trip."""
    import maya.cmds as cmds

    character = str(_STATE["targetCharacter"])
    control = str(_STATE.get("editableControl") or "")
    if not control or not cmds.objExists(control):
        raise RuntimeError(f"No editable HIK control is selected for: {character}")
    attribute = f"{control}.translateX"
    curves = cmds.listConnections(attribute, source=True, destination=False, type="animCurve") or []
    if curves:
        for curve in curves:
            cmds.keyframe(curve, time=(0, 0), relative=True, valueChange=1.0)
        cmds.refresh(force=True)
        return control, "animCurve.translateX"
    if not cmds.getAttr(attribute, settable=True):
        raise RuntimeError(f"HIK control is not editable: {attribute}")
    cmds.setAttr(attribute, float(cmds.getAttr(attribute)) + 1.0)
    cmds.refresh(force=True)
    return control, "translateX"


def _require_prepared_state() -> None:
    if not _STATE.get("session"):
        raise RuntimeError("Run prepare_control_rig_demo or run_fresh_scene_demo first")


def _public_state() -> Dict[str, Any]:
    return {key: value for key, value in _STATE.items() if key != "session"}


if __name__ == "__main__":
    # Paste/execute this file in Maya's Python Script Editor to get the
    # visible SOURCE -> TARGET Control Rig Bake-To state in one operation.
    run_fresh_scene_demo()
