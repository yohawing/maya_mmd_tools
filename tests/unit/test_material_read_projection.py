"""Pure contracts for immutable Material authoring read projections."""

from dataclasses import FrozenInstanceError

import pytest

from mmd_tools.core.material_read_projection import (
    MaterialAssignmentKind,
    MaterialAssignmentSummary,
    MaterialDetailProjection,
    MaterialListItemProjection,
    MaterialListProjection,
    MaterialListSemantic,
    MaterialPreviewState,
    MaterialTextureBinding,
    MaterialTextureProvenance,
    MaterialTextureSlot,
)
from mmd_tools.core.model_authoring_spec import MmdMaterialSpec


def _material(index=2, binding="shader"):
    return MmdMaterialSpec("Material", index=index, binding_identity=binding)


def _item(index=2, binding="shader"):
    return MaterialListItemProjection(
        MaterialListSemantic(index, binding, "Material"),
        MaterialAssignmentSummary(
            MaterialAssignmentKind.EXPLICIT_FACES,
            mesh_count=1,
            face_count=24,
        ),
    )


def test_list_projection_is_frozen_hashable_and_routes_by_semantic_identity():
    first = _item(0, "shaderA")
    second = _item(2, "shaderB")
    projection = MaterialListProjection("|model", (first, second))

    assert projection.item_for_index(2) is second
    assert projection.item_for_binding("shaderA") is first
    assert first.assignment.label == "meshes=1, faces=24"
    assert len({projection, projection}) == 1
    with pytest.raises(FrozenInstanceError):
        projection.root_identity = "|other"


def test_list_projection_rejects_missing_binding_and_noncanonical_order():
    with pytest.raises(ValueError, match="canonical identity"):
        _item(0, None)
    with pytest.raises(ValueError, match="ascending PMX indices"):
        MaterialListProjection("|model", (_item(2, "shaderB"), _item(0, "shaderA")))
    with pytest.raises(ValueError, match="unique canonical bindings"):
        MaterialListProjection("|model", (_item(0, "shader"), _item(1, "shader")))


def test_assignment_summary_distinguishes_unknown_face_count():
    summary = MaterialAssignmentSummary(
        MaterialAssignmentKind.UNKNOWN,
        mesh_count=0,
        face_count=None,
    )

    assert summary.label == "meshes=0, faces=?"
    with pytest.raises(ValueError, match="mesh_count"):
        MaterialAssignmentSummary(MaterialAssignmentKind.EMPTY, mesh_count=-1, face_count=0)
    with pytest.raises(ValueError, match="face_count"):
        MaterialAssignmentSummary(MaterialAssignmentKind.EMPTY, mesh_count=0, face_count=True)


def test_assignment_summary_distinguishes_whole_object_faces_and_empty():
    whole = MaterialAssignmentSummary(
        MaterialAssignmentKind.WHOLE_OBJECT,
        mesh_count=2,
        face_count=None,
    )
    explicit = MaterialAssignmentSummary(
        MaterialAssignmentKind.EXPLICIT_FACES,
        mesh_count=1,
        face_count=12,
    )
    empty = MaterialAssignmentSummary(MaterialAssignmentKind.EMPTY, 0, 0)
    mixed = MaterialAssignmentSummary(MaterialAssignmentKind.MIXED, 2, 12)

    assert whole.label == "meshes=2, faces=all"
    assert explicit.label == "meshes=1, faces=12"
    assert empty.label == "meshes=0, faces=0"
    assert mixed.label == "meshes=2, faces=all+12"
    with pytest.raises(ValueError, match="whole-object"):
        MaterialAssignmentSummary(MaterialAssignmentKind.WHOLE_OBJECT, 1, 12)
    with pytest.raises(ValueError, match="explicit-face"):
        MaterialAssignmentSummary(MaterialAssignmentKind.EXPLICIT_FACES, 1, None)


