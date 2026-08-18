"""Select representative local PMX/VMD cases without modifying source assets.

The selector is intentionally host-neutral.  It parses local files, records
stable metrics and hashes, and writes a manifest consumed by the Maya-hosted
roundtrip runner.  It never writes below the scan root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
BUILD_ROOT = (ROOT / "build").resolve()
DEFAULT_SCAN_ROOT = Path(r"F:\mmd")
DEFAULT_OUTPUT = ROOT / "build" / "reports" / "local_asset_roundtrip" / "representative.json"
EXCLUDED_DIRECTORY_NAMES = {".git", "__MACOSX", "node_modules"}
MANIFEST_SCHEMA_VERSION = 1

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _require_build_path(value: str | Path) -> Path:
    """Resolve a report path and keep it inside this repository's build tree."""

    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    resolved = path.resolve()
    if resolved != BUILD_ROOT and BUILD_ROOT not in resolved.parents:
        raise ValueError(f"output must resolve under {BUILD_ROOT}: {resolved}")
    return resolved


def _sha256(path: Path) -> str:
    """Return a streaming SHA-256 digest for a local asset."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_name(value: Any) -> str:
    """Normalize a model/track name for exact compatibility matching."""

    return " ".join(str(value or "").strip().casefold().split())


def _safe_name(value: str) -> str:
    """Return a stable filesystem-safe case fragment."""

    result = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return result[:64] or "asset"


def _asset_files(root: Path, suffix: str) -> list[Path]:
    """Enumerate supported assets while ignoring archive/tooling detritus."""

    return sorted(
        path
        for path in root.rglob(f"*{suffix}")
        if path.is_file()
        and path.suffix.casefold() == suffix.casefold()
        and not any(part in EXCLUDED_DIRECTORY_NAMES for part in path.parts)
        and not path.name.startswith("._")
    )


def _object_name(value: Any, *fields: str) -> str:
    """Read the first non-empty name field from a parser object."""

    for field in fields:
        name = getattr(value, field, None)
        if name:
            return str(name)
    return ""


def _pmx_descriptor(path: Path) -> dict[str, Any]:
    """Parse one PMX and return public metrics plus private match sets."""

    from mmd_tools.core.pmx_data.bone import PmxBoneFlag
    from mmd_tools.core.mmd_parser import parse_pmx_file
    from mmd_tools.core.pmx_local_axis import maya_basis_from_pmx_local_axes

    data = parse_pmx_file(str(path), require_native_pmx_parse=False)
    bone_names = {
        _normalized_name(_object_name(bone, "name", "name_english"))
        for bone in data.bones
        if _object_name(bone, "name", "name_english")
    }
    morph_names = {
        _normalized_name(_object_name(morph, "name", "name_english"))
        for morph in data.morphs
        if _object_name(morph, "name", "name_english")
    }
    invalid_local_axis_bones = 0
    for bone in data.bones:
        flags = int(getattr(bone, "bone_flag", 0))
        if not flags & int(PmxBoneFlag.LOCAL_AXIS):
            continue
        x_axis = getattr(bone, "x_axis_direction", ()) or ()
        z_axis = getattr(bone, "z_axis_direction", ()) or ()
        try:
            maya_basis_from_pmx_local_axes(x_axis, z_axis)
        except (TypeError, ValueError):
            invalid_local_axis_bones += 1
    sdef_vertices = sum(
        1 for vertex in data.vertices if int(getattr(vertex, "weight_transform_type", 0)) == 3
    )
    return {
        "path": str(path.resolve()),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
        "metrics": {
            "model_name": str(getattr(data.header, "model_name", "") or ""),
            "vertices": len(data.vertices),
            "faces": len(data.faces),
            "materials": len(data.materials),
            "bones": len(data.bones),
            "morphs": len(data.morphs),
            "display_frames": len(data.display_frames),
            "rigid_bodies": len(data.rigid_bodies),
            "joints": len(data.joints),
            "soft_bodies": len(data.soft_bodies),
            "invalid_local_axis_bones": invalid_local_axis_bones,
            "sdef_vertices": sdef_vertices,
        },
        "_bone_names": bone_names,
        "_morph_names": morph_names,
    }


def _frame_number(frame: Any) -> int:
    return int(getattr(frame, "frame_number", getattr(frame, "frame", 0)))


def _interpolation_variant_count(frames: Iterable[Any]) -> int:
    """Count distinct raw interpolation payloads without interpreting them."""

    payloads = set()
    for frame in frames:
        interpolation = getattr(frame, "interpolation", None)
        if interpolation is None:
            continue
        try:
            payloads.add(bytes(interpolation))
        except (TypeError, ValueError):
            continue
    return len(payloads)


def _vmd_descriptor(path: Path) -> dict[str, Any]:
    """Parse one VMD and calculate selection metrics."""

    from mmd_tools.core.vmd_data import VmdData

    data = VmdData().parse_file(str(path))
    sections = (
        ("bone", data.bone_frames, "bone_name"),
        ("morph", data.morph_frames, "morph_name"),
        ("camera", data.camera_frames, None),
        ("light", data.light_frames, None),
        ("shadow", data.shadow_frames, None),
        ("ik", data.ik_show_hide_frames, None),
    )
    frame_numbers: list[int] = []
    active_tracks: set[tuple[str, str]] = set()
    interpolation_frames: list[Any] = []
    section_counts: dict[str, int] = {}
    bone_names: set[str] = set()
    morph_names: set[str] = set()
    ik_names: set[str] = set()
    seen_track_frames: set[tuple[str, str, int]] = set()
    duplicate_key_count = 0
    total_keys = 0
    for section, frames, name_field in sections:
        section_counts[f"{section}_frames"] = len(frames)
        total_keys += len(frames)
        for frame in frames:
            frame_number = _frame_number(frame)
            frame_numbers.append(frame_number)
            if name_field:
                name = _normalized_name(getattr(frame, name_field, ""))
                active_tracks.add((section, name))
                track_key = (section, name, frame_number)
                if track_key in seen_track_frames:
                    duplicate_key_count += 1
                seen_track_frames.add(track_key)
                if section == "bone" and name:
                    bone_names.add(name)
                if section == "morph" and name:
                    morph_names.add(name)
            elif section == "ik" and getattr(frame, "ik_states", None):
                for name, _state in frame.ik_states:
                    normalized_name = _normalized_name(name)
                    active_tracks.add((section, normalized_name))
                    ik_names.add(normalized_name)
                    track_key = (section, normalized_name, frame_number)
                    if track_key in seen_track_frames:
                        duplicate_key_count += 1
                    seen_track_frames.add(track_key)
            else:
                track_name = "<single>"
                active_tracks.add((section, track_name))
                track_key = (section, track_name, frame_number)
                if track_key in seen_track_frames:
                    duplicate_key_count += 1
                seen_track_frames.add(track_key)
        if section in {"bone", "morph", "camera"}:
            interpolation_frames.extend(frames)

    frame_start = min(frame_numbers) if frame_numbers else None
    frame_end = max(frame_numbers) if frame_numbers else None
    frame_span = frame_end - frame_start + 1 if frame_start is not None else 0
    active_track_count = len(active_tracks)
    density = (
        total_keys / (active_track_count * frame_span)
        if active_track_count and frame_span
        else 0.0
    )
    return {
        "path": str(path.resolve()),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
        "metrics": {
            **section_counts,
            "total_keys": total_keys,
            "active_tracks": active_track_count,
            "frame_start": frame_start,
            "frame_end": frame_end,
            "frame_span": frame_span,
            "density": round(density, 9),
            "interpolation_variant_count": _interpolation_variant_count(interpolation_frames),
            "duplicate_key_count": duplicate_key_count,
        },
        "_bone_names": bone_names,
        "_morph_names": morph_names,
        "_ik_names": ik_names,
    }


def _match_ratio(source: Iterable[str], target: Iterable[str]) -> float:
    """Return exact normalized-name coverage of source tracks in target names."""

    source_names = {name for name in source if name}
    target_names = {name for name in target if name}
    return len(source_names & target_names) / len(source_names) if source_names else 0.0


def _pair_score(vmd: Mapping[str, Any], pmx: Mapping[str, Any]) -> dict[str, float]:
    """Calculate exact track-family coverage for one PMX/VMD pairing.

    ``combined_match_ratio`` is the weakest coverage among the track families
    present in the VMD, so a strong bone match cannot hide a missing morph or
    IK name.
    """

    bone_ratio = _match_ratio(vmd.get("_bone_names", ()), pmx.get("_bone_names", ()))
    morph_ratio = _match_ratio(vmd.get("_morph_names", ()), pmx.get("_morph_names", ()))
    ik_names = set(vmd.get("_ik_names", ()))
    ik_ratio = _match_ratio(ik_names, pmx.get("_bone_names", ())) if ik_names else 1.0
    required_ratios = []
    if vmd.get("_bone_names"):
        required_ratios.append(bone_ratio)
    if vmd.get("_morph_names"):
        required_ratios.append(morph_ratio)
    if ik_names:
        required_ratios.append(ik_ratio)
    combined = min(required_ratios) if required_ratios else 0.0
    return {
        "bone_match_ratio": round(bone_ratio, 6),
        "morph_match_ratio": round(morph_ratio, 6),
        "ik_match_ratio": round(ik_ratio, 6),
        "combined_match_ratio": round(combined, 6),
    }


def _pmx_selection_key(descriptor: Mapping[str, Any]) -> tuple[float, ...]:
    metrics = descriptor["metrics"]
    vertices = max(int(metrics["vertices"]), 1)
    feature_score = sum(
        int(metrics[name]) > 0
        for name in ("materials", "bones", "morphs", "rigid_bodies", "joints")
    )
    return (feature_score, math.log10(vertices), math.log10(max(int(metrics["bones"]), 1)))


def _select_pmx(
    descriptors: list[dict[str, Any]],
    count: int,
) -> list[dict[str, Any]]:
    """Select deterministic small/feature-rich/large PMX representatives.

    Pair compatibility is deliberately not a selection gate.  The selected
    models are the fixed test surface, while each VMD is paired to one of them
    and the resulting coverage metrics are recorded for later Maya evidence.
    """

    if count < 1:
        return []
    usable = [
        item
        for item in descriptors
        if item["metrics"]["bones"] > 0
        and item["metrics"]["vertices"] > 0
    ]
    if not usable:
        raise ValueError("no PMX with parsed bones was found")

    selected: list[dict[str, Any]] = []

    standard_pool = [
        item
        for item in usable
        if 1_000 <= int(item["metrics"]["vertices"]) <= 100_000
        and int(item["metrics"]["bones"]) >= 20
    ] or usable
    standard = max(
        standard_pool,
        key=lambda item: (
            *_pmx_selection_key(item),
            -abs(math.log10(max(int(item["metrics"]["vertices"]), 1)) - 4.3),
            str(item["path"]),
        ),
    )
    selected.append(standard)

    if len(selected) < count:
        feature_rich = max(
            (item for item in usable if item not in selected),
            key=lambda item: (*_pmx_selection_key(item), str(item["path"])),
            default=None,
        )
        if feature_rich is not None:
            selected.append(feature_rich)
    if len(selected) < count:
        largest = max(
            (item for item in usable if item not in selected),
            key=lambda item: (
                int(item["metrics"]["vertices"]),
                *_pmx_selection_key(item),
                str(item["path"]),
            ),
            default=None,
        )
        if largest is not None:
            selected.append(largest)
    return selected[:count]


def _oracle_frames(metrics: Mapping[str, Any]) -> list[int]:
    """Choose stable quarter-span samples for the later Maya oracle."""

    start = metrics.get("frame_start")
    end = metrics.get("frame_end")
    if start is None or end is None:
        return []
    span = int(end) - int(start)
    return sorted(
        {
            int(int(start) + span * ratio + 0.5)
            for ratio in (0.0, 0.25, 0.5, 0.75, 1.0)
        }
    )


def _best_pair(vmd: Mapping[str, Any], pmx_descriptors: Iterable[Mapping[str, Any]]) -> tuple[Mapping[str, Any], dict[str, float]]:
    pairs = [(pmx, _pair_score(vmd, pmx)) for pmx in pmx_descriptors]
    return max(
        pairs,
        key=lambda pair: (
            pair[1]["combined_match_ratio"],
            pair[1]["bone_match_ratio"],
            pair[1]["morph_match_ratio"],
            pair[1]["ik_match_ratio"],
        ),
    )


def _motion_case(
    vmd: Mapping[str, Any],
    pmx: Mapping[str, Any],
    pair: Mapping[str, float],
    classification: str,
) -> dict[str, Any]:
    metrics = dict(vmd["metrics"])
    metrics.update(pair)
    return {
        "name": f"{classification}_{_safe_name(Path(vmd['path']).stem)}",
        "kind": "pmx_vmd",
        "classification": classification,
        "pmx": pmx["path"],
        "vmd": vmd["path"],
        "pmx_sha256": pmx["sha256"],
        "vmd_sha256": vmd["sha256"],
        "metrics": metrics,
        "oracle_frames": _oracle_frames(metrics),
        "reason": (
            f"{classification} selection: total_keys={metrics['total_keys']}, "
            f"active_tracks={metrics['active_tracks']}, frame_span={metrics['frame_span']}, "
            f"density={metrics['density']}, bone_match_ratio={metrics['bone_match_ratio']}, "
            f"morph_match_ratio={metrics['morph_match_ratio']}, ik_match_ratio={metrics['ik_match_ratio']}, "
            f"pairing_is_diagnostic=true"
        ),
    }


def _deduplicate_case_names(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep generated case names unique when sanitized VMD stems collide."""

    used: set[str] = set()
    for case in cases:
        base_name = str(case["name"])
        name = base_name
        if name in used:
            digest = str(case.get("vmd_sha256") or "")[:8] or "case"
            name = f"{base_name}_{digest}"
            suffix = 2
            while name in used:
                name = f"{base_name}_{digest}_{suffix}"
                suffix += 1
        case["name"] = name
        used.add(name)
    return cases


