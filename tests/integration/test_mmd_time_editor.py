"""Maya contracts for adding VMD model motion as baked Time Editor clips."""
from pathlib import Path
from unittest.mock import patch

from maya import cmds

from mmd_tools.core import mmd_time_editor as te
from mmd_tools.core.exceptions import MMDImportException
from mmd_tools.core.vmd_data import VmdData
from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame
from mmd_tools.core.vmd_data.camera_frame import VmdCameraFrame
from mmd_tools.core.vmd_data.morph_frame import VmdMorphFrame
from mmd_tools.io import vmd_importer
from mmd_tools.io.mmd_importer import import_mmd_file
from tests.common.maya_test_base import MayaTestBase

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "tests/data/mmt_test_model.pmx"
VMD = ROOT / "tests/data/mmt_test_model_test_motion.vmd"
OPTIONS = {"setup_rig": True, "setup_bone_orientation": True, "import_physics": False,
           "use_namespace": True}
VMD_OPTIONS = {"pmx_path": str(MODEL), "vmd_fps": 30}
FRAMES = (0, 1, 2, 3, 4, 5)
MORPH_MODEL = ROOT / "tests/data/test_morph_model.pmx"
MORPH_VMD = "bone_and_morph.vmd"  # in-memory VmdData; the path only names the clip
MORPH_VMD_OPTIONS = {"pmx_path": str(MORPH_MODEL), "vmd_fps": 30}
# Camera keys at 0, 1 (a cut), 15, 30, 45 and 60.
CAMERA_VMD = ROOT / "tests/data/camera_motion/camera-edge-generated.vmd"
OTHER_CAMERA_VMD = ROOT / "tests/data/camera_motion/camera-interpolation-isolated.vmd"
CAMERA_OPTIONS = {"vmd_fps": 30, "use_animation_layers": False}
CAMERA_FRAMES = (0, 1, 2, 8, 15, 22, 30, 37, 45, 52, 60)


def import_model():
    cmds.currentUnit(time="ntsc")
    root = import_mmd_file(str(MODEL), options=OPTIONS)
    return root, cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True)


def import_scene_vmd(root):
    """Ordinary (non-clip) VMD import onto the model's scene curves."""
    options = dict(VMD_OPTIONS, target_model=root, use_animation_layers=False)
    assert import_mmd_file(str(VMD), options=options)


def add_clip(root, **kwargs):
    return te.import_vmd_clip(root, VmdData().parse_file(str(VMD)), str(VMD), dict(VMD_OPTIONS), **kwargs)


def bone_and_morph_vmd():
    """In-memory VMD keying the morph model's only bone and both morphs."""
    data = VmdData()
    for frame_number, position, rotation in ((0, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)),
                                             (5, (2.0, 1.0, 0.0), (0.0, 0.3826834, 0.0, 0.9238795))):
        frame = VmdBoneFrame()
        frame.bone_name, frame.frame_number = "全ての親", frame_number
        frame.position, frame.rotation = position, rotation
        data.bone_frames.append(frame)
    for morph_name, frame_number, value in (("笑い", 0, 0.0), ("笑い", 5, 1.0), ("怒り", 1, 0.8), ("怒り", 4, 0.2)):
        frame = VmdMorphFrame()
        frame.morph_name, frame.frame_number, frame.value = morph_name, frame_number, value
        data.morph_frames.append(frame)
    return data


def import_morph_model():
    cmds.currentUnit(time="ntsc")
    root = import_mmd_file(str(MORPH_MODEL), options=OPTIONS)
    return root, cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True)


def add_morph_clip(root, **kwargs):
    return te.import_vmd_clip(root, bone_and_morph_vmd(), MORPH_VMD, dict(MORPH_VMD_OPTIONS), **kwargs)


def morph_only_vmd():
    data = bone_and_morph_vmd()
    data.bone_frames = []
    return data


def import_camera(path, **options):
    """Camera VMD import (ordinary keys, or a clip with ``time_editor_clip=True``)."""
    cmds.currentUnit(time="ntsc")
    return import_mmd_file(str(path), options=dict(CAMERA_OPTIONS, **options))


def camera_samples(frames=CAMERA_FRAMES):
    """MMD camera world matrix plus focal length and orthographic width per frame."""
    camera = cmds.ls("*.mmd_camera", objectsOnly=True, long=True)[0]
    shape = cmds.listRelatives(camera, shapes=True, type="camera", fullPath=True)[0]
    result = {}
    for frame in frames:
        cmds.currentTime(frame)
        result[frame] = [cmds.xform(camera, query=True, worldSpace=True, matrix=True),
                         [cmds.getAttr(shape + ".focalLength"), cmds.getAttr(shape + ".orthographicWidth")]]
    return result


