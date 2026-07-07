"""bake_mode setting behavior tests for model and VMD imports.

These tests keep Maya isolated behind stubs/mocks and verify that
``import.rig.bake_mode`` only affects animation import path selection.
"""

import tempfile
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

import mmd_tools.converters.vmd_converter as vmd_converter_module  # noqa: E402
from mmd_tools.converters.vmd_converter import VmdConverter  # noqa: E402
from mmd_tools.core.settings import settings  # noqa: E402
from mmd_tools.io.mmd_importer import import_mmd_file  # noqa: E402
from mmd_tools.io.pmx_importer import import_pmx_file  # noqa: E402
from mmd_tools.io.vmd_importer import import_vmd_file  # noqa: E402
from mmd_tools.services.settings_service import SettingsService  # noqa: E402


class _FakeHeader:
    model_name = "TestModel"
    model_name_english = "TestModel"
    comment = ""
    comment_english = ""

    def get_name(self):
        return self.model_name


class _FakePmxParser:
    header = _FakeHeader()
    bones = []
    rigid_bodies = []


class TestBakeModeBehavior(unittest.TestCase):
    """import.rig.bake_mode のモデル import / animation import 契約を検証する。"""

    def setUp(self):
        self._saved = {
            "ui.general.development_mode": settings.get("ui.general.development_mode", False),
            "import.rig.bake_mode": settings.get("import.rig.bake_mode", False),
        }

    def tearDown(self):
        for key, value in self._saved.items():
            settings.set(key, value)

    def test_model_import_builds_rig_even_when_bake_mode_enabled(self):
        settings.set("ui.general.development_mode", True)
        settings.set("import.rig.bake_mode", True)
        options = SettingsService().build_pmx_import_options()

        mesh_converter = MagicMock()
        mesh_converter.convert_pmx_mesh.return_value = ("mesh_group", "mesh")
        mesh_converter.created_shaders = []
        mesh_converter.profile = {}
        mesh_converter.unresolved_textures = []
        mesh_converter.unresolved_texture_count = 0

        morph_converter = MagicMock()
        morph_converter.convert_pmx_morphs.return_value = {
            "morphs_converted": 0,
            "total_morphs": 0,
            "blend_shape_nodes": [],
            "bone_morph_nodes": [],
            "material_morph_nodes": [],
        }

        bone_converter = MagicMock()
        bone_converter.convert_pmx_bones.return_value = (["joint"], "skinCluster")

        with ExitStack() as stack:
            stack.enter_context(
                patch("mmd_tools.io.pmx_importer.NamespaceUtils.namespace_context", return_value=nullcontext())
            )
            stack.enter_context(patch("mmd_tools.io.model_import_pipeline.cmds.group", return_value="model_root"))
            stack.enter_context(patch("mmd_tools.io.model_import_pipeline.cmds.select"))
            stack.enter_context(patch("mmd_tools.io.model_import_pipeline.cmds.refresh"))
            stack.enter_context(patch("mmd_tools.io.model_import_pipeline.maya_attribute_utils.set_custom_attributes"))
            stack.enter_context(patch("mmd_tools.io.model_import_pipeline.maya_viewport_utils.setup_mmd_color_management"))
            stack.enter_context(patch("mmd_tools.io.model_import_pipeline.maya_viewport_utils.setup_mmd_transparency"))
            stack.enter_context(patch("mmd_tools.io.model_import_pipeline.sync_dx11_generated_uniforms", return_value=0))
            stack.enter_context(patch("mmd_tools.io.pmx_importer.MeshConverter", return_value=mesh_converter))
            stack.enter_context(patch("mmd_tools.io.pmx_importer.MorphConverter", return_value=morph_converter))
            stack.enter_context(patch("mmd_tools.io.pmx_importer.BoneConverter", return_value=bone_converter))
            root = import_pmx_file(_FakePmxParser(), "model.pmx", options=options)

        self.assertEqual(root, "model_root")
        kwargs = bone_converter.convert_pmx_bones.call_args.kwargs
        self.assertTrue(kwargs["setup_rig"])
        self.assertTrue(kwargs["setup_bone_orientation"])

    def test_animation_import_passes_bake_mode_false_for_rig_path(self):
        settings.set("import.rig.bake_mode", False)
        options = SettingsService().build_vmd_import_options(target_model="model_root")

        converter = MagicMock()
        converter.convert.return_value = True

        with tempfile.TemporaryDirectory() as temp_dir:
            vmd_path = Path(temp_dir) / "motion.vmd"
            vmd_path.write_bytes(b"Vocaloid Motion Data 0002\x00")

            with patch("mmd_tools.io.vmd_importer.VmdConverter", return_value=converter):
                result = import_vmd_file(object(), str(vmd_path), options)

        self.assertTrue(result)
        self.assertFalse(converter.convert.call_args.kwargs["bake_mode"])

        vmd_converter = VmdConverter()
        with patch.object(vmd_converter_module, "HAS_MMD_RUNTIME", True), patch.object(
            vmd_converter_module,
            "is_mmd_runtime_available",
            return_value=True,
        ):
            self.assertFalse(
                vmd_converter._should_use_mmd_runtime_bake(
                    vmd_bytes=b"vmd",
                    pmx_bytes=b"pmx",
                    pmx_path=None,
                    live_rig_target=False,
                    bake_mode=False,
                )
            )

    def test_animation_import_passes_bake_mode_true_for_baked_path(self):
        settings.set("import.rig.bake_mode", True)
        options = SettingsService().build_vmd_import_options(target_model="model_root")

        converter = MagicMock()
        converter.convert.return_value = True

        with tempfile.TemporaryDirectory() as temp_dir:
            vmd_path = Path(temp_dir) / "motion.vmd"
            vmd_path.write_bytes(b"Vocaloid Motion Data 0002\x00")

            with patch("mmd_tools.io.vmd_importer.VmdConverter", return_value=converter):
                result = import_vmd_file(object(), str(vmd_path), options)

        self.assertTrue(result)
        self.assertTrue(converter.convert.call_args.kwargs["bake_mode"])

        vmd_converter = VmdConverter()
        with patch.object(vmd_converter_module, "HAS_MMD_RUNTIME", True), patch.object(
            vmd_converter_module,
            "is_mmd_runtime_available",
            return_value=True,
        ):
            self.assertTrue(
                vmd_converter._should_use_mmd_runtime_bake(
                    vmd_bytes=b"vmd",
                    pmx_bytes=b"pmx",
                    pmx_path=None,
                    live_rig_target=True,
                    bake_mode=True,
                )
            )

    def test_vmd_bake_import_falls_back_to_raw_bytes_when_python_parse_fails(self):
        """bake mode の VMD は Python parser 失敗時も raw bytes runtime path に渡す。"""
        options = {
            "bake_mode": True,
            "target_model": "model_root",
            "pmx_bytes": b"pmx",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            vmd_path = Path(temp_dir) / "truncated.vmd"
            vmd_path.write_bytes(b"Vocaloid Motion Data 0002\x00")

            with patch("mmd_tools.io.mmd_importer.parse_mmd_file", side_effect=ValueError("truncated")), patch(
                "mmd_tools.io.mmd_importer.vmd_importer.import_vmd_file",
                return_value=True,
            ) as import_vmd:
                result = import_mmd_file(str(vmd_path), options=options)

        self.assertTrue(result)
        parser_arg, filepath_arg, options_arg = import_vmd.call_args.args
        self.assertEqual(filepath_arg, str(vmd_path))
        self.assertIs(options_arg, options)
        self.assertEqual(parser_arg.bone_frames, [])
        self.assertEqual(Path(parser_arg.source_file), vmd_path.resolve())


if __name__ == "__main__":
    unittest.main()
