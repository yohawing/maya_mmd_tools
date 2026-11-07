"""Bounded Maya contracts for owned Control Rig arm IK in EDIT."""
from pathlib import Path

from maya import cmds

from mmd_tools.core.mmd_control_rig_arm_ik import enable_arm_ik, disable_arm_ik, inspect_arm_ik
from mmd_tools.core.mmd_control_rig_builder import (
    build_mmd_control_rig, read_mmd_control_rig_metadata, MmdControlRigBuildError,
)
from mmd_tools.core.mmd_control_rig_motion import enter_mmd_control_rig_edit
from tests.common.maya_test_base import MayaTestBase
from tests.integration.test_pose_layers import curve_snapshot

ROOT = Path(__file__).resolve().parents[2]


def create_arm_case(side='right'):
    from mmd_tools.io.mmd_importer import import_mmd_file
    root = import_mmd_file(str(ROOT/'tests/data/mmt_test_model.pmx'), options={
        'setup_rig': True, 'setup_bone_orientation': True, 'import_physics': False, 'use_namespace': True,
    })
    build_mmd_control_rig(root)
    enter_mmd_control_rig_edit(root)
    metadata = read_mmd_control_rig_metadata(root)
    roles = [side+suffix for suffix in ('_arm', '_elbow', '_wrist')]
    controls = [cmds.ls(metadata['controls'][role], long=True)[0] for role in roles]
    joints = [cmds.ls(metadata['bindings'][role]['jointUuid'], long=True)[0] for role in roles]
    for frame, value in ((1, 25), (10, 45)):
        cmds.setKeyframe(controls[1]+'.rotateZ', time=frame, value=value)
    cmds.currentTime(5, edit=True, update=True)
    return root, controls, joints


def arm_pose(joints):
    return [cmds.xform(joint, query=True, worldSpace=True, matrix=True) for joint in joints]


def pose_delta(a, b):
    return max(abs(x-y) for one,two in zip(a,b) for x,y in zip(one,two))


def move_target(row, joints):
    shoulder = cmds.xform(joints[0], query=True, worldSpace=True, translation=True)
    wrist = cmds.xform(row['target'], query=True, worldSpace=True, translation=True)
    target = [w+(s-w)*.1 for s,w in zip(shoulder,wrist)]
    cmds.xform(row['target'], worldSpace=True, translation=target)
    cmds.currentTime(cmds.currentTime(query=True), edit=True, update=True)
    return target


