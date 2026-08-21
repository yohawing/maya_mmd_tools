"""Focused tests for safe target-exclusive Control Rig animLayer ownership."""

from pathlib import Path
import unittest

import maya.cmds as cmds

from mmd_tools.core.mmd_control_rig_anim_layers import (
    MmdControlRigAnimLayerError,
    apply_mmd_control_rig_anim_layer_route,
    capture_mmd_control_rig_anim_layers,
    restore_mmd_control_rig_anim_layer_journal,
    restore_mmd_control_rig_anim_layer_route,
)
from tests.common.maya_test_base import MayaTestBase


class TestMmdControlRigAnimLayers(MayaTestBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plugin = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
        if not cmds.pluginInfo(str(plugin), query=True, loaded=True):
            cls.plugins_loaded.extend(cmds.loadPlugin(str(plugin), quiet=True) or [])

    def test_exclusive_layer_transfer_restore_and_settings_snapshot(self):
        root = cmds.group(empty=True, name="cr061_anim_layer_root")
        joint = cmds.createNode("transform", name="cr061_anim_layer_joint")
        control = cmds.createNode("transform", name="cr061_anim_layer_control")
        cmds.parent(joint, root)
        cmds.parent(control, root)
        layer = cmds.animLayer("cr061_anim_layer_exclusive", override=False, weight=1.0)
        cmds.animLayer(layer, edit=True, attribute=f"{joint}.translateX")
        cmds.setKeyframe(joint, attribute="translateX", time=2.0, value=4.0, animLayer=layer)
        cmds.animLayer(layer, edit=True, weight=0.65, selected=True, preferred=True)

        journal = capture_mmd_control_rig_anim_layers(
            cmds,
            root,
            (f"{joint}.translateX",),
        )
        self.assertEqual(len(journal["layers"]), 1)
        self.assertEqual(journal["layers"][0]["settings"]["weight"], 0.65)
        self.assertTrue(journal["layers"][0]["settings"]["selected"])
        self.assertTrue(journal["layers"][0]["settings"]["preferred"])
        target = f"{cmds.ls(joint, long=True)[0]}.translateX"
        route = journal["routes"][target]
        self.assertTrue(route["curveRef"]["nodeUuid"])
        self.assertTrue(journal["layers"][0]["curves"][0]["keys"])
        self.assertIn("timeInput", journal["layers"][0]["curves"][0])

        operations = []
        apply_mmd_control_rig_anim_layer_route(
            cmds,
            route,
            f"{control}.translateX",
            operations,
        )
        self.assertFalse(cmds.isConnected(route["curve"], route["blend"]))
        self.assertTrue(cmds.isConnected(route["curve"], f"{control}.translateX"))
        self.assertTrue(cmds.isConnected(f"{control}.translateX", route["blend"]))
        self.assertTrue(cmds.isConnected(route["blendOutput"], f"{joint}.translateX"))

        restore_mmd_control_rig_anim_layer_route(
            cmds,
            route,
            f"{control}.translateX",
        )
        self.assertTrue(cmds.isConnected(route["curve"], route["blend"]))
        self.assertFalse(cmds.listConnections(f"{control}.translateX", source=True, destination=False))

        cmds.animLayer(layer, edit=True, weight=0.2, selected=False, preferred=False)
        restore_mmd_control_rig_anim_layer_journal(cmds, journal)
        self.assertEqual(cmds.animLayer(layer, query=True, weight=True), 0.65)
        self.assertTrue(cmds.animLayer(layer, query=True, selected=True))
        self.assertTrue(cmds.animLayer(layer, query=True, preferred=True))
        self.assertTrue(cmds.isConnected(route["curve"], route["blend"]))

    def test_foreign_layer_membership_fails_closed_before_capture(self):
        root = cmds.group(empty=True, name="cr061_anim_layer_owned_root")
        owned = cmds.createNode("transform", name="cr061_anim_layer_owned_joint")
        foreign = cmds.createNode("transform", name="cr061_anim_layer_foreign_joint")
        cmds.parent(owned, root)
        layer = cmds.animLayer("cr061_anim_layer_shared", override=False, weight=1.0)
        cmds.animLayer(layer, edit=True, attribute=f"{owned}.translateX")
        cmds.animLayer(layer, edit=True, attribute=f"{foreign}.translateX")

        with self.assertRaisesRegex(
            MmdControlRigAnimLayerError,
            "foreign/shared",
        ):
            capture_mmd_control_rig_anim_layers(
                cmds,
                root,
                (f"{owned}.translateX",),
            )

    def test_layer_owned_entirely_by_another_scope_is_ignored(self):
        root = cmds.group(empty=True, name="cr061_anim_layer_target_root")
        foreign = cmds.createNode("transform", name="cr061_anim_layer_other_joint")
        layer = cmds.animLayer("cr061_anim_layer_other_model", override=False, weight=1.0)
        cmds.animLayer(layer, edit=True, attribute=f"{foreign}.translateX")

        journal = capture_mmd_control_rig_anim_layers(cmds, root, None)

        self.assertEqual(journal, {"layers": [], "routes": {}})

    def test_nested_layer_fails_closed(self):
        root = cmds.group(empty=True, name="cr061_anim_layer_nested_root")
        joint = cmds.createNode("transform", name="cr061_anim_layer_nested_joint")
        cmds.parent(joint, root)
        parent = cmds.animLayer("cr061_anim_layer_parent", override=False, weight=1.0)
        child = cmds.animLayer("cr061_anim_layer_child", override=False, weight=1.0, parent=parent)
        cmds.animLayer(child, edit=True, attribute=f"{joint}.translateX")

        with self.assertRaisesRegex(MmdControlRigAnimLayerError, "nested/shared"):
            capture_mmd_control_rig_anim_layers(cmds, root, (f"{joint}.translateX",))

    def test_omitted_target_plugs_validates_every_layer_member_route(self):
        root = cmds.group(empty=True, name="cr061_anim_layer_preflight_root")
        joint = cmds.createNode("transform", name="cr061_anim_layer_preflight_joint")
        cmds.parent(joint, root)
        layer = cmds.animLayer("cr061_anim_layer_malformed", override=False, weight=1.0)
        target = f"{cmds.ls(joint, long=True)[0]}.translateX"
        cmds.animLayer(layer, edit=True, attribute=target)

        layered = cmds.animLayer(layer, query=True, layeredPlug=target)
        layered = layered[0] if isinstance(layered, (list, tuple)) else layered
        self.assertTrue(layered)
        blend_node, input_attribute = str(layered).split(".", 1)
        suffix = input_attribute[len("inputB") :]
        cmds.disconnectAttr(f"{blend_node}.output{suffix}", target)

        with self.assertRaisesRegex(MmdControlRigAnimLayerError, "layered plug is missing"):
            # Omitting target_plugs is the preflight call shape and must inspect
            # every member, rather than silently returning an empty route set.
            capture_mmd_control_rig_anim_layers(cmds, root)

    def test_mmd_helper_foreign_fanout_fails_closed(self):
        if "mmdAppend" not in (cmds.allNodeTypes() or []):
            self.skipTest("mmdAppend plugin node is unavailable")

        root = cmds.group(empty=True, name="cr061_anim_layer_helper_root")
        target_joint = cmds.createNode("transform", name="cr061_anim_layer_helper_target")
        foreign_joint = cmds.createNode("transform", name="cr061_anim_layer_helper_foreign")
        cmds.parent(target_joint, root)
        helper = cmds.createNode("mmdAppend", name="cr061_anim_layer_helper")
        helper_output = f"{helper}.outputRotateX"
        cmds.connectAttr(helper_output, f"{target_joint}.rotateX", force=True)
        cmds.connectAttr(helper_output, f"{foreign_joint}.rotateX", force=True)

        layer = cmds.animLayer("cr061_anim_layer_helper_shared", override=False, weight=1.0)
        cmds.animLayer(layer, edit=True, attribute=f"{helper}.baseRotateX")

        with self.assertRaisesRegex(MmdControlRigAnimLayerError, "foreign/shared"):
            capture_mmd_control_rig_anim_layers(cmds, root)


if __name__ == "__main__":
    unittest.main()
