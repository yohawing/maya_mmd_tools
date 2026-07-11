"""
mmd-anim rig primitive API (rig_spec, ik_chain, append_solver) のユニットテスト。

DLL が利用できない環境でも安全に skip する。
"""

import math
import ctypes
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from mmd_tools.core.native import (
    MmdAppendSolver,
    MmdIkChain,
    MmdRigSpec,
    is_rig_primitive_available,
)
from mmd_tools.core.native import mmd_anim_runtime as rt
from mmd_tools.core.native.mmd_anim_runtime_types import MmdRuntimeFfiRigBoneLocalAxisV2
from mmd_tools.core.native.mmd_anim_runtime_signatures import setup_rig_primitive_signatures

_TEST_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_PMX_PATH = _TEST_DATA_DIR / "mmt_test_model.pmx"


def _read_pmx_bytes(path: Path) -> bytes:
    if not path.exists():
        return b""
    return path.read_bytes()


class TestRigPrimitiveAvailability(unittest.TestCase):
    class _FakeFunction:
        def __call__(self, *_args):
            return None

    @staticmethod
    def _bones(local_axis_marker=None):
        bone = {"parent_slot": -1, "rest_position": [0.0, 0.0, 0.0]}
        if local_axis_marker is not None:
            bone["local_axis"] = local_axis_marker
        return [bone]

    @staticmethod
    def _links():
        return [{"bone_slot": 0, "has_angle_limit": False}]

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

    def test_package_availability_uses_runtime_getter_patch(self):
        """core.native API は mmd_anim_runtime 側の patched getter 互換を保つ。"""
        class FakeLib:
            @staticmethod
            def mmd_runtime_ik_chain_create():
                return None

        with patch.object(rt, "get_mmd_runtime_library", return_value=FakeLib()):
            self.assertTrue(rt.is_rig_primitive_available())
            self.assertTrue(is_rig_primitive_available())

    def test_class_factories_use_runtime_getter_patch(self):
        """re-export 後も class factory は runtime module の patched getter を使う。"""
        calls = []

        class FakeLib:
            @staticmethod
            def mmd_runtime_append_solver_create(_config):
                calls.append("create")
                return 123

            @staticmethod
            def mmd_runtime_append_solver_free(_handle):
                calls.append("free")

        fake = FakeLib()
        with patch.object(rt, "get_mmd_runtime_library", return_value=fake):
            solver = MmdAppendSolver.create(ratio=0.5)

        self.assertIsNotNone(solver)
        self.assertIs(solver._lib, fake)
        self.assertEqual(calls, ["create"])
        solver.free()
        self.assertEqual(calls, ["create", "free"])

    def test_local_axis_v2_struct_matches_c_abi_layout(self):
        self.assertEqual(MmdRuntimeFfiRigBoneLocalAxisV2.has_local_axis.offset, 0)
        self.assertEqual(MmdRuntimeFfiRigBoneLocalAxisV2.local_axis_x_xyz.offset, 4)
        self.assertEqual(MmdRuntimeFfiRigBoneLocalAxisV2.local_axis_z_xyz.offset, 16)
        self.assertEqual(ctypes.sizeof(MmdRuntimeFfiRigBoneLocalAxisV2), 28)

    def test_v2_signature_matches_header_argument_order(self):
        fake = SimpleNamespace(
            mmd_runtime_ik_chain_create_v2=self._FakeFunction(),
        )
        setup_rig_primitive_signatures(fake)
        argtypes = fake.mmd_runtime_ik_chain_create_v2.argtypes
        self.assertEqual(len(argtypes), 8)
        self.assertEqual(argtypes[1], ctypes.c_size_t)
        self.assertEqual(argtypes[2]._type_, MmdRuntimeFfiRigBoneLocalAxisV2)
        self.assertEqual(argtypes[3], ctypes.c_uint32)

    def test_ik_create_selects_v2_and_preserves_slot_axis_values(self):
        calls = []

        class FakeLib:
            @staticmethod
            def mmd_runtime_ik_chain_create(*_args):
                calls.append(("legacy",))
                return 11

            @staticmethod
            def mmd_runtime_ik_chain_create_v2(*args):
                axes = args[2]
                calls.append(("v2", bool(axes[0].has_local_axis), list(axes[0].local_axis_x_xyz), list(axes[0].local_axis_z_xyz)))
                return 12

        with patch.object(rt, "get_mmd_runtime_library", return_value=FakeLib()):
            chain = MmdIkChain.create(
                self._bones({"x": [1.0, 2.0, 3.0], "z": [4.0, 5.0, 6.0]}),
                0,
                self._links(),
                1,
                1.0,
            )

        self.assertIsNotNone(chain)
        self.assertEqual(calls, [("v2", True, [1.0, 2.0, 3.0], [4.0, 5.0, 6.0])])

    def test_ik_create_uses_legacy_without_axes_or_v2_symbol(self):
        calls = []

        class FakeLib:
            @staticmethod
            def mmd_runtime_ik_chain_create(*_args):
                calls.append("legacy")
                return 13

        for bones in (self._bones(), self._bones({"x": [1.0, 0.0, 0.0], "z": [0.0, 0.0, 1.0]})):
            with patch.object(rt, "get_mmd_runtime_library", return_value=FakeLib()):
                self.assertIsNotNone(MmdIkChain.create(bones, 0, self._links(), 1, 1.0))
        self.assertEqual(calls, ["legacy", "legacy"])

    def test_ik_create_rejects_malformed_local_axis_without_calling_ffi(self):
        class FakeLib:
            mmd_runtime_ik_chain_create = MagicMock(return_value=14)
            mmd_runtime_ik_chain_create_v2 = MagicMock(return_value=15)

        for malformed in (
            {"x": [1.0, 0.0], "z": [0.0, 0.0, 1.0]},
            {"x": [1.0, 0.0, 0.0]},
            {"x": [1.0, 0.0, float("nan")], "z": [0.0, 0.0, 1.0]},
        ):
            fake = FakeLib()
            with self.subTest(local_axis=malformed), patch.object(rt, "get_mmd_runtime_library", return_value=fake):
                self.assertIsNone(MmdIkChain.create(self._bones(malformed), 0, self._links(), 1, 1.0))
                fake.mmd_runtime_ik_chain_create.assert_not_called()
                fake.mmd_runtime_ik_chain_create_v2.assert_not_called()


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
