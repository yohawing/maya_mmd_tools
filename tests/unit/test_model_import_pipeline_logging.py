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
from mmd_tools.io import pmx_importer  # noqa: E402
from maya import cmds  # noqa: E402


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

    def test_mesh_converter_records_glsl_hardware_backend(self):
        converter = MeshConverter()
        with patch.object(mesh_converter_module.cmds, "objExists", return_value=True), patch.object(
            mesh_converter_module.cmds, "nodeType", return_value="GLSLShader"
        ):
            converter._record_created_shader("glsl1")
        self.assertTrue(converter.has_glsl_shaders)
        self.assertFalse(converter.has_dx11_shaders)

    def test_material_graph_incomplete_retries_once_after_refresh(self):
        pipeline = MagicMock()
        results = [
            {"success": True, "skipped": ["glsl_material_plugs_incomplete:s:MainTextureAdd"]},
            {"success": True, "skipped": []},
        ]
        with patch.object(
            pmx_importer, "build_material_morph_graph", side_effect=results
        ) as build, patch.object(cmds, "refresh") as refresh:
            result = pmx_importer._build_material_morph_graph_with_retry(
                "root", pipeline, SimpleNamespace()
            )
        self.assertEqual(build.call_count, 2)
        refresh.assert_called_once_with(force=True)
        pipeline.sync_dx11_uniforms.assert_called_once_with(
            unittest.mock.ANY, refresh_if_dx11=False
        )
        self.assertEqual(result["skipped"], [])
        self.assertTrue(result["retry"]["attempted"])

    def test_material_graph_persistent_incomplete_stops_after_two_attempts(self):
        pipeline = MagicMock()
        incomplete = {"success": True, "skipped": ["dx11_material_plugs_incomplete:s:x"]}
        with patch.object(
            pmx_importer, "build_material_morph_graph", side_effect=[incomplete, dict(incomplete)]
        ) as build, patch.object(cmds, "refresh"):
            result = pmx_importer._build_material_morph_graph_with_retry(
                "root", pipeline, SimpleNamespace()
            )
        self.assertEqual(build.call_count, 2)
        self.assertEqual(result["retry"]["final_skipped"], incomplete["skipped"])

    def test_material_graph_retry_refresh_and_sync_exceptions_are_fail_soft(self):
        incomplete = {"success": True, "skipped": ["glsl_material_plugs_incomplete:s:x"]}
        for phase in ("refresh", "uniform_sync"):
            with self.subTest(phase=phase):
                pipeline = MagicMock()
                if phase == "uniform_sync":
                    pipeline.sync_dx11_uniforms.side_effect = ValueError("sync failed")
                refresh_effect = RuntimeError("refresh failed") if phase == "refresh" else None
                with patch.object(
                    pmx_importer,
                    "build_material_morph_graph",
                    side_effect=[incomplete, {"success": True, "skipped": []}],
                ) as build, patch.object(cmds, "refresh", side_effect=refresh_effect):
                    result = pmx_importer._build_material_morph_graph_with_retry(
                        "root", pipeline, SimpleNamespace()
                    )
                self.assertEqual(build.call_count, 2)
                self.assertEqual(result["skipped"], [])
                self.assertEqual(len(result["retry"]["errors"]), 1)
                error = result["retry"]["errors"][0]
                self.assertEqual(error["phase"], phase)
                self.assertIn("failed", error["message"])

    def test_material_graph_retry_aggregates_created_and_stable_evaluator_union(self):
        pipeline = MagicMock()
        first = {
            "success": True,
            "skipped": ["glsl_material_plugs_incomplete:s:x"],
            "created": 3,
            "reused": 0,
            "contributions": 7,
            "evaluator_nodes": ["evalA", "evalB", "evalC"],
        }
        final = {
            "success": True,
            "skipped": [],
            "created": 0,
            "reused": 3,
            "contributions": 7,
            "evaluator_nodes": ["evalC", "evalB", "evalA"],
        }
        with patch.object(
            pmx_importer, "build_material_morph_graph", side_effect=[first, final]
        ), patch.object(cmds, "refresh"):
            result = pmx_importer._build_material_morph_graph_with_retry(
                "root", pipeline, SimpleNamespace()
            )
        self.assertEqual(result["created"], 3)
        self.assertEqual(result["reused"], 3)
        self.assertEqual(result["contributions"], 7)
        self.assertEqual(result["evaluator_nodes"], ["evalA", "evalB", "evalC"])
        self.assertEqual(
            result["retry"]["first_counts"],
            {"created": 3, "reused": 0, "contributions": 7},
        )
        self.assertEqual(
            result["retry"]["final_counts"],
            {"created": 0, "reused": 3, "contributions": 7},
        )

    def test_material_graph_clean_or_other_skip_does_not_retry(self):
        for skipped in ([], ["glsl_vp2_not_opengl:s"], ["complete_material_backend_unsupported:s"]):
            with self.subTest(skipped=skipped):
                pipeline = MagicMock()
                with patch.object(
                    pmx_importer,
                    "build_material_morph_graph",
                    return_value={"success": True, "skipped": skipped},
                ) as build, patch.object(cmds, "refresh") as refresh:
                    result = pmx_importer._build_material_morph_graph_with_retry(
                        "root", pipeline, SimpleNamespace()
                    )
                self.assertEqual(build.call_count, 1)
                refresh.assert_not_called()
                pipeline.sync_dx11_uniforms.assert_not_called()
                self.assertNotIn("retry", result)

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

    def test_no_data_physics_phase_logs_at_debug_not_info(self):
        logger = MagicMock()
        # import_physics=True in options skips settings.get fallback.
        pipeline = self._make_pipeline(logger, options={"import_physics": True})
        parser = SimpleNamespace(rigid_bodies=None)

        ncloth, constraints = pipeline.convert_physics(
            file_kind="pmx",
            parser=parser,
            maya_joints=[],
            root_group="root",
        )

        self.assertEqual(ncloth, [])
        self.assertEqual(constraints, [])

        debug_messages = _message_templates(logger.debug)
        info_messages = _message_templates(logger.info)
        self.assertIn("Converting physics...", debug_messages)
        self.assertIn("No physics data found", debug_messages)
        self.assertNotIn("Converting physics...", info_messages)

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


if __name__ == "__main__":
    unittest.main()
