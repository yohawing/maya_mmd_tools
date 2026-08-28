"""Focused contracts for sparse morph-equivalent UV seam welding."""

from types import SimpleNamespace

import pytest

from mmd_tools.core.morph_delta_mapping import (
    collect_morph_delta,
    map_morph_deltas_to_local,
)
from mmd_tools.core.pmx_data.morph import PmxMorphType


def _morph(morph_type, offsets):
    return SimpleNamespace(morph_type=morph_type, offsets=offsets)


def test_duplicate_offsets_are_accumulated_once_for_morph_application():
    morph = _morph(
        PmxMorphType.VertexMorph,
        [
            {"vertex_index": 0, "position_offset": (1.0, 0.0, 0.0)},
            {"vertex_index": 0, "position_offset": (2.0, -0.0, 0.0)},
        ],
    )
    assert collect_morph_delta(morph, 4, 1) == {0: (3.0, 0.0, 0.0)}


def test_mapping_fanout_applies_equivalent_delta_once_and_rejects_conflict():
    equivalent = _morph(
        PmxMorphType.VertexMorph,
        [
            {"vertex_index": 0, "position_offset": (1.0, 0.0, 0.0)},
            {"vertex_index": 1, "position_offset": (1.0, 0.0, 0.0)},
        ],
    )
    assert map_morph_deltas_to_local(equivalent, 3, {0: 0, 1: 0}, 1) == {
        0: (1.0, 0.0, 0.0),
    }

    conflicting = _morph(
        PmxMorphType.VertexMorph,
        [
            {"vertex_index": 0, "position_offset": (1.0, 0.0, 0.0)},
            {"vertex_index": 1, "position_offset": (2.0, 0.0, 0.0)},
        ],
    )
    with pytest.raises(ValueError, match="conflicting source deltas"):
        map_morph_deltas_to_local(conflicting, 3, {0: 0, 1: 0}, 1)


def test_mapping_skips_high_source_absent_from_material_split_mesh():
    morph = _morph(
        PmxMorphType.VertexMorph,
        [{"vertex_index": 7, "position_offset": (1.0, 0.0, 0.0)}],
    )

    assert map_morph_deltas_to_local(morph, 4, {0: 0}, 1) == {}


def test_mapping_rejects_non_sequence_vector_payload():
    morph = _morph(
        PmxMorphType.VertexMorph,
        [{"vertex_index": 0, "position_offset": {0: 1.0, 1: 2.0, 2: 3.0}}],
    )

    with pytest.raises(ValueError):
        collect_morph_delta(morph, 5, 1)
