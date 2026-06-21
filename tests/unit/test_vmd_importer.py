"""VMD importerのMaya依存しない境界処理を検証するテスト。"""

import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from maya import cmds

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
            result = import_vmd_file(object(), vmd_path, {"target_model": target_model})

        self.assertTrue(result)
        kwargs = converter.convert.call_args.kwargs
        self.assertEqual(kwargs["pmx_path"], pmx_path)
        self.assertEqual(kwargs["vmd_bytes"], b"Vocaloid Motion Data 0002\x00")


if __name__ == "__main__":
    unittest.main()
