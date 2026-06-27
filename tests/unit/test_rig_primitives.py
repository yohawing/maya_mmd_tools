"""
mmd-anim rig primitive API (rig_spec, ik_chain, append_solver) のユニットテスト。

DLL が利用できない環境でも安全に skip する。
"""

import math
import unittest
from pathlib import Path

from mmd_tools.core.native import (
    MmdAppendSolver,
    MmdIkChain,
    MmdRigSpec,
    is_rig_primitive_available,
)

_TEST_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_PMX_PATH = _TEST_DATA_DIR / "mmt_test_model.pmx"


def _read_pmx_bytes(path: Path) -> bytes:
    if not path.exists():
        return b""
    return path.read_bytes()


class TestRigPrimitiveAvailability(unittest.TestCase):
    def test_is_rig_primitive_available_returns_bool(self):
        result = is_rig_primitive_available()
        self.assertIsInstance(result, bool)

    def test_safe_fallback_when_unavailable(self):
        spec = MmdRigSpec.from_pmx_bytes(b"not-a-pmx")
        self.assertIsNone(spec)

        chain = MmdIkChain.create([], 0, [], 1, 1.0)
        self.assertIsNone(chain)

        solver = MmdAppendSolver.create(1.0)
        if not is_rig_primitive_available():
            self.assertIsNone(solver)


@unittest.skipUnless(is_rig_primitive_available(), "rig primitive DLL not available")
class TestRigSpec(unittest.TestCase):
    def test_manifest_from_pmx(self):
        pmx_bytes = _read_pmx_bytes(_PMX_PATH)
        if not pmx_bytes:
            self.skipTest("test PMX not found")

        spec = MmdRigSpec.from_pmx_bytes(pmx_bytes)
        self.assertIsNotNone(spec)

        manifest = spec.manifest_json()
        self.assertIsNotNone(manifest)
        self.assertIn("boneCount", manifest)
        self.assertIn("bones", manifest)
        self.assertIsInstance(manifest["bones"], list)
        self.assertGreater(manifest["boneCount"], 0)

        for bone in manifest["bones"]:
            self.assertIn("name", bone)
            self.assertIn("restPosition", bone)
            self.assertIsInstance(bone["restPosition"], list)
            self.assertEqual(len(bone["restPosition"]), 3)

        spec.free()

    def test_invalid_pmx_returns_none(self):
        spec = MmdRigSpec.from_pmx_bytes(b"garbage data")
        self.assertIsNone(spec)

    def test_empty_bytes_returns_none(self):
        spec = MmdRigSpec.from_pmx_bytes(b"")
        self.assertIsNone(spec)


@unittest.skipUnless(is_rig_primitive_available(), "rig primitive DLL not available")
class TestAppendSolver(unittest.TestCase):
    def test_create_and_solve_rotation(self):
        solver = MmdAppendSolver.create(ratio=0.5, affect_rotation=True)
        self.assertIsNotNone(solver)

        result = solver.solve(
            source_position=[0.0, 0.0, 0.0],
            source_rotation=[0.0, 0.0, 0.0, 1.0],
        )
        self.assertIsNotNone(result)
        out_pos, out_rot = result
        self.assertEqual(len(out_pos), 3)
        self.assertEqual(len(out_rot), 4)

        self.assertAlmostEqual(out_rot[3], 1.0, places=5)

        solver.free()

    def test_create_and_solve_translation(self):
        solver = MmdAppendSolver.create(
            ratio=1.0, affect_rotation=False, affect_translation=True
        )
        self.assertIsNotNone(solver)

        result = solver.solve(
            source_position=[2.0, 4.0, -6.0],
            source_rotation=[0.0, 0.0, 0.0, 1.0],
        )
        self.assertIsNotNone(result)
        out_pos, out_rot = result
        self.assertAlmostEqual(out_pos[0], 2.0, places=5)
        self.assertAlmostEqual(out_pos[1], 4.0, places=5)
        self.assertAlmostEqual(out_pos[2], -6.0, places=5)

        solver.free()

    def test_half_ratio_rotation(self):
        solver = MmdAppendSolver.create(ratio=0.5, affect_rotation=True)
        self.assertIsNotNone(solver)

        angle = math.pi / 2
        half_angle = angle / 2
        src_rot = [0.0, 0.0, math.sin(half_angle), math.cos(half_angle)]

        result = solver.solve(
            source_position=[0.0, 0.0, 0.0],
            source_rotation=src_rot,
        )
        self.assertIsNotNone(result)
        _, out_rot = result
        quarter_angle = angle / 4
        self.assertAlmostEqual(out_rot[2], math.sin(quarter_angle), places=4)
        self.assertAlmostEqual(out_rot[3], math.cos(quarter_angle), places=4)

        solver.free()


@unittest.skipUnless(is_rig_primitive_available(), "rig primitive DLL not available")
class TestIkChain(unittest.TestCase):
    def test_create_simple_chain(self):
        bones = [
            {"parent_slot": -1, "rest_position": [0.0, 8.0, 0.0]},
            {"parent_slot": 0, "rest_position": [0.0, 4.0, 0.0]},
            {"parent_slot": 1, "rest_position": [0.0, 0.0, 0.0]},
        ]
        links = [
            {
                "bone_slot": 1,
                "has_angle_limit": True,
                "angle_limit_min": [-3.14, 0.0, 0.0],
                "angle_limit_max": [-0.008, 0.0, 0.0],
            },
        ]
        chain = MmdIkChain.create(bones, target_bone_slot=2, links=links,
                                   iteration_count=40, limit_angle=2.0)
        self.assertIsNotNone(chain)
        self.assertEqual(chain.bone_count, 3)
        self.assertEqual(chain.link_count, 1)

        chain.free()

    def test_solve_identity(self):
        bones = [
            {"parent_slot": -1, "rest_position": [0.0, 8.0, 0.0]},
            {"parent_slot": 0, "rest_position": [0.0, 4.0, 0.0]},
            {"parent_slot": 1, "rest_position": [0.0, 0.0, 0.0]},
        ]
        links = [
            {"bone_slot": 1, "has_angle_limit": False},
        ]
        chain = MmdIkChain.create(bones, target_bone_slot=2, links=links,
                                   iteration_count=40, limit_angle=2.0)
        self.assertIsNotNone(chain)

        positions = [0.0] * 9
        rotations = [0.0, 0.0, 0.0, 1.0] * 3
        goal = [0.0, 0.0, 0.0]

        result = chain.solve(positions, rotations, goal)
        self.assertIsNotNone(result)
        out_rots, stats = result
        self.assertEqual(len(out_rots), 4)
        self.assertIn(stats.break_reason, (0, 1))

        chain.free()


if __name__ == "__main__":
    unittest.main()
