"""PMX bone morph runtime accumulator graph integration tests."""

import json
import math
from pathlib import Path

from maya import cmds

from mmd_tools.converters.bone_morph_runtime import build_bone_morph_graph
from tests.common.maya_test_base import MayaTestBase


class TestBoneMorphRuntime(MayaTestBase):
    """Synthetic scene tests for PMX bone morph DG runtime wiring."""

    def _require_accumulator_node(self):
        try:
            node = cmds.createNode("mmdBoneMorphAccum", name="availability_probe_boneMorphAccum")
        except RuntimeError as exc:
            plugin_path = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
            try:
                self.load_plugin(str(plugin_path))
                node = cmds.createNode("mmdBoneMorphAccum", name="availability_probe_boneMorphAccum")
            except RuntimeError:
                self.skipTest(f"mmdBoneMorphAccum node is unavailable: {exc}")
        cmds.delete(node)

    def _create_indexed_joint(self, name="bone_joint", bone_index=0):
        root = cmds.group(empty=True, name="model_root")
        joint = cmds.createNode("joint", name=name, parent=root)
        cmds.addAttr(joint, longName="mmd_bone_index", attributeType="long")
        cmds.setAttr(f"{joint}.mmd_bone_index", bone_index)
        return root, joint

    def _create_bone_morph_node(self, name, morph_index, offsets):
        node = cmds.createNode("network", name=name)
        cmds.addAttr(
            node,
            longName="weight",
            attributeType="double",
            minValue=0.0,
            maxValue=1.0,
            defaultValue=0.0,
            keyable=True,
        )
        cmds.addAttr(node, longName="mmd_morph_type", dataType="string")
        cmds.addAttr(node, longName="mmd_morph_index", attributeType="long")
        cmds.addAttr(node, longName="mmd_bone_morph_offsets_json", dataType="string")
        cmds.setAttr(f"{node}.mmd_morph_type", "bone", type="string")
        cmds.setAttr(f"{node}.mmd_morph_index", morph_index)
        cmds.setAttr(
            f"{node}.mmd_bone_morph_offsets_json",
            json.dumps(offsets, separators=(",", ":")),
            type="string",
        )
        return node

    def _create_group_morph_node(self, name, morph_index, offsets):
        node = cmds.createNode("network", name=name)
        cmds.addAttr(
            node,
            longName="weight",
            attributeType="double",
            minValue=0.0,
            maxValue=1.0,
            defaultValue=0.0,
            keyable=True,
        )
        cmds.addAttr(node, longName="mmd_morph_type", dataType="string")
        cmds.addAttr(node, longName="mmd_morph_index", attributeType="long")
        cmds.addAttr(node, longName="mmd_group_morph_offsets_json", dataType="string")
        cmds.setAttr(f"{node}.mmd_morph_type", "group", type="string")
        cmds.setAttr(f"{node}.mmd_morph_index", morph_index)
        cmds.setAttr(
            f"{node}.mmd_group_morph_offsets_json",
            json.dumps(offsets, separators=(",", ":")),
            type="string",
        )
        return node

    def test_weight_drives_translate_offsets_and_adds_multiple_morphs(self):
        """weight=0 preserves base translate; multiple weighted offsets add."""
        self._require_accumulator_node()
        root, joint = self._create_indexed_joint()
        cmds.setAttr(f"{joint}.translate", 1.0, 2.0, 3.0, type="double3")

        morph_a = self._create_bone_morph_node(
            "move_a_boneMorph",
            0,
            [{"bone_index": 0, "translation": [2.0, 0.0, 4.0], "rotation": [0.0, 0.0, 0.0, 1.0]}],
        )
        morph_b = self._create_bone_morph_node(
            "move_b_boneMorph",
            1,
            [{"bone_index": 0, "translation": [0.0, 6.0, 2.0], "rotation": [0.0, 0.0, 0.0, 1.0]}],
        )

        result = build_bone_morph_graph(root)
        self.assertTrue(result["success"])
        self.assertEqual(result["contributions"], 2)

        cmds.setAttr(f"{morph_a}.weight", 0.0)
        cmds.setAttr(f"{morph_b}.weight", 0.0)
        base_translate = cmds.getAttr(f"{joint}.translate")[0]
        self.assertListAlmostEqual(base_translate, (1.0, 2.0, 3.0), places=5)

        cmds.setAttr(f"{morph_a}.weight", 0.25)
        cmds.setAttr(f"{morph_b}.weight", 0.5)
        moved_translate = cmds.getAttr(f"{joint}.translate")[0]
        # PMX (x, y, z) translation offsets become Maya (x, y, -z).
        self.assertListAlmostEqual(moved_translate, (1.5, 5.0, 1.0), places=5)

        again = build_bone_morph_graph(root)
        self.assertEqual(again["created"], 0)
        self.assertEqual(again["reused"], 1)
        self.assertEqual(len(cmds.ls(type="mmdBoneMorphAccum") or []), 1)

    def test_group_morph_weight_drives_referenced_bone_morph_offset(self):
        """Group morph references to bone morphs are weighted by group rate."""
        self._require_accumulator_node()
        root, joint = self._create_indexed_joint()

        self._create_bone_morph_node(
            "move_target_boneMorph",
            3,
            [{"bone_index": 0, "translation": [4.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0, 1.0]}],
        )
        group_morph = self._create_group_morph_node(
            "move_group_groupMorph",
            4,
            [{"morph_index": 3, "morph_rate": 0.25}],
        )

        result = build_bone_morph_graph(root)
        self.assertTrue(result["success"])
        self.assertEqual(result["contributions"], 2)

        cmds.setAttr(f"{group_morph}.weight", 0.5)
        self.assertListAlmostEqual(cmds.getAttr(f"{joint}.translate")[0], (0.5, 0.0, 0.0), places=5)

    def test_rotation_offset_uses_quaternion_slerp(self):
        """PMX quaternion is converted to Maya space and slerped by weight."""
        self._require_accumulator_node()
        root, joint = self._create_indexed_joint()
        half_sqrt = math.sqrt(0.5)
        morph = self._create_bone_morph_node(
            "rotate_x_boneMorph",
            0,
            [{"bone_index": 0, "translation": [0.0, 0.0, 0.0], "rotation": [half_sqrt, 0.0, 0.0, half_sqrt]}],
        )

        result = build_bone_morph_graph(root)
        self.assertTrue(result["success"])

        cmds.setAttr(f"{morph}.weight", 0.0)
        base_rotate = cmds.getAttr(f"{joint}.rotate")[0]
        self.assertListAlmostEqual(base_rotate, (0.0, 0.0, 0.0), places=4)

        cmds.setAttr(f"{morph}.weight", 0.5)
        half_rotate = cmds.getAttr(f"{joint}.rotate")[0]
        self.assertAlmostEqual(half_rotate[0], -45.0, delta=0.1)
        self.assertAlmostEqual(half_rotate[1], 0.0, delta=0.1)
        self.assertAlmostEqual(half_rotate[2], 0.0, delta=0.1)

        cmds.setAttr(f"{morph}.weight", 1.0)
        full_rotate = cmds.getAttr(f"{joint}.rotate")[0]
        self.assertAlmostEqual(full_rotate[0], -90.0, delta=0.1)

    def test_accumulator_inserts_upstream_of_mmd_append_when_present(self):
        """When mmdAppend drives a joint, bone morph output feeds append base attrs."""
        self._require_accumulator_node()
        try:
            append_node = cmds.createNode("mmdAppend", name="target_mmdAppend")
        except RuntimeError as exc:
            self.skipTest(f"mmdAppend node is unavailable: {exc}")

        root, joint = self._create_indexed_joint()
        cmds.setAttr(f"{append_node}.affectTranslation", True)
        cmds.setAttr(f"{append_node}.baseTranslate", 3.0, 0.0, 0.0, type="double3")
        cmds.connectAttr(f"{append_node}.outputTranslate", f"{joint}.translate")
        morph = self._create_bone_morph_node(
            "append_upstream_boneMorph",
            0,
            [{"bone_index": 0, "translation": [1.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0, 1.0]}],
        )

        result = build_bone_morph_graph(root)
        self.assertTrue(result["success"])
        accum = result["accumulator_nodes"][0]
        self.assertIn(
            f"{accum}.outputTranslate",
            cmds.listConnections(f"{append_node}.baseTranslate", s=True, d=False, p=True) or [],
        )
        self.assertNotIn(
            f"{accum}.outputTranslate",
            cmds.listConnections(f"{joint}.translate", s=True, d=False, p=True) or [],
        )

        cmds.setAttr(f"{morph}.weight", 1.0)
        self.assertListAlmostEqual(cmds.getAttr(f"{append_node}.baseTranslate")[0], (4.0, 0.0, 0.0), places=5)
