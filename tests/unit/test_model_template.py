"""Tests for the packaged, immutable PMX model template contract."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from mmd_tools.core.model_template import (
    ModelTemplateError,
    instantiate_model_template,
    load_model_template,
    parse_model_template_mapping,
)


def test_default_template_has_exact_product_content() -> None:
    template = load_model_template("pmx20-basic-v1")
    assert template.template_id == "pmx20-basic-v1"
    assert template.spec.model.name == "新規MMDモデル"
    assert template.spec.model.name_english == "New MMD Model"
    assert len(template.spec.bones) == 1
    assert template.spec.bones[0].index == 0
    assert template.spec.bones[0].parent_index == -1
    assert template.spec.bones[0].flags == 30
    assert template.spec.bones[0].tail_offset == (0.0, 1.0, 0.0)
    assert len(template.spec.materials) == 1
    assert template.spec.morphs == ()
    assert template.display_frames[0]["elements"] == ({"type": "bone", "index": 0},)
    assert template.display_frames[1]["elements"] == ()


def test_semistandard_template_comes_from_the_authoritative_fixture_shape() -> None:
    template = load_model_template("pmx20-semistandard-v1")
    assert template.label == "準標準ボーン"
    assert len(template.spec.bones) == 100
    assert template.spec.bones[0].name == "センター"
    assert template.spec.bones[18].grant_parent_index is None
    assert template.spec.bones[20].grant_parent_index == 18
    assert template.spec.bones[53].ik_target_index == 52
    assert template.spec.bones[53].ik_loop_count == 40
    assert template.spec.bones[53].ik_links[0]["bone"] == 51
    assert len(template.spec.materials) == 1
    assert len(template.display_frames) == 9


def test_instantiation_overrides_only_model_names_and_is_fresh() -> None:
    original = load_model_template("pmx20-basic-v1")
    instance = instantiate_model_template("pmx20-basic-v1", "モデル名", "Model Name")
    assert instance is not original
    assert instance.spec is not original.spec
    assert instance.spec.model.name == "モデル名"
    assert instance.spec.model.name_english == "Model Name"
    assert instance.spec.bones == original.spec.bones
    assert instance.spec.materials == original.spec.materials
    assert instance.display_frames == original.display_frames


def test_parser_isolates_input_payload_and_rejects_mutation() -> None:
    payload = load_model_template("pmx20-basic-v1").to_mapping()
    copied = copy.deepcopy(payload)
    template = parse_model_template_mapping(payload)
    payload["display_frames"][0]["elements"][0]["index"] = 999
    assert template.display_frames[0]["elements"][0]["index"] == 0
    assert copied["display_frames"][0]["elements"][0]["index"] == 0
    with pytest.raises((TypeError, AttributeError)):
        template.display_frames[0]["name"] = "changed"  # type: ignore[index]


def test_package_resource_exists_and_is_declared() -> None:
    resource_path = Path(__file__).parents[2] / "mmd_tools" / "config" / "model_templates" / "pmx20_basic_v1.json"
    assert resource_path.is_file()
    json.loads(resource_path.read_text(encoding="utf-8"))
    semi_resource_path = resource_path.with_name("pmx20_semistandard_v1.json")
    assert semi_resource_path.is_file()
    json.loads(semi_resource_path.read_text(encoding="utf-8"))
    assert '"config/model_templates/*.json"' in (Path(__file__).parents[2] / "pyproject.toml").read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value.update({"unknown": True}),
        lambda value: value.update({"template_schema_version": 2}),
        lambda value: value["display_frames"][0]["elements"][0].update({"index": 9}),
        lambda value: value["display_frames"][0]["elements"][0].update({"type": "unknown"}),
        lambda value: value["display_frames"][1].update({"special": 1}),
        lambda value: value["display_frames"][0].update({"extra": True}),
    ],
)
def test_parser_fails_closed_for_invalid_payload(mutator) -> None:
    payload = load_model_template("pmx20-basic-v1").to_mapping()
    mutator(payload)
    with pytest.raises(ModelTemplateError):
        parse_model_template_mapping(payload)


def test_unknown_template_id_fails_closed() -> None:
    with pytest.raises(ModelTemplateError):
        load_model_template("not-a-template")


def test_fingerprint_is_deterministic() -> None:
    first = load_model_template("pmx20-basic-v1").spec.fingerprint()
    second = load_model_template("pmx20-basic-v1").spec.fingerprint()
    assert first == second