def morph_samples(root, joints, frames=FRAMES):
    """Joint world matrices, morph controller inputs and final blendShape weights per frame."""
    controller = cmds.listConnections(root + ".mmd_morph_controller", source=True, destination=False)[0]
    blend_shape = cmds.ls(type="blendShape")[0]
    result = {}
    for frame in frames:
        cmds.currentTime(frame)
        result[frame] = [cmds.xform(j, query=True, worldSpace=True, matrix=True) for j in joints] + [
            [cmds.getAttr("%s.inputWeight[%d]" % (controller, i)) for i in (0, 1)],
            [cmds.getAttr(blend_shape + "." + alias) for alias in ("smile", "angry")],
        ]
    return result


def poses(joints, frames=FRAMES):
    """Sample world matrices by moving the current time (Time Editor ignores getAttr(time=))."""
    result = {}
    for frame in frames:
        cmds.currentTime(frame)
        result[frame] = [cmds.xform(j, query=True, worldSpace=True, matrix=True) for j in joints]
    return result


def pose_error(a, b):
    return max(abs(x - y) for ma, mb in zip(a, b) for x, y in zip(ma, mb))


def curve_payload():
    """Keys and connections of every animCurve outside Time Editor sources, keyed by UUID."""
    result = {}
    for node in cmds.ls(type="animCurve") or []:
        if cmds.listConnections(node, type="timeEditorAnimSource"):
            continue
        result[cmds.ls(node, uuid=True)[0]] = (
            cmds.keyframe(node, query=True, timeChange=True),
            cmds.keyframe(node, query=True, valueChange=True),
            sorted(cmds.listConnections(node, plugs=True, connections=True, source=True, destination=True) or []),
        )
    return result


def writers():
    return sorted((node, plug) for node in cmds.ls(type="timeEditorInterpolator")
                  for plug in cmds.listConnections(node, source=False, destination=True, plugs=True) or [])


