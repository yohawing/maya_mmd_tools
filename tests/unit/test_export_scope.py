"""Verify model-root ownership boundaries used by export collectors."""

import unittest
from unittest import mock

from tests.common.maya_stub import install_maya_stub

install_maya_stub()

from mmd_tools.converters import export_scene_collector as export_scene_collector_module  # noqa: E402
from mmd_tools.converters import morph_converter as morph_converter_module  # noqa: E402
from mmd_tools.converters.export_scene_collector import ExportSceneCollector  # noqa: E402
from mmd_tools.converters.morph_converter import MorphConverter  # noqa: E402


class TestExportScope(unittest.TestCase):
    """Keep root-scoped network morph collection explicit and testable."""

    def test_model_collector_passes_root_to_morph_collection(self):
        roots = []

        class FakeMorphConverter:
            def collect_morphs_from_scene_for_export(self, *, root_group=None):
                roots.append(root_group)
                return []

        mesh_data = {
            "vertices": [
                {
                    "position": [0.0, 0.0, 0.0],
                    "normal": [0.0, 1.0, 0.0],
                    "uv": [0.0, 0.0],
                    "bone_indices": [0],
                    "bone_weights": [1.0],
                },
                {
                    "position": [1.0, 0.0, 0.0],
                    "normal": [0.0, 1.0, 0.0],
                    "uv": [1.0, 0.0],
                    "bone_indices": [0],
                    "bone_weights": [1.0],
                },
                {
                    "position": [0.0, 0.0, 1.0],
                    "normal": [0.0, 1.0, 0.0],
                    "uv": [0.0, 1.0],
                    "bone_indices": [0],
                    "bone_weights": [1.0],
                },
            ],
            "faces": [[0, 1, 2]],
            "materials": [{"name": "material", "face_count": 3}],
            "bones": [],
            "morphs": [],
        }

        with (
            mock.patch.object(export_scene_collector_module, "MorphConverter", FakeMorphConverter),
            mock.patch.object(export_scene_collector_module, "_list_export_mesh_shapes", return_value=["mesh"]),
            mock.patch.object(export_scene_collector_module, "_collect_model_bones", return_value=[]),
            mock.patch.object(export_scene_collector_module, "_get_model_name", return_value="Hero"),
            mock.patch.object(export_scene_collector_module, "_collect_display_frames", return_value=[]),
            mock.patch.object(
                ExportSceneCollector,
                "collect_from_mesh",
                return_value=mesh_data,
            ),
            mock.patch(
                "mmd_tools.converters.physics_export_collector.collect_physics_from_scene",
                return_value=([], []),
            ),
        ):
            payload = ExportSceneCollector().collect_from_model_root("|hero:model_ROOT")

        self.assertEqual(roots, ["|hero:model_ROOT"])
        self.assertEqual(payload["model_name"], "Hero")

    def test_network_morph_collection_passes_selected_root(self):
        converter = object.__new__(MorphConverter)
        converter.logger = mock.Mock()

        with mock.patch.object(
            morph_converter_module,
            "iter_morph_network_metadata",
            return_value=[],
        ) as iterator:
            result = converter.collect_morphs_from_scene_for_export(
                root_group="|hero:model_ROOT",
            )

        self.assertEqual(result, [])
        iterator.assert_called_once_with(
            root_group="|hero:model_ROOT",
            morph_types={"bone", "material"},
        )


if __name__ == "__main__":
    unittest.main()
