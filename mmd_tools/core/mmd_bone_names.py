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

_SEMISTANDARD_BONE_MAP = {
    "全ての親": "master",
    "操作中心": "manipulation_center",
    "グルーブ": "groove",
    "上半身2": "upper_body_2",
    "腰": "waist",
    "胸親": "breast_parent",
    "左腕捩": "left_arm_twist",
    "右腕捩": "right_arm_twist",
    "左腕捩先": "left_arm_twist_end",
    "右腕捩先": "right_arm_twist_end",
    "左手捩": "left_wrist_twist",
    "右手捩": "right_wrist_twist",
    "左手捩先": "left_wrist_twist_end",
    "右手捩先": "right_wrist_twist_end",
    "左足IK": "left_leg_ik",
    "右足IK": "right_leg_ik",
    "左足IK先": "left_leg_ik_end",
    "右足IK先": "right_leg_ik_end",
    "左足IK親": "left_leg_ik_parent",
    "右足IK親": "right_leg_ik_parent",
    "左つま先IK": "left_toe_ik",
    "右つま先IK": "right_toe_ik",
    "左つま先IK先": "left_toe_ik_end",
    "右つま先IK先": "right_toe_ik_end",
    "左足先EX": "left_toe_ex",
    "右足先EX": "right_toe_ex",
    "左親指0": "left_thumb_0",
    "右親指0": "right_thumb_0",
    "左肩P": "left_shoulder_p",
    "右肩P": "right_shoulder_p",
    "左肩C": "left_shoulder_c",
    "右肩C": "right_shoulder_c",
    "左腕D": "left_arm_d",
    "右腕D": "right_arm_d",
    "左腕捩D": "left_arm_twist_d",
    "右腕捩D": "right_arm_twist_d",
    "左ひじD": "left_elbow_d",
    "右ひじD": "right_elbow_d",
    "左手首D": "left_wrist_d",
    "右手首D": "right_wrist_d",
    "左手捩D": "left_wrist_twist_d",
    "右手捩D": "right_wrist_twist_d",
    "左足D": "left_leg_d",
    "右足D": "right_leg_d",
    "左ひざD": "left_knee_d",
    "右ひざD": "right_knee_d",
    "左足首D": "left_ankle_d",
    "右足首D": "right_ankle_d",
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
    "もも": "thigh",
    "太もも": "thigh",
    "首": "neck",
    "頭": "head",
    "腰": "waist",
    "骨盤": "pelvis",
    "胸": "breast",
    "目": "eye",
    "親": "parent",
    "先": "end",
    "元": "base",
    "向き": "direction",
    "補助": "assist",
    "調整": "adjust",
    "抽出": "extract",
    "軸": "axis",
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
    semistandard_name = convert_semistandard_mmd_bone_name_to_ascii(normalized)
    if semistandard_name is not None:
        return converter.maya_safe_name(semistandard_name)

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


def convert_semistandard_mmd_bone_name_to_ascii(name: str | None) -> str | None:
    """Return the hardcoded ASCII name for known standard and semistandard MMD bones."""
    normalized = normalize_mmd_bone_name(name)
    if normalized is None:
        return None
    converted = _SEMISTANDARD_BONE_MAP.get(normalized)
    if converted is not None:
        return converted

    numbered = re.fullmatch(r"(.+?)([0-9]+)", normalized)
    if numbered:
        base = _SEMISTANDARD_BONE_MAP.get(numbered.group(1))
        if base is not None:
            return f"{base}_{numbered.group(2)}"
    return None


def has_semistandard_mmd_bone_name(name: str | None) -> bool:
    """Return True when a name is handled by the hardcoded MMD bone-name table."""
    return convert_semistandard_mmd_bone_name_to_ascii(name) is not None


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
