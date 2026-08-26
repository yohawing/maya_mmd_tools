"""Focused contracts for sparse morph-equivalent UV seam welding."""

from types import SimpleNamespace

import pytest

from mmd_tools.core.morph_weld_plan import (
    build_sparse_morph_signatures,
    collect_morph_delta,
    map_morph_deltas_to_local,
)
from mmd_tools.core.pmx_data.morph import PmxMorphType


def _morph(morph_type, offsets):
    return SimpleNamespace(morph_type=morph_type, offsets=offsets)


def test_equivalent_vertex_and_uv_signatures_match_and_missing_offsets_are_zero():
    morphs = [
        _morph(
            PmxMorphType.VertexMorph,
            [
                {"vertex_index": 0, "position_offset": (1.0, -0.0, 0.5)},
                {"vertex_index": 1, "position_offset": (1.0, 0.0, 0.5)},
            ],
        ),
        _morph(
            PmxMorphType.UVMorph,
            [
                {"vertex_index": 0, "uv_offset": (0.25, 0.0, 0.0, 0.0)},
                {"vertex_index": 1, "uv_offset": (0.25, 0.0, 0.0, 0.0)},
            ],
        ),
    ]

    signatures = build_sparse_morph_signatures(morphs, 3)

    assert signatures[0] == signatures[1]
    assert signatures[0] == (
        (0, int(PmxMorphType.VertexMorph), (1.0, 0.0, 0.5)),
        (1, int(PmxMorphType.UVMorph), (0.25, 0.0, 0.0, 0.0)),
    )
    assert signatures[2] == ()


def test_conflicting_source_deltas_produce_distinct_signatures():
    morph = _morph(
        PmxMorphType.VertexMorph,
        [
            {"vertex_index": 0, "position_offset": (1.0, 0.0, 0.0)},
            {"vertex_index": 1, "position_offset": (2.0, 0.0, 0.0)},
        ],
    )

    signatures = build_sparse_morph_signatures([morph], 2)

    assert signatures[0] != signatures[1]


def test_bone_morph_is_not_treated_as_vertex_indexed():
    bone_morph = _morph(
        PmxMorphType.BoneMorph,
        [{"bone_index": 0, "translation": (1.0, 0.0, 0.0)}],
    )

    assert build_sparse_morph_signatures([bone_morph], 2) == [(), ()]


def test_duplicate_offsets_are_accumulated_once_for_morph_application():
    morph = _morph(
        PmxMorphType.VertexMorph,
        [
            {"vertex_index": 0, "position_offset": (1.0, 0.0, 0.0)},
            {"vertex_index": 0, "position_offset": (2.0, -0.0, 0.0)},
        ],
    )
    assert collect_morph_delta(morph, 4, 1) == {0: (3.0, 0.0, 0.0)}
    assert build_sparse_morph_signatures([morph], 1)[0] == (
        (0, int(PmxMorphType.VertexMorph), (3.0, 0.0, 0.0)),
    )


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


@pytest.mark.parametrize(
    "offset",
    [
        {"vertex_index": 0, "position_offset": (float("nan"), 0.0, 0.0)},
        {"vertex_index": 2, "position_offset": (0.0, 0.0, 0.0)},
        {"vertex_index": 0, "position_offset": (0.0, 0.0)},
    ],
)
def test_malformed_or_non_finite_offsets_fail_closed(offset):
    with pytest.raises(ValueError):
        build_sparse_morph_signatures(
            [_morph(PmxMorphType.VertexMorph, [offset])],
            1,
        )
