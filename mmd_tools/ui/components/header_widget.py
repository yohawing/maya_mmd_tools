"""
メインウィンドウのヘッダーウィジェット
モデル選択、モデル情報表示、クイックアクションを提供
"""

from ...core.logger import get_logger
from ..qt_compat import (
    QWidget,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
)

logger = get_logger(__name__)


class HeaderWidget(QWidget):
    """ヘッダーウィジェット"""

    def __init__(self, app_state, parent=None):
        super().__init__(parent)
        self.app_state = app_state
        self.is_updating = False

        self.setup_ui()
        self.connect_signals()

        # 初期状態を設定
        self.refresh_model_list()

    def setup_ui(self):
        """UIをセットアップ"""
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)

        # 現在のモデルラベル
        model_label = QLabel("現在のモデル:")
        main_layout.addWidget(model_label)

        # モデル選択コンボボックス
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(250)
        main_layout.addWidget(self.model_combo)

        # リフレッシュボタン
        self.refresh_btn = QPushButton("🔄")
        self.refresh_btn.setToolTip("モデルリストを更新")
        self.refresh_btn.setMaximumWidth(30)
        main_layout.addWidget(self.refresh_btn)

        # 右側のスペース
        main_layout.addStretch()

    def connect_signals(self):
        """シグナルを接続"""
        # ApplicationStateのシグナル
        self.app_state.current_model_changed.connect(self.on_current_model_changed)
        self.app_state.model_list_updated.connect(self.on_model_list_updated)

        # UIのシグナル
        self.model_combo.currentTextChanged.connect(self.on_combo_selection_changed)
        self.refresh_btn.clicked.connect(self.refresh_model_list)

    def refresh_model_list(self):
        """モデルリストを更新"""
        self.app_state.refresh_model_list()

    def on_model_list_updated(self, models):
        """モデルリストが更新されたときの処理"""
        self.is_updating = True

        # 現在の選択を保存
        current_model = self.app_state.current_model_root

        # コンボボックスを更新
        self.model_combo.clear()

        if not models:
            self.model_combo.addItem("-- モデルが見つかりません --")
        else:
            for model in models:
                info = self.app_state.get_model_info(model)
                if info:
                    display_name = info["display_name"]
                    namespace = info.get("namespace")
                    if namespace:
                        # namespace付きの場合
                        self.model_combo.addItem(f"{display_name} [{namespace}:{model.split(':')[-1]}]", userData=model)
                    else:
                        # namespaceなしの場合
                        self.model_combo.addItem(f"{display_name} [{model}]", userData=model)
                else:
                    self.model_combo.addItem(model, userData=model)

            # 現在のモデルを選択
            if current_model in models:
                index = models.index(current_model)
                self.model_combo.setCurrentIndex(index)

        self.is_updating = False

    def on_combo_selection_changed(self, text):
        """コンボボックスの選択が変更されたときの処理"""
        if self.is_updating or not text or text.startswith("--"):
            return

        index = self.model_combo.currentIndex()
        model_root = self.model_combo.itemData(index)

        logger.info(f"HeaderWidget: Model selected from combo: {model_root}")
        if model_root:
            self.app_state.current_model_root = model_root

    def on_current_model_changed(self, model_root):
        """現在のモデルが変更されたときの処理"""
        # コンボボックスの選択を更新
        if model_root:
            for i in range(self.model_combo.count()):
                if self.model_combo.itemData(i) == model_root:
                    self.is_updating = True
                    self.model_combo.setCurrentIndex(i)
                    self.is_updating = False
                    break
