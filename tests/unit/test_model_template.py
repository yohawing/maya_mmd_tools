"""Tests for the packaged, immutable PMX model template contract."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from mmd_tools.core.model_template import (
    TEMPLATE_SCHEMA_VERSION,
    ModelTemplateError,
    instantiate_model_template,
    list_model_templates,
    load_model_template,
    parse_model_template_mapping,
)


def test_default_template_has_exact_product_content() -> None:
    template = load_model_template("pmx20-basic-v1")
    assert template.template_id == "pmx20-basic-v1"
    assert template.revision == "1"
    assert template.provenance["source"] == "maya_mmd_tools project-authored product template"
    assert template.license["identifier"] == "MIT"
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


def test_semistandard_template_is_project_authored_with_common_mmd_roles() -> None:
    template = load_model_template("pmx20-semistandard-v1")
    assert template.label == "準標準ボーン"
    assert template.revision == "2"
    assert template.provenance["source"] == "maya_mmd_tools project-authored normalized MMD skeleton"
    assert template.license["identifier"] == "MIT"
    assert template.license["evidence"] == "Repository LICENSE"

    bones_by_name = {bone.name: bone for bone in template.spec.bones}
    required_roles = set(
        "全ての親 センター グルーブ 腰 上半身 下半身 首 頭 両目 左腕捩 右腕捩 左ひじ 右ひじ "
        "左手首 右手首 左人指1 右人指1 左足 右足 左ひざ 右ひざ 左足首 右足首 左つま先 右つま先 "
        "左足ＩＫ 右足ＩＫ 左つま先ＩＫ 右つま先ＩＫ".split()
    )
    assert required_roles <= set(bones_by_name)
    assert bones_by_name["センター"].parent_index == bones_by_name["全ての親"].index
    assert bones_by_name["上半身"].parent_index == bones_by_name["腰"].index
    assert bones_by_name["左ひざ"].parent_index == bones_by_name["左足"].index
    assert bones_by_name["左足ＩＫ"].ik_target_index == bones_by_name["左足首"].index
    assert bones_by_name["左つま先ＩＫ"].ik_target_index == bones_by_name["左つま先"].index

    bone_indices = {bone.index for bone in template.spec.bones}
    assert bone_indices == set(range(len(template.spec.bones)))
    for bone in template.spec.bones:
        references = (bone.parent_index, bone.connect_bone_index, bone.grant_parent_index, bone.ik_target_index)
        assert all(index is None or index == -1 or index in bone_indices for index in references)
        assert all(link["bone"] in bone_indices for link in bone.ik_links)
    assert len(template.spec.materials) == 1
    assert len(template.display_frames) == 8


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
    assert instance.revision == original.revision
    assert instance.provenance == original.provenance
    assert instance.license == original.license


def test_metadata_roundtrip_is_exact_and_immutable() -> None:
    original = load_model_template("pmx20-semistandard-v1")
    payload = original.to_mapping()
    reparsed = parse_model_template_mapping(payload)
    assert reparsed.to_mapping() == payload
    with pytest.raises(TypeError):
        reparsed.provenance["source"] = "changed"  # type: ignore[index]
    with pytest.raises(TypeError):
        reparsed.license["identifier"] = "MIT"  # type: ignore[index]


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


def test_listed_templates_are_loadable_with_complete_metadata() -> None:
    options = list_model_templates()
    assert {option.template_id for option in options} == {"pmx20-basic-v1", "pmx20-semistandard-v1"}
    for option in options:
        template = load_model_template(option.template_id)
        assert option.label == template.label
        assert template.revision.strip()
        assert all(value.strip() for value in template.provenance.values())
        assert all(value.strip() for value in template.license.values())


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value.update({"unknown": True}),
        lambda value: value.update({"template_schema_version": TEMPLATE_SCHEMA_VERSION - 1}),
        lambda value: value.update({"label": "  "}),
        lambda value: value.pop("revision"),
        lambda value: value.update({"revision": "  "}),
        lambda value: value["provenance"].update({"unknown": "field"}),
        lambda value: value["provenance"].update({"source": ""}),
        lambda value: value["license"].update({"evidence": None}),
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