class TestControlRigArmIk(MayaTestBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.load_plugin(str(ROOT/'mmd_tools/plugin_main.py'))

    def test_enable_live_target_pole_and_disable_restore_fk(self):
        root, controls, joints = create_arm_case()
        before = arm_pose(joints)
        curves = curve_snapshot()
        writer = cmds.connectionInfo(controls[1]+'.rotateZ', sourceFromDestination=True)
        row = enable_arm_ik(root, 'right')
        cmds.currentTime(5, edit=True, update=True)
        self.assertLess(pose_delta(before, arm_pose(joints)), 1e-4)
        target = move_target(row, joints)
        actual = cmds.xform(joints[2], query=True, worldSpace=True, translation=True)
        self.assertLess(max(abs(a-b) for a,b in zip(actual,target)), 1e-4)
        elbow = cmds.xform(joints[1], query=True, worldSpace=True, translation=True)
        cmds.move(0, 0, 1, row['pole'], relative=True, worldSpace=True)
        cmds.currentTime(5, edit=True, update=True)
        moved = cmds.xform(joints[1], query=True, worldSpace=True, translation=True)
        self.assertGreater(max(abs(a-b) for a,b in zip(elbow,moved)), 1e-4)
        disable_arm_ik(root, 'right')
        cmds.currentTime(5, edit=True, update=True)
        self.assertLess(pose_delta(before, arm_pose(joints)), 1e-4)
        self.assertEqual(cmds.connectionInfo(controls[1]+'.rotateZ', sourceFromDestination=True), writer)
        self.assertEqual(curve_snapshot(), curves)

    def test_match_fk_preserves_pose_and_undo_redo(self):
        root, _controls, joints = create_arm_case()
        row = enable_arm_ik(root, 'right')
        move_target(row, joints)
        expected = arm_pose(joints)
        disable_arm_ik(root, 'right', match_fk=True)
        self.assertLess(pose_delta(expected, arm_pose(joints)), 1e-4)

        self.assertFalse(inspect_arm_ik(root, 'right')['active'])
        cmds.undo()
        self.assertTrue(inspect_arm_ik(root, 'right')['active'])
        self.assertLess(pose_delta(expected, arm_pose(joints)), 1e-4)
        cmds.redo()
        self.assertFalse(inspect_arm_ik(root, 'right')['active'])
        self.assertLess(pose_delta(expected, arm_pose(joints)), 1e-4)

    def test_straight_fk_and_animated_target_rematch_preserve_other_keys(self):
        root, controls, joints = create_arm_case()
        cmds.cutKey(controls[1]+'.rotateZ', clear=True)
        cmds.setAttr(controls[1]+'.rotateZ', 0)
        cmds.currentTime(5, edit=True, update=True)
        straight = arm_pose(joints)
        row = enable_arm_ik(root, 'right')
        self.assertLess(pose_delta(straight, arm_pose(joints)), 1e-4)
        for node in (row['target'], row['pole']):
            for frame in (1, 10):
                cmds.setKeyframe(node, attribute='translate', time=frame)
        target = move_target(row, joints)
        for axis, value in zip('XYZ', target):
            cmds.setKeyframe(row['target'], attribute='translate'+axis, time=5, value=value)
        cmds.currentTime(5, edit=True, update=True)
        expected = arm_pose(joints)
        self.assertGreater(pose_delta(straight, expected), 1e-4)
        disable_arm_ik(root, 'right', match_fk=True)
        self.assertLess(pose_delta(expected, arm_pose(joints)), 1e-4, 'animated target match FK')

        def endpoints():
            return {(node, axis, frame): cmds.keyframe(node, attribute='translate'+axis,
                    query=True, time=(frame, frame), valueChange=True)
                    for node in (row['target'], row['pole']) for axis in 'XYZ' for frame in (1, 10)}

        original_keys = endpoints()
        enable_arm_ik(root, 'right')
        self.assertLess(pose_delta(expected, arm_pose(joints)), 1e-4)
        self.assertEqual(endpoints(), original_keys)

    def test_animated_intermediate_twists_preserve_segments_and_lifecycle(self):
        """Off-axis intermediate twists must move helper offsets without a world cycle."""
        import tempfile
        from mmd_tools.core.mmd_control_rig_builder import remove_mmd_control_rig
        from mmd_tools.core.mmd_control_rig_motion import restore_mmd_control_rig_attached

        for inward in (False, True):
            with self.subTest(inward=inward):
                cmds.file(new=True, force=True)
                root, _controls, joints = create_arm_case()
                metadata = read_mmd_control_rig_metadata(root)
                for role, values in (("right_arm_twist", (20, 50)), ("right_wrist_twist", (-15, -35))):
                    control = cmds.ls(metadata["controls"][role], long=True)[0]
                    for frame, value in zip((1, 10), values):
                        cmds.setKeyframe(control + ".rotateZ", time=frame, value=value)
                    cmds.keyTangent(control + ".rotateZ", inTangentType="linear", outTangentType="linear")
                cmds.currentTime(1, update=True)
                cmds.currentTime(5, update=True)
                cycles = set(cmds.cycleCheck(all=True, list=True) or [])
                row = enable_arm_ik(root, "right")
                target = move_target(row, joints) if inward else cmds.xform(row["target"], query=True, worldSpace=True, translation=True)
                for frame in (1, 10):
                    for axis, value in zip("XYZ", target):
                        cmds.setKeyframe(row["target"], attribute="translate" + axis, time=frame, value=value)
                cmds.currentTime(5, update=True)
                expected = arm_pose(joints)
                disable_arm_ik(root, "right", match_fk=True)
                self.assertLess(pose_delta(expected, arm_pose(joints)), 1e-4)
                enable_arm_ik(root, "right")
                self.assertLess(pose_delta(expected, arm_pose(joints)), 1e-4)
                cmds.undo()
                self.assertFalse(inspect_arm_ik(root, "right")["active"])
                self.assertLess(pose_delta(expected, arm_pose(joints)), 1e-4)
                cmds.redo()
                self.assertTrue(inspect_arm_ik(root, "right")["active"])
                row = inspect_arm_ik(root, "right")
                helpers = [cmds.ls(uid, long=True)[0] for uid in row["helperUuids"]]
                frames = {}
                for frame in range(1, 11):
                    cmds.currentTime(frame, update=True)
                    frames[frame] = arm_pose(joints)
                    self.assertLess(pose_delta(frames[frame], arm_pose(helpers)), 1e-4, "helper frame " + str(frame))
                    wrist = cmds.xform(joints[-1], query=True, worldSpace=True, translation=True)
                    self.assertLess(max(abs(a-b) for a,b in zip(target, wrist)), 1e-4, "target frame " + str(frame))
                self.assertEqual(set(cmds.cycleCheck(all=True, list=True) or []) - cycles, set())
                with tempfile.TemporaryDirectory() as directory:
                    scene = str(Path(directory) / "twisted_arm.ma")
                    cmds.file(rename=scene)
                    cmds.file(save=True, type="mayaAscii", force=True)
                    cmds.file(scene, open=True, force=True)
                    self.assertTrue(inspect_arm_ik(root, "right")["active"])
                    for frame, pose in frames.items():
                        cmds.currentTime(frame, update=True)
                        self.assertLess(pose_delta(pose, arm_pose(joints)), 1e-4, "reload frame " + str(frame))
                owned = inspect_arm_ik(root, "right")["nodes"]
                restore_mmd_control_rig_attached(root)
                remove_mmd_control_rig(root)
                self.assertTrue(all(not cmds.ls(uid, long=True) for uid in owned))

    def test_intermediate_append_feedback_is_rejected_without_scene_changes(self):
        """A main-arm grant on an intermediate twist would feed IK back into itself."""
        from mmd_tools.io.mmd_importer import import_mmd_file
        for custom_name in (None, "custom_twist"):
            cmds.file(new=True, force=True)
            root = import_mmd_file(str(ROOT / "tests/data/mmt_test_model.pmx"), options={
                "setup_rig": True, "setup_bone_orientation": True,
                "import_physics": False, "use_namespace": True,
            })
            joints = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True)
            by_name = {cmds.getAttr(joint + ".mmd_bone_name"): joint for joint in joints}
            if custom_name:
                cmds.setAttr(by_name["右腕捩"] + ".mmd_bone_name", custom_name, type="string")
            append = cmds.createNode("mmdAppend", name="armTwistFromMainArm")
            cmds.setAttr(append + ".schemaMode", 2)
            cmds.setAttr(append + ".ratio", 0.5)
            cmds.connectAttr(by_name["右腕"] + ".rotate", append + ".sourceRotate")
            cmds.connectAttr(append + ".outputRotate", by_name["右腕捩"] + ".rotate", force=True)
            build_mmd_control_rig(root)
            enter_mmd_control_rig_edit(root)
            metadata = read_mmd_control_rig_metadata(root)
            if custom_name:
                self.assertNotIn("right_arm_twist", metadata["bindings"])
            else:
                self.assertEqual(metadata["bindings"]["right_arm_twist"]["inputKind"], "append_base")
            def snapshot():
                return {
                    "nodes": set(cmds.ls(uuid=True)),
                    "metadata": read_mmd_control_rig_metadata(root),
                    "pose": arm_pose(joints),
                    "append_connections": cmds.listConnections(append, connections=True, plugs=True),
                    "curves": curve_snapshot(),
                }
            before = snapshot()
            with self.assertRaisesRegex(MmdControlRigBuildError, "direct intermediate bone routes"):
                enable_arm_ik(root, "right")
            self.assertEqual(snapshot(), before)

    def test_custom_static_intermediate_accepts_then_rejects_append_route_change(self):
        from mmd_tools.io.mmd_importer import import_mmd_file
        root = import_mmd_file(str(ROOT / "tests/data/mmt_test_model.pmx"), options={
            "setup_rig": True, "setup_bone_orientation": True,
            "import_physics": False, "use_namespace": True,
        })
        joints = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True)
        by_name = {cmds.getAttr(joint + ".mmd_bone_name"): joint for joint in joints}
        twist = by_name["右腕捩"]
        cmds.setAttr(twist + ".mmd_bone_name", "custom_twist", type="string")
        build_mmd_control_rig(root)
        enter_mmd_control_rig_edit(root)
        self.assertNotIn("right_arm_twist", read_mmd_control_rig_metadata(root)["bindings"])
        enable_arm_ik(root, "right")
        disable_arm_ik(root, "right")
        append = cmds.createNode("mmdAppend", name="changedIntermediateAppend")
        cmds.setAttr(append + ".schemaMode", 2)
        cmds.setAttr(append + ".ratio", 0.5)
        cmds.connectAttr(by_name["右腕"] + ".rotate", append + ".sourceRotate")
        cmds.connectAttr(append + ".outputRotate", twist + ".rotate", force=True)
        metadata = read_mmd_control_rig_metadata(root)
        nodes = set(cmds.ls(uuid=True))
        pose = arm_pose(joints)
        with self.assertRaisesRegex(MmdControlRigBuildError, "direct intermediate bone routes"):
            enable_arm_ik(root, "right")
        self.assertEqual(read_mmd_control_rig_metadata(root), metadata)
        self.assertEqual(set(cmds.ls(uuid=True)), nodes)
        self.assertEqual(arm_pose(joints), pose)
        self.assertTrue(cmds.isConnected(append + ".outputRotate", twist + ".rotate"))

    def test_foreign_writer_rejects_without_touching_other_model(self):
        root, controls, joints = create_arm_case()
        other_root, _other_controls, other_joints = create_arm_case('left')
        other_before = arm_pose(other_joints)
        other_metadata = read_mmd_control_rig_metadata(other_root)
        foreign = cmds.createNode('plusMinusAverage', name='ForeignArmWriter')
        plug = controls[0]+'.rotateX'
        original = cmds.getAttr(plug)
        cmds.connectAttr(foreign+'.output1D', plug)
        foreign_input = cmds.connectionInfo(plug, sourceFromDestination=True)
        with self.assertRaises(MmdControlRigBuildError):
            enable_arm_ik(root, 'right')
        self.assertEqual(cmds.connectionInfo(plug, sourceFromDestination=True), foreign_input)
        self.assertEqual(arm_pose(other_joints), other_before)
        cmds.disconnectAttr(foreign_input, plug)
        cmds.setAttr(plug, original)
        self.assertEqual(read_mmd_control_rig_metadata(other_root), other_metadata)

    def test_missing_graph_rejects_without_touching_other_model(self):
        root, _controls, joints = create_arm_case()
        other_root, _other_controls, other_joints = create_arm_case('left')
        other_before = arm_pose(other_joints)
        other_metadata = read_mmd_control_rig_metadata(other_root)
        row = enable_arm_ik(root, 'right')
        cmds.delete(row['pole'])
        damaged = arm_pose(joints)
        with self.assertRaises(MmdControlRigBuildError):
            disable_arm_ik(root, 'right')
        self.assertEqual(arm_pose(joints), damaged)
        self.assertEqual(arm_pose(other_joints), other_before)
        self.assertEqual(read_mmd_control_rig_metadata(other_root), other_metadata)

    def test_inactive_arm_with_reconnected_output_rejects_inspect_and_disable(self):
        from mmd_tools.core.mmd_control_rig_arm_ik import resolve_arm_plug
        root, _controls, _joints = create_arm_case()
        enable_arm_ik(root, 'right')
        disable_arm_ik(root, 'right')
        metadata = read_mmd_control_rig_metadata(root)
        item = metadata['armIk']['right']['inputs'][0]
        source = resolve_arm_plug(cmds, item['outputRef'])
        destination = resolve_arm_plug(cmds, item['controlRef'])
        cmds.connectAttr(source, destination, force=True)
        for action in (inspect_arm_ik, disable_arm_ik):
            with self.assertRaises(MmdControlRigBuildError):
                action(root, 'right')
            self.assertTrue(cmds.isConnected(source, destination))
            self.assertEqual(read_mmd_control_rig_metadata(root), metadata)

    def test_all_created_constraints_are_owned_and_removed(self):
        from mmd_tools.core.mmd_control_rig_motion import restore_mmd_control_rig_attached
        from mmd_tools.core.mmd_control_rig_builder import remove_mmd_control_rig
        root, _controls, _joints = create_arm_case()
        def constraint_uuids():
            return {cmds.ls(node, uuid=True)[0] for node in cmds.ls(type='constraint') or []}

        before = constraint_uuids()
        enable_arm_ik(root, 'right')
        created = constraint_uuids() - before
        self.assertTrue(created)
        metadata = read_mmd_control_rig_metadata(root)
        owned = {item['uuid'] for item in metadata['nodes']}
        self.assertFalse(created - owned)
        disable_arm_ik(root, 'right')
        restore_mmd_control_rig_attached(root)
        remove_mmd_control_rig(root)
        self.assertFalse(created & set(cmds.ls(uuid=True)))

    def test_left_arm_follows_torso_and_ascii_reload(self):
        import tempfile
        root, _controls, joints = create_arm_case('left')
        row = enable_arm_ik(root, 'left')
        move_target(row, joints)
        metadata = read_mmd_control_rig_metadata(root)
        center = cmds.ls(metadata['controls']['center'], long=True)[0]
        cmds.setAttr(center+'.translateX', 1.0)
        cmds.currentTime(5, edit=True, update=True)
        target = cmds.xform(row['target'], query=True, worldSpace=True, translation=True)
        wrist = cmds.xform(joints[2], query=True, worldSpace=True, translation=True)
        self.assertLess(max(abs(a-b) for a,b in zip(target,wrist)), 1e-4)
        expected = arm_pose(joints)
        with tempfile.TemporaryDirectory() as directory:
            scene = str(Path(directory)/'arm.ma')
            cmds.file(rename=scene)
            cmds.file(save=True, type='mayaAscii', force=True)
            cmds.file(new=True, force=True)
            cmds.file(scene, open=True, force=True)
            self.assertTrue(inspect_arm_ik(root, 'left')['active'])
            self.assertLess(pose_delta(expected, arm_pose(joints)), 1e-4)
            disable_arm_ik(root, 'left', match_fk=True)
            self.assertLess(pose_delta(expected, arm_pose(joints)), 1e-4)

    def test_bake_preserves_all_animated_arm_frames(self):
        from mmd_tools.core.mmd_control_rig_motion import bake_mmd_control_rig
        cmds.currentUnit(time='ntsc')
        root, _controls, joints = create_arm_case()
        metadata = read_mmd_control_rig_metadata(root)
        for role, values in (("right_arm_twist", (20, 50)), ("right_wrist_twist", (-15, -35))):
            control = cmds.ls(metadata["controls"][role], long=True)[0]
            for frame, value in zip((1, 10), values):
                cmds.setKeyframe(control + ".rotateZ", time=frame, value=value)
        cmds.currentTime(5, update=True)
        row = enable_arm_ik(root, "right")
        start = cmds.xform(row["target"], query=True, worldSpace=True, translation=True)
        end = move_target(row, joints)
        self.assertGreater(max(abs(a-b) for a, b in zip(start, end)), 0.01)
        for frame, position in ((1, start), (10, end)):
            for axis, value in zip("XYZ", position):
                cmds.setKeyframe(row["target"], attribute="translate" + axis, time=frame, value=value)
        for role in ("right_arm_twist", "right_wrist_twist"):
            joints.append(cmds.ls(metadata["bindings"][role]["jointUuid"], long=True)[0])
        expected = {}
        for frame in range(1, 11):
            cmds.currentTime(frame, edit=True, update=True)
            expected[frame] = arm_pose(joints)
        baked = bake_mmd_control_rig(root)
        self.assertEqual(baked['state'], 'BAKED')
        for frame, pose in expected.items():
            cmds.currentTime(frame, edit=True, update=True)
            self.assertLess(pose_delta(pose, arm_pose(joints)), 1e-4, 'frame '+str(frame))
        from mmd_tools.converters.vmd_scene_collector import VmdSceneCollector
        from mmd_tools.io.vmd_exporter import VmdExporter
        from mmd_tools.io.mmd_importer import import_mmd_file
        names = [cmds.getAttr(joint+'.mmd_bone_name') for joint in joints]
        output = self.get_temp_filename('arm_ik_baked.vmd')
        VmdExporter().export_vmd_animation(output, VmdSceneCollector().collect({'target_model': root}))
        fresh = import_mmd_file(str(ROOT/'tests/data/mmt_test_model.pmx'), options={
            'setup_rig': True, 'setup_bone_orientation': True, 'import_physics': False, 'use_namespace': True,
        })
        self.assertTrue(import_mmd_file(output, options={'target_model': fresh,
                                                       'pmx_path': str(ROOT/'tests/data/mmt_test_model.pmx')}))
        fresh_joints = cmds.listRelatives(fresh, allDescendents=True, type='joint', fullPath=True) or []
        by_name = {cmds.getAttr(j+'.mmd_bone_name'): j for j in fresh_joints}
        for frame, pose in expected.items():
            cmds.currentTime(frame, edit=True, update=True)
            self.assertLess(pose_delta(pose, arm_pose([by_name[n] for n in names])), 1e-4,
                            'VMD reimport frame '+str(frame))

    def test_default_bake_range_includes_animated_torso(self):
        from mmd_tools.core.mmd_control_rig_motion import bake_mmd_control_rig
        root, _controls, joints = create_arm_case()
        enable_arm_ik(root, 'right')
        metadata = read_mmd_control_rig_metadata(root)
        torso = cmds.ls(metadata['controls']['upper_body'], long=True)[0]
        cmds.setKeyframe(torso+'.rotateZ', time=1, value=0)
        cmds.setKeyframe(torso+'.rotateZ', time=20, value=20)
        expected = {}
        for frame in range(1, 21):
            cmds.currentTime(frame, edit=True, update=True)
            expected[frame] = arm_pose(joints)
        bake_mmd_control_rig(root)
        for frame, pose in expected.items():
            cmds.currentTime(frame, edit=True, update=True)
            self.assertLess(pose_delta(pose, arm_pose(joints)), 1e-4, 'frame '+str(frame))

    def test_bake_partial_failure_restores_live_graph_and_keys(self):
        from unittest import mock
        from mmd_tools.core import mmd_control_rig_motion as motion
        root, _controls, joints = create_arm_case()
        enable_arm_ik(root, 'right')
        before_metadata = read_mmd_control_rig_metadata(root)
        before_curves = curve_snapshot()
        before_nodes = set(cmds.ls(uuid=True))
        before_pose = arm_pose(joints)
        original = motion._commit_control_input

        def fail_after_commit(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('injected partial arm bake')

        with mock.patch.object(motion, '_commit_control_input', side_effect=fail_after_commit):
            with self.assertRaises(RuntimeError):
                motion.bake_mmd_control_rig(root, frame_range=(1, 10))
        self.assertEqual(read_mmd_control_rig_metadata(root), before_metadata)
        self.assertEqual(curve_snapshot(), before_curves)
        self.assertEqual(set(cmds.ls(uuid=True)), before_nodes)
        self.assertTrue(inspect_arm_ik(root, 'right')['active'])
        self.assertLess(pose_delta(before_pose, arm_pose(joints)), 1e-4)
        restored = motion.restore_mmd_control_rig_attached(root)
        self.assertEqual(restored['state'], 'ATTACHED')
        self.assertFalse(inspect_arm_ik(root, 'right')['active'])
        from mmd_tools.core.mmd_control_rig_builder import remove_mmd_control_rig
        arm_nodes = before_metadata['armIk']['right']['nodes']
        self.assertTrue(remove_mmd_control_rig(root))
        self.assertFalse(set(cmds.ls(uuid=True)) & set(arm_nodes))
        self.assertIsNone(read_mmd_control_rig_metadata(root))

    def test_partial_bake_rejects_outside_fk_keys_without_mutation(self):
        from mmd_tools.core.mmd_control_rig_motion import bake_mmd_control_rig
        root, _controls, joints = create_arm_case()
        enable_arm_ik(root, 'right')
        metadata = read_mmd_control_rig_metadata(root)
        curves = curve_snapshot()
        nodes = set(cmds.ls(uuid=True))
        pose = arm_pose(joints)
        with self.assertRaises(MmdControlRigBuildError):
            bake_mmd_control_rig(root, frame_range=(3, 7))
        self.assertEqual(read_mmd_control_rig_metadata(root), metadata)
        self.assertEqual(curve_snapshot(), curves)
        self.assertEqual(set(cmds.ls(uuid=True)), nodes)
        self.assertLess(pose_delta(pose, arm_pose(joints)), 1e-4)


if __name__ == '__main__':
    import unittest
    unittest.main()
