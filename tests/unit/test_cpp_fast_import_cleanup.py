"""Failed full imports must release only their native geometry."""

import unittest
from pathlib import Path
from unittest.mock import patch

from tests.common.maya_stub import install_maya_stub

install_maya_stub()

from mmd_tools.io import cpp_fast_importer  # noqa: E402


class TestFastImportCleanup(unittest.TestCase):
    def test_authoring_failure_tracks_renamed_or_adopted_geometry_by_uuid(self):
        rejection = RuntimeError("authoring rejected")
        for remaining, cleanup_error in ((["|renamed"], None), ([], None), (["|renamed"], RuntimeError("delete failed"))):
            with self.subTest(remaining=remaining, cleanup_error=cleanup_error), patch.object(
                cpp_fast_importer, "_candidate_plugin_paths", return_value=[Path("plugin.mll")]
            ), patch.object(Path, "exists", return_value=True), patch.object(
                cpp_fast_importer.cpp_plugin_locator, "is_plugin_loaded", return_value=True
            ), patch.object(cpp_fast_importer, "parse_pmx_native", return_value=object()), patch(
                "maya.cmds.mmdFastLoad", create=True, return_value=["native", "shape"]
            ), patch("maya.cmds.ls", side_effect=[["native-uuid"], remaining]) as lookup, patch(
                "maya.cmds.delete", side_effect=cleanup_error
            ) as delete, patch("mmd_tools.io.pmx_importer.import_pmx_file", side_effect=rejection):
                with self.assertRaises(RuntimeError) as caught:
                    cpp_fast_importer.fast_import(
                        "model.pmx", mesh_only=False, options={"separate_meshes_by_material": False}
                    )
                self.assertIs(caught.exception, rejection)
                lookup.assert_any_call(["native-uuid"], long=True)
                if remaining:
                    delete.assert_called_once_with(remaining)
                else:
                    delete.assert_not_called()
