"""Unit tests for display_frame_resolver — pure logic, no Maya dependency."""

import json
import unittest

from mmd_tools.core.display_frame_resolver import (
    PickerGroup,
    PickerItem,
    resolve_bone_items,
    resolve_display_frames,
    resolve_morph_items,
)


def _make_json(frames):
    return json.dumps(frames, ensure_ascii=False)


SAMPLE_FRAMES = [
    {
        "name": "Root",
        "name_english": "Root",
        "special_flag": 1,
        "elements": [{"type": 0, "index": 0}],
    },
    {
        "name": "表情",
        "name_english": "Exp",
        "special_flag": 1,
        "elements": [
            {"type": 1, "index": 0},
            {"type": 1, "index": 1},
        ],
    },
    {
        "name": "体(上)",
        "name_english": "Upper Body",
        "special_flag": 0,
        "elements": [
            {"type": 0, "index": 3},
            {"type": 0, "index": 4},
            {"type": 0, "index": 5},
        ],
    },
]

BONE_MAP = {0: "center", 3: "upper_body", 4: "neck", 5: "head"}
MORPH_MAP = {0: "smile", 1: "wink"}


class TestResolveDisplayFrames(unittest.TestCase):
    def test_basic_resolution(self):
        groups = resolve_display_frames(_make_json(SAMPLE_FRAMES), BONE_MAP, MORPH_MAP)

        self.assertEqual(len(groups), 3)
        self.assertEqual(groups[0].name, "Root")
        self.assertEqual(groups[0].special_flag, 1)
        self.assertEqual(len(groups[0].items), 1)
        self.assertEqual(groups[0].items[0].resolved_name, "center")

    def test_morph_resolution(self):
        groups = resolve_display_frames(_make_json(SAMPLE_FRAMES), BONE_MAP, MORPH_MAP)

        exp_group = groups[1]
        self.assertEqual(exp_group.name, "表情")
        self.assertEqual(len(exp_group.items), 2)
        self.assertEqual(exp_group.items[0].resolved_name, "smile")
        self.assertEqual(exp_group.items[1].resolved_name, "wink")

    def test_japanese_display_names_are_independent_from_resolved_nodes(self):
        groups = resolve_display_frames(
            _make_json(SAMPLE_FRAMES),
            BONE_MAP,
            MORPH_MAP,
            bone_display_name_map={0: "センター"},
            morph_display_name_map={0: "笑い", 1: "ウィンク"},
        )

        self.assertEqual(groups[0].items[0].resolved_name, "center")
        self.assertEqual(groups[0].items[0].display_name, "センター")
        self.assertEqual(groups[1].items[0].resolved_name, "smile")
        self.assertEqual(groups[1].items[0].display_name, "笑い")

    def test_unresolved_index_returns_empty_string(self):
        groups = resolve_display_frames(
            _make_json([{"name": "G", "name_english": "G", "special_flag": 0, "elements": [{"type": 0, "index": 999}]}]),
            BONE_MAP,
        )

        self.assertEqual(groups[0].items[0].resolved_name, "")
        self.assertEqual(groups[0].items[0].index, 999)

    def test_fallback_flat_list_when_no_frames(self):
        groups = resolve_display_frames(None, BONE_MAP)

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].name, "All Bones")
        self.assertEqual(len(groups[0].items), len(BONE_MAP))
        indices = [item.index for item in groups[0].items]
        self.assertEqual(indices, sorted(BONE_MAP.keys()))

    def test_fallback_on_empty_json(self):
        groups = resolve_display_frames("", BONE_MAP)

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].name, "All Bones")

    def test_fallback_on_invalid_json(self):
        groups = resolve_display_frames("{bad", BONE_MAP)

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].name, "All Bones")

    def test_empty_bone_map_and_no_frames(self):
        groups = resolve_display_frames(None, {})

        self.assertEqual(groups, [])

    def test_empty_elements_produces_empty_items(self):
        groups = resolve_display_frames(
            _make_json([{"name": "Empty", "name_english": "Empty", "special_flag": 0, "elements": []}]),
            BONE_MAP,
        )

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].items, ())

    def test_invalid_element_type_skipped(self):
        groups = resolve_display_frames(
            _make_json([{"name": "X", "name_english": "X", "special_flag": 0, "elements": [{"type": 5, "index": 0}]}]),
            BONE_MAP,
        )

        self.assertEqual(groups[0].items, ())

    def test_non_dict_elements_skipped(self):
        groups = resolve_display_frames(
            _make_json([{"name": "X", "name_english": "X", "special_flag": 0, "elements": [42, "bad", None]}]),
            BONE_MAP,
        )

        self.assertEqual(groups[0].items, ())

    def test_frozen_dataclasses(self):
        groups = resolve_display_frames(_make_json(SAMPLE_FRAMES), BONE_MAP, MORPH_MAP)

        with self.assertRaises(AttributeError):
            groups[0].name = "changed"
        with self.assertRaises(AttributeError):
            groups[0].items[0].resolved_name = "changed"


class TestHelperFilters(unittest.TestCase):
    def test_resolve_bone_items(self):
        groups = resolve_display_frames(_make_json(SAMPLE_FRAMES), BONE_MAP, MORPH_MAP)
        bones = resolve_bone_items(groups)

        self.assertEqual(len(bones), 4)
        self.assertTrue(all(b.element_type == 0 for b in bones))

    def test_resolve_morph_items(self):
        groups = resolve_display_frames(_make_json(SAMPLE_FRAMES), BONE_MAP, MORPH_MAP)
        morphs = resolve_morph_items(groups)

        self.assertEqual(len(morphs), 2)
        self.assertTrue(all(m.element_type == 1 for m in morphs))

    def test_filters_on_empty_groups(self):
        self.assertEqual(resolve_bone_items([]), [])
        self.assertEqual(resolve_morph_items([]), [])


class TestPickerGroupEquality(unittest.TestCase):
    def test_identical_groups_are_equal(self):
        g1 = PickerGroup("A", "A", 0, (PickerItem(0, 1, "j1"),))
        g2 = PickerGroup("A", "A", 0, (PickerItem(0, 1, "j1"),))
        self.assertEqual(g1, g2)

    def test_different_items_are_not_equal(self):
        g1 = PickerGroup("A", "A", 0, (PickerItem(0, 1, "j1"),))
        g2 = PickerGroup("A", "A", 0, (PickerItem(0, 2, "j2"),))
        self.assertNotEqual(g1, g2)


if __name__ == "__main__":
    unittest.main()
