"""MMDShaderOverride の VP2/Maya GUI 依存テスト。

MMDShaderOverride のインスタンス生成は VP2.0 の実オブジェクトが必要なため、
Maya GUI 環境でのみ実行する。
"""

import os
import unittest
from pathlib import Path

from maya import cmds
import maya.api.OpenMaya as om
from mmd_tools.view import shader_override
from tests.common.gui_test_base import GuiTestBase, requires_gui


class _ShaderOverrideTestBase(GuiTestBase):
    """Create the registered MMD shader node used by MPxShaderOverride."""

    def setUp(self):
        super().setUp()
        plugin = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
        if not cmds.pluginInfo(str(plugin), query=True, loaded=True):
            cmds.loadPlugin(str(plugin))
        self.shader_node = cmds.createNode(shader_override.MMDShaderNode.kNodeName)
        selection = om.MSelectionList()
        selection.add(self.shader_node)
        self.shader_object = selection.getDependNode(0)

    def tearDown(self):
        if cmds.objExists(self.shader_node):
            cmds.delete(self.shader_node)
        super().tearDown()


@requires_gui
class TestShaderOverrideFxPath(_ShaderOverrideTestBase):
    """shader_path が実在する .fx ファイルへ解決されることを検証する。"""

    def test_override_resolves_shader_path_to_existing_fx(self):
        override = shader_override.MMDShaderOverride(self.shader_object)
        self.assertTrue(
            os.path.isfile(override.shader_path),
            f"MMDShaderOverride.shader_path does not exist: {override.shader_path}",
        )


@requires_gui
class TestShaderOverrideInit(_ShaderOverrideTestBase):
    """MMDShaderOverride.__init__ の既定値設定を検証する。"""

    def setUp(self):
        super().setUp()
        self.override = shader_override.MMDShaderOverride(self.shader_object)

    def test_initial_shader_is_none(self):
        self.assertIsNone(self.override.shader)

    def test_default_material_scalars(self):
        self.assertEqual(self.override.shininess, 1.0)
        self.assertEqual(self.override.edge_size, 0.01)
        self.assertEqual(self.override.sphere_mode, 0)

    def test_default_diffuse_color(self):
        c = self.override.diffuse_color
        for actual in (c.r, c.g, c.b):
            self.assertAlmostEqual(actual, 0.8, places=6)

    def test_default_edge_color_is_black(self):
        c = self.override.edge_color
        self.assertEqual((c.r, c.g, c.b), (0.0, 0.0, 0.0))


@requires_gui
class TestShaderOverridePureMethods(_ShaderOverrideTestBase):

    def setUp(self):
        super().setUp()
        self.override = shader_override.MMDShaderOverride(self.shader_object)

    def test_supported_draw_apis_ors_all_three(self):
        result = self.override.supportedDrawAPIs()
        self.assertEqual(result, 1 | 2 | 4)

    def test_handles_consolidated_geometry_true(self):
        self.assertTrue(self.override.handlesConsolidatedGeometry())

    def test_activate_key_returns_true(self):
        self.assertTrue(self.override.activateKey(object(), object()))

    def test_creator_returns_instance(self):
        inst = shader_override.MMDShaderOverride.creator(self.shader_object)
        self.assertIsInstance(inst, shader_override.MMDShaderOverride)


if __name__ == "__main__":
    unittest.main()
