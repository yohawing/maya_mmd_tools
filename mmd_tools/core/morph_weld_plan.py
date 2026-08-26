"""Sparse, exact deformation signatures for UV-seam welding.

The PMX morph tables are sparse: an offset only exists for a vertex that the
morph touches.  This module deliberately builds signatures from those
offsets, rather than constructing a vertex-by-morph matrix.  It is kept free
of Maya imports so the same plan can later be used by an option or command.
"""

import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


# PmxMorphType values.  Keeping these integers here avoids making this small
# planner depend on either Maya or the parser package.
VERTEX_INDEXED_MORPH_TYPES = frozenset({1, 3, 4, 5, 6, 7})
_MORPH_TYPE_NAMES = {
    "group": 0,
    "vertex": 1,
    "bone": 2,
    "uv": 3,
    "additional_uv1": 4,
    "additional_uv2": 5,
    "additional_uv3": 6,
    "additional_uv4": 7,
    "material": 8,
    "flip": 9,
    "impulse": 10,
}


class MorphWeldPlanError(ValueError):
    """Raised when morph data cannot be represented by a safe weld plan."""


def _morph_type_value(morph: Any) -> int:
    value = getattr(morph, "morph_type", morph.get("type") if isinstance(morph, Mapping) else None)
    if isinstance(value, str):
        try:
            value = _MORPH_TYPE_NAMES[value.lower()]
        except KeyError:
            raise MorphWeldPlanError("unsupported vertex-indexed morph type: %s" % value)
    if isinstance(value, bool):
        raise MorphWeldPlanError("morph type must be an integer")
    if not isinstance(value, int):
        raise MorphWeldPlanError("morph type must be an integer")
    return int(value)


def _offset_value(offset: Any, key: str, morph_index: int, offset_index: int) -> Sequence[Any]:
    if not isinstance(offset, Mapping) or key not in offset:
        raise MorphWeldPlanError(
            "morph %d offset %d is missing %s" % (morph_index, offset_index, key)
        )
    value = offset[key]
    if not isinstance(value, (list, tuple)):
        raise MorphWeldPlanError("morph %d offset %d %s must be a sequence" % (morph_index, offset_index, key))
    expected = 3 if key == "position_offset" else 4
    if len(value) != expected:
        raise MorphWeldPlanError(
            "morph %d offset %d %s must contain %d values"
            % (morph_index, offset_index, key, expected)
        )
    result = []
    for component in value:
        if isinstance(component, bool):
            raise MorphWeldPlanError("morph %d offset %d %s contains a bool" % (morph_index, offset_index, key))
        try:
            component = float(component)
        except (TypeError, ValueError):
            raise MorphWeldPlanError("morph %d offset %d %s contains a non-number" % (morph_index, offset_index, key))
        if not math.isfinite(component):
            raise MorphWeldPlanError("morph %d offset %d %s contains a non-finite value" % (morph_index, offset_index, key))
        # Signed zero has no semantic effect on PMX deformation.  Normalize it
        # so explicitly-zero and absent offsets share one exact signature.
        result.append(0.0 if component == 0.0 else component)
    return result


def _offsets_for_morph(morph: Any, morph_index: int, source_count: int) -> Dict[int, Tuple[float, ...]]:
    morph_type = _morph_type_value(morph)
    if morph_type not in VERTEX_INDEXED_MORPH_TYPES:
        return {}
    value_key = "position_offset" if morph_type == 1 else "uv_offset"
    offsets = getattr(morph, "offsets", morph.get("offsets", ()) if isinstance(morph, Mapping) else ())
    if offsets is None:
        offsets = ()
    if not isinstance(offsets, (list, tuple)):
        raise MorphWeldPlanError("morph %d offsets must be a sequence" % morph_index)

    # PMX offsets are additive.  Accumulate duplicates once so MorphConverter
    # can apply one delta per resulting Maya vertex.  Every operation is
    # validated before it is admitted to the plan.
    accumulated: Dict[int, List[float]] = {}
    for offset_index, offset in enumerate(offsets):
        if not isinstance(offset, Mapping) or "vertex_index" not in offset:
            raise MorphWeldPlanError("morph %d offset %d is missing vertex_index" % (morph_index, offset_index))
        source_index = offset["vertex_index"]
        if isinstance(source_index, bool):
            raise MorphWeldPlanError("morph %d offset %d vertex_index must be an integer" % (morph_index, offset_index))
        if not isinstance(source_index, int):
            raise MorphWeldPlanError("morph %d offset %d vertex_index must be an integer" % (morph_index, offset_index))
        source_index = int(source_index)
        if source_index < 0 or source_index >= source_count:
            raise MorphWeldPlanError(
                "morph %d offset %d vertex_index %d is outside %d vertices"
                % (morph_index, offset_index, source_index, source_count)
            )
        values = _offset_value(offset, value_key, morph_index, offset_index)
        target = accumulated.setdefault(source_index, [0.0] * len(values))
        for component_index, component in enumerate(values):
            target[component_index] += component
            if not math.isfinite(target[component_index]):
                raise MorphWeldPlanError("morph %d offset accumulation is non-finite" % morph_index)

    return {
        source_index: tuple(0.0 if value == 0.0 else value for value in values)
        for source_index, values in accumulated.items()
        if any(value != 0.0 for value in values)
    }


