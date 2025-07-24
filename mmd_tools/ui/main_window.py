import logging
import os
from maya import cmds
import maya.OpenMayaUI as mui
from .qt_compat import QMainWindow, QTabWidget, QDockWidget, Qt, QSettings, wrapInstance, QWidget
from ..core.log_handlers import QtLogHandler
from .components.log_viewer import LogViewer
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

        self.tab_widget = QTabWidget()
        self.tab_widget.setObjectName("mainTabWidget")  # Add objectName
        self.setCentralWidget(self.tab_widget)

        self.log_viewer = LogViewer()
        self.log_viewer.setObjectName("logViewer")  # Add objectName
        log_dock_widget = QDockWidget("Log", self)
        log_dock_widget.setObjectName("logDockWidget")  # Add objectName
        log_dock_widget.setWidget(self.log_viewer)  # ログビューアを実際にドックウィジェットに追加
        self.addDockWidget(Qt.BottomDockWidgetArea, log_dock_widget)

        self.load_stylesheet()
        self.setup_logging()
        self.setup_tabs()
        self.restore_settings()
        
        # 最小サイズを設定
        self.setMinimumWidth(600)
        self.setMinimumHeight(500)

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
        self.import_export_presenter = ImportExportPresenter(import_export_tab)
        self.tab_widget.addTab(import_export_tab, "File I/O")

        # Info Tab
        info_tab = InfoTab()
        self.info_presenter = InfoPresenter(info_tab)
        self.tab_widget.addTab(info_tab, "Info")

        # Material Tab
        material_tab = MaterialTab()
        self.material_presenter = MaterialPresenter(material_tab)
        self.tab_widget.addTab(material_tab, "Material")

        # Bone Tab
        bone_tab = BoneTab()
        self.bone_presenter = BonePresenter(bone_tab)
        self.tab_widget.addTab(bone_tab, "Bone")

        # Morph Tab
        morph_tab = MorphTab()
        self.morph_presenter = MorphPresenter(morph_tab)
        self.tab_widget.addTab(morph_tab, "Morph")

        # Display Pane Tab
        display_pane_tab = DisplayPaneTab()
        self.display_pane_presenter = DisplayPanePresenter(display_pane_tab)
        self.tab_widget.addTab(display_pane_tab, "Display Pane")

        # Physics Tab
        physics_tab = PhysicsTab()
        self.physics_presenter = PhysicsPresenter(physics_tab)
        self.tab_widget.addTab(physics_tab, "Physics")

        # Settings Tab
        settings_tab = SettingsTab()
        self.settings_presenter = SettingsPresenter(settings_tab)
        self.tab_widget.addTab(settings_tab, "Settings")
        
        # logger参照のためにグローバルスコープで取得
        global logger
        logger = get_logger(__name__)

        # Connect presenters
        self.import_export_presenter.model_imported.connect(
            self.info_presenter.on_model_imported
        )
        self.import_export_presenter.model_imported.connect(
            self.material_presenter.on_model_imported
        )
        self.import_export_presenter.model_imported.connect(
            self.bone_presenter.on_model_imported
        )
        self.import_export_presenter.model_imported.connect(
            self.morph_presenter.on_model_imported
        )
        self.import_export_presenter.model_imported.connect(
            self.display_pane_presenter.on_model_imported
        )
        self.import_export_presenter.model_imported.connect(
            self.physics_presenter.on_model_imported
        )
