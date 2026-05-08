"""
maya_mmd_toolsの設定を管理するモジュールです

MayaのoptionVarを使用して、セッション間で設定を永続化します。
Settingsクラスのシングルトンインスタンスが作成され、プラグインのすべての部分が同じ設定にアクセスできるようになります。
"""

import json
import os
from collections import UserDict

try:
    from maya import cmds

    MAYA_AVAILABLE = True
except ImportError:
    MAYA_AVAILABLE = False


class Settings(UserDict):
    """
    MayaのoptionVarを使用してプラグイン設定を管理するための辞書のようなクラスです。

    このクラスは、JSONファイルからデフォルト値を読み込み、ネストされた辞書アクセスを許可し、
    変更をMayaのoptionVarsに永続化します。
    """

    _instance = None
    _prefix = "mmd_tools_"

    # Singleton pattern to ensure only one instance exists
    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(Settings, cls).__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self, *args, **kwargs):
        # This check prevents re-initialization for the singleton
        if hasattr(self, "_initialized"):
            return
        super().__init__(*args, **kwargs)
        self._initialized = True
        self._defaults = self._load_defaults_from_json()
        self.data = self._defaults.copy()
        # Maya環境が利用可能な場合のみ設定を読み込む
        if MAYA_AVAILABLE:
            try:
                self.load()
            except AttributeError:
                # Maya APIが完全に初期化されていない場合はスキップ
                pass

    def _load_defaults_from_json(self):
        """デフォルトの設定をJSONファイルから読み込みます。"""
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "..", "config", "default_settings.json")

        if not os.path.exists(json_path):
            return {}

        try:
            with open(json_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def __setitem__(self, key, value):
        """Set an item and save it to optionVar."""
        super().__setitem__(key, value)
        self.save()

    def _flatten_dict(self, d, parent_key="", sep="::"):
        """Flatten a nested dictionary."""
        items = []
        for k, v in d.items():
            if k.startswith("_"):
                continue  # Ignore comments
            new_key = parent_key + sep + k if parent_key else k
            if isinstance(v, dict):
                items.extend(self._flatten_dict(v, new_key, sep=sep).items())
            else:
                items.append((new_key, v))
        return dict(items)

    def get_option_var_key(self, key):
        """Get the full key for the optionVar."""
        return f"{self._prefix}{key}"

    def load(self):
        """Load all settings from Maya's optionVars."""
        if not MAYA_AVAILABLE:
            return

        # cmds.optionVarが利用可能かチェック
        try:
            if not hasattr(cmds, "optionVar"):
                return
        except Exception:
            return

        flat_defaults = self._flatten_dict(self._defaults)
        for key, default_value in flat_defaults.items():
            option_var_key = self.get_option_var_key(key)
            value = default_value
            if cmds.optionVar(exists=option_var_key):
                loaded_value = cmds.optionVar(q=option_var_key)
                # Convert type based on the default value's type
                try:
                    if isinstance(default_value, bool):
                        value = bool(int(loaded_value))
                    else:
                        value = type(default_value)(loaded_value)
                except (ValueError, TypeError):
                    value = default_value  # Fallback to default if conversion fails

            # Set the loaded value in the nested dictionary
            keys = key.split("::")
            d = self.data
            for k in keys[:-1]:
                d = d.setdefault(k, {})
            d[keys[-1]] = value

    def save(self):
        """Save all current settings to Maya's optionVars."""
        if not MAYA_AVAILABLE:
            return

        # cmds.optionVarが利用可能かチェック
        try:
            if not hasattr(cmds, "optionVar"):
                return
        except Exception:
            return

        flat_data = self._flatten_dict(self.data)
        for key, value in flat_data.items():
            option_var_key = self.get_option_var_key(key)
            if isinstance(value, bool):
                cmds.optionVar(iv=(option_var_key, int(value)))
            elif isinstance(value, int):
                cmds.optionVar(iv=(option_var_key, value))
            elif isinstance(value, float):
                cmds.optionVar(fv=(option_var_key, value))
            elif isinstance(value, str):
                cmds.optionVar(sv=(option_var_key, value))

    def reset(self):
        """Reset all settings to their default values from the JSON file."""
        self._defaults = self._load_defaults_from_json()
        self.data = self._defaults.copy()
        self.save()

    def get(self, key_path, default=None):
        """
        ドット記法を使用して設定値にアクセスします。

        Args:
            key_path (str): ドットで区切られたキーパス (例: 'import.physics.import_physics')
            default: キーが見つからない場合に返す値

        Returns:
            設定値、またはキーが見つからない場合はデフォルト値
        """
        keys = key_path.split(".")
        value = self.data

        for key in keys:
            try:
                value = value[key]
            except (KeyError, TypeError):
                return default

        return value

    def set(self, key_path, value):
        """
        ドット記法を使用して設定値を設定します。

        Args:
            key_path (str): ドットで区切られたキーパス (例: 'import.physics.import_physics')
            value: 設定する値
        """
        keys = key_path.split(".")
        d = self.data

        for key in keys[:-1]:
            if key not in d:
                d[key] = {}
            d = d[key]

        d[keys[-1]] = value
        self.save()


# シングルトンインスタンスを保持する変数
_settings_instance = None


def get_settings():
    """設定インスタンスを取得する関数（遅延初期化）"""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance


# 遅延初期化のためのプロパティ風アクセス
class SettingsProxy:
    """設定へのプロキシクラス（遅延初期化を実現）"""

    def __getattr__(self, name):
        return getattr(get_settings(), name)

    def __setattr__(self, name, value):
        return setattr(get_settings(), name, value)


# Singleton instance for global access
settings = SettingsProxy()
