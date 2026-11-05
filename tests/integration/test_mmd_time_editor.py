"""Maya contracts for one model's reversible bone Time Editor clip."""
from unittest.mock import patch

from maya import cmds

from mmd_tools.core import mmd_time_editor as te
from mmd_tools.io.mmd_importer import import_mmd_file
from tests.common.maya_test_base import MayaTestBase
from tests.integration.test_control_rig_eye import ROOT, MODEL, OPTIONS


def create_case():
    """Import genuine VMD curves directly, in its native 30 fps time unit."""
    cmds.currentUnit(time="ntsc")
    root = import_mmd_file(str(MODEL), options=OPTIONS)
    path = ROOT / "tests/data/mmt_test_model_test_motion.vmd"
    assert import_mmd_file(str(path), options={"target_model": root, "pmx_path": str(MODEL),
                                               "use_animation_layers": False, "vmd_fps": 30})
    joints = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True)
    return root, joints


def curve_payload(uuids=None):
    """Snapshot original identities and all authored key/tangent data."""
    result = {}
    nodes = (cmds.ls(list(uuids)) or []) if uuids is not None else (cmds.ls(type="animCurve") or [])
    for node in nodes:
        result[cmds.ls(node, uuid=True)[0]] = {
            "times": cmds.keyframe(node, query=True, timeChange=True),
            "values": cmds.keyframe(node, query=True, valueChange=True),
            "tangents": {flag: cmds.keyTangent(node, query=True, **{flag: True})
                         for flag in ("inTangentType", "outTangentType", "inAngle", "outAngle",
                                      "inWeight", "outWeight", "lock", "weightLock", "weightedTangents")},
            "infinity": [cmds.getAttr(node + "." + attr) for attr in ("preInfinity", "postInfinity")],
            "rotationInterpolation": (cmds.getAttr(node + ".rotationInterpolation")
                                      if cmds.attributeQuery("rotationInterpolation", node=node, exists=True) else None),
        }
    return result


def poses(joints, frames=(0, 1, 2, 3, 4, 5)):
    """Sample without consuming the operation's Undo entry."""
    enabled = cmds.undoInfo(query=True, state=True)
    cmds.undoInfo(stateWithoutFlush=False)
    try:
        result = {}
        for frame in frames:
            cmds.currentTime(frame)
            result[frame] = [cmds.xform(j, query=True, worldSpace=True, matrix=True) for j in joints]
        return result
    finally:
        cmds.undoInfo(stateWithoutFlush=enabled)


def pose_error(a, b):
    return max(abs(x-y) for ma, mb in zip(a, b) for x, y in zip(ma, mb))


