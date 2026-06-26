"""File > Import translator の Maya 非依存契約を検証するテスト。"""

import unittest
from unittest.mock import MagicMock, patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

import maya.OpenMayaMPx as mpx  # noqa: E402
from mmd_tools.io import mmd_file_translator  # noqa: E402


class _FakeFileObject:
    def __init__(self, path):
        self._path = path

    def resolvedFullName(self):
        return self._path


class TestMmdFileTranslator(unittest.TestCase):
    def test_identify_file_accepts_mmd_extensions(self):
        translator = mmd_file_translator.MmdFileTranslator()

        self.assertEqual(
            translator.identifyFile(_FakeFileObject("model.pmx"), "", 0),
            mpx.MPxFileTranslator.kIsMyFileType,
        )
        self.assertEqual(
            translator.identifyFile(_FakeFileObject("motion.vmd"), "", 0),
            mpx.MPxFileTranslator.kIsMyFileType,
        )

    def test_identify_file_rejects_other_extensions(self):
        translator = mmd_file_translator.MmdFileTranslator()

        self.assertEqual(
            translator.identifyFile(_FakeFileObject("scene.fbx"), "", 0),
            mpx.MPxFileTranslator.kNotMyFileType,
        )

    def test_reader_defers_shared_import_contract(self):
        translator = mmd_file_translator.MmdFileTranslator()

        with patch.object(mmd_file_translator, "_schedule_import") as schedule_import:
            translator.reader(_FakeFileObject("model.pmx"), "", translator.kImportAccessMode)

        schedule_import.assert_called_once_with("model.pmx")

    def test_deferred_import_routes_to_shared_import_contract(self):
        with patch.object(mmd_file_translator, "import_dropped_files", return_value=True) as import_paths:
            mmd_file_translator._deferred_import("model.pmx")

        import_paths.assert_called_once_with(["model.pmx"])

    def test_reader_raises_for_vmd_before_model_load(self):
        translator = mmd_file_translator.MmdFileTranslator()

        with patch.object(mmd_file_translator, "_selected_model_root", return_value=None):
            with patch.object(mmd_file_translator, "_display_warning"):
                with self.assertRaises(RuntimeError):
                    translator.reader(_FakeFileObject("motion.vmd"), "", translator.kImportAccessMode)

    def test_register_and_deregister_use_translator_name(self):
        plugin = MagicMock()
        with patch.object(mpx, "MFnPlugin", return_value=plugin):
            mmd_file_translator.register_file_translator("mobject")
            mmd_file_translator.deregister_file_translator("mobject")

        plugin.registerFileTranslator.assert_called_once_with(
            mmd_file_translator.TRANSLATOR_NAME,
            None,
            mmd_file_translator.translator_creator,
        )
        plugin.deregisterFileTranslator.assert_called_once_with(mmd_file_translator.TRANSLATOR_NAME)


if __name__ == "__main__":
    unittest.main()
