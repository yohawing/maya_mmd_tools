"""
mmd-anim runtime resultsをGoldenOracle互換JSONLとして出力するCLI。

GoldenOracleのmotion-numeric manifestからPMX/VMD/framesを読み取り、
mmd-anim FFIで評価したbone worldMatrixとmorph weightをJSONLに保存する。
Mayaシーンを経由しないため、runtime単体の数値検証に使用する。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from mmd_tools.core.native.mmd_anim_runtime import (
    MmdRuntimeClip,
    MmdRuntimeInstance,
    MmdRuntimeModel,
    get_runtime_library_path,
    is_mmd_runtime_available,
)
from mmd_tools.core.pmx_data import PmxData


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def _find_case(manifest: Dict[str, Any], case_name: str) -> Dict[str, Any]:
    for case in manifest.get("cases", []):
        if case.get("name") == case_name:
            return case
    raise ValueError(f"case not found: {case_name}")


def _resolve_manifest_path(manifest_path: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return (manifest_path.parent / path).resolve()


def _offset_tag(sample_frame_offset: float) -> str:
    if abs(sample_frame_offset) < 1.0e-9:
        return ""
    text = f"{sample_frame_offset:g}".replace("-", "m").replace(".", "p")
    return f".offset{text}"


def _ik_option_tag(
    ik_tolerance: Optional[float],
    ik_max_iterations_cap: Optional[int],
) -> str:
    parts = []
    if ik_tolerance is not None:
        text = f"{ik_tolerance:g}".replace("-", "m").replace(".", "p")
        parts.append(f"tol{text}")
    if ik_max_iterations_cap is not None:
        parts.append(f"ikcap{max(0, int(ik_max_iterations_cap))}")
    return f".{'.'.join(parts)}" if parts else ""


def _default_output_path(
    manifest_path: Path,
    case: Dict[str, Any],
    sample_frame_offset: float,
    ik_tolerance: Optional[float],
    ik_max_iterations_cap: Optional[int],
) -> Path:
    oracle = case.get("oracle", {})
    oracle_path = oracle.get("path")
    filename = (
        f"runtime{_offset_tag(sample_frame_offset)}"
        f"{_ik_option_tag(ik_tolerance, ik_max_iterations_cap)}.actual.jsonl"
    )
    if oracle_path:
        resolved_oracle = _resolve_manifest_path(manifest_path, oracle_path)
        return resolved_oracle.with_name(filename)

    out_dir = manifest_path.parent.parent / "runs" / "motion-numeric"
    return out_dir / case["name"] / filename


def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def _focus_targets(
    manifest: Dict[str, Any],
    case: Dict[str, Any],
    target_key: str,
) -> List[str]:
    case_focus = case.get("metadata", {}).get("focus", {})
    if target_key in case_focus:
        return list(case_focus.get(target_key, []))

    defaults = manifest.get("defaults", {})
    default_focus = defaults.get("focus", {})
    return list(default_focus.get(target_key, []))


def _make_record(
    *,
    frame: int,
    evaluated_frame: float,
    pmx_path: Path,
    vmd_path: Path,
    pmx_data: PmxData,
    world_matrices: List[List[float]],
    morph_weights: List[float],
    ik_enabled: List[int],
    runtime_library_path: Optional[Path],
    ik_tolerance: Optional[float],
    ik_max_iterations_cap: Optional[int],
) -> Dict[str, Any]:
    bones = []
    for index, bone in enumerate(pmx_data.bones):
        matrix = world_matrices[index] if index < len(world_matrices) else []
        bones.append(
            {
                "index": index,
                "name": bone.name,
                "worldMatrix": matrix,
            }
        )

    morphs = []
    for index, morph in enumerate(pmx_data.morphs):
        weight = morph_weights[index] if index < len(morph_weights) else 0.0
        morphs.append(
            {
                "index": index,
                "name": morph.name,
                "weight": weight,
            }
        )

    return {
        "schemaVersion": 1,
        "source": {
            "backend": "maya_mmd_tools.mmd-anim-runtime",
            "runtimeLibrary": str(runtime_library_path) if runtime_library_path else None,
            "model": str(pmx_path),
            "motion": str(vmd_path),
            "evaluatedFrame": evaluated_frame,
            "ikTolerance": ik_tolerance,
            "ikMaxIterationsCap": ik_max_iterations_cap,
        },
        "frame": frame,
        "models": [
            {
                "index": 0,
                "name": str(pmx_path),
                "filename": str(pmx_path),
                "visible": True,
                "bones": bones,
                "morphs": morphs,
                "ikEnabled": ik_enabled,
            }
        ],
    }


def dump_runtime_oracle(
    *,
    manifest_path: Path,
    case_name: str,
    output_path: Optional[Path] = None,
    sample_frame_offset: float = 0.0,
    ik_tolerance: Optional[float] = None,
    ik_max_iterations_cap: Optional[int] = None,
) -> Path:
    manifest = _load_json(manifest_path)
    case = _find_case(manifest, case_name)

    assets = case.get("assets", {})
    pmx_path = Path(assets["model"])
    vmd_path = Path(assets["motion"])
    frames = [int(frame) for frame in case.get("frames", [])]
    if not frames:
        raise ValueError(f"case has no frames: {case_name}")

    if not pmx_path.exists():
        raise FileNotFoundError(f"PMX not found: {pmx_path}")
    if not vmd_path.exists():
        raise FileNotFoundError(f"VMD not found: {vmd_path}")

    if not is_mmd_runtime_available():
        raise RuntimeError("mmd-anim runtime library is not available")

    output_path = output_path or _default_output_path(
        manifest_path,
        case,
        sample_frame_offset,
        ik_tolerance,
        ik_max_iterations_cap,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pmx_data = PmxData().parse_file(str(pmx_path))
    model = MmdRuntimeModel.from_pmx_bytes(pmx_path.read_bytes())
    if model is None:
        raise RuntimeError("failed to create MmdRuntimeModel")

    clip = None
    instance = None
    try:
        clip = MmdRuntimeClip.from_vmd_bytes_for_model(model, vmd_path.read_bytes())
        if clip is None:
            raise RuntimeError("failed to create MmdRuntimeClip")

        instance = MmdRuntimeInstance.for_model(model)
        if instance is None:
            raise RuntimeError("failed to create MmdRuntimeInstance")

        runtime_library_path = get_runtime_library_path()
        with output_path.open("w", encoding="utf-8", newline="\n") as f:
            for frame in frames:
                evaluated_frame = float(frame) + sample_frame_offset
                if ik_tolerance is None and ik_max_iterations_cap is None:
                    evaluated = instance.evaluate_clip_frame(clip, evaluated_frame)
                else:
                    evaluated = instance.evaluate_clip_frame_with_ik_options(
                        clip,
                        evaluated_frame,
                        ik_tolerance=1.0e-2 if ik_tolerance is None else ik_tolerance,
                        ik_max_iterations_cap=0
                        if ik_max_iterations_cap is None
                        else ik_max_iterations_cap,
                    )
                if not evaluated:
                    raise RuntimeError(f"runtime evaluation failed at frame {evaluated_frame}")

                record = _make_record(
                    frame=frame,
                    evaluated_frame=evaluated_frame,
                    pmx_path=pmx_path,
                    vmd_path=vmd_path,
                    pmx_data=pmx_data,
                    world_matrices=instance.get_world_matrices() or [],
                    morph_weights=instance.get_morph_weights() or [],
                    ik_enabled=instance.get_ik_enabled() or [],
                    runtime_library_path=runtime_library_path,
                    ik_tolerance=ik_tolerance,
                    ik_max_iterations_cap=ik_max_iterations_cap,
                )
                f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                f.write("\n")
    finally:
        if instance is not None:
            instance.free()
        if clip is not None:
            clip.free()
        model.free()

    return output_path


def _matrix_delta(a: List[float], b: List[float]) -> float:
    if len(a) != len(b):
        return float("inf")
    return max((abs(float(x) - float(y)) for x, y in zip(a, b)), default=0.0)


def compare_oracle(
    actual_path: Path,
    oracle_path: Path,
    *,
    bone_names: Optional[set] = None,
    morph_names: Optional[set] = None,
    epsilon: Optional[float] = None,
) -> Dict[str, Any]:
    actual_records = {int(r["frame"]): r for r in _iter_jsonl(actual_path)}
    oracle_records = {int(r["frame"]): r for r in _iter_jsonl(oracle_path)}

    frames = sorted(set(actual_records) & set(oracle_records))
    summary: Dict[str, Any] = {
        "actual": str(actual_path),
        "oracle": str(oracle_path),
        "framesCompared": len(frames),
        "actualOnlyFrames": sorted(set(actual_records) - set(oracle_records)),
        "oracleOnlyFrames": sorted(set(oracle_records) - set(actual_records)),
        "maxWorldMatrixAbsDelta": 0.0,
        "maxMorphWeightAbsDelta": 0.0,
        "worstWorldMatrix": None,
        "worstMorphWeight": None,
        "epsilon": epsilon,
        "passed": None,
    }

    for frame in frames:
        actual_model = actual_records[frame]["models"][0]
        oracle_model = oracle_records[frame]["models"][0]
        actual_bones = {int(b["index"]): b for b in actual_model.get("bones", [])}
        oracle_bones = {int(b["index"]): b for b in oracle_model.get("bones", [])}

        for index in sorted(set(actual_bones) & set(oracle_bones)):
            if bone_names is not None and oracle_bones[index].get("name") not in bone_names:
                continue
            delta = _matrix_delta(
                actual_bones[index].get("worldMatrix", []),
                oracle_bones[index].get("worldMatrix", []),
            )
            if delta > summary["maxWorldMatrixAbsDelta"]:
                summary["maxWorldMatrixAbsDelta"] = delta
                summary["worstWorldMatrix"] = {
                    "frame": frame,
                    "index": index,
                    "name": oracle_bones[index].get("name"),
                    "delta": delta,
                }

        actual_morphs = {int(m["index"]): m for m in actual_model.get("morphs", [])}
        oracle_morphs = {int(m["index"]): m for m in oracle_model.get("morphs", [])}
        for index in sorted(set(actual_morphs) & set(oracle_morphs)):
            if morph_names is not None and oracle_morphs[index].get("name") not in morph_names:
                continue
            delta = abs(
                float(actual_morphs[index].get("weight", 0.0))
                - float(oracle_morphs[index].get("weight", 0.0))
            )
            if delta > summary["maxMorphWeightAbsDelta"]:
                summary["maxMorphWeightAbsDelta"] = delta
                summary["worstMorphWeight"] = {
                    "frame": frame,
                    "index": index,
                    "name": oracle_morphs[index].get("name"),
                    "delta": delta,
                }

    if epsilon is not None:
        summary["passed"] = (
            summary["maxWorldMatrixAbsDelta"] <= epsilon
            and summary["maxMorphWeightAbsDelta"] <= epsilon
        )

    return summary


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dump mmd-anim runtime results as GoldenOracle-compatible JSONL."
    )
    parser.add_argument("--manifest", required=True, help="GoldenOracle motion-numeric manifest path")
    parser.add_argument("--case", required=True, help="Manifest case name")
    parser.add_argument("--output", help="Output JSONL path. Defaults to runtime.actual.jsonl next to oracle.")
    parser.add_argument(
        "--sample-frame-offset",
        type=float,
        default=0.0,
        help="Evaluate runtime at manifest frame plus this offset, while keeping output frame labels unchanged.",
    )
    parser.add_argument("--compare-oracle", action="store_true", help="Compare output with manifest oracle path")
    parser.add_argument("--focus-only", action="store_true", help="Compare only manifest defaults.focus targets")
    parser.add_argument("--report-json", help="Optional path for comparison summary JSON")
    parser.add_argument("--ik-tolerance", type=float, help="Override runtime IK tolerance for diagnostics")
    parser.add_argument(
        "--ik-max-iterations-cap",
        type=int,
        help="Override runtime IK max iteration cap. 0 means no cap.",
    )
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest).resolve()
    output_path = Path(args.output).resolve() if args.output else None

    try:
        actual_path = dump_runtime_oracle(
            manifest_path=manifest_path,
            case_name=args.case,
            output_path=output_path,
            sample_frame_offset=args.sample_frame_offset,
            ik_tolerance=args.ik_tolerance,
            ik_max_iterations_cap=args.ik_max_iterations_cap,
        )
        print(f"runtime oracle written: {actual_path}")

        if args.compare_oracle:
            manifest = _load_json(manifest_path)
            case = _find_case(manifest, args.case)
            oracle_path = _resolve_manifest_path(manifest_path, case["oracle"]["path"])
            summary = compare_oracle(
                actual_path,
                oracle_path,
                bone_names=set(_focus_targets(manifest, case, "bones")) if args.focus_only else None,
                morph_names=set(_focus_targets(manifest, case, "morphs")) if args.focus_only else None,
                epsilon=case.get("compare", {}).get("epsilon"),
            )
            summary["focusOnly"] = bool(args.focus_only)
            text = json.dumps(summary, ensure_ascii=False, indent=2)
            print(text)
            if args.report_json:
                report_path = Path(args.report_json).resolve()
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(text + "\n", encoding="utf-8")

        return 0
    except Exception as exc:
        print(f"runtime oracle dump failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
