"""
MMD Tools - Maya MMD import/export toolkit
"""

# パッケージのメタデータ
__name__ = "mmd_tools"
__version__ = "1.0.0"
__author__ = "MMD Tools Team"

# コアモジュールを直接アクセス可能にする
from .core.exceptions import MMDParseException
from .core.mmd_parser import parse_mmd_file
from .core.pmd_parser import PmdParser
from .core.pmx_parser import PmxParser
from .core.vmd_parser import VmdParser
from .settings import get_settings, settings

# 公開API
__all__ = [
    "MMDParseException",
    "PmdParser",
    "PmxParser",
    "VmdParser",
    "get_settings",
    "parse_mmd_file",
    "settings",
]
