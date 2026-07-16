"""Focused nested-group routing tests for the bone morph runtime."""

import unittest
from unittest import mock

from mmd_tools.converters import bone_morph_runtime


class TestBoneMorphRuntimeGuard(unittest.TestCase):
    def test_nested_group_bone_routes_use_cumulative_coefficients(self):
        contributions = {}
        skipped = []
        indices = {"groupOuter": 10, "groupInner": 9, "boneSmile": 4}
        group_offsets = {
            "groupOuter": [{"morph_index": 9, "morph_rate": 0.5}],
            "groupInner": [{"morph_index": 4, "morph_rate": 0.25}],
        }
        bone_offsets = [{
            "bone_index": 3,
            "translation": [0.0, 0.0, 0.0],
            "rotation": [0.0, 0.0, 0.0, 1.0],
        }]

        with mock.patch.object(
            bone_morph_runtime,
            "_get_explicit_morph_index",
            side_effect=lambda node: indices[node],
        ), mock.patch.object(
            bone_morph_runtime,
            "_parse_group_offsets_json",
            side_effect=lambda node: group_offsets[node],
        ), mock.patch.object(
            bone_morph_runtime,
            "_parse_offsets_json",
            return_value=bone_offsets,
        ), mock.patch.object(
            bone_morph_runtime,
            "_get_morph_order",
            side_effect=lambda node: indices[node],
        ):
            bone_morph_runtime._append_group_morph_contributions(
                contributions,
                ["groupOuter", "groupInner"],
                ["boneSmile"],
                {3: "joint3"},
                skipped,
            )

        routes = contributions["joint3"]
        self.assertEqual(
            [(route["group_morph_node"], route["group_morph_rate"]) for route in routes],
            [("groupInner", 0.25), ("groupOuter", 0.125)],
        )
        self.assertEqual(skipped, [])
