"""PMX bone morph runtime accumulator graph integration tests."""

import json
import math
import os
from pathlib import Path
from unittest import mock

import maya.api.OpenMaya as om
from maya import cmds

from mmd_tools.converters import bone_morph_runtime
from mmd_tools.converters.bone_morph_runtime import build_bone_morph_graph
from mmd_tools.converters.vmd_context import VmdImportStateContext
from mmd_tools.converters.vmd_import_state import clear_existing_motion
from mmd_tools.converters.vmd_morph_mapping import iter_morph_mappings
from mmd_tools.nodes.mmd_bone_morph_accum_node import MmdBoneMorphAccumNode
from tests.common.maya_test_base import MayaTestBase


def _restore_skip_shader_override(previous):
    if previous is None:
        os.environ.pop("MMD_TOOLS_SKIP_SHADER_OVERRIDE", None)
    else:
        os.environ["MMD_TOOLS_SKIP_SHADER_OVERRIDE"] = previous


def _load_repo_plugin_for_tests(owned_plugins):
    previous = os.environ.get("MMD_TOOLS_SKIP_SHADER_OVERRIDE")
    os.environ["MMD_TOOLS_SKIP_SHADER_OVERRIDE"] = "1"
    plugin_path = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
    was_loaded = False
    try:
        was_loaded = bool(cmds.pluginInfo(str(plugin_path), query=True, loaded=True))
        if not was_loaded:
            owned_plugins.extend(cmds.loadPlugin(str(plugin_path), quiet=True) or [])
        return previous
    except Exception:
        if not was_loaded:
            try:
                if cmds.pluginInfo(str(plugin_path), query=True, loaded=True):
                    cmds.unloadPlugin(str(plugin_path))
            except Exception:
                pass
        _restore_skip_shader_override(previous)
        raise


