"""Material property helpers shared by mesh conversion and shader setup."""

from __future__ import annotations

from mmd_tools.core.pmx_data.material import PmxDrawFlag

PMX_DOUBLE_SIDED_DRAW_FLAG = int(getattr(PmxDrawFlag, "DOUBLE_SIDED", 0x01))
PMX_EDGE_DRAWING_DRAW_FLAG = int(getattr(PmxDrawFlag, "EDGE_DRAWING", 0x10))


def material_is_double_sided(material) -> bool:
    """Return True when a PMX material has the DOUBLE_SIDED draw flag."""
    draw_flag = getattr(material, "draw_flag", None)
    if draw_flag is None:
        return False
    try:
        return bool(int(draw_flag) & PMX_DOUBLE_SIDED_DRAW_FLAG)
    except (TypeError, ValueError):
        return False


def material_has_outline(material, *, is_pmd: bool = False) -> bool:
    """Return whether the authored PMD/PMX material requests an outline."""
    raw_flag = getattr(material, "edge_flag" if is_pmd else "draw_flag", None)
    if raw_flag is None:
        return False
    try:
        return bool(int(raw_flag)) if is_pmd else bool(int(raw_flag) & PMX_EDGE_DRAWING_DRAW_FLAG)
    except (TypeError, ValueError):
        return False
