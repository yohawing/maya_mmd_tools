"""Pure logic for reading and categorizing PMX morph metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List

PANEL_NAMES: Dict[int, str] = {
    0: "System",
    1: "Eyebrow",
    2: "Eye",
    3: "Mouth",
    4: "Other",
}


@dataclass(frozen=True)
class MorphInfo:
    """Read-only morph metadata for picker categorization."""

    name: str
    name_english: str
    panel: int
    morph_type: str
    index: int


@dataclass(frozen=True)
class CategorizedMorphs:
    """Morphs grouped by PMX panel for picker display."""

    eyebrow: tuple[MorphInfo, ...]
    eye: tuple[MorphInfo, ...]
    mouth: tuple[MorphInfo, ...]
    other: tuple[MorphInfo, ...]


def categorize_morphs(morphs: Iterable[MorphInfo]) -> CategorizedMorphs:
    """Group morphs by panel, excluding system/reserved (panel 0)."""
    eyebrow: List[MorphInfo] = []
    eye: List[MorphInfo] = []
    mouth: List[MorphInfo] = []
    other: List[MorphInfo] = []

    for morph in morphs:
        if morph.panel == 0:
            continue
        if morph.panel == 1:
            eyebrow.append(morph)
        elif morph.panel == 2:
            eye.append(morph)
        elif morph.panel == 3:
            mouth.append(morph)
        else:
            other.append(morph)

    return CategorizedMorphs(
        eyebrow=tuple(eyebrow),
        eye=tuple(eye),
        mouth=tuple(mouth),
        other=tuple(other),
    )


def read_morph_list_from_metadata(morph_metadata_dicts: list[dict]) -> list[MorphInfo]:
    """Build :class:`MorphInfo` list from raw metadata dicts, sorted by index."""
    morphs = [_morph_info_from_dict(entry) for entry in morph_metadata_dicts]
    return sorted(morphs, key=lambda morph: morph.index)


def read_morph_list_from_blendshape_json(
    names_json: dict[str, str],
    panel: int = 4,
) -> list[MorphInfo]:
    """Build vertex morph list from blendShape weight-index JSON mapping."""
    morphs: List[MorphInfo] = []
    for key, raw_name in names_json.items():
        try:
            index = int(key)
        except (TypeError, ValueError):
            continue
        morphs.append(
            MorphInfo(
                name=str(raw_name),
                name_english="",
                panel=panel,
                morph_type="vertex",
                index=index,
            )
        )
    return sorted(morphs, key=lambda morph: morph.index)


def _morph_info_from_dict(entry: dict) -> MorphInfo:
    return MorphInfo(
        name=_coerce_str(entry.get("name")),
        name_english=_coerce_str(entry.get("name_english")),
        panel=_coerce_int(entry.get("panel"), default=0),
        morph_type=_coerce_str(entry.get("morph_type"), default="vertex"),
        index=_coerce_int(entry.get("index"), default=-1),
    )


def _coerce_str(value: object, *, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _coerce_int(value: object, *, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default