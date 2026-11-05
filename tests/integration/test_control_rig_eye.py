"""Maya contracts for the optional combined-eye FK control."""
from pathlib import Path

from maya import cmds

from mmd_tools.core.mmd_control_rig_builder import build_mmd_control_rig, read_mmd_control_rig_metadata
from mmd_tools.core.mmd_control_rig_motion import (
    enter_mmd_control_rig_edit, restore_mmd_control_rig_attached, bake_mmd_control_rig,
)
from mmd_tools.io.mmd_importer import import_mmd_file
from tests.common.maya_test_base import MayaTestBase

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "tests/data/mmt_test_model.pmx"
OPTIONS = {"setup_rig": True, "setup_bone_orientation": True, "import_physics": False,
           "use_namespace": True}


def eye_joints(root):
    joints = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
    by_name = {cmds.getAttr(j + ".mmd_bone_name"): j for j in joints}
    return [by_name[name] for name in ("両目", "左目", "右目")]


def pose(joints):
    return [cmds.xform(j, query=True, worldSpace=True, matrix=True) for j in joints]


def append_edges(eyes):
    nodes = [cmds.listConnections(j + ".rotateX", source=True, destination=False)[0] for j in eyes]
    return {n: sorted(cmds.listConnections(n, source=True, destination=False,
                                         plugs=True, connections=True) or []) for n in nodes}


class TestControlRigEye(MayaTestBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.load_plugin(str(ROOT / "mmd_tools/plugin_main.py"))

    def setUp(self):
        super().setUp()
        # VMD is fixed at 30 fps; compare frames in the same time unit.
        cmds.currentUnit(time="ntsc")

    def assert_pose(self, actual, expected):
        self.assertLess(max(abs(a-b) for x,y in zip(actual, expected) for a,b in zip(x,y)), 1e-4)

    def test_edit_restore_preserves_original_curves_and_eye_append(self):
        root = import_mmd_file(str(MODEL), options=OPTIONS)
        joints = eye_joints(root)
        edges = append_edges(joints[1:])
        for frame, value in ((1, -8), (10, 13)):
            cmds.setKeyframe(joints[0], attribute="rotateY", time=frame, value=value)
        writer = cmds.connectionInfo(joints[0] + ".rotateY", sourceFromDestination=True)
        original_keys = cmds.keyframe(writer.split(".")[0], query=True, valueChange=True)
        build_mmd_control_rig(root)
        metadata = read_mmd_control_rig_metadata(root)
        self.assertEqual(metadata["bindings"]["both_eyes"]["inputKind"], "direct_channel")
        self.assertNotIn("left_eye", metadata["controls"])
        self.assertNotIn("right_eye", metadata["controls"])
        cmds.currentTime(5)
        expected = pose(joints)
        enter_mmd_control_rig_edit(root)
        self.assert_pose(pose(joints), expected)
        restore_mmd_control_rig_attached(root)
        self.assert_pose(pose(joints), expected)
        self.assertEqual(cmds.connectionInfo(joints[0] + ".rotateY", sourceFromDestination=True), writer)
        self.assertEqual(cmds.keyframe(writer.split(".")[0], query=True, valueChange=True), original_keys)
        self.assertEqual(append_edges(joints[1:]), edges)

    def test_bake_and_vmd_reimport_preserve_both_and_independent_eyes(self):
        self._check_bake_and_roundtrip(animate_eye_bases=False, reimport=True)

    def test_bake_and_vmd_preserve_independent_eye_base_animation(self):
        self._check_bake_and_roundtrip(animate_eye_bases=True, reimport=True)

    def _check_bake_and_roundtrip(self, *, animate_eye_bases, reimport):
        root = import_mmd_file(str(MODEL), options=OPTIONS)
        joints = eye_joints(root)
        edges = append_edges(joints[1:])
        # Independent eye offsets must survive combined-eye control edits.
        for index, eye in enumerate(joints[1:]):
            append = cmds.listConnections(eye + ".rotateX", source=True, destination=False)[0]
            for frame in range(1, 11):
                value = 3 + (2 * (frame - 1) / 9 if animate_eye_bases else 0)
                cmds.setKeyframe(append, attribute="baseRotateY", time=frame,
                                value=value * (index * 2 - 1))
        edges = append_edges(joints[1:])
        build_mmd_control_rig(root)
        enter_mmd_control_rig_edit(root)
        metadata = read_mmd_control_rig_metadata(root)
        control = cmds.ls(metadata["controls"]["both_eyes"], long=True)[0]
        for axis in "XYZ":
            self.assertTrue(cmds.getAttr(control + ".rotate" + axis, keyable=True))
            self.assertFalse(cmds.getAttr(control + ".rotate" + axis, lock=True))
        # Compare authored VMD key frames; sparse Euler and VMD quaternion
        # interpolation between keys are different contracts.
        for frame in range(1, 11):
            weight = (frame - 1) / 9
            values = [a + (b-a)*weight for a,b in zip((4,-7,2), (-6,11,-3))]
            for axis, value in zip("XYZ", values):
                cmds.setKeyframe(control, attribute="rotate" + axis, time=frame, value=value)
        for axis in "XYZ":
            self.assertEqual(cmds.keyframe(control, attribute="rotate" + axis, query=True,
                                           timeChange=True), list(range(1, 11)))
        expected = {}
        for frame in range(1, 11):
            cmds.currentTime(frame)
            expected[frame] = pose(joints)
        self.assertGreater(max(abs(a-b) for a,b in zip(expected[1][0], expected[10][0])), 1e-3)
        bake_mmd_control_rig(root)
        self.assertEqual(append_edges(joints[1:]), edges)
        for frame in expected:
            cmds.currentTime(frame)
            self.assert_pose(pose(joints), expected[frame])
        if not reimport:
            return
        from mmd_tools.converters.vmd_scene_collector import VmdSceneCollector
        from mmd_tools.io.vmd_exporter import VmdExporter
        output = self.get_temp_filename("both_eyes_fk.vmd")
        VmdExporter().export_vmd_animation(output, VmdSceneCollector().collect({"target_model": root}))
        fresh = import_mmd_file(str(MODEL), options=OPTIONS)
        self.assertTrue(import_mmd_file(output, options={"target_model": fresh, "pmx_path": str(MODEL)}))
        fresh_eyes = eye_joints(fresh)
        for frame in expected:
            cmds.currentTime(frame)
            self.assert_pose(pose(fresh_eyes), expected[frame])

    def test_missing_combined_eye_remains_optional(self):
        root = import_mmd_file(str(MODEL), options=OPTIONS)
        both = eye_joints(root)[0]
        cmds.setAttr(both + ".mmd_bone_name", "custom_eye_driver", type="string")
        build_mmd_control_rig(root)
        metadata = read_mmd_control_rig_metadata(root)
        self.assertNotIn("both_eyes", metadata["controls"])
        enter_mmd_control_rig_edit(root)
        restore_mmd_control_rig_attached(root)

    def test_foreign_combined_eye_writer_is_not_claimed(self):
        root = import_mmd_file(str(MODEL), options=OPTIONS)
        both = eye_joints(root)[0]
        writer = cmds.createNode("multiplyDivide", name="externalEyeWriter")
        cmds.connectAttr(writer + ".outputX", both + ".rotateX")
        source = cmds.connectionInfo(both + ".rotateX", sourceFromDestination=True)
        build_mmd_control_rig(root)
        metadata = read_mmd_control_rig_metadata(root)
        self.assertNotIn("both_eyes", metadata["controls"])
        self.assertEqual(cmds.connectionInfo(both + ".rotateX", sourceFromDestination=True), source)
