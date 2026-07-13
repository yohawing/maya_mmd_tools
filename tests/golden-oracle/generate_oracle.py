"""Generate provenance-stamped candidate oracle JSONL files safely.

By default outputs are written below ``build/golden-oracle/candidate``. The
tracked oracle directory is writable only with the explicit ``--write-tracked``
flag.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

SCRIPT = Path(__file__).resolve()
REPO_ROOT = SCRIPT.parents[2]
DEFAULT_MANIFEST = SCRIPT.parent / "manifest.json"
DEFAULT_OUT_DIR = REPO_ROOT / "build" / "golden-oracle" / "candidate"
TRACKED_ORACLE_DIR = SCRIPT.parent / "oracle"
EXPECTED_RUNTIME_ABI = 2
_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")


def _resolve_manifest_path(manifest_path: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    return path.resolve() if path.is_absolute() else (manifest_path.parent / path).resolve()


def _case_filename(case: Dict[str, Any]) -> str:
    oracle_path = case.get("oracle", {}).get("path")
    return Path(oracle_path).name if oracle_path else f"{case['name']}.oracle.jsonl"


def _output_path(
    manifest_path: Path,
    case: Dict[str, Any],
    out_dir: Path,
    write_tracked: bool,
) -> Path:
    if write_tracked:
        oracle_path = case.get("oracle", {}).get("path")
        if not oracle_path:
            raise ValueError(f"tracked oracle path is missing: {case.get('name')}")
        output = _resolve_manifest_path(manifest_path, oracle_path)
        try:
            output.relative_to(TRACKED_ORACLE_DIR.resolve())
        except ValueError as exc:
            raise ValueError(f"tracked oracle path escapes oracle directory: {output}") from exc
        return output
    return out_dir.resolve() / _case_filename(case)


def _bundled_runtime_path() -> Path:
    system = platform.system()
    platform_dir = "win64" if system == "Windows" else "macos" if system == "Darwin" else "linux"
    names = (
        ["mmd_runtime_ffi.dll", "mmd_anim_ffi.dll"]
        if system == "Windows"
        else ["libmmd_runtime_ffi.dylib", "mmd_runtime_ffi.dylib"]
        if system == "Darwin"
        else ["libmmd_runtime_ffi.so", "mmd_runtime_ffi.so"]
    )
    base = REPO_ROOT / "mmd_tools" / "native" / platform_dir
    for name in names:
        candidate = base / name
        if candidate.is_file():
            return candidate.resolve()
    raise RuntimeError(f"bundled runtime library was not found under {base}")


def _workspace_version(cargo_toml: Path, explicit: Optional[str] = None) -> str:
    if explicit is not None:
        if not _SEMVER_RE.fullmatch(explicit):
            raise ValueError(f"invalid semantic version: {explicit!r}")
        return explicit
    text = cargo_toml.read_text(encoding="utf-8")
    section = re.search(r"(?ms)^\[workspace\.package\]\s*$\n(.*?)(?=^\[|\Z)", text)
    match = re.search(r"(?m)^version\s*=\s*\"([^\"]+)\"", section.group(1)) if section else None
    if not match or not _SEMVER_RE.fullmatch(match.group(1)):
        raise RuntimeError(f"workspace.package.version could not be established: {cargo_toml}")
    return match.group(1)


def _submodule_commit(repo: Path) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        commit = result.stdout.strip()
        return commit if re.fullmatch(r"[0-9a-fA-F]{40}", commit) else None
    except (OSError, subprocess.SubprocessError):
        return None


def _establish_provenance(
    requested_path: Path,
    version: str,
    runtime_module: Any,
    expected_abi: int = EXPECTED_RUNTIME_ABI,
) -> Dict[str, Any]:
    requested = requested_path.resolve(strict=True)
    lib = runtime_module.get_mmd_runtime_library()
    if lib is None:
        raise RuntimeError(f"runtime library failed to load: {requested}")
    loaded_path = getattr(runtime_module, "_runtime_loader", None)
    actual_raw = getattr(loaded_path, "_runtime_lib_path", None) or getattr(lib, "_name", None)
    if not actual_raw:
        raise RuntimeError("loaded runtime path could not be established")
    actual = Path(actual_raw).resolve(strict=True)
    if actual != requested:
        raise RuntimeError(f"loaded runtime path mismatch: requested={requested}, actual={actual}")
    try:
        abi = int(lib.mmd_runtime_abi_version())
        flags_func = getattr(lib, "mmd_runtime_feature_flags")
        feature_flags = int(flags_func())
    except Exception as exc:
        raise RuntimeError("runtime ABI or feature flags could not be established") from exc
    if abi != expected_abi:
        raise RuntimeError(f"runtime ABI mismatch: expected={expected_abi}, actual={abi}")
    digest = hashlib.sha256(actual.read_bytes()).hexdigest()
    if len(digest) != 64:
        raise RuntimeError("runtime SHA-256 could not be established")
    commit = _submodule_commit(REPO_ROOT / "external" / "mmd-anim")
    if commit is None:
        raise RuntimeError("mmd-anim source commit could not be established")
    return {
        "mmdAnimVersion": version,
        "runtimeRequestedPath": str(requested),
        "runtimeLoadedPath": str(actual),
        "runtimeSha256": digest,
        "runtimeAbi": abi,
        "runtimeFeatureFlags": feature_flags,
        "mmdAnimCommit": commit,
    }


def _generate_case(
    manifest_path: Path,
    case: Dict[str, Any],
    output_path: Path,
    provenance: Dict[str, Any],
) -> Path:
    """Generate one oracle file after runtime provenance has been verified."""
    from mmd_tools.core.mmd_parser import parse_pmx_file
    from mmd_tools.core.native.mmd_anim_runtime import MmdRuntimeClip, MmdRuntimeInstance, MmdRuntimeModel

    assets = case.get("assets", {})
    pmx_path = _resolve_manifest_path(manifest_path, assets["model"])
    vmd_path = _resolve_manifest_path(manifest_path, assets["motion"])
    frames = [int(frame) for frame in case.get("frames", [])]
    if not frames:
        raise ValueError(f"case has no frames: {case.get('name')}")

    pmx_data = parse_pmx_file(str(pmx_path))
    bone_names = [bone.name for bone in pmx_data.bones]
    model = MmdRuntimeModel.from_pmx_bytes(pmx_path.read_bytes())
    clip = MmdRuntimeClip.from_vmd_bytes_for_model(model, vmd_path.read_bytes()) if model else None
    instance = MmdRuntimeInstance.for_model(model) if model else None
    if model is None or clip is None or instance is None:
        raise RuntimeError(f"runtime object creation failed for {case.get('name')}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            for frame in frames:
                if not instance.evaluate_clip_frame(clip, float(frame)):
                    raise RuntimeError(f"evaluate_clip_frame failed at frame {frame}")
                matrices = instance.get_world_matrices()
                if matrices is None:
                    raise RuntimeError(f"get_world_matrices failed at frame {frame}")
                bones: List[Dict[str, Any]] = [
                    {
                        "index": index,
                        "name": bone_names[index] if index < len(bone_names) else f"bone_{index}",
                        "worldMatrix": [float(value) for value in matrix],
                    }
                    for index, matrix in enumerate(matrices)
                ]
                source = {
                    "mmdVersion": "mmd-anim",
                    "dumperVersion": "1.0.0",
                    "backend": "mmd-anim.ffi",
                    "model": str(pmx_path),
                    "motion": str(vmd_path),
                    "evaluatedFrame": float(frame),
                    **provenance,
                }
                record = {
                    "schemaVersion": 1,
                    "source": source,
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
                stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        temporary.replace(output_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return output_path


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    destinations = parser.add_mutually_exclusive_group()
    destinations.add_argument("--out-dir")
    destinations.add_argument("--write-tracked", action="store_true")
    parser.add_argument("--ffi-path")
    parser.add_argument("--mmd-anim-version")
    parser.add_argument("--case", action="append", dest="cases")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    manifest_path = Path(args.manifest).resolve()
    out_dir = Path(args.out_dir).resolve() if args.out_dir else DEFAULT_OUT_DIR
    requested_runtime = Path(args.ffi_path).resolve() if args.ffi_path else _bundled_runtime_path()
    if not requested_runtime.is_file():
        raise RuntimeError(f"requested runtime library does not exist: {requested_runtime}")

    # The loader snapshots its candidates at import time, so set this first.
    os.environ["MMD_ANIM_FFI_PATH"] = str(requested_runtime)
    sys.path.insert(0, str(REPO_ROOT))
    from mmd_tools.core.native import mmd_anim_runtime as runtime_module

    version = _workspace_version(
        REPO_ROOT / "external" / "mmd-anim" / "Cargo.toml",
        args.mmd_anim_version,
    )
    provenance = _establish_provenance(requested_runtime, version, runtime_module)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases = [case for case in manifest.get("cases", []) if case.get("kind", "") in ("motion-numeric", "")]
    if args.cases:
        requested_cases = set(args.cases)
        known = {case.get("name") for case in cases}
        missing = sorted(requested_cases - known)
        if missing:
            raise ValueError(f"unknown --case value(s): {', '.join(missing)}")
        cases = [case for case in cases if case.get("name") in requested_cases]

    failures = 0
    for case in cases:
        try:
            output = _output_path(manifest_path, case, out_dir, args.write_tracked)
            print(f"Generating oracle: {case.get('name')} -> {output}")
            _generate_case(manifest_path, case, output, provenance)
        except Exception as exc:
            print(f"FAILED {case.get('name')}: {exc}", file=sys.stderr)
            failures += 1
    print(f"Done: {len(cases) - failures}/{len(cases)} succeeded")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
