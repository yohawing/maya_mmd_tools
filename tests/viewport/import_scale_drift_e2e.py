"""Reproduce import-scale drift between joints and skinCluster bind matrices.

Run with mayapy. The default mode is an investigation reproducer: scale=1.0
must stay clean, while non-1.0 imports are expected to show bind/world drift.
Use ``--expect fixed`` after changing import scaling behavior.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import traceback
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


def _emit(payload: dict[str, Any], log_path: str | None = None) -> None:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    print(text)
    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8", errors="replace") as handle:
            handle.write(text + "\n")


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


def _matrix_distance(a, b) -> float:
    return math.sqrt(sum((float(a[i]) - float(b[i])) ** 2 for i in range(16)))


def _visible_mesh_transforms(cmds, root: str) -> list[str]:
    shapes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
    meshes: list[str] = []
    for shape in shapes:
        try:
            if cmds.getAttr(f"{shape}.intermediateObject"):
                continue
        except Exception:
            pass
        parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        if parents and parents[0] not in meshes:
            meshes.append(parents[0])
    return meshes


def _skin_clusters(cmds, root: str) -> list[str]:
    clusters: list[str] = []
    for mesh in _visible_mesh_transforms(cmds, root):
        for node in cmds.listHistory(mesh, pruneDagObjects=True) or []:
            if cmds.nodeType(node) == "skinCluster" and node not in clusters:
                clusters.append(node)
    return clusters


def _analyze_scale(pmx_path: Path, scale: float, parser_route: str) -> dict[str, Any]:
    import maya.api.OpenMaya as om
    import maya.cmds as cmds

    from mmd_tools.core import settings
    from mmd_tools.io.mmd_importer import import_mmd_file

    cmds.file(new=True, force=True)
    settings.set("import.model.create_mmd_shaders", False)
    root = import_mmd_file(
        str(pmx_path),
        options={
            "scale": scale,
            "import_physics": False,
            "use_namespace": False,
            "use_native_pmx_parse": parser_route == "native",
            "require_native_pmx_parse": parser_route == "native",
        },
    )
    if not root:
        raise RuntimeError(f"PMX import failed: {pmx_path}")

    samples: list[dict[str, Any]] = []
    max_bind_world_delta = 0.0
    max_translate_delta = 0.0
    cluster_count = 0
    for skin_cluster in _skin_clusters(cmds, root):
        cluster_count += 1
        influences = cmds.skinCluster(skin_cluster, query=True, influence=True) or []
        for logical_index, joint in enumerate(influences):
            if not cmds.objExists(joint):
                continue
            try:
                bind_pre = om.MMatrix(cmds.getAttr(f"{skin_cluster}.bindPreMatrix[{logical_index}]"))
                bind_world = bind_pre.inverse()
                world = om.MMatrix(cmds.getAttr(f"{joint}.worldMatrix[0]"))
            except Exception:
                continue
            matrix_delta = _matrix_distance(bind_world, world)
            translate_delta = math.sqrt(sum((float(bind_world[12 + i]) - float(world[12 + i])) ** 2 for i in range(3)))
            max_bind_world_delta = max(max_bind_world_delta, matrix_delta)
            max_translate_delta = max(max_translate_delta, translate_delta)
            if len(samples) < 8 and translate_delta > 1.0e-6:
                samples.append(
                    {
                        "joint": joint,
                        "skinCluster": skin_cluster,
                        "logicalIndex": logical_index,
                        "matrixDelta": round(matrix_delta, 6),
                        "translateDelta": round(translate_delta, 6),
                        "bindWorldTranslate": [round(float(bind_world[12 + i]), 6) for i in range(3)],
                        "jointWorldTranslate": [round(float(world[12 + i]), 6) for i in range(3)],
                    }
                )

    return {
        "parser": parser_route,
        "scale": scale,
        "root": root,
        "rootScale": [round(float(value), 6) for value in cmds.getAttr(f"{root}.scale")[0]],
        "skinClusterCount": cluster_count,
        "maxBindWorldDelta": round(max_bind_world_delta, 6),
        "maxTranslateDelta": round(max_translate_delta, 6),
        "samples": samples,
    }


def run(
    pmx_path: Path,
    scales: list[float],
    *,
    parsers: list[str],
    clean_threshold: float,
    drift_threshold: float,
    expect: str,
    log_path: str | None = None,
) -> dict[str, Any]:
    _repo_imports()
    results = [
        _analyze_scale(pmx_path, scale, parser_route)
        for parser_route in parsers
        for scale in scales
    ]
    baseline_failures = [
        item
        for item in results
        if abs(item["scale"] - 1.0) <= 1.0e-9 and item["maxBindWorldDelta"] > clean_threshold
    ]
    scaled_drifts = [
        item
        for item in results
        if abs(item["scale"] - 1.0) > 1.0e-9 and item["maxBindWorldDelta"] > drift_threshold
    ]
    scaled_clean_failures = [
        item
        for item in results
        if abs(item["scale"] - 1.0) > 1.0e-9 and item["maxBindWorldDelta"] > clean_threshold
    ]

    if baseline_failures:
        status = "fail"
        reason = "baseline scale=1.0 import has bind/world drift"
    elif expect == "drift" and not scaled_drifts:
        status = "fail"
        reason = "non-1.0 import did not reproduce bind/world drift"
    elif expect == "fixed" and scaled_clean_failures:
        status = "fail"
        reason = "non-1.0 import still has bind/world drift"
    else:
        status = "pass"
        reason = "drift reproduced" if expect == "drift" else "scaled imports are clean"

    payload = {
        "status": status,
        "reason": reason,
        "expect": expect,
        "cleanThreshold": clean_threshold,
        "driftThreshold": drift_threshold,
        "results": results,
    }
    _emit(payload, log_path)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=str(ROOT / "tests/data/mmt_test_model.pmx"))
    parser.add_argument("--scale", action="append", type=float, default=[1.0, 2.0, 0.5])
    parser.add_argument("--parser", choices=["legacy", "native", "both"], default="both")
    parser.add_argument("--expect", choices=["drift", "fixed"], default="drift")
    parser.add_argument("--clean-threshold", type=float, default=1.0e-4)
    parser.add_argument("--drift-threshold", type=float, default=1.0)
    parser.add_argument("--log")
    args = parser.parse_args()

    initialized = False
    try:
        initialized = _initialize_maya()
        parsers = ["legacy", "native"] if args.parser == "both" else [args.parser]
        result = run(
            Path(args.model).resolve(),
            args.scale,
            parsers=parsers,
            clean_threshold=args.clean_threshold,
            drift_threshold=args.drift_threshold,
            expect=args.expect,
            log_path=args.log,
        )
        return 0 if result["status"] == "pass" else 1
    except Exception:
        _emit({"status": "error", "traceback": traceback.format_exc()}, args.log)
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
