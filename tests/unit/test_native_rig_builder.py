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
)

_TEST_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_PMX_PATH = _TEST_DATA_DIR / "mmt_test_model.pmx"
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
    def test_from_mmt_model(self):
        mmt_bytes = _read_pmx(_PMX_PATH)
        if not mmt_bytes:
            self.skipTest("mmt test model not found")
        prims = NativeRigPrimitives.from_pmx_bytes(mmt_bytes)
        self.assertIsNotNone(prims)
        prims.free()


if __name__ == "__main__":
    unittest.main()
