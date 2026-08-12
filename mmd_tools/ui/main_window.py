from maya import cmds
import maya.OpenMayaUI as mui
from .qt_compat import (
    QMainWindow,
    QTabWidget,
    Qt,
    QSettings,
    wrapInstance,
    QWidget,
    QVBoxLayout,
    QStatusBar,
    QProgressBar,
    QLabel,
)
from .components.header_widget import HeaderWidget
from .application_state import ApplicationState
from ..core import settings_keys as setting_keys
from ..core.logger import get_logger, install_maya_script_editor_handler
from ..services.settings_service import SettingsService
from .tabs.import_export_tab import ImportExportTab
from .presenters.import_export_presenter import ImportExportPresenter
from .tabs.export_tab import ExportTab
from .presenters.export_presenter import ExportPresenter
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

logger = get_logger(__name__)


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
        self.settings_service = SettingsService()
        self.authoring_composition = self._create_authoring_composition()

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

        self.setup_logging()
        self.setup_tabs()
        self.tab_widget.currentChanged.connect(self._on_main_tab_changed)
        self.restore_settings()

        # ApplicationStateのシグナルを接続
        self.app_state.status_message.connect(self.show_status_message)
        self.app_state.progress_updated.connect(self.update_progress)
        self.app_state.current_model_changed.connect(self.update_window_title)

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
                label="MMD Tools",
                tabToControl=["AttributeEditor", -1],  # アトリビュートエディタの右にタブとして追加
                initialWidth=800,
                initialHeight=600,
                widthProperty="preferred",
                retain=False,  # Maya終了時に保持しない
                floating=False,
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
        self.bone_presenter.disconnect_signals()
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

    def update_window_title(self, model_root=None):
        """ウィンドウタイトルを更新（現在のモデル名を含める）"""
        base_title = "MMD Tools"

        if model_root:
            # モデル情報を取得
            info = self.app_state.get_model_info(model_root)
            if info:
                # 表示名を優先、なければモデルルート名を使用
                model_name = info.get("display_name", model_root)
                self.setWindowTitle(f"{base_title} - {model_name}")
            else:
                self.setWindowTitle(f"{base_title} - {model_root}")
        else:
            self.setWindowTitle(base_title)

    def setup_logging(self):
        install_maya_script_editor_handler()
        get_logger(__name__).info("MMD Tools UI initialized.")

    @staticmethod
    def _create_authoring_composition():
        """Build authoring services without making UI startup depend on them."""
        try:
            from ..adapters.maya_authoring_factory import build_maya_authoring_composition

            return build_maya_authoring_composition(cmds)
        except Exception:
            logger.error(
                "Maya model authoring services are unavailable; authoring controls are disabled.",
                exc_info=True,
            )
            return None

    def _authoring_presenter_kwargs(self):
        """Return shared authoring dependencies for Material/Bone/Morph tabs."""
        composition = getattr(self, "authoring_composition", None)
        if composition is None:
            return {"authoring_coordinator": None}
        return {
            "maya_adapter": composition.cmds_adapter,
            "authoring_coordinator": composition.coordinator,
        }

    def _create_model_action(self):
        """Return only the Create Model action from the optional composition."""
        composition = getattr(self, "authoring_composition", None)
        return composition.create_model_action if composition is not None else None

    def setup_tabs(self):
        # UITranslatorを取得
        from .translations import UITranslator

        translator = UITranslator.instance()

        # 言語設定を読み込み
        current_language = self.settings_service.get(setting_keys.UI_GENERAL_LANGUAGE, "ja")
        translator.set_language(current_language)

        # Import Tab
        import_export_tab = ImportExportTab()
        self.import_export_tab = import_export_tab
        self.import_export_presenter = ImportExportPresenter(
            import_export_tab,
            self.app_state,
            create_model_action=self._create_model_action(),
        )
        self.tab_widget.addTab(import_export_tab, translator.translate("file_io", "tabs"))

        # Export workflow tab: all public export decisions and the Validation Console live here.
        export_tab = ExportTab(settings_service=self.settings_service)
        self.export_tab = export_tab
        self.export_presenter = ExportPresenter(
            export_tab,
            self.app_state,
            workflow_service=None,
        )
        self.tab_widget.addTab(export_tab, translator.translate("export_workflow", "tabs"))

        # Info Tab
        info_tab = InfoTab()
        self.info_presenter = InfoPresenter(info_tab, self.app_state)
        self.tab_widget.addTab(info_tab, translator.translate("info", "tabs"))

        # Material Tab
        material_tab = MaterialTab()
        authoring_kwargs = self._authoring_presenter_kwargs()
        self.material_presenter = MaterialPresenter(
            material_tab,
            self.app_state,
            **authoring_kwargs,
        )
        self.tab_widget.addTab(material_tab, translator.translate("material", "tabs"))

        # Bone Tab
        bone_tab = BoneTab()
        self.bone_presenter = BonePresenter(
            bone_tab,
            self.app_state,
            **authoring_kwargs,
        )
        self.tab_widget.addTab(bone_tab, translator.translate("bone", "tabs"))

        # Morph Tab
        morph_tab = MorphTab()
        composition = getattr(self, "authoring_composition", None)
        self.morph_presenter = MorphPresenter(
            morph_tab,
            self.app_state,
            material_morph_work=(
                getattr(composition, "material_morph_work", None)
                if composition is not None
                else None
            ),
            **authoring_kwargs,
        )
        self.tab_widget.addTab(morph_tab, translator.translate("morph", "tabs"))
        self.morph_tab = morph_tab

        # Display Pane Tab (PMX display-frame editor; independent of Animator Toolset)
        display_pane_tab = DisplayPaneTab()
        self.display_pane_presenter = DisplayPanePresenter(display_pane_tab, self.app_state)
        self.tab_widget.addTab(display_pane_tab, translator.translate("display_pane", "tabs"))
        self.display_pane_tab = display_pane_tab

        # Physics Tab
        self._add_physics_tab()

        # Settings Tab
        settings_tab = SettingsTab()
        self.settings_presenter = SettingsPresenter(settings_tab, self.app_state)
        self.tab_widget.addTab(settings_tab, translator.translate("settings", "tabs"))

        # タブリストを保存（retranslate用）
        self.tabs = [
            import_export_tab,
            export_tab,
            info_tab,
            material_tab,
            bone_tab,
            morph_tab,
            display_pane_tab,
            self.physics_tab,
            settings_tab,
        ]

    def _add_physics_tab(self):
        """Create the Physics tab and add it to the tab widget."""
        from .translations import UITranslator

        translator = UITranslator.instance()
        physics_tab = PhysicsTab()
        self.physics_presenter = PhysicsPresenter(physics_tab, self.app_state)
        self.tab_widget.addTab(physics_tab, translator.translate("physics", "tabs"))
        self.physics_tab = physics_tab
        return physics_tab

    def _on_main_tab_changed(self, index):
        """Refresh data-backed tabs when they become active."""
        active_tab = self.tab_widget.widget(index)
        morph_tab = getattr(self, "morph_tab", None)
        morph_presenter = getattr(self, "morph_presenter", None)
        if morph_tab is not None and morph_presenter is not None and active_tab is morph_tab:
            morph_presenter.ensure_morphs_loaded()

        display_pane_tab = getattr(self, "display_pane_tab", None)
        display_pane_presenter = getattr(self, "display_pane_presenter", None)
        if (
            display_pane_tab is not None
            and display_pane_presenter is not None
            and active_tab is display_pane_tab
        ):
            display_pane_presenter.refresh()

        physics_tab = getattr(self, "physics_tab", None)
        presenter = getattr(self, "physics_presenter", None)
        if physics_tab is not None and presenter is not None and active_tab is physics_tab:
            presenter.refresh_physics()

    def refresh_development_mode_visibility(self):
        """Development Mode 依存の UI 表示を現在のウィンドウへ再適用する。"""
        if hasattr(self, "import_export_tab"):
            self.import_export_tab._apply_dev_mode_visibility()

        from mmd_tools.plugin_main import install_mmd_menu

        install_mmd_menu()

    def retranslate_all_tabs(self):
        """すべてのタブのUIテキストを再翻訳"""
        from .translations import UITranslator

        translator = UITranslator.instance()

        # タブのタイトルを実際に追加されたタブに合わせて再設定
        tab_entries = [
            (self.import_export_tab, "file_io"),
            (self.export_tab, "export_workflow"),
            (self.info_presenter.view, "info"),
            (self.material_presenter.view, "material"),
            (self.bone_presenter.view, "bone"),
            (self.morph_tab, "morph"),
            (self.display_pane_tab, "display_pane"),
            (self.physics_tab, "physics"),
            (self.settings_presenter.view, "settings"),
        ]
        for tab, key in tab_entries:
            index = self.tab_widget.indexOf(tab)
            if index >= 0:
                self.tab_widget.setTabText(index, translator.translate(key, "tabs"))

        # 各タブのretranslateUiメソッドを呼び出し
        for tab in self.tabs:
            if hasattr(tab, "retranslateUi"):
                tab.retranslateUi()

        if hasattr(self.header_widget, "retranslateUi"):
            self.header_widget.retranslateUi()

        self.retranslateUi()

    def retranslateUi(self):
        """メインウィンドウのUIテキストを再翻訳"""
        # 現在はウィンドウタイトルは固定なので特に処理なし
        # 将来的にメニューバーなどを追加した場合はここで翻訳
        pass
