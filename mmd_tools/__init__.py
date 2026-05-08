"""
MMD Tools - Maya MMD import/export toolkit
"""

import sys

# パッケージのメタデータ
__name__ = "mmd_tools"
__version__ = "0.1.0"
__author__ = "MMD Tools Team"

# コアモジュールを直接アクセス可能にする
from .core.exceptions import MMDParseException
from .core.mmd_parser import parse_mmd_file
from .core.pmd_data import PmdData
from .core.pmx_data import PmxData
from .core.vmd_data import VmdData
from .core.settings import get_settings, settings
from .core import settings as _settings_module

# mmd_tools.settings としてアクセス可能にする
sys.modules["mmd_tools.settings"] = _settings_module

# 公開API
__all__ = [
    "MMDParseException",
    "PmdData",
    "PmxData",
    "VmdData",
    "get_settings",
    "parse_mmd_file",
    "settings",
]
