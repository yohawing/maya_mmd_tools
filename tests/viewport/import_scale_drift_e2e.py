"""Accept the legacy/native PMX mesh, joint, and skin import scale contract.

Run with mayapy.  The default acceptance matrix imports scales ``0.5``, ``1.0``
and ``1.5`` through both parser routes.  Each import must retain its requested
``mmd_import_scale``, keep the model root at identity scale, produce visible
skinned geometry, and keep ``bindPreMatrix`` aligned with its influence joints.
World bounds and influence-joint positions are normalized by the requested
scale before comparison.  The normalized comparison tolerance is deliberately
small (``LINEARITY_TOLERANCE``) and is reported in the JSON output.
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
DEFAULT_SCALES = (0.5, 1.0, 1.5)
IDENTITY_SCALE_TOLERANCE = 1.0e-6
SCALE_MATCH_TOLERANCE = 1.0e-6
LINEARITY_TOLERANCE = 1.0e-5


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


def _finite_matrix_values(value) -> bool:
    """Return whether a Maya matrix payload has 16 finite numeric values."""
    try:
        values = [float(component) for component in value]
    except (TypeError, ValueError, OverflowError):
        return False
    return len(values) == 16 and all(math.isfinite(component) for component in values)


def _finite_matrix(matrix) -> bool:
    """Return whether an OpenMaya matrix contains only finite values."""
    try:
        return all(math.isfinite(float(matrix[index])) for index in range(16))
    except (IndexError, TypeError, ValueError, OverflowError):
        return False


def _vector_values(value, size: int) -> list[float]:
    """Flatten Maya's one-element tuple wrapper around vector attributes."""
    while isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], (list, tuple)):
        value = value[0]
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise ValueError(f"expected a vector with {size} values, got {value!r}")
    return [float(component) for component in value]


def _normalized_samples(samples, scale: float) -> list[list[float]] | None:
    """Return scale-normalized, sorted samples for order-independent comparison."""
    if not scale or not math.isfinite(scale):
        return None
    normalized = []
    for sample in samples:
        try:
            values = [float(value) / scale for value in sample]
        except (TypeError, ValueError, OverflowError):
            return None
        if not all(math.isfinite(value) for value in values):
            return None
        normalized.append(values)
    return sorted(normalized)


def _sample_linear_error(first, first_scale: float, second, second_scale: float) -> float | None:
    """Return max normalized error, or ``None`` when sample topology differs."""
    first_normalized = _normalized_samples(first, first_scale)
    second_normalized = _normalized_samples(second, second_scale)
    if first_normalized is None or second_normalized is None:
        return math.inf
    if len(first_normalized) != len(second_normalized):
        return None
    if not first_normalized:
        return 0.0
    return max(
        abs(a - b)
        for first_sample, second_sample in zip(first_normalized, second_normalized)
        for a, b in zip(first_sample, second_sample)
    )


def resolve_scales(values=None) -> list[float]:
    """Resolve explicit ``--scale`` values, or return the acceptance defaults."""
    if values:
        return [float(value) for value in values]
    return list(DEFAULT_SCALES)


def _coerce_tolerance(value):
    """Return a finite non-negative tolerance, or ``None`` when invalid."""
    try:
        tolerance = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return tolerance if math.isfinite(tolerance) and tolerance >= 0.0 else None


def _finite_samples(samples, sample_size: int) -> bool:
    """Return whether every recorded sample has the expected finite shape."""
    if not samples:
        return False
    try:
        return all(
            len(sample) == sample_size
            and all(math.isfinite(float(value)) for value in sample)
            for sample in samples
        )
    except (TypeError, ValueError, OverflowError):
        return False


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


def _skin_clusters(cmds, meshes: list[str]) -> list[str]:
    clusters: list[str] = []
    for mesh in meshes:
        for node in cmds.listHistory(mesh, pruneDagObjects=True) or []:
            if cmds.nodeType(node) == "skinCluster" and node not in clusters:
                clusters.append(node)
    return clusters


