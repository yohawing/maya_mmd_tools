"""Pure helpers for parsing Nox arguments and handling local artifacts.

The functions in this module deliberately do not depend on Nox sessions or
project-specific release policy.  The noxfile keeps compatibility wrappers
where a helper needs the noxfile's patched repository root.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional


REPO_ROOT = Path(__file__).resolve().parent.parent


def _option(args: list[str], name: str, default: str) -> str:
    """Return a string option value from nox positional arguments."""
    try:
        index = args.index(name)
    except ValueError:
        return default
    try:
        return args[index + 1]
    except IndexError as exc:
        raise ValueError(f"{name} requires a value") from exc


def _options(args: list[str], name: str) -> list[str]:
    """Return all string option values from nox positional arguments."""
    values: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == name:
            if i + 1 >= len(args):
                raise ValueError(f"{name} requires a value")
            values.append(args[i + 1])
            i += 2
            continue
        i += 1
    return values


def _without_option(args: list[str], name: str) -> list[str]:
    """Return args with a single value option removed."""
    filtered: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == name:
            if i + 1 >= len(args):
                raise ValueError(f"{name} requires a value")
            i += 2
            continue
        filtered.append(args[i])
        i += 1
    return filtered


def _has_flag(args: list[str], name: str) -> bool:
    """Return True if a boolean flag is present in positional arguments."""
    return name in args


def _cargo_args_with_physics_feature(args: list[str]) -> list[str]:
    """Return cargo args that enable the native Bullet physics runtime feature."""
    feature = "physics-bullet-native"
    cargo_args = list(args)
    for index, value in enumerate(cargo_args):
        if value == "--features":
            if index + 1 >= len(cargo_args):
                raise ValueError("--features requires a value")
            features = cargo_args[index + 1].replace(",", " ").split()
            if feature not in features:
                features.append(feature)
                cargo_args[index + 1] = " ".join(features)
            return cargo_args
        if value.startswith("--features="):
            features = value.split("=", 1)[1].replace(",", " ").split()
            if feature not in features:
                features.append(feature)
                cargo_args[index] = "--features=" + " ".join(features)
            return cargo_args
    cargo_args.extend(["--features", feature])
    return cargo_args


def _resolve_existing_or_repo_path(value: str, root: Optional[Path] = None) -> Path:
    """Resolve an input path from absolute or repository-relative text."""
    path = Path(value)
    if not path.is_absolute():
        path = (root or REPO_ROOT) / path
    return path.resolve()


def _sha256_file(path: Path) -> str:
    """Return the SHA-256 digest for a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mmd_anim_cli_version(exe: Path, root: Optional[Path] = None) -> str:
    """Return the first non-empty line of the mmd-anim CLI version output."""
    result = subprocess.run(
        [str(exe), "--version"],
        cwd=root or REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return next((line.strip() for line in result.stdout.splitlines() if line.strip()), "")


def _download_file(url: str, destination: Path) -> None:
    """Download a URL to a local file."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as response, destination.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def _extract_archive(archive: Path, destination: Path) -> None:
    """Extract a supported mmd-anim release archive."""
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zip_file:
            zip_file.extractall(destination)
    elif archive.name.endswith(".tar.gz"):
        with tarfile.open(archive, "r:gz") as tar_file:
            tar_file.extractall(destination)
    else:
        raise ValueError(f"Unsupported archive format: {archive}")