class TestMmdTimeEditor(MayaTestBase):
    _baseline = None
    _morph_baseline = None
    _camera_baselines = {}

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

    def baseline(self):
        """World poses of an ordinary VMD import, computed once per class."""
        if TestMmdTimeEditor._baseline is None:
            root, joints = import_model()
            import_scene_vmd(root)
            TestMmdTimeEditor._baseline = poses(joints)
            cmds.file(new=True, force=True)
        return TestMmdTimeEditor._baseline

    def assert_matches(self, joints, offset=0):
        base = self.baseline()
        actual = poses(joints, [frame + offset for frame in FRAMES])
        for frame in FRAMES:
            self.assertLess(pose_error(actual[frame + offset], base[frame]), 1e-4, str(frame + offset))

    def morph_baseline(self):
        """Bone poses and morph weights of an ordinary import of the in-memory VMD."""
        if TestMmdTimeEditor._morph_baseline is None:
            root, joints = import_morph_model()
            options = dict(MORPH_VMD_OPTIONS, target_model=root, use_animation_layers=False)
            vmd_importer.import_vmd_file(bone_and_morph_vmd(), MORPH_VMD, options)
            TestMmdTimeEditor._morph_baseline = morph_samples(root, joints)
            cmds.file(new=True, force=True)
        return TestMmdTimeEditor._morph_baseline

    def assert_morph_matches(self, root, joints, offset=0):
        base = self.morph_baseline()
        actual = morph_samples(root, joints, [frame + offset for frame in FRAMES])
        for frame in FRAMES:
            self.assertLess(pose_error(actual[frame + offset], base[frame]), 1e-4, str(frame + offset))

    def camera_baseline(self, path):
        """Camera samples of an ordinary camera VMD import, computed once per file."""
        if path not in TestMmdTimeEditor._camera_baselines:
            cmds.file(new=True, force=True)
            self.assertTrue(import_camera(path))
            TestMmdTimeEditor._camera_baselines[path] = camera_samples()
            cmds.file(new=True, force=True)
        return TestMmdTimeEditor._camera_baselines[path]

    def assert_camera_matches(self, path, offset=0):
        base = self.camera_baseline(path)
        actual = camera_samples([frame + offset for frame in CAMERA_FRAMES])
        for frame in CAMERA_FRAMES:
            self.assertLess(pose_error(actual[frame + offset], base[frame]), 1e-3, str(frame + offset))

    def test_bake_falls_back_for_mixed_drivers(self):
        target = cmds.createNode("transform")
        x, y = target + ".translateX", target + ".translateY"
        for frame, value in ((1, 2), (3, 6)):
            cmds.setKeyframe(x, time=frame, value=value)
        cmds.connectAttr(x, y)
        plugs = [x, y]
        frames = range(1, 4)
        expected = [cmds.getAttr(p, time=t) for p in plugs for t in frames]
        original = cmds.connectionInfo(x, sourceFromDestination=True)
        bake = cmds.bakeResults

        def fallback(*args, **kwargs):
            self.assertEqual(cmds.connectionInfo(x, sourceFromDestination=True), original)
            return bake(*args, **kwargs)

        with patch.object(cmds, "bakeResults", side_effect=fallback) as called:
            te._bake_curves(plugs, 1, 3)
        called.assert_called_once()
        self.assertEqual([cmds.getAttr(p, time=t) for p in plugs for t in frames], expected)

    def test_physics_model_targets_driver_inputs(self):
        """Physics drivers own their joints, so the clip keys the drivers' pre-physics inputs."""
        cmds.currentUnit(time="ntsc")
        root = import_mmd_file(str(ROOT / "tests/data/yw_test_model.pmx"),
                               options={"import_physics": True, "create_mmd_shaders": False, "setup_rig": True})
        # Normal import may move this input onto an upstream authoring transform.
        driver = cmds.ls(type="mmdPhysicsBoneDriver")[0]
        cmds.setKeyframe(driver + ".inPreRotateX", time=0, value=5)
        result = add_clip(root)
        plugs = cmds.timeEditorAnimSource(result["source"], query=True, targets=True)
        pre = driver + ".inPreRotateX"
        upstream = cmds.connectionInfo(pre, sourceFromDestination=True)
        self.assertTrue(upstream)
        self.assertTrue(pre in plugs or upstream in plugs, upstream)
        self.assertGreater(result["targets"], 0)
        self.assertTrue(te.is_time_editor_driven(root))

    def test_first_clip_moves_all_targets_into_source(self):
        self.baseline()
        root, joints = import_model()
        result = import_mmd_file(str(VMD), options=dict(VMD_OPTIONS, target_model=root, time_editor_clip=True))
        self.assertEqual(result["targets"], 684)
        self.assertEqual(result["clip"], "mmt_test_model_test_motion")
        self.assertEqual(curve_payload(), {})
        self.assertEqual(cmds.ls(type="animCurveTT"), [])
        self.assertTrue(te.is_time_editor_driven(root))
        self.assert_matches(joints)
        cmds.timeEditorClip(edit=True, moveClip=20, clipId=result["clipId"])
        self.assert_matches(joints, 20)

    def test_clip_preserves_baked_rotation_at_integer_frame(self):
        root, joints = import_morph_model()
        options = dict(MORPH_VMD_OPTIONS, target_model=root, use_animation_layers=False)
        vmd_importer.import_vmd_file(bone_and_morph_vmd(), MORPH_VMD, options)
        cmds.currentTime(5)
        expected = cmds.getAttr(joints[0] + ".rotate")[0]
        self.assertGreater(max(map(abs, expected)), 1.0)

        cmds.file(new=True, force=True)
        root, joints = import_morph_model()
        add_morph_clip(root)
        cmds.currentTime(5)
        for actual, reference in zip(cmds.getAttr(joints[0] + ".rotate")[0], expected):
            self.assertAlmostEqual(actual, reference, delta=1e-10)

    def test_camera_rotation_crossing_180_matches_scene(self):
        cmds.currentUnit(time="ntsc")
        data = VmdData()
        for number, angle in enumerate((2.9, 3.0, -3.0)):
            frame = VmdCameraFrame()
            frame.frame_number = number
            frame.rotation = (0.0, 0.0, angle)
            frame.viewing_angle = 45
            data.camera_frames.append(frame)
        vmd_importer.import_vmd_file(data, "camera_wrap.vmd", dict(CAMERA_OPTIONS, scene_animation_only=True))
        expected = camera_samples(range(3))
        cmds.file(new=True, force=True)
        cmds.currentUnit(time="ntsc")
        te.import_vmd_clip(None, data, "camera_wrap.vmd", dict(CAMERA_OPTIONS))
        actual = camera_samples(range(3))
        for frame in expected:
            self.assertLess(pose_error(actual[frame], expected[frame]), 1e-6)

    def test_second_clip_added_at_non_rest_time(self):
        self.baseline()
        root, joints = import_model()
        first = add_clip(root)
        cmds.currentTime(3)
        second = add_clip(root, name="second take!", start=20)
        self.assertEqual(second["clip"], "second_take")
        self.assertEqual(first["track"], second["track"])
        self.assert_matches(joints)
        self.assert_matches(joints, 20)
        cmds.timeEditorClip(edit=True, removeClip=True, clipId=first["clipId"])
        self.assert_matches(joints, 20)

    def test_clip_sharing_channels_follows_previous_clip(self):
        """Overlapping clips on separate tracks are averaged, so the next take goes after the last one."""
        self.baseline()
        root, joints = import_model()
        first = add_clip(root)
        second = add_clip(root)
        self.assertEqual(first["track"], second["track"])
        self.assertEqual(cmds.timeEditorClip(second["clipId"], query=True, startTime=True), 5.0)
        self.assert_matches(joints, 5)
        morph_root, _ = import_morph_model()
        self.assertNotEqual(add_morph_clip(morph_root)["track"], first["track"])

    def test_clip_includes_hand_keyed_channel(self):
        root, joints = import_model()
        plug = joints[0] + ".scaleX"
        cmds.setKeyframe(plug, time=0, value=1)
        cmds.setKeyframe(plug, time=5, value=2)
        clip = add_clip(root, start=10)
        cmds.currentTime(15)
        self.assertAlmostEqual(cmds.getAttr(plug), 2)
        cmds.timeEditorClip(edit=True, removeClip=True, clipId=clip["clipId"])
        self.assertFalse(cmds.listConnections(plug, source=True, destination=False, type="animCurve"))

    def assert_route_matches(self, **route):
        root, joints = import_model()
        options = dict(VMD_OPTIONS, target_model=root, use_animation_layers=False, **route)
        self.assertTrue(import_mmd_file(str(VMD), options=options))
        expected = poses(joints)
        cmds.file(new=True, force=True)
        root, joints = import_model()
        result = te.import_vmd_clip(root, VmdData().parse_file(str(VMD)), VMD, dict(VMD_OPTIONS, **route))
        # Moving the clip proves every curve left the scene: a stray scene curve would stay at frame 0.
        cmds.timeEditorClip(edit=True, moveClip=20, clipId=result["clipId"])
        actual = poses(joints, [frame + 20 for frame in FRAMES])
        for frame in FRAMES:
            self.assertLess(pose_error(expected[frame], actual[frame + 20]), 1e-6)

    def test_bake_mode_matches_ordinary_import(self):
        if not vmd_importer.is_mmd_runtime_available():
            self.skipTest("native bake runtime unavailable")
        self.assert_route_matches(bake_mode=True)

    def test_control_rig_matches_ordinary_import(self):
        self.assert_route_matches(create_mmd_control_rig=True)

    def test_failure_after_import_leaves_scene_unchanged(self):
        root, joints = import_model()
        add_clip(root)
        curves = set(cmds.ls(type="animCurve"))
        wired = writers()
        clips = cmds.ls(type="timeEditorClip")
        real = vmd_importer.import_vmd_file

        def failing(*args, **kwargs):
            real(*args, **kwargs)
            raise RuntimeError("injected")

        with patch.object(vmd_importer, "import_vmd_file", failing):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                add_clip(root, name="fail")
        self.assertEqual(set(cmds.ls(type="animCurve")), curves)
        self.assertEqual(writers(), wired)
        self.assertEqual(cmds.ls(type="timeEditorClip"), clips)
        self.assert_matches(joints)

    def test_saved_scene_reloads_clip(self):
        self.baseline()
        root, joints = import_model()
        add_clip(root)
        path = self.get_temp_filename("time_editor_clip.ma")
        cmds.file(rename=path)
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(new=True, force=True)
        cmds.file(path, open=True, force=True)
        self.assert_matches(cmds.ls(joints, long=True))

    def test_ordinary_vmd_import_rejected_on_clip_driven_model(self):
        root, _joints = import_model()
        add_clip(root)
        curves = set(cmds.ls(type="animCurve"))
        wired = writers()
        with self.assertRaises(MMDImportException) as raised:
            import_scene_vmd(root)
        self.assertEqual(raised.exception.reason_code, "time_editor_clip_owns_model")
        self.assertEqual(set(cmds.ls(type="animCurve")), curves)
        self.assertEqual(writers(), wired)

    def test_bone_and_morph_share_one_clip(self):
        self.morph_baseline()
        root, joints = import_morph_model()
        clip = add_morph_clip(root)
        controller = cmds.listConnections(root + ".mmd_morph_controller", source=True, destination=False)[0]
        targets = cmds.timeEditorAnimSource(clip["source"], query=True, targets=True)
        self.assertTrue(any(plug.startswith(controller + ".") for plug in targets), targets)
        self.assertTrue(any(plug.endswith(".translateX") for plug in targets), targets)
        self.assertTrue(te.is_time_editor_driven(root))
        self.assert_morph_matches(root, joints)
        cmds.timeEditorClip(edit=True, moveClip=20, clipId=clip["clipId"])
        self.assert_morph_matches(root, joints, 20)

    def test_second_clip_added_while_clip_drives_morphs(self):
        self.morph_baseline()
        root, joints = import_morph_model()
        add_morph_clip(root)
        add_morph_clip(root, name="second", start=20)
        self.assert_morph_matches(root, joints)
        self.assert_morph_matches(root, joints, 20)

    def test_clip_consumes_scene_morph_keys(self):
        self.morph_baseline()
        root, joints = import_morph_model()
        controller = cmds.listConnections(root + ".mmd_morph_controller", source=True, destination=False)[0]
        cmds.setKeyframe(controller, attribute="inputWeight[0]", time=0, value=0.25)
        cmds.setKeyframe(controller, attribute="inputWeight[0]", time=30, value=0.75)
        clip = add_morph_clip(root, start=10)
        self.assert_morph_matches(root, joints, 10)
        cmds.timeEditorClip(edit=True, removeClip=True, clipId=clip["clipId"])
        self.assertFalse(cmds.listConnections(controller, source=True, destination=False, type="animCurve"))

    def test_morph_only_vmd_makes_clip(self):
        root, joints = import_morph_model()
        options = dict(MORPH_VMD_OPTIONS, target_model=root, use_animation_layers=False)
        vmd_importer.import_vmd_file(morph_only_vmd(), MORPH_VMD, options)
        base = morph_samples(root, joints)
        cmds.file(new=True, force=True)
        root, joints = import_morph_model()
        clip = te.import_vmd_clip(root, morph_only_vmd(), MORPH_VMD, dict(MORPH_VMD_OPTIONS))
        targets = cmds.timeEditorAnimSource(clip["source"], query=True, targets=True)
        self.assertFalse(any(plug.endswith(".translateX") for plug in targets), targets)
        self.assertTrue(te.is_time_editor_driven(root))
        actual = morph_samples(root, joints)
        for frame in FRAMES:
            self.assertLess(pose_error(actual[frame], base[frame]), 1e-4, str(frame))

    def test_camera_vmd_becomes_camera_clip(self):
        self.camera_baseline(CAMERA_VMD)
        result = import_camera(CAMERA_VMD, time_editor_clip=True)
        self.assertEqual(result["clip"], "camera_edge_generated")
        self.assertEqual(curve_payload(), {})
        self.assertTrue(te.is_time_editor_driven(None))
        self.assert_camera_matches(CAMERA_VMD)
        cmds.timeEditorClip(edit=True, moveClip=20, clipId=result["clipId"])
        self.assert_camera_matches(CAMERA_VMD, 20)
        # Ordinary camera keys would be hidden under the clip.
        with self.assertRaises(MMDImportException) as raised:
            import_camera(OTHER_CAMERA_VMD)
        self.assertEqual(raised.exception.reason_code, "time_editor_clip_owns_camera")

    def test_camera_clip_consumes_scene_keys(self):
        self.camera_baseline(CAMERA_VMD)
        self.camera_baseline(OTHER_CAMERA_VMD)
        self.assertTrue(import_camera(OTHER_CAMERA_VMD))
        clip = import_camera(CAMERA_VMD, time_editor_clip=True)
        self.assert_camera_matches(CAMERA_VMD)
        cmds.timeEditorClip(edit=True, removeClip=True, clipId=clip["clipId"])
        self.assertEqual(curve_payload(), {})
        self.assertFalse(te.is_time_editor_driven(None))

    def test_second_camera_clip_and_removal(self):
        self.camera_baseline(CAMERA_VMD)
        first = import_camera(CAMERA_VMD, time_editor_clip=True)
        second = te.import_vmd_clip(None, VmdData().parse_file(str(CAMERA_VMD)), str(CAMERA_VMD),
                                    dict(CAMERA_OPTIONS), name="second", start=100)
        self.assertEqual(first["track"], second["track"])
        self.assert_camera_matches(CAMERA_VMD)
        self.assert_camera_matches(CAMERA_VMD, 100)
        cmds.timeEditorClip(edit=True, removeClip=True, clipId=first["clipId"])
        self.assert_camera_matches(CAMERA_VMD, 100)
