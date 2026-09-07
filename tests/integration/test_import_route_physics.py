"""Live physics parity for the Python and C++ PMX import entries.

The import route is the only variable in these tests.  Physics assertions use
the imported registry-owned solver and compare its live output with the
native PMX stepping oracle; descriptor provenance alone is not sufficient.
"""

from __future__ import annotations

import math
from pathlib import Path

from maya import cmds

from mmd_tools.core.constants import (
    ATTR_MMD_MODEL_NAME,
)
from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.core.model_registry import (
    REGISTRY_CATEGORY_PHYSICS,
    list_model_registry_members,
)
from mmd_tools.core.native.mmd_anim_runtime import is_native_physics_available
from mmd_tools.core.native.mmd_anim_runtime_handles import MmdRuntimePhysicsWorld
from mmd_tools.core.coordinate_transform import mmd_matrix_to_maya
from tests.common.import_route import ImportRoute, import_pmx_via_route
from tests.common.maya_test_base import MayaTestBase
from tests.integration.test_physics_dag_parity import TestPhysicsDagParity
from tests.integration.test_physics_solver_node import (
    _connect_enabled_world,
    _model_physics_solvers,
    _solver_world_gravity,
)


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "physics" / "test_hair_physics.pmx"
LIVE_STEPS = 30
LIVE_FRAME_INCREMENT = 0.5
DIRECT_MATRIX_DELTA = 1e-5
NATIVE_ORACLE_MATRIX_DELTA = 0.01
DYNAMIC_RESPONSE_DELTA = 0.01


