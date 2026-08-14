"""Pure contracts for immutable morph read projections."""

from dataclasses import FrozenInstanceError

import pytest

from mmd_tools.core.morph_binding_resolver import MorphBinding, MorphBindingWarning
from mmd_tools.core.morph_read_projection import (
    MorphBindingProjection,
    MorphBlendShapeReadProjection,
)


def _binding(index=3):
    return MorphBinding(
        raw_pmx_name="Smile",
        global_morph_index=index,
        blend_shape_identity="faceBS",
        alias="Smile",
        logical_target_index=index,
        weight_plug="faceBS.weight[{}]".format(index),
        controller_identity="controller",
        controller_slot=index,
    )


def test_projection_is_frozen_and_exposes_only_canonical_preview_plugs():
    morph = MorphBindingProjection(
        raw_pmx_name="Smile",
        global_morph_index=3,
        binding_identity="morphNode",
        bindings=(_binding(),),
        warnings=(MorphBindingWarning("legacy", "legacy scene"),),
    )
    projection = MorphBlendShapeReadProjection(
        root_identity="|root",
        controller_identity="controller",
        owned_mesh_identities=("|root|meshShape",),
        owned_blend_shape_identities=("faceBS",),
        morphs=(morph,),
    )

    assert morph.preview_plugs == ("faceBS.weight[3]",)
    assert projection.binding_for_index(3) is morph
    with pytest.raises(FrozenInstanceError):
        morph.binding_identity = "changed"


def test_binding_lookup_fails_when_index_is_missing_or_ambiguous():
    first = MorphBindingProjection("A", 1, "morphA", (_binding(1),), ())
    duplicate = MorphBindingProjection("B", 1, "morphB", (_binding(1),), ())
    projection = MorphBlendShapeReadProjection(
        "|root",
        "controller",
        (),
        (),
        (first, duplicate),
    )

    with pytest.raises(KeyError, match="not unique"):
        projection.binding_for_index(1)
    with pytest.raises(KeyError, match="not unique"):
        projection.binding_for_index(99)
