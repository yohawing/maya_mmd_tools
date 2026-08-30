"""
メインウィンドウのヘッダーウィジェット
モデル選択、モデル情報表示、クイックアクションを提供
"""

import inspect

from ...core.logger import get_logger
from ...core.name_display import preferred_pmx_display_name
from ..combo_box_utils import add_combo_item_with_tooltip, configure_model_combo_width
from ..qt_compat import (
    QWidget,
    QHBoxLayout,
    QLabel,
    QComboBox,
)
from ..translations import UITranslator
from ..presenters.list_presenter_helpers import maya_node_leaf_name
from .symbol_tool_button import SymbolToolButton

logger = get_logger(__name__)


class HeaderWidget(QWidget):
    """ヘッダーウィジェット"""

    def __init__(self, app_state, parent=None):
        super().__init__(parent)
        self.app_state = app_state
        self._translator = UITranslator.instance()
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
        self.model_label = QLabel()
        main_layout.addWidget(self.model_label)

        # モデル選択コンボボックス
        self.model_combo = QComboBox()
        configure_model_combo_width(self.model_combo)
        main_layout.addWidget(self.model_combo)

        # リフレッシュボタン
        self.refresh_btn = SymbolToolButton("refresh", "Refresh")
        self.refresh_btn.setObjectName("headerRefreshButton")
        main_layout.addWidget(self.refresh_btn)

        # 右側のスペース
        main_layout.addStretch()
        self.retranslateUi()
        add_combo_item_with_tooltip(
            self.model_combo,
            self.tr("no_mmd_models", "placeholders"),
        )

    def tr(self, key, category=None):
        """現在の UI 言語で翻訳する。"""
        return self._translator.translate(key, category)

    def retranslateUi(self):
        """言語切替時にヘッダーの固定テキストを更新する。"""
        self.model_label.setText(self.tr("current_model", "fields"))
        self.refresh_btn.setToolTip(self.tr("refresh_list", "tooltips"))
        if self.model_combo.count() == 1 and self.model_combo.itemData(0) is None:
            self.model_combo.setItemText(0, self.tr("no_mmd_models", "placeholders"))

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
        refresh = self.app_state.refresh_model_list
        # ApplicationState's explicit path owns selection revalidation.  Keep
        # a narrow fallback for legacy/headless app-state doubles that still
        # expose the pre-generation zero-argument method.
        try:
            parameters = inspect.signature(refresh).parameters.values()
            supports_explicit = any(
                parameter.name == "explicit"
                or parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in parameters
            )
        except (TypeError, ValueError):
            supports_explicit = False
        if supports_explicit:
            refresh(explicit=True)
        else:
            refresh()
            if not hasattr(self.app_state, "refresh_generation"):
                self.app_state.select_model_from_maya_selection()

    def on_model_list_updated(self, models):
        """モデルリストが更新されたときの処理"""
        self.is_updating = True

        # 現在の選択を保存
        current_model = self.app_state.current_model_root

        # コンボボックスを更新
        self.model_combo.clear()

        if not models:
            add_combo_item_with_tooltip(self.model_combo, self.tr("no_mmd_models", "placeholders"))
        else:
            for model in models:
                info = self.app_state.get_model_info(model)
                if info:
                    leaf = maya_node_leaf_name(model)
                    display_name = preferred_pmx_display_name(
                        info.get("name_jp"),
                        info.get("name_en"),
                        fallback=leaf,
                        language=self._translator.get_language(),
                    )
                    namespace = info.get("namespace")
                    if self._translator.get_language() == "en":
                        if namespace:
                            # English labels must distinguish same-named PMX
                            # roots loaded into different Maya namespaces,
                            # without falling back to the Japanese PMX name.
                            label = f"{display_name} [{namespace}]"
                        else:
                            label = f"{display_name} [{leaf}]" if leaf.isascii() else display_name
                    elif namespace:
                        # namespace付きの場合
                        label = f"{display_name} [{namespace}:{model.split(':')[-1]}]"
                    else:
                        # namespaceなしの場合
                        label = f"{display_name} [{model}]"
                    add_combo_item_with_tooltip(self.model_combo, label, user_data=model)
                else:
                    add_combo_item_with_tooltip(self.model_combo, model, user_data=model)

            # 現在のモデルを選択
            if current_model in models:
                index = models.index(current_model)
                self.model_combo.setCurrentIndex(index)
            else:
                # Some legacy services return short roots while Application
                # State retains the canonical long identity.
                service = getattr(self.app_state, "scene_model_service", None)
                canonicalize = getattr(service, "canonical_node", None)
                if callable(canonicalize):
                    try:
                        current_identity = canonicalize(current_model)
                        for index, model in enumerate(models):
                            if canonicalize(model) == current_identity:
                                self.model_combo.setCurrentIndex(index)
                                break
                    except Exception:
                        logger.debug("Could not match canonical current model in Header", exc_info=True)

        self.is_updating = False

    def on_combo_selection_changed(self, text):
        """コンボボックスの選択が変更されたときの処理"""
        if self.is_updating or not text or text.startswith("--"):
            return

        index = self.model_combo.currentIndex()
        model_root = self.model_combo.itemData(index)

        logger.debug(f"HeaderWidget: Model selected from combo: {model_root}")
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
