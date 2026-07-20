"""Maya 2024 smoke for report-only HumanIK constraint classification.

The checked-in PMX fixture is imported with its MMD rig, classified from live
DG connections, and verified to have identical connections before and after.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import maya.cmds as cmds
import maya.standalone

from mmd_tools.core.humanik_builder import resolve_scene_humanik_assignments
from mmd_tools.core.humanik_constraints import (
    classify_humanik_constraints,
    collect_humanik_constraint_facts,
    snapshot_constraint_connections,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke HumanIK constraint reporting under mayapy.")
    parser.add_argument("--pmx", default="tests/data/mmt_test_model.pmx")
    parser.add_argument("--out", default="build/reports/humanik_constraint_report_smoke.json")
    return parser.parse_args()


def _load_mmd_plugin() -> None:
    plugin_path = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
    if not cmds.pluginInfo(plugin_path.stem, query=True, loaded=True):
        cmds.loadPlugin(str(plugin_path), quiet=True)


def main() -> int:
    args = _parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"status": "fail", "pmx": str(args.pmx)}
    maya.standalone.initialize(name="python")
    try:
        from mmd_tools.io.mmd_importer import import_mmd_file

        payload["mayaVersion"] = cmds.about(version=True)
        _load_mmd_plugin()
        root = import_mmd_file(
            str(Path(args.pmx).resolve()),
            options={
                "setup_rig": True,
                "import_physics": False,
                "create_mmd_shaders": False,
                "use_native_pmx_parse": False,
                "require_native_pmx_parse": False,
            },
        )
        if not root:
            raise RuntimeError(f"PMX import failed: {args.pmx}")
        assignments = resolve_scene_humanik_assignments(str(root))
        before = snapshot_constraint_connections()
        facts = collect_humanik_constraint_facts()
        report = classify_humanik_constraints(
            facts,
            (assignment.joint for assignment in assignments.assignments),
        )
        after = snapshot_constraint_connections()
        payload.update(
            {
                "modelRoot": str(root),
                "assignmentCount": len(assignments.assignments),
                "connectionsUnchanged": before == after,
                "connectionSnapshot": after,
                "report": report,
                "status": "pass" if report["nodeCount"] > 0 and before == after else "fail",
            }
        )
        if payload["status"] != "pass":
            raise RuntimeError("HumanIK constraint report acceptance failed")
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "status": payload["status"],
                    "assignmentCount": payload["assignmentCount"],
                    "nodeCount": report["nodeCount"],
                    "counts": report["counts"],
                    "connectionsUnchanged": payload["connectionsUnchanged"],
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        payload["error"] = str(exc)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        raise
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
