"""VMD existing-motion clearing and layer selection tests."""

from unittest.mock import MagicMock, patch

import maya.cmds as cmds

from mmd_tools.converters.vmd_context import VmdImportStateContext
from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.converters.vmd_import_state import clear_existing_motion, record_bind_poses
from mmd_tools.core.constants import ATTR_MMD_BONE_NAME
from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame
from tests.common.maya_test_base import MayaTestBase


def _bone_frame(bone_name, frame_number, position, rotation=(0.0, 0.0, 0.0, 1.0)):
    frame = VmdBoneFrame()
    frame.bone_name = bone_name
    frame.frame_number = frame_number
    frame.position = position
    frame.rotation = rotation
    return frame


class TestVmdMotionClear(MayaTestBase):
    """Existing VMD motion clearing tests."""

    def setUp(self):
        super().setUp()
        self.converter = VmdConverter()

    def _import_state_context(self) -> VmdImportStateContext:
        return VmdImportStateContext(
            logger=self.converter.logger,
            bone_name_mapping=self.converter.bone_name_mapping,
            bone_bind_poses=self.converter._bone_bind_poses,
            morph_name_mapping=self.converter.morph_name_mapping,
            collect_append_info=lambda: {},
            iter_morph_mappings=self.converter._iter_morph_mappings,
            set_refresh_suspended=self.converter._set_vmd_import_refresh_suspended,
        )

    def test_anim_layer_selection_restore_deselects_new_vmd_layer(self):
        """VMD import 中に作られた layer を selected のまま残さない。"""
        previous_layer = cmds.animLayer("pre_vmd_selected_layer", override=False, weight=1.0)
        cmds.animLayer(previous_layer, edit=True, selected=True)
        snapshot = VmdConverter._capture_anim_layer_selection()

        vmd_layer = cmds.animLayer("VMD_Motion_restore_test", override=False, weight=1.0)
        cmds.animLayer(vmd_layer, edit=True, selected=True)

        VmdConverter._restore_anim_layer_selection(snapshot)

        self.assertTrue(cmds.animLayer(previous_layer, query=True, selected=True))
        self.assertFalse(cmds.animLayer(vmd_layer, query=True, selected=True))

    def test_clear_existing_motion_cuts_joint_morph_keys_and_deletes_vmd_layer(self):
        """VMD 再インポート前に対象 motion key と既存 VMD layer を消す。"""
        joint = cmds.joint(name="clear_existing_motion_joint")
        cmds.setKeyframe(joint, attribute="translateX", time=1, value=1.0)
        cmds.setKeyframe(joint, attribute="rotateY", time=5, value=20.0)

        mesh = cmds.polyCube(name="clear_existing_motion_mesh")[0]
        blend_shape = cmds.blendShape(mesh, name="clear_existing_motion_blendShape")[0]
        cmds.aliasAttr("smile", f"{blend_shape}.weight[0]")
        cmds.setKeyframe(blend_shape, attribute="weight[0]", time=3, value=0.75)

        layer = cmds.animLayer("VMD_Motion_clear_existing_test", override=False, weight=1.0)

        self.converter.bone_name_mapping = {"センター": joint}
        self.converter.morph_name_mapping = {"smile": (blend_shape, "weight[0]", "smile")}

        clear_existing_motion(self._import_state_context(), layer, target_namespace=None)

        self.assertIsNone(cmds.keyframe(joint, attribute="translateX", query=True, timeChange=True))
        self.assertIsNone(cmds.keyframe(joint, attribute="rotateY", query=True, timeChange=True))
        self.assertIsNone(cmds.keyframe(blend_shape, attribute="weight[0]", query=True, timeChange=True))
        self.assertFalse(cmds.objExists(layer))
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.rotateY"), 0.0, places=4)

    def test_clear_existing_motion_restores_bind_pose_for_accurate_reimport(self):
        """cutKey 後にジョイントが rest position に戻り、後続の bind pose 記録が正確になる。"""
        joint = cmds.joint(name="clear_restore_bind_joint")
        cmds.setAttr(f"{joint}.translateX", 3.0)
        rest_tx = cmds.getAttr(f"{joint}.translateX")

        self.converter.bone_name_mapping = {"test_bone": joint}
        logger_mock = MagicMock()
        # Keep shared mapping state; only swap logger for level assertions.
        context = VmdImportStateContext(
            logger=logger_mock,
            bone_name_mapping=self.converter.bone_name_mapping,
            bone_bind_poses=self.converter._bone_bind_poses,
            morph_name_mapping=self.converter.morph_name_mapping,
            collect_append_info=lambda: {},
            iter_morph_mappings=self.converter._iter_morph_mappings,
            set_refresh_suspended=self.converter._set_vmd_import_refresh_suspended,
        )
        record_bind_poses(context)

        debug_msgs = [call[0][0] for call in logger_mock.debug.call_args_list if call[0]]
        info_msgs = [call[0][0] for call in logger_mock.info.call_args_list if call[0]]
        self.assertIn("Recording initial bone positions", debug_msgs)
        self.assertNotIn("Recording initial bone positions", info_msgs)

        cmds.setKeyframe(joint, attribute="translateX", time=1, value=rest_tx + 5.0)
        cmds.setKeyframe(joint, attribute="rotateX", time=1, value=45.0)
        cmds.currentTime(1, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateX"), rest_tx + 5.0, places=4)

        self.converter.morph_name_mapping = {}
        layer = cmds.animLayer("VMD_Motion_restore_test", override=False, weight=1.0)
        clear_existing_motion(self._import_state_context(), layer, target_namespace=None)

        self.assertAlmostEqual(cmds.getAttr(f"{joint}.rotateX"), 0.0, places=4)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateX"), rest_tx, places=4)

    def test_clear_existing_motion_does_not_clear_camera_or_light_keys(self):
        """clear_existing_motion はモデル motion だけを対象にし、camera/light key は残す。"""
        camera = self.converter._get_or_create_camera()
        camera_shape = cmds.listRelatives(camera, shapes=True, type="camera")[0]
        light = self.converter._get_or_create_light()
        light_shape = cmds.listRelatives(light, shapes=True, type="directionalLight")[0]

        cmds.setKeyframe(camera, attribute="translateX", time=2, value=4.0)
        cmds.setKeyframe(camera_shape, attribute="focalLength", time=2, value=35.0)
        cmds.setKeyframe(light, attribute="rotateX", time=2, value=10.0)
        cmds.setKeyframe(light_shape, attribute="colorR", time=2, value=0.25)

        self.converter.bone_name_mapping = {}
        self.converter.morph_name_mapping = {}
        layer = cmds.animLayer("VMD_Motion_model_only_clear_test", override=False, weight=1.0)

        clear_existing_motion(self._import_state_context(), layer, target_namespace=None)

        self.assertEqual(cmds.keyframe(camera, attribute="translateX", query=True, timeChange=True), [2.0])
        self.assertEqual(cmds.keyframe(camera_shape, attribute="focalLength", query=True, timeChange=True), [2.0])
        self.assertEqual(cmds.keyframe(light, attribute="rotateX", query=True, timeChange=True), [2.0])
        self.assertEqual(cmds.keyframe(light_shape, attribute="colorR", query=True, timeChange=True), [2.0])
        self.assertFalse(cmds.objExists(layer))

    def test_clear_existing_motion_scopes_namespace_less_append_and_ik_to_target_root(self):
        """Replacing model B motion leaves same-kind append/IK keys on model A intact."""
        root_a = cmds.group(empty=True, name="clear_model_a_root")
        root_b = cmds.group(empty=True, name="clear_model_b_root")
        cmds.select(clear=True)
        joint_a = cmds.joint(name="clear_model_a_joint")
        cmds.parent(joint_a, root_a)
        cmds.select(clear=True)
        joint_b = cmds.joint(name="clear_model_b_joint")
        cmds.parent(joint_b, root_b)

        append_a = cmds.createNode("transform", name="clear_model_a_append")
        append_b = cmds.createNode("transform", name="clear_model_b_append")
        for append_node, joint in ((append_a, joint_a), (append_b, joint_b)):
            for attribute in ("baseTranslateX", "baseRotateX"):
                cmds.addAttr(append_node, longName=attribute, attributeType="double", keyable=True)
            cmds.addAttr(append_node, longName="mmd_owner_joint", attributeType="message")
            cmds.addAttr(append_node, longName="mmd_model_root", attributeType="message")
            cmds.connectAttr(f"{joint}.message", f"{append_node}.mmd_owner_joint")
            root = root_a if append_node == append_a else root_b
            cmds.connectAttr(f"{root}.message", f"{append_node}.mmd_model_root")
            cmds.setKeyframe(append_node, attribute="baseTranslateX", time=4, value=10.0)
            cmds.setKeyframe(append_node, attribute="baseRotateX", time=7, value=20.0)

        append_attrs = ("baseTranslateX", "baseRotateX")
        append_snapshots = {}
        for append_node, joint, root in (
            (append_a, joint_a, root_a),
            (append_b, joint_b, root_b),
        ):
            append_snapshots[append_node] = {
                "uuid": cmds.ls(append_node, uuid=True)[0],
                "joint_connection": cmds.listConnections(
                    f"{append_node}.mmd_owner_joint",
                    source=True,
                    destination=False,
                    plugs=True,
                ),
                "root_connection": cmds.listConnections(
                    f"{append_node}.mmd_model_root",
                    source=True,
                    destination=False,
                    plugs=True,
                ),
                "curves": {
                    attribute: {
                        "nodes": cmds.listConnections(
                            f"{append_node}.{attribute}",
                            source=True,
                            destination=False,
                            type="animCurve",
                        )
                        or [],
                        "connections": cmds.listConnections(
                            f"{append_node}.{attribute}",
                            source=True,
                            destination=False,
                            plugs=True,
                        )
                        or [],
                        "times": cmds.keyframe(
                            f"{append_node}.{attribute}",
                            query=True,
                            timeChange=True,
                        ),
                        "values": cmds.keyframe(
                            f"{append_node}.{attribute}",
                            query=True,
                            valueChange=True,
                        ),
                    }
                    for attribute in append_attrs
                },
            }
            self.assertEqual(append_snapshots[append_node]["joint_connection"], [f"{joint}.message"])
            self.assertEqual(append_snapshots[append_node]["root_connection"], [f"{root}.message"])
            for attribute in append_attrs:
                curve_nodes = append_snapshots[append_node]["curves"][attribute]["nodes"]
                self.assertEqual(len(curve_nodes), 1)
                append_snapshots[append_node]["curves"][attribute]["uuids"] = [
                    cmds.ls(curve, uuid=True)[0] for curve in curve_nodes
                ]

        ik_a = cmds.createNode("network", name="clear_model_a_ik")
        ik_b = cmds.createNode("network", name="clear_model_b_ik")
        for ik_node, joint in ((ik_a, joint_a), (ik_b, joint_b)):
            cmds.addAttr(ik_node, longName="enabled", attributeType="bool", keyable=True)
            cmds.addAttr(ik_node, longName="inputRotate", attributeType="double", keyable=True)
            cmds.addAttr(ik_node, longName="mmd_owner_joint", attributeType="message")
            cmds.connectAttr(f"{joint}.message", f"{ik_node}.mmd_owner_joint")
            cmds.setKeyframe(ik_node, attribute="enabled", time=6, value=1.0)

        context = VmdImportStateContext(
            logger=self.converter.logger,
            bone_name_mapping={},
            bone_bind_poses=self.converter._bone_bind_poses,
            morph_name_mapping={},
            collect_append_info=lambda: {
                joint_a: {"node": append_a},
                joint_b: {"node": append_b},
            },
            iter_morph_mappings=self.converter._iter_morph_mappings,
            set_refresh_suspended=self.converter._set_vmd_import_refresh_suspended,
        )
        layer = cmds.animLayer("VMD_Motion_root_scoped_clear", override=False, weight=1.0)
        foreign_layer_node = cmds.createNode("transform", name="clear_model_a_layer_member")
        cmds.animLayer(layer, edit=True, attribute=f"{foreign_layer_node}.translateX")
        self.assertTrue(cmds.objExists(append_a))
        self.assertTrue(cmds.objExists(append_b))

        with patch(
            "mmd_tools.converters.vmd_import_state._ls_mmd_ccd_ik_nodes",
            return_value=[ik_a, ik_b],
        ):
            clear_existing_motion(context, layer, target_model=root_b)

        self.assertEqual(cmds.ls(append_a, uuid=True)[0], append_snapshots[append_a]["uuid"])
        self.assertEqual(cmds.ls(append_b, uuid=True)[0], append_snapshots[append_b]["uuid"])
        for attribute in append_attrs:
            target_snapshot = append_snapshots[append_b]["curves"][attribute]
            foreign_snapshot = append_snapshots[append_a]["curves"][attribute]
            self.assertIsNone(cmds.keyframe(f"{append_b}.{attribute}", query=True, timeChange=True))
            self.assertIsNone(cmds.keyframe(f"{append_b}.{attribute}", query=True, valueChange=True))
            self.assertEqual(
                cmds.listConnections(
                    f"{append_b}.{attribute}",
                    source=True,
                    destination=False,
                    type="animCurve",
                )
                or [],
                target_snapshot["nodes"],
            )
            self.assertEqual(
                [cmds.ls(curve, uuid=True)[0] for curve in target_snapshot["nodes"]],
                target_snapshot["uuids"],
            )
            self.assertEqual(
                cmds.listConnections(
                    f"{append_b}.{attribute}",
                    source=True,
                    destination=False,
                    plugs=True,
                )
                or [],
                target_snapshot["connections"],
            )
            self.assertEqual(
                cmds.keyframe(f"{append_a}.{attribute}", query=True, timeChange=True),
                foreign_snapshot["times"],
            )
            self.assertEqual(
                cmds.keyframe(f"{append_a}.{attribute}", query=True, valueChange=True),
                foreign_snapshot["values"],
            )
            self.assertEqual(
                [cmds.ls(curve, uuid=True)[0] for curve in foreign_snapshot["nodes"]],
                foreign_snapshot["uuids"],
            )
            self.assertEqual(
                cmds.listConnections(
                    f"{append_a}.{attribute}",
                    source=True,
                    destination=False,
                    plugs=True,
                )
                or [],
                foreign_snapshot["connections"],
            )
        self.assertEqual(
            cmds.listConnections(
                f"{append_b}.mmd_owner_joint",
                source=True,
                destination=False,
                plugs=True,
            ),
            append_snapshots[append_b]["joint_connection"],
        )
        self.assertEqual(
            cmds.listConnections(
                f"{append_b}.mmd_model_root",
                source=True,
                destination=False,
                plugs=True,
            ),
            append_snapshots[append_b]["root_connection"],
        )
        self.assertEqual(
            cmds.listConnections(
                f"{append_a}.mmd_owner_joint",
                source=True,
                destination=False,
                plugs=True,
            ),
            append_snapshots[append_a]["joint_connection"],
        )
        self.assertEqual(
            cmds.listConnections(
                f"{append_a}.mmd_model_root",
                source=True,
                destination=False,
                plugs=True,
            ),
            append_snapshots[append_a]["root_connection"],
        )
        self.assertEqual(cmds.keyframe(f"{ik_a}.enabled", query=True, timeChange=True), [6.0])
        self.assertIsNone(cmds.keyframe(f"{ik_b}.enabled", query=True))
        self.assertTrue(cmds.objExists(layer))

    def test_clear_existing_motion_clears_layered_curves_in_place(self):
        """Layer/blend-backed curves lose keys without deleting their curve nodes."""
        root = cmds.group(empty=True, name="clear_layered_root")
        cmds.select(clear=True)
        joint = cmds.joint(name="clear_layered_joint")
        cmds.parent(joint, root)
        append = cmds.createNode("transform", name="clear_layered_append")
        cmds.addAttr(append, longName="baseTranslateX", attributeType="double", keyable=True)
        cmds.addAttr(append, longName="mmd_owner_joint", attributeType="message")
        cmds.addAttr(append, longName="mmd_model_root", attributeType="message")
        cmds.connectAttr(f"{joint}.message", f"{append}.mmd_owner_joint")
        cmds.connectAttr(f"{root}.message", f"{append}.mmd_model_root")

        layer = cmds.animLayer("clear_layered_layer", override=False, weight=1.0)
        foreign_layer_node = cmds.createNode("transform", name="clear_layered_foreign")
        cmds.animLayer(layer, edit=True, attribute=f"{foreign_layer_node}.translateX")
        cmds.animLayer(layer, edit=True, attribute=f"{append}.baseTranslateX")
        cmds.setKeyframe(append, attribute="baseTranslateX", time=4, value=10.0, animLayer=layer)
        layer_curves = cmds.animLayer(layer, query=True, animCurves=True) or []
        self.assertTrue(layer_curves)
        curve_uuids = [cmds.ls(curve, uuid=True)[0] for curve in layer_curves]

        context = VmdImportStateContext(
            logger=self.converter.logger,
            bone_name_mapping={},
            bone_bind_poses=self.converter._bone_bind_poses,
            morph_name_mapping={},
            collect_append_info=lambda: {joint: {"node": append}},
            iter_morph_mappings=self.converter._iter_morph_mappings,
            set_refresh_suspended=self.converter._set_vmd_import_refresh_suspended,
        )
        clear_existing_motion(context, "missing_layer", target_model=root)

        self.assertIsNone(
            cmds.keyframe(append, attribute="baseTranslateX", query=True, timeChange=True)
        )
        self.assertTrue(cmds.objExists(layer))
        remaining_curves = cmds.animLayer(layer, query=True, animCurves=True) or []
        self.assertEqual(
            [cmds.ls(curve, uuid=True)[0] for curve in remaining_curves],
            curve_uuids,
        )

    def test_clear_existing_motion_preserves_append_shared_across_model_roots(self):
        """A shared append node is fail-closed even when its reported target is model B."""
        root_a = cmds.group(empty=True, name="shared_append_model_a_root")
        root_b = cmds.group(empty=True, name="shared_append_model_b_root")
        cmds.select(clear=True)
        joint_a = cmds.joint(name="shared_append_model_a_joint")
        cmds.parent(joint_a, root_a)
        cmds.select(clear=True)
        joint_b = cmds.joint(name="shared_append_model_b_joint")
        cmds.parent(joint_b, root_b)
        append_node = cmds.createNode("transform", name="shared_append_node")
        cmds.addAttr(append_node, longName="baseRotateX", attributeType="double", keyable=True)
        cmds.addAttr(append_node, longName="ownerA", attributeType="message")
        cmds.addAttr(append_node, longName="ownerB", attributeType="message")
        cmds.connectAttr(f"{joint_a}.message", f"{append_node}.ownerA")
        cmds.connectAttr(f"{joint_b}.message", f"{append_node}.ownerB")
        cmds.setKeyframe(append_node, attribute="baseRotateX", time=5, value=15.0)
        context = VmdImportStateContext(
            logger=self.converter.logger,
            bone_name_mapping={},
            bone_bind_poses={},
            morph_name_mapping={},
            collect_append_info=lambda: {
                joint_b: {"node": append_node, "source_joint": joint_a}
            },
            iter_morph_mappings=self.converter._iter_morph_mappings,
            set_refresh_suspended=self.converter._set_vmd_import_refresh_suspended,
        )

        clear_existing_motion(context, "missing_layer", target_model=root_b)

        self.assertEqual(cmds.keyframe(f"{append_node}.baseRotateX", query=True, timeChange=True), [5.0])

    def test_convert_clear_existing_motion_replaces_previous_bone_keys(self):
        """clear ON の再 import は古いキーを残さず新しい VMD キーだけにする。"""
        joint = cmds.joint(name="clear_existing_convert_center")
        target_model = cmds.group(empty=True, name="clear_existing_model_root")
        cmds.parent(joint, target_model)
        cmds.addAttr(joint, longName=ATTR_MMD_BONE_NAME, dataType="string")
        cmds.setAttr(f"{joint}.{ATTR_MMD_BONE_NAME}", "センター", type="string")
        cmds.setKeyframe(joint, attribute="translateX", time=1, value=10.0)

        vmd_data = type("VmdDataStub", (), {})()
        vmd_data.bone_frames = [_bone_frame("センター", 8, (2.0, 0.0, 0.0))]
        vmd_data.morph_frames = []
        vmd_data.camera_frames = []
        vmd_data.light_frames = []

        self.converter.use_animation_layers = False
        # The fixture has no paired raw PMX/VMD bytes; bypass only the
        # registered sparse compiler so this test remains focused on clearing
        # and replacing legacy scene keys.
        with patch.object(
            self.converter,
            "_compiled_registered_sparse_frames",
            return_value=(tuple(vmd_data.bone_frames), {}),
        ):
            self.assertTrue(
                self.converter.convert(
                    vmd_data,
                    clear_existing_motion=True,
                    target_model=target_model,
                )
            )

        self.assertNotIn(1.0, cmds.keyframe(joint, attribute="translateX", query=True, timeChange=True) or [])
        self.assertIn(8.0, cmds.keyframe(joint, attribute="translateX", query=True, timeChange=True) or [])
