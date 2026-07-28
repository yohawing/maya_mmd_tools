"""Unit coverage for the deterministic ``yw_test_model`` Control Rig VMD fixture."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from mmd_tools.core.vmd_data import VmdData
from tests.common.test_fixture_provider import TestFixtureProvider


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "tests" / "data"
GENERATOR_PATH = DATA / "generate_yw_test_model_control_rig_vmd.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("yw_test_model_control_rig_vmd_generator", GENERATOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load fixture generator: {GENERATOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestYwControlRigVmdFixture(unittest.TestCase):
    """The checked-in motion is generated, parseable, and provenance-backed."""

    @classmethod
    def setUpClass(cls):
        cls.provider = TestFixtureProvider()
        cls.verified = cls.provider.get_verified_fixture("yw_test_model_control_rig_vmd")
        cls.vmd_path = Path(cls.provider.get_verified_vmd_file())
        cls.source_path = Path(cls.provider.get_verified_source_file())
        cls.generator = _load_generator()

    def test_manifest_registers_source_and_motion_with_selection_metadata(self):
        self.assertIn("yw_test_model_control_rig_vmd", self.provider.get_registered_fixture_names())
        manifest = self.verified["manifest"]
        self.assertEqual(manifest["license"]["identifier"], "CC0")
        self.assertEqual(manifest["motion"]["bone_frames"], [0, 10, 20])
        self.assertEqual(manifest["motion"]["ik_enable_frames"], [0, 6, 12, 20])
        self.assertEqual(manifest["coverage"]["bone_morph"]["status"], "deferred")

    def test_regeneration_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as directory:
            regenerated = self.generator.generate_fixture(
                Path(directory) / "regenerated.vmd",
                DATA / "yw_test_model.pmx",
                self.source_path,
            )
            self.assertEqual(Path(regenerated).read_bytes(), self.vmd_path.read_bytes())
            vmd_entry = next(entry for entry in self.verified["manifest"]["files"] if entry["kind"] == "vmd")
            self.assertEqual(
                hashlib.sha256(Path(regenerated).read_bytes()).hexdigest(),
                vmd_entry["sha256"],
            )

    def test_parse_back_covers_bone_motion_and_per_side_ik_enable(self):
        parsed = VmdData().parse_file(str(self.vmd_path))
        self.assertEqual(parsed.header.model_name, "YWテスト用モデル")
        self.assertEqual(len(parsed.bone_frames), 18)
        self.assertEqual(sorted({frame.frame_number for frame in parsed.bone_frames}), [0, 10, 20])
        names = {frame.bone_name for frame in parsed.bone_frames}
        self.assertEqual(
            names,
            {"左足ＩＫ", "右足ＩＫ", "左つま先ＩＫ", "右つま先ＩＫ", "左足", "右足"},
        )
        self.assertEqual(len(parsed.ik_show_hide_frames), 4)
        states = {
            frame.frame_number: dict(frame.ik_states)
            for frame in parsed.ik_show_hide_frames
        }
        self.assertEqual(states[0]["左足ＩＫ"], 1)
        self.assertEqual(states[6]["左足ＩＫ"], 0)
        self.assertEqual(states[12]["右つま先ＩＫ"], 0)
        self.assertEqual(states[20]["左つま先ＩＫ"], 1)
        self.assertEqual(states[20]["右足ＩＫ"], 0)

    def test_structure_resolution_and_shift_jis_are_fail_closed(self):
        roles = self.generator.resolve_structural_roles(DATA / "yw_test_model.pmx")
        self.assertEqual(roles["left_foot_ik"]["index"], 9)
        self.assertEqual(roles["right_toe_ik"]["name"], "右つま先ＩＫ")
        self.assertEqual(roles["left_grant_source"]["target_name"], "左足D")
        with self.assertRaises(ValueError):
            self.generator._shift_jis_name("😀", 20, "unsupported")
        with self.assertRaises(ValueError):
            self.generator._shift_jis_name("左足ＩＫ", 8, "too_short")

    def test_plain_writer_loader_does_not_poison_maya_io_package(self):
        io_before = sys.modules.get("mmd_tools.io")
        private_before = sys.modules.get("mmd_tools._fixture_vmd_exporter")
        self.assertIsNotNone(self.generator._vmd_exporter_class())
        io_after = sys.modules.get("mmd_tools.io")
        if io_before is not None:
            self.assertIs(io_after, io_before)
        elif io_after is not None:
            self.assertEqual(Path(getattr(io_after, "__file__", "")).name, "__init__.py")
        self.assertIs(sys.modules.get("mmd_tools._fixture_vmd_exporter"), private_before)


if __name__ == "__main__":
    unittest.main()
