"""Pure comparison helpers for Maya Bullet versus native physics captures."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any


def _distance(left: list[float], right: list[float]) -> float:
    return math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(left, right)))


def _normalized_rotation_rows(matrix: list[float]) -> list[list[float]]:
    """Extract normalized XYZ basis rows from a Maya world matrix."""
    if len(matrix) != 16:
        raise ValueError("world matrix must contain 16 values")
    rows = [[float(matrix[row * 4 + column]) for column in range(3)] for row in range(3)]
    result: list[list[float]] = []
    for row in rows:
        length = math.sqrt(sum(value * value for value in row))
        if not math.isfinite(length) or length <= 1.0e-8:
            raise ValueError("world matrix has a degenerate rotation basis")
        result.append([value / length for value in row])
    return result


def _rotation_angle_degrees(left: list[float], right: list[float]) -> float:
    """Return the shortest angular distance between two world matrices."""
    lhs = _normalized_rotation_rows(left)
    rhs = _normalized_rotation_rows(right)
    trace = sum(sum(lhs[row][axis] * rhs[row][axis] for axis in range(3)) for row in range(3))
    cosine = max(-1.0, min(1.0, (trace - 1.0) * 0.5))
    return math.degrees(math.acos(cosine))


def compare_bullet_world_transform_delta(
    baseline: dict[str, dict[str, dict[str, Any]]],
    native: dict[str, dict[str, dict[str, Any]]],
    frames: list[int],
) -> dict[str, Any]:
    """Report exact world translation/rotation drift without imposing a gate."""
    compared = 0
    translations: list[float] = []
    rotations: list[float] = []
    translation_worst: dict[str, Any] | None = None
    rotation_worst: dict[str, Any] | None = None
    for bone_index in sorted(set(baseline) & set(native), key=lambda value: int(value)):
        for frame in frames:
            frame_key = str(frame)
            bullet_sample = baseline[bone_index].get(frame_key)
            native_sample = native[bone_index].get(frame_key)
            if not bullet_sample or not native_sample:
                continue
            try:
                translation = _distance(
                    bullet_sample["worldTranslate"], native_sample["worldTranslate"]
                )
                rotation = _rotation_angle_degrees(
                    bullet_sample["worldMatrix"], native_sample["worldMatrix"]
                )
            except (KeyError, TypeError, ValueError):
                continue
            if not math.isfinite(translation) or not math.isfinite(rotation):
                continue
            compared += 1
            translations.append(translation)
            rotations.append(rotation)
            evidence = {
                "boneIndex": int(bone_index),
                "joint": bullet_sample.get("joint"),
                "frame": int(frame),
                "translation": translation,
                "rotationDegrees": rotation,
                "bulletWorldTranslate": bullet_sample.get("worldTranslate"),
                "nativeWorldTranslate": native_sample.get("worldTranslate"),
            }
            if translation_worst is None or translation > translation_worst["translation"]:
                translation_worst = evidence
            if rotation_worst is None or rotation > rotation_worst["rotationDegrees"]:
                rotation_worst = evidence
    sorted_rotations = sorted(rotations)
    p95_index = max(0, math.ceil(len(sorted_rotations) * 0.95) - 1)
    return {
        "comparedSamples": compared,
        "maxTranslation": translation_worst["translation"] if translation_worst else None,
        "maxRotationDegrees": rotation_worst["rotationDegrees"] if rotation_worst else None,
        "rmsTranslation": (
            math.sqrt(sum(value * value for value in translations) / len(translations))
            if translations
            else None
        ),
        "rmsRotationDegrees": (
            math.sqrt(sum(value * value for value in rotations) / len(rotations))
            if rotations
            else None
        ),
        "p95RotationDegrees": sorted_rotations[p95_index] if sorted_rotations else None,
        "translationWorst": translation_worst,
        "rotationWorst": rotation_worst,
    }


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


def compare_mesh_vertex_samples(
    baseline: dict[int, list[tuple[float, float, float]]],
    native: dict[int, list[tuple[float, float, float]]],
    frames: list[int],
    threshold: float,
) -> dict[str, Any]:
    """Compare matching world-space mesh vertices, failing closed on bad data."""
    limit = float(threshold)
    if not math.isfinite(limit) or limit < 0.0:
        raise ValueError(f"mesh threshold must be finite and non-negative: {threshold!r}")
    per_frame: dict[str, Any] = {}
    all_distances: list[float] = []
    failures: list[dict[str, Any]] = []
    for frame in frames:
        left = baseline.get(frame)
        right = native.get(frame)
        if left is None or right is None:
            failure = {"frame": int(frame), "reason": "missing_frame"}
            failures.append(failure)
            per_frame[str(frame)] = {**failure, "passed": False}
            continue
        if len(left) != len(right) or not left:
            failure = {
                "frame": int(frame),
                "reason": "vertex_count_mismatch" if len(left) != len(right) else "no_vertices",
                "baselineVertexCount": len(left),
                "nativeVertexCount": len(right),
            }
            failures.append(failure)
            per_frame[str(frame)] = {**failure, "passed": False}
            continue
        distances: list[float] = []
        nonfinite = 0
        for lhs, rhs in zip(left, right):
            values = (*lhs, *rhs)
            if len(lhs) != 3 or len(rhs) != 3 or not all(math.isfinite(float(value)) for value in values):
                nonfinite += 1
                continue
            distances.append(_distance(list(lhs), list(rhs)))
        max_distance = max(distances) if distances else None
        rms = math.sqrt(sum(value * value for value in distances) / len(distances)) if distances else None
        failed = (
            nonfinite > 0
            or len(distances) != len(left)
            or max_distance is None
            or max_distance > limit
        )
        evidence = {
            "frame": int(frame),
            "vertexCount": len(left),
            "comparedVertexCount": len(distances),
            "nonfiniteVertexCount": nonfinite,
            "max": max_distance,
            "rms": rms,
            "passed": not failed,
        }
        per_frame[str(frame)] = evidence
        all_distances.extend(distances)
        if failed:
            failures.append(evidence)
    return {
        "passed": bool(frames) and not failures,
        "threshold": limit,
        "frameCount": len(frames),
        "comparedVertexSamples": len(all_distances),
        "max": max(all_distances) if all_distances else None,
        "rms": (
            math.sqrt(sum(value * value for value in all_distances) / len(all_distances))
            if all_distances
            else None
        ),
        "failureCount": len(failures),
        "failures": failures[:20],
        "frames": per_frame,
    }
