"""VMD importerのMaya依存しない境界処理を検証するテスト。"""

import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from maya import cmds

from mmd_tools.core.exceptions import MMDImportException
from mmd_tools.io.vmd_importer import import_vmd_file
from tests.common.maya_test_base import MayaTestBase


class TestVmdImporter(MayaTestBase):
    """VMD importerのruntime bake入力解決を検証する。"""

    def test_target_model_source_file_is_passed_as_pmx_path(self):
        target_model = cmds.group(empty=True, name="mmd_model_root")
        cmds.addAttr(target_model, longName="mmd_source_file", dataType="string")

        temp_root = Path(tempfile.mkdtemp())
        pmx_path = str(temp_root / "source" / "model.pmx")
        vmd_path = str(temp_root / "motion" / "motion.vmd")
        Path(pmx_path).parent.mkdir(parents=True, exist_ok=True)
        Path(vmd_path).parent.mkdir(parents=True, exist_ok=True)
        Path(pmx_path).write_bytes(b"pmx")
        Path(vmd_path).write_bytes(b"Vocaloid Motion Data 0002\x00")

        self.files_created.extend([pmx_path, vmd_path])
        cmds.setAttr(f"{target_model}.mmd_source_file", pmx_path, type="string")
        self.assertEqual(cmds.getAttr(f"{target_model}.mmd_source_file"), pmx_path)

        with patch("mmd_tools.io.vmd_importer.VmdConverter") as converter_class:
            converter = converter_class.return_value
            converter.convert.return_value = True
            result = import_vmd_file(
                object(),
                vmd_path,
                {"target_model": target_model, "clear_existing_motion": True},
            )

        self.assertTrue(result)
        self.assertEqual(converter.motion_scale, 1.0)
        kwargs = converter.convert.call_args.kwargs
        self.assertEqual(kwargs["pmx_path"], pmx_path)
        self.assertEqual(kwargs["vmd_bytes"], b"Vocaloid Motion Data 0002\x00")
        self.assertTrue(kwargs["clear_existing_motion"])

    def test_motion_scale_option_is_applied_to_converter(self):
        temp_root = Path(tempfile.mkdtemp())
        vmd_path = str(temp_root / "motion.vmd")
        Path(vmd_path).write_bytes(b"Vocaloid Motion Data 0002\x00")
        self.files_created.append(vmd_path)

        with patch("mmd_tools.io.vmd_importer.VmdConverter") as converter_class:
            converter = converter_class.return_value
            converter.convert.return_value = True
            result = import_vmd_file(object(), vmd_path, {"motion_scale": 2.5})

        self.assertTrue(result)
        self.assertEqual(converter.motion_scale, 2.5)

    def test_camera_light_import_options_are_applied_to_converter(self):
        temp_root = Path(tempfile.mkdtemp())
        vmd_path = str(temp_root / "motion.vmd")
        Path(vmd_path).write_bytes(b"Vocaloid Motion Data 0002\x00")
        self.files_created.append(vmd_path)

        with patch("mmd_tools.io.vmd_importer.VmdConverter") as converter_class:
            converter = converter_class.return_value
            converter.convert.return_value = True
            result = import_vmd_file(
                object(),
                vmd_path,
                {
                    "import_camera_animation": False,
                    "import_light_animation": False,
                },
            )

        self.assertTrue(result)
        self.assertFalse(converter.import_camera_animation)
        self.assertFalse(converter.import_light_animation)

    def test_progress_callback_is_forwarded_to_converter(self):
        temp_root = Path(tempfile.mkdtemp())
        vmd_path = str(temp_root / "motion.vmd")
        Path(vmd_path).write_bytes(b"Vocaloid Motion Data 0002\x00")
        self.files_created.append(vmd_path)
        progress = []
        progress_callback = progress.append

        with patch("mmd_tools.io.vmd_importer.VmdConverter") as converter_class:
            converter = converter_class.return_value
            converter.convert.return_value = True
            result = import_vmd_file(
                object(),
                vmd_path,
                {},
                progress_callback=progress_callback,
            )

        self.assertTrue(result)
        self.assertIs(converter.convert.call_args.kwargs["progress_callback"], progress_callback)
        self.assertIn(15, progress)
        self.assertIn(25, progress)
        self.assertIn(35, progress)

    def test_converter_failure_raises_import_exception(self):
        temp_root = Path(tempfile.mkdtemp())
        vmd_path = str(temp_root / "motion.vmd")
        Path(vmd_path).write_bytes(b"Vocaloid Motion Data 0002\x00")
        self.files_created.append(vmd_path)

        with patch("mmd_tools.io.vmd_importer.VmdConverter") as converter_class:
            converter = converter_class.return_value
            converter.convert.return_value = False

            with self.assertRaises(MMDImportException):
                import_vmd_file(object(), vmd_path, {})


if __name__ == "__main__":
    unittest.main()
