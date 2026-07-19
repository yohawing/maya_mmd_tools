"""Pure Python helpers for on-demand MMD texture path resolution.

Maya 2024 on Windows can corrupt non-ANSI fileTextureName strings in VP2's
standard file texture backend. This module keeps the import path unchanged and
only provides safe, user-triggered copying into an ASCII workspace cache.
"""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


ALLOWED_TEXTURE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".tga",
    ".tif",
    ".tiff",
    ".dds",
    ".spa",
    ".sph",
}


@dataclass
class TextureResolution:
    """Result of classifying or resolving one texture path."""

    original_path: str
    source_path: Optional[str]
    file_texture_path: str
    cached: bool
    status: str
    reason: str = ""
    cache_path: Optional[str] = None


def encode_original_texture_path(path) -> str:
    """Encode an original texture path as ASCII-safe UTF-8 base64."""

    text = "" if path is None else os.fspath(path)
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def decode_original_texture_path(encoded) -> str:
    """Decode a legacy ASCII-safe original texture path when it is path-like."""

    if not encoded:
        return ""
    text = os.fspath(encoded)
    try:
        decoded = base64.b64decode(text.encode("ascii"), altchars=b"-_", validate=True).decode("utf-8")
    except Exception:
        return text
    if _looks_like_texture_path(decoded):
        return decoded
    return text


def _looks_like_texture_path(path) -> bool:
    text = "" if path is None else os.fspath(path)
    if not text or "\x00" in text:
        return False
    suffix = Path(text.replace("\\", "/")).suffix.lower()
    return "/" in text or "\\" in text or suffix in ALLOWED_TEXTURE_EXTENSIONS


def is_ansi_incompatible_path(path, encoding=None) -> bool:
    """Return True when *path* cannot be encoded by Windows ANSI APIs.

    Maya VP2's file texture backend can still go through the process ANSI
    codepage on Windows, even when Python can access the file through wide APIs.
    The encoding is injectable so unit tests can exercise Windows codepages on
    any platform.
    """

    if not path:
        return False
    enc = encoding or ("mbcs" if os.name == "nt" else None)
    if not enc:
        return False
    try:
        os.fspath(path).encode(enc)
    except UnicodeEncodeError:
        return True
    except LookupError:
        return False
    return False


def is_non_ascii_path(path) -> bool:
    """Return True when *path* contains non-ASCII characters."""

    if not path:
        return False
    try:
        os.fspath(path).encode("ascii")
    except UnicodeEncodeError:
        return True
    return False


def classify_unreadable_file_texture_path(file_texture_path, encoding=None) -> str:
    """Return the unreadable reason for a file texture path, or an empty string."""

    if not file_texture_path:
        return "empty_path"
    text = os.fspath(file_texture_path)
    if "?" in text:
        return "question_mark_path"
    if is_non_ascii_path(text):
        return "non_ascii_path"
    if is_ansi_incompatible_path(text, encoding=encoding):
        return "ansi_incompatible_path"
    if not Path(text).exists():
        return "missing_file"
    return ""


def is_unreadable_file_texture_path(file_texture_path) -> bool:
    """Return True when Maya's current file texture path should be repaired."""

    return bool(classify_unreadable_file_texture_path(file_texture_path))


_TEXTURE_ISSUE_DESCRIPTIONS = {
    "non_ascii_path": "Maya may fail to display this texture path",
    "ansi_incompatible_path": "Unsupported characters in path",
    "missing_file": "File not found",
    "question_mark_path": "Path corrupted on reopen",
    "empty_path": "No texture path",
    "resolved": "Fixed",
    "missing_original_path": "Original path not recorded",
    "absolute_original_path_rejected": "Absolute original path is outside the model folder",
    "parent_traversal_rejected": "Path escapes the model folder",
    "outside_model_directory_rejected": "File is outside the model folder",
    "extension_rejected": "Unsupported file type",
    "symlink_rejected": "Symbolic link not allowed",
    "source_not_found": "Source file not found",
    "source_not_file": "Source is not a file",
    "source_not_readable": "Source file is not readable",
    "cache_copy_failed": "Failed to copy texture to cache",
}