def _analyze_scale(pmx_path: Path, scale: float, parser_route: str) -> dict[str, Any]:
    import maya.api.OpenMaya as om
    import maya.cmds as cmds

    from mmd_tools.core.constants import ATTR_MMD_IMPORT_SCALE
    from mmd_tools.core import settings
    from mmd_tools.io.mmd_importer import import_mmd_file

    scale = float(scale)
    if not math.isfinite(scale) or scale <= 0.0:
        raise ValueError("scale must be a finite positive number")

    cmds.file(new=True, force=True)
    settings.set("import.model.create_mmd_shaders", False)
    root = import_mmd_file(
        str(pmx_path),
        options={
            "scale": scale,
            "import_physics": False,
            "setup_rig": False,
            "use_namespace": False,
            "use_native_pmx_parse": parser_route == "native",
            "require_native_pmx_parse": parser_route == "native",
        },
    )
    if not root:
        raise RuntimeError(f"PMX import failed: {pmx_path}")

    root_import_scale = None
    try:
        root_import_scale = float(cmds.getAttr(f"{root}.{ATTR_MMD_IMPORT_SCALE}"))
    except Exception:
        pass
    root_import_scale_matches = (
        root_import_scale is not None
        and math.isfinite(root_import_scale)
        and math.isclose(
            root_import_scale,
            scale,
            rel_tol=0.0,
            abs_tol=SCALE_MATCH_TOLERANCE,
        )
    )

    root_scale = _vector_values(cmds.getAttr(f"{root}.scale"), 3)
    visible_meshes = _visible_mesh_transforms(cmds, root)
    mesh_world_bounds = []
    invalid_mesh_bounds_count = 0
    for mesh in visible_meshes:
        try:
            bounds = [float(value) for value in cmds.exactWorldBoundingBox(mesh)]
        except (TypeError, ValueError, OverflowError, RuntimeError):
            invalid_mesh_bounds_count += 1
            continue
        if len(bounds) != 6 or not all(math.isfinite(value) for value in bounds):
            invalid_mesh_bounds_count += 1
            continue
        mesh_world_bounds.append(bounds)

    samples: list[dict[str, Any]] = []
    influence_joint_world_positions: list[list[float]] = []
    seen_influences: set[str] = set()
    max_bind_world_delta = 0.0
    max_translate_delta = 0.0
    requested_influence_count = 0
    invalid_influence_count = 0
    skin_clusters = _skin_clusters(cmds, visible_meshes)
    for skin_cluster in skin_clusters:
        try:
            influences = cmds.skinCluster(skin_cluster, query=True, influence=True) or []
        except (RuntimeError, TypeError, ValueError, OverflowError):
            invalid_influence_count += 1
            continue
        for logical_index, joint in enumerate(influences):
            requested_influence_count += 1
            try:
                influence_exists = cmds.objExists(joint)
            except (RuntimeError, TypeError, ValueError, OverflowError):
                influence_exists = False
            if not influence_exists:
                invalid_influence_count += 1
                continue
            try:
                bind_pre_values = cmds.getAttr(f"{skin_cluster}.bindPreMatrix[{logical_index}]")
                world_values = cmds.getAttr(f"{joint}.worldMatrix[0]")
                if not _finite_matrix_values(bind_pre_values) or not _finite_matrix_values(world_values):
                    invalid_influence_count += 1
                    continue
                bind_pre = om.MMatrix(bind_pre_values)
                bind_world = bind_pre.inverse()
                world = om.MMatrix(world_values)
                if not _finite_matrix(bind_world) or not _finite_matrix(world):
                    invalid_influence_count += 1
                    continue
            except (RuntimeError, TypeError, ValueError, OverflowError, IndexError):
                invalid_influence_count += 1
                continue
            if joint not in seen_influences:
                seen_influences.add(joint)
                influence_joint_world_positions.append(
                    [float(world[12 + axis]) for axis in range(3)]
                )
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
        "rootImportScale": root_import_scale,
        "rootImportScaleMatches": root_import_scale_matches,
        "rootScale": [round(value, 9) for value in root_scale],
        "visibleMeshCount": len(visible_meshes),
        "invalidMeshBoundsCount": invalid_mesh_bounds_count,
        "meshWorldBounds": [[round(value, 9) for value in bounds] for bounds in mesh_world_bounds],
        "skinClusterCount": len(skin_clusters),
        "requestedInfluenceCount": requested_influence_count,
        "invalidInfluenceCount": invalid_influence_count,
        "influenceJointCount": len(influence_joint_world_positions),
        "influenceJointWorldPositions": [
            [round(value, 9) for value in position]
            for position in influence_joint_world_positions
        ],
        # Keep enough precision for the acceptance threshold; six decimal
        # places could round a just-over-threshold drift down to a false pass.
        "maxBindWorldDelta": round(max_bind_world_delta, 9),
        "maxTranslateDelta": round(max_translate_delta, 9),
        "samples": samples,
    }


