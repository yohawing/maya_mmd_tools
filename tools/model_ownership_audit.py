"""Audit MMD model-root ownership without mutating the inspected scene.

Run with Maya's standalone Python::

    mayapy tools/model_ownership_audit.py --scene build/example.ma

The report is intentionally diagnostic.  Existing root.message fan-out is
reported as migration_required, while unknown destinations and ambiguous
legacy owner links fail the audit.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import maya.standalone


ROOT = Path(__file__).resolve().parents[1]


def _parse_args() -> argparse.Namespace:
    """Parse the read-only audit command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", type=Path, required=True, help="Maya scene to inspect")
    parser.add_argument(
        "--root",
        action="append",
        default=[],
        help="Optional model root to inspect; repeat for multiple roots",
    )
    parser.add_argument("--out", type=Path, default=None, help="Optional JSON report path")
    return parser.parse_args()


def _resolve(path: Path) -> Path:
    """Resolve a CLI path relative to the repository root."""
    return path if path.is_absolute() else ROOT / path


def main() -> int:
    """Open one scene, emit the ownership report, and leave no scene changes."""
    args = _parse_args()
    scene_path = _resolve(args.scene).resolve()
    if not scene_path.is_file():
        raise FileNotFoundError(f"scene does not exist: {scene_path}")
    output_path = _resolve(args.out).resolve() if args.out is not None else None
    if output_path is not None and (
        output_path == scene_path
        or (output_path.exists() and output_path.samefile(scene_path))
    ):
        raise ValueError("--out must not overwrite --scene")

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    maya.standalone.initialize(name="python")
    try:
        from maya import cmds

        from tests.common.maya_plugin_setup import load_mmd_tools_plugin

        load_mmd_tools_plugin(ROOT, cmds_module=cmds)
        from mmd_tools.core.model_ownership_audit import (
            aggregate_model_audit_status,
            audit_model_root,
            audit_scene_model_roots,
        )

        cmds.file(str(scene_path), open=True, force=True)
        if args.root:
            models = [audit_model_root(root) for root in args.root]
            report = {
                "schema_version": 1,
                "scene": str(scene_path),
                "models": models,
                "status": aggregate_model_audit_status(models),
            }
        else:
            report = {**audit_scene_model_roots(), "scene": str(scene_path)}
        encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
        print(encoded)
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(encoded + "\n", encoding="utf-8")
        return 0 if report.get("status", "pass") != "fail" else 1
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
