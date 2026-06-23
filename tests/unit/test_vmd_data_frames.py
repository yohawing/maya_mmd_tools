"""VMD data frame types — round-trip (construct → write → parse) tests."""

import io
import unittest


from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame
from mmd_tools.core.vmd_data.camera_frame import VmdCameraFrame
from mmd_tools.core.vmd_data.header import VmdHeader
from mmd_tools.core.vmd_data.ik_show_hide_frame import VmdIKShowHideFrame
from mmd_tools.core.vmd_data.light_frame import VmdLightFrame
from mmd_tools.core.vmd_data.morph_frame import VmdMorphFrame
from mmd_tools.core.vmd_data.shadow_frame import VmdShadowFrame


class TestVmdHeader(unittest.TestCase):
    def test_round_trip(self):
        header = VmdHeader()
        header.model_name = "test_model"

        buf = io.BytesIO()
        header.write(buf)

        buf.seek(0)
        parsed = VmdHeader()
        parsed.parse(buf)

        self.assertEqual(parsed.model_name, "test_model")
        self.assertTrue(parsed.magic.startswith(VmdHeader.SIGNATURE))

    def test_invalid_magic_raises(self):
        buf = io.BytesIO(b"\x00" * 50)
        header = VmdHeader()
        with self.assertRaisesRegex(ValueError, "Invalid magic number"):
            header.parse(buf)

    def test_write_size(self):
        header = VmdHeader()
        header.model_name = "abc"
        buf = io.BytesIO()
        header.write(buf)
        self.assertEqual(buf.tell(), 50)  # 30 (magic) + 20 (model name)


class TestVmdBoneFrame(unittest.TestCase):
    def _make_frame(self, name="bone", frame=10, pos=(1.0, 2.0, 3.0), rot=(0.1, 0.2, 0.3, 0.9)):
        f = VmdBoneFrame()
        f.bone_name = name
        f.frame_number = frame
        f.position = pos
        f.rotation = rot
        f.interpolation = bytes(range(64))
        return f

    def test_size(self):
        self.assertEqual(VmdBoneFrame.size(), 111)

    def test_round_trip(self):
        original = self._make_frame()
        data = original.write()
        self.assertEqual(len(data), VmdBoneFrame.size())

        parsed = VmdBoneFrame()
        parsed.parse(data)

        self.assertEqual(parsed.bone_name, "bone")
        self.assertEqual(parsed.frame_number, 10)
        for i in range(3):
            self.assertLess(abs(parsed.position[i] - original.position[i]), 1e-6)
        for i in range(4):
            self.assertLess(abs(parsed.rotation[i] - original.rotation[i]), 1e-6)
        self.assertEqual(parsed.interpolation, bytes(range(64)))

    def test_default_interpolation_padding(self):
        f = VmdBoneFrame()
        f.bone_name = "a"
        f.interpolation = b""
        data = f.write()
        self.assertEqual(len(data), VmdBoneFrame.size())
        self.assertEqual(data[-64:], b"\x00" * 64)

    def test_japanese_bone_name(self):
        original = self._make_frame(name="センター")
        data = original.write()
        parsed = VmdBoneFrame()
        parsed.parse(data)
        self.assertEqual(parsed.bone_name, "センター")


class TestVmdMorphFrame(unittest.TestCase):
    def test_size(self):
        self.assertEqual(VmdMorphFrame.size(), 23)

    def test_round_trip(self):
        original = VmdMorphFrame()
        original.morph_name = "smile"
        original.frame_number = 42
        original.value = 0.75

        data = original.write()
        self.assertEqual(len(data), VmdMorphFrame.size())

        parsed = VmdMorphFrame()
        parsed.parse(data)

        self.assertEqual(parsed.morph_name, "smile")
        self.assertEqual(parsed.frame_number, 42)
        self.assertLess(abs(parsed.value - 0.75), 1e-6)

    def test_japanese_morph_name(self):
        original = VmdMorphFrame()
        original.morph_name = "まばたき"
        original.frame_number = 0
        original.value = 1.0

        data = original.write()
        parsed = VmdMorphFrame()
        parsed.parse(data)
        self.assertEqual(parsed.morph_name, "まばたき")


