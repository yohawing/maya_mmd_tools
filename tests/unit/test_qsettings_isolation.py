"""Regression coverage for the process-level UI QSettings boundary."""

import os
import plistlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.common.qsettings_isolation import (  # noqa: E402
    activate_qsettings_isolation,
    host_native_qsettings_fingerprints,
    isolated_qsettings_root,
    isolated_settings_store,
)

activate_qsettings_isolation()

from mmd_tools.ui.qt_compat import QApplication  # noqa: E402
from mmd_tools.ui.tabs.import_export_tab import ImportExportTab  # noqa: E402


def _run_plain_mayapy(code):
    """Run a Qt widget assertion outside Maya's headless QGuiApplication."""

    project_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project_root)
    environment["MMD_TEST_DEFER_MAYA_INIT"] = "1"
    environment["MAYA_SKIP_USERSETUP_PY"] = "1"
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(project_root),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestQSettingsIsolation(unittest.TestCase):
    """Use production widgets while proving both native scopes stay read-only."""

    @classmethod
    def setUpClass(cls):
        instance = QApplication.instance()
        # Maya 2024 mayapy owns a QGuiApplication after standalone startup.
        # Constructing QWidget subclasses on that batch application is a
        # native crash, so widget assertions are delegated to a plain mayapy
        # child while the surrounding contract tests remain in this suite.
        cls._plain_mayapy = instance is not None and not isinstance(instance, QApplication)
        cls.app = None if cls._plain_mayapy else instance or QApplication([])

    def setUp(self):
        self.store = isolated_settings_store("maya_mmd_tools", "ImportExportTab")
        self.store.clear()
        self.store.sync()

    def tearDown(self):
        self.store.clear()
        self.store.sync()
        if self.app is not None:
            self.app.processEvents()

    def test_host_fingerprint_boundary_does_not_import_qt(self):
        """The outer Maya launcher can probe native stores before PySide exists."""
        project_root = Path(__file__).resolve().parents[2]
        child_code = """
import sys
from tests.common.qsettings_isolation import host_native_qsettings_fingerprints
host_native_qsettings_fingerprints()
print("mmd_tools.ui.qt_compat" in sys.modules)
"""
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(project_root)
        completed = subprocess.run(
            [sys.executable, "-c", child_code],
            cwd=str(project_root),
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual("False", completed.stdout.strip())

    def test_host_mac_fingerprint_uses_qt_native_domain_path(self):
        """Qt NativeFormat uses com.<org>.<app>.plist on macOS."""
        from tests.common import qsettings_isolation

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            preferences = home / "Library" / "Preferences"
            preferences.mkdir(parents=True)
            qt_path = preferences / "com.yohawing.maya_mmd_tools.plist"
            legacy_path = preferences / "yohawing.maya_mmd_tools.plist"
            qt_path.write_bytes(plistlib.dumps({"matrix": "one"}))
            legacy_path.write_bytes(plistlib.dumps({"matrix": "legacy"}))
            scope = (("yohawing", "maya_mmd_tools"),)

            with mock.patch.object(qsettings_isolation.os, "name", "posix"), mock.patch.object(
                qsettings_isolation.sys, "platform", "darwin"
            ), mock.patch.object(qsettings_isolation.Path, "home", return_value=home):
                before = host_native_qsettings_fingerprints(scope)
                legacy_path.write_bytes(plistlib.dumps({"matrix": "legacy-changed"}))
                self.assertEqual(before, host_native_qsettings_fingerprints(scope))
                qt_path.write_bytes(plistlib.dumps({"matrix": "two"}))
                after = host_native_qsettings_fingerprints(scope)

        self.assertNotEqual(before, after)

    def test_default_production_qsettings_uses_temp_backend_and_fixture_value(self):
        if self._plain_mayapy:
            before = host_native_qsettings_fingerprints()
            try:
                completed = _run_plain_mayapy(
                    """
from tests.common.qsettings_isolation import (
    activate_qsettings_isolation,
    host_native_qsettings_fingerprints,
    isolated_qsettings_root,
    isolated_settings_store,
)
activate_qsettings_isolation()
from mmd_tools.ui.qt_compat import QApplication
from mmd_tools.ui.tabs.import_export_tab import ImportExportTab
app = QApplication.instance() or QApplication([])
store = isolated_settings_store("maya_mmd_tools", "ImportExportTab")
store.clear(); store.sync()
before = host_native_qsettings_fingerprints()
tab = ImportExportTab()
tab.import_path_edit.setText("matrix-value")
tab.namespace_edit.setText("matrix-value")
store.sync()
assert store.value("import_path") == "matrix-value"
assert store.value("custom_namespace_name") == "matrix-value"
assert str(isolated_qsettings_root()).replace("\\\\", "/").lower() in store.fileName().replace("\\\\", "/").lower()
assert "HKEY_CURRENT_USER" not in store.fileName()
tab.deleteLater(); app.processEvents()
assert before == host_native_qsettings_fingerprints()
"""
                )
            finally:
                after = host_native_qsettings_fingerprints()
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(before, after)
            return

        before = host_native_qsettings_fingerprints()
        tab = ImportExportTab()
        try:
            tab.import_path_edit.setText("matrix-value")
            tab.namespace_edit.setText("matrix-value")
            self.store.sync()
            self.assertEqual(self.store.value("import_path"), "matrix-value")
            self.assertEqual(self.store.value("custom_namespace_name"), "matrix-value")
            self.assertIn(
                str(isolated_qsettings_root()).replace("\\", "/").lower(),
                self.store.fileName().replace("\\", "/").lower(),
            )
            self.assertNotIn("HKEY_CURRENT_USER", self.store.fileName())
        finally:
            tab.deleteLater()
            self.app.processEvents()
        self.assertEqual(before, host_native_qsettings_fingerprints())

    def test_real_clear_history_click_only_clears_fixture_store(self):
        if self._plain_mayapy:
            before = host_native_qsettings_fingerprints()
            try:
                completed = _run_plain_mayapy(
                    """
import os
import tempfile
from tests.common.qsettings_isolation import activate_qsettings_isolation, host_native_qsettings_fingerprints
activate_qsettings_isolation()
from mmd_tools.ui.qt_compat import QApplication
from mmd_tools.ui.tabs.import_export_tab import ImportExportTab
app = QApplication.instance() or QApplication([])
with tempfile.TemporaryDirectory() as directory:
    paths = [os.path.join(directory, name) for name in ("model.pmx", "motion.vmd", "export.pmx")]
    for path in paths:
        with open(path, "w", encoding="utf-8"):
            pass
    tab = ImportExportTab()
    view_state = tab.view_state
    view_state.save_file_history("import", paths[0])
    view_state.save_file_history("vmd", paths[1])
    view_state.save_file_history("export", paths[2])
    before = host_native_qsettings_fingerprints()
    tab.clear_history_button.click()
    assert view_state.load_file_history() == [{"path": paths[2], "type": "export"}]
    tab.deleteLater(); app.processEvents()
assert before == host_native_qsettings_fingerprints()
"""
                )
            finally:
                after = host_native_qsettings_fingerprints()
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(before, after)
            return

        with tempfile.TemporaryDirectory() as directory:
            import_path = os.path.join(directory, "model.pmx")
            vmd_path = os.path.join(directory, "motion.vmd")
            export_path = os.path.join(directory, "export.pmx")
            for path in (import_path, vmd_path, export_path):
                with open(path, "w", encoding="utf-8"):
                    pass

            tab = ImportExportTab()
            try:
                view_state = tab.view_state
                view_state.save_file_history("import", import_path)
                view_state.save_file_history("vmd", vmd_path)
                view_state.save_file_history("export", export_path)
                before = host_native_qsettings_fingerprints()
                tab.clear_history_button.click()
                self.assertEqual(
                    view_state.load_file_history(),
                    [{"path": export_path, "type": "export"}],
                )
            finally:
                tab.deleteLater()
                self.app.processEvents()
            self.assertEqual(before, host_native_qsettings_fingerprints())

    def test_redirected_constructor_preserves_supported_overloads(self):
        """Only the production org/app overload is redirected."""
        state = activate_qsettings_isolation()
        redirected = state["redirected"]

        with_parent = redirected(
            "maya_mmd_tools",
            "ImportExportTab",
            parent=self.app or QApplication.instance(),
        )
        self.assertIn(
            str(state["root"]).replace("\\", "/").lower(),
            with_parent.fileName().replace("\\", "/").lower(),
        )

        explicit_path = state["root"] / "explicit.ini"
        explicit = redirected(str(explicit_path), redirected.IniFormat)
        self.assertEqual(
            str(explicit_path).replace("\\", "/").lower(),
            explicit.fileName().replace("\\", "/").lower(),
        )

        native = redirected(
            redirected.NativeFormat,
            redirected.UserScope,
            "maya_mmd_tools",
            "ImportExportTab",
        )
        self.assertNotIn(
            str(state["root"]).replace("\\", "/").lower(),
            native.fileName().replace("\\", "/").lower(),
        )

    def test_forced_child_termination_cannot_write_native_scopes(self):
        """A killed runner leaves only its temporary INI backend behind."""
        before = host_native_qsettings_fingerprints()
        project_root = Path(__file__).resolve().parents[2]
        child_code = """
import os
from tests.common.qsettings_isolation import activate_qsettings_isolation
activate_qsettings_isolation()
from mmd_tools.ui.import_export_view_state import ImportExportViewState
view_state = ImportExportViewState()
view_state.set("import_path", "matrix-value")
view_state._settings.sync()
os._exit(23)
"""
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(project_root)
        environment.setdefault("QT_QPA_PLATFORM", "offscreen")
        completed = subprocess.run(
            [sys.executable, "-c", child_code],
            cwd=str(project_root),
            env=environment,
            check=False,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 23)
        self.assertEqual(before, host_native_qsettings_fingerprints())


if __name__ == "__main__":
    unittest.main()
