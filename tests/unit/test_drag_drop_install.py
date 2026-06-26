"""Drag-and-drop installer の Maya 非依存部分を検証するテスト。"""

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

import drag_drop_install
from maya import cmds  # noqa: E402


class TestDragDropInstall(unittest.TestCase):
    def test_module_text_uses_maya_and_package_versions(self):
        text = drag_drop_install._module_text(Path("F:/Develop/maya_mmd_tools"), "2024", "1.2.3")

        self.assertIn("+ MAYAVERSION:2024 maya_mmd_tools 1.2.3", text)
        self.assertIn("scripts: .", text)
        self.assertIn("plug-ins: plug-ins", text)
        self.assertIn("PYTHONPATH +:= .", text)

    def test_install_writes_module_file_and_defers_plugin_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            module_dir = Path(tmp) / "modules"
            root = Path(tmp) / "repo"
            root.mkdir()

            with patch.object(drag_drop_install, "_module_dir", return_value=module_dir):
                with patch.object(drag_drop_install, "_maya_version", return_value="2024"):
                    with patch.object(drag_drop_install, "_package_version", return_value="9.8.7"):
                        with patch.object(cmds, "evalDeferred") as eval_deferred:
                            mod_path = drag_drop_install.install(root)

            self.assertEqual(mod_path, module_dir / "maya_mmd_tools.mod")
            self.assertIn("maya_mmd_tools 9.8.7", mod_path.read_text(encoding="utf-8"))
            eval_deferred.assert_called_once()


if __name__ == "__main__":
    unittest.main()
