import logging
import os
from maya import cmds
import maya.OpenMayaUI as mui
from .qt_compat import (
    QMainWindow, QTabWidget, QDockWidget, Qt, QSettings, 
    wrapInstance, QWidget, QVBoxLayout, QStatusBar, QProgressBar, QLabel
)
from ..core.log_handlers import QtLogHandler
from .components.log_viewer import LogViewer
from .components.header_widget import HeaderWidget
from .application_state import ApplicationState
from ..core.logger import get_logger
from .tabs.import_export_tab import ImportExportTab
from .presenters.import_export_presenter import ImportExportPresenter
from .tabs.info_tab import InfoTab
from .presenters.info_presenter import InfoPresenter
from .tabs.material_tab import MaterialTab
from .presenters.material_presenter import MaterialPresenter
from .tabs.bone_tab import BoneTab
from .presenters.bone_presenter import BonePresenter
from .tabs.morph_tab import MorphTab
from .presenters.morph_presenter import MorphPresenter
from .tabs.display_pane_tab import DisplayPaneTab
from .presenters.display_pane_presenter import DisplayPanePresenter
from .tabs.physics_tab import PhysicsTab
from .presenters.physics_presenter import PhysicsPresenter
from .tabs.settings_tab import SettingsTab
from .presenters.settings_presenter import SettingsPresenter


