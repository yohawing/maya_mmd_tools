"""Unit tests for morph_metadata_reader — pure logic, no Maya dependency."""

import unittest

from mmd_tools.core.morph_metadata_reader import (
    CategorizedMorphs,
    MORPH_TAB_GROUP_ORDER,
    MorphInfo,
    PANEL_GROUP_LABELS,
    categorize_morphs,
    group_morph_names_by_panel,
    morph_info_from_presenter_entry,
    panel_display_group,
    read_morph_list_from_blendshape_json,
    read_morph_list_from_metadata,
)


def _morph(
    name: str,
    *,
    name_english: str = "",
    panel: int = 4,
    morph_type: str = "vertex",
    index: int = 0,
) -> MorphInfo:
    return MorphInfo(
        name=name,
        name_english=name_english,
        panel=panel,
        morph_type=morph_type,
        index=index,
    )


class TestCategorizeMorphs(unittest.TestCase):
    def test_categorize_basic(self):
        morphs = [
            _morph("まばたき", panel=2, index=0),
            _morph("にこり", panel=3, index=1),
            _morph("困り眉", panel=1, index=2),
            _morph("はぁ", panel=4, index=3),
            _morph("あ", panel=3, index=4),
            _morph("じと目", panel=2, index=5),
        ]
        result = categorize_morphs(morphs)

        self.assertEqual([m.name for m in result.eyebrow], ["困り眉"])
        self.assertEqual([m.name for m in result.eye], ["まばたき", "じと目"])
        self.assertEqual([m.name for m in result.mouth], ["にこり", "あ"])
        self.assertEqual([m.name for m in result.other], ["はぁ"])

    def test_categorize_excludes_system_panel(self):
        morphs = [
            _morph("system", panel=0, index=0),
            _morph("smile", panel=3, index=1),
        ]
        result = categorize_morphs(morphs)

        self.assertEqual([m.name for m in result.mouth], ["smile"])
        self.assertEqual(result.eyebrow, ())
        self.assertEqual(result.eye, ())
        self.assertEqual(result.other, ())

    def test_categorize_empty(self):
        result = categorize_morphs([])

        self.assertEqual(result, CategorizedMorphs((), (), (), ()))

    def test_categorize_preserves_order(self):
        morphs = [
            _morph("eye_a", panel=2, index=0),
            _morph("eye_b", panel=2, index=1),
            _morph("mouth_a", panel=3, index=2),
            _morph("mouth_b", panel=3, index=3),
            _morph("unknown", panel=99, index=4),
            _morph("other_b", panel=5, index=5),
        ]
        result = categorize_morphs(morphs)

        self.assertEqual([m.name for m in result.eye], ["eye_a", "eye_b"])
        self.assertEqual([m.name for m in result.mouth], ["mouth_a", "mouth_b"])
        self.assertEqual([m.name for m in result.other], ["unknown", "other_b"])


class TestReadMorphListFromMetadata(unittest.TestCase):
    def test_read_from_metadata_sorts_by_index(self):
        entries = [
            {"name": "c", "name_english": "C", "panel": 3, "morph_type": "vertex", "index": 2},
            {"name": "a", "name_english": "A", "panel": 1, "morph_type": "vertex", "index": 0},
            {"name": "b", "name_english": "B", "panel": 2, "morph_type": "bone", "index": 1},
        ]
        result = read_morph_list_from_metadata(entries)

        self.assertEqual([m.name for m in result], ["a", "b", "c"])
        self.assertEqual([m.index for m in result], [0, 1, 2])

    def test_read_from_metadata_handles_missing_fields(self):
        result = read_morph_list_from_metadata([{}, {"name": "only_name"}])
        morph = result[0]

        self.assertEqual(morph.name, "")
        self.assertEqual(morph.name_english, "")
        self.assertEqual(morph.panel, 0)
        self.assertEqual(morph.morph_type, "vertex")
        self.assertEqual(morph.index, -1)
        self.assertEqual(result[1].name, "only_name")


