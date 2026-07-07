"""Compare Maya Bullet physics bone positions against Golden Oracle (MMD 9.32).

Run under mayapy. Imports PMX + VMD with Bullet physics, steps through frames,
dumps bone worldMatrix, then compares against oracle JSONL from MMD 9.32.

Coordinate note: the PMX importer converts MMD coordinates (left-handed, Z-forward)
to Maya coordinates (right-handed, Z-backward) during import, so Maya joint
worldMatrix values are in Maya's coordinate system. The oracle stores MMD-native
coordinates. Translation distances include this baseline offset (~5mm at frame 0
for typical models) plus physics solver divergence.

Thresholds: scale error > 0.01 or max translation > 50mm triggers failure.
The 50mm envelope is intentionally loose — Bullet and MMD use different solvers,
so exact match is not expected for secondary motion chains.

Usage:
    mayapy tests/viewport/physics_oracle_compare.py --manifest <oracle-batch.json>
    mayapy tests/viewport/physics_oracle_compare.py --manifest <oracle-batch.json> --case sour-miku-rabbithole-physics
    mayapy tests/viewport/physics_oracle_compare.py --manifest <oracle-batch.json> --out build/reports/physics_oracle.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "build" / "reports" / "physics_oracle_compare.json"

PHYSICS_KEYWORDS = ["髪", "スカート", "胸", "ネクタイ", "袖", "リボン"]


def _initialize_maya() -> bool:
    import maya.standalone

    try:
        maya.standalone.initialize(name="python")
        return True
    except RuntimeError:
        return False


def _repo_imports() -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def _dist3(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a[:3], b[:3])))


def _extract_translation(wm: list[float]) -> list[float]:
    return [wm[12], wm[13], wm[14]]


def _extract_scale(wm: list[float]) -> list[float]:
    sx = math.sqrt(wm[0] ** 2 + wm[1] ** 2 + wm[2] ** 2)
    sy = math.sqrt(wm[4] ** 2 + wm[5] ** 2 + wm[6] ** 2)
    sz = math.sqrt(wm[8] ** 2 + wm[9] ** 2 + wm[10] ** 2)
    return [sx, sy, sz]


def _classify_bone(name: str) -> str:
    for kw in PHYSICS_KEYWORDS:
        if kw in name:
            return kw
    return "kinematic"


def _load_oracle(path: Path) -> dict[int, dict[str, list[float]]]:
    result: dict[int, dict[str, list[float]]] = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            frame = rec["frame"]
            bones: dict[str, list[float]] = {}
            for b in rec["models"][0]["bones"]:
                bones[b["name"]] = b["worldMatrix"]
            result[frame] = bones
    return result


def _get_bone_world_matrix(cmds: Any, joint: str) -> list[float]:
    wm = cmds.getAttr(f"{joint}.worldMatrix[0]")
    if isinstance(wm, (list, tuple)) and len(wm) == 1:
        wm = wm[0]
    if isinstance(wm, (list, tuple)) and len(wm) == 16:
        return [float(v) for v in wm]
    flat: list[float] = []
    for row in wm:
        if isinstance(row, (list, tuple)):
            flat.extend(float(v) for v in row)
        else:
            flat.append(float(row))
    return flat


SCALE_ERROR_THRESHOLD = 0.01
MAX_ENVELOPE_THRESHOLD = 50.0


def _resolve_oracle_path(case: dict[str, Any], oracle_base: Path) -> Path:
    oracle_rel = case.get("oracle", {}).get("path", "")
    if oracle_rel:
        resolved = (oracle_base / oracle_rel).resolve()
        if resolved.exists():
            return resolved
    return oracle_base / case["name"] / "oracle.actual.jsonl"


def _run_case(
    cmds: Any,
    case: dict[str, Any],
    oracle_base: Path,
    dgdirty: bool,
) -> dict[str, Any]:
    from mmd_tools.io.mmd_importer import import_mmd_file

    model_path = case["assets"]["model"]
    motion_path = case["assets"]["motion"]
    oracle_name = case["name"]
    oracle_path = _resolve_oracle_path(case, oracle_base)
    sample_frames: list[int] = case.get("frames", [0, 30, 60, 120, 180])

    if not oracle_path.exists():
        return {"name": oracle_name, "status": "skip", "reason": f"oracle not found: {oracle_path}"}

    oracle = _load_oracle(oracle_path)
    oracle_frames = sorted(oracle.keys())
    frames_to_compare = [f for f in sample_frames if f in oracle]
    if not frames_to_compare:
        return {"name": oracle_name, "status": "skip", "reason": "no overlapping frames"}

    print(f"\n=== Case: {oracle_name} ===", file=sys.stderr)
    print(f"  Model: {model_path}", file=sys.stderr)
    print(f"  Motion: {motion_path}", file=sys.stderr)
    print(f"  Oracle frames: {oracle_frames}", file=sys.stderr)
    print(f"  Comparing frames: {frames_to_compare}", file=sys.stderr)

    os.environ["MMD_TOOLS_SKIP_SHADER_OVERRIDE"] = "1"
    cmds.file(new=True, force=True)

    root = import_mmd_file(
        str(model_path),
        options={
            "import_physics": True,
            "create_physics_joints": True,
            "create_mmd_shaders": False,
            "use_namespace": False,
            "cpp_fast_load": False,
        },
    )
    if not root:
        return {"name": oracle_name, "status": "error", "reason": "PMX import failed"}

    root_long = (cmds.ls(root, long=True) or [root])[0]

    if motion_path:
        print(f"  Importing VMD motion: {motion_path}", file=sys.stderr)
        import_mmd_file(
            str(motion_path),
            options={
                "target_model": root,
                "bake_mode": True,
            },
        )

    all_joints = cmds.listRelatives(root_long, allDescendents=True, type="joint", fullPath=True) or []
    bone_map: dict[str, str] = {}
    for j in all_joints:
        if cmds.attributeQuery("mmd_bone_name", node=j, exists=True):
            name = cmds.getAttr(f"{j}.mmd_bone_name") or ""
            if name:
                bone_map[name] = j

    max_frame = max(frames_to_compare)
    cmds.playbackOptions(
        min=0,
        max=max_frame + 10,
        animationStartTime=0,
        animationEndTime=max_frame + 10,
    )

    per_frame_results: dict[str, dict[str, Any]] = {}
    category_stats: dict[str, dict[str, float]] = {}

    for frame in frames_to_compare:
        cmds.currentTime(frame, edit=True)
        if dgdirty:
            cmds.dgdirty(allPlugs=True)
            cmds.currentTime(frame, edit=True)

        oracle_bones = oracle[frame]
        frame_diffs: list[dict[str, Any]] = []

        for bone_name, oracle_wm in oracle_bones.items():
            if bone_name not in bone_map:
                continue

            maya_wm = _get_bone_world_matrix(cmds, bone_map[bone_name])

            oracle_t = _extract_translation(oracle_wm)
            maya_t = _extract_translation(maya_wm)
            t_dist = _dist3(oracle_t, maya_t)

            maya_scale = _extract_scale(maya_wm)
            scale_err = abs(maya_scale[0] - 1) + abs(maya_scale[1] - 1) + abs(maya_scale[2] - 1)

            category = _classify_bone(bone_name)

            diff_entry = {
                "name": bone_name,
                "category": category,
                "translationDist": round(t_dist, 6),
                "scaleError": round(scale_err, 6),
            }
            frame_diffs.append(diff_entry)

            if category != "kinematic":
                stats = category_stats.setdefault(category, {
                    "maxTransDist": 0.0,
                    "sumTransDist": 0.0,
                    "count": 0,
                    "maxScaleErr": 0.0,
                })
                stats["maxTransDist"] = max(stats["maxTransDist"], t_dist)
                stats["sumTransDist"] += t_dist
                stats["count"] += 1
                stats["maxScaleErr"] = max(stats["maxScaleErr"], scale_err)

        per_frame_results[str(frame)] = {
            "matched": len(frame_diffs),
            "unmatched": len(oracle_bones) - len([d for d in frame_diffs]),
            "physics": [d for d in frame_diffs if d["category"] != "kinematic"],
            "topDiffs": sorted(frame_diffs, key=lambda d: -d["translationDist"])[:20],
        }

        physics_count = len([d for d in frame_diffs if d["category"] != "kinematic"])
        max_diff = max((d["translationDist"] for d in frame_diffs), default=0)
        print(f"  Frame {frame}: {len(frame_diffs)} matched, physics={physics_count}, maxDiff={max_diff:.4f}", file=sys.stderr)

    category_summary = {}
    for cat, stats in category_stats.items():
        avg = stats["sumTransDist"] / stats["count"] if stats["count"] else 0
        category_summary[cat] = {
            "maxTransDist": round(stats["maxTransDist"], 6),
            "avgTransDist": round(avg, 6),
            "maxScaleErr": round(stats["maxScaleErr"], 6),
            "sampleCount": stats["count"],
        }

    max_scale_err = max((s["maxScaleErr"] for s in category_stats.values()), default=0.0)
    max_trans_dist = max((s["maxTransDist"] for s in category_stats.values()), default=0.0)
    failures: list[str] = []
    if max_scale_err > SCALE_ERROR_THRESHOLD:
        failures.append(f"scale_error {max_scale_err:.4f} > {SCALE_ERROR_THRESHOLD}")
    if max_trans_dist > MAX_ENVELOPE_THRESHOLD:
        failures.append(f"max_translation {max_trans_dist:.2f} > {MAX_ENVELOPE_THRESHOLD}")

    status = "fail" if failures else "pass"

    return {
        "name": oracle_name,
        "status": status,
        "failures": failures if failures else None,
        "model": str(model_path),
        "motion": str(motion_path),
        "bonesMapped": len(bone_map),
        "oracleBones": len(oracle[frames_to_compare[0]]),
        "framesCompared": frames_to_compare,
        "categorySummary": category_summary,
        "perFrame": per_frame_results,
    }


def run(*, manifest_path: Path, out: Path, case_filter: str | None, dgdirty: bool) -> dict[str, Any]:
    _repo_imports()
    import maya.cmds as cmds

    from mmd_tools.converters import PhysicsConverter

    if not PhysicsConverter.is_bullet_available():
        payload = {"status": "skip", "reason": "Bullet plugin unavailable"}
        return payload

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    oracle_base = manifest_path.parent

    cases = manifest["cases"]
    if case_filter:
        cases = [c for c in cases if c["name"] == case_filter]
        if not cases:
            return {"status": "error", "reason": f"case '{case_filter}' not found"}

    results: list[dict[str, Any]] = []
    for case in cases:
        try:
            result = _run_case(cmds, case, oracle_base, dgdirty)
            results.append(result)
        except Exception:
            results.append({
                "name": case["name"],
                "status": "error",
                "exception": traceback.format_exc(),
            })

    payload = {
        "status": "pass" if all(r.get("status") == "pass" for r in results) else "partial",
        "manifest": str(manifest_path),
        "caseCount": len(results),
        "results": results,
    }

    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n", encoding="utf-8")
    print(f"\nReport written to {out}", file=sys.stderr)

    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="oracle-batch.json path")
    parser.add_argument("--case", default=None, help="Run only this case name")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--dgdirty", action="store_true")
    args = parser.parse_args()

    initialized = False
    try:
        initialized = _initialize_maya()
        payload = run(
            manifest_path=Path(args.manifest).resolve(),
            out=Path(args.out).resolve(),
            case_filter=args.case,
            dgdirty=args.dgdirty,
        )
        return 0 if payload.get("status") in {"pass", "skip"} else 1
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        if initialized:
            try:
                import maya.standalone
                maya.standalone.uninitialize()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