class TestBoneMorphRuntime(MayaTestBase):
    """Synthetic scene tests for PMX bone morph DG runtime wiring."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plugins_loaded = []
        cls._previous_skip_shader_override = _load_repo_plugin_for_tests(cls.plugins_loaded)

    @classmethod
    def tearDownClass(cls):
        try:
            super().tearDownClass()
        finally:
            _restore_skip_shader_override(cls._previous_skip_shader_override)

    def test_plugin_harness_uses_class_local_ownership(self):
        self.assertIsNot(type(self).plugins_loaded, MayaTestBase.plugins_loaded)

    def test_plugin_load_failure_restores_environment_and_unloads_partial_load(self):
        previous = os.environ.get("MMD_TOOLS_SKIP_SHADER_OVERRIDE")
        os.environ["MMD_TOOLS_SKIP_SHADER_OVERRIDE"] = "preserved"
        owned_plugins = []
        fake_cmds = mock.Mock()
        fake_cmds.pluginInfo.side_effect = [False, True]
        fake_cmds.loadPlugin.side_effect = RuntimeError("simulated plugin load failure")
        try:
            with mock.patch.dict(globals(), {"cmds": fake_cmds}):
                with self.assertRaisesRegex(RuntimeError, "simulated plugin load failure"):
                    _load_repo_plugin_for_tests(owned_plugins)
            self.assertEqual(os.environ.get("MMD_TOOLS_SKIP_SHADER_OVERRIDE"), "preserved")
            self.assertEqual(owned_plugins, [])
            fake_cmds.unloadPlugin.assert_called_once()
        finally:
            _restore_skip_shader_override(previous)

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
        self._current_model_root = root
        return root, joint

    def _connect_morph_root(self, node, root_group=None):
        root_group = root_group or self._current_model_root
        cmds.addAttr(node, longName="mmd_model_root", attributeType="message")
        cmds.connectAttr(f"{root_group}.message", f"{node}.mmd_model_root")

    def _create_bone_morph_node(self, name, morph_index, offsets, root_group=None):
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
        self._connect_morph_root(node, root_group)
        return node

    def _create_group_morph_node(self, name, morph_index, offsets, root_group=None):
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
        self._connect_morph_root(node, root_group)
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

        with mock.patch.object(
            bone_morph_runtime,
            "_joint_bind_world_orientation",
            wraps=bone_morph_runtime._joint_bind_world_orientation,
        ) as bind_orientation:
            result = build_bone_morph_graph(root)
        self.assertTrue(result["success"])
        self.assertEqual(result["contributions"], 2)
        self.assertEqual(bind_orientation.call_count, 1)

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
        """Cross-index controller dirtying drives the referenced bone leaf."""
        self._require_accumulator_node()
        root, joint = self._create_indexed_joint()

        bone_morph = self._create_bone_morph_node(
            "move_target_boneMorph",
            3,
            [
                {
                    "bone_index": 0,
                    "translation": [4.0, 0.0, 0.0],
                    "rotation": [0.0, 0.0, math.sqrt(0.5), math.sqrt(0.5)],
                }
            ],
        )
        self._create_group_morph_node(
            "move_group_groupMorph",
            4,
            [{"morph_index": 3, "morph_rate": 0.25}],
        )
        controller = cmds.createNode("mmdMorphController")
        cmds.setAttr(f"{controller}.topologyVersion", 1)
        cmds.setAttr(f"{controller}.groupTopology", '{"3":[[4,0.25]]}', type="string")
        cmds.connectAttr(f"{controller}.outputWeight[3]", f"{bone_morph}.weight")

        result = build_bone_morph_graph(root)
        self.assertTrue(result["success"])
        self.assertEqual(result["contributions"], 1)

        cmds.setAttr(f"{controller}.inputWeight[4]", 0.5)
        self.assertListAlmostEqual(cmds.getAttr(f"{joint}.translate")[0], (0.5, 0.0, 0.0), places=5)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.rotateZ"), 11.25, delta=0.1)

    def test_root_scoped_discovery_isolates_same_index_and_skips_legacy_network(self):
        """Building model B isolates its Controller-driven bone leaf and warns for legacy data."""
        self._require_accumulator_node()
        root_a, joint_a = self._create_indexed_joint(name="shared_bone", bone_index=7)
        root_b, joint_b = self._create_indexed_joint(name="shared_bone", bone_index=7)
        offset = [
            {
                "bone_index": 7,
                "translation": [2.0, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0, 1.0],
            }
        ]
        bone_a = self._create_bone_morph_node("model_a_boneMorph", 5, offset, root_a)
        bone_b = self._create_bone_morph_node("model_b_boneMorph", 5, offset, root_b)
        controller_a = cmds.createNode("mmdMorphController")
        cmds.addAttr(root_a, longName="mmd_morph_controller", attributeType="message")
        cmds.connectAttr(f"{controller_a}.message", f"{root_a}.mmd_morph_controller")
        cmds.setAttr(f"{controller_a}.topologyVersion", 1)
        cmds.setAttr(f"{controller_a}.groupTopology", '{"5":[[6,0.5]]}', type="string")
        cmds.connectAttr(f"{controller_a}.outputWeight[5]", f"{bone_a}.weight")
        controller_b = cmds.createNode("mmdMorphController")
        cmds.addAttr(root_b, longName="mmd_morph_controller", attributeType="message")
        cmds.connectAttr(f"{controller_b}.message", f"{root_b}.mmd_morph_controller")
        cmds.setAttr(f"{controller_b}.topologyVersion", 1)
        cmds.setAttr(f"{controller_b}.groupTopology", '{"5":[[6,0.5]]}', type="string")
        cmds.connectAttr(f"{controller_b}.outputWeight[5]", f"{bone_b}.weight")
        legacy = self._create_bone_morph_node("legacy_unowned_boneMorph", 5, offset, root_b)
        cmds.deleteAttr(f"{legacy}.mmd_model_root")

        with self.assertLogs(
            "mmd_tools.converters.morph_scene_metadata",
            level="WARNING",
        ) as captured:
            result = build_bone_morph_graph(root_b)

        self.assertTrue(result["success"])
        self.assertEqual(result["contributions"], 1)
        migration_warnings = [
            message for message in captured.output if "migration required" in message
        ]
        self.assertEqual(len(migration_warnings), 1)
        self.assertIn(legacy, migration_warnings[0])
        self.assertIn(root_b, migration_warnings[0])

        cmds.setAttr(f"{controller_a}.inputWeight[5]", 1.0)
        cmds.setAttr(f"{controller_a}.inputWeight[6]", 1.0)
        cmds.setAttr(f"{legacy}.weight", 1.0)
        self.assertListAlmostEqual(cmds.getAttr(f"{joint_a}.translate")[0], (0.0, 0.0, 0.0))
        self.assertListAlmostEqual(cmds.getAttr(f"{joint_b}.translate")[0], (0.0, 0.0, 0.0))

        cmds.setAttr(f"{controller_b}.inputWeight[5]", 1.0)
        cmds.setAttr(f"{controller_b}.inputWeight[6]", 1.0)
        self.assertListAlmostEqual(cmds.getAttr(f"{joint_b}.translate")[0], (3.0, 0.0, 0.0))
        self.assertListAlmostEqual(cmds.getAttr(f"{joint_a}.translate")[0], (0.0, 0.0, 0.0))

    def test_explicit_root_rejects_malformed_root_connections_once(self):
        """Unconnected, multi-root, and non-DAG root metadata all fail closed."""
        root, _joint = self._create_indexed_joint()
        other_root = cmds.group(empty=True, name="other_model_root")
        offset = [
            {
                "bone_index": 0,
                "translation": [1.0, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0, 1.0],
            }
        ]
        unconnected = self._create_bone_morph_node("unconnected_boneMorph", 0, offset)
        cmds.disconnectAttr(f"{root}.message", f"{unconnected}.mmd_model_root")

        multiple = self._create_bone_morph_node("multiple_roots_boneMorph", 1, offset)
        cmds.deleteAttr(f"{multiple}.mmd_model_root")
        cmds.addAttr(multiple, longName="mmd_model_root", attributeType="message", multi=True)
        cmds.connectAttr(f"{root}.message", f"{multiple}.mmd_model_root[0]")
        cmds.connectAttr(f"{other_root}.message", f"{multiple}.mmd_model_root[1]")

        invalid = self._create_bone_morph_node("invalid_root_boneMorph", 2, offset)
        cmds.disconnectAttr(f"{root}.message", f"{invalid}.mmd_model_root")
        invalid_root = cmds.createNode("network", name="not_a_dag_model_root")
        cmds.connectAttr(f"{invalid_root}.message", f"{invalid}.mmd_model_root")

        with self.assertLogs(
            "mmd_tools.converters.morph_scene_metadata",
            level="WARNING",
        ) as captured:
            discovered = list(bone_morph_runtime._iter_bone_morph_nodes(root))

        self.assertEqual(discovered, [])
        migration_warnings = [
            message for message in captured.output if "migration required" in message
        ]
        self.assertEqual(len(migration_warnings), 3)
        for node in (unconnected, multiple, invalid):
            matching = [message for message in migration_warnings if node in message]
            self.assertEqual(len(matching), 1)
            self.assertIn(root, matching[0])

    def test_root_scoped_clear_preserves_bone_morph_curve_and_runtime_graph(self):
        """Clear target morph keys in place while leaving both model graphs intact."""
        self._require_accumulator_node()
        target_root, target_joint = self._create_indexed_joint(
            name="clear_target_bone",
            bone_index=0,
        )
        foreign_root, foreign_joint = self._create_indexed_joint(
            name="clear_foreign_bone",
            bone_index=0,
        )
        target_morph = self._create_bone_morph_node(
            "clear_target_boneMorph",
            0,
            [{"bone_index": 0, "translation": [1.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0, 1.0]}],
            target_root,
        )
        foreign_morph = self._create_bone_morph_node(
            "clear_foreign_boneMorph",
            0,
            [{"bone_index": 0, "translation": [2.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0, 1.0]}],
            foreign_root,
        )

        target_result = build_bone_morph_graph(target_root)
        foreign_result = build_bone_morph_graph(foreign_root)
        self.assertTrue(target_result["success"])
        self.assertTrue(foreign_result["success"])
        target_accum = target_result["accumulator_nodes"][0]
        foreign_accum = foreign_result["accumulator_nodes"][0]

        cmds.setKeyframe(target_morph, attribute="weight", time=2, value=0.25)
        cmds.setKeyframe(target_morph, attribute="weight", time=8, value=0.75)
        cmds.setKeyframe(foreign_morph, attribute="weight", time=3, value=0.4)
        cmds.setKeyframe(foreign_morph, attribute="weight", time=9, value=0.9)

        def _curve_state(morph):
            curves = cmds.listConnections(
                f"{morph}.weight",
                source=True,
                destination=False,
                type="animCurve",
                plugs=True,
            ) or []
            self.assertEqual(len(curves), 1)
            curve_plug = curves[0]
            curve = curve_plug.split(".", 1)[0]
            return {
                "curve": curve,
                "uuid": (cmds.ls(curve, uuid=True) or [None])[0],
                "source": curve_plug,
                "destination": tuple(
                    cmds.listConnections(
                        curve_plug,
                        source=False,
                        destination=True,
                        plugs=True,
                    )
                    or []
                ),
                "times": tuple(
                    cmds.keyframe(morph, attribute="weight", query=True, timeChange=True) or []
                ),
                "values": tuple(
                    cmds.keyframe(morph, attribute="weight", query=True, valueChange=True) or []
                ),
            }

        target_curve_before = _curve_state(target_morph)
        foreign_curve_before = _curve_state(foreign_morph)
        target_contribution_sources = tuple(
            cmds.listConnections(
                f"{target_accum}.contribution[0].weight",
                source=True,
                destination=False,
                plugs=True,
            )
            or []
        )
        foreign_contribution_sources = tuple(
            cmds.listConnections(
                f"{foreign_accum}.contribution[0].weight",
                source=True,
                destination=False,
                plugs=True,
            )
            or []
        )
        target_output_destinations = tuple(
            cmds.listConnections(
                f"{target_accum}.outputTranslate",
                source=False,
                destination=True,
                plugs=True,
            )
            or []
        )
        foreign_output_destinations = tuple(
            cmds.listConnections(
                f"{foreign_accum}.outputTranslate",
                source=False,
                destination=True,
                plugs=True,
            )
            or []
        )

        context = VmdImportStateContext(
            logger=bone_morph_runtime.logger,
            bone_name_mapping={},
            bone_bind_poses={},
            morph_name_mapping={
                "target": (target_morph, "weight", "target"),
                "foreign": (foreign_morph, "weight", "foreign"),
            },
            collect_append_info=lambda: {},
            iter_morph_mappings=iter_morph_mappings,
            set_refresh_suspended=lambda _value: None,
        )
        clear_existing_motion(context, "missing_layer", target_model=target_root)

        self.assertIsNone(
            cmds.keyframe(target_morph, attribute="weight", query=True, timeChange=True)
        )
        self.assertEqual(
            cmds.keyframe(foreign_morph, attribute="weight", query=True, timeChange=True),
            list(foreign_curve_before["times"]),
        )
        self.assertEqual(_curve_state(target_morph)["curve"], target_curve_before["curve"])
        self.assertEqual(_curve_state(target_morph)["uuid"], target_curve_before["uuid"])
        self.assertEqual(_curve_state(target_morph)["source"], target_curve_before["source"])
        self.assertEqual(
            tuple(
                cmds.listConnections(
                    f"{target_accum}.contribution[0].weight",
                    source=True,
                    destination=False,
                    plugs=True,
                )
                or []
            ),
            target_contribution_sources,
        )
        self.assertEqual(
            tuple(
                cmds.listConnections(
                    f"{target_accum}.outputTranslate",
                    source=False,
                    destination=True,
                    plugs=True,
                )
                or []
            ),
            target_output_destinations,
        )
        self.assertTrue(cmds.objExists(target_morph))
        self.assertTrue(cmds.objExists(target_accum))
        self.assertTrue(cmds.objExists(target_joint))

        self.assertEqual(_curve_state(foreign_morph), foreign_curve_before)
        self.assertEqual(
            tuple(
                cmds.listConnections(
                    f"{foreign_accum}.contribution[0].weight",
                    source=True,
                    destination=False,
                    plugs=True,
                )
                or []
            ),
            foreign_contribution_sources,
        )
        self.assertEqual(
            tuple(
                cmds.listConnections(
                    f"{foreign_accum}.outputTranslate",
                    source=False,
                    destination=True,
                    plugs=True,
                )
                or []
            ),
            foreign_output_destinations,
        )
        self.assertTrue(cmds.objExists(foreign_morph))
        self.assertTrue(cmds.objExists(foreign_accum))
        self.assertTrue(cmds.objExists(foreign_joint))

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

    def test_asymmetric_rotation_preserves_base_order_in_joint_orient_basis(self):
        """Bind-basis offsets compose after nonzero base rotation for every rotateOrder."""
        self._require_accumulator_node()
        raw_axis = om.MVector(0.31, -0.57, 0.76).normal()
        raw_angle = math.radians(73.0)
        raw_quat = om.MQuaternion(raw_angle, raw_axis)
        reflected = om.MQuaternion(-raw_quat.x, -raw_quat.y, raw_quat.z, raw_quat.w)
        second_quat = om.MQuaternion(math.radians(41.0), om.MVector(-0.42, 0.81, 0.39).normal())
        second_reflected = om.MQuaternion(
            -second_quat.x,
            -second_quat.y,
            second_quat.z,
            second_quat.w,
        )
        orders = (
            om.MEulerRotation.kXYZ,
            om.MEulerRotation.kYZX,
            om.MEulerRotation.kZXY,
            om.MEulerRotation.kXZY,
            om.MEulerRotation.kYXZ,
            om.MEulerRotation.kZYX,
        )

        for rotate_order, maya_order in enumerate(orders):
            with self.subTest(rotate_order=rotate_order):
                cmds.file(new=True, force=True)
                root = cmds.group(empty=True, name=f"model_root_{rotate_order}")
                parent = cmds.createNode("joint", name=f"parent_{rotate_order}", parent=root)
                joint = cmds.createNode("joint", name=f"target_{rotate_order}", parent=parent)
                cmds.setAttr(f"{parent}.jointOrient", 18.0, -27.0, 11.0, type="double3")
                cmds.setAttr(f"{joint}.jointOrient", -21.0, 13.0, 34.0, type="double3")
                cmds.setAttr(f"{joint}.rotateOrder", rotate_order)
                cmds.setAttr(f"{joint}.rotate", 17.0, -23.0, 31.0, type="double3")
                cmds.addAttr(joint, longName="mmd_bone_index", attributeType="long")
                cmds.setAttr(f"{joint}.mmd_bone_index", 0)
                self._current_model_root = root
                morph = self._create_bone_morph_node(
                    f"asymmetric_{rotate_order}_boneMorph",
                    1,
                    [
                        {
                            "bone_index": 0,
                            "translation": [0.0, 0.0, 0.0],
                            "rotation": [raw_quat.x, raw_quat.y, raw_quat.z, raw_quat.w],
                        }
                    ],
                )
                earlier_morph = self._create_bone_morph_node(
                    f"earlier_{rotate_order}_boneMorph",
                    0,
                    [
                        {
                            "bone_index": 0,
                            "translation": [0.0, 0.0, 0.0],
                            "rotation": [
                                second_quat.x,
                                second_quat.y,
                                second_quat.z,
                                second_quat.w,
                            ],
                        }
                    ],
                )

                result = build_bone_morph_graph(root)
                self.assertTrue(result["success"])
                cmds.setAttr(f"{morph}.weight", 0.5)
                cmds.setAttr(f"{earlier_morph}.weight", 0.25)

                actual_values = cmds.getAttr(f"{joint}.rotate")[0]
                actual = om.MEulerRotation(
                    *(math.radians(value) for value in actual_values),
                    maya_order,
                ).asQuaternion()
                base = om.MEulerRotation(
                    math.radians(17.0),
                    math.radians(-23.0),
                    math.radians(31.0),
                    maya_order,
                ).asQuaternion()
                bind = bone_morph_runtime._joint_bind_world_orientation(joint)
                offset = bind * reflected * bind.inverse()
                earlier_offset = bind * second_reflected * bind.inverse()
                expected = (
                    base
                    * om.MQuaternion.slerp(om.MQuaternion(), earlier_offset, 0.25)
                    * om.MQuaternion.slerp(om.MQuaternion(), offset, 0.5)
                )
                dot = abs(
                    actual.x * expected.x
                    + actual.y * expected.y
                    + actual.z * expected.z
                    + actual.w * expected.w
                )
                self.assertAlmostEqual(dot, 1.0, places=6)

    def test_accumulator_inserts_upstream_of_mmd_append_when_present(self):
        """When mmdAppend drives a joint, bone morph output feeds append base attrs."""
        self._require_accumulator_node()
        try:
            append_node = cmds.createNode("mmdAppend", name="target_mmdAppend")
        except RuntimeError as exc:
            self.skipTest(f"mmdAppend node is unavailable: {exc}")

        root, joint = self._create_indexed_joint()
        cmds.setAttr(f"{append_node}.affectTranslation", True)
        cmds.setAttr(f"{append_node}.affectRotation", True)
        cmds.setAttr(f"{append_node}.baseTranslate", 3.0, 0.0, 0.0, type="double3")
        cmds.setAttr(f"{append_node}.baseRotate", 5.0, 0.0, 0.0, type="double3")
        cmds.connectAttr(f"{append_node}.outputTranslate", f"{joint}.translate")
        cmds.connectAttr(f"{append_node}.outputRotate", f"{joint}.rotate")
        half_sqrt = math.sqrt(0.5)
        morph = self._create_bone_morph_node(
            "append_upstream_boneMorph",
            0,
            [
                {
                    "bone_index": 0,
                    "translation": [1.0, 0.0, 0.0],
                    "rotation": [half_sqrt, 0.0, 0.0, half_sqrt],
                }
            ],
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
        self.assertAlmostEqual(cmds.getAttr(f"{append_node}.baseRotateX"), -85.0, delta=0.1)

    def test_accumulator_feeds_mmd_ccd_ik_input_not_joint_rotate(self):
        """When mmdCcdIk drives joint.rotate, bone morph feeds inputRotate[bone_slot]."""
        self._require_accumulator_node()
        try:
            ik_node = cmds.createNode("mmdCcdIk", name="link_mmdCcdIk")
        except RuntimeError as exc:
            self.skipTest(f"mmdCcdIk node is unavailable: {exc}")

        root, joint = self._create_indexed_joint(name="ik_link_joint", bone_index=0)
        # link_i=0 maps to bone_slot=1 so the destination must use chainJson, not link index.
        chain = {
            "bones": [
                {"rest_position": [0.0, 0.0, 0.0], "parent_slot": -1},
                {"rest_position": [0.0, 1.0, 0.0], "parent_slot": 0},
            ],
            "links": [{"bone_slot": 1}],
            "targetBoneSlot": 0,
            "controllerBoneSlot": -1,
            "iterationCount": 1,
            "limitAngle": 0.1,
        }
        cmds.setAttr(f"{ik_node}.chainJson", json.dumps(chain), type="string")
        # Disabled solver is pass-through: outputRotate[link_i] == inputRotate[bone_slot].
        cmds.setAttr(f"{ik_node}.enabled", False)
        cmds.setAttr(f"{ik_node}.inputRotate[1]", 5.0, 0.0, 0.0, type="double3")
        cmds.connectAttr(f"{ik_node}.outputRotate[0]", f"{joint}.rotate", force=True)

        half_sqrt = math.sqrt(0.5)
        morph = self._create_bone_morph_node(
            "ik_upstream_boneMorph",
            0,
            [
                {
                    "bone_index": 0,
                    "translation": [0.0, 0.0, 0.0],
                    # PMX +90° X → Maya -90° X after coordinate conversion.
                    "rotation": [half_sqrt, 0.0, 0.0, half_sqrt],
                }
            ],
        )

        result = build_bone_morph_graph(root)
        self.assertTrue(result["success"])
        self.assertEqual(result["created"], 1)
        accum = result["accumulator_nodes"][0]

        joint_rotate_sources = cmds.listConnections(f"{joint}.rotate", s=True, d=False, p=True) or []
        self.assertIn(f"{ik_node}.outputRotate[0]", joint_rotate_sources)
        self.assertNotIn(f"{accum}.outputRotate", joint_rotate_sources)

        ik_input_sources = cmds.listConnections(
            f"{ik_node}.inputRotate[1]", s=True, d=False, p=True
        ) or []
        self.assertIn(f"{accum}.outputRotate", ik_input_sources)
        # Wrong slot must stay free (proves bone_slot mapping, not link_i).
        self.assertFalse(
            cmds.listConnections(f"{ik_node}.inputRotate[0]", s=True, d=False, p=True) or []
        )

        # Morph weight contributes through the solver input; disabled IK copies it out.
        cmds.setAttr(f"{morph}.weight", 0.0)
        base_input = cmds.getAttr(f"{ik_node}.inputRotate[1]")[0]
        self.assertListAlmostEqual(base_input, (5.0, 0.0, 0.0), places=4)
        self.assertListAlmostEqual(cmds.getAttr(f"{joint}.rotate")[0], (5.0, 0.0, 0.0), places=4)

        cmds.setAttr(f"{morph}.weight", 1.0)
        morphed_input = cmds.getAttr(f"{ik_node}.inputRotate[1]")[0]
        self.assertAlmostEqual(morphed_input[0], -85.0, delta=0.15)
        self.assertAlmostEqual(morphed_input[1], 0.0, delta=0.15)
        self.assertAlmostEqual(morphed_input[2], 0.0, delta=0.15)
        joint_rotate = cmds.getAttr(f"{joint}.rotate")[0]
        self.assertAlmostEqual(joint_rotate[0], morphed_input[0], delta=0.15)
        self.assertAlmostEqual(joint_rotate[1], morphed_input[1], delta=0.15)
        self.assertAlmostEqual(joint_rotate[2], morphed_input[2], delta=0.15)

        # Idempotent re-build: still one accumulator, IK output stays on joint.rotate.
        again = build_bone_morph_graph(root)
        self.assertEqual(again["created"], 0)
        self.assertEqual(again["reused"], 1)
        self.assertEqual(len(cmds.ls(type="mmdBoneMorphAccum") or []), 1)
        self.assertIn(
            f"{ik_node}.outputRotate[0]",
            cmds.listConnections(f"{joint}.rotate", s=True, d=False, p=True) or [],
        )
        self.assertIn(
            f"{accum}.outputRotate",
            cmds.listConnections(f"{ik_node}.inputRotate[1]", s=True, d=False, p=True) or [],
        )
        # Observable evaluation still succeeds (no DG cycle).
        cmds.getAttr(f"{joint}.rotate")
        cmds.getAttr(f"{ik_node}.outputRotate[0]")

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

    def test_probe_create_failure_preserves_scene_modified_and_restores_undo(self):
        """Unproven create failure keeps scene ownership visible but restores undo."""
        cmds_mock = mock.Mock()
        cmds_mock.file.side_effect = lambda **kwargs: True if kwargs.get("query") else None
        cmds_mock.undoInfo.side_effect = lambda **kwargs: True if kwargs.get("query") else None
        cmds_mock.createNode.side_effect = RuntimeError("unavailable")
        cmds_mock.objExists.return_value = False

        with mock.patch.object(bone_morph_runtime, "cmds", cmds_mock):
            bone_morph_runtime.probe_bone_morph_accum_availability()

        self.assertIn(mock.call(stateWithoutFlush=False), cmds_mock.undoInfo.call_args_list)
        self.assertIn(mock.call(stateWithoutFlush=True), cmds_mock.undoInfo.call_args_list)
        self.assertNotIn(mock.call(modified=True), cmds_mock.file.call_args_list)

    def test_probe_does_not_hide_scene_change_when_cleanup_fails(self):
        """A residual probe node must leave Maya's scene visibly dirty."""
        cmds_mock = mock.Mock()
        cmds_mock.file.side_effect = lambda **kwargs: False if kwargs.get("query") else None
        cmds_mock.undoInfo.side_effect = lambda **kwargs: True if kwargs.get("query") else None
        cmds_mock.createNode.return_value = bone_morph_runtime._PROBE_NODE_NAME
        cmds_mock.nodeType.return_value = "unknown"
        cmds_mock.objExists.return_value = True
        cmds_mock.delete.side_effect = RuntimeError("delete failed")

        with mock.patch.object(bone_morph_runtime, "cmds", cmds_mock):
            bone_morph_runtime.probe_bone_morph_accum_availability()

        self.assertNotIn(mock.call(modified=False), cmds_mock.file.call_args_list)

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
