"""Native TT insertion preserves time units, validation and Maya undo order."""

import json
import os
from pathlib import Path

from maya import cmds

from tests.common.maya_test_base import MayaTestBase


class TestVmdTimeCurveKeys(MayaTestBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        root = Path(__file__).resolve().parents[2]
        version = str(cmds.about(version=True)).split()[0]
        config = os.environ.get("MMD_TOOLS_CPP_CONFIG", "Release")
        suffix = ".mll" if os.name == "nt" else ".bundle"
        cls.load_plugin(str(root / "plug-ins" / version / config / ("mmd_tools_cpp" + suffix)))
        if not callable(getattr(cmds, "mmdVmdTimeCurveKeys", None)):
            raise RuntimeError("Build the C++ plugin with mmdVmdTimeCurveKeys before testing")

    def test_native_matches_commands_and_restores_replaced_curve(self):
        old_unit = cmds.currentUnit(query=True, time=True)
        try:
            for unit in ("film", "ntsc", "ntscf"):
                with self.subTest(unit=unit):
                    cmds.currentUnit(time=unit)
                    curve = cmds.createNode("animCurveTT")
                    reference = cmds.createNode("animCurveTT")
                    times = [-2.5, 0., 1.25, 1.25, 9., 50.]
                    for time in times:
                        cmds.setKeyframe(reference, time=time, value=time)
                    cmds.setKeyframe(curve, time=7, value=13)
                    cmds.undoInfo(openChunk=True)
                    try:
                        cmds.mmdVmdTimeCurveKeys(payload=json.dumps({"curve": curve, "times": times}))
                    finally:
                        cmds.undoInfo(closeChunk=True)
                    expected = cmds.keyframe(reference, query=True, valueChange=True)
                    self.assertEqual(cmds.keyframe(curve, query=True, valueChange=True), expected)
                    self.assertEqual(cmds.keyframe(curve, query=True, timeChange=True), sorted(set(times)))
                    for time in (-1., .5, 4., 21.):
                        self.assertAlmostEqual(cmds.keyframe(curve, query=True, eval=True, time=(time, time))[0],
                                               cmds.keyframe(reference, query=True, eval=True, time=(time, time))[0])
                    cmds.undo()
                    self.assertEqual(cmds.keyframe(curve, query=True, timeChange=True), [7.])
                    self.assertEqual(cmds.keyframe(curve, query=True, valueChange=True), [13.])
                    cmds.redo()
                    self.assertEqual(cmds.keyframe(curve, query=True, valueChange=True), expected)
        finally:
            cmds.currentUnit(time=old_unit)

    def test_invalid_request_does_not_edit_curve(self):
        curve = cmds.createNode("animCurveTT")
        for times in ([2., 1.], [0., float("inf")], [0., "bad"]):
            with self.assertRaises(RuntimeError):
                cmds.mmdVmdTimeCurveKeys(payload=json.dumps({"curve": curve, "times": times}))
            self.assertEqual(cmds.keyframe(curve, query=True, keyframeCount=True), 0)
        curve = cmds.createNode("animCurveTU")
        cmds.setKeyframe(curve, time=1, value=5)
        with self.assertRaises(RuntimeError):
            cmds.mmdVmdTimeCurveKeys(payload=json.dumps({"curve": curve, "times": [1, 2]}))
        self.assertEqual(cmds.keyframe(curve, query=True, valueChange=True), [5.])
