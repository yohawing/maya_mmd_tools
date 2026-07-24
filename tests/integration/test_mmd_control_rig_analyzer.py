"""Maya integration coverage for the report-only MMD control-rig analyzer."""

import os
from pathlib import Path

from maya import cmds

from mmd_tools.core import settings
from mmd_tools.core.mmd_control_rig_analyzer import (
    INPUT_IK_CONTROLLER,
    INPUT_SOLVER_OUTPUT,
    STATUS_READY,
    analyze_mmd_control_rig,
)
from mmd_tools.io.mmd_importer import import_mmd_file
from tests.common.maya_test_base import MayaTestBase


_TEST_DATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
)
_PMX_PATH = os.path.join(_TEST_DATA, "mmt_test_model.pmx")


class TestMmdControlRigAnalyzerIntegration(MayaTestBase):
    """Verify the real rig-mode fixture produces a buildable MVP spec."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._previous_skip_shader_override = os.environ.get(
            "MMD_TOOLS_SKIP_SHADER_OVERRIDE"
        )
        os.environ["MMD_TOOLS_SKIP_SHADER_OVERRIDE"] = "1"
        root = Path(__file__).resolve().parents[2]
        cpp_plugin = root / "plug-ins" / "2024" / "Debug" / "mmd_tools_cpp.mll"
        if not cpp_plugin.exists():
            raise RuntimeError(
                "Maya 2024 Debug C++ plugin is required; run "
                "'uvx nox -s cpp_build -- --maya 2024 --config Debug'"
            )
        python_plugin = root / "mmd_tools" / "plugin_main.py"
        owned_plugins = []
        for plugin in (cpp_plugin, python_plugin):
            plugin_path = str(plugin)
            if cmds.pluginInfo(plugin_path, query=True, loaded=True):
                continue
            cmds.loadPlugin(plugin_path, quiet=True)
            owned_plugins.append(plugin_path)
        # The Python plugin detects and depends on the already loaded C++ rig
        # node provider, so unload it first at class teardown.
        cls.plugins_loaded = list(reversed(owned_plugins))

    @classmethod
    def tearDownClass(cls):
        try:
            super().tearDownClass()
        finally:
            if cls._previous_skip_shader_override is None:
                os.environ.pop("MMD_TOOLS_SKIP_SHADER_OVERRIDE", None)
            else:
                os.environ[
                    "MMD_TOOLS_SKIP_SHADER_OVERRIDE"
                ] = cls._previous_skip_shader_override

    def setUp(self):
        super().setUp()
        self._create_shaders = settings.get("import.model.create_mmd_shaders", True)
        self._add_semistandard = settings.get(
            "import.rig.add_semi_standard_bones",
            False,
        )
        settings.set("import.model.create_mmd_shaders", False)
        settings.set("import.rig.add_semi_standard_bones", False)

    def tearDown(self):
        settings.set("import.model.create_mmd_shaders", self._create_shaders)
        settings.set("import.rig.add_semi_standard_bones", self._add_semistandard)
        super().tearDown()

    def test_mmt_rig_fixture_classifies_mvp_without_mutating_scene(self):
        root = import_mmd_file(
            _PMX_PATH,
            options={
                "setup_rig": True,
                "setup_bone_orientation": True,
                "import_physics": False,
            },
        )
        self.assertTrue(root)
        nodes_before = set(cmds.ls(long=True) or [])

        spec = analyze_mmd_control_rig(root)

        self.assertEqual(set(cmds.ls(long=True) or []), nodes_before)
        roles = spec.roles_by_name
        for role in ("master", "center", "groove", "left_foot_ik", "right_foot_ik"):
            self.assertEqual(roles[role].status, STATUS_READY, role)
        self.assertEqual(
            roles["left_foot_ik"].binding.input_kind,
            INPUT_IK_CONTROLLER,
        )
        self.assertEqual(
            roles["right_foot_ik"].binding.input_kind,
            INPUT_IK_CONTROLLER,
        )
        self.assertTrue(roles["left_foot_ik"].binding.ik_solvers)
        self.assertTrue(roles["right_foot_ik"].binding.ik_solvers)
        self.assertTrue(spec.can_build_mvp)
        self.assertTrue(spec.display_frames)

        solver_outputs = [
            binding
            for binding in spec.bones
            if binding.input_kind == INPUT_SOLVER_OUTPUT
        ]
        self.assertTrue(solver_outputs)
        self.assertTrue(all(binding.blocked for binding in solver_outputs))


if __name__ == "__main__":
    import unittest

    unittest.main()
