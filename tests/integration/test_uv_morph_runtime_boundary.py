"""Maya integration probe for the current PMX UV morph runtime boundary.

The test creates and fresh-imports a real PMX containing one type-3 UV morph.
It records the intentional current contract: the imported UV metadata survives,
but changing the morph weight does not evaluate Maya mesh UVs.
"""

from pathlib import Path

from maya import cmds
from maya.api import OpenMaya as om

from mmd_tools.converters.morph_converter import MorphConverter
from mmd_tools.core import settings_keys
from mmd_tools.core.pmx_data.morph import PmxMorphType
from mmd_tools.core.settings import settings
from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.io.mmd_importer import import_mmd_file
from mmd_tools.io.pmx_exporter import PmxExporter
from tests.common.maya_test_base import MayaTestBase


class TestUVMorphRuntimeBoundary(MayaTestBase):
    """Verify PMX UV morph metadata without claiming UV runtime evaluation."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plugin_path = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
        cls.load_plugin(str(plugin_path))

    def _read_uv_set(self, mesh_shape: str):
        """Read all values from the imported mesh's first explicit UV set."""
        selection = om.MSelectionList()
        selection.add(mesh_shape)
        mesh_fn = om.MFnMesh(selection.getDagPath(0))
        uv_sets = list(mesh_fn.getUVSetNames())
        self.assertTrue(uv_sets, "fresh-imported mesh has no UV set to read")
        uv_set = uv_sets[0]
        u_values, v_values = mesh_fn.getUVs(uv_set)
        self.assertEqual(len(u_values), len(v_values))
        self.assertGreater(len(u_values), 0, "fresh-imported UV set has no values")
        return tuple((float(u), float(v)) for u, v in zip(u_values, v_values))

    def test_type3_uv_morph_preserves_metadata_without_uv_runtime_evaluation(self):
        """Type-3 UV offsets survive import while UV values stay unchanged at weight 1."""
        uv_offset = (0.125, -0.25, 0.375, -0.5)
        source_vertex_index = 1
        pmx_path = self.get_temp_filename("uv_morph_runtime_boundary.pmx")

        PmxExporter().export_pmx_model(
            pmx_path,
            {
                "model_name": "UVMorphRuntimeBoundary",
                "vertices": [
                    {"position": [0.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [0.125, 0.25]},
                    {"position": [1.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [0.375, 0.5]},
                    {"position": [0.0, 1.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [0.625, 0.75]},
                ],
                "faces": [[0, 1, 2]],
                "morphs": [
                    {
                        "type": "uv",
                        "name": "UV runtime boundary",
                        "name_english": "UV runtime boundary",
                        "panel": 4,
                        "offsets": [
                            {
                                "vertex_index": source_vertex_index,
                                "uv_offset": list(uv_offset),
                            }
                        ],
                    }
                ],
            },
        )

        written_pmx = parse_pmx_file(
            pmx_path,
            use_native_pmx_parse=False,
            require_native_pmx_parse=False,
        )
        self.assertEqual(len(written_pmx.morphs), 1)
        self.assertEqual(int(written_pmx.morphs[0].morph_type), int(PmxMorphType.UVMorph))
        self.assertEqual(written_pmx.morphs[0].offsets[0]["vertex_index"], source_vertex_index)
        self.assertEqual(tuple(written_pmx.morphs[0].offsets[0]["uv_offset"]), uv_offset)

        view_settings = (
            settings_keys.IMPORT_MODEL_CREATE_MMD_SHADERS,
            settings_keys.IMPORT_VIEW_SETUP_COLOR_MANAGEMENT,
            settings_keys.IMPORT_VIEW_SETUP_TRANSPARENCY,
        )
        saved_view_settings = {key: settings.get(key) for key in view_settings}
        try:
            for key in view_settings:
                settings.set(key, False)

            cmds.file(new=True, force=True)
            root = import_mmd_file(
                pmx_path,
                options={
                    "create_mmd_shaders": False,
                    "import_morphs": True,
                    "import_physics": False,
                    "setup_rig": False,
                    "setup_bone_orientation": False,
                    "use_cpp_fast_load": False,
                    "use_native_pmx_parse": False,
                    "require_native_pmx_parse": False,
                },
            )
            self.assertIsNotNone(root)

            collected_morphs = MorphConverter().collect_morphs_from_scene_for_export(
                root_group=root,
                require_contiguous=False,
            )
            self.assertEqual(len(collected_morphs), 1)
            collected = collected_morphs[0]
            self.assertEqual(collected["type"], "uv")
            self.assertEqual(len(collected["offsets"]), 1)
            collected_offset = collected["offsets"][0]
            self.assertEqual(collected_offset["vertex_index"], source_vertex_index)
            self.assertEqual(len(collected_offset["uv_offset"]), 4)
            for actual, expected in zip(collected_offset["uv_offset"], uv_offset):
                self.assertAlmostEqual(actual, expected, places=6)

            mesh_shapes = [
                shape
                for shape in (cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or [])
                if not cmds.getAttr(f"{shape}.intermediateObject")
            ]
            self.assertEqual(len(mesh_shapes), 1)

            controllers = cmds.listConnections(
                f"{root}.mmd_morph_controller",
                source=True,
                destination=False,
            ) or []
            self.assertEqual(len(controllers), 1)
            controller = controllers[0]

            cmds.setAttr(f"{controller}.inputWeight[0]", 0.0)
            uv_at_weight_zero = self._read_uv_set(mesh_shapes[0])
            cmds.setAttr(f"{controller}.inputWeight[0]", 1.0)
            uv_at_weight_one = self._read_uv_set(mesh_shapes[0])

            self.assertEqual(
                uv_at_weight_zero,
                uv_at_weight_one,
                "current contract: PMX UV morph metadata imports, but UV runtime evaluation is not implemented",
            )
        finally:
            for key, value in saved_view_settings.items():
                settings.set(key, value)
