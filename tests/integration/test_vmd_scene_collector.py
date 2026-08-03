"""Integration tests for VMD export via VmdSceneCollector + VmdExporter."""

import os

from maya import cmds

from mmd_tools.converters.vmd_scene_collector import VmdSceneCollector
from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.actions.export_vmd_action import ExportVmdAction, ExportVmdRequest
from mmd_tools.core.constants import (
    ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON,
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_CAMERA,
    ATTR_MMD_LIGHT,
    ATTR_MMD_MODEL_NAME,
)
from mmd_tools.core.namespace_utils import NamespaceUtils
from mmd_tools.core.vmd_data import VmdData
from mmd_tools.io.mmd_importer import import_mmd_file
from mmd_tools.io.vmd_exporter import VmdExporter
from tests.common.maya_test_base import MayaTestBase
from tests.common.test_fixture_provider import TestFixtureProvider


class TestVmdSceneCollector(MayaTestBase):
    """Round-trip tests: Maya keyed scene -> collect -> export VMD -> parse."""

    def setUp(self):
        super().setUp()
        cmds.file(new=True, force=True)
        self._original_time_unit = cmds.currentUnit(query=True, time=True)
        cmds.currentUnit(time="film")
        self.fixture_provider = TestFixtureProvider()

    def tearDown(self):
        self.fixture_provider.cleanup_temp_files()
        cmds.currentUnit(time=self._original_time_unit)
        super().tearDown()
        cmds.file(new=True, force=True)

    def test_roundtrip_keyed_joint_to_vmd_bone_frames(self):
        root, joint = self._make_keyed_joint_scene()

        maya_data = VmdSceneCollector().collect({"target_model": root})
        output_path = self.get_temp_filename("keyed_joint_export.vmd")
        VmdExporter().export_vmd_animation(output_path, maya_data)

        self.assertTrue(os.path.exists(output_path), "VMD file was not written")
        parsed = VmdData().parse_file(output_path)

        self.assertEqual(parsed.header.model_name, "ExportModel")
        self.assertEqual(len(parsed.bone_frames), 2)
        self.assertEqual(parsed.bone_frames[0].bone_name, "センター")
        self.assertEqual(parsed.bone_frames[0].frame_number, 0)
        self.assertEqual(parsed.bone_frames[1].bone_name, "センター")
        # VMD is fixed at 30fps, so Maya film frame 10 becomes VMD frame 12.
        self.assertEqual(parsed.bone_frames[1].frame_number, 12)
        self.assertEqual(parsed.bone_frames[1].position, (5.0, 1.0, -2.0))

    def test_roundtrip_keyed_joint_uses_bind_relative_scaled_vmd_offset(self):
        root, _joint = self._make_keyed_joint_scene(bind_pose=(3.0, 4.0, 5.0), keyed_pose=(5.0, 8.0, -1.0))

        maya_data = VmdSceneCollector().collect(
            {
                "target_model": root,
                "motion_scale": 2.0,
                "bone_bind_poses": {"センター": (3.0, 4.0, 5.0)},
            }
        )
        output_path = self.get_temp_filename("bind_relative_joint_export.vmd")
        VmdExporter().export_vmd_animation(output_path, maya_data)

        parsed = VmdData().parse_file(output_path)

        self.assertEqual(len(parsed.bone_frames), 2)
        self.assertEqual(parsed.bone_frames[1].position, (1.0, 2.0, 3.0))

    def test_roundtrip_joint_orient_rotation_imports_back_to_original_rotate(self):
        root, joint = self._make_keyed_joint_scene()
        cmds.setAttr(f"{joint}.jointOrient", 0.0, 45.0, 0.0)
        cmds.setAttr(f"{joint}.rotateOrder", 0)
        cmds.setKeyframe(joint, attribute="rotateX", time=10, value=90.0)
        cmds.setKeyframe(joint, attribute="rotateZ", time=10, value=0.0)

        maya_data = VmdSceneCollector().collect({"target_model": root})
        output_path = self.get_temp_filename("joint_orient_rotation_export.vmd")
        VmdExporter().export_vmd_animation(output_path, maya_data)
        parsed = VmdData().parse_file(output_path)
        frame = parsed.bone_frames[1]

        converter = VmdConverter()
        rx, ry, rz = converter._convert_vmd_quat_to_joint_rotate(joint, *frame.rotation)

        self.assertAlmostEqual(rx, 90.0, places=5)
        self.assertAlmostEqual(ry, 0.0, places=5)
        self.assertAlmostEqual(rz, 0.0, places=5)

    def test_export_vmd_action_uses_default_scene_collector(self):
        root, _joint = self._make_keyed_joint_scene()
        output_path = self.get_temp_filename("action_keyed_joint_export.vmd")

        result = ExportVmdAction().execute(
            ExportVmdRequest(
                file_path=output_path,
                options={"target_model": root, "export_format": "vmd"},
            )
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(result.exported_path, output_path)
        parsed = VmdData().parse_file(output_path)
        self.assertEqual(parsed.header.model_name, "ExportModel")
        self.assertEqual(len(parsed.bone_frames), 2)

    def test_roundtrip_imported_fixture_motion_exports_parseable_vmd(self):
        pmx_path = self.fixture_provider.get_pmx_file("mmt_test_model")
        vmd_path = self.fixture_provider.get_vmd_file("mmt_test_model_test_motion")

        root = import_mmd_file(
            pmx_path,
            options={"setup_rig": True, "setup_bone_orientation": True},
        )
        self.assertIsNotNone(root, "PMX import failed")
        self.assertTrue(
            import_mmd_file(vmd_path, options={"target_model": root, "pmx_path": pmx_path}),
            "VMD import failed",
        )

        maya_data = VmdSceneCollector().collect({"target_model": root})
        output_path = self.get_temp_filename("imported_fixture_export.vmd")
        VmdExporter().export_vmd_animation(output_path, maya_data)

        parsed = VmdData().parse_file(output_path)
        self.assertTrue(parsed.header.model_name)
        self.assertGreater(len(parsed.bone_frames), 0)
        self.assertTrue(any(frame.bone_name == "センター" for frame in parsed.bone_frames))

    def test_roundtrip_tagged_camera_and_light_to_vmd_frames(self):
        camera = self._make_keyed_camera()
        light = self._make_keyed_light()

        maya_data = VmdSceneCollector().collect({"cameras": [camera], "lights": [light]})
        output_path = self.get_temp_filename("camera_light_export.vmd")
        VmdExporter().export_vmd_animation(output_path, maya_data)

        parsed = VmdData().parse_file(output_path)

        self.assertEqual(len(parsed.camera_frames), 1)
        camera_frame = parsed.camera_frames[0]
        self.assertEqual(camera_frame.frame_number, 15)
        self.assertEqual(camera_frame.position, (1.0, 2.0, 3.0))
        self.assertAlmostEqual(camera_frame.rotation[2], 0.5235987901687622)
        self.assertEqual(camera_frame.distance, -45.0)
        self.assertEqual(camera_frame.viewing_angle, 42)
        self.assertEqual(camera_frame.perspective, 1)

        self.assertEqual(len(parsed.light_frames), 1)
        light_frame = parsed.light_frames[0]
        self.assertEqual(light_frame.frame_number, 10)
        self.assertAlmostEqual(light_frame.color[0], 0.1)
        self.assertAlmostEqual(light_frame.color[1], 0.2)
        self.assertAlmostEqual(light_frame.color[2], 0.3)
        self.assertAlmostEqual(light_frame.position[0], -1.0)
        self.assertAlmostEqual(light_frame.position[1], 0.0)
        self.assertAlmostEqual(light_frame.position[2], 0.0)

    def test_namespaced_target_scopes_automatic_blendshape_discovery(self):
        hero_root = self._make_namespaced_keyed_blendshape("hero", "hero_morph")
        self._make_namespaced_keyed_blendshape("rival", "rival_morph")

        maya_data = VmdSceneCollector().collect({"target_model": hero_root})

        self.assertEqual(
            {frame["morph_name"] for frame in maya_data["morph_frames"]},
            {"hero_morph"},
        )

    def _make_namespaced_keyed_blendshape(self, namespace, morph_name):
        with NamespaceUtils.namespace_context(namespace):
            root = cmds.group(empty=True, name="model_ROOT")
            base, _base_shape = cmds.polyCube(name="Geometry")
            target, _target_shape = cmds.polyCube(name="GeometryTarget")
            cmds.parent(base, root)
            cmds.parent(target, root)
            blend_shape = cmds.blendShape(target, base, name="faceBlendShape")[0]
            cmds.addAttr(
                blend_shape,
                longName=ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON,
                dataType="string",
            )
            cmds.setAttr(
                f"{blend_shape}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}",
                '{"0": "' + morph_name + '"}',
                type="string",
            )
            cmds.setKeyframe(blend_shape, attribute="weight[0]", time=5, value=0.5)
            return (cmds.ls(root, long=True) or [root])[0]

    def _make_keyed_joint_scene(self, bind_pose=(0.0, 0.0, 0.0), keyed_pose=(5.0, 1.0, 2.0)):
        root = cmds.group(empty=True, name="model_root")
        cmds.addAttr(root, longName=ATTR_MMD_MODEL_NAME, dataType="string")
        cmds.setAttr(f"{root}.{ATTR_MMD_MODEL_NAME}", "ExportModel", type="string")

        cmds.select(clear=True)
        joint = cmds.joint(name="center_joint")
        cmds.parent(joint, root)
        cmds.addAttr(joint, longName=ATTR_MMD_BONE_NAME, dataType="string")
        cmds.setAttr(f"{joint}.{ATTR_MMD_BONE_NAME}", "センター", type="string")

        cmds.setKeyframe(joint, attribute="translateX", time=0, value=bind_pose[0])
        cmds.setKeyframe(joint, attribute="translateX", time=10, value=keyed_pose[0])
        cmds.setKeyframe(joint, attribute="translateY", time=0, value=bind_pose[1])
        cmds.setKeyframe(joint, attribute="translateY", time=10, value=keyed_pose[1])
        cmds.setKeyframe(joint, attribute="translateZ", time=0, value=bind_pose[2])
        cmds.setKeyframe(joint, attribute="translateZ", time=10, value=keyed_pose[2])
        cmds.setKeyframe(joint, attribute="rotateZ", time=0, value=0.0)
        cmds.setKeyframe(joint, attribute="rotateZ", time=10, value=90.0)
        return root, joint

    def _make_keyed_camera(self):
        camera, _shape = cmds.camera(name="mmd_camera")
        cmds.addAttr(camera, longName=ATTR_MMD_CAMERA, attributeType="bool")
        cmds.setAttr(f"{camera}.{ATTR_MMD_CAMERA}", True)
        cmds.addAttr(camera, longName="mmd_camera_distance", attributeType="double", keyable=True)
        cmds.addAttr(camera, longName="mmd_camera_viewing_angle", attributeType="double", keyable=True)
        cmds.addAttr(camera, longName="mmd_camera_perspective", attributeType="long", keyable=True)

        cmds.setKeyframe(camera, attribute="translateX", time=12, value=1.0)
        cmds.setKeyframe(camera, attribute="translateY", time=12, value=2.0)
        cmds.setKeyframe(camera, attribute="translateZ", time=12, value=-3.0)
        cmds.setKeyframe(camera, attribute="rotateX", time=12, value=10.0)
        cmds.setKeyframe(camera, attribute="rotateY", time=12, value=20.0)
        cmds.setKeyframe(camera, attribute="rotateZ", time=12, value=-30.0)
        cmds.setKeyframe(camera, attribute="mmd_camera_distance", time=12, value=-45.0)
        cmds.setKeyframe(camera, attribute="mmd_camera_viewing_angle", time=12, value=42.0)
        cmds.setKeyframe(camera, attribute="mmd_camera_perspective", time=12, value=1.0)
        return camera

    def _make_keyed_light(self):
        light = cmds.group(empty=True, name="mmd_light")
        cmds.addAttr(light, longName=ATTR_MMD_LIGHT, attributeType="bool")
        cmds.setAttr(f"{light}.{ATTR_MMD_LIGHT}", True)
        cmds.addAttr(light, longName="mmd_light_color", usedAsColor=True, attributeType="float3")
        cmds.addAttr(light, longName="mmd_light_colorR", attributeType="float", parent="mmd_light_color", keyable=True)
        cmds.addAttr(light, longName="mmd_light_colorG", attributeType="float", parent="mmd_light_color", keyable=True)
        cmds.addAttr(light, longName="mmd_light_colorB", attributeType="float", parent="mmd_light_color", keyable=True)
        cmds.setKeyframe(light, attribute="mmd_light_colorR", time=8, value=0.1)
        cmds.setKeyframe(light, attribute="mmd_light_colorG", time=8, value=0.2)
        cmds.setKeyframe(light, attribute="mmd_light_colorB", time=8, value=0.3)
        cmds.setKeyframe(light, attribute="rotateX", time=8, value=0.0)
        cmds.setKeyframe(light, attribute="rotateY", time=8, value=90.0)
        cmds.setKeyframe(light, attribute="rotateZ", time=8, value=0.0)
        return light
