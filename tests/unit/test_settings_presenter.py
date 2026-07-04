"""SettingsPresenter の純 Python ロジックテスト (Maya / Qt 非依存)。

Development Mode チェックボックス変更時に logging.level が決定論的に設定される動作を検証する。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.core.settings import Settings  # noqa: E402
from mmd_tools.ui.presenters.settings_presenter import SettingsPresenter  # noqa: E402


def _reset_singleton():
    Settings._instance = None


# ---------------------------------------------------------------------------
# Minimal view / app-state fakes
# ---------------------------------------------------------------------------

class _FakeSignal:
    def connect(self, _cb):
        pass


class _FakeCheckBox:
    def __init__(self, checked=False):
        self._checked = checked
        self.stateChanged = _FakeSignal()

    def isChecked(self):
        return self._checked

    def setChecked(self, v):
        self._checked = bool(v)


class _FakeComboBox:
    def __init__(self, items=None, index=0):
        self._items = items or ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        self._index = index
        self.currentTextChanged = _FakeSignal()
        self.currentIndexChanged = _FakeSignal()

    def addItems(self, items):
        self._items = list(items)

    def addItem(self, label, data=None):
        self._items.append(label)

    def currentText(self):
        if 0 <= self._index < len(self._items):
            return self._items[self._index]
        return ""

    def currentData(self):
        return None

    def findText(self, text):
        try:
            return self._items.index(text)
        except ValueError:
            return -1

    def setCurrentIndex(self, idx):
        self._index = idx

    def count(self):
        return len(self._items)

    def itemData(self, _i):
        return None


class _FakeLineEdit:
    def __init__(self, text=""):
        self._text = text

    def text(self):
        return self._text

    def setText(self, v):
        self._text = v


class _FakeSpinBox:
    def __init__(self, value=3939):
        self._value = value

    def value(self):
        return self._value

    def setValue(self, value):
        self._value = int(value)


class _FakeGroup:
    def __init__(self):
        self.visible = None

    def setVisible(self, value):
        self.visible = bool(value)


class _FakeButton:
    clicked = _FakeSignal()

    def __init__(self):
        self.text = ""

    def setText(self, text):
        self.text = text


class _FakeMayaCmds:
    def __init__(self):
        self.open_ports = set()
        self.calls = []

    def commandPort(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        name = kwargs.get("name") or (args[0] if args else None)
        if kwargs.get("query"):
            return name in self.open_ports
        if kwargs.get("close"):
            self.open_ports.discard(name)
            return None
        self.open_ports.add(name)
        return None


class _FakeView:
    def __init__(self):
        self.development_mode_check = _FakeCheckBox(False)
        self.log_level_combo = _FakeComboBox()
        self.logging_enabled_check = _FakeCheckBox(True)
        self.log_file_path_edit = _FakeLineEdit("logs/mmd_tools.log")
        self.language_combo = _FakeComboBox(["Japanese", "English"], 0)
        self.command_port_spin = _FakeSpinBox()
        self.dev_tools_group = _FakeGroup()
        self.save_settings_btn = _FakeButton()
        self.reset_settings_btn = _FakeButton()
        self.export_settings_btn = _FakeButton()
        self.import_settings_btn = _FakeButton()
        self.log_file_browse_btn = _FakeButton()
        self.open_command_port_btn = _FakeButton()
        self.import_export_tab = _FakeImportExportTab()

    def window(self):
        return self


class _FakeImportExportTab:
    def __init__(self):
        self.apply_dev_mode_visibility_calls = 0

    def _apply_dev_mode_visibility(self):
        self.apply_dev_mode_visibility_calls += 1


class _FakeAppState:
    def __init__(self):
        self.statuses = []

    def emit_status(self, msg):
        self.statuses.append(msg)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDevelopmentModeLogLevels(unittest.TestCase):
    """Development Mode ON/OFF 時のログレベル同期動作を検証する。"""

    def setUp(self):
        _reset_singleton()
        self.view = _FakeView()
        self.app_state = _FakeAppState()
        self.maya_cmds = _FakeMayaCmds()
        self.presenter = SettingsPresenter(self.view, self.app_state, maya_cmds=self.maya_cmds)

    def tearDown(self):
        _reset_singleton()

    def test_dev_mode_on_sets_info_level(self):
        """Development Mode を ON にすると logging.level が INFO になる。"""
        from mmd_tools.core.settings import settings

        self.view.development_mode_check.setChecked(True)
        self.presenter.on_development_mode_changed()

        self.assertEqual(settings.get("logging.level"), "INFO")

    def test_dev_mode_off_sets_warning_level(self):
        """Development Mode を OFF にすると logging.level が WARNING になる。"""
        from mmd_tools.core.settings import settings

        self.view.development_mode_check.setChecked(False)
        self.presenter.on_development_mode_changed()

        self.assertEqual(settings.get("logging.level"), "WARNING")

    def test_dev_mode_on_updates_combo_to_info(self):
        """Development Mode ON 時、コンボボックス表示が INFO に変わる。"""
        self.view.development_mode_check.setChecked(True)
        self.presenter.on_development_mode_changed()

        self.assertEqual(self.view.log_level_combo.currentText(), "INFO")

    def test_dev_mode_off_updates_combo_to_warning(self):
        """Development Mode OFF 時、コンボボックス表示が WARNING に変わる。"""
        self.view.development_mode_check.setChecked(True)
        self.presenter.on_development_mode_changed()

        self.view.development_mode_check.setChecked(False)
        self.presenter.on_development_mode_changed()

        self.assertEqual(self.view.log_level_combo.currentText(), "WARNING")

    def test_no_change_during_loading(self):
        """_loading フラグが立っている間は on_development_mode_changed が無視される。"""
        from mmd_tools.core.settings import settings

        settings.set("logging.level", "DEBUG")

        self.presenter._loading = True
        self.view.development_mode_check.setChecked(True)
        self.presenter.on_development_mode_changed()
        self.presenter._loading = False

        # _loading 中なので変更されない
        self.assertEqual(settings.get("logging.level"), "DEBUG")


class TestLoadSettings(unittest.TestCase):
    """load_settings() が development_mode を正しく読み込む。"""

    def setUp(self):
        _reset_singleton()
        self.view = _FakeView()
        self.app_state = _FakeAppState()
        self.maya_cmds = _FakeMayaCmds()
        self.presenter = SettingsPresenter(self.view, self.app_state, maya_cmds=self.maya_cmds)

    def tearDown(self):
        _reset_singleton()

    def test_default_logging_level_is_warning(self):
        """default_settings.json で logging.level が WARNING に設定されている。"""
        import json

        config_path = os.path.join(
            os.path.dirname(__file__), "..", "..",
            "mmd_tools", "config", "default_settings.json",
        )
        with open(config_path) as f:
            defaults = json.load(f)
        self.assertEqual(defaults["logging"]["level"], "WARNING")
        self.assertNotIn("log_level", defaults["ui"]["general"])

    def test_development_mode_default_is_false(self):
        """デフォルト設定では development_mode が False (チェックなし) になる。"""
        from mmd_tools.core.settings import settings
        settings.set("ui.general.development_mode", False)
        self.presenter.load_settings()
        self.assertFalse(self.view.development_mode_check.isChecked())

    def test_development_mode_true_checked(self):
        """settings に development_mode=True を設定すると、ロード後にチェックされる。"""
        from mmd_tools.core.settings import settings
        settings.set("ui.general.development_mode", True)
        settings.set("ui.dev.command_port", 3939)
        self.presenter.load_settings()
        self.assertTrue(self.view.development_mode_check.isChecked())
        self.assertIn(":3939", self.maya_cmds.open_ports)

    def test_default_command_port_is_3939(self):
        """Command Port のデフォルトは 3939。"""
        import json

        config_path = os.path.join(
            os.path.dirname(__file__), "..", "..",
            "mmd_tools", "config", "default_settings.json",
        )
        with open(config_path) as f:
            defaults = json.load(f)
        self.assertEqual(defaults["ui"]["dev"]["command_port"], 3939)

    def test_dev_tools_visibility_follows_development_mode(self):
        """開発ツールは Development Mode に従って表示する。"""
        from mmd_tools.core.settings import settings

        settings.set("ui.general.development_mode", False)
        self.presenter.load_settings()
        self.assertFalse(self.view.dev_tools_group.visible)

        settings.set("ui.general.development_mode", True)
        self.presenter.load_settings()
        self.assertTrue(self.view.dev_tools_group.visible)


class TestSaveSettings(unittest.TestCase):
    """save_all_settings() が development_mode を正しく保存する。"""

    def setUp(self):
        _reset_singleton()
        self.view = _FakeView()
        self.app_state = _FakeAppState()
        self.maya_cmds = _FakeMayaCmds()
        self.presenter = SettingsPresenter(self.view, self.app_state, maya_cmds=self.maya_cmds)

    def tearDown(self):
        _reset_singleton()

    def test_save_persists_development_mode_true(self):
        """チェックを ON にして保存すると settings に True が書き込まれる。"""
        from mmd_tools.core.settings import settings

        self.view.development_mode_check.setChecked(True)
        self.presenter.save_all_settings()

        self.assertTrue(settings.get("ui.general.development_mode"))

    def test_save_persists_development_mode_false(self):
        """チェックを OFF にして保存すると settings に False が書き込まれる。"""
        from mmd_tools.core.settings import settings

        self.view.development_mode_check.setChecked(False)
        self.presenter.save_all_settings()

        self.assertFalse(settings.get("ui.general.development_mode"))

    def test_save_reapplies_import_export_dev_visibility(self):
        """保存後、現在の Import/Export タブ表示にも Development Mode を再適用する。"""
        self.view.development_mode_check.setChecked(True)

        self.presenter.save_all_settings()

        self.assertEqual(self.view.import_export_tab.apply_dev_mode_visibility_calls, 1)

    def test_save_persists_command_port(self):
        """Command Port 欄の値を設定に保存する。"""
        from mmd_tools.core.settings import settings

        self.view.command_port_spin.setValue(7788)
        self.presenter.save_all_settings()

        self.assertEqual(settings.get("ui.dev.command_port"), 7788)


class TestCommandPortAction(unittest.TestCase):
    """開発用 commandPort ボタンの動作を検証する。"""

    def setUp(self):
        _reset_singleton()
        self.view = _FakeView()
        self.app_state = _FakeAppState()
        self.maya_cmds = _FakeMayaCmds()
        self.presenter = SettingsPresenter(self.view, self.app_state, maya_cmds=self.maya_cmds)

    def tearDown(self):
        _reset_singleton()

    def test_open_command_port_requires_development_mode(self):
        """通常モードでは commandPort を開かない。"""
        self.view.development_mode_check.setChecked(False)

        self.presenter.open_command_port()

        self.assertTrue(self.app_state.statuses)
        self.assertIn("Command Port", self.app_state.statuses[-1])

    def test_open_command_port_creates_python_command_port(self):
        """開発モードでは Python commandPort を開く。"""
        from mmd_tools.core.settings import settings

        self.view.development_mode_check.setChecked(True)
        self.view.command_port_spin.setValue(7788)

        self.presenter.open_command_port()

        self.assertIn(":7788", self.maya_cmds.open_ports)
        self.assertEqual(settings.get("ui.dev.command_port"), 7788)
        self.assertIn(":7788", self.app_state.statuses[-1])

    def test_open_command_port_reports_existing_port(self):
        """同じ commandPort が Maya 内で開いている場合は再作成しない。"""
        self.view.development_mode_check.setChecked(True)
        self.view.command_port_spin.setValue(3939)
        self.maya_cmds.open_ports.add(":3939")

        self.presenter.open_command_port()

        self.assertIn(":3939", self.app_state.statuses[-1])

    def test_close_command_port_closes_existing_port(self):
        """開いている commandPort は閉じられる。"""
        self.view.development_mode_check.setChecked(True)
        self.view.command_port_spin.setValue(7788)
        self.maya_cmds.open_ports.add(":7788")

        self.presenter.close_command_port()

        self.assertNotIn(":7788", self.maya_cmds.open_ports)
        self.assertIn(":7788", self.app_state.statuses[-1])

    def test_toggle_command_port_closes_when_open(self):
        """ボタン操作は開いている port では close として働く。"""
        self.view.development_mode_check.setChecked(True)
        self.view.command_port_spin.setValue(7788)
        self.maya_cmds.open_ports.add(":7788")

        self.presenter.toggle_command_port()

        self.assertNotIn(":7788", self.maya_cmds.open_ports)

    def test_close_button_text_falls_back_when_translation_cache_is_stale(self):
        """古い翻訳キャッシュでも close ボタン表示は警告キー文字列に落ちない。"""
        from mmd_tools.ui.translations import UITranslator

        translator = UITranslator.instance()
        original_translations = translator._translations
        original_language = translator.get_language()
        try:
            translator.set_language("ja")
            translator._translations = {
                "ja": {"buttons": {"open_command_port": "開く"}},
                "en": {"buttons": {"open_command_port": "Open"}},
            }
            self.view.command_port_spin.setValue(7788)
            self.maya_cmds.open_ports.add(":7788")

            self.presenter.refresh_command_port_status()

            self.assertEqual(self.view.open_command_port_btn.text, "閉じる")
        finally:
            translator._translations = original_translations
            translator.set_language(original_language)

    def test_development_mode_on_auto_opens_command_port(self):
        """Development Mode を ON にした時点で commandPort を自動 open する。"""
        self.view.command_port_spin.setValue(7788)
        self.view.development_mode_check.setChecked(True)

        self.presenter.on_development_mode_changed()

        self.assertIn(":7788", self.maya_cmds.open_ports)

    def test_development_mode_off_auto_closes_command_port(self):
        """Development Mode を OFF にすると開いている commandPort を閉じる。"""
        self.view.command_port_spin.setValue(7788)
        self.view.development_mode_check.setChecked(False)
        self.maya_cmds.open_ports.add(":7788")

        self.presenter.on_development_mode_changed()

        self.assertNotIn(":7788", self.maya_cmds.open_ports)


if __name__ == "__main__":
    unittest.main()