def _validate_result(item: dict[str, Any], clean_threshold: float) -> list[str]:
    """Validate the required mesh/joint/skin witnesses for one import."""
    failures = []
    clean_value = _coerce_tolerance(clean_threshold)
    if clean_value is None:
        failures.append("clean threshold must be finite and non-negative")
        clean_value = 0.0
    expected_scale = float(item.get("scale", 0.0))
    persisted_scale = item.get("rootImportScale")
    if not item.get("rootImportScaleMatches", False):
        failures.append(
            "persisted root mmd_import_scale does not match requested "
            f"scale ({persisted_scale!r} != {expected_scale!r})"
        )

    root_scale = item.get("rootScale") or []
    try:
        root_scale_values = [float(value) for value in root_scale]
    except (TypeError, ValueError, OverflowError):
        root_scale_values = []
    if len(root_scale_values) != 3 or any(
        not math.isfinite(value) or abs(value - 1.0) > IDENTITY_SCALE_TOLERANCE
        for value in root_scale_values
    ):
        failures.append(f"model root transform scale is not identity: {root_scale!r}")

    if int(item.get("visibleMeshCount", 0)) < 1:
        failures.append("import produced no visible mesh")
    if int(item.get("invalidMeshBoundsCount", 0)) > 0:
        failures.append(
            f"{item['invalidMeshBoundsCount']} visible mesh world bounds are invalid"
        )
    if not item.get("meshWorldBounds"):
        failures.append("visible mesh world bounds are unavailable")
    elif not _finite_samples(item["meshWorldBounds"], 6):
        failures.append("visible mesh world bounds contain non-finite data")
    if int(item.get("skinClusterCount", 0)) < 1:
        failures.append("import produced no skinCluster")
    if int(item.get("invalidInfluenceCount", 0)) > 0:
        failures.append(
            f"{item['invalidInfluenceCount']} skin influences have invalid bind/world data"
        )
    if int(item.get("influenceJointCount", 0)) < 1:
        failures.append("skinCluster produced no influence-joint witness")
    elif not _finite_samples(item.get("influenceJointWorldPositions"), 3):
        failures.append("influence-joint world positions contain non-finite data")
    try:
        max_bind_world_delta = float(item.get("maxBindWorldDelta", math.inf))
    except (TypeError, ValueError, OverflowError):
        max_bind_world_delta = math.inf
    if not math.isfinite(max_bind_world_delta):
        failures.append("maxBindWorldDelta is non-finite")
    elif max_bind_world_delta > clean_value:
        failures.append(
            "skinCluster bindPreMatrix/world mismatch exceeds "
            f"clean threshold ({item.get('maxBindWorldDelta')!r} > {clean_value!r})"
        )
    return failures


def _linear_failures(
    results: list[dict[str, Any]],
    *,
    tolerance: float = LINEARITY_TOLERANCE,
) -> list[str]:
    """Check scale-normalized bounds and joint positions within each parser route."""
    tolerance_value = _coerce_tolerance(tolerance)
    if tolerance_value is None:
        return ["linearity tolerance must be finite and non-negative"]
    failures = []
    routes = sorted({str(item.get("parser", "")) for item in results})
    for parser_route in routes:
        route_results = [item for item in results if item.get("parser") == parser_route]
        for index, first in enumerate(route_results):
            for second in route_results[index + 1 :]:
                for field, label in (
                    ("meshWorldBounds", "mesh world bounds"),
                    ("influenceJointWorldPositions", "influence-joint world positions"),
                ):
                    first_samples = first.get(field) or []
                    second_samples = second.get(field) or []
                    error = _sample_linear_error(
                        first_samples,
                        float(first.get("scale", 0.0)),
                        second_samples,
                        float(second.get("scale", 0.0)),
                    )
                    if error is None:
                        failures.append(
                            f"{parser_route} {label} sample count differs at "
                            f"scales {first.get('scale')} and {second.get('scale')}"
                        )
                    elif error > tolerance_value:
                        failures.append(
                            f"{parser_route} {label} are not linear across scales "
                            f"{first.get('scale')} and {second.get('scale')} "
                            f"(normalized error {error!r} > {tolerance_value!r})"
                        )
    return failures


