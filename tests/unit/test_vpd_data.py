"""VPD data types — unit tests for header and bone pose."""

from mmd_tools.core.vpd_data.header import VpdHeader
from mmd_tools.core.vpd_data.bone_pose import BonePose


class TestVpdHeader:
    def test_defaults(self):
        h = VpdHeader()
        assert h.signature == "Vocaloid Pose Data file"
        assert h.parent_file == ""
        assert h.bone_count == 0

    def test_repr(self):
        h = VpdHeader()
        h.parent_file = "model.osm"
        h.bone_count = 5
        r = repr(h)
        assert "model.osm" in r
        assert "5" in r

    def test_str(self):
        h = VpdHeader()
        h.bone_count = 3
        s = str(h)
        assert "VPD Header" in s
        assert "3" in s


class TestBonePose:
    def test_defaults(self):
        bp = BonePose()
        assert bp.bone_index == 0
        assert bp.bone_name == ""
        assert bp.position == [0.0, 0.0, 0.0]
        assert bp.quaternion == [0.0, 0.0, 0.0, 1.0]

    def test_repr(self):
        bp = BonePose()
        bp.bone_index = 3
        bp.bone_name = "center"
        bp.position = [1.0, 2.0, 3.0]
        r = repr(bp)
        assert "center" in r
        assert "3" in r

    def test_to_vpd_format(self):
        bp = BonePose()
        bp.bone_index = 0
        bp.bone_name = "センター"
        bp.position = [1.5, 2.5, 3.5]
        bp.quaternion = [0.0, 0.0, 0.0, 1.0]

        text = bp.to_vpd_format()
        assert "Bone0{センター" in text
        assert "1.500000,2.500000,3.500000;" in text
        assert "0.000000,0.000000,0.000000,1.000000;" in text
        assert text.endswith("}\n")

    def test_str_matches_format(self):
        bp = BonePose()
        bp.bone_index = 1
        bp.bone_name = "head"
        bp.position = [0.0, 10.0, 0.0]
        bp.quaternion = [0.1, 0.2, 0.3, 0.9]

        s = str(bp)
        assert "Bone1{head" in s
        assert "0.100000" in s

    def test_vpd_format_multiple_bones(self):
        poses = []
        for i, name in enumerate(["center", "upper_body", "head"]):
            bp = BonePose()
            bp.bone_index = i
            bp.bone_name = name
            bp.position = [float(i), 0.0, 0.0]
            poses.append(bp)

        lines = "".join(p.to_vpd_format() for p in poses)
        assert "Bone0{center" in lines
        assert "Bone1{upper_body" in lines
        assert "Bone2{head" in lines
