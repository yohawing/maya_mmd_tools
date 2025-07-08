"""
MorphConverterFactory: モーフハンドラーファクトリー

このモジュールは、モーフタイプに応じた適切なハンドラーを提供するファクトリークラスを定義します。
"""

from typing import Any, Dict, Type

from .base_morph_handler import BaseMorphHandler
from .vertex_morph_handler import VertexMorphHandler
from .uv_morph_handler import UVMorphHandler
from .material_morph_handler import MaterialMorphHandler
from .group_morph_handler import GroupMorphHandler
from .bone_morph_handler import BoneMorphHandler


class MorphConverterFactory:
    """モーフタイプに応じたハンドラーを提供するファクトリー"""

    def __init__(self):
        self._handlers: Dict[str, Type[BaseMorphHandler]] = {}
        self._register_default_handlers()

    def _register_default_handlers(self):
        """デフォルトハンドラーの登録"""
        self.register_handler("vertex", VertexMorphHandler)
        self.register_handler("uv", UVMorphHandler)
        self.register_handler("material", MaterialMorphHandler)
        self.register_handler("group", GroupMorphHandler)
        self.register_handler("bone", BoneMorphHandler)

    def register_handler(self, morph_type: str, handler_class: Type[BaseMorphHandler]):
        """カスタムハンドラーの登録"""
        self._handlers[morph_type] = handler_class

    def get_handler(self, morph_data: Any, settings_dict: dict) -> BaseMorphHandler:
        """適切なハンドラーを取得"""
        for handler_class in self._handlers.values():
            handler = handler_class(settings_dict)
            if handler.can_handle(morph_data):
                return handler
        raise ValueError(f"No handler found for morph type: {type(morph_data)}")