def _select_motion_cases(
    descriptors: list[dict[str, Any]],
    pmx_descriptors: list[dict[str, Any]],
    *,
    dense_count: int,
    sparse_count: int,
    dense_density: float,
    sparse_density: float,
) -> list[dict[str, Any]]:
    """Select broad density buckets and record one deterministic PMX pairing."""

    candidates: list[tuple[dict[str, Any], dict[str, Any], dict[str, float]]] = []
    for vmd in descriptors:
        metrics = vmd["metrics"]
        if metrics["total_keys"] <= 0:
            continue
        pmx, pair = _best_pair(vmd, pmx_descriptors)
        candidates.append((vmd, pmx, pair))

    dense_candidates = [
        item
        for item in candidates
        if item[0]["metrics"]["density"] >= dense_density
        and item[0]["metrics"]["total_keys"] >= 1_000
    ]
    sparse_candidates = [
        item
        for item in candidates
        if item[0]["metrics"]["density"] <= sparse_density
        and item[0]["metrics"]["total_keys"] >= 20
    ]
    if len(dense_candidates) < dense_count:
        raise ValueError(
            f"only {len(dense_candidates)} dense VMD candidates; {dense_count} required"
        )
    if len(sparse_candidates) < sparse_count:
        raise ValueError(
            f"only {len(sparse_candidates)} sparse VMD candidates; {sparse_count} required"
        )

    dense_candidates.sort(
        key=lambda item: (
            -item[0]["metrics"]["density"],
            -item[0]["metrics"]["total_keys"],
            str(item[0]["path"]),
        ),
    )
    sparse_candidates.sort(
        key=lambda item: (
            -item[0]["metrics"]["interpolation_variant_count"],
            -item[0]["metrics"]["total_keys"],
            item[0]["metrics"]["density"],
            str(item[0]["path"]),
        ),
    )
    cases = [
        _motion_case(vmd, pmx, pair, "dense")
        for vmd, pmx, pair in dense_candidates[:dense_count]
    ]
    cases.extend(
        _motion_case(vmd, pmx, pair, "sparse")
        for vmd, pmx, pair in sparse_candidates[:sparse_count]
    )
    return _deduplicate_case_names(cases)


