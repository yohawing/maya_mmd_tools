"""Run local PMX/VMD asset smoke checks from a JSON manifest."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from maya import standalone

standalone.initialize(name="python")

from maya import cmds  # noqa: E402

from mmd_tools.actions import ImportModelAction, ImportModelRequest
from mmd_tools.io.mmd_importer import import_mmd_file


def _resolve_path(value: str, manifest_dir: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = manifest_dir / path
    return path.resolve()


def _write_reports(results: list[dict[str, Any]], out_json: Path, out_md: Path) -> None:
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    failed = [result for result in results if result["status"] == "fail"]
    payload = {
        "status": "fail" if failed else "pass",
        "results": results,
    }
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Local Assets Check",
        "",
        f"- Status: {payload['status']}",
        "",
        "| Asset | Status | Seconds | Detail |",
        "| --- | --- | ---: | --- |",
    ]
    for result in results:
        lines.append(
            f"| {result['name']} | {result['status']} | {result['duration_sec']} | {result['detail']} |"
        )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _check_asset(asset: dict[str, Any], manifest_dir: Path, strict: bool) -> dict[str, Any]:
    name = str(asset.get("name") or asset.get("model") or "asset")
    started = time.perf_counter()

    model_value = asset.get("model")
    if not model_value:
        return {
            "name": name,
            "status": "skip",
            "duration_sec": round(time.perf_counter() - started, 3),
            "detail": "model path is missing",
        }

    model_path = _resolve_path(str(model_value), manifest_dir)
    if not model_path.exists():
        return {
            "name": name,
            "status": "fail" if strict else "skip",
            "duration_sec": round(time.perf_counter() - started, 3),
            "detail": f"model not found: {model_path}",
        }

    options = {
        "import_physics": bool(asset.get("import_physics", False)),
        "setup_rig": bool(asset.get("setup_rig", False)),
        "setup_bone_orientation": bool(asset.get("setup_bone_orientation", False)),
    }
    result = ImportModelAction().execute(
        ImportModelRequest(
            file_path=str(model_path),
            options=options,
            create_new_scene=True,
        )
    )
    if not result.succeeded:
        return {
            "name": name,
            "status": "fail",
            "duration_sec": round(time.perf_counter() - started, 3),
            "detail": f"model import failed: {result.error}",
        }

    motion_value = asset.get("motion")
    if motion_value:
        motion_path = _resolve_path(str(motion_value), manifest_dir)
        if not motion_path.exists():
            return {
                "name": name,
                "status": "fail" if strict else "skip",
                "duration_sec": round(time.perf_counter() - started, 3),
                "detail": f"motion not found: {motion_path}",
            }
        try:
            import_mmd_file(
                str(motion_path),
                options={
                    "target_model": result.root_node,
                    "pmx_path": str(model_path),
                    "setup_rig": options["setup_rig"],
                },
            )
        except Exception as exc:
            return {
                "name": name,
                "status": "fail",
                "duration_sec": round(time.perf_counter() - started, 3),
                "detail": f"motion import failed: {exc}",
            }

    joint_count = len(cmds.ls(type="joint") or [])
    mesh_count = len(cmds.ls(type="mesh") or [])
    return {
        "name": name,
        "status": "pass",
        "duration_sec": round(time.perf_counter() - started, 3),
        "detail": f"root={result.root_node}, joints={joint_count}, meshes={mesh_count}",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-md", required=True)
    parser.add_argument("--strict-local", action="store_true")
    args = parser.parse_args()

    manifest_path = Path(args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assets = manifest.get("assets") or []
    results = [_check_asset(asset, manifest_path.parent, args.strict_local) for asset in assets]
    if not results:
        results = [
            {
                "name": str(manifest_path),
                "status": "fail" if args.strict_local else "skip",
                "duration_sec": 0.0,
                "detail": "manifest contains no assets",
            }
        ]

    _write_reports(results, Path(args.out_json), Path(args.out_md))
    return 1 if any(result["status"] == "fail" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
