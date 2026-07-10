"""PMX bone morph runtime accumulator graph integration tests."""

import json
import math
from pathlib import Path
from unittest import mock

from maya import cmds

from mmd_tools.converters import bone_morph_runtime
from mmd_tools.converters.bone_morph_runtime import build_bone_morph_graph
from mmd_tools.nodes.mmd_bone_morph_accum_node import MmdBoneMorphAccumNode
from tests.common.maya_test_base import MayaTestBase


class TestBoneMorphRuntime(MayaTestBase):
    """Synthetic scene tests for PMX bone morph DG runtime wiring."""

    def test_plug_match_guard_ignores_uninitialized_attributes(self):
        class FakePlug:
            def __eq__(self, other):
                if other is None:
                    raise TypeError("MPlug or MObject expected.")
                return False

            def attribute(self):
                raise RuntimeError("no attribute")

        self.assertFalse(MmdBoneMorphAccumNode._plug_matches_any(FakePlug(), (None,)))

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

    def _scene_nodes_snapshot(self):
        return set(cmds.ls(long=True) or [])

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

    def test_probe_reports_unavailable_when_create_fails(self):
        """createNode failure is treated as node_type_unavailable, not success."""
        cmds_mock = mock.Mock()
        cmds_mock.createNode.side_effect = RuntimeError("Unknown object type: mmdBoneMorphAccum")
        cmds_mock.objExists.return_value = False

        with mock.patch.object(bone_morph_runtime, "cmds", cmds_mock):
            availability = bone_morph_runtime.probe_bone_morph_accum_availability()

        self.assertFalse(availability["available"])
        self.assertEqual(availability["code"], "node_type_unavailable")
        self.assertEqual(availability["reason"], "node_type_unavailable")
        self.assertIn("create_failed", availability["detail"])
        cmds_mock.delete.assert_not_called()

    def test_probe_deletes_unknown_node_and_reports_unavailable(self):
        """Unknown node returned by createNode is deleted and fails soft."""
        probe_name = bone_morph_runtime._PROBE_NODE_NAME
        cmds_mock = mock.Mock()
        cmds_mock.createNode.return_value = probe_name
        cmds_mock.nodeType.return_value = "unknown"
        cmds_mock.objExists.return_value = True
        cmds_mock.attributeQuery.return_value = True

        with mock.patch.object(bone_morph_runtime, "cmds", cmds_mock):
            availability = bone_morph_runtime.probe_bone_morph_accum_availability()

        self.assertFalse(availability["available"])
        self.assertEqual(availability["code"], "node_type_unavailable")
        self.assertEqual(availability["actual_type"], "unknown")
        self.assertIn("unknown_or_wrong_type", availability["detail"])
        cmds_mock.delete.assert_called_once_with(probe_name)

    def test_probe_reports_missing_required_attributes(self):
        """Missing required attrs fail soft and delete the temporary probe."""
        probe_name = bone_morph_runtime._PROBE_NODE_NAME
        cmds_mock = mock.Mock()
        cmds_mock.createNode.return_value = probe_name
        cmds_mock.nodeType.return_value = bone_morph_runtime.ACCUM_NODE_TYPE
        cmds_mock.objExists.return_value = True

        def _attr_exists(attr, node=None, exists=False):
            return attr not in ("rotateOffsetQuat", "baseRotate", "outputRotate")

        cmds_mock.attributeQuery.side_effect = _attr_exists

        with mock.patch.object(bone_morph_runtime, "cmds", cmds_mock):
            availability = bone_morph_runtime.probe_bone_morph_accum_availability()

        self.assertFalse(availability["available"])
        self.assertEqual(availability["code"], "node_type_unavailable")
        self.assertEqual(
            availability["missing_attributes"],
            ["rotateOffsetQuat", "baseRotate", "outputRotate"],
        )
        self.assertIn("missing_attributes", availability["detail"])
        cmds_mock.delete.assert_called_once_with(probe_name)

    def test_create_accumulator_deletes_unknown_or_invalid_node(self):
        """Per-joint create rejects unknown/invalid nodes without leaving artifacts."""
        cmds_mock = mock.Mock()
        cmds_mock.createNode.return_value = "joint_boneMorphAccum"
        cmds_mock.nodeType.return_value = "unknown"
        cmds_mock.objExists.return_value = True

        with mock.patch.object(bone_morph_runtime, "cmds", cmds_mock):
            node = bone_morph_runtime._create_accumulator("joint")

        self.assertIsNone(node)
        cmds_mock.delete.assert_called_once_with("joint_boneMorphAccum")

    def test_build_skips_graph_when_node_type_unavailable(self):
        """Unavailable accumulators skip graph mutation and preserve morph metadata."""
        root, joint = self._create_indexed_joint()
        morph = self._create_bone_morph_node(
            "skip_graph_boneMorph",
            0,
            [{"bone_index": 0, "translation": [1.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0, 1.0]}],
        )
        morph_json_before = cmds.getAttr(f"{morph}.mmd_bone_morph_offsets_json")
        morph_type_before = cmds.getAttr(f"{morph}.mmd_morph_type")
        before_nodes = self._scene_nodes_snapshot()

        unavailable = {
            "available": False,
            "code": "node_type_unavailable",
            "reason": "node_type_unavailable",
            "node_type": bone_morph_runtime.ACCUM_NODE_TYPE,
            "detail": "create_failed: simulated",
            "missing_attributes": [],
            "actual_type": "",
        }
        with mock.patch.object(
            bone_morph_runtime,
            "probe_bone_morph_accum_availability",
            return_value=unavailable,
        ):
            result = build_bone_morph_graph(root)

        self.assertFalse(result["success"])
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["reused"], 0)
        self.assertEqual(result["contributions"], 0)
        self.assertEqual(result["accumulator_nodes"], [])
        self.assertIn("node_type_unavailable", result["skipped"])
        self.assertEqual(len(result["warnings"]), 1)
        warning = result["warnings"][0]
        self.assertEqual(warning["code"], "node_type_unavailable")
        self.assertEqual(warning["reason"], "node_type_unavailable")
        self.assertEqual(warning["node_type"], "mmdBoneMorphAccum")

        self.assertTrue(cmds.objExists(morph))
        self.assertEqual(cmds.getAttr(f"{morph}.mmd_bone_morph_offsets_json"), morph_json_before)
        self.assertEqual(cmds.getAttr(f"{morph}.mmd_morph_type"), morph_type_before)
        self.assertEqual(self._scene_nodes_snapshot(), before_nodes)
        self.assertEqual(len(cmds.ls(type="mmdBoneMorphAccum") or []), 0)
        # Joint remains free of accumulator-driven connections.
        self.assertFalse(cmds.listConnections(f"{joint}.translate", s=True, d=False) or [])
        self.assertFalse(cmds.listConnections(f"{joint}.rotate", s=True, d=False) or [])

    def test_build_available_path_still_creates_accumulator(self):
        """Registered node with full contract continues to build graphs."""
        self._require_accumulator_node()
        root, joint = self._create_indexed_joint(name="available_joint")
        morph = self._create_bone_morph_node(
            "available_path_boneMorph",
            0,
            [{"bone_index": 0, "translation": [2.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0, 1.0]}],
        )

        result = build_bone_morph_graph(root)
        self.assertTrue(result["success"])
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["warnings"], [])
        self.assertEqual(len(result["accumulator_nodes"]), 1)
        accum = result["accumulator_nodes"][0]
        self.assertTrue(cmds.objExists(accum))
        self.assertEqual(cmds.nodeType(accum), "mmdBoneMorphAccum")

        cmds.setAttr(f"{morph}.weight", 1.0)
        self.assertListAlmostEqual(cmds.getAttr(f"{joint}.translate")[0], (2.0, 0.0, 0.0), places=5)
