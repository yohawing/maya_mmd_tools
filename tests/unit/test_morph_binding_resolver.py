from dataclasses import FrozenInstanceError

import pytest

from mmd_tools.core.morph_binding_resolver import (
    MorphBinding,
    MorphBindingRequest,
    MorphBindingResolutionError,
    MorphDestinationObservation,
    resolve_morph_binding,
)


def _request(**overrides):
    values = {
        "raw_pmx_name": "笑い",
        "global_morph_index": 7,
        "controller_identity": "|model|mmdMorphController",
        "controller_slot": 12,
    }
    values.update(overrides)
    return MorphBindingRequest(**values)


def _destination(node="|model|faceBS", plug="|model|faceBS.w[2]", node_type="blendShape"):
    return MorphDestinationObservation(node_identity=node, node_type=node_type, plug=plug)


def _resolve(*, request=None, destinations=None, aliases=None, raw=None):
    return resolve_morph_binding(
        request or _request(),
        destinations if destinations is not None else [_destination()],
        aliases
        if aliases is not None
        else {"|model|faceBS": [("SmileRenamed", "weight[2]")]},
        raw if raw is not None else {
            "|model|faceBS": {"2": {"name": "笑い", "index": 7}}
        },
    )


def test_resolves_authoritative_name_with_independent_controller_slot():
    resolution = _resolve()

    assert resolution.bindings == (
        MorphBinding(
            raw_pmx_name="笑い",
            global_morph_index=7,
            blend_shape_identity="|model|faceBS",
            alias="SmileRenamed",
            logical_target_index=2,
            weight_plug="|model|faceBS.weight[2]",
            controller_identity="|model|mmdMorphController",
            controller_slot=12,
        ),
    )
    assert resolution.warnings == ()
    with pytest.raises(FrozenInstanceError):
        resolution.bindings[0].alias = "changed"


def test_resolves_destination_alias_and_survives_alias_rename():
    before = _resolve(
        destinations=[_destination(plug="|model|faceBS.original")],
        aliases={"|model|faceBS": [("original", "w[2]")]},
    ).bindings[0]
    after = _resolve(
        destinations=[_destination(plug="|model|faceBS.renamed")],
        aliases={"|model|faceBS": [("renamed", "weight[2]")]},
    ).bindings[0]

    assert before.logical_target_index == after.logical_target_index == 2
    assert before.raw_pmx_name == after.raw_pmx_name == "笑い"
    assert (before.alias, after.alias) == ("original", "renamed")


def test_allows_multi_mesh_binding_for_one_request():
    resolution = _resolve(
        destinations=[
            _destination(),
            _destination("|model|bodyBS", "|model|bodyBS.BodySmile"),
        ],
        aliases={
            "|model|faceBS": [("SmileRenamed", "weight[2]")],
            "|model|bodyBS": [("BodySmile", "w[4]")],
        },
        raw={
            "|model|faceBS": {"2": {"name": "笑い", "index": 7}},
            "|model|bodyBS": {"4": {"name": "笑い", "index": 7}},
        },
    )

    assert [item.blend_shape_identity for item in resolution.bindings] == [
        "|model|bodyBS",
        "|model|faceBS",
    ]


def test_ignores_unrelated_raw_entries_in_targeted_resolution():
    resolution = _resolve(
        raw={
            "|model|faceBS": {
                "2": {"name": "笑い", "index": 7},
                "3": {"name": "まばたき", "index": 8},
            }
        }
    )

    assert len(resolution.bindings) == 1


def test_legacy_unique_sanitized_alias_returns_stable_warning():
    resolution = _resolve(
        request=_request(raw_pmx_name="Smile!"),
        destinations=[_destination(plug="|model|faceBS.Smile_")],
        aliases={"|model|faceBS": [("Smile_", "weight[2]")]},
        raw={},
    )

    assert resolution.bindings[0].raw_pmx_name == "Smile!"
    assert [warning.code for warning in resolution.warnings] == [
        "legacy_sanitized_alias_fallback"
    ]


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"destinations": [_destination(node_type="network")]}, "wrong_node_type"),
        (
            {"destinations": [_destination(), _destination(plug="|model|faceBS.weight[3]")]},
            "duplicate_blendshape_candidate",
        ),
        (
            {"aliases": {"|model|faceBS": [("same", "weight[2]"), ("same", "w[3]")]}},
            "alias_ambiguity",
        ),
        ({"destinations": [_destination(plug="|model|faceBS.weight[bad]")]}, "malformed_destination"),
        (
            {"raw": {"|model|faceBS": {"2": {"name": "笑い"}}}},
            "malformed_raw_entry",
        ),
        (
            {"raw": {"|model|faceBS": {"2": {"name": "笑い", "index": 8}}}},
            "stale_raw_name_mapping",
        ),
        (
            {"raw": {"|model|faceBS": {"2": {"name": "まばたき", "index": 7}}}},
            "stale_raw_name_mapping",
        ),
        (
            {"destinations": [_destination(plug="|model|faceBS.missingAlias")]},
            "alias_destination_ambiguity",
        ),
        ({"destinations": []}, "no_binding_candidate"),
        ({"aliases": {}}, "missing_alias_observation"),
    ],
)
def test_fails_closed_with_stable_diagnostic_codes(kwargs, code):
    with pytest.raises(MorphBindingResolutionError) as caught:
        _resolve(**kwargs)

    assert caught.value.code == code


def test_authoritative_raw_mapping_wins_over_sanitize_collision():
    resolution = _resolve(
        request=_request(raw_pmx_name="A!"),
        aliases={"|model|faceBS": [("A_", "weight[2]"), ("A__1", "weight[3]")]},
        raw={
            "|model|faceBS": {
                "2": {"name": "A!", "index": 7},
                "3": {"name": "A@", "index": 8},
            }
        },
    )

    assert resolution.bindings[0].raw_pmx_name == "A!"


def test_legacy_fallback_rejects_non_unique_or_suffixed_sanitize_alias():
    with pytest.raises(MorphBindingResolutionError) as caught:
        _resolve(
            request=_request(raw_pmx_name="Smile!"),
            destinations=[_destination(plug="|model|faceBS.Smile__1")],
            aliases={
                "|model|faceBS": [
                    ("Smile_", "weight[1]"),
                    ("Smile__1", "weight[2]"),
                ]
            },
            raw={},
        )

    assert caught.value.code == "legacy_alias_ambiguity"


def test_legacy_fallback_rejects_sanitize_collision_with_authoritative_entry():
    with pytest.raises(MorphBindingResolutionError) as caught:
        _resolve(
            request=_request(raw_pmx_name="A!"),
            destinations=[_destination(plug="|model|faceBS.A_")],
            aliases={"|model|faceBS": [("A_", "weight[2]"), ("A__1", "weight[3]")]},
            raw={"|model|faceBS": {"3": {"name": "A@", "index": 8}}},
        )

    assert caught.value.code == "stale_raw_name_mapping"


def test_existing_node_mapping_without_target_never_uses_legacy_fallback():
    with pytest.raises(MorphBindingResolutionError) as caught:
        _resolve(
            request=_request(raw_pmx_name="Smile!"),
            destinations=[_destination(plug="|model|faceBS.Smile_")],
            aliases={"|model|faceBS": [("Smile_", "weight[2]"), ("Blink", "weight[3]")]},
            raw={"|model|faceBS": {"3": {"name": "Blink", "index": 8}}},
        )

    assert caught.value.code == "stale_raw_name_mapping"
