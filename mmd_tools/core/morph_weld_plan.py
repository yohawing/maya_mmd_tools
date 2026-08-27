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


class MorphWeldPlanError(ValueError):
    """Raised when morph data cannot be represented by a safe weld plan."""


def _morph_type_value(morph: Any) -> int:
    value = getattr(morph, "morph_type", None)
    if isinstance(value, bool) or not isinstance(value, int):
        raise MorphWeldPlanError("morph type must be an integer")
    return int(value)


def _offset_value(offset: Any, key: str, morph_index: int, offset_index: int) -> Sequence[Any]:
    expected = 3 if key == "position_offset" else 4
    try:
        value = offset[key]
        if not isinstance(value, (list, tuple)):
            invalid = True
            result = ()
        else:
            result = tuple(float(component) for component in value)
            invalid = (
                len(value) != expected
                or any(isinstance(component, bool) for component in value)
                or not all(math.isfinite(component) for component in result)
            )
    except (KeyError, TypeError, ValueError):
        invalid = True
    if invalid:
        raise MorphWeldPlanError("morph %d offset %d has invalid %s" % (morph_index, offset_index, key))
    # Signed zero has no semantic effect on PMX deformation. Normalize it so
    # explicitly-zero and absent offsets share one exact signature.
    return tuple(0.0 if component == 0.0 else component for component in result)


def _offsets_for_morph(morph: Any, morph_index: int, source_count: int) -> Dict[int, Tuple[float, ...]]:
    morph_type = _morph_type_value(morph)
    if morph_type not in VERTEX_INDEXED_MORPH_TYPES:
        return {}
    value_key = "position_offset" if morph_type == 1 else "uv_offset"
    offsets = getattr(morph, "offsets", ())
    if offsets is None:
        offsets = ()
    if not isinstance(offsets, (list, tuple)):
        raise MorphWeldPlanError("morph %d offsets must be a sequence" % morph_index)

    # PMX offsets are additive.  Accumulate duplicates once so MorphConverter
    # can apply one delta per resulting Maya vertex.  Every operation is
    # validated before it is admitted to the plan.
    accumulated: Dict[int, List[float]] = {}
    for offset_index, offset in enumerate(offsets):
        try:
            source_index = offset["vertex_index"]
        except (KeyError, TypeError):
            raise MorphWeldPlanError(
                "morph %d contains an invalid %s offset" % (morph_index, value_key)
            )
        if (
            isinstance(source_index, bool)
            or not isinstance(source_index, int)
            or source_index < 0
            or source_index >= source_count
        ):
            raise MorphWeldPlanError(
                "morph %d contains an invalid %s offset" % (morph_index, value_key)
            )
        values = _offset_value(offset, value_key, morph_index, offset_index)
        target = accumulated.setdefault(source_index, [0.0] * len(values))
        for component_index, component in enumerate(values):
            target[component_index] += component
            if not math.isfinite(target[component_index]):
                raise MorphWeldPlanError(
                    "morph %d contains an invalid %s offset" % (morph_index, value_key)
                )

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
    source_count = max(
        local_count,
        max(source_to_local.keys(), default=-1) + 1 if source_to_local else 0,
        max(
            (
                offset.get("vertex_index", -1)
                for offset in (getattr(morph, "offsets", ()) or ())
                if isinstance(offset, Mapping)
                and isinstance(offset.get("vertex_index"), int)
                and not isinstance(offset.get("vertex_index"), bool)
            ),
            default=-1,
        )
        + 1,
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
