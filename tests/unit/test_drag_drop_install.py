"""Drag-and-drop installer の Maya 非依存部分を検証するテスト。"""

import unittest
import tempfile
import sys
from pathlib import Path
from unittest.mock import patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

import drag_drop_install
from maya import cmds  # noqa: E402


class TestDragDropInstall(unittest.TestCase):
    def _make_source_tree(self, root: Path) -> None:
        (root / "mmd_tools").mkdir(parents=True)
        (root / "mmd_tools" / "__init__.py").write_text('__version__ = "1.2.3"\n', encoding="utf-8")
        (root / "plug-ins").mkdir()
        (root / "plug-ins" / "mmd_tools_plugin.py").write_text("# plugin\n", encoding="utf-8")
        (root / "plug-ins" / "2024" / "Release").mkdir(parents=True)
        (root / "plug-ins" / "2024" / "Release" / "mmd_tools_cpp.mll").write_text("plugin\n", encoding="utf-8")
        (root / "plug-ins" / "2026" / "Release").mkdir(parents=True)
        (root / "plug-ins" / "2026" / "Release" / "mmd_tools_cpp.mll").write_text("plugin\n", encoding="utf-8")
        (root / "resources" / "icons").mkdir(parents=True)
        (root / "resources" / "icons" / "icon.png").write_text("icon\n", encoding="utf-8")

    def test_module_text_uses_maya_and_package_versions(self):
        text = drag_drop_install._module_text(Path("F:/Develop/maya_mmd_tools"), ("2024", "2026"), "1.2.3")

        self.assertIn("+ MAYAVERSION:2024 maya_mmd_tools 1.2.3", text)
        self.assertIn("+ MAYAVERSION:2026 maya_mmd_tools 1.2.3", text)
        self.assertIn("scripts: .", text)
        self.assertIn("plug-ins: plug-ins", text)
        self.assertIn("icons: resources/icons", text)
        self.assertIn("PYTHONPATH +:= .", text)

    def test_discover_maya_versions_prefers_bundled_release_plugins(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "plug-ins" / "2026" / "Release").mkdir(parents=True)
            (root / "plug-ins" / "2024" / "Release").mkdir(parents=True)

            versions = drag_drop_install._discover_maya_versions(root, "2025")

            self.assertEqual(versions, ("2024", "2026"))

    def test_discover_maya_versions_falls_back_to_current_maya(self):
        with tempfile.TemporaryDirectory() as tmp:
            versions = drag_drop_install._discover_maya_versions(Path(tmp), "2025")

            self.assertEqual(versions, ("2025",))

    def test_install_copies_to_modules_and_writes_module_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            module_dir = Path(tmp) / "modules"
            root = Path(tmp) / "repo"
            root.mkdir()
            self._make_source_tree(root)
            installed_root = module_dir / "maya_mmd_tools"
            installed_root.mkdir(parents=True)
            (installed_root / "stale.txt").write_text("stale\n", encoding="utf-8")

            try:
                with patch.object(drag_drop_install, "_module_dir", return_value=module_dir):
                    with patch.object(drag_drop_install, "_maya_version", return_value="2024"):
                        with patch.object(drag_drop_install, "_package_version", return_value="1.2.3"):
                            with patch.object(cmds, "evalDeferred") as eval_deferred:
                                mod_path = drag_drop_install.install(root)

                self.assertEqual(mod_path, module_dir / "maya_mmd_tools.mod")
                self.assertTrue((installed_root / "mmd_tools" / "__init__.py").is_file())
                self.assertFalse((installed_root / "stale.txt").exists())
                module_text = mod_path.read_text(encoding="utf-8")
                self.assertIn("+ MAYAVERSION:2024 maya_mmd_tools 1.2.3", module_text)
                self.assertIn("+ MAYAVERSION:2026 maya_mmd_tools 1.2.3", module_text)
                self.assertNotIn("+ MAYAVERSION:2025", module_text)
                self.assertIn(installed_root.resolve().as_posix(), module_text)
                self.assertNotIn(root.resolve().as_posix(), module_text)
                self.assertIn(str(installed_root.resolve()), sys.path)
                eval_deferred.assert_called_once()
            finally:
                installed_root_path = str(installed_root.resolve())
                if installed_root_path in sys.path:
                    sys.path.remove(installed_root_path)

    def test_install_copy_excludes_dev_cache_and_build_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            module_dir = Path(tmp) / "modules"
            root = Path(tmp) / "repo"
            root.mkdir()
            self._make_source_tree(root)
            (root / ".git").mkdir()
            (root / ".git" / "config").write_text("git\n", encoding="utf-8")
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "module.pyc").write_text("cache\n", encoding="utf-8")
            (root / "build").mkdir()
            (root / "build" / "temp.txt").write_text("build\n", encoding="utf-8")
            (root / "docs-dev").mkdir()
            (root / "docs-dev" / "design.md").write_text("docs\n", encoding="utf-8")
            (root / "external" / "mmd-anim" / "target").mkdir(parents=True)
            (root / "external" / "mmd-anim" / "target" / "debug.txt").write_text("target\n", encoding="utf-8")
            (root / "external" / "mmd-anim" / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
            (root / "mmd_tools" / "compiled.pyo").write_text("cache\n", encoding="utf-8")

            installed_root = module_dir / "maya_mmd_tools"
            try:
                with patch.object(drag_drop_install, "_module_dir", return_value=module_dir):
                    with patch.object(drag_drop_install, "_maya_version", return_value="2024"):
                        with patch.object(drag_drop_install, "_package_version", return_value="1.2.3"):
                            with patch.object(cmds, "evalDeferred"):
                                drag_drop_install.install(root)

                self.assertFalse((installed_root / ".git").exists())
                self.assertFalse((installed_root / "__pycache__").exists())
                self.assertFalse((installed_root / "build").exists())
                self.assertFalse((installed_root / "docs-dev").exists())
                self.assertFalse((installed_root / "external" / "mmd-anim" / "target").exists())
                self.assertFalse((installed_root / "mmd_tools" / "compiled.pyo").exists())
                self.assertTrue((installed_root / "external" / "mmd-anim" / "Cargo.toml").is_file())
            finally:
                installed_root_path = str(installed_root.resolve())
                if installed_root_path in sys.path:
                    sys.path.remove(installed_root_path)

    def test_remove_existing_install_refuses_unsafe_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            module_dir = Path(tmp) / "modules"
            unsafe_target = module_dir / "not_maya_mmd_tools"
            unsafe_target.mkdir(parents=True)
            (unsafe_target / "file.txt").write_text("keep\n", encoding="utf-8")

            with self.assertRaises(RuntimeError):
                drag_drop_install._remove_existing_install(unsafe_target, module_dir)

            self.assertTrue((unsafe_target / "file.txt").is_file())


if __name__ == "__main__":
    unittest.main()
