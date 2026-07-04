"""Unit tests for collecting HumanIK candidates from Maya-like scenes."""

import unittest

from mmd_tools.config.humanik_mapping import HIK_BONE_INDICES
from mmd_tools.core.constants import ATTR_MMD_BONE_INDEX, ATTR_MMD_BONE_NAME, ATTR_MMD_BONE_NAME_EN
from mmd_tools.core.humanik_builder import collect_humanik_joint_candidates, resolve_scene_humanik_assignments


class FakeCmds:
    """Small Maya cmds fake for HumanIK builder tests."""

    def __init__(self):
        self.types = {
            "|model": "transform",
            "|model|lower": "joint",
            "|model|spine": "joint",
            "|other|arm": "joint",
        }
        self.children = {
            "|model": ["|model|spine", "|model|lower"],
            "|other": ["|other|arm"],
        }
        self.attrs = {
            ("|model|lower", ATTR_MMD_BONE_NAME): "下半身",
            ("|model|lower", ATTR_MMD_BONE_INDEX): 1,
            ("|model|spine", ATTR_MMD_BONE_NAME_EN): "upper_body",
            ("|model|spine", ATTR_MMD_BONE_INDEX): 2,
            ("|other|arm", ATTR_MMD_BONE_NAME): "左腕",
            ("|other|arm", ATTR_MMD_BONE_INDEX): 3,
        }

    def objExists(self, node):
        return node in self.types

    def nodeType(self, node):
        return self.types[node]

    def listRelatives(self, node, allDescendents=False, fullPath=False, type=None):
        values = list(self.children.get(node, []))
        if type:
            values = [value for value in values if self.types.get(value) == type]
        return values

    def ls(self, *args, **kwargs):
        if args:
            node = args[0]
            return [node] if node in self.types else []
        node_type = kwargs.get("type")
        if node_type:
            return [node for node, value in self.types.items() if value == node_type]
        return list(self.types)

    def attributeQuery(self, attr, node, exists=False):
        return exists and (node, attr) in self.attrs

    def getAttr(self, plug):
        node, attr = plug.rsplit(".", 1)
        return self.attrs[(node, attr)]


class TestHumanIkBuilder(unittest.TestCase):
    """HumanIK builder scene collection tests."""

    def test_collect_humanik_joint_candidates_from_model_root(self):
        candidates = collect_humanik_joint_candidates("|model", FakeCmds())

        self.assertEqual([candidate.node for candidate in candidates], ["|model|lower", "|model|spine"])
        self.assertEqual(candidates[0].mmd_name, "下半身")
        self.assertEqual(candidates[1].english_name, "upper_body")

    def test_collect_humanik_joint_candidates_can_scan_all_joints(self):
        candidates = collect_humanik_joint_candidates(cmds_module=FakeCmds())

        self.assertEqual([candidate.node for candidate in candidates], ["|model|lower", "|model|spine", "|other|arm"])

    def test_resolve_scene_humanik_assignments_uses_collected_metadata(self):
        result = resolve_scene_humanik_assignments("|model", FakeCmds())

        assignments = result.assignments_by_hik_index
        self.assertEqual(assignments[HIK_BONE_INDICES["Hips"]].joint, "|model|lower")
        self.assertEqual(assignments[HIK_BONE_INDICES["Spine"]].source, "english_name")

    def test_collect_humanik_joint_candidates_rejects_missing_root(self):
        with self.assertRaisesRegex(ValueError, "Model root does not exist"):
            collect_humanik_joint_candidates("|missing", FakeCmds())


if __name__ == "__main__":
    unittest.main()
