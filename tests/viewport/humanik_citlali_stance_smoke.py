"""Maya 2024 Citlali HumanIK setup/restore regression gate.

Imports the ASCII-path Citlali PMX fixture through the production importer and
runs :class:`HumanIkFrontendSession` setup/characterize.  The report preserves
rotate, jointOrient, and JO-aware skin-product restore evidence when the strict
transaction fails.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

import maya.cmds as cmds
import maya.standalone


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pmx",
        default="build/fixtures/citlali_ascii_file/citlali.pmx",
        help="ASCII-path Citlali PMX fixture (the source asset is never modified).",
    )
    parser.add_argument("--out", default="build/reports/humanik_citlali_stance_smoke.json")
    parser.add_argument("--profile", choices=("body-only", "full"), default="body-only")
    return parser.parse_args()


def _load_plugin() -> None:
    plugin_path = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
    if not cmds.pluginInfo(plugin_path.stem, query=True, loaded=True):
        cmds.loadPlugin(str(plugin_path), quiet=True)


def _load_model(path: Path) -> str:
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


def _write_report(path: Path, report: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _flatten(value: Any) -> List[float]:
    while isinstance(value, (tuple, list)) and len(value) == 1 and isinstance(value[0], (tuple, list)):
        value = value[0]
    return [float(item) for item in (value or ())]


def _transform_snapshot(assignments: Iterable[Any]) -> Dict[str, Dict[str, Any]]:
    attrs = ("translate", "rotate", "rotateAxis", "jointOrient", "scale")
    snapshot: Dict[str, Dict[str, Any]] = {}
    for assignment in assignments:
        joints = cmds.ls(str(assignment.joint), long=True) or [str(assignment.joint)]
        joint = str(joints[0])
        row = {
            "hikBone": str(assignment.hik_bone),
            "worldMatrix": _flatten(cmds.getAttr(f"{joint}.worldMatrix[0]")),
        }
        for attr in attrs:
            row[attr] = _flatten(cmds.getAttr(f"{joint}.{attr}"))
        snapshot[joint] = row
    return snapshot


def _transform_diffs(before: Dict[str, Dict[str, Any]], after: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for joint in sorted(set(before) | set(after)):
        left = before.get(joint, {})
        right = after.get(joint, {})
        residuals = {
            key: max((abs(a - b) for a, b in zip(left.get(key, []), right.get(key, []))), default=0.0)
            for key in ("translate", "rotate", "rotateAxis", "jointOrient", "scale", "worldMatrix")
        }
        if any(value > 0.0 for value in residuals.values()):
            rows.append({
                "joint": joint,
                "hikBone": right.get("hikBone", left.get("hikBone")),
                "residuals": residuals,
                "before": left,
                "after": right,
            })
    return rows


def main() -> int:
    args = _parse_args()
    # Maya 2024 mayapy inherits a locale that cannot print fixture texture
    # names.  Keep the diagnostic gate itself UTF-8 without touching the PMX.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    maya.standalone.initialize(name="python")
    report: Dict[str, Any] = {
        "status": "fail",
        "pmx": str(Path(args.pmx).resolve()),
        "profile": args.profile,
    }
    try:
        _load_plugin()
        root = _load_model(Path(args.pmx))
        report["modelRoot"] = root
        from mmd_tools.core.humanik_builder import resolve_scene_humanik_assignments

        resolved = resolve_scene_humanik_assignments(root, cmds_module=cmds)
        before_state = _transform_snapshot(resolved.assignments)
        from mmd_tools.core.humanik_frontend import HumanIkFrontendSession

        session = HumanIkFrontendSession()
        try:
            binding = session.setup_and_characterize(root, profile=args.profile)
        except Exception as error:
            report["error"] = str(error)
            pending = session._pending_stances.get(root)
            if pending is not None:
                report["stance"] = pending.to_dict()
            report["transformDiffs"] = _transform_diffs(before_state, _transform_snapshot(resolved.assignments))
            raise
        else:
            report["status"] = "pass"
            report["binding"] = binding.to_dict()
            report["stance"] = dict(binding.stance)
            report["transformDiffs"] = _transform_diffs(before_state, _transform_snapshot(resolved.assignments))
        _write_report(Path(args.out), report)
        return 0
    except Exception:
        _write_report(Path(args.out), report)
        return 1
    finally:
        try:
            maya.standalone.uninitialize()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