def describe_texture_issue(reason) -> str:
    """Return a plain-language description for a texture issue reason code.

    Falls back to the raw reason for unknown codes so nothing is hidden.
    """

    if not reason:
        return "Cannot be displayed"
    return _TEXTURE_ISSUE_DESCRIPTIONS.get(str(reason), str(reason))


def compute_model_hash(model_path) -> str:
    """Return a stable 16-character hash for the PMX/PMD model."""

    path = Path(model_path)
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except (OSError, shutil.Error):
        digest.update(str(path.resolve(strict=False)).encode("utf-8"))
    return digest.hexdigest()[:16]


def classify_texture_resolution(
    *,
    original_path,
    file_texture_path,
    model_path,
) -> TextureResolution:
    """Classify whether a broken file texture path can be resolved."""

    original = "" if original_path is None else os.fspath(original_path)
    current = "" if file_texture_path is None else os.fspath(file_texture_path)
    if not is_unreadable_file_texture_path(current):
        return TextureResolution(original, current, current, cached=False, status="ok")

    source, reason = find_resolvable_source(original, model_path)
    if source is None:
        return TextureResolution(original, None, current, cached=False, status="unrecoverable", reason=reason)
    return TextureResolution(original, str(source), current, cached=False, status="resolvable")


def resolve_texture_to_cache(
    *,
    original_path,
    file_texture_path,
    model_path,
    workspace_root,
) -> TextureResolution:
    """Copy a resolvable texture into the workspace ASCII cache."""

    classified = classify_texture_resolution(
        original_path=original_path,
        file_texture_path=file_texture_path,
        model_path=model_path,
    )
    if classified.status != "resolvable" or not classified.source_path:
        return classified

    try:
        cache_path = copy_texture_to_cache(
            classified.source_path,
            workspace_root,
            model_path,
            original_path=classified.original_path,
        )
    except OSError:
        return TextureResolution(
            original_path=classified.original_path,
            source_path=classified.source_path,
            file_texture_path=classified.file_texture_path,
            cached=False,
            status="unrecoverable",
            reason="cache_copy_failed",
        )
    return TextureResolution(
        original_path=classified.original_path,
        source_path=classified.source_path,
        file_texture_path=str(cache_path),
        cached=True,
        status="resolved",
        cache_path=str(cache_path),
    )


def find_resolvable_source(original_path, model_path) -> Tuple[Optional[Path], str]:
    """Find a readable texture source under the PMX/PMD parent directory."""

    if not original_path:
        return None, "missing_original_path"
    original_text = os.fspath(original_path)
    model_parent = Path(model_path).parent
    original = Path(original_text)
    if original.is_absolute():
        candidate = original
    else:
        if _has_parent_traversal(original_text):
            return None, "parent_traversal_rejected"
        candidate = model_parent / original
    return _validate_source(candidate, model_parent)


def build_texture_source_candidates(original_path, model_path) -> List[Dict[str, object]]:
    """Return the filesystem candidates checked for one PMX/PMD texture path."""

    if not original_path:
        return []
    original_text = os.fspath(original_path)
    model_parent = Path(model_path).parent
    original = Path(original_text)
    if original.is_absolute():
        candidate = original
        kind = "absolute_original"
        source, reason = _validate_source(candidate, model_parent)
    else:
        candidate = model_parent / original
        kind = "model_relative"
        if _has_parent_traversal(original_text):
            source, reason = None, "parent_traversal_rejected"
        else:
            source, reason = _validate_source(candidate, model_parent)

    return [_texture_source_candidate_record(kind, candidate, source, reason)]


