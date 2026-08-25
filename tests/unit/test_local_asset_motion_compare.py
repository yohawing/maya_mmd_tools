"""Focused tests for the local motion-parity runner setup."""

from __future__ import annotations

import unittest
from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests.viewport import local_asset_motion_compare


class LocalAssetMotionCompareTest(unittest.TestCase):
    @patch("tests.common.maya_plugin_setup.load_mmd_tools_plugin")
    @patch("tests.viewport.local_asset_motion_compare.maya.standalone.initialize")
    def test_initialize_maya_loads_production_plugin(
        self,
        initialize_mock,
        load_plugin_mock,
    ):
        local_asset_motion_compare._initialize_maya()

        initialize_mock.assert_called_once_with(name="python")
        load_plugin_mock.assert_called_once_with(local_asset_motion_compare.ROOT)

    @patch("mmd_tools.io.mmd_importer.import_mmd_file")
    @patch("tests.viewport.local_asset_motion_compare.cmds.select")
    @patch("tests.viewport.local_asset_motion_compare.cmds.file")
    def test_import_pmx_vmd_forwards_bake_mode(
        self,
        _file_mock,
        _select_mock,
        import_mmd_file_mock,
    ):
        import_mmd_file_mock.side_effect = ["|model", True]

        root = local_asset_motion_compare._import_pmx_vmd(
            Path("model.pmx"),
            Path("motion.vmd"),
            setup_rig=False,
            setup_bone_orientation=False,
            bake_mode=True,
        )

        self.assertEqual(root, "|model")
        vmd_options = import_mmd_file_mock.call_args_list[1].kwargs["options"]
        self.assertIs(vmd_options["bake_mode"], True)

    @patch("tests.viewport.local_asset_motion_compare._compare_frames")
    @patch("tests.viewport.local_asset_motion_compare._capture_vertices")
    @patch("tests.viewport.local_asset_motion_compare._import_pmx_vmd")
    def test_run_case_selects_bake_then_live_rig(
        self,
        import_pmx_vmd_mock,
        capture_vertices_mock,
        compare_frames_mock,
    ):
        import_pmx_vmd_mock.side_effect = ["|bake", "|rig"]
        capture_vertices_mock.side_effect = [{0: []}, {0: []}]
        compare_frames_mock.return_value = {"passed": True}
        args = Namespace(
            frame=None,
            strict_local=True,
            skip_fbx=True,
            vertex_threshold=1.0,
        )

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pmx_path = root / "model.pmx"
            vmd_path = root / "motion.vmd"
            pmx_path.touch()
            vmd_path.touch()
            result = local_asset_motion_compare._run_case(
                {
                    "name": "parity",
                    "pmx": str(pmx_path),
                    "vmd": str(vmd_path),
                    "frames": [0],
                },
                args,
                root,
            )

        self.assertEqual(result["status"], "passed")
        self.assertIs(import_pmx_vmd_mock.call_args_list[0].kwargs["bake_mode"], True)
        self.assertIs(import_pmx_vmd_mock.call_args_list[1].kwargs["bake_mode"], False)


if __name__ == "__main__":
    unittest.main()