def build_sparse_morph_signatures(morphs: Iterable[Any], source_count: int) -> List[Tuple[Tuple[int, int, Tuple[float, ...]], ...]]:
    """Build one sparse exact morph signature for each PMX source vertex.

    Each entry is ``(morph_index, morph_type, accumulated_delta)``.  Missing
    offsets and explicit zero offsets are both the zero deformation and are
    omitted.  The result is ``O(source_count + total_indexed_offsets)`` and
    contains no source-by-morph matrix.
    """
    if isinstance(source_count, bool) or not isinstance(source_count, int) or source_count < 0:
        raise MorphWeldPlanError("source_count must be a non-negative integer")
    signatures: List[List[Tuple[int, int, Tuple[float, ...]]]] = [[] for _ in range(source_count)]
    for morph_index, morph in enumerate(morphs or ()):
        morph_type = _morph_type_value(morph)
        if morph_type not in VERTEX_INDEXED_MORPH_TYPES:
            continue
        for source_index, delta in _offsets_for_morph(morph, morph_index, source_count).items():
            signatures[source_index].append((morph_index, morph_type, delta))
    return [tuple(entries) for entries in signatures]


def collect_morph_delta(morph: Any, morph_index: int, source_count: int) -> Dict[int, Tuple[float, ...]]:
    """Return one validated, accumulated delta per source for one morph."""
    return _offsets_for_morph(morph, morph_index, source_count)


def map_morph_deltas_to_local(
    morph: Any,
    morph_index: int,
    source_to_local: Optional[Mapping[int, int]] = None,
    local_count: int = 0,
) -> Dict[int, Tuple[float, ...]]:
    """Map one morph's sparse deltas once per local vertex.

    Several PMX sources may share a Maya vertex after a seam weld. Their
    accumulated deltas must be exactly equal; otherwise the weld is not
    representable by one blendShape point and this function fails closed.
    """
    offsets = getattr(morph, "offsets", ()) or ()
    source_max = -1
    for offset in offsets:
        if not isinstance(offset, Mapping) or "vertex_index" not in offset:
            raise MorphWeldPlanError("morph %d offset is missing vertex_index" % morph_index)
        source_index = offset["vertex_index"]
        if isinstance(source_index, bool) or not isinstance(source_index, int):
            raise MorphWeldPlanError("morph %d offset vertex_index must be an integer" % morph_index)
        source_max = max(source_max, int(source_index))
    source_count = max(
        local_count,
        max(source_to_local.keys(), default=-1) + 1 if source_to_local else 0,
        source_max + 1,
    )

    mapped: Dict[int, Tuple[float, ...]] = {}
    for source_index, delta in collect_morph_delta(morph, morph_index, source_count).items():
        local_index = source_to_local.get(source_index) if source_to_local is not None else source_index
        if local_index is None:
            continue
        if local_index < 0 or local_index >= local_count:
            raise MorphWeldPlanError(
                "morph %d source vertex %d maps outside local mesh vertex count %d"
                % (morph_index, source_index, local_count)
            )
        previous = mapped.get(local_index)
        if previous is not None and previous != delta:
            raise MorphWeldPlanError(
                "morph %d has conflicting source deltas for local vertex %d"
                % (morph_index, local_index)
            )
        mapped[local_index] = delta
    return mapped
