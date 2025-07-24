from maya import cmds
from ..qt_compat import Qt
from ...core.logger import get_logger
from ...core.maya_utils import (
    find_all_mmd_models,
    get_parent_mmd_root,
    get_mmd_model_display_name,
    set_custom_attributes,
)

logger = get_logger(__name__)


class InfoPresenter:
    def __init__(self, view):
        self.view = view
        self.current_model_root = None
        self.is_updating = False  # 更新中フラグ（フィードバックループ防止）
        self.connect_signals()
        self.scan_mmd_models()

    def connect_signals(self):
        # モデル選択
        self.view.model_combo.currentTextChanged.connect(self.on_model_selected)
        self.view.refresh_button.clicked.connect(self.on_refresh_clicked)

        # モデル情報編集
        self.view.model_name_jp_edit.textChanged.connect(self.update_model_info)
        self.view.model_name_en_edit.textChanged.connect(self.update_model_info)
        # QTextEditはtextChangedではなくtextChangedシグナルを使用
        self.view.comment_jp_edit.textChanged.connect(self.update_model_info)
        self.view.comment_en_edit.textChanged.connect(self.update_model_info)

    def on_model_imported(self, root_node):
        self.current_model_root = root_node
        self.view.set_fields_enabled(True)  # フィールドを有効化
        self.load_model_info()
        logger.info(f"Model info loaded for: {root_node}")

    def load_model_info(self):
        if not self.current_model_root or not cmds.objExists(self.current_model_root):
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
            if not cmds.attributeQuery(
                "mmd_model_name_jp", node=self.current_model_root, exists=True
            ):
                logger.warning(
                    f"Attribute mmd_model_name_jp not found on {self.current_model_root}"
                )

            # 文字列アトリビュートの値を安全に取得
            model_name_jp = ""
            model_name_en = ""
            comment_jp = ""
            comment_en = ""

            try:
                model_name_jp = cmds.getAttr(
                    f"{self.current_model_root}.mmd_model_name_jp"
                )
                if model_name_jp is None:
                    model_name_jp = ""
            except:
                model_name_jp = ""

            try:
                model_name_en = cmds.getAttr(
                    f"{self.current_model_root}.mmd_model_name_en"
                )
                if model_name_en is None:
                    model_name_en = ""
            except:
                model_name_en = ""

            try:
                comment_jp = cmds.getAttr(f"{self.current_model_root}.mmd_comment_jp")
                if comment_jp is None:
                    comment_jp = ""
            except:
                comment_jp = ""

            try:
                comment_en = cmds.getAttr(f"{self.current_model_root}.mmd_comment_en")
                if comment_en is None:
                    comment_en = ""
            except:
                comment_en = ""

            logger.debug(
                f"Loaded values - JP: '{model_name_jp}', EN: '{model_name_en}'"
            )

            self.view.model_name_jp_edit.setText(model_name_jp)
            self.view.model_name_en_edit.setText(model_name_en)
            self.view.comment_jp_edit.setPlainText(
                comment_jp
            )  # QTextEditはsetPlainTextを使用
            self.view.comment_en_edit.setPlainText(comment_en)

            logger.info(f"Loaded model info for {self.current_model_root}")
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
        if not self.current_model_root or not cmds.objExists(self.current_model_root):
            return

        try:
            # set_custom_attributesを使用して一括設定
            attributes = {
                "mmd_model_name_jp": self.view.model_name_jp_edit.text(),
                "mmd_model_name_en": self.view.model_name_en_edit.text(),
                "mmd_comment_jp": self.view.comment_jp_edit.toPlainText(),
                "mmd_comment_en": self.view.comment_en_edit.toPlainText()
            }
            set_custom_attributes(self.current_model_root, attributes)
            logger.debug(f"Updated model info for {self.current_model_root}")
        except Exception as e:
            logger.error(f"Failed to update model info: {e}", exc_info=True)

    def scan_mmd_models(self):
        """シーン内のMMDモデルをスキャンしてコンボボックスを更新"""
        self.is_updating = True
        current_text = self.view.model_combo.currentText()

        # コンボボックスをクリア
        self.view.model_combo.clear()

        # MMDモデルを検索
        mmd_models = find_all_mmd_models()

        if not mmd_models:
            self.view.model_combo.addItem("No MMD models found")
            self.view.set_fields_enabled(False)
            self.current_model_root = None
            self.is_updating = False
            return

        # コンボボックスにモデルを追加
        for model in mmd_models:
            display_name = get_mmd_model_display_name(model)
            self.view.model_combo.addItem(f"{display_name} ({model})", userData=model)

        # 以前の選択を復元、または最初のモデルを選択
        restored = False
        if current_text:
            index = self.view.model_combo.findText(current_text)
            if index >= 0:
                self.view.model_combo.setCurrentIndex(index)
                restored = True

        if not restored and mmd_models:
            # 現在のMaya選択からモデルを推測
            selected = cmds.ls(selection=True)
            if selected:
                for obj in selected:
                    parent_root = get_parent_mmd_root(obj)
                    if parent_root and parent_root in mmd_models:
                        index = mmd_models.index(parent_root)
                        self.view.model_combo.setCurrentIndex(index)
                        restored = True
                        break

        if not restored:
            self.view.model_combo.setCurrentIndex(0)

        self.is_updating = False

        # コンボボックスの選択が確定したら、手動でon_model_selectedを呼び出す
        if (
            self.view.model_combo.count() > 0
            and self.view.model_combo.currentText() != "No MMD models found"
        ):
            self.on_model_selected(self.view.model_combo.currentText())

    def on_refresh_clicked(self):
        """Refreshボタンがクリックされた時の処理"""
        # シーンを再スキャン
        self.scan_mmd_models()

        # 現在のMaya選択からモデルを推測して選択
        selected = cmds.ls(selection=True)
        if selected:
            for obj in selected:
                parent_root = get_parent_mmd_root(obj)
                if parent_root:
                    # コンボボックスで該当モデルを探す
                    for i in range(self.view.model_combo.count()):
                        if self.view.model_combo.itemData(i) == parent_root:
                            self.view.model_combo.setCurrentIndex(i)
                            logger.info(
                                f"Selected model based on Maya selection: {parent_root}"
                            )
                            break
                    break

    def on_model_selected(self, text):
        """コンボボックスでモデルが選択されたときの処理"""
        if self.is_updating or not text or text == "No MMD models found":
            return

        # userDataからルートノード名を取得
        index = self.view.model_combo.currentIndex()
        root_node = self.view.model_combo.itemData(index)

        if root_node and cmds.objExists(root_node):
            self.current_model_root = root_node
            self.view.set_fields_enabled(True)
            self.load_model_info()
            logger.info(f"Selected MMD model: {root_node}")
        else:
            self.current_model_root = None
            self.view.set_fields_enabled(False)
