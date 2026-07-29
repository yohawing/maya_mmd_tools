"""Unit contracts for the deterministic Control Rig BoneMorph fixture."""

from __future__ import annotations

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.core.pmx_data.morph import PmxMorphType
from mmd_tools.core.vmd_data import VmdData
from tests.common.test_fixture_provider import TestFixtureProvider


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "tests" / "data"
GENERATOR_PATH = DATA / "generate_yw_test_model_control_rig_bone_morph.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("yw_test_model_control_rig_bone_morph_generator", GENERATOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load fixture generator: {GENERATOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestYwControlRigBoneMorphFixture(unittest.TestCase):
    """The PMX/VMD pair is structural, provenance-backed, and reproducible."""

    @classmethod
    def setUpClass(cls):
        cls.provider = TestFixtureProvider()
        cls.verified = cls.provider.get_verified_fixture("yw_test_model_control_rig_bone_morph")
        cls.model_path = Path(cls.provider.get_verified_pmx_file("yw_test_model_control_rig_bone_morph"))
        cls.vmd_path = Path(cls.provider.get_verified_vmd_file("yw_test_model_control_rig_bone_morph"))
        cls.source_path = Path(cls.provider.get_verified_source_file("yw_test_model_control_rig_bone_morph"))
        cls.generator = _load_generator()

    def test_manifest_provenance_and_structural_bone_morph(self):
        manifest = self.verified["manifest"]
        self.assertEqual(manifest["license"]["identifier"], "MIT")
        self.assertIn("PmxData legacy parse/write", manifest["provenance"]["record"])
        pmx = parse_pmx_file(str(self.model_path), use_native_pmx_parse=False)
        self.assertEqual(len(pmx.morphs), 1)
        morph = pmx.morphs[0]
        self.assertEqual(morph.name, "CR_BoneMorph")
        self.assertEqual(morph.morph_type, PmxMorphType.BoneMorph)
        self.assertEqual(morph.offsets[0]["bone_index"], 2)
        self.assertGreater(abs(float(morph.offsets[0]["translation"][0])), 0.0)

    def test_regeneration_is_byte_identical_and_morph_keys_resolve(self):
        with tempfile.TemporaryDirectory() as directory:
            model, motion = self.generator.generate_fixture(
                Path(directory) / "model.pmx",
                Path(directory) / "motion.vmd",
                DATA / "yw_test_model.pmx",
                self.source_path,
            )
            self.assertEqual(Path(model).read_bytes(), self.model_path.read_bytes())
            self.assertEqual(Path(motion).read_bytes(), self.vmd_path.read_bytes())
            for path, kind in ((model, "pmx"), (motion, "vmd")):
                entry = next(item for item in self.verified["manifest"]["files"] if item["kind"] == kind)
                self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(), entry["sha256"])
        parsed = VmdData().parse_file(str(self.vmd_path))
        rows = [(frame.morph_name, frame.frame_number, frame.value) for frame in parsed.morph_frames]
        self.assertEqual(rows, [("CR_BoneMorph", 0, 0.0), ("CR_BoneMorph", 10, 0.75), ("CR_BoneMorph", 20, 1.0)])

    def test_provider_registers_case_explicitly(self):
        self.assertIn("yw_test_model_control_rig_bone_morph", self.provider.get_registered_fixture_names())


if __name__ == "__main__":
    unittest.main()
