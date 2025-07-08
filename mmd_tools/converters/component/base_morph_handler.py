"""
BaseMorphHandler: モーフハンドラーの抽象基底クラス

このモジュールは、すべてのモーフハンドラーが継承する基底クラスを定義します。
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict

from maya import cmds
from mmd_tools.core import maya_utils


class BaseMorphHandler(ABC):
    """モーフハンドラーの基底クラス"""

    def __init__(self, settings_dict: dict):
        self.settings = settings_dict
        self.logger = self._setup_logger()

    def _setup_logger(self) -> logging.Logger:
        """ログ出力の設定"""
        return maya_utils.setup_logger(f"mmd_tools.{self.__class__.__name__}")

    @abstractmethod
    def can_handle(self, morph_data: Any) -> bool:
        """このハンドラーが対象のモーフタイプを処理できるかチェック"""
        pass

    @abstractmethod
    def convert(self, morph_data: Any, mesh_node: str, **kwargs) -> Dict[str, Any]:
        """モーフデータをMayaのblendShapeに変換"""
        pass

    def validate_input(self, morph_data: Any, mesh_node: str) -> bool:
        """入力データの検証"""
        # 基本的な検証
        if not hasattr(morph_data, "name") or not morph_data.name:
            self.logger.warning("Morph data has no name")
            return False

        if not cmds.objExists(mesh_node):
            self.logger.error(f"Mesh node {mesh_node} does not exist")
            return False

        return True