class TestReadMorphListFromBlendshapeJson(unittest.TestCase):
    def test_read_from_blendshape_json_basic(self):
        names_json = {"2": "にこり", "0": "まばたき", "1": "ウィンク"}
        result = read_morph_list_from_blendshape_json(names_json, panel=3)

        self.assertEqual([m.name for m in result], ["まばたき", "ウィンク", "にこり"])
        self.assertEqual([m.index for m in result], [0, 1, 2])
        self.assertTrue(all(m.morph_type == "vertex" for m in result))
        self.assertTrue(all(m.panel == 3 for m in result))
        self.assertTrue(all(m.name_english == "" for m in result))

    def test_read_from_blendshape_json_empty(self):
        self.assertEqual(read_morph_list_from_blendshape_json({}), [])


class TestFrozenDataclasses(unittest.TestCase):
    def test_frozen_dataclasses(self):
        morph = _morph("smile", panel=3)
        categorized = categorize_morphs([morph])

        with self.assertRaises(AttributeError):
            morph.name = "changed"
        with self.assertRaises(AttributeError):
            categorized.mouth = ()


class TestPanelDisplayGroup(unittest.TestCase):
    def test_panels_0_to_4_map_to_morph_tab_labels(self):
        self.assertIsNone(panel_display_group(0))
        self.assertEqual(panel_display_group(1), "眉")
        self.assertEqual(panel_display_group(2), "目")
        self.assertEqual(panel_display_group(3), "口")
        self.assertEqual(panel_display_group(4), "その他")
        self.assertEqual(panel_display_group(99), "その他")

    def test_group_labels_cover_only_user_panels(self):
        self.assertEqual(tuple(PANEL_GROUP_LABELS.keys()), (1, 2, 3, 4))
        self.assertEqual(MORPH_TAB_GROUP_ORDER, ("眉", "目", "口", "その他"))


class TestMorphInfoFromPresenterEntry(unittest.TestCase):
    def test_reads_panel_and_network_type_across_morph_kinds(self):
        cases = [
            ("vertex_a", {"panel": 1, "type": 0}, "vertex", 1),
            ("bone_b", {"panel": 2, "type": 10, "mmd_morph_type": "bone"}, "bone", 2),
            ("mat_c", {"panel": 3, "type": 11, "mmd_morph_type": "material"}, "material", 3),
            ("group_d", {"panel": 4, "type": 12, "mmd_morph_type": "group"}, "group", 4),
            ("system_e", {"panel": 0, "type": 0}, "vertex", 0),
        ]
        for name, data, morph_type, panel in cases:
            info = morph_info_from_presenter_entry(name, data)
            self.assertEqual(info.name, name)
            self.assertEqual(info.morph_type, morph_type)
            self.assertEqual(info.panel, panel)

    def test_missing_panel_defaults_to_other_not_system(self):
        info = morph_info_from_presenter_entry("fallback", {"name_en": "fb"})
        self.assertEqual(info.panel, 4)
        self.assertEqual(info.name_english, "fb")
        self.assertEqual(info.morph_type, "vertex")
        self.assertEqual(info.index, -1)


class TestGroupMorphNamesByPanel(unittest.TestCase):
    def test_excludes_system_and_uses_japanese_labels(self):
        morphs = [
            _morph("sys", panel=0, morph_type="vertex", index=0),
            _morph("brow", panel=1, morph_type="vertex", index=1),
            _morph("eye", panel=2, morph_type="bone", index=2),
            _morph("mouth", panel=3, morph_type="material", index=3),
            _morph("other", panel=4, morph_type="group", index=4),
        ]
        grouped = group_morph_names_by_panel(morphs)

        self.assertEqual(grouped["眉"], ["brow"])
        self.assertEqual(grouped["目"], ["eye"])
        self.assertEqual(grouped["口"], ["mouth"])
        self.assertEqual(grouped["その他"], ["other"])
        self.assertNotIn("sys", grouped["眉"] + grouped["目"] + grouped["口"] + grouped["その他"])


if __name__ == "__main__":
    unittest.main()