def test_detail_keeps_semantics_provenance_bindings_and_preview_separate():
    main_binding = MaterialTextureBinding(
        MaterialTextureSlot.MAIN,
        "shader.MainTexture",
        "mainFile",
    )
    main = MaterialTextureProvenance(
        MaterialTextureSlot.MAIN,
        "textures/body.png",
        "C:/model/textures/body.png",
        main_binding,
    )
    detail = MaterialDetailProjection(
        root_identity="|model",
        material=MmdMaterialSpec(
            "Material",
            index=2,
            binding_identity="shader",
            texture_path="textures/body.png",
            resolved_texture_path="C:/model/textures/body.png",
        ),
        assignment=MaterialAssignmentSummary(
            MaterialAssignmentKind.EXPLICIT_FACES,
            2,
            48,
        ),
        textures=(main,),
        preview=MaterialPreviewState("dx11Shader", outline_enabled=True),
    )

    assert detail.texture(MaterialTextureSlot.MAIN) is main
    assert detail.material.texture_path == "textures/body.png"
    assert detail.preview.outline_enabled is True
    assert hash(detail)


def test_texture_projection_rejects_slot_mismatch_duplicates_and_empty_paths():
    wrong_binding = MaterialTextureBinding(
        MaterialTextureSlot.SPHERE,
        "shader.SphereTexture",
    )
    with pytest.raises(ValueError, match="slot must match"):
        MaterialTextureProvenance(
            MaterialTextureSlot.MAIN,
            "body.png",
            "C:/body.png",
            wrong_binding,
        )
    with pytest.raises(ValueError, match="non-empty path"):
        MaterialTextureProvenance(MaterialTextureSlot.MAIN, "", None)
    with pytest.raises(ValueError, match="node.attribute"):
        MaterialTextureBinding(MaterialTextureSlot.MAIN, "shader")

    duplicate = MaterialTextureProvenance(MaterialTextureSlot.MAIN, None, None)
    with pytest.raises(ValueError, match="slots must be unique"):
        MaterialDetailProjection(
            "|model",
            _material(),
            MaterialAssignmentSummary(MaterialAssignmentKind.EMPTY, 0, 0),
            (duplicate, duplicate),
            MaterialPreviewState("lambert", False),
        )


def test_exact_slot_lookup_does_not_fallback_to_another_texture():
    sphere = MaterialTextureProvenance(MaterialTextureSlot.SPHERE, "env.spa", None)
    detail = MaterialDetailProjection(
        "|model",
        MmdMaterialSpec(
            "Material",
            index=2,
            binding_identity="shader",
            sphere_texture_path="env.spa",
        ),
        MaterialAssignmentSummary(MaterialAssignmentKind.EMPTY, 0, 0),
        (sphere,),
        MaterialPreviewState("lambert", False),
    )

    with pytest.raises(KeyError, match="main"):
        detail.texture(MaterialTextureSlot.MAIN)
    with pytest.raises(TypeError, match="MaterialTextureSlot"):
        detail.texture("sphere")


def test_detail_allows_sparse_slots_but_rejects_noncanonical_slot_order():
    main = MaterialTextureProvenance(MaterialTextureSlot.MAIN, None, None)
    toon = MaterialTextureProvenance(MaterialTextureSlot.TOON, None, None)

    detail = MaterialDetailProjection(
        "|model",
        _material(),
        MaterialAssignmentSummary(MaterialAssignmentKind.EMPTY, 0, 0),
        (main, toon),
        MaterialPreviewState("lambert", False),
    )
    assert detail.textures == (main, toon)

    with pytest.raises(ValueError, match="canonical slot order"):
        MaterialDetailProjection(
            "|model",
            _material(),
            MaterialAssignmentSummary(MaterialAssignmentKind.EMPTY, 0, 0),
            (toon, main),
            MaterialPreviewState("lambert", False),
        )


def test_detail_rejects_stale_provenance_and_a_binding_for_another_shader():
    stale = MaterialTextureProvenance(MaterialTextureSlot.MAIN, "stale.png", None)
    with pytest.raises(ValueError, match="semantic material paths"):
        MaterialDetailProjection(
            "|model",
            _material(),
            MaterialAssignmentSummary(MaterialAssignmentKind.EMPTY, 0, 0),
            (stale,),
            MaterialPreviewState("lambert", False),
        )

    foreign = MaterialTextureProvenance(
        MaterialTextureSlot.MAIN,
        None,
        None,
        MaterialTextureBinding(MaterialTextureSlot.MAIN, "other.color"),
    )
    with pytest.raises(ValueError, match="material canonical binding"):
        MaterialDetailProjection(
            "|model",
            _material(),
            MaterialAssignmentSummary(MaterialAssignmentKind.EMPTY, 0, 0),
            (foreign,),
            MaterialPreviewState("lambert", False),
        )
