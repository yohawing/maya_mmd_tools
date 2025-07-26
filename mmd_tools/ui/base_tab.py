from typing import Optional

from .qt_compat import QWidget
from .translations import UITranslator


class BaseTab(QWidget):
    """すべてのタブの基底クラス"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._translator = UITranslator.instance()
    
    def tr(self, key: str, category: Optional[str] = None) -> str:
        """UI文字列を現在の言語設定で取得
        
        Args:
            key: 翻訳キー
            category: カテゴリ（指定しない場合はドット記法で解釈）
            
        Returns:
            翻訳された文字列
            
        Examples:
            self.tr("import", "buttons") -> "インポート"
            self.tr("buttons.import") -> "インポート"
        """
        return self._translator.translate(key, category)
    
    def retranslateUi(self):
        """言語切り替え時にUIを再翻訳
        
        各タブクラスでオーバーライドして実装する
        """
        pass
