"""MMDShaderOverride の VP2/Maya GUI 依存テスト。

MMDShaderOverride のインスタンス生成は VP2.0 の実オブジェクトが必要なため、
Maya GUI 環境でのみ実行する。
"""

import os
import unittest

from mmd_tools.view import shader_override
from tests.common.gui_test_base import GuiTestBase, requires_gui


@requires_gui
class TestShaderOverrideFxPath(GuiTestBase):
    """shader_path が実在する .fx ファイルへ解決されることを検証する。"""

    def test_override_resolves_shader_path_to_existing_fx(self):
        override = shader_override.MMDShaderOverride(object())
        self.assertTrue(
            os.path.isfile(override.shader_path),
            f"MMDShaderOverride.shader_path does not exist: {override.shader_path}",
        )


@requires_gui
class TestShaderOverrideInit(GuiTestBase):
    """MMDShaderOverride.__init__ の既定値設定を検証する。"""

    def setUp(self):
        super().setUp()
        self.override = shader_override.MMDShaderOverride(object())

    def test_initial_shader_is_none(self):
        self.assertIsNone(self.override.shader)

    def test_default_material_scalars(self):
        self.assertEqual(self.override.shininess, 1.0)
        self.assertEqual(self.override.edge_size, 0.01)
        self.assertEqual(self.override.sphere_mode, 0)

    def test_default_diffuse_color(self):
        c = self.override.diffuse_color
        self.assertEqual((c.r, c.g, c.b), (0.8, 0.8, 0.8))

    def test_default_edge_color_is_black(self):
        c = self.override.edge_color
        self.assertEqual((c.r, c.g, c.b), (0.0, 0.0, 0.0))


@requires_gui
class TestShaderOverridePureMethods(GuiTestBase):

    def setUp(self):
        super().setUp()
        self.override = shader_override.MMDShaderOverride(object())

    def test_supported_draw_apis_ors_all_three(self):
        result = self.override.supportedDrawAPIs()
        self.assertEqual(result, 1 | 2 | 4)

    def test_handles_consolidated_geometry_true(self):
        self.assertTrue(self.override.handlesConsolidatedGeometry())

    def test_activate_key_returns_true(self):
        self.assertTrue(self.override.activateKey(object(), object()))

    def test_creator_returns_instance(self):
        inst = shader_override.MMDShaderOverride.creator(object())
        self.assertIsInstance(inst, shader_override.MMDShaderOverride)


if __name__ == "__main__":
    unittest.main()
