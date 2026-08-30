"""CR061-03 late-failure rollback coverage for mixed VMD scene channels."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from maya import cmds

from mmd_tools.converters.vmd_camera_animation import get_or_create_camera
from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.converters.vmd_light_animation import get_or_create_light
from mmd_tools.converters.vmd_import_state import clear_existing_motion
from mmd_tools.converters.vmd_rotation_time_curve import (
    rotation_time_curve_interpolation_by_bone,
)
from mmd_tools.core.vmd_data import VmdData
from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame
from mmd_tools.core import mmd_control_rig_builder
from mmd_tools.io.mmd_importer import import_mmd_file
from tests.common.maya_test_base import MayaTestBase


_TEST_DATA = Path(__file__).resolve().parents[1] / "data"
_PMX_PATH = str(_TEST_DATA / "mmt_test_model.pmx")


def _synthetic_motion(*frames):
    """Build a deterministic model-only VMD payload for converter gates."""
    data = VmdData()
    for bone_name, frame_number, position in frames:
        frame = VmdBoneFrame()
        frame.bone_name = bone_name
        frame.frame_number = int(frame_number)
        frame.position = tuple(float(value) for value in position)
        frame.rotation = (0.0, 0.0, 0.0, 1.0)
        data.bone_frames.append(frame)
    return data


def _registered_convert(converter, motion, **kwargs):
    """Run synthetic motion through the production model-paired clip path."""
    temp_path = None
    try:
        compiled_source = VmdData()
        compiled_source.header.model_name = motion.header.model_name
        compiled_source.bone_frames = list(motion.bone_frames)
        with tempfile.NamedTemporaryFile(suffix=".vmd", delete=False) as temp_file:
            temp_path = Path(temp_file.name)
        compiled_source.write_file(str(temp_path))
        return converter.convert(
            motion,
            vmd_bytes=temp_path.read_bytes(),
            pmx_bytes=Path(_PMX_PATH).read_bytes(),
            pmx_path=_PMX_PATH,
            **kwargs,
        )
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _find_mmd_joint(root, bone_name):
    """Resolve an imported PMX joint by its MMD bone-name attribute."""
    for node in cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []:
        if cmds.attributeQuery("mmd_bone_name", node=node, exists=True):
            if cmds.getAttr(f"{node}.mmd_bone_name") == bone_name:
                return node
    raise AssertionError(f"MMD bone not found: {bone_name}")


def _control_for_role(root, role):
    """Resolve a UUID-backed control from the root metadata."""
    metadata = json.loads(cmds.getAttr(f"{root}.mmd_control_rig_json"))
    control_uuid = metadata["controls"][role]
    controls = cmds.ls(control_uuid, long=True) or []
    if not controls:
        raise AssertionError(f"Control UUID not found: {role} / {control_uuid}")
    return controls[0], metadata


def _curve_state(plug):
    """Capture key payload and animCurve identity without mutating Maya."""
    curve_plugs = cmds.listConnections(
        plug,
        source=True,
        destination=False,
        type="animCurve",
        plugs=True,
    ) or []
    curve = curve_plugs[0].split(".", 1)[0] if curve_plugs else None
    if not curve:
        return {
            "curve": None,
            "uuid": None,
            "input": (),
            "times": (),
            "values": (),
            "in_tangent": (),
            "out_tangent": (),
            "in_angle": (),
            "out_angle": (),
            "in_weight": (),
            "out_weight": (),
            "weighted": None,
            "pre_infinite": None,
            "post_infinite": None,
            "pre_infinite_attr": None,
            "post_infinite_attr": None,
        }
    return {
        "curve": curve,
        "uuid": (cmds.ls(curve, uuid=True) or [None])[0],
        "input": tuple(
            cmds.listConnections(
                f"{curve}.input",
                source=True,
                destination=False,
                plugs=True,
            )
            or []
        ),
        "times": tuple(cmds.keyframe(curve, query=True, timeChange=True) or []),
        "values": tuple(cmds.keyframe(curve, query=True, valueChange=True) or []),
        "in_tangent": tuple(cmds.keyTangent(curve, query=True, inTangentType=True) or []),
        "out_tangent": tuple(cmds.keyTangent(curve, query=True, outTangentType=True) or []),
        "in_angle": tuple(cmds.keyTangent(curve, query=True, inAngle=True) or []),
        "out_angle": tuple(cmds.keyTangent(curve, query=True, outAngle=True) or []),
        "in_weight": tuple(cmds.keyTangent(curve, query=True, inWeight=True) or []),
        "out_weight": tuple(cmds.keyTangent(curve, query=True, outWeight=True) or []),
        "weighted": cmds.keyTangent(curve, query=True, weightedTangents=True),
        "pre_infinite": cmds.setInfinity(curve, query=True, preInfinite=True),
        "post_infinite": cmds.setInfinity(curve, query=True, postInfinite=True),
        "pre_infinite_attr": cmds.getAttr(f"{curve}.preInfinity"),
        "post_infinite_attr": cmds.getAttr(f"{curve}.postInfinity"),
    }


def _timeline_state():
    """Capture all timeline fields that the Control Rig transaction owns."""
    return {
        "current": float(cmds.currentTime(query=True)),
        "min": float(cmds.playbackOptions(query=True, min=True)),
        "max": float(cmds.playbackOptions(query=True, max=True)),
        "animation_start": float(cmds.playbackOptions(query=True, animationStartTime=True)),
        "animation_end": float(cmds.playbackOptions(query=True, animationEndTime=True)),
        "time_unit": cmds.currentUnit(query=True, time=True),
    }


class _EmptyVmdData:
    bone_frames = []
    morph_frames = []
    camera_frames = []
    light_frames = []
    ik_show_hide_frames = []


class TestCr06103SceneTransaction(MayaTestBase):
    """Force a late mixed-import failure and prove scene state is restored."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plugin_path = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
        if not cmds.pluginInfo(str(plugin_path), query=True, loaded=True):
            cls.plugins_loaded.extend(cmds.loadPlugin(str(plugin_path), quiet=True) or [])

    def _import_control_fixture(self, namespace):
        """Import the deterministic indexed PMX fixture without optional shading."""
        root = import_mmd_file(
            _PMX_PATH,
            options={
                "custom_namespace": namespace,
                "setup_rig": True,
                "setup_bone_orientation": True,
                "import_physics": False,
                "create_mmd_shaders": False,
            },
        )
        self.assertTrue(root)
        return cmds.ls(root, long=True)[0]

    def test_convert_control_rig_motion_a_to_b_clears_only_target_owned_keys(self):
        """Public VmdConverter.convert replaces A with B on one Control Rig root."""
        target_root = self._import_control_fixture("cr06103_target")
        foreign_root = self._import_control_fixture("cr06103_foreign")
        foreign_joint = _find_mmd_joint(foreign_root, "センター")
        cmds.setKeyframe(foreign_joint, attribute="translateX", time=17, value=4.5)

        motion_a = _synthetic_motion(
            ("センター", 5, (0.25, 0.0, 0.0)),
            ("センター", 9, (0.75, 0.0, 0.0)),
        )
        self.assertTrue(
            _registered_convert(
                VmdConverter(),
                motion_a,
                target_model=target_root,
                create_mmd_control_rig=True,
            )
        )
        center_control, metadata_a = _control_for_role(target_root, "center")
        groove_control, metadata_a_again = _control_for_role(target_root, "groove")
        self.assertEqual(metadata_a, metadata_a_again)
        self.assertEqual(metadata_a["state"], "EDIT")
        self.assertEqual(metadata_a["owner"], "CONTROL_OWNED")
        center_before = _curve_state(f"{center_control}.translateX")
        self.assertTrue(center_before["curve"])
        if not center_before["input"]:
            cmds.connectAttr("time1.outTime", f"{center_before['curve']}.input", force=True)
            center_before = _curve_state(f"{center_control}.translateX")
        self.assertGreaterEqual(len(center_before["times"]), 2)
        self.assertIsNotNone(center_before["uuid"])
        self.assertTrue(center_before["input"])
        # Capture after the target import establishes its global time unit;
        # the subsequent clear must not touch this foreign model.
        foreign_before = _curve_state(f"{foreign_joint}.translateX")

        motion_b = _synthetic_motion(
            ("グルーブ", 21, (0.4, 0.0, 0.0)),
            ("グルーブ", 27, (1.1, 0.0, 0.0)),
        )
        self.assertTrue(
            _registered_convert(
                VmdConverter(),
                motion_b,
                target_model=target_root,
                clear_existing_motion=True,
                create_mmd_control_rig=True,
            )
        )

        center_after = _curve_state(f"{center_control}.translateX")
        groove_after = _curve_state(f"{groove_control}.translateX")
        self.assertEqual(center_after["curve"], center_before["curve"])
        self.assertEqual(center_after["uuid"], center_before["uuid"])
        self.assertEqual(center_after["input"], center_before["input"])
        self.assertFalse(center_after["times"], "A-only center keys survived clear_existing_motion")
        self.assertTrue(groove_after["times"], "B-only groove keys were not authored")
        self.assertEqual(
            _curve_state(f"{foreign_joint}.translateX"),
            foreign_before,
            "foreign model animation was modified by target-scoped reimport",
        )
        metadata_after = json.loads(cmds.getAttr(f"{target_root}.mmd_control_rig_json"))
        self.assertEqual(metadata_after["state"], "EDIT")
        self.assertEqual(metadata_after["owner"], "CONTROL_OWNED")
        self.assertEqual(metadata_after["controls"], metadata_a["controls"])

    def test_registered_control_rig_rotation_time_curve_uses_raw_export_bytes(self):
        """Semantic registered curves must not be coerced to raw bytes."""
        target_root = self._import_control_fixture("cr06103_registered_time_curve")
        motion = _synthetic_motion(
            ("センター", 0, (0.25, 0.0, 0.0)),
            ("センター", 30, (0.75, 0.0, 0.0)),
        )
        expected = bytes(motion.bone_frames[1].interpolation)
        converter = VmdConverter()
        converter.use_quaternion_interpolation = True
        converter.use_vmd_rotation_time_curve = True

        self.assertTrue(
            _registered_convert(
                converter,
                motion,
                target_model=target_root,
                create_mmd_control_rig=True,
            )
        )

        metadata = json.loads(cmds.getAttr(f"{target_root}.mmd_control_rig_json"))
        self.assertEqual(metadata["rotationInterpolationMode"], "vmd_time_curve_experimental")
        interpolation = rotation_time_curve_interpolation_by_bone(metadata)
        self.assertEqual(interpolation["センター"][30], expected)

    def test_convert_control_rig_late_failure_restores_exact_a_transaction_state(self):
        """A late failure after clear and partial B keys restores the full A state."""
        target_root = self._import_control_fixture("cr06103_rollback")
        motion_a = _synthetic_motion(
            ("センター", 6, (0.2, 0.0, 0.0)),
            ("センター", 12, (0.9, 0.0, 0.0)),
        )
        self.assertTrue(
            _registered_convert(
                VmdConverter(),
                motion_a,
                target_model=target_root,
                create_mmd_control_rig=True,
            )
        )
        center_control, metadata = _control_for_role(target_root, "center")
        center_plug = f"{center_control}.translateX"
        center_curve = _curve_state(center_plug)
        self.assertTrue(center_curve["curve"])
        if not center_curve["input"]:
            cmds.connectAttr("time1.outTime", f"{center_curve['curve']}.input", force=True)
            center_curve = _curve_state(center_plug)
        self.assertTrue(center_curve["input"])
        cmds.keyTangent(center_curve["curve"], edit=True, weightedTangents=True)
        cmds.keyTangent(
            center_curve["curve"],
            edit=True,
            time=(center_curve["times"][0], center_curve["times"][0]),
            inTangentType="fixed",
            outTangentType="fixed",
            outAngle=23.0,
            outWeight=0.65,
        )
        cmds.keyTangent(
            center_curve["curve"],
            edit=True,
            time=(center_curve["times"][-1], center_curve["times"][-1]),
            inTangentType="fixed",
            outTangentType="fixed",
            inAngle=-17.0,
            inWeight=0.45,
        )
        cmds.setInfinity(center_curve["curve"], edit=True, preInfinite="cycle", postInfinite="oscillate")
        cmds.currentUnit(time="ntscf")
        cmds.playbackOptions(min=-20, max=300, animationStartTime=10, animationEndTime=250)
        cmds.currentTime(123, edit=True)
        before = _curve_state(center_plug)
        before_metadata_raw = cmds.getAttr(f"{target_root}.mmd_control_rig_json")
        before_timeline = _timeline_state()

        motion_b = _synthetic_motion(
            ("センター", 18, (2.0, 0.0, 0.0)),
            ("センター", 24, (3.0, 0.0, 0.0)),
        )
        motion_b.morph_frames = [object()]
        converter = VmdConverter()

        def _fail_after_partial_b_writes(_frames):
            partial = _curve_state(center_plug)
            self.assertTrue(partial["times"], "late failure was not reached after B key writes")
            self.assertNotEqual(partial["times"], before["times"])
            raise RuntimeError("forced late B morph failure")

        with patch.object(
            converter,
            "_convert_morph_animation",
            side_effect=_fail_after_partial_b_writes,
        ):
            with self.assertRaises(Exception) as raised:
                _registered_convert(
                    converter,
                    motion_b,
                    target_model=target_root,
                    clear_existing_motion=True,
                    create_mmd_control_rig=True,
                )
        self.assertIn("forced late B morph failure", str(raised.exception))

        after = _curve_state(center_plug)
        self.assertEqual(after["curve"], before["curve"])
        self.assertEqual(after["uuid"], before["uuid"])
        self.assertEqual(after["input"], before["input"])
        self.assertEqual(after["times"], before["times"])
        self.assertEqual(after["values"], before["values"])
        self.assertEqual(after["in_tangent"], before["in_tangent"])
        self.assertEqual(after["out_tangent"], before["out_tangent"])
        self.assertEqual(after["weighted"], before["weighted"])
        self.assertEqual(after["pre_infinite"], before["pre_infinite"])
        self.assertEqual(after["post_infinite"], before["post_infinite"])
        self.assertEqual(after["pre_infinite_attr"], before["pre_infinite_attr"])
        self.assertEqual(after["post_infinite_attr"], before["post_infinite_attr"])
        for field in ("in_angle", "out_angle", "in_weight", "out_weight"):
            self.assertEqual(len(after[field]), len(before[field]))
            for actual, expected in zip(after[field], before[field]):
                self.assertAlmostEqual(actual, expected, places=5, msg=field)
        self.assertEqual(cmds.getAttr(f"{target_root}.mmd_control_rig_json"), before_metadata_raw)
        self.assertEqual(_timeline_state(), before_timeline)
        self.assertEqual(metadata["state"], "EDIT")
        self.assertEqual(metadata["owner"], "CONTROL_OWNED")

    def test_legacy_a_is_cleared_before_new_control_rig_samples_bind_basis(self):
        """A->B creation samples the bind pose, not A's current evaluated pose."""
        target_root = self._import_control_fixture("cr06103_basis_order")
        center_joint = _find_mmd_joint(target_root, "センター")
        bind_matrix = tuple(cmds.xform(center_joint, query=True, worldSpace=True, matrix=True))

        motion_a = _synthetic_motion(
            ("センター", 5, (0.25, 0.0, 0.0)),
            ("センター", 9, (0.75, 0.0, 0.0)),
        )
        legacy_converter = VmdConverter()
        legacy_converter.use_animation_layers = False
        self.assertTrue(_registered_convert(legacy_converter, motion_a, target_model=target_root))
        cmds.currentTime(9, edit=True)
        cmds.refresh(force=True)
        evaluated_a = tuple(cmds.xform(center_joint, query=True, worldSpace=True, matrix=True))
        self.assertNotEqual(evaluated_a, bind_matrix)

        observed_build_joint_matrices = []
        real_build = mmd_control_rig_builder.build_mmd_control_rig

        def observe_build(*args, **kwargs):
            observed_build_joint_matrices.append(
                tuple(cmds.xform(center_joint, query=True, worldSpace=True, matrix=True))
            )
            return real_build(*args, **kwargs)

        motion_b = _synthetic_motion(
            ("グルーブ", 21, (0.4, 0.0, 0.0)),
            ("グルーブ", 27, (1.1, 0.0, 0.0)),
        )
        with patch.object(mmd_control_rig_builder, "build_mmd_control_rig", side_effect=observe_build):
            control_converter = VmdConverter()
            control_converter.use_animation_layers = False
            self.assertTrue(
                _registered_convert(
                    control_converter,
                    motion_b,
                    target_model=target_root,
                    clear_existing_motion=True,
                    create_mmd_control_rig=True,
                )
            )

        self.assertEqual(len(observed_build_joint_matrices), 1)
        for actual, expected in zip(observed_build_joint_matrices[0], bind_matrix):
            self.assertAlmostEqual(actual, expected, places=5)
        center_zero, _metadata = _control_for_role(target_root, "center")
        center_zero = cmds.listRelatives(center_zero, parent=True, fullPath=True)[0]
        zero_matrix = tuple(cmds.xform(center_zero, query=True, worldSpace=True, matrix=True))
        for actual, expected in zip(zero_matrix, bind_matrix):
            self.assertAlmostEqual(actual, expected, places=5)

    def test_new_control_rig_preflight_failure_restores_legacy_a_motion(self):
        """A legacy curve survives a failed A->B rig transition exactly."""
        target_root = self._import_control_fixture("cr06103_basis_rollback")
        center_joint = _find_mmd_joint(target_root, "センター")
        motion_a = _synthetic_motion(
            ("センター", 6, (0.2, 0.0, 0.0)),
            ("センター", 12, (0.9, 0.0, 0.0)),
        )
        legacy_converter = VmdConverter()
        legacy_converter.use_animation_layers = False
        self.assertTrue(_registered_convert(legacy_converter, motion_a, target_model=target_root))
        before = _curve_state(f"{center_joint}.translateX")
        self.assertTrue(before["curve"])

        motion_b = _synthetic_motion(
            ("センター", 18, (2.0, 0.0, 0.0)),
            ("センター", 24, (3.0, 0.0, 0.0)),
        )
        motion_b.morph_frames = [object()]
        control_converter = VmdConverter()
        control_converter.use_animation_layers = False
        with patch.object(control_converter, "_convert_morph_animation", side_effect=RuntimeError("forced basis rollback")):
            with self.assertRaises(Exception) as raised:
                _registered_convert(
                    control_converter,
                    motion_b,
                    target_model=target_root,
                    clear_existing_motion=True,
                    create_mmd_control_rig=True,
                )

        self.assertIn("forced basis rollback", str(raised.exception))
        self.assertEqual(_curve_state(f"{center_joint}.translateX"), before)
        self.assertFalse(cmds.objExists(f"{target_root}.mmd_control_rig_json"))

    def test_late_failure_restores_curve_timeline_and_created_camera_light(self):
        root = cmds.group(empty=True, name="cr06103_transaction_model")
        joint = cmds.joint(name="cr06103_transaction_joint")
        if not (cmds.listRelatives(joint, parent=True, fullPath=True) or []):
            cmds.parent(joint, root)
        cmds.setKeyframe(joint, attribute="rotateX", time=1, value=10.0)
        cmds.setKeyframe(joint, attribute="rotateX", time=5, value=30.0)
        mesh = cmds.polyCube(name="cr06103_transaction_mesh")[0]
        cmds.parent(mesh, root)
        blend_shape = cmds.blendShape(mesh, name="cr06103_transaction_blendShape")[0]
        cmds.setKeyframe(blend_shape, attribute="weight[0]", time=1, value=0.25)
        cmds.setKeyframe(blend_shape, attribute="weight[0]", time=5, value=0.75)

        converter = VmdConverter()
        converter.bone_name_mapping = {"センター": joint}
        converter.morph_name_mapping = {
            "笑顔": [(blend_shape, "weight[0]", "笑顔")],
        }
        snapshot = converter._capture_mmd_control_rig_scene_snapshot(
            root,
            _EmptyVmdData(),
        )

        # Simulate clear_existing_motion plus successful camera/light writes,
        # followed by a late exception in a subsequent mixed channel.
        cmds.setKeyframe(joint, attribute="rotateX", time=1, value=99.0)
        cmds.setKeyframe(joint, attribute="rotateX", time=2, value=55.0)
        cmds.setKeyframe(blend_shape, attribute="weight[0]", time=1, value=0.95)
        cmds.setKeyframe(blend_shape, attribute="weight[0]", time=2, value=0.5)
        cmds.playbackOptions(min=0, max=240, animationStartTime=0, animationEndTime=240)
        cmds.currentTime(42, edit=True)
        get_or_create_camera()
        get_or_create_light()

        transaction = {
            "root": root,
            "created": False,
            "entered_here": False,
            "prior_animation_snapshot": [],
            "scene_snapshot": snapshot,
        }
        rollback_error = converter._rollback_mmd_control_rig_import(transaction)

        self.assertIsNone(rollback_error, rollback_error)
        self.assertEqual(cmds.keyframe(joint, attribute="rotateX", query=True, timeChange=True), [1.0, 5.0])
        restored_values = cmds.keyframe(joint, attribute="rotateX", query=True, valueChange=True)
        self.assertEqual(len(restored_values), 2)
        self.assertAlmostEqual(restored_values[0], 10.0, places=7)
        self.assertAlmostEqual(restored_values[1], 30.0, places=7)
        morph_values = cmds.keyframe(blend_shape, attribute="weight[0]", query=True, valueChange=True)
        self.assertEqual(len(morph_values), 2)
        self.assertAlmostEqual(morph_values[0], 0.25, places=7)
        self.assertAlmostEqual(morph_values[1], 0.75, places=7)
        self.assertEqual(cmds.playbackOptions(query=True, max=True), snapshot["timeline"]["max"])
        self.assertEqual(cmds.currentTime(query=True), snapshot["timeline"]["current_time"])
        self.assertFalse(cmds.ls("*.mmd_camera", objectsOnly=True))
        self.assertFalse(cmds.ls("*.mmd_light", objectsOnly=True))

    def test_entered_rig_failure_restores_rig_before_original_joint_source(self):
        root = cmds.group(empty=True, name="cr06103_transaction_order_model")
        joint = cmds.joint(name="cr06103_transaction_order_joint")
        if not (cmds.listRelatives(joint, parent=True, fullPath=True) or []):
            cmds.parent(joint, root)
        cmds.setKeyframe(joint, attribute="rotateX", time=2, value=12.0)
        original_curve = (cmds.listConnections(f"{joint}.rotateX", source=True, destination=False) or [None])[0]
        raw_metadata = '{"state":"ATTACHED","owner":"MMD_OWNED"}'
        cmds.addAttr(root, longName="mmd_control_rig_json", dataType="string")
        cmds.setAttr(f"{root}.mmd_control_rig_json", raw_metadata, type="string")

        converter = VmdConverter()
        converter.bone_name_mapping = {"センター": joint}
        snapshot = converter._capture_mmd_control_rig_scene_snapshot(root, _EmptyVmdData())
        cmds.setKeyframe(joint, attribute="rotateX", time=2, value=88.0)

        events = []
        original_scene_restore = converter._restore_mmd_control_rig_scene_snapshot
        transaction = {
            "root": root,
            "created": True,
            "entered_here": True,
            "prior_raw_metadata": raw_metadata,
            "prior_animation_snapshot": [],
            "scene_snapshot": snapshot,
        }
        with patch(
            "mmd_tools.core.mmd_control_rig_motion.restore_mmd_control_rig_attached",
            side_effect=lambda _root: events.append("rig_restore"),
        ), patch(
            "mmd_tools.core.mmd_control_rig_builder.remove_mmd_control_rig",
            side_effect=lambda _root: events.append("rig_remove"),
        ), patch.object(
            converter,
            "_restore_mmd_control_rig_scene_snapshot",
            side_effect=lambda value: (events.append("scene_restore"), original_scene_restore(value))[1],
        ):
            rollback_error = converter._rollback_mmd_control_rig_import(transaction)

        self.assertIsNone(rollback_error, rollback_error)
        self.assertEqual(events, ["rig_restore", "rig_remove", "scene_restore"])
        self.assertTrue(cmds.objExists(original_curve))
        self.assertAlmostEqual(cmds.keyframe(original_curve, query=True, valueChange=True)[0], 12.0, places=7)
        self.assertEqual(cmds.getAttr(f"{root}.mmd_control_rig_json"), raw_metadata)

    def test_existing_camera_and_light_curves_restore(self):
        root = cmds.group(empty=True, name="cr06103_transaction_scene_model")
        camera = get_or_create_camera()
        light = get_or_create_light()
        cmds.setKeyframe(camera, attribute="translateX", time=3, value=4.0)
        cmds.setKeyframe(light, attribute="rotateX", time=3, value=15.0)

        converter = VmdConverter()
        converter.bone_name_mapping = {}
        snapshot = converter._capture_mmd_control_rig_scene_snapshot(root, _EmptyVmdData())
        cmds.setKeyframe(camera, attribute="translateX", time=3, value=99.0)
        cmds.setKeyframe(camera, attribute="translateX", time=4, value=50.0)
        cmds.setKeyframe(light, attribute="rotateX", time=3, value=88.0)

        rollback_error = converter._rollback_mmd_control_rig_import(
            {
                "root": root,
                "entered_here": False,
                "created": False,
                "prior_animation_snapshot": [],
                "scene_snapshot": snapshot,
            }
        )

        self.assertIsNone(rollback_error, rollback_error)
        camera_values = cmds.keyframe(camera, attribute="translateX", query=True, valueChange=True)
        self.assertEqual(len(camera_values), 1)
        self.assertAlmostEqual(camera_values[0], 4.0, places=7)
        light_values = cmds.keyframe(light, attribute="rotateX", query=True, valueChange=True)
        self.assertEqual(len(light_values), 1)
        self.assertAlmostEqual(light_values[0], 15.0, places=7)

    def test_legacy_ik_solver_channels_restore_after_late_failure(self):
        """Rollback restores fallback solver keys, including inputRotate elements."""
        root = cmds.group(empty=True, name="cr06103_transaction_legacy_ik_model")
        joint = cmds.joint(name="cr06103_transaction_legacy_ik_joint")
        if not (cmds.listRelatives(joint, parent=True, fullPath=True) or []):
            cmds.parent(joint, root)
        solver = cmds.createNode("mmdCcdIk", name="cr06103_transaction_legacy_ik_solver")
        if not cmds.attributeQuery("mmd_ik_bone_name", node=solver, exists=True):
            cmds.addAttr(solver, longName="mmd_ik_bone_name", dataType="string")
        cmds.setAttr(f"{solver}.mmd_ik_bone_name", "右髪ＩＫ", type="string")
        chain = {
            "bones": [{"rest_position": [0.0, 0.0, 0.0], "parent_slot": -1}],
            "links": [{"bone_slot": 2}],
            "targetBoneSlot": 0,
            "controllerBoneSlot": 0,
            "iterationCount": 1,
            "limitAngle": 1.0,
        }
        cmds.setAttr(f"{solver}.chainJson", json.dumps(chain), type="string")
        cmds.connectAttr(f"{solver}.outputRotate[0]", f"{joint}.rotate", force=True)
        cmds.setKeyframe(solver, attribute="enabled", time=1, value=0)
        input_plug = f"{solver}.inputRotate[2].inputRotateElementX"
        cmds.setKeyframe(input_plug, time=1, value=0.25)

        converter = VmdConverter()
        converter.bone_name_mapping = {"右髪ＩＫ": joint}
        snapshot = converter._capture_mmd_control_rig_scene_snapshot(root, _EmptyVmdData())
        snapshot_plugs = {
            f"{row['node']}.{row['attribute']}"
            for row in snapshot["channels"]
        }
        self.assertIn(f"{solver}.enabled", snapshot_plugs)
        self.assertIn(input_plug, snapshot_plugs)
        cmds.setKeyframe(solver, attribute="enabled", time=2, value=1)
        cmds.setKeyframe(input_plug, time=2, value=0.75)

        rollback_error = converter._rollback_mmd_control_rig_import(
            {
                "root": root,
                "entered_here": False,
                "created": False,
                "prior_animation_snapshot": [],
                "scene_snapshot": snapshot,
            }
        )

        self.assertIsNone(rollback_error, rollback_error)
        self.assertEqual(cmds.keyframe(f"{solver}.enabled", query=True, timeChange=True), [1.0])
        self.assertEqual(cmds.keyframe(input_plug, query=True, timeChange=True), [1.0])
        self.assertAlmostEqual(
            cmds.keyframe(input_plug, query=True, valueChange=True)[0],
            0.25,
            places=7,
        )

    def test_clear_existing_motion_scopes_legacy_ik_nested_channels(self):
        """Clear target legacy IK payload while preserving foreign graph state."""
        target_root = cmds.group(empty=True, name="cr06103_clear_legacy_ik_target_root")
        foreign_root = cmds.group(empty=True, name="cr06103_clear_legacy_ik_foreign_root")

        cmds.select(clear=True)
        target_joint = cmds.joint(name="cr06103_clear_legacy_ik_target_joint")
        cmds.parent(target_joint, target_root)
        cmds.select(clear=True)
        foreign_joint = cmds.joint(name="cr06103_clear_legacy_ik_foreign_joint")
        cmds.parent(foreign_joint, foreign_root)

        def _create_solver(name, joint):
            solver = cmds.createNode("mmdCcdIk", name=name)
            cmds.connectAttr(f"{solver}.outputRotate[0]", f"{joint}.rotate", force=True)
            cmds.setKeyframe(solver, attribute="enabled", time=3, value=0)
            enabled = f"{solver}.enabled"
            nested_x = f"{solver}.inputRotate[2].inputRotateElementX"
            nested_z = f"{solver}.inputRotate[5].inputRotateElementZ"
            cmds.setKeyframe(nested_x, time=7, value=0.25)
            cmds.setKeyframe(nested_z, time=9, value=-0.5)
            enabled_curve = (cmds.listConnections(
                enabled,
                source=True,
                destination=False,
                type="animCurve",
            ) or [None])[0]
            enabled_curve_uuid = cmds.ls(enabled_curve, uuid=True)[0] if enabled_curve else None
            nested_curves = tuple(
                (cmds.listConnections(plug, source=True, destination=False, type="animCurve") or [None])[0]
                for plug in (nested_x, nested_z)
            )
            nested_curve_uuids = tuple(
                cmds.ls(curve, uuid=True)[0] for curve in nested_curves if curve
            )
            return (
                solver,
                enabled,
                enabled_curve,
                enabled_curve_uuid,
                (nested_x, nested_z),
                nested_curves,
                nested_curve_uuids,
                cmds.connectionInfo(
                f"{joint}.rotate",
                sourceFromDestination=True,
                ),
            )

        (
            target_solver,
            target_enabled,
            target_enabled_curve,
            target_enabled_curve_uuid,
            target_inputs,
            target_curves,
            target_curve_uuids,
            target_source,
        ) = _create_solver(
            "cr06103_clear_legacy_ik_target_solver",
            target_joint,
        )
        (
            foreign_solver,
            foreign_enabled,
            foreign_enabled_curve,
            foreign_enabled_curve_uuid,
            foreign_inputs,
            foreign_curves,
            foreign_curve_uuids,
            foreign_source,
        ) = _create_solver(
            "cr06103_clear_legacy_ik_foreign_solver",
            foreign_joint,
        )

        converter = VmdConverter()
        converter.bone_name_mapping = {}
        converter.morph_name_mapping = {}

        clear_existing_motion(
            converter._import_state_context(),
            "cr06103_clear_legacy_ik_missing_layer",
            target_model=target_root,
        )

        self.assertFalse(cmds.keyframe(target_enabled, query=True, timeChange=True))
        self.assertEqual(
            (cmds.listConnections(target_enabled, source=True, destination=False, type="animCurve") or [None])[0],
            target_enabled_curve,
        )
        self.assertEqual(cmds.ls(target_enabled_curve, uuid=True)[0], target_enabled_curve_uuid)
        for input_plug in target_inputs:
            self.assertFalse(cmds.keyframe(input_plug, query=True, timeChange=True))
        self.assertEqual(
            tuple(
                (cmds.listConnections(plug, source=True, destination=False, type="animCurve") or [None])[0]
                for plug in target_inputs
            ),
            target_curves,
        )
        self.assertEqual(
            tuple(cmds.ls(curve, uuid=True)[0] for curve in target_curves if curve),
            target_curve_uuids,
        )
        self.assertEqual(
            cmds.keyframe(foreign_enabled, query=True, timeChange=True),
            [3.0],
        )
        self.assertEqual(
            (cmds.listConnections(foreign_enabled, source=True, destination=False, type="animCurve") or [None])[0],
            foreign_enabled_curve,
        )
        self.assertEqual(cmds.ls(foreign_enabled_curve, uuid=True)[0], foreign_enabled_curve_uuid)
        self.assertEqual(cmds.keyframe(foreign_inputs[0], query=True, timeChange=True), [7.0])
        self.assertEqual(cmds.keyframe(foreign_inputs[1], query=True, timeChange=True), [9.0])
        self.assertEqual(
            tuple(
                (cmds.listConnections(plug, source=True, destination=False, type="animCurve") or [None])[0]
                for plug in foreign_inputs
            ),
            foreign_curves,
        )
        self.assertEqual(
            tuple(cmds.ls(curve, uuid=True)[0] for curve in foreign_curves if curve),
            foreign_curve_uuids,
        )
        self.assertTrue(cmds.objExists(target_solver))
        self.assertTrue(cmds.objExists(foreign_solver))
        self.assertEqual(
            cmds.connectionInfo(f"{target_joint}.rotate", sourceFromDestination=True),
            target_source,
        )
        self.assertEqual(
            cmds.connectionInfo(f"{foreign_joint}.rotate", sourceFromDestination=True),
            foreign_source,
        )

    def test_zero_key_curve_stays_empty_after_late_failure(self):
        root = cmds.group(empty=True, name="cr06103_transaction_empty_curve_model")
        control = cmds.group(empty=True, name="cr06103_transaction_empty_curve_control")
        cmds.parent(control, root)
        curve = cmds.createNode("animCurveTL", name="cr06103_transaction_empty_curve")
        cmds.connectAttr(f"{curve}.output", f"{control}.translateX", force=True)

        converter = VmdConverter()
        converter.bone_name_mapping = {"センター": control}
        snapshot = converter._capture_mmd_control_rig_scene_snapshot(root, _EmptyVmdData())
        cmds.setKeyframe(control, attribute="translateX", time=6, value=42.0)
        self.assertEqual(cmds.keyframe(curve, query=True, timeChange=True), [6.0])

        rollback_error = converter._rollback_mmd_control_rig_import(
            {
                "root": root,
                "entered_here": False,
                "created": False,
                "prior_animation_snapshot": [],
                "scene_snapshot": snapshot,
            }
        )

        self.assertIsNone(rollback_error, rollback_error)
        self.assertFalse(cmds.keyframe(curve, query=True, timeChange=True))
        self.assertEqual(
            cmds.listConnections(f"{control}.translateX", source=True, destination=False),
            [curve],
        )


if __name__ == "__main__":
    unittest.main()
