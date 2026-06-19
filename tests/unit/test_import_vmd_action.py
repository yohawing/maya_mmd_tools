"""ImportVmdActionのMaya非依存の実行契約を検証するテスト。"""

import unittest

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.actions.import_vmd_action import (  # noqa: E402
    ImportVmdAction,
    ImportVmdRequest,
)


class TestImportVmdAction(unittest.TestCase):
    """VMD import action の依存境界を検証する。"""

    def test_execute_calls_importer_with_file_path_and_options(self):
        calls = []
        options = {"target_model": "model_root"}

        def importer(file_path, options=None):
            calls.append((file_path, options))
            return "motion_root"

        action = ImportVmdAction(importer=importer, new_scene=lambda: None)
        result = action.execute(ImportVmdRequest("motion.vmd", options))

        self.assertTrue(result.succeeded)
        self.assertEqual(result.root_node, "motion_root")
        self.assertIsNone(result.error)
        self.assertEqual(calls, [("motion.vmd", options)])
        self.assertIs(calls[0][1], options)

    def test_execute_calls_new_scene_before_import_when_requested(self):
        calls = []

        def new_scene():
            calls.append("new_scene")

        def importer(_file_path, options=None):
            calls.append("importer")
            return "motion_root"

        action = ImportVmdAction(importer=importer, new_scene=new_scene)
        result = action.execute(ImportVmdRequest("motion.vmd", {}, create_new_scene=True))

        self.assertTrue(result.succeeded)
        self.assertEqual(calls, ["new_scene", "importer"])

    def test_execute_does_not_call_new_scene_when_not_requested(self):
        calls = []

        def new_scene():
            calls.append("new_scene")

        def importer(_file_path, options=None):
            calls.append("importer")
            return "motion_root"

        action = ImportVmdAction(importer=importer, new_scene=new_scene)
        result = action.execute(ImportVmdRequest("motion.vmd", {}))

        self.assertTrue(result.succeeded)
        self.assertEqual(calls, ["importer"])

    def test_execute_returns_failure_when_importer_returns_none(self):
        action = ImportVmdAction(importer=lambda _path, options=None: None, new_scene=lambda: None)

        result = action.execute(ImportVmdRequest("motion.vmd", {}))

        self.assertFalse(result.succeeded)
        self.assertIsNone(result.root_node)
        self.assertIsNone(result.error)

    def test_execute_converts_importer_exception_to_result_error(self):
        error = RuntimeError("boom")

        def importer(_file_path, options=None):
            raise error

        action = ImportVmdAction(importer=importer, new_scene=lambda: None)
        result = action.execute(ImportVmdRequest("motion.vmd", {}))

        self.assertFalse(result.succeeded)
        self.assertIsNone(result.root_node)
        self.assertIs(result.error, error)

    def test_execute_converts_new_scene_exception_to_result_error(self):
        error = RuntimeError("new scene failed")

        def new_scene():
            raise error

        action = ImportVmdAction(importer=lambda _path, options=None: "motion_root", new_scene=new_scene)
        result = action.execute(ImportVmdRequest("motion.vmd", {}, create_new_scene=True))

        self.assertFalse(result.succeeded)
        self.assertIsNone(result.root_node)
        self.assertIs(result.error, error)


if __name__ == "__main__":
    unittest.main()
