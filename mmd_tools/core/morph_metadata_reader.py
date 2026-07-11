"""Pure logic for reading and categorizing PMX morph metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional

# English labels used by AnimationPresenter morph picker groups.
PANEL_NAMES: Dict[int, str] = {
    0: "System",
    1: "Eyebrow",
    2: "Eye",
    3: "Mouth",
    4: "Other",
}

# Japanese MorphTab filter labels (panels 1-4). Panel 0 is intentionally absent.
PANEL_GROUP_LABELS: Dict[int, str] = {
    1: "眉",
    2: "目",
    3: "口",
    4: "その他",
}

# Stable MorphTab group order for filter UI.
MORPH_TAB_GROUP_ORDER: tuple[str, ...] = ("眉", "目", "口", "その他")


def parse_blendshape_morph_entries(parsed: object) -> Dict[int, Dict[str, object]]:
    """Normalize new object and legacy string blendShape metadata schemas."""
    if not isinstance(parsed, dict):
        return {}
    result = {}
    for key, value in parsed.items():
        try:
            weight_index = int(key)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            entry = {"name": str(value.get("name", ""))}
            try:
                entry["index"] = int(value["index"])
            except (KeyError, TypeError, ValueError):
                pass
        else:
            entry = {"name": str(value)}
        result[weight_index] = entry
    return result


def parse_blendshape_morph_names(parsed: object) -> Dict[int, str]:
    """Return weight-index to raw PMX name for either stored schema."""
    return {
        index: str(entry["name"])
        for index, entry in parse_blendshape_morph_entries(parsed).items()
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


def panel_display_group(panel: int) -> Optional[str]:
    """Return MorphTab Japanese group label for *panel*.

    System/reserved panel ``0`` returns ``None`` (excluded from user-facing
    filter groups). Unknown panels map to ``その他``, matching
    :func:`categorize_morphs`.
    """
    if panel == 0:
        return None
    return PANEL_GROUP_LABELS.get(panel, PANEL_GROUP_LABELS[4])


def morph_info_from_presenter_entry(name: str, data: Mapping[str, object]) -> MorphInfo:
    """Build :class:`MorphInfo` from MorphPresenter ``morph_data`` entry.

    Missing ``panel`` defaults to Other (4), not System (0), so incomplete
    scene metadata still participates in user-facing grouping.
    """
    panel_raw = data.get("panel")
    if panel_raw is None:
        panel = 4
    else:
        panel = _coerce_int(panel_raw, default=4)

    morph_type_raw = data.get("mmd_morph_type") or data.get("morph_type")
    if morph_type_raw:
        morph_type = _coerce_str(morph_type_raw, default="vertex")
    elif data.get("_pmx_type_raw"):
        morph_type = {
            0: "group",
            1: "vertex",
            2: "bone",
            3: "uv",
            4: "uv",
            5: "uv",
            6: "uv",
            7: "uv",
            8: "material",
            9: "flip",
            10: "impulse",
        }.get(_coerce_int(data.get("type"), default=1), "vertex")
    else:
        # MorphPresenter stores PMX morph type as integer ``type``.
        type_index = _coerce_int(data.get("type"), default=0)
        morph_type = {
            0: "vertex",
            10: "bone",
            11: "material",
            12: "group",
        }.get(type_index, "vertex")

    return MorphInfo(
        name=name,
        name_english=_coerce_str(data.get("name_en") or data.get("name_english")),
        panel=panel,
        morph_type=morph_type,
        index=_coerce_int(data.get("index"), default=-1),
    )


def group_morph_names_by_panel(morphs: Iterable[MorphInfo]) -> Dict[str, List[str]]:
    """Map MorphTab Japanese labels to morph names via :func:`categorize_morphs`.

    Panel 0 morphs are excluded. Only panels 1-4 labels are returned.
    """
    categorized = categorize_morphs(morphs)
    return {
        PANEL_GROUP_LABELS[1]: [m.name for m in categorized.eyebrow],
        PANEL_GROUP_LABELS[2]: [m.name for m in categorized.eye],
        PANEL_GROUP_LABELS[3]: [m.name for m in categorized.mouth],
        PANEL_GROUP_LABELS[4]: [m.name for m in categorized.other],
    }


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
    for key, raw_entry in names_json.items():
        try:
            index = int(key)
        except (TypeError, ValueError):
            continue
        raw_name = raw_entry.get("name", "") if isinstance(raw_entry, dict) else raw_entry
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
