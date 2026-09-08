"""Legacy hardware material migration against actual Maya nodes and Undo."""

from pathlib import Path
import tempfile
from unittest import mock

from maya import cmds

from mmd_tools.converters import render_preview_migration as migration
from mmd_tools.converters.export_scene_collector import _collect_mmd_material_dict
from mmd_tools.converters.mesh_converter import MeshConverter
from mmd_tools.core.model_registry import ensure_model_registry, register_model_members, REGISTRY_CATEGORY_MATERIAL
from mmd_tools.core.pmx_data.material import PmxMaterial
from tests.common.maya_test_base import MayaTestBase


class TestRenderPreviewMigration(MayaTestBase):
    def make_legacy(self):
        cmds.loadPlugin("dx11Shader", quiet=True)
        root = cmds.group(empty=True, name="legacy_root")
        cmds.addAttr(root, longName="mmd_model_name", dataType="string")
        cmds.setAttr(root + ".mmd_model_name", "legacy", type="string")
        mesh = cmds.parent(cmds.polyPlane()[0], root)[0]
        shader = cmds.shadingNode("dx11Shader", asShader=True, name="legacyMaterial")
        material = PmxMaterial()
        material.name = "legacyMaterial"
        material.diffuse = (.2, .4, .6, .7)
        MeshConverter("")._apply_custom_attributes(shader, material, [], False, material_index=0)
        cmds.addAttr(shader, longName="mmdTransparencyMode", dataType="string")
        cmds.setAttr(shader + ".mmdTransparencyMode", "opaque", type="string")
        cmds.setAttr(shader + ".mmdDoubleSided", True)
        cmds.addAttr(shader, longName="edge_flag", attributeType="long")
        cmds.setAttr(shader + ".edge_flag", 1)
        registry = ensure_model_registry(root)
        register_model_members(registry, REGISTRY_CATEGORY_MATERIAL, [shader])
        group = cmds.sets(renderable=True, noSurfaceShader=True, empty=True)
        cmds.connectAttr(shader + ".outColor", group + ".surfaceShader")
        cmds.sets(mesh, edit=True, forceElement=group)
        cmds.addAttr(shader, longName="MainTexture", attributeType="float3")
        for axis in "RGB":
            cmds.addAttr(shader, longName="MainTexture" + axis, attributeType="float", parent="MainTexture")
        texture = cmds.shadingNode("file", asTexture=True)
        cmds.connectAttr(texture + ".outColor", shader + ".MainTexture")
        return root, shader, group, texture

    def test_material_assignment_metadata_texture_undo_and_reload(self):
        root, shader, group, texture = self.make_legacy()
        original = _collect_mmd_material_dict(shader)
        original_id = cmds.ls(shader, uuid=True)
        report = migration.migrate_model_render_preview(root)
        self.assertEqual(report, {"materials": 1, "sources": 0})
        self.assertEqual(cmds.nodeType(shader), "standardSurface")
        self.assertEqual(_collect_mmd_material_dict(shader), original)
        self.assertTrue(cmds.isConnected(shader + ".outColor", group + ".surfaceShader"))
        self.assertTrue(cmds.isConnected(texture + ".outColor", shader + ".baseColor"))
        self.assertAlmostEqual(cmds.getAttr(texture + ".alphaGain"), .7, places=6)
        self.assertEqual(cmds.getAttr(shader + ".mmdTransparencyMode"), "opaque")
        self.assertTrue(cmds.getAttr(shader + ".mmdDoubleSided"))
        self.assertTrue(cmds.attributeQuery("edge_flag", node=shader, exists=True))
        self.assertTrue(cmds.isConnected(shader + ".mmd_diffuse_alpha", shader + ".opacityR"))
        cmds.undo()
        self.assertEqual(cmds.ls(shader, uuid=True), original_id)
        self.assertEqual(cmds.nodeType(shader), "dx11Shader")
        self.assertTrue(cmds.isConnected(texture + ".outColor", shader + ".MainTexture"))
        cmds.redo()
        self.assertEqual(cmds.nodeType(shader), "standardSurface")
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "migrated.ma")
            cmds.file(rename=path)
            cmds.file(save=True, type="mayaAscii", force=True)
            cmds.file(path, open=True, force=True)
            self.assertEqual(_collect_mmd_material_dict(shader), original)
            self.assertTrue(cmds.isConnected(texture + ".outColor", shader + ".baseColor"))
        self.assertEqual(migration.migrate_model_render_preview(root), {"materials": 0, "sources": 0})

    def test_failed_binding_restores_legacy_material(self):
        root, shader, group, texture = self.make_legacy()
        original_id = cmds.ls(shader, uuid=True)
        before = set(cmds.ls())
        with mock.patch.object(migration, "build_material_morph_graph", return_value={"success": False, "skipped": ["injected"]}):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                migration.migrate_model_render_preview(root)
        self.assertEqual(set(cmds.ls()), before)
        self.assertEqual(cmds.ls(shader, uuid=True), original_id)
        self.assertTrue(cmds.isConnected(shader + ".outColor", group + ".surfaceShader"))
        self.assertTrue(cmds.isConnected(texture + ".outColor", shader + ".MainTexture"))

    def test_ui_failure_does_not_report_a_completed_migration_as_failed(self):
        from mmd_tools.tools import migrate_render_preview as tool

        root, shader, _, _ = self.make_legacy()
        for failure in ("refresh", "message"):
            with self.subTest(failure=failure):
                ui = mock.Mock()
                refresh = mock.Mock()
                if failure == "refresh":
                    refresh.side_effect = RuntimeError("UI unavailable")
                else:
                    ui.inViewMessage.side_effect = RuntimeError("HUD unavailable")
                with mock.patch("mmd_tools.core.name_translation.resolve_model_root", return_value=root), mock.patch.object(tool, "logger") as logger:
                    result = tool.migrate_selected(cmds_module=ui, on_applied=refresh)
                self.assertIsNotNone(result)
                self.assertEqual(cmds.nodeType(shader), "standardSurface")
                logger.error.assert_not_called()
                ui.warning.assert_not_called()
                cmds.undo()
