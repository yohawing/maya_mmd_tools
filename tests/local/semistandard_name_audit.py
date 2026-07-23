"""Audit local PMX/VMD assets for semistandard bone-name conversion gaps."""

from __future__ import annotations

import argparse
import json
import re
import time
import unicodedata
from collections import defaultdict
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from mmd_tools.core.exceptions import MMDParseException
from mmd_tools.core.mmd_bone_names import (
    convert_mmd_bone_name_to_ascii,
    has_semistandard_mmd_bone_name,
    normalize_mmd_bone_name,
)
from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.core.vmd_data import VmdData
from mmd_tools.core.unicode_converter import get_converter


_AUDIT_SUFFIXES = {".pmx", ".vmd"}
_GENERIC_ASCII_NAMES = {"d", "p", "c", "ik", "ex"}
_SEMISTANDARD_EXACT = {"全ての親", "操作中心", "グルーブ", "上半身2", "腰", "胸親"}
_SEMISTANDARD_MARKERS = (
    "足IK",
    "足IK親",
    "つま先IK",
    "つま先IK先",
    "足先EX",
    "親指0",
    "肩P",
    "肩C",
    "腕捩",
    "手捩",
    "腕D",
    "腕捩D",
    "ひじD",
    "手首D",
    "手捩D",
    "足D",
    "ひざD",
    "足首D",
)
_MODEL_DEPENDENT_MARKERS = (
    "髪",
    "前髪",
    "横髪",
    "後髪",
    "胸",
    "骨盤",
    "スカート",
    "袖",
    "リボン",
    "ツインテ",
    "テール",
    "ネクタイ",
    "マント",
    "尻尾",
    "羽",
    "補助",
    "抽出",
    "軸",
)
_LOW_RISK_SEMISTANDARD_ALIAS_RE = re.compile(r"^[左右](足IK先|腕捩先|手捩先)$")
_SIDE_PREFIX_RE = re.compile(r"^[左右].+")
_MAYA_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _resolve_path(value: str, base_dir: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _looks_semistandard_like(name: str | None) -> bool:
    normalized = normalize_mmd_bone_name(name)
    if not normalized:
        return False
    if has_semistandard_mmd_bone_name(normalized) or normalized in _SEMISTANDARD_EXACT:
        return True
    if any(marker in normalized for marker in _SEMISTANDARD_MARKERS):
        return True
    return bool(_SIDE_PREFIX_RE.match(normalized) and normalized.endswith(("P", "C", "D")))


def _generic_ascii_name(name: str | None) -> bool:
    normalized = normalize_mmd_bone_name(name)
    if not normalized:
        return False
    if not normalized.isascii():
        return False
    simplified = re.sub(r"[^A-Za-z0-9]+", "_", normalized).strip("_").lower()
    return simplified in _GENERIC_ASCII_NAMES or bool(re.fullmatch(r"[dpc]_[0-9]+", simplified))


def _finding(
    severity: str,
    kind: str,
    path: Path,
    *,
    source: str,
    name: str,
    converted: str | None = None,
    index: int | None = None,
    english_name: str | None = None,
    detail: str = "",
    count: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "severity": severity,
        "kind": kind,
        "file": str(path),
        "source": source,
        "name": name,
    }
    if converted is not None:
        result["converted"] = converted
    if index is not None:
        result["index"] = index
    if english_name is not None:
        result["english_name"] = english_name
    if detail:
        result["detail"] = detail
    if count is not None:
        result["count"] = count
    return result


def _corpus_name_flags(name: str, converted: str) -> list[str]:
    """Return deterministic hazards for a material or morph name.

    Unicode letters are expected input for MMD names and are therefore not
    treated as punctuation.  The source spelling is checked for Maya hazards
    before conversion because ``maya_safe_name`` may replace them silently.
    """
    flags: list[str] = []
    if "HASH" in converted:
        flags.append("hash_fallback")
    if converted and not converted.isascii():
        flags.append("non_ascii_remaining")
    if not converted:
        flags.append("empty_result")
    if name and name[0].isdigit():
        flags.append("leading_digit")
    if ":" in name or "|" in name:
        flags.append("colon_namespace")
    if any(not (char.isalnum() or char == "_") for char in name):
        flags.append("unsupported_punctuation")
    if converted and not _MAYA_SAFE_IDENTIFIER_RE.fullmatch(converted):
        flags.append("unsafe_maya_identifier")
    return flags


def _name_inventory_record(
    path: Path,
    *,
    category: str,
    source: str,
    index: int,
    name: str,
    english_name: str = "",
    morph_type: str | None = None,
    converter: Any,
) -> dict[str, Any]:
    converted = converter.convert(name) or ""
    flags = _corpus_name_flags(name, converted)
    return {
        "category": category,
        "source_kind": source,
        "file": str(path),
        "index": index,
        "name": name,
        "english_name": english_name,
        "converted": converted,
        "flags": flags,
        "morph_type": morph_type,
    }


def _corpus_findings(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert per-name hazards into findings without dropping inventory."""
    findings: list[dict[str, Any]] = []
    for record in records:
        for flag in record["flags"]:
            finding = _finding(
                "warning",
                f"{record['category']}_{flag}",
                Path(record["file"]),
                source=record["source_kind"],
                name=record["name"],
                converted=record["converted"],
                index=record["index"],
                english_name=record.get("english_name") or None,
                detail=f"{record['category']} name conversion hazard: {flag}",
                count=1,
            )
            finding.update(
                {
                    "category": record["category"],
                    "source_kind": record["source_kind"],
                    "occurrence_count": 1,
                    "distinct_file_count": 1,
                    "morph_type": record.get("morph_type"),
                }
            )
            findings.append(finding)
    return findings


def _build_corpus_statistics(
    records: Iterable[dict[str, Any]],
    *,
    category: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Aggregate all names and rank dangerous names by model frequency.

    ``occurrences`` counts every appearance, while ``distinct_files`` counts
    each PMX once.  ``within_model_repeats`` makes repeated names in one model
    visible without allowing them to outrank a cross-model occurrence.
    """
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    all_records = [record for record in records if record["category"] == category]
    for record in all_records:
        normalized = unicodedata.normalize("NFKC", str(record.get("name") or "")).strip()
        grouped[normalized].append(record)

    converted_to_names: dict[str, set[str]] = defaultdict(set)
    for normalized, entries in grouped.items():
        for entry in entries:
            if entry["converted"]:
                converted_to_names[entry["converted"]].add(normalized)
    collision_names = {
        name
        for names in converted_to_names.values()
        if len(names) > 1
        for name in names
    }

    statistics: list[dict[str, Any]] = []
    for normalized, entries in grouped.items():
        file_counts = Counter(str(entry["file"]) for entry in entries)
        converted_counts = Counter(str(entry.get("converted") or "") for entry in entries)
        converted = converted_counts.most_common(1)[0][0] if converted_counts else ""
        flags = set(flag for entry in entries for flag in entry.get("flags", []))
        if normalized in collision_names:
            flags.add("conversion_collision")
        morph_types = sorted({str(entry.get("morph_type")) for entry in entries if entry.get("morph_type")})
        examples = [
            {
                "file": entry["file"],
                "name": entry["name"],
                "english_name": entry.get("english_name", ""),
                "index": entry["index"],
                "morph_type": entry.get("morph_type"),
            }
            for entry in entries[:8]
        ]
        row = {
            "category": category,
            "source_kind": f"pmx.{category}s",
            "normalized_name": normalized,
            "original_name": normalized,
            "converted": converted,
            "occurrences": len(entries),
            "distinct_files": len(file_counts),
            "distinct_models": len(file_counts),
            "within_model_repeats": sum(max(count - 1, 0) for count in file_counts.values()),
            "dangerous": bool(flags),
            "flags": sorted(flags),
            "morph_types": morph_types,
            "examples": examples,
        }
        statistics.append(row)

    statistics.sort(
        key=lambda row: (
            -int(row["dangerous"]),
            -int(row["distinct_models"]),
            -int(row["occurrences"]),
            str(row["normalized_name"]),
        )
    )
    return statistics, [row for row in statistics if row["dangerous"]]


def _corpus_collision_findings(statistics: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for row in statistics:
        if "conversion_collision" not in row.get("flags", []):
            continue
        example = row["examples"][0] if row.get("examples") else {}
        finding = _finding(
            "warning",
            f"{row['category']}_conversion_collision",
            Path(example.get("file", "")),
            source=row["source_kind"],
            name=row["original_name"],
            converted=row["converted"],
            detail="distinct original names convert to the same Maya-safe name",
            count=row["occurrences"],
        )
        finding.update(
            {
                "category": row["category"],
                "source_kind": row["source_kind"],
                "occurrence_count": row["occurrences"],
                "distinct_file_count": row["distinct_files"],
                "distinct_model_count": row["distinct_models"],
                "flags": row["flags"],
                "examples": row["examples"],
                "morph_type": ", ".join(row.get("morph_types") or []),
            }
        )
        findings.append(finding)
    return findings


def _iter_manifest_paths(manifest: Path) -> Iterable[Path]:
    manifest_dir = manifest.parent
    data = json.loads(manifest.read_text(encoding="utf-8"))

    for value in data.get("files") or []:
        yield _resolve_path(str(value), manifest_dir)

    for asset in data.get("assets") or []:
        for key in ("model", "motion", "pmx", "vmd"):
            value = asset.get(key)
            if value:
                yield _resolve_path(str(value), manifest_dir)

    for case in data.get("cases") or []:
        assets = case.get("assets") or {}
        for key in ("model", "motion", "pmx", "vmd", "characterMotion", "cameraMotion"):
            value = assets.get(key) or case.get(key)
            if value:
                yield _resolve_path(str(value), manifest_dir)


def _iter_scan_root_paths(scan_roots: Iterable[Path]) -> Iterable[Path]:
    for root in scan_roots:
        if root.is_file() and root.suffix.lower() in _AUDIT_SUFFIXES:
            yield root.resolve()
            continue
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in _AUDIT_SUFFIXES:
                yield path.resolve()


def _unique_paths(paths: Iterable[Path], max_files: int | None) -> tuple[list[Path], int, bool]:
    result: list[Path] = []
    seen: set[str] = set()
    truncated = False
    for path in paths:
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
        if max_files is not None and len(result) >= max_files:
            truncated = True
            break
    return result, len(seen), truncated


def _audit_pmx(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    started = time.perf_counter()
    findings: list[dict[str, Any]] = []
    converted_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    converter = get_converter()
    material_names: list[dict[str, Any]] = []
    morph_names: list[dict[str, Any]] = []
    semistandard_count = 0
    english_override_count = 0

    try:
        pmx = parse_pmx_file(str(path), use_native_pmx_parse=False)
    except (FileNotFoundError, MMDParseException, OSError, UnicodeError) as exc:
        return (
            {
                "file": str(path),
                "type": "pmx",
                "status": "fail",
                "duration_sec": round(time.perf_counter() - started, 3),
                "detail": f"parse failed: {exc}",
                "_material_names": material_names,
                "_morph_names": morph_names,
            },
            [_finding("error", "parse_error", path, source="pmx", name=path.name, detail=str(exc))],
        )

    for index, bone in enumerate(getattr(pmx, "bones", []) or []):
        native_name = getattr(bone, "name", "") or ""
        english_name = getattr(bone, "name_english", "") or ""
        is_semistandard = has_semistandard_mmd_bone_name(native_name)
        source_name = native_name if is_semistandard else (bone.get_name() if hasattr(bone, "get_name") else english_name or native_name)
        converted = convert_mmd_bone_name_to_ascii(source_name) or ""
        converted_buckets[converted].append({"index": index, "name": native_name, "english_name": english_name})

        if is_semistandard:
            semistandard_count += 1
            if english_name and convert_mmd_bone_name_to_ascii(english_name) != converted:
                english_override_count += 1
            continue

        if _looks_semistandard_like(native_name):
            findings.append(
                _finding(
                    "warning",
                    "unmapped_semistandard_like_pmx_bone",
                    path,
                    source="pmx.bones",
                    index=index,
                    name=native_name,
                    english_name=english_name,
                    converted=converted,
                    detail="native bone name looks like a semistandard variant but is not in the hardcoded map",
                )
            )
        if _generic_ascii_name(source_name):
            findings.append(
                _finding(
                    "warning",
                    "generic_ascii_import_name",
                    path,
                    source="pmx.bones",
                    index=index,
                    name=native_name,
                    english_name=english_name,
                    converted=converted,
                    detail="selected import name is too generic and will rely on Maya uniquing",
                )
            )
        if "HASH" in converted and _looks_semistandard_like(native_name):
            findings.append(
                _finding(
                    "warning",
                    "hashed_semistandard_like_name",
                    path,
                    source="pmx.bones",
                    index=index,
                    name=native_name,
                    english_name=english_name,
                    converted=converted,
                    detail="semistandard-like name falls back to hash tokenization",
                )
            )

    for converted, entries in converted_buckets.items():
        if converted and len(entries) > 1 and _generic_ascii_name(converted):
            findings.append(
                _finding(
                    "warning",
                    "generic_converted_name_collision",
                    path,
                    source="pmx.bones",
                    name=converted,
                    converted=converted,
                    count=len(entries),
                    detail=json.dumps(entries[:8], ensure_ascii=False),
                )
            )

    for index, material in enumerate(getattr(pmx, "materials", []) or []):
        material_names.append(
            _name_inventory_record(
                path,
                category="material",
                source="pmx.materials",
                index=index,
                name=str(getattr(material, "name", "") or ""),
                english_name=str(getattr(material, "name_english", "") or ""),
                converter=converter,
            )
        )
    for index, morph in enumerate(getattr(pmx, "morphs", []) or []):
        morph_type = getattr(getattr(morph, "morph_type", None), "name", None)
        if morph_type is None:
            morph_type = str(getattr(morph, "morph_type", ""))
        morph_names.append(
            _name_inventory_record(
                path,
                category="morph",
                source="pmx.morphs",
                index=index,
                name=str(getattr(morph, "name", "") or ""),
                english_name=str(getattr(morph, "name_english", "") or ""),
                morph_type=morph_type,
                converter=converter,
            )
        )

    findings.extend(_corpus_findings(material_names))
    findings.extend(_corpus_findings(morph_names))

    return (
        {
            "file": str(path),
            "type": "pmx",
            "status": "pass",
            "duration_sec": round(time.perf_counter() - started, 3),
            "bones": len(getattr(pmx, "bones", []) or []),
            "semistandard_bones": semistandard_count,
            "english_overrides": english_override_count,
            "materials": len(getattr(pmx, "materials", []) or []),
            "morphs": len(getattr(pmx, "morphs", []) or []),
            "_material_names": material_names,
            "_morph_names": morph_names,
        },
        findings,
    )


def _audit_vmd(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    started = time.perf_counter()
    try:
        vmd = VmdData().parse_file(str(path))
    except (FileNotFoundError, MMDParseException, OSError, UnicodeError, ValueError) as exc:
        return (
            {
                "file": str(path),
                "type": "vmd",
                "status": "fail",
                "duration_sec": round(time.perf_counter() - started, 3),
                "detail": f"parse failed: {exc}",
            },
            [_finding("error", "parse_error", path, source="vmd", name=path.name, detail=str(exc))],
        )

    findings: list[dict[str, Any]] = []
    frame_names = {frame.bone_name for frame in getattr(vmd, "bone_frames", []) or []}
    for name in sorted(frame_names):
        converted = convert_mmd_bone_name_to_ascii(name) or ""
        if _looks_semistandard_like(name) and not has_semistandard_mmd_bone_name(name):
            findings.append(
                _finding(
                    "warning",
                    "unmapped_semistandard_like_vmd_bone",
                    path,
                    source="vmd.bone_frames",
                    name=name,
                    converted=converted,
                    detail="VMD bone name looks like a semistandard variant but is not in the hardcoded map",
                )
            )
        if _generic_ascii_name(name):
            findings.append(
                _finding(
                    "warning",
                    "generic_ascii_vmd_bone",
                    path,
                    source="vmd.bone_frames",
                    name=name,
                    converted=converted,
                    detail="VMD bone name is too generic to identify a semistandard target",
                )
            )
        if "HASH" in converted and _looks_semistandard_like(name):
            findings.append(
                _finding(
                    "warning",
                    "hashed_semistandard_like_vmd_bone",
                    path,
                    source="vmd.bone_frames",
                    name=name,
                    converted=converted,
                    detail="semistandard-like VMD bone name falls back to hash tokenization",
                )
            )

    return (
        {
            "file": str(path),
            "type": "vmd",
            "status": "pass",
            "duration_sec": round(time.perf_counter() - started, 3),
            "bone_frames": len(getattr(vmd, "bone_frames", []) or []),
            "unique_bone_names": len(frame_names),
        },
        findings,
    )


def _audit_path(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    suffix = path.suffix.lower()
    if suffix == ".pmx":
        return _audit_pmx(path)
    if suffix == ".vmd":
        return _audit_vmd(path)
    return (
        {
            "file": str(path),
            "type": suffix.lstrip(".") or "unknown",
            "status": "skip",
            "duration_sec": 0.0,
            "detail": "unsupported extension",
        },
        [],
    )


def _model_dependent_hint(normalized_name: str) -> str:
    markers = [marker for marker in _MODEL_DEPENDENT_MARKERS if marker in normalized_name]
    return ",".join(markers)


def _build_name_statistics(
    findings: list[dict[str, Any]],
    *,
    min_candidate_files: int,
    min_candidate_findings: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for finding in findings:
        normalized = normalize_mmd_bone_name(finding.get("name")) or str(finding.get("name", ""))
        grouped[normalized].append(finding)

    statistics: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for normalized, items in grouped.items():
        file_counts = Counter(str(item["file"]) for item in items)
        kind_counts = Counter(str(item["kind"]) for item in items)
        converted_counts = Counter(str(item.get("converted") or "") for item in items)
        source_counts = Counter(str(item["source"]) for item in items)
        original_counts = Counter(str(item["name"]) for item in items)
        converted = converted_counts.most_common(1)[0][0] if converted_counts else ""
        hash_fallback = any("HASH" in str(item.get("converted") or "") for item in items)
        generic_ascii = any(str(item["kind"]).startswith("generic_ascii") for item in items)
        model_dependent = _model_dependent_hint(normalized)
        unmapped = any(str(item["kind"]).startswith("unmapped_semistandard_like") for item in items)
        low_risk_alias = bool(_LOW_RISK_SEMISTANDARD_ALIAS_RE.fullmatch(normalized))
        registration_candidate = (
            unmapped
            and low_risk_alias
            and not hash_fallback
            and not generic_ascii
            and not model_dependent
            and len(file_counts) >= min_candidate_files
            and len(items) >= min_candidate_findings
        )

        row = {
            "normalized_name": normalized,
            "converted": converted,
            "findings": len(items),
            "files": len(file_counts),
            "registration_candidate": registration_candidate,
            "low_risk_alias": low_risk_alias,
            "hash_fallback": hash_fallback,
            "generic_ascii": generic_ascii,
            "model_dependent_hint": model_dependent,
            "kinds": dict(kind_counts),
            "sources": dict(source_counts),
            "original_names": dict(original_counts.most_common(8)),
            "examples": [
                {
                    "file": file,
                    "count": count,
                }
                for file, count in file_counts.most_common(5)
            ],
        }
        statistics.append(row)
        if registration_candidate:
            candidates.append(row)

    statistics.sort(key=lambda row: (-int(row["files"]), -int(row["findings"]), str(row["normalized_name"])))
    candidates.sort(key=lambda row: (-int(row["files"]), -int(row["findings"]), str(row["normalized_name"])))
    return statistics, candidates


def _write_reports(payload: dict[str, Any], out_json: Path, out_md: Path, limit_findings: int) -> None:
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = payload["summary"]
    lines = [
        "# Semistandard Name Audit (Bone / Material / Morph)",
        "",
        f"- Status: {payload['status']}",
        f"- Files scanned: {summary['files_scanned']} / discovered {summary['files_discovered']}",
        f"- Truncated by max-files: {summary['truncated_by_max_files']}",
        f"- PMX: {summary['pmx_files']}",
        f"- VMD: {summary['vmd_files']}",
        f"- Findings: {summary['findings']} (errors {summary['errors']}, warnings {summary['warnings']})",
        f"- Registration candidates: {summary['registration_candidates']}",
        f"- Material names: {summary['material_names']} ({summary['dangerous_materials']} dangerous names)",
        f"- Morph names: {summary['morph_names']} ({summary['dangerous_morphs']} dangerous names)",
        "",
        "| File | Type | Status | Seconds | Detail |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for result in payload["results"]:
        lines.append(
            f"| {result['file']} | {result['type']} | {result['status']} | {result['duration_sec']} | {result.get('detail', '')} |"
        )

    candidates = payload["registration_candidates"]
    if candidates:
        lines.extend(
            [
                "",
                "## Registration Candidates",
                "",
                "| Normalized name | Converted | Files | Findings | Original names | Examples |",
                "| --- | --- | ---: | ---: | --- | --- |",
            ]
        )
        for row in candidates[:limit_findings]:
            original_names = ", ".join(row["original_names"].keys())
            examples = ", ".join(Path(example["file"]).name for example in row["examples"])
            lines.append(
                f"| {row['normalized_name']} | {row['converted']} | {row['files']} | {row['findings']} | {original_names} | {examples} |"
            )

    for title, rows in (
        ("Material Dangerous Name Ranking", payload["dangerous_materials"]),
        ("Morph Dangerous Name Ranking", payload["dangerous_morphs"]),
    ):
        if not rows:
            continue
        lines.extend(
            [
                "",
                f"## {title}",
                "",
                "| Original name | Converted | Occurrences | Distinct models | Within-model repeats | Flags | Morph type | Examples |",
                "| --- | --- | ---: | ---: | ---: | --- | --- | --- |",
            ]
        )
        for row in rows[:limit_findings]:
            flags = ", ".join(row["flags"])
            morph_types = ", ".join(row.get("morph_types") or [])
            examples = ", ".join(Path(example["file"]).name for example in row["examples"])
            lines.append(
                f"| {row['original_name']} | {row['converted']} | {row['occurrences']} | "
                f"{row['distinct_models']} | {row['within_model_repeats']} | {flags} | {morph_types} | {examples} |"
            )

    statistics = payload["name_statistics"]
    if statistics:
        lines.extend(
            [
                "",
                f"## Name Statistics (first {min(limit_findings, len(statistics))} of {len(statistics)})",
                "",
                "| Normalized name | Converted | Files | Findings | Candidate | Hash | Model hint | Kinds |",
                "| --- | --- | ---: | ---: | --- | --- | --- | --- |",
            ]
        )
        for row in statistics[:limit_findings]:
            kinds = ", ".join(f"{key}:{value}" for key, value in row["kinds"].items())
            lines.append(
                f"| {row['normalized_name']} | {row['converted']} | {row['files']} | {row['findings']} | "
                f"{row['registration_candidate']} | {row['hash_fallback']} | {row['model_dependent_hint']} | {kinds} |"
            )

    findings = payload["findings"]
    if findings:
        lines.extend(
            [
                "",
                f"## Findings (first {min(limit_findings, len(findings))} of {len(findings)})",
                "",
                "| Severity | Kind | Source | Name | Converted | Occurrences | Distinct files | Morph type | File | Detail |",
                "| --- | --- | --- | --- | --- | ---: | ---: | --- | --- | --- |",
            ]
        )
        for finding in findings[:limit_findings]:
            lines.append(
                "| {severity} | {kind} | {source} | {name} | {converted} | {occurrences} | {files} | {morph_type} | {file} | {detail} |".format(
                    severity=finding["severity"],
                    kind=finding["kind"],
                    source=finding["source"],
                    name=finding["name"],
                    converted=finding.get("converted", ""),
                    occurrences=finding.get("occurrence_count", finding.get("count", 1)),
                    files=finding.get("distinct_file_count", 1),
                    morph_type=finding.get("morph_type") or "",
                    file=finding["file"],
                    detail=finding.get("detail", ""),
                )
            )

    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_audit(
    paths: list[Path],
    *,
    discovered_count: int,
    truncated_by_max_files: bool,
    out_json: Path,
    out_md: Path,
    limit_findings: int,
    min_candidate_files: int,
    min_candidate_findings: int,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    material_names: list[dict[str, Any]] = []
    morph_names: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            findings.append(_finding("error", "missing_file", path, source="input", name=path.name, detail="file not found"))
            results.append(
                {
                    "file": str(path),
                    "type": path.suffix.lower().lstrip(".") or "unknown",
                    "status": "fail",
                    "duration_sec": 0.0,
                    "detail": "file not found",
                }
            )
            continue
        result, path_findings = _audit_path(path)
        material_names.extend(result.pop("_material_names", []))
        morph_names.extend(result.pop("_morph_names", []))
        results.append(result)
        findings.extend(path_findings)

    material_statistics, dangerous_materials = _build_corpus_statistics(material_names, category="material")
    morph_statistics, dangerous_morphs = _build_corpus_statistics(morph_names, category="morph")
    findings.extend(_corpus_collision_findings(material_statistics))
    findings.extend(_corpus_collision_findings(morph_statistics))
    errors = sum(1 for finding in findings if finding["severity"] == "error")
    warnings = sum(1 for finding in findings if finding["severity"] == "warning")
    if not results:
        status = "skip"
    elif errors:
        status = "fail"
    elif warnings:
        status = "findings"
    else:
        status = "pass"

    bone_findings = [finding for finding in findings if finding.get("category") not in {"material", "morph"}]
    name_statistics, registration_candidates = _build_name_statistics(
        bone_findings,
        min_candidate_files=min_candidate_files,
        min_candidate_findings=min_candidate_findings,
    )
    payload = {
        "status": status,
        "summary": {
            "files_discovered": discovered_count,
            "files_scanned": len(paths),
            "truncated_by_max_files": truncated_by_max_files,
            "pmx_files": sum(1 for result in results if result["type"] == "pmx"),
            "vmd_files": sum(1 for result in results if result["type"] == "vmd"),
            "findings": len(findings),
            "errors": errors,
            "warnings": warnings,
            "name_statistics": len(name_statistics),
            "registration_candidates": len(registration_candidates),
            "material_names": len(material_names),
            "morph_names": len(morph_names),
            "material_statistics": len(material_statistics),
            "morph_statistics": len(morph_statistics),
            "dangerous_materials": len(dangerous_materials),
            "dangerous_morphs": len(dangerous_morphs),
            "min_candidate_files": min_candidate_files,
            "min_candidate_findings": min_candidate_findings,
        },
        "results": results,
        "registration_candidates": registration_candidates,
        "name_statistics": name_statistics,
        "findings": findings,
        "name_inventory": {
            "materials": material_names,
            "morphs": morph_names,
        },
        "material_statistics": material_statistics,
        "morph_statistics": morph_statistics,
        "dangerous_materials": dangerous_materials,
        "dangerous_morphs": dangerous_morphs,
    }
    _write_reports(payload, out_json, out_md, limit_findings)
    return payload


def _collect_input_paths(args: argparse.Namespace) -> tuple[list[Path], int, bool]:
    manifest_inputs: list[Path] = []
    for value in args.manifest or []:
        manifest = Path(value).resolve()
        if manifest.exists():
            manifest_inputs.extend(_iter_manifest_paths(manifest))
        else:
            manifest_inputs.append(manifest)
    scan_roots = [Path(value).resolve() for value in args.scan_root or []]

    def iter_inputs() -> Iterable[Path]:
        yield from manifest_inputs
        yield from _iter_scan_root_paths(scan_roots)

    return _unique_paths(iter_inputs(), args.max_files)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", action="append", help="JSON manifest containing assets/files/cases entries")
    parser.add_argument("--scan-root", action="append", help="Directory or file to scan for .pmx/.vmd assets")
    parser.add_argument("--max-files", type=int, help="Limit scanned files after discovery")
    parser.add_argument("--out-json", default="build/reports/semistandard_name_audit.json")
    parser.add_argument("--out-md", default="build/reports/semistandard_name_audit.md")
    parser.add_argument("--limit-findings", type=int, default=200)
    parser.add_argument("--min-candidate-files", type=int, default=2)
    parser.add_argument("--min-candidate-findings", type=int, default=2)
    parser.add_argument("--strict-local", action="store_true")
    args = parser.parse_args()

    paths, discovered, truncated = _collect_input_paths(args)
    payload = run_audit(
        paths,
        discovered_count=discovered,
        truncated_by_max_files=truncated,
        out_json=Path(args.out_json),
        out_md=Path(args.out_md),
        limit_findings=args.limit_findings,
        min_candidate_files=args.min_candidate_files,
        min_candidate_findings=args.min_candidate_findings,
    )
    if args.strict_local and payload["status"] in {"fail", "findings"}:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