class TestMmdTimeEditor(MayaTestBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        version = str(cmds.about(version=True)).split(".", 1)[0]
        native = ROOT / "plug-ins" / version / "Debug" / "mmd_tools_cpp.mll"
        if not native.exists():
            raise RuntimeError("Build the Maya Debug C++ plugin before this integration test")
        cls.load_plugin(str(native))
        cls.load_plugin(str(ROOT / "mmd_tools/plugin_main.py"))
        cls.plugins_loaded.reverse()

    def assert_poses(self, actual, expected):
        for frame in expected:
            self.assertLess(pose_error(actual[frame], expected[frame]), 1e-4, str(frame))

    def test_clip_move_remove_recreate_preserves_curves_and_other_model(self):
        root, joints = create_case()
        other = import_mmd_file(str(MODEL), options=OPTIONS)
        others = cmds.listRelatives(other, allDescendents=True, type="joint", fullPath=True)
        original = curve_payload()
        baseline = poses(joints)
        untouched = poses(others)
        self.assertGreater(pose_error(baseline[0], baseline[5]), 1e-3)
        row = te.create_model_clip(root)
        self.assertEqual(row["inputCount"], 684)
        self.assertTrue(row["active"])
        self.assert_poses(poses(joints), baseline)
        cmds.timeEditorClip(edit=True, moveClip=20, clipId=row["clipId"])
        moved = poses(joints, [f + 20 for f in baseline])
        self.assert_poses({f: moved[f + 20] for f in baseline}, baseline)
        self.assert_poses(poses(others), untouched)
        # Time Editor owns copied curves while the original UUIDs stay intact.
        self.assertEqual(curve_payload(original), original)
        cmds.timeEditorClip(edit=True, mute=True, clipId=row["clipId"])
        self.assertTrue(cmds.timeEditorClip(row["clipId"], query=True, mute=True))
        # Native clip mute has no promise of falling back to scene animation.
        # Unmute must restore this clip, and removal while muted must restore
        # the original authoring inputs without changing another model.
        cmds.timeEditorClip(edit=True, mute=False, clipId=row["clipId"])
        unmuted = poses(joints, [f + 20 for f in baseline])
        self.assert_poses({f: unmuted[f + 20] for f in baseline}, baseline)
        cmds.timeEditorClip(edit=True, mute=True, clipId=row["clipId"])
        self.assertTrue(te.remove_model_clip(root))
        self.assertFalse(te.inspect_model_clip(root)["active"])
        self.assert_poses(poses(joints), baseline)
        self.assertEqual(curve_payload(), original)
        self.assertTrue(te.create_model_clip(root)["active"])
        self.assertTrue(te.remove_model_clip(root))
        self.assertEqual(curve_payload(), original)

    def test_fractional_fixture_motion_survives_clip_move_and_removal(self):
        """Cover intermediate times for the fixture's existing TT input curves."""
        root, joints = create_case()
        timing_curves = cmds.ls(type="animCurveTT") or []
        self.assertEqual(len(timing_curves), 9)
        self.assertTrue(all(cmds.keyframe(node, query=True, keyframeCount=True)
                            for node in timing_curves))
        original = curve_payload()
        frames = [index / 4.0 for index in range(21)]
        baseline = poses(joints, frames)
        self.assertGreater(pose_error(baseline[0.0], baseline[5.0]), 1e-3)
        row = te.create_model_clip(root)
        self.assert_poses(poses(joints, frames), baseline)
        cmds.timeEditorClip(edit=True, moveClip=20, clipId=row["clipId"])
        moved = poses(joints, [frame + 20 for frame in frames])
        self.assert_poses({frame: moved[frame + 20] for frame in frames}, baseline)
        self.assertEqual(curve_payload(original), original)
        self.assertTrue(te.remove_model_clip(root))
        self.assert_poses(poses(joints, frames), baseline)
        self.assertEqual(curve_payload(), original)

    def test_nonidentity_time_curve_is_rejected_without_scene_changes(self):
        root, joints = create_case()
        frames = [index / 4.0 for index in range(21)]
        linear_pose = poses(joints, frames)
        timing = next(node for node in cmds.ls(type="animCurveTT")
                      if node.endswith(":head_vmdRotationTime"))
        # Keep both endpoints while creating a real, monotonic time warp.
        cmds.setKeyframe(timing, time=2.5, value=0.5)
        cmds.keyTangent(timing, edit=True, inTangentType="linear", outTangentType="linear")
        self.assertGreater(abs(cmds.getAttr(timing + ".output", time=2.5) - 2.5), 0.1)
        baseline = poses(joints, frames)
        self.assertGreater(max(pose_error(baseline[f], linear_pose[f]) for f in frames), 1e-3)
        original = curve_payload()
        uuids = set(cmds.ls(uuid=True))
        curves = cmds.ls(type="animCurve") or []
        connections = {node: sorted(cmds.listConnections(node, source=True, destination=True,
                                                       plugs=True, connections=True) or [])
                       for node in curves}
        metadata_exists = cmds.attributeQuery(te.METADATA_ATTRIBUTE, node=root, exists=True)
        with self.assertRaises(te.MmdTimeEditorError):
            te.create_model_clip(root)
        self.assertEqual(set(cmds.ls(uuid=True)), uuids)
        self.assertEqual(curve_payload(), original)
        self.assertEqual({node: sorted(cmds.listConnections(node, source=True, destination=True,
                                                          plugs=True, connections=True) or [])
                          for node in curves}, connections)
        self.assertEqual(cmds.attributeQuery(te.METADATA_ATTRIBUTE, node=root, exists=True), metadata_exists)
        self.assertFalse(te.inspect_model_clip(root)["active"])
        self.assert_poses(poses(joints, frames), baseline)

    def test_create_and_remove_undo_redo(self):
        root, joints = create_case()
        baseline, original = poses(joints), curve_payload()
        te.create_model_clip(root)
        cmds.undo()
        self.assertFalse(te.inspect_model_clip(root)["active"])
        self.assertEqual(curve_payload(), original)
        cmds.redo()
        self.assertTrue(te.inspect_model_clip(root)["active"])
        te.remove_model_clip(root)
        cmds.undo()
        self.assertTrue(te.inspect_model_clip(root)["active"])
        cmds.redo()
        self.assertFalse(te.inspect_model_clip(root)["active"])
        self.assert_poses(poses(joints), baseline)
        self.assertEqual(curve_payload(), original)

    def test_nonbase_layer_and_existing_clip_rejected_without_change(self):
        root, _ = create_case()
        layer = cmds.animLayer("UserLayer")
        before, original = set(cmds.ls(uuid=True)), curve_payload()
        with self.assertRaises(te.MmdTimeEditorError):
            te.create_model_clip(root)
        self.assertEqual(set(cmds.ls(uuid=True)), before)
        self.assertEqual(curve_payload(), original)
        cmds.delete(layer)
        other, _ = create_case()
        te.create_model_clip(root)
        before = set(cmds.ls(uuid=True))
        with self.assertRaises(te.MmdTimeEditorError):
            te.create_model_clip(other)
        self.assertEqual(set(cmds.ls(uuid=True)), before)
        self.assertTrue(te.inspect_model_clip(root)["active"])
        self.assertFalse(te.inspect_model_clip(other)["active"])

    def test_control_rig_edit_rejected_without_change(self):
        from mmd_tools.core.mmd_control_rig_builder import build_mmd_control_rig, read_mmd_control_rig_metadata
        from mmd_tools.core.mmd_control_rig_motion import enter_mmd_control_rig_edit
        root, joints = create_case()
        build_mmd_control_rig(root)
        enter_mmd_control_rig_edit(root)
        before, original = set(cmds.ls(uuid=True)), curve_payload()
        baseline = poses(joints)
        metadata = read_mmd_control_rig_metadata(root)
        with self.assertRaises(te.MmdTimeEditorError):
            te.create_model_clip(root)
        self.assertEqual(set(cmds.ls(uuid=True)), before)
        self.assertEqual(curve_payload(), original)
        self.assertEqual(read_mmd_control_rig_metadata(root), metadata)
        self.assert_poses(poses(joints), baseline)

    def test_humanik_scene_rejected_without_change(self):
        root, joints = create_case()
        if "HIKCharacterNode" not in cmds.allNodeTypes():
            self.load_plugin("mayaHIK")
        character = cmds.createNode("HIKCharacterNode", name="UnrelatedHumanIK")
        before, original = set(cmds.ls(uuid=True)), curve_payload()
        baseline = poses(joints)
        with self.assertRaises(te.MmdTimeEditorError):
            te.create_model_clip(root)
        self.assertEqual(set(cmds.ls(uuid=True)), before)
        self.assertTrue(cmds.objExists(character))
        self.assertEqual(curve_payload(), original)
        self.assert_poses(poses(joints), baseline)

    def test_unknown_writer_rejected_without_change(self):
        root, joints = create_case()
        target = next(j + ".translateX" for j in joints
                      if cmds.connectionInfo(j + ".translateX", sourceFromDestination=True))
        foreign = cmds.createNode("multiplyDivide")
        cmds.connectAttr(foreign + ".outputX", target, force=True)
        before, original = set(cmds.ls(uuid=True)), curve_payload()
        with self.assertRaises(te.MmdTimeEditorError):
            te.create_model_clip(root)
        self.assertEqual(set(cmds.ls(uuid=True)), before)
        self.assertEqual(curve_payload(), original)
        self.assertEqual(cmds.connectionInfo(target, sourceFromDestination=True), foreign + ".outputX")

    def test_foreign_incoming_is_rejected_and_remains_connected(self):
        root, _ = create_case()
        foreign = cmds.createNode("network", name="UserOwnedDriver")
        original = curve_payload()
        row = te.create_model_clip(root)
        source = row["source"]
        cmds.addAttr(source, longName="userInput", attributeType="message")
        cmds.connectAttr(foreign + ".message", source + ".userInput")
        before = set(cmds.ls(uuid=True))
        curves = curve_payload()
        metadata = cmds.getAttr(root + "." + te.METADATA_ATTRIBUTE)
        with self.assertRaises(te.MmdTimeEditorError):
            te.remove_model_clip(root)
        self.assertEqual(set(cmds.ls(uuid=True)), before)
        self.assertEqual(curve_payload(), curves)
        self.assertEqual(cmds.getAttr(root + "." + te.METADATA_ATTRIBUTE), metadata)
        self.assertTrue(cmds.isConnected(foreign + ".message", source + ".userInput"))
        cmds.disconnectAttr(foreign + ".message", source + ".userInput")
        self.assertTrue(te.remove_model_clip(root))
        self.assertTrue(cmds.objExists(foreign))
        self.assertEqual(curve_payload(), original)

    def test_native_creation_failure_rolls_back_original_animation(self):
        root, joints = create_case()
        baseline, original = poses(joints), curve_payload()
        before = set(cmds.ls(uuid=True))
        original_command = cmds.timeEditorClip

        def fail_after_creation(*args, **kwargs):
            result = original_command(*args, **kwargs)
            if "animSource" in kwargs and not kwargs.get("query"):
                raise RuntimeError("injected after native clip creation")
            return result

        with patch.object(cmds, "timeEditorClip", side_effect=fail_after_creation):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                te.create_model_clip(root)
        # Maya's global Time Editor singleton may be initialized by the command.
        after = set(cmds.ls(uuid=True))
        self.assertTrue(before <= after)
        added = after - before
        self.assertTrue(all(cmds.nodeType(cmds.ls(uuid)[0]) == "timeEditor" for uuid in added))
        self.assertEqual(curve_payload(), original)
        self.assert_poses(poses(joints), baseline)
        self.assertFalse(te.inspect_model_clip(root)["active"])
