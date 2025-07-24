"""
メインウィンドウのヘッダーウィジェット
モデル選択、モデル情報表示、クイックアクションを提供
"""

from maya import cmds
from ...core.logger import get_logger
from ..qt_compat import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QGroupBox,
    QGridLayout,
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
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)
        
        # 上段：モデル選択とアクション
        top_layout = QHBoxLayout()
        
        # モデル選択
        model_group = QGroupBox("現在のモデル")
        model_layout = QHBoxLayout()
        
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(250)
        model_layout.addWidget(self.model_combo)
        
        self.refresh_btn = QPushButton("🔄")
        self.refresh_btn.setToolTip("モデルリストを更新")
        self.refresh_btn.setMaximumWidth(30)
        model_layout.addWidget(self.refresh_btn)
        
        self.select_in_maya_btn = QPushButton("選択")
        self.select_in_maya_btn.setToolTip("Mayaビューポートでモデルを選択")
        model_layout.addWidget(self.select_in_maya_btn)
        
        self.focus_btn = QPushButton("フォーカス")
        self.focus_btn.setToolTip("モデルにビューをフォーカス")
        model_layout.addWidget(self.focus_btn)
        
        model_group.setLayout(model_layout)
        top_layout.addWidget(model_group)
        
        # モデル情報表示
        info_group = QGroupBox("モデル情報")
        info_layout = QGridLayout()
        info_layout.setSpacing(5)
        
        # 名前表示
        self.name_label = QLabel("名前: -")
        info_layout.addWidget(self.name_label, 0, 0, 1, 2)
        
        # 統計情報
        self.vertex_label = QLabel("頂点数: -")
        self.material_label = QLabel("マテリアル: -")
        self.bone_label = QLabel("ボーン: -")
        self.morph_label = QLabel("モーフ: -")
        
        info_layout.addWidget(self.vertex_label, 1, 0)
        info_layout.addWidget(self.material_label, 1, 1)
        info_layout.addWidget(self.bone_label, 2, 0)
        info_layout.addWidget(self.morph_label, 2, 1)
        
        info_group.setLayout(info_layout)
        top_layout.addWidget(info_group)
        
        # 右側のスペース
        top_layout.addStretch()
        
        main_layout.addLayout(top_layout)
    
    def connect_signals(self):
        """シグナルを接続"""
        # ApplicationStateのシグナル
        self.app_state.current_model_changed.connect(self.on_current_model_changed)
        self.app_state.model_list_updated.connect(self.on_model_list_updated)
        
        # UIのシグナル
        self.model_combo.currentTextChanged.connect(self.on_combo_selection_changed)
        self.refresh_btn.clicked.connect(self.refresh_model_list)
        self.select_in_maya_btn.clicked.connect(self.select_model_in_maya)
        self.focus_btn.clicked.connect(self.focus_on_model)
    
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
            self.set_action_buttons_enabled(False)
        else:
            for model in models:
                info = self.app_state.get_model_info(model)
                display_name = info['display_name'] if info else model
                self.model_combo.addItem(f"{display_name} ({model})", userData=model)
            
            # 現在のモデルを選択
            if current_model in models:
                index = models.index(current_model)
                self.model_combo.setCurrentIndex(index)
            
            self.set_action_buttons_enabled(True)
        
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
        
        # モデル情報を更新
        self.update_model_info()
    
    def update_model_info(self):
        """モデル情報表示を更新"""
        info = self.app_state.get_model_info()
        
        if info:
            # 名前表示
            name_parts = []
            if info['name_jp']:
                name_parts.append(info['name_jp'])
            if info['name_en']:
                name_parts.append(f"({info['name_en']})")
            
            name_text = " ".join(name_parts) if name_parts else info['display_name']
            self.name_label.setText(f"名前: {name_text}")
            
            # 統計情報
            self.vertex_label.setText(f"頂点数: {info['vertex_count']:,}")
            self.material_label.setText(f"マテリアル: {info['material_count']}")
            self.bone_label.setText(f"ボーン: {info['bone_count']}")
            self.morph_label.setText(f"モーフ: {info['morph_count']}")
        else:
            # 情報をクリア
            self.name_label.setText("名前: -")
            self.vertex_label.setText("頂点数: -")
            self.material_label.setText("マテリアル: -")
            self.bone_label.setText("ボーン: -")
            self.morph_label.setText("モーフ: -")
    
    def select_model_in_maya(self):
        """Mayaビューポートでモデルを選択"""
        model_root = self.app_state.current_model_root
        if model_root and cmds.objExists(model_root):
            cmds.select(model_root, replace=True)
            logger.info(f"Selected model in Maya: {model_root}")
            self.app_state.emit_status(f"モデルを選択しました: {model_root}")
    
    def focus_on_model(self):
        """モデルにビューをフォーカス"""
        model_root = self.app_state.current_model_root
        if model_root and cmds.objExists(model_root):
            cmds.select(model_root, replace=True)
            cmds.viewFit()
            logger.info(f"Focused on model: {model_root}")
            self.app_state.emit_status(f"モデルにフォーカスしました: {model_root}")
    
    def set_action_buttons_enabled(self, enabled):
        """アクションボタンの有効/無効を設定"""
        self.select_in_maya_btn.setEnabled(enabled)
        self.focus_btn.setEnabled(enabled)