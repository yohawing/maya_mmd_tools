"""Mayapy E2E smoke for the HumanIK VMD-import mode gate.

Covers ``HUMANIK-SOURCE-VMD-IK-PARITY-1``'s mode rule against a real Maya
scene (not unit-test doubles): VMD import must stay permitted in
NEUTRAL/SOURCE, must be refused fail-closed while the target model is a
HumanIK TARGET preview, and the refusal must be completely side-effect free
(scene, HIK mode, writer topology, and existing animCurves untouched).

Scenario, all against one Kokomi PMX fixture imported twice:

1. Characterize both copies (``HumanIkFrontendSession.setup_and_characterize``),
   select the first as SOURCE and enter a TARGET preview on the second
   (mirrors ``tests/viewport/humanik_target_preview_smoke.py``).
2. Attempt a VMD import onto the TARGET model.  Expect
   ``mmd_tools.core.exceptions.MMDImportException`` naming the blocking mode
   and ``Restore MMD Rig``.
3. Diff a topology snapshot (per-joint incoming connections on the HIK
   assignment channels, plus the scene's ``animCurve`` node set) captured
   immediately before and after the refused import.  They must be identical.
4. Call ``session.restore_mmd_rig()`` (the actual "Restore MMD Rig" action)
   and retry the same VMD import onto the same model.  It must now succeed.

Usage::

    mayapy tests/viewport/humanik_vmd_import_gate_smoke.py \\
        --model "F:/MMD/pmx/.../model.pmx" \\
        --motion tests/data/mmt_test_model_test_motion.vmd \\
        --out build/reports/humanik_vmd_import_gate_smoke.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import maya.cmds as cmds
import maya.standalone


DEFAULT_MODEL = r"F:\MMD\pmx\【珊瑚宫心海】_by_原神_32c242c2043da5bac0d24f1b07a2f3f8\珊瑚宫心海.pmx"
DEFAULT_MOTION = "tests/data/mmt_test_model_test_motion.vmd"
CHANNELS = ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--motion", default=DEFAULT_MOTION)
    parser.add_argument("--out", default="build/reports/humanik_vmd_import_gate_smoke.json")
    return parser.parse_args()


def _load_plugin() -> None:
    path = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
    if not cmds.pluginInfo(path.stem, query=True, loaded=True):
        cmds.loadPlugin(str(path), quiet=True)


def _import_model(path: Path) -> str:
    from mmd_tools.io.mmd_importer import import_mmd_file

    root = import_mmd_file(
        str(path),
        options={
            "use_namespace": True,
            "setup_rig": True,
            "import_physics": False,
            "create_mmd_shaders": False,
            "use_native_pmx_parse": False,
            "require_native_pmx_parse": False,
        },
    )
    if not root:
        raise RuntimeError(f"PMX import failed: {path}")
    return str(root)


def _import_motion(motion: Path, model: Path, target_model: str) -> None:
    """Import a VMD in rig mode onto ``target_model``; raises on gate/other failure."""
    from mmd_tools.io.mmd_importer import import_mmd_file

    if not import_mmd_file(
        str(motion),
        options={
            "target_model": target_model,
            "pmx_path": str(model),
            "bake_mode": False,
            "clear_existing_motion": True,
            "use_native_pmx_parse": False,
            "require_native_pmx_parse": False,
        },
    ):
        raise RuntimeError(f"VMD import failed: {motion}")


def _snapshot(target_joints: List[str]) -> Dict[str, Any]:
    """Capture writer topology + animCurve inventory for side-effect diffing."""
    connections: Dict[str, List[str]] = {}
    for joint in sorted(target_joints):
        for channel in CHANNELS:
            plug = f"{joint}.{channel}"
            connections[plug] = sorted(
                cmds.listConnections(plug, source=True, destination=False, plugs=True) or []
            )
    return {
        "animCurves": sorted(cmds.ls(type="animCurve") or []),
        "connections": connections,
    }


def main() -> int:
    args = _parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {"status": "fail", "model": args.model, "motion": args.motion}
    maya.standalone.initialize(name="python")
    try:
        from mmd_tools.core.exceptions import MMDImportException
        from mmd_tools.core.humanik_builder import resolve_scene_humanik_assignments
        from mmd_tools.core.humanik_frontend import HumanIkFrontendSession

        _load_plugin()
        model = Path(args.model).resolve()
        motion = Path(args.motion).resolve()

        source_root = _import_model(model)
        target_root = _import_model(model)

        session = HumanIkFrontendSession()
        session.setup_and_characterize(source_root)
        session.enter_source_mode(source_root)
        session.setup_and_characterize(target_root)
        session.enter_target_mode(target_root)

        target_result = resolve_scene_humanik_assignments(target_root)
        target_joints = [item.joint for item in target_result.assignments]

        before = _snapshot(target_joints)
        gate_error: str | None = None
        try:
            _import_motion(motion, model, target_root)
        except MMDImportException as exc:
            gate_error = str(exc)
        after = _snapshot(target_joints)

        topology_unchanged = before == after
        gate_raised = gate_error is not None
        gate_names_mode = bool(gate_error) and (
            "TARGET preview" in gate_error or "Control Rig" in gate_error
        )
        gate_says_restore = bool(gate_error) and "Restore MMD Rig" in gate_error

        restored = session.restore_mmd_rig()

        post_restore_error: str | None = None
        try:
            _import_motion(motion, model, target_root)
            post_restore_success = True
        except Exception as exc:  # noqa: BLE001 - captured for diagnostics
            post_restore_success = False
            post_restore_error = str(exc)

        payload.update(
            {
                "mayaVersion": cmds.about(version=True),
                "sourceRoot": source_root,
                "targetRoot": target_root,
                "targetJointCount": len(target_joints),
                "gateRaised": gate_raised,
                "gateError": gate_error,
                "gateNamesMode": gate_names_mode,
                "gateSaysRestore": gate_says_restore,
                "topologyUnchangedAfterRefusal": topology_unchanged,
                "restoreMmdRigReturned": bool(restored),
                "postRestoreImportSucceeded": post_restore_success,
                "postRestoreError": post_restore_error,
            }
        )
        payload["status"] = "pass" if all(
            (
                gate_raised,
                gate_names_mode,
                gate_says_restore,
                topology_unchanged,
                post_restore_success,
            )
        ) else "fail"
        if payload["status"] != "pass":
            raise RuntimeError("HumanIK VMD import gate acceptance failed")
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        # ensure_ascii=True here: the console codepage (e.g. cp932 on Windows)
        # may not encode CJK PMX paths in the payload; the UTF-8 report file
        # above keeps the readable form.
        print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
        return 0
    except Exception as exc:
        payload["error"] = str(exc)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        raise
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
