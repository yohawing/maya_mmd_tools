"""Maya-independent tests for the MMD name translation tool."""

import csv
from pathlib import Path
from types import SimpleNamespace

import pytest

from mmd_tools.core.name_translation import (
    NameEntry,
    NameTranslationError,
    build_translation_plan,
    collect_name_entries,
    format_preview,
    load_translation_dictionary,
)


def _entry(kind, node, source, english="", index=None, rename_allowed=True):
    return NameEntry(
        kind=kind,
        node=node,
        source_name=source,
        english_name=english,
        english_attr=f"mmd_{kind}_name_en",
        index=index,
        rename_allowed=rename_allowed,
    )


def test_load_dictionary_accepts_utf8_header_and_quoted_cells(tmp_path):
    path = tmp_path / "names.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["日本語", "英語"])
        writer.writerow(["左腕", "left arm"])
        writer.writerow(["髪,長", "hair, long"])

    assert load_translation_dictionary(str(path)) == {
        "左腕": "left arm",
        "髪,長": "hair, long",
    }


def test_dictionary_rejects_malformed_rows_empty_cells_and_duplicates(tmp_path):
    cases = {
        "short.csv": "左腕\n",
        "empty.csv": ",left_arm\n",
        "duplicate.csv": "左腕,left_arm\n左腕,left_arm_2\n",
    }
    for filename, contents in cases.items():
        path = tmp_path / filename
        path.write_text(contents, encoding="utf-8")
        with pytest.raises(NameTranslationError):
            load_translation_dictionary(str(path))


def test_shipped_standard_name_preset_is_loadable_and_broad():
    path = Path(__file__).parents[2] / "mmd_tools" / "config" / "name_translation_presets" / "mmd_standard_names.csv"

    assert path.is_file()
    translations = load_translation_dictionary(str(path))

    assert len(translations) >= 200
    assert translations["センター"] == "Center"
    assert translations["左腕"] == "Left Arm"
    assert translations["左足ＩＫ"] == "Left Leg IK"
    assert translations["左足IK"] == "Left Leg IK"
    assert translations["左親指０"] == "Left Thumb 0"
    assert translations["前髪"] == "Bangs"
    assert translations["顔"] == "Face"
    assert translations["スカート"] == "Skirt"
    assert translations["まばたき"] == "Blink"
    assert translations["眉上"] == "Brows Up"
    assert translations["口開き"] == "Mouth Open"


def test_shipped_standard_name_preset_has_strict_two_column_nonempty_rows():
    path = Path(__file__).parents[2] / "mmd_tools" / "config" / "name_translation_presets" / "mmd_standard_names.csv"

    with path.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream))

    assert rows[0] == ["Japanese", "English"]
    data_rows = rows[1:]
    assert all(len(row) == 2 and all(cell.strip() for cell in row) for row in data_rows)
    assert len({row[0] for row in data_rows}) == len(data_rows)


def test_shipped_standard_name_preset_is_listed_as_package_data():
    project = Path(__file__).parents[2] / "pyproject.toml"

    assert '"config/name_translation_presets/*.csv"' in project.read_text(encoding="utf-8")


def test_plan_updates_only_empty_english_names_without_sanitizing_english_name():
    plan = build_translation_plan(
        [
            _entry("bone", "|root|左腕", "左腕"),
            _entry("material", "|root|mat", "髪", english="already set"),
        ],
        {"左腕": "left arm", "髪": "hair"},
    )

    assert plan[0].english_name == "left arm"
    assert plan[1].english_name is None
    assert plan[0].maya_name is None


def test_numbered_suffix_inherits_base_translation_with_exact_override():
    plan = build_translation_plan(
        [
            _entry("material", "|root|base", "スカート", index=0),
            _entry("material", "|root|derived", "スカート_1", index=1),
            _entry("material", "|root|padded", "スカート_02", index=2),
            _entry("material", "|root|exact", "スカート_3", index=3),
            _entry("material", "|root|unrelated", "スカート親", index=4),
        ],
        {"スカート": "Skirt", "スカート_3": "Pleated Skirt"},
        rename_nodes=True,
    )

    assert [change.translated_name for change in plan] == [
        "Skirt",
        "Skirt_1",
        "Skirt_02",
        "Pleated Skirt",
        None,
    ]
    assert [change.english_name for change in plan] == [
        "Skirt",
        "Skirt_1",
        "Skirt_02",
        "Pleated Skirt",
        None,
    ]
    assert [change.maya_name for change in plan[:4]] == [
        "Skirt",
        "Skirt_1",
        "Skirt_02",
        "Pleated_Skirt",
    ]


def test_plan_overwrite_and_node_rename_are_independent():
    entries = [
        _entry("bone", "|root|jointA", "左腕", english="old", index=2),
        _entry("bone", "|root|jointB", "右腕", index=3),
    ]
    plan = build_translation_plan(
        entries,
        {"左腕": "left arm", "右腕": "left arm"},
        set_english=False,
        overwrite=True,
        rename_nodes=True,
        used_names={"left_arm_1"},
    )

    assert [change.english_name for change in plan] == [None, None]
    assert [change.maya_name for change in plan] == ["left_arm", "left_arm_2"]
    assert format_preview(plan) == (
        "bone[2]: |root|jointA; node='left_arm'",
        "bone[3]: |root|jointB; node='left_arm_2'",
    )


def test_untranslated_name_is_not_written_but_can_use_existing_safe_name_policy():
    plan = build_translation_plan(
        [_entry("morph", "|root|morphNode", "表情", index=7)],
        {},
        rename_nodes=True,
    )

    assert plan[0].translated_name is None
    assert plan[0].english_name is None
    assert plan[0].maya_name == "expression"


def test_model_root_never_enters_node_rename_path():
    plan = build_translation_plan(
        [_entry("model", "|Miku_root", "ミク", rename_allowed=False)],
        {"ミク": "Miku"},
        rename_nodes=True,
    )
    assert plan[0].english_name == "Miku"
    assert plan[0].maya_name is None


def test_collect_entries_includes_owned_physics_shapes_without_rename(monkeypatch):
    attrs = {
        "|root|rbShape": {"nameJp": "髪剛体", "nameEn": "", "pmxIndex": 4},
        "|root|jointShape": {"nameJp": "髪ジョイント", "nameEn": "Hair Joint", "pmxIndex": 2},
    }

    def list_relatives(_root, **kwargs):
        return {
            "joint": [],
            "mmdRigidBodyShape": ["|root|rbShape"],
            "mmdPhysicsJointShape": ["|root|jointShape"],
        }.get(kwargs.get("type"), [])

    cmds = SimpleNamespace(
        objExists=lambda node: node == "|root",
        ls=lambda node=None, **kwargs: [node] if node and kwargs.get("long") else [],
        listRelatives=list_relatives,
        attributeQuery=lambda attr, node, exists: exists and attr in attrs.get(node, {}),
        getAttr=lambda path: attrs[path.rsplit(".", 1)[0]][path.rsplit(".", 1)[1]],
    )
    from mmd_tools.core import model_registry

    monkeypatch.setattr(model_registry, "list_model_registry_members", lambda *_args: ())

    entries = collect_name_entries("|root", cmds_module=cmds)

    assert [(entry.kind, entry.index, entry.english_attr) for entry in entries] == [
        ("joint", 2, "nameEn"),
        ("rigid_body", 4, "nameEn"),
    ]
    assert all(not entry.rename_allowed for entry in entries)