class MainWindow(QMainWindow):
    """Mayaと統合されたメインウィンドウ"""
    
    WINDOW_NAME = "MMDToolsMainWindow"
    WORKSPACE_CONTROL_NAME = "MMDToolsWorkspaceControl"
    
    def __init__(self, parent=None):
        # Mayaのメインウィンドウを親に設定
        if parent is None:
            parent = self.get_maya_main_window()
        
        super().__init__(parent)
        self.setWindowTitle("MMD Tools")
        self.setObjectName(self.WINDOW_NAME)
        
        # アプリケーション状態管理
        self.app_state = ApplicationState()
        
        # 中央ウィジェットの設定
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # メインレイアウト
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # ヘッダーウィジェット
        self.header_widget = HeaderWidget(self.app_state)
        main_layout.addWidget(self.header_widget)
        
        # タブウィジェット
        self.tab_widget = QTabWidget()
        self.tab_widget.setObjectName("mainTabWidget")
        main_layout.addWidget(self.tab_widget)
        
        # ステータスバー
        self.setup_status_bar()
        
        # ログビューア（ドッキング可能）
        self.log_viewer = LogViewer()
        self.log_viewer.setObjectName("logViewer")
        log_dock_widget = QDockWidget("Log", self)
        log_dock_widget.setObjectName("logDockWidget")
        log_dock_widget.setWidget(self.log_viewer)
        self.addDockWidget(Qt.BottomDockWidgetArea, log_dock_widget)

        self.load_stylesheet()
        self.setup_logging()
        self.setup_tabs()
        self.restore_settings()
        
        # ApplicationStateのシグナルを接続
        self.app_state.status_message.connect(self.show_status_message)
        self.app_state.progress_updated.connect(self.update_progress)
        
        # 最小サイズを設定
        self.setMinimumWidth(800)
        self.setMinimumHeight(600)
        
        # 初期化完了後、モデルリストを更新
        self.app_state.refresh_model_list()

    @staticmethod
    def get_maya_main_window():
        """Mayaのメインウィンドウを取得"""
        main_window_ptr = mui.MQtUtil.mainWindow()
        return wrapInstance(int(main_window_ptr), QWidget)
    
    def show_window(self, dockable=False):
        """メインウィンドウを表示"""
        if dockable:
            # workspaceControlを使用してMayaパネルとして表示
            self.setWindowFlags(Qt.Widget)
            self.setAttribute(Qt.WA_DeleteOnClose, False)  # クローズ時に削除しない
            
            # workspaceControlの作成
            workspace_control_name = self.WORKSPACE_CONTROL_NAME
            if cmds.workspaceControl(workspace_control_name, exists=True):
                cmds.workspaceControl(workspace_control_name, e=True, close=True)
                cmds.deleteUI(workspace_control_name)
            
            # まずウィンドウを表示
            self.show()
            
            # workspaceControlを作成してウィンドウをホスト
            cmds.workspaceControl(
                workspace_control_name,
                label='MMD Tools',
                tabToControl=['AttributeEditor', -1],  # アトリビュートエディタの右にタブとして追加
                initialWidth=800,
                initialHeight=600,
                widthProperty='preferred',
                retain=False,  # Maya終了時に保持しない
                floating=False
            )
            
            # ウィンドウをworkspaceControlにアタッチ
            cmds.control(self.WINDOW_NAME, e=True, parent=workspace_control_name)
        else:
            # 通常のフローティングウィンドウとして表示
            self.setWindowFlags(Qt.Window)
            self.show()
    
    def create_main_window(self, dockable=False):
        """互換性のためのメソッド（show_windowを呼び出す）"""
        self.show_window(dockable=dockable)

    def closeEvent(self, event):
        self.save_settings()
        super().closeEvent(event)

    def save_settings(self):
        settings = QSettings("yohawing", "maya_mmd_tools")
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("windowState", self.saveState())

    def restore_settings(self):
        settings = QSettings("yohawing", "maya_mmd_tools")
        geometry = settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        state = settings.value("windowState")
        if state:
            self.restoreState(state)

    def setup_status_bar(self):
        """ステータスバーをセットアップ"""
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        
        # 永続的なウィジェット（右側）
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(200)
        self.progress_bar.setVisible(False)
        self.status_bar.addPermanentWidget(self.progress_bar)
        
        # バージョン情報
        from mmd_tools import __version__
        maya_version = cmds.about(version=True)
        version_label = QLabel(f"MMD Tools v{__version__} | Maya {maya_version}")
        self.status_bar.addPermanentWidget(version_label)
        
        # 初期メッセージ
        self.status_bar.showMessage("準備完了", 2000)
    
    def show_status_message(self, message):
        """ステータスメッセージを表示"""
        self.status_bar.showMessage(message, 5000)  # 5秒間表示
    
    def update_progress(self, value):
        """進捗状況を更新"""
        if value > 0 and value < 100:
            self.progress_bar.setVisible(True)
            self.progress_bar.setValue(value)
        else:
            self.progress_bar.setVisible(False)
            if value >= 100:
                self.progress_bar.setValue(100)
    
    def load_stylesheet(self):
        style_path = os.path.join(os.path.dirname(__file__), "stylesheet.qss")
        try:
            with open(style_path, "r") as f:
                self.setStyleSheet(f.read())
        except FileNotFoundError:
            logger.warning(f"Stylesheet not found at {style_path}")

    def setup_logging(self):
        logger = get_logger(__name__)
        handler = QtLogHandler()
        handler.message_written.connect(self.log_viewer.append)
        logger.add_handler(handler)
        logger.set_level(logging.INFO)
        logger.info("MMD Tools UI initialized.")

    def setup_tabs(self):
        # File I/O Tab
        import_export_tab = ImportExportTab()
        self.import_export_presenter = ImportExportPresenter(import_export_tab, self.app_state)
        self.tab_widget.addTab(import_export_tab, "File I/O")

        # Info Tab
        info_tab = InfoTab()
        self.info_presenter = InfoPresenter(info_tab, self.app_state)
        self.tab_widget.addTab(info_tab, "Info")

        # Material Tab
        material_tab = MaterialTab()
        self.material_presenter = MaterialPresenter(material_tab, self.app_state)
        self.tab_widget.addTab(material_tab, "Material")

        # Bone Tab
        bone_tab = BoneTab()
        self.bone_presenter = BonePresenter(bone_tab, self.app_state)
        self.tab_widget.addTab(bone_tab, "Bone")

        # Morph Tab
        morph_tab = MorphTab()
        self.morph_presenter = MorphPresenter(morph_tab, self.app_state)
        self.tab_widget.addTab(morph_tab, "Morph")

        # Display Pane Tab
        display_pane_tab = DisplayPaneTab()
        self.display_pane_presenter = DisplayPanePresenter(display_pane_tab, self.app_state)
        self.tab_widget.addTab(display_pane_tab, "Display Pane")

        # Physics Tab
        physics_tab = PhysicsTab()
        self.physics_presenter = PhysicsPresenter(physics_tab, self.app_state)
        self.tab_widget.addTab(physics_tab, "Physics")

        # Settings Tab
        settings_tab = SettingsTab()
        self.settings_presenter = SettingsPresenter(settings_tab, self.app_state)
        self.tab_widget.addTab(settings_tab, "Settings")
        
        # logger参照のためにグローバルスコープで取得
        global logger
        logger = get_logger(__name__)
