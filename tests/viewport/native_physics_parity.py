"""Pure comparison helpers for Maya Bullet versus native physics captures."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any


def _distance(left: list[float], right: list[float]) -> float:
    return math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(left, right)))


def static_extent_from_positions(positions: list[Any]) -> float:
    """Return a finite, non-zero diagonal from static model-space vertices."""
    finite: list[tuple[float, float, float]] = []
    for position in positions:
        try:
            point = tuple(float(position[index]) for index in range(3))
        except (IndexError, TypeError, ValueError):
            continue
        if all(math.isfinite(value) for value in point):
            finite.append(point)
    if not finite:
        raise ValueError("PMX has no finite static vertex positions")
    ranges = [max(point[axis] for point in finite) - min(point[axis] for point in finite) for axis in range(3)]
    extent = math.sqrt(sum(value * value for value in ranges))
    if not math.isfinite(extent) or extent <= 1.0e-6:
        raise ValueError(f"PMX static vertex extent is invalid: {extent!r}")
    return extent


def static_pmx_extent(pmx_path: Path) -> float:
    """Parse PMX and derive the threshold extent before Maya physics exists."""
    from mmd_tools.core.mmd_parser import parse_pmx_file

    pmx = parse_pmx_file(str(pmx_path))
    vertices = list(getattr(pmx, "vertices", []) or [])
    return static_extent_from_positions([getattr(vertex, "position", None) for vertex in vertices])


def apply_import_scale(static_extent: float, import_scale: float) -> float:
    """Convert PMX model-space extent to the Maya import scene scale."""
    extent = float(static_extent)
    scale = float(import_scale)
    if not math.isfinite(extent) or extent <= 1.0e-6:
        raise ValueError(f"PMX static vertex extent is invalid: {extent!r}")
    if not math.isfinite(scale) or scale <= 0.0:
        raise ValueError(f"PMX import scale must be finite and positive: {scale!r}")
    scaled = extent * abs(scale)
    if not math.isfinite(scaled) or scaled <= 1.0e-6:
        raise ValueError(f"scaled PMX static extent is invalid: {scaled!r}")
    return scaled


def compare_bullet_world_sanity(
    baseline: dict[str, dict[str, dict[str, Any]]],
    native: dict[str, dict[str, dict[str, Any]]],
    frames: list[int],
    model_extent: float,
) -> dict[str, Any]:
    """Reject gross Maya Bullet world-space explosions relative to native.

    Thresholds deliberately scale with the imported model extent.  This is a
    catastrophic-regression gate, not a tight solver-parity check: ordinary
    differences between Bullet implementations remain allowed.
    """
    extent = max(abs(float(model_extent)), 1.0e-6)
    displacement_limit = extent * 2.0
    absolute_separation_limit = extent * 2.0
    relative_separation_slack = extent * 0.25
    relative_separation_factor = 4.0
    compared = 0
    failures: list[dict[str, Any]] = []
    worst: dict[str, Any] | None = None
    worst_ratio = 0.0

    for bone_index in sorted(set(baseline) & set(native), key=lambda value: int(value)):
        for frame in frames:
            frame_key = str(frame)
            bullet_sample = baseline[bone_index].get(frame_key)
            native_sample = native[bone_index].get(frame_key)
            if not bullet_sample or not native_sample:
                continue
            compared += 1
            finite = bool(bullet_sample.get("finite")) and bool(native_sample.get("finite"))
            if finite:
                bullet_world = bullet_sample["worldTranslate"]
                native_world = native_sample["worldTranslate"]
                bullet_parent = bullet_sample["parentWorldTranslate"]
                native_parent = native_sample["parentWorldTranslate"]
                displacement = _distance(bullet_world, native_world)
                bullet_separation = _distance(bullet_world, bullet_parent)
                native_separation = _distance(native_world, native_parent)
            else:
                displacement = math.inf
                bullet_separation = math.inf
                native_separation = math.inf

            separation_limit = max(
                absolute_separation_limit,
                native_separation * relative_separation_factor + relative_separation_slack,
            )
            displacement_ratio = displacement / displacement_limit
            separation_ratio = bullet_separation / separation_limit
            ratio = max(displacement_ratio, separation_ratio)
            evidence = {
                "boneIndex": int(bone_index),
                "frame": int(frame),
                "joint": bullet_sample.get("joint"),
                "finite": finite,
                "bulletWorldTranslate": bullet_sample.get("worldTranslate"),
                "nativeWorldTranslate": native_sample.get("worldTranslate"),
                "bulletParentWorldTranslate": bullet_sample.get("parentWorldTranslate"),
                "nativeParentWorldTranslate": native_sample.get("parentWorldTranslate"),
                "bulletParentSeparation": bullet_separation,
                "nativeParentSeparation": native_separation,
                "worldDisplacement": displacement,
                "displacementLimit": displacement_limit,
                "separationLimit": separation_limit,
                "ratio": ratio,
            }
            if worst is None or ratio > worst_ratio:
                worst = evidence
                worst_ratio = ratio
            if not finite or displacement > displacement_limit or bullet_separation > separation_limit:
                failures.append(evidence)

    return {
        "passed": compared > 0 and not failures,
        "modelExtent": extent,
        "comparedSamples": compared,
        "displacementLimit": displacement_limit,
        "absoluteSeparationLimit": absolute_separation_limit,
        "relativeSeparationFactor": relative_separation_factor,
        "relativeSeparationSlack": relative_separation_slack,
        "failureCount": len(failures),
        "worst": worst,
        "failures": failures[:20],
    }
