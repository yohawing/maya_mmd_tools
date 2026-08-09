"""Focused tests for the pure authoring-to-export payload bridge."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from mmd_tools.converters.authoring_export_bridge import (
    AuthoringExportIntegrationError,
    project_authoring_spec,
)
from mmd_tools.core.model_authoring_spec import (
    MmdBoneSpec,
    MmdMaterialSpec,
    MmdModelAuthoringSpec,
    MmdModelSpec,
    MmdMorphSpec,
)
from mmd_tools.core.pmx_data import PmxData
from mmd_tools.io.pmx_exporter import PmxExporter
from mmd_tools.validation.export_validator import validate_model_data


def _spec(*, morphs=(), binding_identity="|root", material=None):
    return MmdModelAuthoringSpec(
        model=MmdModelSpec("新しいモデル", "New Model", "コメント", "Comment"),
        bones=(
            MmdBoneSpec(
                name="Root JP",
                name_english="Root EN",
                index=0,
                flags=30,
                tail_offset=(0.0, 1.0, 0.0),
                binding_identity=binding_identity,
            ),
        ),
        materials=(material or MmdMaterialSpec(name="Mat JP", name_english="Mat EN", index=0),),
        morphs=tuple(morphs),
    )


def _oracle(*, morphs=None):
    return {
        "model_name": "old",
        "vertices": [
            {
                "position": [0.0, 0.0, 0.0],
                "weight_transform_type": 3,
                "sdef_c": [1.0, 2.0, 3.0],
                "additional_uvs": [[0.25, 0.5, 0.75, 1.0]],
            }
        ],
        "faces": [[0, 0, 0]],
        "bones": [{"source_joint": "|root", "name": "old", "oracle_only": {"keep": True}}],
        "materials": [
            {
                "source_material_index": 0,
                "face_count": 3,
                "texture_index": 4,
                "sphere_texture_index": 5,
                "toon_texture_index": 6,
                "oracle_only": "material",
            }
        ],
        "morphs": morphs or [],
        "display_frames": [{"name": "Root", "special_flag": 1, "elements": [{"type": 0, "index": 0}]}],
        "rigid_bodies": [{"name": "body"}],
        "joints": [{"name": "joint"}],
        "soft_bodies": [{"count": 1}],
        "textures": ["a.png", "b.png", "c.png", "d.png", "e.png", "f.png", "g.png"],
    }


def test_projection_overlays_semantics_and_retains_oracle_payload() -> None:
    morph = MmdMorphSpec(
        name="Smile",
        name_english="Smile",
        index=0,
        morph_type="flip",
        offsets=({"morph_index": 1, "flip_rate": 0.25},),
        runtime_capability="unsupported",
        loss_policy="reject",
    )
    spec = _spec(morphs=(morph,))
    oracle = _oracle(morphs=[{"index": 0, "type": "flip", "offsets": [{"morph_index": 1, "flip_rate": 0.25}], "raw": {"keep": True}}])
    original = copy.deepcopy(oracle)
    projected = project_authoring_spec(spec, oracle)

    assert projected["model_name"] == "新しいモデル"
    assert projected["model_name_english"] == "New Model"
    assert projected["bones"][0]["name"] == "Root JP"
    assert projected["bones"][0]["source_joint"] == "|root"
    assert projected["materials"][0]["name"] == "Mat JP"
    assert projected["materials"][0]["face_count"] == 3
    assert projected["materials"][0]["texture_index"] == -1
    assert projected["morphs"][0]["type"] == "flip"
    assert projected["morphs"][0]["raw"] == {"keep": True}
    assert projected["vertices"] == original["vertices"]
    assert projected["faces"] == original["faces"]
    assert projected["display_frames"] == original["display_frames"]
    assert projected["soft_bodies"] == original["soft_bodies"]
    assert oracle == original


def test_vertex_offsets_compare_as_unordered_values_with_tolerance() -> None:
    morph = MmdMorphSpec(
        name="Vertex",
        index=0,
        morph_type="vertex",
        offsets=(
            {"vertex_index": 2, "position_offset": (0.1, 0.2, 0.3)},
            {"vertex_index": 1, "position_offset": (1.0, 2.0, 3.0)},
        ),
    )
    oracle = _oracle(
        morphs=[
            {
                "index": 0,
                "type": "vertex",
                "offsets": [
                    {"vertex_index": 1, "position_offset": [1.0, 2.0, 3.0000005]},
                    {"vertex_index": 2, "position_offset": [0.1, 0.2, 0.3]},
                ],
            }
        ]
    )
    projected = project_authoring_spec(_spec(morphs=(morph,)), oracle)
    assert projected["morphs"][0]["offsets"][0]["vertex_index"] == 2


def test_collector_shape_morph_without_explicit_index_uses_position() -> None:
    morph = MmdMorphSpec(
        name="Flip", index=0, morph_type="flip", offsets=(), runtime_capability="unsupported", loss_policy="reject"
    )
    oracle = _oracle(morphs=[{"type": "flip", "offsets": [], "collector_only": True}])
    projected = project_authoring_spec(_spec(morphs=(morph,)), oracle)
    assert projected["morphs"][0]["collector_only"] is True


def test_vertex_offset_mismatch_fails_closed() -> None:
    morph = MmdMorphSpec(
        name="Vertex", index=0, morph_type="vertex", offsets=({"vertex_index": 0, "position_offset": (1.0, 0.0, 0.0)},)
    )
    oracle = _oracle(morphs=[{"index": 0, "type": "vertex", "offsets": [{"vertex_index": 0, "position_offset": [1.1, 0.0, 0.0]}]}])
    with pytest.raises(AuthoringExportIntegrationError, match="vertex offsets differ") as exc_info:
        project_authoring_spec(_spec(morphs=(morph,)), oracle)
    assert exc_info.value.report.is_blocking
    assert exc_info.value.report.issues[0].code == "AUTHORING_ORACLE_MISMATCH"
    assert exc_info.value.report.issues[0].path == "morphs[0].offsets"


@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload["materials"][0].pop("source_material_index"),
        lambda payload: payload["materials"][0].update({"source_material_index": 1}),
        lambda payload: payload["morphs"].append({"index": 1, "type": "flip", "offsets": []}),
    ],
)
def test_collection_provenance_mismatch_fails_closed(mutator) -> None:
    morph = MmdMorphSpec(
        name="Flip", index=0, morph_type="flip", offsets=(), runtime_capability="unsupported", loss_policy="reject"
    )
    oracle = _oracle(morphs=[{"index": 0, "type": "flip", "offsets": []}])
    mutator(oracle)
    with pytest.raises(AuthoringExportIntegrationError):
        project_authoring_spec(_spec(morphs=(morph,)), oracle)


def test_binding_identity_and_count_are_strict() -> None:
    with pytest.raises(AuthoringExportIntegrationError, match="source_joint"):
        project_authoring_spec(_spec(binding_identity="|other"), _oracle())
    oracle = _oracle()
    oracle["materials"].append({"source_material_index": 1, "face_count": 0})
    with pytest.raises(AuthoringExportIntegrationError, match="material count"):
        project_authoring_spec(_spec(), oracle)


def test_unassigned_extra_material_gets_zero_face_placeholder_and_exports(tmp_path: Path) -> None:
    base = _spec()
    extra = MmdMaterialSpec(name="未割当", name_english="Unassigned", index=1)
    spec = MmdModelAuthoringSpec(
        model=base.model,
        bones=base.bones,
        materials=(base.materials[0], extra),
        morphs=base.morphs,
    )
    projected = project_authoring_spec(spec, _oracle())

    placeholder = projected["materials"][1]
    assert placeholder["source_material_index"] == 1
    assert placeholder["face_count"] == 0
    assert placeholder["texture_index"] == -1
    assert placeholder["sphere_texture_index"] == -1

    model_data = {
        "model_name": projected["model_name"],
        "model_name_english": projected["model_name_english"],
        "vertices": [
            {
                "position": [0.0, 0.0, 0.0],
                "normal": [0.0, 0.0, 1.0],
                "uv": [0.0, 0.0],
                "bone_indices": [0],
            },
            {
                "position": [1.0, 0.0, 0.0],
                "normal": [0.0, 0.0, 1.0],
                "uv": [1.0, 0.0],
                "bone_indices": [0],
            },
            {
                "position": [0.0, 1.0, 0.0],
                "normal": [0.0, 0.0, 1.0],
                "uv": [0.0, 1.0],
                "bone_indices": [0],
            },
        ],
        "faces": [[0, 1, 2]],
        "materials": projected["materials"],
        "textures": projected["textures"],
        "bones": None,
    }
    validation = validate_model_data(model_data, "pmx")
    assert not validation.is_blocking, validation.issues

    output = tmp_path / "zero-face-extra-material.pmx"
    PmxExporter(native_parts_exporter=None).export_pmx_model(str(output), model_data)
    parsed = PmxData().parse_file(str(output))
    assert len(parsed.materials) == 2
    assert parsed.materials[1].face_count == 0


def test_texture_paths_rebuild_indices_append_and_clear_without_resolved_paths() -> None:
    material = MmdMaterialSpec(
        name="Mat JP",
        name_english="Mat EN",
        index=0,
        texture_path="new.png",
        resolved_texture_path="C:/absolute/should-not-be-exported.png",
        sphere_texture_path=None,
        toon_texture_path="toon.png",
    )
    projected = project_authoring_spec(_spec(material=material), _oracle())
    output_material = projected["materials"][0]
    assert projected["textures"] == [
        "a.png",
        "b.png",
        "c.png",
        "d.png",
        "e.png",
        "f.png",
        "g.png",
        "new.png",
        "toon.png",
    ]
    assert output_material["texture_index"] == 7
    assert output_material["sphere_texture_index"] == -1
    assert output_material["toon_texture_index"] == 8
    assert "absolute/should-not-be-exported" not in projected["textures"]


def test_projection_clears_stale_collector_semantic_missing_after_overlay() -> None:
    material = MmdMaterialSpec(
        name="完全な材質",
        index=0,
        texture_path="textures/主.png",
    )
    oracle = _oracle()
    oracle["materials"][0]["semantic_missing"] = ["name", "texture_table"]

    projected = project_authoring_spec(_spec(material=material), oracle)

    assert "semantic_missing" not in projected["materials"][0]
    assert projected["materials"][0]["texture_index"] == len(oracle["textures"])
    assert projected["textures"][-1] == "textures/主.png"


def test_shared_toon_keeps_validated_index_and_does_not_append_path() -> None:
    material = MmdMaterialSpec(
        name="Mat JP",
        name_english="Mat EN",
        index=0,
        shared_toon=True,
        toon_texture_index=3,
        toon_texture_path="ignored-for-shared-toon.png",
    )
    projected = project_authoring_spec(_spec(material=material), _oracle())
    output_material = projected["materials"][0]
    assert output_material["shared_toon_flag"] == 1
    assert output_material["toon_texture_index"] == 3
    assert "ignored-for-shared-toon.png" not in projected["textures"]


def test_shared_toon_index_is_fail_closed() -> None:
    material = MmdMaterialSpec(name="Mat", index=0, shared_toon=True)
    with pytest.raises(AuthoringExportIntegrationError, match="shared toon"):
        project_authoring_spec(_spec(material=material), _oracle())
