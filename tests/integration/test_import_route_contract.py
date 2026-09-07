"""Shared functional parity checks for Python and C++ PMX import entries."""

from __future__ import annotations

from pathlib import Path

from maya import cmds

from mmd_tools.core.mmd_parser import parse_pmx_file
from tests.common.import_contract_fixture import (
    assert_import_contract,
    assert_scene_reopen_contract,
    write_morph_contract_fixture,
)
from tests.common.import_route import ImportRoute, import_pmx_via_route
from tests.common.maya_test_base import MayaTestBase
from tests.common.test_fixture_provider import TestFixtureProvider


class TestImportRouteContract(MayaTestBase):
    """Run exactly the same source-derived assertions for both import routes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plugin_path = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
        cls.load_plugin(str(plugin_path))

    def setUp(self):
        super().setUp()
        from mmd_tools.core import settings

        settings.set("import.model.create_mmd_shaders", False)
        self.fixture_provider = TestFixtureProvider()

    def tearDown(self):
        self.fixture_provider.cleanup_temp_files()
        super().tearDown()

    def _run_contract_for_routes(self, filepath, pmx, *, expect_physics, options):
        for route in (ImportRoute.PYTHON, ImportRoute.CPP):
            with self.subTest(route=route.value, fixture=Path(filepath).name):
                cmds.file(new=True, force=True)
                root = import_pmx_via_route(self, filepath, route, options=options)
                assert_import_contract(self, root, pmx, expect_physics=expect_physics)
                assert_scene_reopen_contract(self, pmx, expect_physics=expect_physics)

    def test_morph_uv_and_metadata_contract_uses_one_assertion_suite(self):
        """Vertex/bone/material/group evaluate; UV, Flip, and Impulse remain metadata-scoped."""
        fixture_path = self.get_temp_filename("import_route_morph_contract.pmx")
        write_morph_contract_fixture(fixture_path)
        pmx = parse_pmx_file(fixture_path, use_native_pmx_parse=False)
        self._run_contract_for_routes(
            fixture_path,
            pmx,
            expect_physics=False,
            options={"import_physics": False, "import_morphs": True},
        )

    def test_mesh_skin_material_and_physics_contract_uses_one_assertion_suite(self):
        """Real fixture checks skin/material/indexed physics provenance."""
        fixture_path = self.fixture_provider.get_verified_pmx_file("yw_test_model")
        pmx = parse_pmx_file(fixture_path, use_native_pmx_parse=False)
        self._run_contract_for_routes(
            fixture_path,
            pmx,
            expect_physics=True,
            options={"import_physics": True, "import_morphs": True},
        )

    def test_multi_import_morph_ownership_and_shared_material_rejection(self):
        """Exercise the GUI oracle with vertex and non-vertex-only morph models."""
        from mmd_tools.core import model_registry
        from mmd_tools.core.pmx_data.morph import PmxMorphType
        from tools.smoke.maya_fast_import_authoring import _multi_import_contract

        for vertex_morphs in (True, False):
            fixture_path = self.get_temp_filename(f"multi_morph_{vertex_morphs}.pmx")
            write_morph_contract_fixture(fixture_path)
            if not vertex_morphs:
                pmx = parse_pmx_file(fixture_path, use_native_pmx_parse=False)
                pmx.morphs = [m for m in pmx.morphs if m.morph_type == PmxMorphType.BoneMorph]
                pmx.display_frames = []
                pmx.write_file(fixture_path)
            for route in ImportRoute:
                with self.subTest(route=route.value, vertex_morphs=vertex_morphs):
                    cmds.file(new=True, force=True)
                    options = {"import_physics": False, "import_morphs": True}
                    first = import_pmx_via_route(self, fixture_path, route, options=options)
                    second = import_pmx_via_route(self, fixture_path, route, options=options)
                    witness = _multi_import_contract(cmds, first, second)
                    for presence in witness["criticalPresence"].values():
                        self.assertTrue(presence["mmdMorphController"])
                        self.assertEqual(presence["blendShape"], vertex_morphs)
                    materials = model_registry.list_model_registry_members(
                        first, model_registry.REGISTRY_CATEGORY_MATERIAL,
                    )
                    self.assertTrue(materials)
                    model_registry.register_model_members(
                        model_registry.get_model_registry(second),
                        model_registry.REGISTRY_CATEGORY_MATERIAL, [materials[0]],
                    )
                    with self.assertRaisesRegex(RuntimeError, "shared across imported roots"):
                        _multi_import_contract(cmds, first, second)
