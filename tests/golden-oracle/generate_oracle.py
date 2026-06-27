"""Generate oracle JSONL for all cases in the numeric manifest.

Uses mmd-anim FFI directly (no Maya dependency). The oracle captures
mmd-anim runtime world matrices as the expected reference for CI regression gates.

Usage:
    python tests/golden-oracle/generate_oracle.py [--manifest PATH]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

SCRIPT = Path(__file__).resolve()
DEFAULT_MANIFEST = SCRIPT.parent / "manifest.json"


def _resolve_manifest_path(manifest_path: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return (manifest_path.parent / path).resolve()


def _generate_case(
    manifest_path: Path,
    case: Dict[str, Any],
) -> Path:
    """Generate oracle JSONL for a single case using mmd-anim FFI."""
    sys.path.insert(0, str(SCRIPT.parents[2]))
    from mmd_tools.core.native.mmd_anim_runtime import (
        MmdRuntimeClip,
        MmdRuntimeInstance,
        MmdRuntimeModel,
    )
    from mmd_tools.core.pmx_data import PmxData

    assets = case.get("assets", {})
    pmx_path = _resolve_manifest_path(manifest_path, assets["model"])
    vmd_path = _resolve_manifest_path(manifest_path, assets["motion"])
    frames = [int(f) for f in case.get("frames", [])]
    if not frames:
        raise ValueError(f"case has no frames: {case.get('name')}")

    oracle_rel = case.get("oracle", {}).get("path")
    if oracle_rel:
        output_path = _resolve_manifest_path(manifest_path, oracle_rel)
    else:
        output_path = manifest_path.parent / "oracle" / f"{case['name']}.oracle.jsonl"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pmx_bytes = pmx_path.read_bytes()
    vmd_bytes = vmd_path.read_bytes()

    pmx_data = PmxData()
    pmx_data.parse_file(str(pmx_path))
    bone_names = [b.name for b in pmx_data.bones]

    model = MmdRuntimeModel.from_pmx_bytes(pmx_bytes)
    if model is None:
        raise RuntimeError(f"Failed to create runtime model from {pmx_path}")
    clip = MmdRuntimeClip.from_vmd_bytes_for_model(model, vmd_bytes)
    if clip is None:
        raise RuntimeError(f"Failed to create runtime clip from {vmd_path}")
    instance = MmdRuntimeInstance.for_model(model)
    if instance is None:
        raise RuntimeError("Failed to create runtime instance")

    with output_path.open("w", encoding="utf-8", newline="\n") as f:
        for frame in frames:
            if not instance.evaluate_clip_frame(clip, float(frame)):
                raise RuntimeError(f"evaluate_clip_frame failed at frame {frame}")
            matrices = instance.get_world_matrices()
            if matrices is None:
                raise RuntimeError(f"get_world_matrices failed at frame {frame}")

            bones: List[Dict[str, Any]] = []
            for idx, mat in enumerate(matrices):
                name = bone_names[idx] if idx < len(bone_names) else f"bone_{idx}"
                bones.append({
                    "index": idx,
                    "name": name,
                    "worldMatrix": [float(v) for v in mat],
                })

            record = {
                "schemaVersion": 1,
                "source": {
                    "mmdVersion": "mmd-anim",
                    "dumperVersion": "1.0.0",
                    "backend": "mmd-anim.ffi",
                    "model": str(pmx_path),
                    "motion": str(vmd_path),
                    "evaluatedFrame": float(frame),
                },
                "frame": frame,
                "models": [{
                    "index": 0,
                    "name": str(pmx_path),
                    "filename": str(pmx_path),
                    "visible": True,
                    "bones": bones,
                    "morphs": [],
                }],
            }
            f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            f.write("\n")

    return output_path


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    args = parser.parse_args()
    manifest_path = Path(args.manifest).resolve()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases = manifest.get("cases", [])

    failures = 0
    for case in cases:
        name = case.get("name", "unknown")
        kind = case.get("kind", "")
        if kind not in ("motion-numeric", ""):
            print(f"SKIP {name} (kind={kind})")
            continue

        print(f"\n{'='*60}")
        print(f"Generating oracle: {name}")
        print(f"{'='*60}")
        try:
            output = _generate_case(manifest_path, case)
            print(f"  -> {output}")
        except Exception as exc:
            print(f"  FAILED: {exc}", file=sys.stderr)
            failures += 1

    print(f"\nDone: {len(cases) - failures}/{len(cases)} succeeded")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