def build_texture_path_diagnostics(
    *,
    original_path,
    file_texture_path,
    model_path,
    encoding=None,
) -> Dict[str, object]:
    """Return structured path diagnostics for an unresolved texture issue."""

    original_text = "" if original_path is None else os.fspath(original_path)
    current_text = "" if file_texture_path is None else os.fspath(file_texture_path)
    original = Path(original_text) if original_text else None
    return {
        "model_parent": str(Path(model_path).resolve(strict=False).parent),
        "original_path_is_absolute": bool(original and original.is_absolute()),
        "original_path_has_parent_traversal": _has_parent_traversal(original_text),
        "original_path_has_non_ascii": is_non_ascii_path(original_text),
        "original_path_ansi_incompatible": is_ansi_incompatible_path(original_text, encoding=encoding),
        "current_path_has_non_ascii": is_non_ascii_path(current_text),
        "current_path_ansi_incompatible": is_ansi_incompatible_path(current_text, encoding=encoding),
        "current_path_unreadable_reason": classify_unreadable_file_texture_path(
            current_text,
            encoding=encoding,
        ),
    }


def normalize_original_texture_key(original_path, model_path) -> str:
    """Return the logical model-parent-relative texture key used for cache naming."""

    original_text = "" if original_path is None else os.fspath(original_path)
    if not original_text:
        return ""

    model_parent = Path(model_path).resolve(strict=False).parent
    original = Path(original_text)
    if original.is_absolute():
        try:
            rel = original.resolve(strict=False).relative_to(model_parent)
        except ValueError:
            return original_text.replace("\\", "/")
        return rel.as_posix()

    normalized = Path(original_text.replace("\\", "/"))
    parts = []
    for part in normalized.parts:
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def texture_cache_dir(workspace_root, model_path) -> Path:
    """Return the workspace cache directory for one PMX/PMD model."""

    return Path(workspace_root) / "sourceimages" / "mmd_tools_texture_cache" / compute_model_hash(model_path)


def cache_path_for_original_texture(original_path, workspace_root, model_path, source_path=None) -> Path:
    """Return the deterministic cache path for an original PMX texture path."""

    key = normalize_original_texture_key(original_path, model_path)
    suffix_source = Path(source_path) if source_path else Path(key)
    suffix = suffix_source.suffix.lower()
    stem = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return texture_cache_dir(workspace_root, model_path) / f"{stem}{suffix}"


def copy_texture_to_cache(source_path, workspace_root, model_path, original_path=None) -> Path:
    """Copy source into the deterministic MMD texture cache, overwriting in place."""

    source = Path(source_path)
    cache_dir = texture_cache_dir(workspace_root, model_path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_path_for_original_texture(original_path or source.name, workspace_root, model_path, source_path=source)
    shutil.copy2(source, target)
    return target


def _validate_source(candidate: Path, model_parent: Path) -> Tuple[Optional[Path], str]:
    try:
        resolved_candidate = candidate.resolve(strict=False)
        resolved_model_parent = model_parent.resolve(strict=False)
    except OSError:
        if candidate.is_absolute():
            return None, "absolute_original_path_rejected"
        return None, "source_not_found"

    try:
        resolved_candidate.relative_to(resolved_model_parent)
    except ValueError:
        if candidate.is_absolute():
            return None, "absolute_original_path_rejected"
        return None, "outside_model_directory_rejected"
    if candidate.suffix.lower() not in ALLOWED_TEXTURE_EXTENSIONS:
        return None, "extension_rejected"
    if candidate.is_symlink():
        return None, "symlink_rejected"
    if not candidate.exists():
        return None, "source_not_found"
    if not candidate.is_file():
        return None, "source_not_file"
    try:
        with candidate.open("rb"):
            pass
    except OSError:
        return None, "source_not_readable"
    return candidate, ""


def _texture_source_candidate_record(
    kind: str,
    candidate: Path,
    source: Optional[Path],
    reason: str,
) -> Dict[str, object]:
    return {
        "kind": kind,
        "path": str(candidate),
        "accepted": source is not None,
        "reason": reason,
        "resolved_path": str(source) if source is not None else "",
        "exists": _path_exists(candidate),
        "is_file": _path_is_file(candidate),
        "is_absolute": candidate.is_absolute(),
    }


def _path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _path_is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _has_parent_traversal(path_text: str) -> bool:
    normalized = path_text.replace("\\", "/")
    return ".." in Path(normalized).parts
