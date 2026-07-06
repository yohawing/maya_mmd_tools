"""Unit tests for morph_metadata_reader — pure logic, no Maya dependency."""

import unittest

from mmd_tools.core.morph_metadata_reader import (
    CategorizedMorphs,
    MorphInfo,
    categorize_morphs,
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


if __name__ == "__main__":
    unittest.main()