def _public_pmx_case(descriptor: Mapping[str, Any], label: str) -> dict[str, Any]:
    metrics = dict(descriptor["metrics"])
    return {
        "name": f"pmx_{label}",
        "kind": "pmx",
        "classification": "pmx_only",
        "pmx": descriptor["path"],
        "pmx_sha256": descriptor["sha256"],
        "metrics": metrics,
        "reason": (
            f"{label} PMX selection: vertices={metrics['vertices']}, bones={metrics['bones']}, "
            f"materials={metrics['materials']}, morphs={metrics['morphs']}, "
            f"physics={metrics['rigid_bodies'] + metrics['joints']}, "
            f"invalid_local_axis_bones={metrics.get('invalid_local_axis_bones', 0)}, "
            f"sdef_vertices={metrics.get('sdef_vertices', 0)}"
        ),
    }


def build_manifest(
    scan_root: str | Path,
    *,
    pmx_count: int = 3,
    dense_count: int = 2,
    sparse_count: int = 2,
    dense_density: float = 0.2,
    sparse_density: float = 0.05,
) -> dict[str, Any]:
    """Scan local assets and return a fixed representative manifest."""

    root = Path(scan_root).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"scan root does not exist: {root}")
    pmx_paths = _asset_files(root, ".pmx")
    vmd_paths = _asset_files(root, ".vmd")
    pmx_descriptors: list[dict[str, Any]] = []
    vmd_descriptors: list[dict[str, Any]] = []
    parse_errors: list[dict[str, str]] = []
    for path in pmx_paths:
        try:
            pmx_descriptors.append(_pmx_descriptor(path))
        except Exception as exc:  # noqa: BLE001 - manifest records per-file parse failures.
            parse_errors.append({"kind": "pmx", "path": str(path), "error": str(exc)})
    for path in vmd_paths:
        try:
            vmd_descriptors.append(_vmd_descriptor(path))
        except Exception as exc:  # noqa: BLE001 - manifest records per-file parse failures.
            parse_errors.append({"kind": "vmd", "path": str(path), "error": str(exc)})

    selected_pmx = _select_pmx(pmx_descriptors, pmx_count)
    motion_cases = _select_motion_cases(
        vmd_descriptors,
        selected_pmx,
        dense_count=dense_count,
        sparse_count=sparse_count,
        dense_density=dense_density,
        sparse_density=sparse_density,
    )
    labels = ("small_medium", "feature_rich", "largest")
    pmx_cases = [
        _public_pmx_case(descriptor, labels[index] if index < len(labels) else f"representative_{index}")
        for index, descriptor in enumerate(selected_pmx)
    ]
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "kind": "local-asset-roundtrip-representative",
        "scan_root": str(root),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "selection_policy": {
            "excluded_directory_names": sorted(EXCLUDED_DIRECTORY_NAMES),
            "pmx_count": pmx_count,
            "dense_count": dense_count,
            "sparse_count": sparse_count,
            "min_dense_total_keys": 1_000,
            "min_sparse_total_keys": 20,
            "dense_density": dense_density,
            "sparse_density": sparse_density,
            "pairing_strategy": "one best PMX per VMD; match ratios are diagnostic only",
            "pairing_is_selection_gate": False,
            "motion_pair_scope": "selected PMX representatives only",
            "frame_span_formula": "max_frame - min_frame + 1",
            "density_formula": "total_keys / (active_tracks * frame_span)",
        },
        "scan": {
            "pmx_files": len(pmx_paths),
            "pmx_parsed": len(pmx_descriptors),
            "vmd_files": len(vmd_paths),
            "vmd_parsed": len(vmd_descriptors),
            "parse_errors": parse_errors,
        },
        "cases": [*pmx_cases, *motion_cases],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan-root", default=str(DEFAULT_SCAN_ROOT))
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--pmx-count", type=int, default=3)
    parser.add_argument("--dense-count", type=int, default=2)
    parser.add_argument("--sparse-count", type=int, default=2)
    parser.add_argument("--dense-density", type=float, default=0.2)
    parser.add_argument("--sparse-density", type=float, default=0.05)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.pmx_count < 1 or args.dense_count < 1 or args.sparse_count < 1:
        raise SystemExit("pmx/dense/sparse counts must be positive")
    if not 0.0 <= args.sparse_density <= args.dense_density:
        raise SystemExit("sparse density must be <= dense density and both must be non-negative")
    output = _require_build_path(args.out)
    manifest = build_manifest(
        args.scan_root,
        pmx_count=args.pmx_count,
        dense_count=args.dense_count,
        sparse_count=args.sparse_count,
        dense_density=args.dense_density,
        sparse_density=args.sparse_density,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Manifest: {output}")
    print(
        "Scanned: "
        f"PMX {manifest['scan']['pmx_parsed']}/{manifest['scan']['pmx_files']}, "
        f"VMD {manifest['scan']['vmd_parsed']}/{manifest['scan']['vmd_files']}"
    )
    print(f"Cases: {len(manifest['cases'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
