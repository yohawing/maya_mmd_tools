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


def create_eye_ik_case():
    """Create real nonzero combined-eye keys plus independent append bases."""
    cmds.currentUnit(time='ntsc')
    root = import_mmd_file(str(MODEL), options=OPTIONS)
    joints = eye_joints(root)
    for index, eye in enumerate(joints[1:]):
        append = cmds.listConnections(eye+'.rotateX', source=True, destination=False)[0]
        cmds.setKeyframe(append+'.baseRotateY', time=1, value=(-3, 3)[index])
        cmds.setKeyframe(append+'.baseRotateY', time=10, value=(-5, 5)[index])
    build_mmd_control_rig(root)
    enter_mmd_control_rig_edit(root)
    metadata = read_mmd_control_rig_metadata(root)
    control = cmds.ls(metadata['controls']['both_eyes'], long=True)[0]
    for frame, values in ((1, (4, -7, 11)), (10, (-6, 11, 17))):
        for axis, value in zip('XYZ', values):
            if cmds.setKeyframe(control, attribute='rotate'+axis, time=frame, value=value) <= 0:
                raise AssertionError('Combined-eye FK key was not created')
    cmds.currentTime(5, update=True)
    return root, control, joints


class TestControlRigEyeIk(MayaTestBase):
    """Eye aim must retain authored roll, append bases and exact FK restoration."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.load_plugin(str(ROOT/'mmd_tools/plugin_main.py'))

    def assert_eye_pose(self, actual, expected):
        self.assertLess(max(abs(a-b) for x,y in zip(actual, expected) for a,b in zip(x,y)), 1e-4)

    def move_eye_target(self, target):
        for axis in 'XYZ':
            self.assertTrue(cmds.getAttr(target+'.translate'+axis, keyable=True))
            self.assertFalse(cmds.getAttr(target+'.translate'+axis, lock=True))
        before = cmds.getAttr(target+'.translateX')
        count = cmds.setKeyframe(target, attribute='translateX', time=5, value=before+.4)
        self.assertGreater(count, 0)
        self.assertTrue(cmds.keyframe(target, attribute='translateX', query=True, timeChange=True))
        cmds.currentTime(5, update=True)

    def test_quaternion_fk_survives_active_ik_save_and_reenable(self):
        """Keep grouped FK quaternion curves intact while eye IK owns rotation."""
        from mmd_tools.core.mmd_control_rig_eye_ik import enable_eye_ik, disable_eye_ik
        from tests.integration.test_pose_layers import curve_snapshot

        root, control, joints = create_eye_ik_case()
        curves = [cmds.connectionInfo(control + '.rotate' + axis, sourceFromDestination=True).split('.')[0]
                  for axis in 'XYZ']
        cmds.rotationInterpolation(*curves, convert='quaternionSlerp')
        frames = (1, 3.5, 5, 8.5, 10)
        expected = {}
        for frame in frames:
            cmds.currentTime(frame, update=True)
            expected[frame] = pose(joints)
        original_curves = curve_snapshot()
        scene = self.get_temp_filename('quaternion_eye.ma')
        for _ in range(2):
            cmds.currentTime(5, update=True)
            row = enable_eye_ik(root)
            self.move_eye_target(row['target'])
            cmds.file(rename=scene)
            cmds.file(save=True, type='mayaAscii', force=True)
            cmds.file(scene, open=True, force=True)
            disable_eye_ik(root)
            for frame in frames:
                cmds.currentTime(frame, update=True)
                self.assert_eye_pose(pose(joints), expected[frame])
            actual = curve_snapshot()
            for name in curves:
                self.assertEqual(original_curves[name]['times'], actual[name]['times'])
                self.assertEqual(original_curves[name]['destinations'], actual[name]['destinations'])
                for before, after in zip(original_curves[name]['values'], actual[name]['values']):
                    self.assertAlmostEqual(before, after, delta=1e-5)

    def test_old_eye_without_fk_storage_requires_rebuild_but_can_restore(self):
        from mmd_tools.core.mmd_control_rig_eye_ik import enable_eye_ik, disable_eye_ik
        from mmd_tools.core.mmd_control_rig_builder import (
            _write_metadata, MmdControlRigBuildError, remove_mmd_control_rig,
        )

        root, _control, joints = create_eye_ik_case()
        expected = pose(joints)
        enable_eye_ik(root)
        disable_eye_ik(root)
        metadata = read_mmd_control_rig_metadata(root)
        for item in metadata['eyeIk']['inputs']:
            item.pop('storageRef')
        _write_metadata(cmds, root, metadata)
        with self.assertRaisesRegex(MmdControlRigBuildError, 'requires rebuilding'):
            enable_eye_ik(root)
        self.assertEqual(read_mmd_control_rig_metadata(root), metadata)
        self.assert_eye_pose(pose(joints), expected)
        restore_mmd_control_rig_attached(root)
        self.assertEqual(read_mmd_control_rig_metadata(root)['state'], 'ATTACHED')
        remove_mmd_control_rig(root)
        self.assertFalse(set(cmds.ls(uuid=True)) & set(metadata['eyeIk']['nodes']))

    def test_active_old_eye_can_select_target_without_reconnecting(self):
        from mmd_tools.core.mmd_control_rig_eye_ik import enable_eye_ik
        from mmd_tools.core.mmd_control_rig_builder import _write_metadata

        root, _control, joints = create_eye_ik_case()
        target = enable_eye_ik(root)['target']
        expected = pose(joints)
        metadata = read_mmd_control_rig_metadata(root)
        for item in metadata['eyeIk']['inputs']:
            item.pop('storageRef')
        _write_metadata(cmds, root, metadata)
        self.assertEqual(enable_eye_ik(root)['target'], target)
        self.assertEqual(read_mmd_control_rig_metadata(root), metadata)
        self.assert_eye_pose(pose(joints), expected)

    def test_nonzero_fk_roll_enable_target_response_and_restore_keys(self):
        from mmd_tools.core.mmd_control_rig_eye_ik import enable_eye_ik, disable_eye_ik, inspect_eye_ik
        from tests.integration.test_pose_layers import curve_snapshot
        root, control, joints = create_eye_ik_case()
        original = pose(joints)
        edges = append_edges(joints[1:])
        sources = [cmds.connectionInfo(control+'.rotate'+a, sourceFromDestination=True) for a in 'XYZ']
        curves = curve_snapshot()
        row = enable_eye_ik(root)
        self.assertTrue(row['active'])
        self.assert_eye_pose(pose(joints), original)
        self.move_eye_target(row['target'])
        moved = pose(joints)
        for index in range(3):
            self.assertGreater(max(abs(a-b) for a,b in zip(original[index], moved[index])), 1e-3)
        self.assertEqual(append_edges(joints[1:]), edges)
        disable_eye_ik(root)
        self.assertFalse(inspect_eye_ik(root)['active'])
        self.assert_eye_pose(pose(joints), original)
        self.assertEqual([cmds.connectionInfo(control+'.rotate'+a, sourceFromDestination=True) for a in 'XYZ'], sources)
        after = curve_snapshot()
        self.assertEqual({key: after.get(key) for key in curves}, curves)

    def test_match_fk_and_undo_redo_preserve_current_pose(self):
        from mmd_tools.core.mmd_control_rig_eye_ik import enable_eye_ik, disable_eye_ik, inspect_eye_ik
        root, _control, joints = create_eye_ik_case()
        row = enable_eye_ik(root)
        self.move_eye_target(row['target'])
        expected = pose(joints)
        disable_eye_ik(root, match_fk=True)
        self.assert_eye_pose(pose(joints), expected)
        self.assertFalse(inspect_eye_ik(root)['active'])
        cmds.undo()
        self.assertTrue(inspect_eye_ik(root)['active'])
        self.assert_eye_pose(pose(joints), expected)
        cmds.redo()
        self.assertFalse(inspect_eye_ik(root)['active'])
        self.assert_eye_pose(pose(joints), expected)

    def test_target_follows_actual_eye_parent_when_head_moves(self):
        from maya.api import OpenMaya as om
        from mmd_tools.core.mmd_control_rig_eye_ik import enable_eye_ik
        root, _control, joints = create_eye_ik_case()
        row = enable_eye_ik(root)
        metadata = read_mmd_control_rig_metadata(root)
        head = cmds.ls(metadata['controls']['head'], long=True)[0]
        parent = cmds.listRelatives(joints[0], parent=True, fullPath=True)[0]

        def relative_target():
            world = om.MMatrix(cmds.xform(row['target'], query=True, worldSpace=True, matrix=True))
            inverse = om.MMatrix(cmds.getAttr(parent+'.worldInverseMatrix[0]'))
            return list(world * inverse)

        before_local = relative_target()
        before_world = pose([row['target']])[0]
        cmds.setAttr(head+'.rotateY', 15)
        cmds.currentTime(5, update=True)
        self.assertGreater(max(abs(a-b) for a,b in zip(before_world, pose([row['target']])[0])), 1e-3)
        self.assertLess(max(abs(a-b) for a,b in zip(before_local, relative_target())), 1e-4)

    def test_foreign_target_writer_rejects_without_touching_other_model(self):
        from mmd_tools.core.mmd_control_rig_eye_ik import enable_eye_ik, disable_eye_ik, inspect_eye_ik
        from mmd_tools.core.mmd_control_rig_builder import MmdControlRigBuildError
        root, _control, _joints = create_eye_ik_case()
        other, _other_control, other_joints = create_eye_ik_case()
        other_metadata = read_mmd_control_rig_metadata(other)
        other_pose = pose(other_joints)
        row = enable_eye_ik(root)
        foreign = cmds.createNode('multiplyDivide', name='ForeignEyeTarget')
        cmds.connectAttr(foreign+'.outputX', row['target']+'.translateX', force=True)
        source = cmds.connectionInfo(row['target']+'.translateX', sourceFromDestination=True)
        for action in (inspect_eye_ik, disable_eye_ik):
            with self.assertRaises(MmdControlRigBuildError):
                action(root)
            self.assertEqual(cmds.connectionInfo(row['target']+'.translateX', sourceFromDestination=True), source)
            self.assertEqual(read_mmd_control_rig_metadata(other), other_metadata)
            self.assert_eye_pose(pose(other_joints), other_pose)

    def test_eye_target_bake_vmd_and_ascii_roundtrip(self):
        import tempfile
        from mmd_tools.core.mmd_control_rig_eye_ik import enable_eye_ik, inspect_eye_ik
        from mmd_tools.converters.vmd_scene_collector import VmdSceneCollector
        from mmd_tools.io.vmd_exporter import VmdExporter
        root, _control, joints = create_eye_ik_case()
        edges = append_edges(joints[1:])
        row = enable_eye_ik(root)
        target = row['target']
        start = cmds.getAttr(target+'.translateX')
        for frame in range(1, 11):
            self.assertGreater(cmds.setKeyframe(target, attribute='translateX', time=frame,
                               value=start+.05*(frame-1)), 0)
        self.assertEqual(cmds.keyframe(target, attribute='translateX', query=True,
                                      timeChange=True), list(range(1, 11)))
        expected = {}
        for frame in range(1, 11):
            cmds.currentTime(frame, update=True)
            expected[frame] = pose(joints)
        self.assertGreater(max(abs(a-b) for a,b in zip(expected[1][0], expected[10][0])), 1e-3)
        with tempfile.TemporaryDirectory() as directory:
            scene = str(Path(directory)/'eye.ma')
            cmds.file(rename=scene)
            cmds.file(save=True, type='mayaAscii', force=True)
            cmds.file(new=True, force=True)
            cmds.file(scene, open=True, force=True)
            self.assertTrue(inspect_eye_ik(root)['active'])
            for frame in expected:
                cmds.currentTime(frame, update=True)
                self.assert_eye_pose(pose(joints), expected[frame])
            bake_mmd_control_rig(root)
            self.assertEqual(append_edges(joints[1:]), edges)
            for frame in expected:
                cmds.currentTime(frame, update=True)
                self.assert_eye_pose(pose(joints), expected[frame])
            output = str(Path(directory)/'eye.vmd')
            VmdExporter().export_vmd_animation(output, VmdSceneCollector().collect({'target_model': root}))
            fresh = import_mmd_file(str(MODEL), options=OPTIONS)
            self.assertTrue(import_mmd_file(output, options={'target_model': fresh, 'pmx_path': str(MODEL)}))
            for frame in expected:
                cmds.currentTime(frame, update=True)
                self.assert_eye_pose(pose(eye_joints(fresh)), expected[frame])

    def test_partial_bake_failure_restores_eye_graph_keys_and_metadata(self):
        from unittest import mock
        from mmd_tools.core import mmd_control_rig_motion as motion
        from mmd_tools.core.mmd_control_rig_eye_ik import enable_eye_ik, inspect_eye_ik
        from tests.integration.test_pose_layers import curve_snapshot
        root, _control, joints = create_eye_ik_case()
        row = enable_eye_ik(root)
        self.move_eye_target(row['target'])
        metadata = read_mmd_control_rig_metadata(root)
        curves = curve_snapshot()
        nodes = set(cmds.ls(uuid=True))
        expected = pose(joints)
        original = motion._commit_control_input

        def injected(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('injected eye bake failure')

        with mock.patch.object(motion, '_commit_control_input', side_effect=injected):
            with self.assertRaises(RuntimeError):
                bake_mmd_control_rig(root)
        self.assertTrue(inspect_eye_ik(root)['active'])
        self.assertEqual(read_mmd_control_rig_metadata(root), metadata)
        self.assertEqual(curve_snapshot(), curves)
        self.assertEqual(set(cmds.ls(uuid=True)), nodes)
        self.assert_eye_pose(pose(joints), expected)

    def test_arm_and_eye_ik_bake_together_preserves_both_target_motions(self):
        from mmd_tools.core.mmd_control_rig_arm_ik import enable_arm_ik, inspect_arm_ik
        from mmd_tools.core.mmd_control_rig_eye_ik import enable_eye_ik, inspect_eye_ik
        from tests.integration.test_control_rig_arm_ik import create_arm_case
        cmds.currentUnit(time='ntsc')
        root, _controls, arm_joints = create_arm_case()
        eyes = eye_joints(root)
        arm = enable_arm_ik(root, 'right')
        eye = enable_eye_ik(root)
        for target in (arm['target'], eye['target']):
            start = cmds.getAttr(target+'.translateX')
            for frame in range(1, 11):
                self.assertGreater(cmds.setKeyframe(target, attribute='translateX', time=frame,
                                   value=start+.02*(frame-1)), 0)
            self.assertEqual(cmds.keyframe(target, attribute='translateX', query=True,
                                          timeChange=True), list(range(1, 11)))
        selected = arm_joints + eyes
        expected = {}
        for frame in range(1, 11):
            cmds.currentTime(frame, update=True)
            expected[frame] = pose(selected)
        for index in (2, 4, 5):
            self.assertGreater(max(abs(a-b) for a,b in zip(expected[1][index], expected[10][index])), 1e-3)
        bake_mmd_control_rig(root)
        self.assertFalse(inspect_arm_ik(root, 'right')['active'])
        self.assertFalse(inspect_eye_ik(root)['active'])
        self.assertEqual(read_mmd_control_rig_metadata(root)['state'], 'BAKED')
        for frame in expected:
            cmds.currentTime(frame, update=True)
            self.assert_eye_pose(pose(selected), expected[frame])
