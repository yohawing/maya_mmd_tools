"""VMD data frame types — round-trip (construct → write → parse) tests."""

import io

import pytest

from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame
from mmd_tools.core.vmd_data.camera_frame import VmdCameraFrame
from mmd_tools.core.vmd_data.header import VmdHeader
from mmd_tools.core.vmd_data.ik_show_hide_frame import VmdIKShowHideFrame
from mmd_tools.core.vmd_data.light_frame import VmdLightFrame
from mmd_tools.core.vmd_data.morph_frame import VmdMorphFrame
from mmd_tools.core.vmd_data.shadow_frame import VmdShadowFrame


class TestVmdHeader:
    def test_round_trip(self):
        header = VmdHeader()
        header.model_name = "test_model"

        buf = io.BytesIO()
        header.write(buf)

        buf.seek(0)
        parsed = VmdHeader()
        parsed.parse(buf)

        assert parsed.model_name == "test_model"
        assert parsed.magic.startswith(VmdHeader.SIGNATURE)

    def test_invalid_magic_raises(self):
        buf = io.BytesIO(b"\x00" * 50)
        header = VmdHeader()
        with pytest.raises(ValueError, match="Invalid magic number"):
            header.parse(buf)

    def test_write_size(self):
        header = VmdHeader()
        header.model_name = "abc"
        buf = io.BytesIO()
        header.write(buf)
        assert buf.tell() == 50  # 30 (magic) + 20 (model name)


class TestVmdBoneFrame:
    def _make_frame(self, name="bone", frame=10, pos=(1.0, 2.0, 3.0), rot=(0.1, 0.2, 0.3, 0.9)):
        f = VmdBoneFrame()
        f.bone_name = name
        f.frame_number = frame
        f.position = pos
        f.rotation = rot
        f.interpolation = bytes(range(64))
        return f

    def test_size(self):
        assert VmdBoneFrame.size() == 111

    def test_round_trip(self):
        original = self._make_frame()
        data = original.write()
        assert len(data) == VmdBoneFrame.size()

        parsed = VmdBoneFrame()
        parsed.parse(data)

        assert parsed.bone_name == "bone"
        assert parsed.frame_number == 10
        for i in range(3):
            assert abs(parsed.position[i] - original.position[i]) < 1e-6
        for i in range(4):
            assert abs(parsed.rotation[i] - original.rotation[i]) < 1e-6
        assert parsed.interpolation == bytes(range(64))

    def test_default_interpolation_padding(self):
        f = VmdBoneFrame()
        f.bone_name = "a"
        f.interpolation = b""
        data = f.write()
        assert len(data) == VmdBoneFrame.size()
        assert data[-64:] == b"\x00" * 64

    def test_japanese_bone_name(self):
        original = self._make_frame(name="センター")
        data = original.write()
        parsed = VmdBoneFrame()
        parsed.parse(data)
        assert parsed.bone_name == "センター"


class TestVmdMorphFrame:
    def test_size(self):
        assert VmdMorphFrame.size() == 23

    def test_round_trip(self):
        original = VmdMorphFrame()
        original.morph_name = "smile"
        original.frame_number = 42
        original.value = 0.75

        data = original.write()
        assert len(data) == VmdMorphFrame.size()

        parsed = VmdMorphFrame()
        parsed.parse(data)

        assert parsed.morph_name == "smile"
        assert parsed.frame_number == 42
        assert abs(parsed.value - 0.75) < 1e-6

    def test_japanese_morph_name(self):
        original = VmdMorphFrame()
        original.morph_name = "まばたき"
        original.frame_number = 0
        original.value = 1.0

        data = original.write()
        parsed = VmdMorphFrame()
        parsed.parse(data)
        assert parsed.morph_name == "まばたき"