def evaluate_results(
    results: list[dict[str, Any]],
    *,
    clean_threshold: float,
    linearity_tolerance: float = LINEARITY_TOLERANCE,
) -> dict[str, Any]:
    """Aggregate per-import and cross-scale acceptance checks fail-closed."""
    failures = []
    clean_value = _coerce_tolerance(clean_threshold)
    linearity_value = _coerce_tolerance(linearity_tolerance)
    if clean_value is None:
        failures.append("clean threshold must be finite and non-negative")
        clean_value = 0.0
    if linearity_value is None:
        failures.append("linearity tolerance must be finite and non-negative")
        linearity_value = 0.0
    for item in results:
        item_failures = list(item.get("failures") or [])
        if item.get("error"):
            item_failures.append(str(item["error"]))
        item_failures.extend(_validate_result(item, clean_value))
        item["failures"] = item_failures
        item["status"] = "pass" if not item_failures else "fail"
        failures.extend(
            f"{item.get('parser')} scale={item.get('scale')}: {failure}"
            for failure in item_failures
        )
    failures.extend(_linear_failures(results, tolerance=linearity_value))
    return {
        "status": "pass" if not failures and results else "fail",
        "reason": "all import-scale acceptance checks passed" if not failures else "; ".join(failures),
        "failures": failures,
    }


def run(
    pmx_path: Path,
    scales: list[float],
    *,
    parsers: list[str],
    clean_threshold: float,
    expect: str = "fixed",
    linearity_tolerance: float = LINEARITY_TOLERANCE,
    log_path: str | None = None,
) -> dict[str, Any]:
    """Run and aggregate the mayapy import-scale acceptance matrix."""
    if expect != "fixed":
        raise ValueError("import-scale acceptance runner only supports --expect fixed")
    _repo_imports()
    results = []
    for parser_route in parsers:
        for scale in scales:
            try:
                results.append(_analyze_scale(pmx_path, scale, parser_route))
            except Exception as exc:
                results.append(
                    {
                        "parser": parser_route,
                        "scale": scale,
                        "status": "error",
                        "error": f"import analysis failed: {exc}",
                    }
                )
    evaluation = evaluate_results(
        results,
        clean_threshold=clean_threshold,
        linearity_tolerance=linearity_tolerance,
    )
    payload = {
        **evaluation,
        "expect": expect,
        "cleanThreshold": clean_threshold,
        "linearityTolerance": linearity_tolerance,
        "results": results,
    }
    _emit(payload, log_path)
    return payload


def parse_args(argv=None):
    """Parse CLI arguments without initializing Maya (unit-test friendly)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=str(ROOT / "tests/data/mmt_test_model.pmx"))
    parser.add_argument(
        "--scale",
        action="append",
        type=float,
        default=None,
        help="Import scale (repeatable); defaults to 0.5, 1.0, and 1.5.",
    )
    parser.add_argument("--parser", choices=["legacy", "native", "both"], default="both")
    parser.add_argument("--expect", choices=["fixed"], default="fixed")
    parser.add_argument("--clean-threshold", type=float, default=1.0e-4)
    parser.add_argument(
        "--linearity-tolerance",
        type=float,
        default=LINEARITY_TOLERANCE,
        help="Absolute tolerance after scale normalization.",
    )
    parser.add_argument("--log")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()

    initialized = False
    try:
        initialized = _initialize_maya()
        parsers = ["legacy", "native"] if args.parser == "both" else [args.parser]
        result = run(
            Path(args.model).resolve(),
            resolve_scales(args.scale),
            parsers=parsers,
            clean_threshold=args.clean_threshold,
            expect=args.expect,
            linearity_tolerance=args.linearity_tolerance,
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
