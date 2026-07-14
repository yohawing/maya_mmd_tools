"""Logging boundary tests for ModelImportPipeline internal phase details.

Namespace routing, no-data physics phase, and cleanup details must be DEBUG.
Behavior (return values / NamespaceUtils calls) must stay unchanged.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.io.model_import_pipeline import ModelImportPipeline  # noqa: E402
from mmd_tools.io import model_import_pipeline  # noqa: E402
from mmd_tools.converters import mesh_converter as mesh_converter_module  # noqa: E402
from mmd_tools.converters.mesh_converter import MeshConverter  # noqa: E402


def _message_templates(mock_log):
    # call[0] is args tuple (Py3.7-safe; _Call.args is 3.8+)
    return [call[0][0] for call in mock_log.call_args_list if call[0]]


class TestModelImportPipelineLogging(unittest.TestCase):
    """Internal pipeline routing details use DEBUG, not INFO."""

    def _make_pipeline(self, logger, options=None):
        return ModelImportPipeline(
            logger=logger,
            filepath="model.pmx",
            scale=1.0,
            options=options or {},
        )

    def test_custom_namespace_logs_at_debug_not_info(self):
        logger = MagicMock()
        pipeline = self._make_pipeline(logger, options={"use_namespace": True})

        with patch(
            "mmd_tools.io.model_import_pipeline.NamespaceUtils.ensure_unique_namespace",
            return_value="CustomNS",
        ) as mock_ensure:
            result = pipeline.resolve_namespace("Model", custom_namespace="CustomNS")

        self.assertEqual(result, "CustomNS")
        mock_ensure.assert_called_once_with("CustomNS")

        debug_messages = _message_templates(logger.debug)
        info_messages = _message_templates(logger.info)
        self.assertIn("Using custom namespace: %s", debug_messages)
        self.assertNotIn("Using custom namespace: %s", info_messages)

    def test_glsl_shader_refreshes_before_uniform_sync(self):
        logger = MagicMock()
        pipeline = self._make_pipeline(logger)
        converter = SimpleNamespace(
            has_dx11_shaders=False,
            has_glsl_shaders=True,
            created_shaders=["glsl1"],
        )
        order = []
        with patch.object(
            model_import_pipeline.cmds,
            "refresh",
            side_effect=lambda **_kwargs: order.append("refresh"),
        ), patch.object(
            model_import_pipeline,
            "sync_dx11_generated_uniforms",
            side_effect=lambda _shaders: order.append("sync") or 0,
        ):
            pipeline.sync_dx11_uniforms(converter, refresh_if_dx11=True)
        self.assertEqual(order, ["refresh", "sync"])

    def test_texture_nodes_receive_instance_root_ownership(self):
        pipeline = self._make_pipeline(MagicMock())
        with patch.object(model_import_pipeline.cmds, "attributeQuery", return_value=False) as query, patch.object(
            model_import_pipeline.cmds, "addAttr"
        ) as add_attr, patch.object(model_import_pipeline.cmds, "connectAttr") as connect_attr:
            pipeline.connect_texture_nodes_to_root("ModelRoot", ["texture_file"])

        query.assert_called_once_with("mmd_model_root", node="texture_file", exists=True)
        add_attr.assert_called_once_with("texture_file", longName="mmd_model_root", attributeType="message")
        connect_attr.assert_called_once_with(
            "ModelRoot.message",
            "texture_file.mmd_model_root",
            force=True,
        )

    def test_mesh_converter_records_glsl_hardware_backend(self):
        converter = MeshConverter()
        with patch.object(mesh_converter_module.cmds, "objExists", return_value=True), patch.object(
            mesh_converter_module.cmds, "nodeType", return_value="GLSLShader"
        ):
            converter._record_created_shader("glsl1")
        self.assertTrue(converter.has_glsl_shaders)
        self.assertFalse(converter.has_dx11_shaders)

    def test_generated_namespace_logs_at_debug_not_info(self):
        logger = MagicMock()
        pipeline = self._make_pipeline(logger, options={"use_namespace": True})

        with patch(
            "mmd_tools.io.model_import_pipeline.NamespaceUtils.generate_namespace",
            return_value="ModelNS",
        ) as mock_generate, patch(
            "mmd_tools.io.model_import_pipeline.NamespaceUtils.ensure_unique_namespace",
            return_value="ModelNS",
        ) as mock_ensure:
            result = pipeline.resolve_namespace("Model")

        self.assertEqual(result, "ModelNS")
        mock_generate.assert_called_once_with("Model")
        mock_ensure.assert_called_once_with("ModelNS")

        debug_messages = _message_templates(logger.debug)
        info_messages = _message_templates(logger.info)
        self.assertIn("Using namespace: %s", debug_messages)
        self.assertNotIn("Using namespace: %s", info_messages)

    def test_cleanup_namespace_logs_at_debug_not_info(self):
        logger = MagicMock()
        pipeline = self._make_pipeline(logger)

        with patch(
            "mmd_tools.io.model_import_pipeline.NamespaceUtils.cleanup_namespace"
        ) as mock_cleanup:
            pipeline.cleanup_namespace("FailedNS")

        mock_cleanup.assert_called_once_with("FailedNS", force=True)

        debug_messages = _message_templates(logger.debug)
        info_messages = _message_templates(logger.info)
        self.assertIn("Cleaning up namespace: %s", debug_messages)
        self.assertNotIn("Cleaning up namespace: %s", info_messages)

    def test_physics_import_is_disabled_without_explicit_option(self):
        logger = MagicMock()
        profile = {}
        pipeline = self._make_pipeline(logger, options={"profile": profile})
        parser = SimpleNamespace(rigid_bodies=[object()], joints=[object()], bones=[object()])

        with patch("mmd_tools.converters.physics_scene_builder.build_physics_scene") as build_scene:
            result = pipeline.convert_physics(
                file_kind="pmx",
                parser=parser,
                maya_joints=[],
                root_group="root",
            )

        self.assertEqual(result, ([], []))
        build_scene.assert_not_called()
        self.assertEqual(profile["physics_converter"]["reason"], "import_physics_disabled")

    def test_physics_import_uses_explicit_option_without_environment_gate(self):
        logger = MagicMock()
        profile = {}
        pipeline = self._make_pipeline(logger, options={"profile": profile, "import_physics": True})
        parser = SimpleNamespace(rigid_bodies=[object()], joints=[object()], bones=[object()])

        with patch(
            "mmd_tools.converters.physics_scene_builder.build_physics_scene",
            return_value=(["rb"], ["joint"]),
        ) as build_scene, patch.object(pipeline, "_store_source_pmx_payload") as store_payload:
            result = pipeline.convert_physics(
                file_kind="pmx",
                parser=parser,
                maya_joints=[],
                root_group="root",
            )

        self.assertEqual(result, (["rb"], ["joint"]))
        build_scene.assert_called_once()
        store_payload.assert_called_once_with("root")


if __name__ == "__main__":
    unittest.main()