class TestVmdCameraFrame:
    def test_size(self):
        assert VmdCameraFrame.size() == 61

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
        assert len(data) == VmdCameraFrame.size()

        parsed = VmdCameraFrame()
        parsed.parse(data)

        assert parsed.frame_number == 100
        assert abs(parsed.distance - (-45.0)) < 1e-6
        for i in range(3):
            assert abs(parsed.position[i] - original.position[i]) < 1e-6
            assert abs(parsed.rotation[i] - original.rotation[i]) < 1e-6
        assert parsed.interpolation == bytes(range(24))
        assert parsed.viewing_angle == 30
        assert parsed.perspective == 0

    def test_perspective_off(self):
        f = VmdCameraFrame()
        f.perspective = 1
        data = f.write()
        parsed = VmdCameraFrame()
        parsed.parse(data)
        assert parsed.perspective == 1


class TestVmdLightFrame:
    def test_size(self):
        assert VmdLightFrame.size() == 28

    def test_round_trip(self):
        original = VmdLightFrame()
        original.frame_number = 5
        original.color = (0.6, 0.6, 0.6)
        original.position = (-0.5, -1.0, 0.5)

        data = original.write()
        assert len(data) == VmdLightFrame.size()

        parsed = VmdLightFrame()
        parsed.parse(data)

        assert parsed.frame_number == 5
        for i in range(3):
            assert abs(parsed.color[i] - original.color[i]) < 1e-6
            assert abs(parsed.position[i] - original.position[i]) < 1e-6


class TestVmdShadowFrame:
    def test_size(self):
        assert VmdShadowFrame.size() == 9

    def test_round_trip(self):
        original = VmdShadowFrame()
        original.frame_number = 20
        original.mode = 2
        original.distance = 0.01

        data = original.write()
        assert len(data) == VmdShadowFrame.size()

        parsed = VmdShadowFrame()
        parsed.parse(data)

        assert parsed.frame_number == 20
        assert parsed.mode == 2
        assert abs(parsed.distance - 0.01) < 1e-6

    def test_mode_values(self):
        for mode in (0, 1, 2):
            f = VmdShadowFrame()
            f.mode = mode
            data = f.write()
            parsed = VmdShadowFrame()
            parsed.parse(data)
            assert parsed.mode == mode


class TestVmdIKShowHideFrame:
    def test_empty_ik_round_trip(self):
        original = VmdIKShowHideFrame()
        original.frame_number = 15
        original.visible = 1
        original.ik_count = 0
        original.ik_states = []

        data = original.write()
        assert len(data) == 9  # base size

        parsed = VmdIKShowHideFrame()
        parsed.parse(data)

        assert parsed.frame_number == 15
        assert parsed.visible == 1
        assert parsed.ik_count == 0
        assert parsed.ik_states == []

    def test_with_ik_states(self):
        original = VmdIKShowHideFrame()
        original.frame_number = 30
        original.visible = 1
        original.ik_count = 2
        original.ik_states = [("left_ik", 1), ("right_ik", 0)]

        data = original.write()
        expected_size = 9 + 2 * 21  # base + 2 * (name20 + flag1)
        assert len(data) == expected_size

        parsed = VmdIKShowHideFrame()
        parsed.parse(data)

        assert parsed.frame_number == 30
        assert parsed.visible == 1
        assert parsed.ik_count == 2
        assert len(parsed.ik_states) == 2
        assert parsed.ik_states[0][0] == "left_ik"
        assert parsed.ik_states[0][1] == 1
        assert parsed.ik_states[1][0] == "right_ik"
        assert parsed.ik_states[1][1] == 0

    def test_japanese_ik_names(self):
        original = VmdIKShowHideFrame()
        original.frame_number = 0
        original.visible = 1
        original.ik_count = 1
        original.ik_states = [("左足ＩＫ", 1)]

        data = original.write()
        parsed = VmdIKShowHideFrame()
        parsed.parse(data)

        assert parsed.ik_states[0][0] == "左足ＩＫ"
        assert parsed.ik_states[0][1] == 1