class TestImportRoutePhysics(MayaTestBase):
    """Apply one live-physics contract to both PMX import entries."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not FIXTURE_PATH.exists():
            raise RuntimeError(f"physics fixture not found: {FIXTURE_PATH}")
        try:
            native_available = bool(is_native_physics_available())
        except Exception as exc:
            raise RuntimeError("native physics availability check failed") from exc
        if not native_available:
            raise RuntimeError(
                "native physics DLL is required for import-route live physics parity"
            )

        plugin_path = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
        cls.load_plugin(str(plugin_path))
        cls.pmx = parse_pmx_file(str(FIXTURE_PATH), use_native_pmx_parse=False)
        cls.pmx_bytes = FIXTURE_PATH.read_bytes()

    def _import(self, route: ImportRoute, *, import_physics: bool) -> str:
        return import_pmx_via_route(
            self,
            str(FIXTURE_PATH),
            route,
            options={
                "import_physics": import_physics,
                "import_morphs": False,
            },
        )

    def _registry_solver(self, root: str) -> str:
        members = list_model_registry_members(root, REGISTRY_CATEGORY_PHYSICS)
        self.assertIsNotNone(members, "physics registry must be present")
        solvers = [
            node
            for node in members or []
            if cmds.objExists(node) and cmds.nodeType(node) == "mmdPhysicsSolver"
        ]
        self.assertEqual(len(solvers), 1, f"registry physics members: {members}")
        self.assertEqual(set(solvers), set(_model_physics_solvers(root)))
        return str(solvers[0])

    def _solver_world(self, solver: str) -> str:
        source = cmds.connectionInfo(
            f"{solver}.inWorldSettings", sourceFromDestination=True
        )
        self.assertTrue(source, f"solver has no World connection: {solver}")
        world = str(source).rsplit(".", 1)[0]
        self.assertEqual(cmds.nodeType(world), "mmdPhysicsWorldShape")
        return world

    def _assert_finite_matrix(self, matrix, label: str) -> None:
        self.assertIsNotNone(matrix, label)
        self.assertTrue(matrix, label)
        self.assertTrue(
            all(math.isfinite(float(value)) for value in matrix),
            label,
        )

    def _assert_matrices_close(
        self, actual, expected, label: str, *, delta=DIRECT_MATRIX_DELTA
    ) -> None:
        self.assertEqual(len(actual), len(expected), label)
        for index, (actual_value, expected_value) in enumerate(zip(actual, expected)):
            self.assertAlmostEqual(
                float(actual_value),
                float(expected_value),
                delta=delta,
                msg=f"{label}[{index}]",
            )

    def _native_live_oracle(self, gravity):
        """Reuse the DAG parity split-evaluation helper for the PMX oracle."""
        world = MmdRuntimePhysicsWorld.from_pmx_bytes(self.pmx_bytes)
        self.assertIsNotNone(world, "native PMX physics world creation")
        try:
            self.assertTrue(world.set_gravity(gravity))
            raw_matrices = TestPhysicsDagParity._run_split_eval_steps(
                self,
                world,
                num_steps=LIVE_STEPS,
            )
        finally:
            world.free()
        flat = []
        for raw_matrix in raw_matrices:
            flat.extend(mmd_matrix_to_maya(raw_matrix))
        self._assert_finite_matrix(flat, "native live oracle matrices")
        return flat

    def _run_live_contract(self, route: ImportRoute):
        root = self._import(route, import_physics=True)
        solver = self._registry_solver(root)
        world = self._solver_world(solver)

        self.assertFalse(cmds.getAttr(f"{world}.enable"))
        self.assertTrue(cmds.getAttr(f"{solver}.enable"))
        self.assertEqual(int(cmds.getAttr(f"{solver}.inputMode")), 1)

        cmds.currentTime(0)
        self.assertFalse(cmds.getAttr(f"{solver}.outSolved"))
        self.assertEqual(cmds.getAttr(f"{solver}.outStatus"), "disabled")

        # The production World control is the physics OFF switch.  Enabling it
        # through the existing solver helper also validates both connections.
        _connect_enabled_world(solver)
        gravity = _solver_world_gravity(solver)
        self.assertGreater(
            max(abs(float(value)) for value in gravity),
            0.0,
            "live physics must use a non-zero gravity vector",
        )

        cmds.currentUnit(time="ntsc")
        cmds.currentTime(0)
        self.assertTrue(cmds.getAttr(f"{solver}.outSolved"))
        before = list(cmds.getAttr(f"{solver}.outBoneMatrices"))
        self._assert_finite_matrix(before, f"{route.value} rest matrices")
        self.assertEqual(int(cmds.getAttr(f"{solver}.outBoneCount")), len(self.pmx.bones))

        for step in range(1, LIVE_STEPS + 1):
            # Half-frame increments make each DG step 1/60 s, matching the
            # shared DAG parity helper while still exercising sequential time.
            cmds.currentTime(step * LIVE_FRAME_INCREMENT)
            self.assertTrue(
                cmds.getAttr(f"{solver}.outSolved"),
                f"{route.value} live step {step}",
            )

        after = list(cmds.getAttr(f"{solver}.outBoneMatrices"))
        self._assert_finite_matrix(after, f"{route.value} live matrices")
        self.assertEqual(cmds.getAttr(f"{solver}.outStatus"), "stepped")

        dynamic_bones = {
            int(body.related_bone_index)
            for body in self.pmx.rigid_bodies
            if int(body.physics_mode) != 0
            and 0 <= int(body.related_bone_index) < len(self.pmx.bones)
        }
        self.assertTrue(dynamic_bones, "fixture must contain dynamic physics bones")
        changed_bones = 0
        for bone_index in dynamic_bones:
            offset = bone_index * 16
            delta = max(
                abs(
                    float(after[offset + component])
                    - float(before[offset + component])
                )
                for component in range(16)
            )
            if delta > DYNAMIC_RESPONSE_DELTA:
                changed_bones += 1
        self.assertGreater(changed_bones, 0, f"{route.value} live physics response")

        oracle = self._native_live_oracle(gravity)
        self._assert_matrices_close(
            after,
            oracle,
            f"{route.value} native live parity",
            delta=NATIVE_ORACLE_MATRIX_DELTA,
        )
        return {
            "root": root,
            "solver": solver,
            "world": world,
            "gravity": gravity,
            "before": before,
            "after": after,
        }

    def _assert_save_reopen_contract(self, route: ImportRoute, result) -> None:
        """Reopen one already-simulated route without importing it again."""
        scene_path = self.get_temp_filename(
            f"import_route_physics_{route.value}.ma"
        )
        cmds.file(rename=scene_path)
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(new=True, force=True)
        cmds.file(scene_path, open=True, force=True)

        roots = [
            node
            for node in cmds.ls(type="transform", long=True) or []
            if cmds.attributeQuery(ATTR_MMD_MODEL_NAME, node=node, exists=True)
            and cmds.getAttr(f"{node}.{ATTR_MMD_MODEL_NAME}")
            == self.pmx.header.model_name
        ]
        self.assertEqual(len(roots), 1)
        reopened_root = roots[0]
        reopened_solver = self._registry_solver(reopened_root)
        reopened_world = self._solver_world(reopened_solver)
        self.assertTrue(cmds.getAttr(f"{reopened_world}.enable"))

        cmds.currentUnit(time="ntsc")
        cmds.currentTime(0)
        self.assertTrue(cmds.getAttr(f"{reopened_solver}.outSolved"))
        reset = list(cmds.getAttr(f"{reopened_solver}.outBoneMatrices"))
        self._assert_finite_matrix(reset, f"{route.value} reopened reset matrices")
        self._assert_matrices_close(
            reset,
            result["before"],
            f"{route.value} reset after reopen",
        )

        for step in range(1, LIVE_STEPS + 1):
            cmds.currentTime(step * LIVE_FRAME_INCREMENT)
            self.assertTrue(cmds.getAttr(f"{reopened_solver}.outSolved"))
        reopened_after = list(cmds.getAttr(f"{reopened_solver}.outBoneMatrices"))
        self._assert_matrices_close(
            reopened_after,
            result["after"],
            f"{route.value} live after reopen",
        )

    def test_python_and_cpp_routes_match_live_physics_and_native_oracle(self):
        """The same off/on, live, and reopen contract must agree for both routes."""
        results = {}
        for route in ImportRoute:
            with self.subTest(route=route.value):
                cmds.file(new=True, force=True)
                result = self._run_live_contract(route)
                self._assert_save_reopen_contract(route, result)
                results[route] = result

        self._assert_matrices_close(
            results[ImportRoute.PYTHON]["before"],
            results[ImportRoute.CPP]["before"],
            "Python/C++ rest matrices",
        )
        self._assert_matrices_close(
            results[ImportRoute.PYTHON]["after"],
            results[ImportRoute.CPP]["after"],
            "Python/C++ live matrices",
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
