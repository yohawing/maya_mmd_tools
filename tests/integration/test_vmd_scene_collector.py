"""Integration tests for VMD export via VmdSceneCollector + VmdExporter."""

import math
import os
from pathlib import Path

from maya import cmds

from mmd_tools.converters.vmd_scene_collector import VmdSceneCollector
from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.adapters.maya_vmd_prepare_backend import create_maya_bake_timeline_vmd_action
from mmd_tools.services.export_workflow_service import ExportWorkflowRequest
from mmd_tools.core.constants import (
    ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON,
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_CAMERA,
    ATTR_MMD_LIGHT,
    ATTR_MMD_MODEL_NAME,
)
from mmd_tools.core.namespace_utils import NamespaceUtils
from mmd_tools.core.native.mmd_anim_runtime_sampling import sample_vmd_camera_frames
from mmd_tools.core.vmd_data import VmdData
from mmd_tools.io.mmd_importer import import_mmd_file
from mmd_tools.io.vmd_exporter import VmdExporter
from tests.common.maya_test_base import MayaTestBase
from tests.common.test_fixture_provider import TestFixtureProvider


class _RecordingVmdExporter:
    """Delegate VMD writing while recording whether the writer was reached."""

    def __init__(self):
        self.calls = []
        self._delegate = VmdExporter()

    def to_vmd_data(self, animation_data):
        """Keep the action's normal data normalization contract."""
        return self._delegate.to_vmd_data(animation_data)

    def export_vmd_animation(self, file_path, animation_data):
        """Record writer entry before delegating to the real Python writer."""
        self.calls.append((file_path, animation_data))
        return self._delegate.export_vmd_animation(file_path, animation_data)


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

    def test_one_shot_bake_timeline_export_uses_streamed_payload(self):
        root, _joint = self._make_keyed_joint_scene()
        output_path = self.get_temp_filename("action_keyed_joint_export.vmd")

        result = self._export_bake_timeline(
            output_path,
            {
                "target_model": root,
                "current_model_root": root,
                "export_format": "vmd",
                "export_strategy": "bake_timeline",
                "frame_range": (0, 10),
            },
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(
            os.path.normpath(result.exported_path),
            os.path.normpath(output_path),
        )
        parsed = VmdData().parse_file(output_path)
        self.assertEqual(parsed.header.model_name, "ExportModel")
        self.assertEqual(len(parsed.bone_frames), 11)
        sampled_frames = sorted({frame.frame_number for frame in parsed.bone_frames})
        self.assertEqual(len(sampled_frames), 11)
        self.assertEqual(sampled_frames[-1], 12)

    def test_bake_timeline_frame_range_matches_maya_numeric_oracle(self):
        cmds.currentUnit(time="ntsc")
        root, joint = self._make_keyed_joint_scene(
            keyed_pose=(2.0, 1.0, 2.0),
            keyed_frame=2,
        )
        blend_shape = self._make_keyed_blendshape(root)
        camera = self._make_keyed_camera()
        light = self._make_keyed_light()
        output_path = self.get_temp_filename("bake_timeline_numeric_oracle.vmd")
        cmds.currentTime(7, edit=True)

        result = self._export_bake_timeline(
            output_path,
            {
                "target_model": root,
                "current_model_root": root,
                "export_format": "vmd",
                "export_strategy": "bake_timeline",
                "frame_range": (0, 2),
                "blend_shapes": [blend_shape],
                "cameras": [camera],
                "lights": [light],
            },
        )

        self.assertTrue(result.succeeded, result.error)
        self.assertEqual(float(cmds.currentTime(query=True)), 7.0)
        parsed = VmdData().parse_file(output_path)
        self.assertEqual(
            [frame.frame_number for frame in parsed.bone_frames],
            [0, 1, 2],
        )
        for frame in parsed.bone_frames:
            cmds.currentTime(frame.frame_number, edit=True)
            expected_x = float(cmds.getAttr(f"{joint}.translateX"))
            expected_y = float(cmds.getAttr(f"{joint}.translateY"))
            expected_z = float(cmds.getAttr(f"{joint}.translateZ"))
            expected_angle = math.radians(float(cmds.getAttr(f"{joint}.rotateZ")))
            expected_rotation = (
                0.0,
                0.0,
                math.sin(expected_angle / 2.0),
                math.cos(expected_angle / 2.0),
            )
            rotation_dot = abs(
                sum(actual * expected for actual, expected in zip(frame.rotation, expected_rotation))
            )
            self.assertAlmostEqual(frame.position[0], expected_x, places=5)
            self.assertAlmostEqual(frame.position[1], expected_y, places=5)
            self.assertAlmostEqual(frame.position[2], -expected_z, places=5)
            self.assertAlmostEqual(rotation_dot, 1.0, places=5)
        self.assertEqual(
            [frame.frame_number for frame in parsed.morph_frames],
            [0, 1, 2],
        )
        self.assertEqual(
            [frame.frame_number for frame in parsed.camera_frames],
            [],
        )
        self.assertEqual(
            [frame.frame_number for frame in parsed.light_frames],
            [],
        )

    def test_bake_timeline_imported_fixture_fresh_import_matches_exported_bone_payload(self):
        self._assert_bake_timeline_fresh_import_bone_payload(
            "mmt_test_model",
            "mmt_test_model_test_motion",
            "bake_timeline_mmt_fixture_export.vmd",
        )

    def test_bake_timeline_one_bone_fixture_fresh_import_matches_exported_bone_payload(self):
        self._assert_bake_timeline_fresh_import_bone_payload(
            "test_1bone_cube",
            "test_1bone_cube_motion",
            "bake_timeline_one_bone_fixture_export.vmd",
        )

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

    def test_bake_timeline_exports_selected_ordinary_camera_dense(self):
        camera, shape = cmds.camera(name="render_camera")
        cmds.setAttr(f"{camera}.translateY", 10.0)
        cmds.setAttr(f"{camera}.rotateX", -20.0)
        cmds.setAttr(f"{shape}.centerOfInterest", 30.0)
        cmds.setKeyframe(camera, attribute="rotateY", time=1, value=0.0)
        cmds.setKeyframe(camera, attribute="rotateY", time=3, value=20.0)
        cmds.select(camera, replace=True)
        output_path = self.get_temp_filename("selected_camera_dense.vmd")

        result = self._export_bake_timeline(
            output_path,
            {
                "current_model_root": None,
                "require_target": False,
                "require_current_model": False,
                "export_format": "vmd",
                "export_strategy": "bake_timeline",
                "export_target": "camera",
                "frame_range": (1, 3),
            },
        )

        self.assertTrue(result.succeeded, repr(result))
        parsed = VmdData().parse_file(output_path)
        self.assertEqual(parsed.header.model_name, "カメラ・照明")
        self.assertEqual(
            [frame.frame_number for frame in parsed.camera_frames],
            # Maya film (24 fps) samples 1/2/3 map to VMD 1/2/4 at 30 fps.
            [1, 2, 4],
        )
        self.assertTrue(
            all(0.0 < abs(frame.distance) <= 1000.0 for frame in parsed.camera_frames)
        )
        self.assertTrue(
            all(abs(frame.position[1]) <= 1e-5 for frame in parsed.camera_frames)
        )
        self.assertEqual(parsed.bone_frames, [])

    def test_preserve_keys_exports_sparse_mmd_camera_rig_with_interpolation(self):
        from mmd_tools.core.vmd_data.camera_frame import VmdCameraFrame

        source_frames = []
        expected_interpolation = []
        for frame_number, seed in ((0, 7), (30, 61)):
            frame = VmdCameraFrame()
            frame.frame_number = frame_number
            frame.position = (frame_number / 10.0, 4.0, -2.0)
            frame.rotation = (0.1, 0.2, 0.3)
            frame.distance = -30.0
            frame.viewing_angle = 50
            frame.perspective = 0
            frame.interpolation = bytes((seed + index) % 128 for index in range(24))
            source_frames.append(frame)
            expected_interpolation.append(frame.interpolation)
        self.assertTrue(VmdConverter()._convert_camera_animation(source_frames))

        output_path = self.get_temp_filename("camera_preserve_keys.vmd")
        result = self._export_bake_timeline(
            output_path,
            {
                "current_model_root": None,
                "require_target": False,
                "require_current_model": False,
                "export_format": "vmd",
                "export_strategy": "preserve_keys",
                "export_target": "camera",
            },
        )

        self.assertTrue(result.succeeded, repr(result))
        parsed = VmdData().parse_file(output_path)
        self.assertEqual(parsed.header.model_name, "カメラ・照明")
        self.assertEqual(len(parsed.camera_frames), 2)
        self.assertEqual(
            [frame.interpolation for frame in parsed.camera_frames],
            expected_interpolation,
        )
        self.assertEqual(parsed.bone_frames, [])

        cmds.file(new=True, force=True)
        cmds.currentUnit(time="film")
        self.assertTrue(import_mmd_file(output_path))
        tagged = cmds.ls(f"*.{ATTR_MMD_CAMERA}", objectsOnly=True) or []
        self.assertEqual(len(tagged), 1)
        targets = cmds.listConnections(
            f"{tagged[0]}.mmd_camera_target_node",
            source=True,
            destination=False,
        ) or []
        self.assertEqual(len(targets), 1)
        self.assertEqual(
            len(cmds.keyframe(f"{targets[0]}.translateX", query=True) or []),
            2,
        )
        self.assertTrue(
            cmds.attributeQuery(
                "mmd_camera_interpolation_json",
                node=tagged[0],
                exists=True,
            )
        )

    def test_preserve_keys_import_export_keeps_mmd_anim_camera_interpolation(self):
        source_path = (
            Path(__file__).resolve().parents[1]
            / "data"
            / "camera_motion"
            / "camera-edge-generated.vmd"
        )
        source_bytes = source_path.read_bytes()
        source_samples = sample_vmd_camera_frames(
            source_bytes,
            start_frame=0.0,
            frame_step=1.0,
            frame_count=61,
        )
        if source_samples is None:
            self.skipTest("mmd-anim camera track sampling is unavailable")
        source_data = VmdData().parse_file(str(source_path))

        self.assertTrue(import_mmd_file(str(source_path)))
        output_path = self.get_temp_filename("camera_edge_preserve_keys.vmd")
        result = self._export_bake_timeline(
            output_path,
            {
                "current_model_root": None,
                "require_target": False,
                "require_current_model": False,
                "export_format": "vmd",
                "export_strategy": "preserve_keys",
                "export_target": "camera",
            },
        )

        self.assertTrue(result.succeeded, repr(result))
        exported_data = VmdData().parse_file(output_path)
        self.assertEqual(
            [frame.frame_number for frame in exported_data.camera_frames],
            [frame.frame_number for frame in source_data.camera_frames],
        )
        self.assertEqual(
            [frame.interpolation for frame in exported_data.camera_frames],
            [frame.interpolation for frame in source_data.camera_frames],
        )

        exported_samples = sample_vmd_camera_frames(
            Path(output_path).read_bytes(),
            start_frame=0.0,
            frame_step=1.0,
            frame_count=61,
        )
        self.assertIsNotNone(exported_samples, "mmd-anim could not sample the exported VMD")
        self.assertEqual(len(exported_samples), len(source_samples))

        for source, exported in zip(source_samples, exported_samples):
            frame = source["frame"]
            self.assertEqual(exported["frame"], frame)
            self.assertAlmostEqual(exported["distance"], source["distance"], places=4)
            self.assertAlmostEqual(exported["fov"], source["fov"], places=4)
            self.assertEqual(exported["perspective"], source["perspective"])
            for channel, actual, expected in zip(
                "XYZ",
                exported["position"],
                source["position"],
            ):
                self.assertAlmostEqual(
                    actual,
                    expected,
                    places=4,
                    msg=f"position{channel} mismatch at VMD frame {frame}",
                )
            for channel, actual, expected in zip(
                "XYZ",
                exported["rotation"],
                source["rotation"],
            ):
                angle_delta = (actual - expected + math.pi) % (2.0 * math.pi) - math.pi
                self.assertAlmostEqual(
                    angle_delta,
                    0.0,
                    places=4,
                    msg=f"rotation{channel} mismatch at VMD frame {frame}",
                )

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

    def _assert_bake_timeline_fresh_import_bone_payload(
        self,
        pmx_fixture_name,
        vmd_fixture_name,
        output_file_name,
    ):
        """Export without exact-run reduction, then verify fresh-import parity."""
        frame_range = (0, 2)
        expected_frame_numbers = list(range(frame_range[0], frame_range[1] + 1))
        pmx_path = self.fixture_provider.get_pmx_file(pmx_fixture_name)
        source_vmd_path = self.fixture_provider.get_vmd_file(vmd_fixture_name)

        source_root = import_mmd_file(
            pmx_path,
            options={"setup_rig": True, "setup_bone_orientation": True},
        )
        self.assertIsNotNone(source_root, f"PMX import failed: {pmx_fixture_name}")
        self.assertTrue(
            import_mmd_file(
                source_vmd_path,
                options={"target_model": source_root, "pmx_path": pmx_path},
            ),
            f"VMD import failed: {vmd_fixture_name}",
        )

        output_path = self.get_temp_filename(output_file_name)
        result = self._export_bake_timeline(
            output_path,
            {
                "target_model": source_root,
                "current_model_root": source_root,
                "export_format": "vmd",
                "export_strategy": "bake_timeline",
                "frame_range": frame_range,
                # Bake Timeline deliberately discards imported raw bone keys;
                # this integration fixture explicitly accepts that loss.
                "ack_warnings": True,
                "bake_timeline_exact_run_reduction": False,
            },
        )

        self.assertTrue(result.succeeded, result.error)
        exported = VmdData().parse_file(output_path)
        self.assertGreater(len(exported.bone_frames), 0, "Bake Timeline export has no bone frames")
        self.assertEqual(
            sorted({frame.frame_number for frame in exported.bone_frames}),
            expected_frame_numbers,
        )
        exported_by_key = {
            (frame.bone_name, frame.frame_number): (frame.position, frame.rotation)
            for frame in exported.bone_frames
        }
        self.assertEqual(len(exported_by_key), len(exported.bone_frames))
        # Static and single-key tracks remain intentionally sparse even when
        # Bake Timeline exact-run reduction is disabled. Fresh-import parity
        # below remains the authority for every emitted bone payload.

        cmds.file(new=True, force=True)
        cmds.currentUnit(time="ntsc")
        fresh_root = import_mmd_file(
            pmx_path,
            options={"setup_rig": True, "setup_bone_orientation": True},
        )
        self.assertIsNotNone(fresh_root, f"Fresh PMX import failed: {pmx_fixture_name}")
        self.assertTrue(
            import_mmd_file(
                output_path,
                options={"target_model": fresh_root, "pmx_path": pmx_path},
            ),
            "Fresh exported VMD import failed",
        )

        collected = self._export_bake_timeline_payload(
            output_path,
            {
                "target_model": fresh_root,
                "current_model_root": fresh_root,
                "export_strategy": "bake_timeline",
                "frame_range": frame_range,
                "bake_timeline_exact_run_reduction": False,
            },
        )
        collected_by_key = {
            (frame.bone_name, frame.frame_number): (
                frame.position,
                frame.rotation,
            )
            for frame in collected.bone_frames
        }
        self.assertEqual(set(collected_by_key), set(exported_by_key))
        for key in sorted(exported_by_key):
            expected_position, expected_rotation = exported_by_key[key]
            actual_position, actual_rotation = collected_by_key[key]
            self.assertListAlmostEqual(actual_position, expected_position, places=5)
            self.assertAlmostEqual(
                abs(sum(actual * expected for actual, expected in zip(actual_rotation, expected_rotation))),
                1.0,
                places=5,
                msg=f"Quaternion mismatch for {key}",
            )

    def _export_bake_timeline(self, output_path, options):
        """Run the production Bake Timeline export in one operation."""
        request = ExportWorkflowRequest(file_path=output_path, options=dict(options))
        return create_maya_bake_timeline_vmd_action().execute_one_shot(
            request, acknowledge_warnings=True
        )

    def _export_bake_timeline_payload(self, output_path, options):
        """Parse the final one-shot output after direct atomic publication."""
        request = ExportWorkflowRequest(file_path=output_path, options=dict(options))
        result = create_maya_bake_timeline_vmd_action().execute_one_shot(
            request, acknowledge_warnings=True
        )
        self.assertTrue(result.succeeded, result.error)
        return VmdData().parse_file(output_path)

    def _make_keyed_blendshape(self, root):
        base, _base_shape = cmds.polyCube(name="bake_timeline_base")
        target, _target_shape = cmds.polyCube(name="bake_timeline_target")
        cmds.parent(base, root)
        cmds.parent(target, root)
        blend_shape = cmds.blendShape(target, base, name="bakeTimelineBlendShape")[0]
        cmds.addAttr(
            blend_shape,
            longName=ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON,
            dataType="string",
        )
        cmds.setAttr(
            f"{blend_shape}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}",
            '{"0": "笑い"}',
            type="string",
        )
        cmds.setKeyframe(blend_shape, attribute="weight[0]", time=0, value=0.0)
        cmds.setKeyframe(blend_shape, attribute="weight[0]", time=2, value=1.0)
        return blend_shape

    def _make_keyed_joint_scene(
        self,
        bind_pose=(0.0, 0.0, 0.0),
        keyed_pose=(5.0, 1.0, 2.0),
        keyed_frame=10,
    ):
        root = cmds.group(empty=True, name="model_root")
        cmds.addAttr(root, longName=ATTR_MMD_MODEL_NAME, dataType="string")
        cmds.setAttr(f"{root}.{ATTR_MMD_MODEL_NAME}", "ExportModel", type="string")

        cmds.select(clear=True)
        joint = cmds.joint(name="center_joint")
        cmds.parent(joint, root)
        cmds.addAttr(joint, longName=ATTR_MMD_BONE_NAME, dataType="string")
        cmds.setAttr(f"{joint}.{ATTR_MMD_BONE_NAME}", "センター", type="string")

        cmds.setKeyframe(joint, attribute="translateX", time=0, value=bind_pose[0])
        cmds.setKeyframe(joint, attribute="translateX", time=keyed_frame, value=keyed_pose[0])
        cmds.setKeyframe(joint, attribute="translateY", time=0, value=bind_pose[1])
        cmds.setKeyframe(joint, attribute="translateY", time=keyed_frame, value=keyed_pose[1])
        cmds.setKeyframe(joint, attribute="translateZ", time=0, value=bind_pose[2])
        cmds.setKeyframe(joint, attribute="translateZ", time=keyed_frame, value=keyed_pose[2])
        cmds.setKeyframe(joint, attribute="rotateZ", time=0, value=0.0)
        cmds.setKeyframe(joint, attribute="rotateZ", time=keyed_frame, value=90.0)
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