class TestVmdCameraFrame(unittest.TestCase):
    def test_size(self):
        self.assertEqual(VmdCameraFrame.size(), 61)

    def test_round_trip(self):
        original = VmdCameraFrame()
        original.frame_number = 100
        original.distance = -45.0
        original.position = (1.0, 15.0, -5.0)
        original.rotation = (0.1, 0.2, 0.3)
        original.interpolation = bytes(range(24))
        original.viewing_angle = 30
        original.perspective = 0

        data = original.write()
        self.assertEqual(len(data), VmdCameraFrame.size())

        parsed = VmdCameraFrame()
        parsed.parse(data)

        self.assertEqual(parsed.frame_number, 100)
        self.assertLess(abs(parsed.distance - (-45.0)), 1e-6)
        for i in range(3):
            self.assertLess(abs(parsed.position[i] - original.position[i]), 1e-6)
            self.assertLess(abs(parsed.rotation[i] - original.rotation[i]), 1e-6)
        self.assertEqual(parsed.interpolation, bytes(range(24)))
        self.assertEqual(parsed.viewing_angle, 30)
        self.assertEqual(parsed.perspective, 0)

    def test_perspective_off(self):
        f = VmdCameraFrame()
        f.perspective = 1
        data = f.write()
        parsed = VmdCameraFrame()
        parsed.parse(data)
        self.assertEqual(parsed.perspective, 1)


class TestVmdLightFrame(unittest.TestCase):
    def test_size(self):
        self.assertEqual(VmdLightFrame.size(), 28)

    def test_round_trip(self):
        original = VmdLightFrame()
        original.frame_number = 5
        original.color = (0.6, 0.6, 0.6)
        original.position = (-0.5, -1.0, 0.5)

        data = original.write()
        self.assertEqual(len(data), VmdLightFrame.size())

        parsed = VmdLightFrame()
        parsed.parse(data)

        self.assertEqual(parsed.frame_number, 5)
        for i in range(3):
            self.assertLess(abs(parsed.color[i] - original.color[i]), 1e-6)
            self.assertLess(abs(parsed.position[i] - original.position[i]), 1e-6)


class TestVmdShadowFrame(unittest.TestCase):
    def test_size(self):
        self.assertEqual(VmdShadowFrame.size(), 9)

    def test_round_trip(self):
        original = VmdShadowFrame()
        original.frame_number = 20
        original.mode = 2
        original.distance = 0.01

        data = original.write()
        self.assertEqual(len(data), VmdShadowFrame.size())

        parsed = VmdShadowFrame()
        parsed.parse(data)

        self.assertEqual(parsed.frame_number, 20)
        self.assertEqual(parsed.mode, 2)
        self.assertLess(abs(parsed.distance - 0.01), 1e-6)

    def test_mode_values(self):
        for mode in (0, 1, 2):
            f = VmdShadowFrame()
            f.mode = mode
            data = f.write()
            parsed = VmdShadowFrame()
            parsed.parse(data)
            self.assertEqual(parsed.mode, mode)


class TestVmdIKShowHideFrame(unittest.TestCase):
    def test_empty_ik_round_trip(self):
        original = VmdIKShowHideFrame()
        original.frame_number = 15
        original.visible = 1
        original.ik_count = 0
        original.ik_states = []

        data = original.write()
        self.assertEqual(len(data), 9)  # base size

        parsed = VmdIKShowHideFrame()
        parsed.parse(data)

        self.assertEqual(parsed.frame_number, 15)
        self.assertEqual(parsed.visible, 1)
        self.assertEqual(parsed.ik_count, 0)
        self.assertEqual(parsed.ik_states, [])

    def test_with_ik_states(self):
        original = VmdIKShowHideFrame()
        original.frame_number = 30
        original.visible = 1
        original.ik_count = 2
        original.ik_states = [("left_ik", 1), ("right_ik", 0)]

        data = original.write()
        expected_size = 9 + 2 * 21  # base + 2 * (name20 + flag1)
        self.assertEqual(len(data), expected_size)

        parsed = VmdIKShowHideFrame()
        parsed.parse(data)

        self.assertEqual(parsed.frame_number, 30)
        self.assertEqual(parsed.visible, 1)
        self.assertEqual(parsed.ik_count, 2)
        self.assertEqual(len(parsed.ik_states), 2)
        self.assertEqual(parsed.ik_states[0][0], "left_ik")
        self.assertEqual(parsed.ik_states[0][1], 1)
        self.assertEqual(parsed.ik_states[1][0], "right_ik")
        self.assertEqual(parsed.ik_states[1][1], 0)

    def test_japanese_ik_names(self):
        original = VmdIKShowHideFrame()
        original.frame_number = 0
        original.visible = 1
        original.ik_count = 1
        original.ik_states = [("左足ＩＫ", 1)]

        data = original.write()
        parsed = VmdIKShowHideFrame()
        parsed.parse(data)

        self.assertEqual(parsed.ik_states[0][0], "左足ＩＫ")
        self.assertEqual(parsed.ik_states[0][1], 1)
