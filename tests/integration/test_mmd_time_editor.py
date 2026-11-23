"""Maya contracts for adding VMD bone motion as baked Time Editor clips."""
from pathlib import Path
from unittest.mock import patch

from maya import cmds

from mmd_tools.core import mmd_time_editor as te
from mmd_tools.core.exceptions import MMDImportException
from mmd_tools.core.vmd_data import VmdData
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


def writers(root):
    return {plug: cmds.connectionInfo(plug, sourceFromDestination=True) for plug in te._target_plugs(root)}


class TestMmdTimeEditor(MayaTestBase):
    _baseline = None

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

    def test_first_clip_bakes_all_targets_into_source(self):
        self.baseline()
        root, joints = import_model()
        result = import_mmd_file(str(VMD), options=dict(VMD_OPTIONS, target_model=root, time_editor_clip=True))
        self.assertEqual(result["targets"], 684)
        self.assertEqual(result["clip"], "mmt_test_model_test_motion")
        self.assertEqual(curve_payload(), {})
        self.assertEqual(cmds.ls(type="animCurveTT"), [])
        rotations = [c for c in cmds.listConnections(result["source"], type="animCurveTA") or []]
        self.assertTrue(rotations)
        self.assertEqual({cmds.getAttr(c + ".rotationInterpolation") for c in set(rotations)}, {4})  # quaternion slerp
        self.assertTrue(te.is_time_editor_driven(root))
        self.assert_matches(joints)
        cmds.timeEditorClip(edit=True, moveClip=20, clipId=result["clipId"])
        self.assert_matches(joints, 20)

    def test_second_clip_added_at_non_rest_time(self):
        self.baseline()
        root, joints = import_model()
        first = add_clip(root)
        cmds.currentTime(3)
        second = add_clip(root, name="second take!", start=20)
        self.assertEqual(second["clip"], "second_take")
        self.assertNotEqual(first["track"], second["track"])
        self.assert_matches(joints)
        self.assert_matches(joints, 20)
        cmds.timeEditorClip(edit=True, removeClip=True, clipId=first["clipId"])
        self.assert_matches(joints, 20)

    def test_clip_over_scene_animation_restores_it_on_removal(self):
        self.baseline()
        root, joints = import_model()
        import_scene_vmd(root)
        original = curve_payload()
        wired = writers(root)
        clip = add_clip(root, start=10)
        self.assert_matches(joints, 10)
        # Maya's Time Editor adjusts the scene curves while a clip is on top of
        # them; the curves themselves must survive and come back on removal.
        self.assertEqual(set(curve_payload()), set(original))
        cmds.timeEditorClip(edit=True, removeClip=True, clipId=clip["clipId"])
        self.assertEqual(curve_payload(), original)
        self.assertEqual(writers(root), wired)
        self.assert_matches(joints)

    def test_failure_after_import_leaves_scene_unchanged(self):
        root, joints = import_model()
        add_clip(root)
        curves = set(cmds.ls(type="animCurve"))
        wired = writers(root)
        clips = cmds.ls(type="timeEditorClip")
        real = vmd_importer.import_vmd_file

        def failing(*args, **kwargs):
            real(*args, **kwargs)
            raise RuntimeError("injected")

        with patch.object(vmd_importer, "import_vmd_file", failing):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                add_clip(root, name="fail")
        self.assertEqual(set(cmds.ls(type="animCurve")), curves)
        self.assertEqual(writers(root), wired)
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
        wired = writers(root)
        with self.assertRaises(MMDImportException) as raised:
            import_scene_vmd(root)
        self.assertEqual(raised.exception.reason_code, "time_editor_clip_owns_model")
        self.assertEqual(set(cmds.ls(type="animCurve")), curves)
        self.assertEqual(writers(root), wired)
