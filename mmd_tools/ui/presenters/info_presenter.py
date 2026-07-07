from mmd_tools.core.constants import (
    ATTR_MMD_COMMENT,
    ATTR_MMD_COMMENT_EN,
    ATTR_MMD_MODEL_NAME_EN,
    ATTR_MMD_MODEL_NAME,
)
from ...core.logger import get_logger
from ...core.maya_attribute_utils import (
    set_custom_attributes,
)
from ..combo_box_utils import add_combo_item_with_tooltip

logger = get_logger(__name__)


class InfoPresenter:
    def __init__(self, view, app_state):
        self.view = view
        self.app_state = app_state
        self.scene_model_service = self.app_state.scene_model_service
        self.is_updating = False  # 更新中フラグ（フィードバックループ防止）
        self.connect_signals()

        # 既に選択されているモデルがある場合は情報をロード
        if self.app_state.current_model_root:
            self.view.set_fields_enabled(True)
            self.load_model_info()

    def connect_signals(self):
        # ApplicationStateのシグナル
        self.app_state.current_model_changed.connect(self.on_current_model_changed)
        self.app_state.model_list_updated.connect(self.on_model_list_updated)

        # モデル選択（Infoタブ内のコンボボックスは残す場合）
        self.view.model_combo.currentTextChanged.connect(self.on_model_selected)
        self.view.refresh_button.clicked.connect(self.on_refresh_clicked)

        # モデル情報編集
        self.view.model_name_jp_edit.textChanged.connect(self.update_model_info)
        self.view.model_name_en_edit.textChanged.connect(self.update_model_info)
        # QTextEditはtextChangedではなくtextChangedシグナルを使用
        self.view.comment_jp_edit.textChanged.connect(self.update_model_info)
        self.view.comment_en_edit.textChanged.connect(self.update_model_info)

    def on_current_model_changed(self, model_root):
        """ApplicationStateのモデル変更を受けて更新"""
        if model_root:
            self.view.set_fields_enabled(True)
            self.load_model_info()
        else:
            self.view.set_fields_enabled(False)
            self.clear_fields()

    def on_model_list_updated(self, models):
        """モデルリストが更新されたときの処理"""
        self.update_model_combo(models)

    def load_model_info(self):
        current_model_root = self.app_state.current_model_root
        if not current_model_root or not self.scene_model_service.object_exists(current_model_root):
            logger.warning("No model selected or model does not exist.")
            # フィールドをクリア
            self.clear_fields()
            return

        try:
            # Disconnect signals to prevent feedback loops
            self.view.model_name_jp_edit.textChanged.disconnect(self.update_model_info)
            self.view.model_name_en_edit.textChanged.disconnect(self.update_model_info)
            self.view.comment_jp_edit.textChanged.disconnect(self.update_model_info)
            self.view.comment_en_edit.textChanged.disconnect(self.update_model_info)

            # アトリビュートの存在を確認
            if not self.scene_model_service.attribute_exists(current_model_root, ATTR_MMD_MODEL_NAME):
                logger.warning(f"Attribute {ATTR_MMD_MODEL_NAME} not found on {current_model_root}")

            # 文字列アトリビュートの値を安全に取得
            model_name_jp = self.scene_model_service.get_attr_safe(current_model_root, ATTR_MMD_MODEL_NAME, "")
            model_name_en = self.scene_model_service.get_attr_safe(current_model_root, ATTR_MMD_MODEL_NAME_EN, "")
            comment_jp = self.scene_model_service.get_attr_safe(current_model_root, ATTR_MMD_COMMENT, "")
            comment_en = self.scene_model_service.get_attr_safe(current_model_root, ATTR_MMD_COMMENT_EN, "")

            logger.debug(f"Loaded values - JP: '{model_name_jp}', EN: '{model_name_en}'")

            self.view.model_name_jp_edit.setText(model_name_jp)
            self.view.model_name_en_edit.setText(model_name_en)
            self.view.comment_jp_edit.setPlainText(comment_jp)  # QTextEditはsetPlainTextを使用
            self.view.comment_en_edit.setPlainText(comment_en)

            logger.info(f"Loaded model info for {current_model_root}")
        except Exception as e:
            logger.error(f"Failed to load model info: {e}", exc_info=True)
            # エラー時もフィールドをクリア
            self.clear_fields()
        finally:
            # Reconnect signals
            self.view.model_name_jp_edit.textChanged.connect(self.update_model_info)
            self.view.model_name_en_edit.textChanged.connect(self.update_model_info)
            self.view.comment_jp_edit.textChanged.connect(self.update_model_info)
            self.view.comment_en_edit.textChanged.connect(self.update_model_info)

    def clear_fields(self):
        """フィールドをクリア"""
        self.view.model_name_jp_edit.clear()
        self.view.model_name_en_edit.clear()
        self.view.comment_jp_edit.clear()
        self.view.comment_en_edit.clear()

    def update_model_info(self):
        current_model_root = self.app_state.current_model_root
        if not current_model_root or not self.scene_model_service.object_exists(current_model_root):
            return

        try:
            # set_custom_attributesを使用して一括設定
            attributes = {
                ATTR_MMD_MODEL_NAME: self.view.model_name_jp_edit.text(),
                ATTR_MMD_MODEL_NAME_EN: self.view.model_name_en_edit.text(),
                ATTR_MMD_COMMENT: self.view.comment_jp_edit.toPlainText(),
                ATTR_MMD_COMMENT_EN: self.view.comment_en_edit.toPlainText(),
            }
            set_custom_attributes(current_model_root, attributes)
            logger.debug(f"Updated model info for {current_model_root}")

            # ApplicationStateのキャッシュをクリア
            self.app_state.clear_cache()
        except Exception as e:
            logger.error(f"Failed to update model info: {e}", exc_info=True)

    def update_model_combo(self, models):
        """モデルコンボボックスを更新"""
        self.is_updating = True
        current_model = self.app_state.current_model_root

        # コンボボックスをクリア
        self.view.model_combo.clear()

        if not models:
            add_combo_item_with_tooltip(self.view.model_combo, "No MMD models found")
            self.view.set_fields_enabled(False)
        else:
            # コンボボックスにモデルを追加
            for model in models:
                display_name = self.scene_model_service.get_model_display_name(model)
                add_combo_item_with_tooltip(self.view.model_combo, f"{display_name} ({model})", user_data=model)

            # 現在のモデルを選択
            if current_model in models:
                index = models.index(current_model)
                self.view.model_combo.setCurrentIndex(index)
            else:
                self.view.model_combo.setCurrentIndex(0)

            self.view.set_fields_enabled(True)

        self.is_updating = False

    def on_refresh_clicked(self):
        """Refreshボタンがクリックされた時の処理"""
        # ApplicationStateでリフレッシュ
        self.app_state.refresh_model_list()

        # Maya選択からモデルを選択
        self.app_state.select_model_from_maya_selection()

    def on_model_selected(self, text):
        """コンボボックスでモデルが選択されたときの処理"""
        if self.is_updating or not text or text == "No MMD models found":
            return

        # userDataからルートノード名を取得
        index = self.view.model_combo.currentIndex()
        root_node = self.view.model_combo.itemData(index)

        if root_node and self.scene_model_service.object_exists(root_node):
            # ApplicationStateを更新
            self.app_state.current_model_root = root_node
            logger.info(f"Selected MMD model: {root_node}")
        else:
            self.app_state.current_model_root = None
