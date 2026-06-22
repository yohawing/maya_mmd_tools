"""
native_rig_builder モジュールのユニットテスト。

PMX fixture から manifest 取得、ミニチェーン構築、append solver 構築を検証。
"""

import unittest
from pathlib import Path

from mmd_tools.core.native import is_rig_primitive_available
from mmd_tools.converters.native_rig_builder import (
    NativeRigPrimitives,
    RigManifest,
    build_ik_mini_chain,
)

_TEST_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_PMX_PATH = _TEST_DATA_DIR / "mmt_test_model.pmx"
_LUMINE_PMX_PATH = _TEST_DATA_DIR / "Lumine" / "Lumine.pmx"


def _read_pmx(path: Path) -> bytes:
    if not path.exists():
        return b""
    return path.read_bytes()


class TestRigManifest(unittest.TestCase):
    @unittest.skipUnless(is_rig_primitive_available(), "DLL not available")
    def test_from_pmx_bytes(self):
        pmx_bytes = _read_pmx(_PMX_PATH)
        if not pmx_bytes:
            self.skipTest("fixture not found")
        manifest = RigManifest.from_pmx_bytes(pmx_bytes)
        self.assertIsNotNone(manifest)
        self.assertGreater(manifest.bone_count, 0)
        self.assertEqual(len(manifest.bones), manifest.bone_count)

    def test_from_invalid_bytes(self):
        manifest = RigManifest.from_pmx_bytes(b"garbage")
        self.assertIsNone(manifest)


@unittest.skipUnless(is_rig_primitive_available(), "DLL not available")
class TestNativeRigPrimitives(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lumine_bytes = _read_pmx(_LUMINE_PMX_PATH)
        cls.mmt_bytes = _read_pmx(_PMX_PATH)

    def test_from_pmx_bytes_lumine(self):
        if not self.lumine_bytes:
            self.skipTest("Lumine PMX not found")
        prims = NativeRigPrimitives.from_pmx_bytes(self.lumine_bytes)
        self.assertIsNotNone(prims)
        self.assertIsNotNone(prims.manifest)
        prims.free()

    def test_ik_chains_built(self):
        if not self.lumine_bytes:
            self.skipTest("Lumine PMX not found")
        prims = NativeRigPrimitives.from_pmx_bytes(self.lumine_bytes)
        if prims.manifest.ik_chain_count > 0:
            self.assertGreater(len(prims.ik_chains), 0)
            chain, mapping = prims.ik_chains[0]
            self.assertIn("pmx_to_slot", mapping)
            self.assertIn("controller_pmx_index", mapping)
            self.assertIn("target_pmx_index", mapping)
            self.assertGreater(chain.bone_count, 0)
        prims.free()

    def test_append_solvers_built(self):
        if not self.lumine_bytes:
            self.skipTest("Lumine PMX not found")
        prims = NativeRigPrimitives.from_pmx_bytes(self.lumine_bytes)
        if prims.manifest.grant_count > 0:
            self.assertGreater(len(prims.append_solvers), 0)
            solver, info = prims.append_solvers[0]
            self.assertIn("target_pmx_index", info)
            self.assertIn("source_pmx_index", info)
            self.assertIn("ratio", info)
        prims.free()

    def test_mini_chain_slot_mapping(self):
        if not self.lumine_bytes:
            self.skipTest("Lumine PMX not found")
        manifest = RigManifest.from_pmx_bytes(self.lumine_bytes)
        if not manifest.ik_chains:
            self.skipTest("No IK chains in model")

        ik_def = manifest.ik_chains[0]
        result = build_ik_mini_chain(manifest, ik_def)
        self.assertIsNotNone(result)
        chain, mapping = result

        for slot, pmx_idx in mapping["slot_to_pmx"].items():
            self.assertEqual(mapping["pmx_to_slot"][pmx_idx], slot)

        self.assertIn(ik_def["controllerBoneIndex"], mapping["pmx_to_slot"])
        self.assertIn(ik_def["targetBoneIndex"], mapping["pmx_to_slot"])

        chain.free()

    def test_from_mmt_model(self):
        if not self.mmt_bytes:
            self.skipTest("mmt test model not found")
        prims = NativeRigPrimitives.from_pmx_bytes(self.mmt_bytes)
        self.assertIsNotNone(prims)
        prims.free()


if __name__ == "__main__":
    unittest.main()
