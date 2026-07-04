"""Audit local PMX/VMD assets for semistandard bone-name conversion gaps."""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import defaultdict
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
_SIDE_PREFIX_RE = re.compile(r"^[左右].+")


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


def _unique_paths(paths: Iterable[Path], max_files: int | None) -> tuple[list[Path], int]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
        if max_files is not None and len(result) >= max_files:
            break
    return result, len(seen)


def _audit_pmx(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    started = time.perf_counter()
    findings: list[dict[str, Any]] = []
    converted_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
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

    return (
        {
            "file": str(path),
            "type": "pmx",
            "status": "pass",
            "duration_sec": round(time.perf_counter() - started, 3),
            "bones": len(getattr(pmx, "bones", []) or []),
            "semistandard_bones": semistandard_count,
            "english_overrides": english_override_count,
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


def _write_reports(payload: dict[str, Any], out_json: Path, out_md: Path, limit_findings: int) -> None:
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = payload["summary"]
    lines = [
        "# Semistandard Bone Name Audit",
        "",
        f"- Status: {payload['status']}",
        f"- Files scanned: {summary['files_scanned']} / discovered {summary['files_discovered']}",
        f"- PMX: {summary['pmx_files']}",
        f"- VMD: {summary['vmd_files']}",
        f"- Findings: {summary['findings']} (errors {summary['errors']}, warnings {summary['warnings']})",
        "",
        "| File | Type | Status | Seconds | Detail |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for result in payload["results"]:
        lines.append(
            f"| {result['file']} | {result['type']} | {result['status']} | {result['duration_sec']} | {result.get('detail', '')} |"
        )

    findings = payload["findings"]
    if findings:
        lines.extend(
            [
                "",
                f"## Findings (first {min(limit_findings, len(findings))} of {len(findings)})",
                "",
                "| Severity | Kind | Source | Name | Converted | File | Detail |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for finding in findings[:limit_findings]:
            lines.append(
                "| {severity} | {kind} | {source} | {name} | {converted} | {file} | {detail} |".format(
                    severity=finding["severity"],
                    kind=finding["kind"],
                    source=finding["source"],
                    name=finding["name"],
                    converted=finding.get("converted", ""),
                    file=finding["file"],
                    detail=finding.get("detail", ""),
                )
            )

    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_audit(
    paths: list[Path],
    *,
    discovered_count: int,
    out_json: Path,
    out_md: Path,
    limit_findings: int,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
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
        results.append(result)
        findings.extend(path_findings)

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

    payload = {
        "status": status,
        "summary": {
            "files_discovered": discovered_count,
            "files_scanned": len(paths),
            "pmx_files": sum(1 for result in results if result["type"] == "pmx"),
            "vmd_files": sum(1 for result in results if result["type"] == "vmd"),
            "findings": len(findings),
            "errors": errors,
            "warnings": warnings,
        },
        "results": results,
        "findings": findings,
    }
    _write_reports(payload, out_json, out_md, limit_findings)
    return payload


def _collect_input_paths(args: argparse.Namespace) -> tuple[list[Path], int]:
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
    parser.add_argument("--strict-local", action="store_true")
    args = parser.parse_args()

    paths, discovered = _collect_input_paths(args)
    payload = run_audit(
        paths,
        discovered_count=discovered,
        out_json=Path(args.out_json),
        out_md=Path(args.out_md),
        limit_findings=args.limit_findings,
    )
    if args.strict_local and payload["status"] in {"fail", "findings"}:
        return 1
    return 1 if payload["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
