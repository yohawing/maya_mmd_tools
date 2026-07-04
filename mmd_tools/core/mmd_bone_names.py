"""MMD bone-name normalization helpers for Maya-safe node names."""

from __future__ import annotations

import hashlib
import re
import unicodedata

from .unicode_converter import get_converter


_BONE_NAME_REPLACEMENTS = {
    "捻": "捩",
    "肘": "ひじ",
    "膝": "ひざ",
    "つまさき": "つま先",
}

_BONE_TOKEN_MAP = {
    "全ての親": "master",
    "操作中心": "manipulation_center",
    "センター": "center",
    "グルーブ": "groove",
    "上半身": "upper_body",
    "下半身": "lower_body",
    "足IK親": "leg_ik_parent",
    "足先": "toe",
    "つま先": "toe",
    "親指": "thumb",
    "人差指": "index",
    "中指": "middle",
    "薬指": "ring",
    "小指": "pinky",
    "手首": "wrist",
    "足首": "ankle",
    "両目": "both_eyes",
    "左": "left",
    "右": "right",
    "上": "upper",
    "下": "lower",
    "前": "front",
    "後": "back",
    "腕": "arm",
    "手": "hand",
    "足": "leg",
    "肩": "shoulder",
    "ひじ": "elbow",
    "ひざ": "knee",
    "首": "neck",
    "頭": "head",
    "腰": "waist",
    "胸": "breast",
    "目": "eye",
    "親": "parent",
    "先": "end",
    "捩": "twist",
    "IK": "ik",
    "EX": "ex",
    "P": "p",
    "C": "c",
    "D": "d",
}

_BONE_TOKENS_BY_LENGTH = sorted(_BONE_TOKEN_MAP, key=len, reverse=True)
_ASCII_RUN_RE = re.compile(r"[A-Za-z0-9_]+")
_SAFE_SEPARATORS_RE = re.compile(r"[_\s\-.:|]+")


def normalize_mmd_bone_name(name: str | None) -> str | None:
    """Normalize common spelling variants before MMD bone-name tokenization."""
    if name is None:
        return None

    normalized = unicodedata.normalize("NFKC", str(name)).strip()
    for source, replacement in _BONE_NAME_REPLACEMENTS.items():
        normalized = normalized.replace(source, replacement)
    return normalized


def convert_mmd_bone_name_to_ascii(name: str | None) -> str | None:
    """Convert a PMX/MMD bone name to a readable Maya-safe ASCII node name."""
    normalized = normalize_mmd_bone_name(name)
    if normalized is None:
        return None
    if not normalized:
        return ""

    converter = get_converter()
    if converter.is_ascii_only(normalized):
        return converter.maya_safe_name(normalized)

    parts: list[str] = []
    index = 0
    while index < len(normalized):
        char = normalized[index]
        if char in {" ", "_", "-", ".", ":", "|"}:
            index += 1
            continue

        ascii_match = _ASCII_RUN_RE.match(normalized, index)
        if ascii_match:
            parts.extend(_ascii_run_to_parts(ascii_match.group(0)))
            index = ascii_match.end()
            continue

        matched = False
        for token in _BONE_TOKENS_BY_LENGTH:
            if normalized.startswith(token, index):
                parts.append(_BONE_TOKEN_MAP[token])
                index += len(token)
                matched = True
                break
        if matched:
            continue

        unknown_start = index
        index += 1
        while index < len(normalized) and not _starts_known_token_or_ascii(normalized, index):
            index += 1
        parts.append(_hash_unknown_token(normalized[unknown_start:index]))

    result = "_".join(part for part in parts if part)
    return converter.maya_safe_name(re.sub(r"_+", "_", result).strip("_"))


def _ascii_run_to_parts(text: str) -> list[str]:
    safe_text = _SAFE_SEPARATORS_RE.sub("_", text).strip("_")
    if not safe_text:
        return []
    return [part.lower() for part in safe_text.split("_") if part]


def _starts_known_token_or_ascii(text: str, index: int) -> bool:
    if _ASCII_RUN_RE.match(text, index):
        return True
    return any(text.startswith(token, index) for token in _BONE_TOKENS_BY_LENGTH)


def _hash_unknown_token(text: str) -> str:
    return "HASH" + hashlib.md5(text.encode("utf-8")).hexdigest()[:8]
