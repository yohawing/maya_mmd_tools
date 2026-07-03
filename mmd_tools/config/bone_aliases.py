"""Shared MMD bone name alias tables for importer and rig helpers."""

from __future__ import annotations

from types import MappingProxyType
from typing import Dict, Iterable, Tuple

from mmd_tools.validation.bone_validator import BoneValidator


_EXTRA_BONE_ALIASES: Dict[str, Tuple[str, ...]] = {
    "センター": ("centre",),
    "下半身": ("lowerbody",),
    "左足": ("leftleg", "left_thigh", "左もも"),
    "右足": ("rightleg", "right_thigh", "右もも"),
    "腰": ("koshi",),
    "全ての親": ("マスター",),
}


def _build_bone_aliases() -> Dict[str, Tuple[str, ...]]:
    aliases: Dict[str, Tuple[str, ...]] = {}
    source_tables = (
        BoneValidator.STANDARD_BONES,
        BoneValidator.SEMI_STANDARD_BONES,
    )
    for source in source_tables:
        for standard_name, variations in source.items():
            aliases[standard_name] = _unique_names(
                (
                    standard_name,
                    *variations,
                    *_EXTRA_BONE_ALIASES.get(standard_name, ()),
                )
            )
    for side_fingers in BoneValidator.FINGER_BONES.values():
        for finger_bones in side_fingers.values():
            for standard_name, variations in finger_bones.items():
                aliases[standard_name] = _unique_names((standard_name, *variations))
    return aliases


def _unique_names(names: Iterable[str]) -> Tuple[str, ...]:
    seen = set()
    result = []
    for name in names:
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return tuple(result)


BONE_NAME_ALIASES = MappingProxyType(_build_bone_aliases())


def get_bone_aliases(standard_name: str) -> Tuple[str, ...]:
    """Return known exact/partial name aliases for an MMD standard bone."""
    return BONE_NAME_ALIASES.get(standard_name, (standard_name,))


def get_original_bone_name_aliases(standard_name: str) -> Tuple[str, ...]:
    """Return non-ASCII aliases suitable for exact original PMX bone-name checks."""
    return tuple(name for name in get_bone_aliases(standard_name) if not name.isascii())
