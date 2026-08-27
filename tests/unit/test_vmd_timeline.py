"""VMD timeline setup tests."""

import maya.cmds as cmds

from mmd_tools.converters.vmd_context import VmdTimelineContext
from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.converters.vmd_timeline import setup_timeline
from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame
from tests.common.maya_test_base import MayaTestBase


def _bone_frame(frame_number: int) -> VmdBoneFrame:
    frame = VmdBoneFrame()
    frame.bone_name = "センター"
    frame.frame_number = frame_number
    frame.position = (0.0, 0.0, 0.0)
    frame.rotation = (0.0, 0.0, 0.0, 1.0)
    return frame


class TestVmdTimeline(MayaTestBase):
    """Timeline context and scene playback range tests."""

    def setUp(self):
        super().setUp()
        self.converter = VmdConverter()

    def test_setup_timeline_accepts_direct_context(self):
        """Direct timeline contexts set playback range without converter private state."""
        vmd_data = type("VmdDataStub", (), {})()
        vmd_data.bone_frames = [_bone_frame(30)]

        context = VmdTimelineContext(
            logger=self.converter.logger,
            fps=60.0,
            vmd_frame_to_maya_time=lambda frame: frame * 2.0,
        )

        setup_timeline(context, vmd_data)

        self.assertEqual(cmds.playbackOptions(q=True, max=True), 60.0)
        self.assertEqual(cmds.currentUnit(q=True, time=True), "ntscf")

    def test_camera_only_motion_sets_playback_range(self):
        """Camera-only VMD uses its last key instead of the absent bone range."""
        vmd_data = type("VmdDataStub", (), {})()
        vmd_data.bone_frames = []
        vmd_data.morph_frames = []
        vmd_data.camera_frames = [{"frame_number": 75}]
        vmd_data.light_frames = []
        context = VmdTimelineContext(
            logger=self.converter.logger,
            fps=30.0,
            vmd_frame_to_maya_time=float,
        )

        setup_timeline(context, vmd_data)

        self.assertEqual(cmds.playbackOptions(q=True, min=True), 0.0)
        self.assertEqual(cmds.playbackOptions(q=True, max=True), 75.0)
        self.assertEqual(
            cmds.playbackOptions(q=True, animationEndTime=True),
            75.0,
        )

    def test_timeline_context_factory_matches_converter_state(self):
        """Converter timeline context factory binds fps and frame conversion."""
        self.converter.fps = 24.0

        context = self.converter._timeline_context()

        self.assertIsInstance(context, VmdTimelineContext)
        self.assertIs(context.logger, self.converter.logger)
        self.assertEqual(context.fps, 24.0)
        self.assertEqual(context.vmd_frame_to_maya_time(10.0), self.converter.vmd_frame_to_maya_time(10.0))
