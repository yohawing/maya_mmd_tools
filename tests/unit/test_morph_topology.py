import pytest

from mmd_tools.core.morph_topology import (
    MorphTopologyError,
    compute_group_topology,
    inspect_group_topology,
    parse_group_topology,
    parse_raw_offsets_json,
    serialize_group_topology,
)


def _morph(index, morph_type="vertex", offsets=()):
    return {"index": index, "morph_type": morph_type, "offsets": offsets}


def test_compute_flattens_group_and_flip_offsets_from_raw_authority():
    morphs = (
        _morph(0, "group", ({"morph_index": 1, "morph_rate": 0.5},)),
        _morph(1, "flip", ({"morph_index": 2, "flip_rate": 0.25},)),
        _morph(2),
    )

    assert compute_group_topology(morphs) == {
        "1": ((0, 0.5),),
        "2": ((0, 0.125), (1, 0.25)),
    }


@pytest.mark.parametrize(
    "morphs,code",
    [
        ((_morph(0, "group", ({"morph_index": 8, "morph_rate": 1.0},)),), "malformed"),
        (
            (
                _morph(0, "group", ({"morph_index": 1, "morph_rate": 1.0},)),
                _morph(1, "group", ({"morph_index": 0, "morph_rate": 1.0},)),
            ),
            "cycle",
        ),
    ],
)
def test_compute_fails_closed_with_stable_diagnostic(morphs, code):
    with pytest.raises(MorphTopologyError) as caught:
        compute_group_topology(morphs)
    assert caught.value.diagnostic.code == code


@pytest.mark.parametrize(
    "morph_type,offset",
    [
        ("group", {"morph_index": 1, "flip_rate": 0.5}),
        ("flip", {"morph_index": 1, "morph_rate": 0.5}),
        ("group", {"morph_index": 1, "morph_rate": 0.5, "extra": 1}),
    ],
)
def test_compute_requires_type_exact_offset_schema(morph_type, offset):
    with pytest.raises(MorphTopologyError, match="offset fields"):
        compute_group_topology((_morph(0, morph_type, (offset,)), _morph(1)))


def test_parse_rejects_version_and_malformed_payload_instead_of_empty_topology():
    with pytest.raises(MorphTopologyError) as version:
        parse_group_topology(2, "{}")
    assert version.value.diagnostic.code == "version"

    with pytest.raises(MorphTopologyError) as malformed:
        parse_group_topology(1, '{"1":[[true,1.0]]}')
    assert malformed.value.diagnostic.code == "malformed"

    with pytest.raises(MorphTopologyError) as duplicate:
        parse_group_topology(1, '{"1":[],"1":[[0,1.0]]}')
    assert duplicate.value.diagnostic.code == "malformed"
    assert "duplicate target key" in str(duplicate.value)

    with pytest.raises(MorphTopologyError, match="duplicate raw offset field"):
        parse_raw_offsets_json(
            '[{"morph_index":0,"morph_index":1,"morph_rate":0.5}]'
        )


def test_inspection_reports_stale_and_returns_canonical_repair_payload():
    morphs = (
        _morph(0, "group", ({"morph_index": 1, "morph_rate": 0.5},)),
        _morph(1),
    )
    inspection = inspect_group_topology(morphs, 1, "{}")

    assert inspection.repairable
    assert tuple(item.code for item in inspection.diagnostics) == ("stale",)
    assert serialize_group_topology(inspection.expected) == '{"1":[[0,0.5]]}'
    with pytest.raises(TypeError):
        inspection.expected["2"] = ()


def test_inspection_never_marks_cycle_repairable():
    morphs = (
        _morph(0, "group", ({"morph_index": 0, "morph_rate": 1.0},)),
    )
    inspection = inspect_group_topology(morphs, 1, "{}")

    assert not inspection.valid
    assert not inspection.repairable
    assert inspection.diagnostics[0].code == "cycle"
