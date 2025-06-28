import maya.cmds as cmds
import maya.mel as mel
import os

# PySide6をデフォルトとし、PySide2にフォールバック
try:
    from PySide6 import QtWidgets, QtCore, QtGui
    from shiboken6 import wrapInstance
except ImportError:
    from PySide2 import QtWidgets, QtCore, QtGui
    from shiboken2 import wrapInstance

import maya.OpenMayaUI as mui

# Import MMD Importer and Exporter modules
from mmd_tools.io import mmd_importer
from mmd_tools.io import pmx_exporter

class MMDToolsUI(QtWidgets.QDialog):
    """
    MMD Toolsプラグインのユーザーインターフェースを管理するクラス。
    PySide2を使用してUIを構築し、ファイル選択ダイアログ、インポート/エクスポートオプション、進捗表示などを提供する。
    """
    def __init__(self, parent=None):
        """
        MMDToolsUIクラスのコンストラクタ。
        UIの初期設定とウィジェットの作成を行う。
        """
        # QDialogのコンストラクタを呼び出し、Mayaのメインウィンドウを親として設定
        super(MMDToolsUI, self).__init__(parent)

        # UIウィンドウの基本設定
        self.setWindowTitle("MMD Tools")
        self.setMinimumWidth(400)
        self.setMinimumHeight(500)

        # UI要素の参照を保持する変数
        self.progress_bar = None
        self.progress_text = None
        self.log_field = None

        # UIの構築
        self._setup_ui()

    def _setup_ui(self):
        """
        UIのウィジェットとレイアウトを構築するプライベートメソッド。
        """
        # メインレイアウト (垂直方向)
        main_layout = QtWidgets.QVBoxLayout(self)

        # --- インポートセクション ---
        import_group = QtWidgets.QGroupBox("Import MMD Model/Motion")
        import_layout = QtWidgets.QVBoxLayout(import_group)
        import_button = QtWidgets.QPushButton("Import MMD Model/Motion")
        import_button.setFixedHeight(30)
        import_button.clicked.connect(self.show_import_dialog)
        import_layout.addWidget(import_button)
        main_layout.addWidget(import_group)

        # --- エクスポートセクション ---
        export_group = QtWidgets.QGroupBox("Export MMD Model")
        export_layout = QtWidgets.QVBoxLayout(export_group)
        export_button = QtWidgets.QPushButton("Export MMD Model (PMX)")
        export_button.setFixedHeight(30)
        export_button.clicked.connect(self.show_export_dialog)
        export_layout.addWidget(export_button)
        main_layout.addWidget(export_group)

        # --- 進捗表示セクション ---
        progress_group = QtWidgets.QGroupBox("Progress")
        progress_layout = QtWidgets.QVBoxLayout(progress_group)
        self.progress_text = QtWidgets.QLabel("Ready")
        self.progress_text.setAlignment(QtCore.Qt.AlignLeft)
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100) # 0から100の範囲で設定
        progress_layout.addWidget(self.progress_text)
        progress_layout.addWidget(self.progress_bar)
        main_layout.addWidget(progress_group)

        # --- ログ表示セクション ---
        log_group = QtWidgets.QGroupBox("Log")
        log_layout = QtWidgets.QVBoxLayout(log_group)
        self.log_field = QtWidgets.QTextEdit()
        self.log_field.setReadOnly(True) # 読み取り専用に設定
        self.log_field.setLineWrapMode(QtWidgets.QTextEdit.WidgetWidth) # ウィジェットの幅に合わせて折り返し
        self.log_field.setMinimumHeight(150)
        log_layout.addWidget(self.log_field)
        main_layout.addWidget(log_group)

        # レイアウトを適用
        self.setLayout(main_layout)

        self.log_message("MMD Tools UI initialized.", level="info")

    def create_main_window(self):
        """
        MMD ToolsのメインUIウィンドウを表示する。
        PySide2のQDialogとして表示される。
        """
        # Mayaのメインウィンドウを取得し、PySideの親として設定
        # これにより、PySideのウィンドウがMayaのウィンドウにペアレントされ、Mayaの終了時に適切にクリーンアップされる
        maya_main_window_ptr = mui.MQtUtil.mainWindow()
        maya_main_window = wrapInstance(int(maya_main_window_ptr), QtWidgets.QWidget)
        
        # 既存のウィンドウがあれば閉じる
        # MayaのUIコマンドではなく、PySideのインスタンスを直接操作
        for widget in QtWidgets.QApplication.topLevelWidgets():
            if isinstance(widget, MMDToolsUI) and widget.windowTitle() == self.windowTitle():
                widget.close()

        # ウィンドウを表示
        self.setParent(maya_main_window) # 親を設定
        self.setWindowFlags(QtCore.Qt.Window) # 独立したウィンドウとして表示
        self.show()

    def show_import_dialog(self):
        """
        MMDモデル/モーションファイルのインポートダイアログを表示する。
        ユーザーにファイルを選択させ、インポートオプションを設定させる。
        """
        # ファイル選択ダイアログを表示 (Mayaのcmds.fileDialog2を使用)
        # fileFilter: 許可するファイルの種類と拡張子
        # dialogStyle=2: OSネイティブのダイアログスタイル
        # caption: ダイアログのタイトル
        # fileMode=1: 既存の単一ファイルを選択
        file_paths = cmds.fileDialog2(fileFilter="MMD Files (*.pmx *.pmd *.vmd);;PMX Models (*.pmx);;PMD Models (*.pmd);;VMD Motions (*.vmd)",
                                      dialogStyle=2,
                                      caption="Import MMD Model/Motion",
                                      fileMode=1)

        if file_paths:
            selected_file = file_paths[0] # 選択されたファイルのパスを取得
            self.log_message(f"Selected file for import: {os.path.basename(selected_file)}", level="info")
            self.update_progress(0, "Importing...")

            try:
                # 現時点ではオプションなしでインポートを実行
                mmd_importer.import_mmd_file(selected_file)
                self.update_progress(100, "Import Complete!")
                self.log_message(f"Successfully imported {os.path.basename(selected_file)}", level="info")
                QtWidgets.QMessageBox.information(self, "Import Complete", f"Successfully imported {os.path.basename(selected_file)}")
            except Exception as e:
                self.update_progress(0, "Import Failed!")
                self.log_message(f"Error importing {os.path.basename(selected_file)}: {e}", level="error")
                QtWidgets.QMessageBox.critical(self, "Import Failed", f"Import failed: {e}")
                cmds.error(f"Import failed: {e}") # Mayaのエラーログにも出力

    def show_export_dialog(self):
        """
        MayaシーンからMMDモデル/モーションファイルをエクスポートするダイアログを表示する。
        ユーザーに保存先を選択させ、エクスポートオプションを設定させる。
        """
        # ファイル保存ダイアログを表示 (Mayaのcmds.fileDialog2を使用)
        # fileFilter: 許可するファイルの種類と拡張子
        # dialogStyle=2: OSネイティブのダイアログスタイル
        # caption: ダイアログのタイトル
        # fileMode=0: ファイルを保存
        file_path = cmds.fileDialog2(fileFilter="PMX Files (*.pmx)",
                                     dialogStyle=2,
                                     caption="Export MMD Model (PMX)",
                                     fileMode=0)

        if file_path:
            save_path = file_path[0] # 保存先パスを取得
            self.log_message(f"Selected path for export: {os.path.basename(save_path)}", level="info")
            self.update_progress(0, "Exporting...")

            # エクスポート対象のオブジェクトを取得
            # 現時点では、シーン内の選択されたトランスフォームノードを対象とする。
            selected_objects = cmds.ls(selection=True, type='transform')

            if selected_objects:
                try:
                    # 現時点では、選択された最初のオブジェクトをルートとしてエクスポート
                    pmx_exporter.export_pmx_file(save_path, selected_objects[0])
                    self.update_progress(100, "Export Complete!")
                    self.log_message(f"Successfully exported {os.path.basename(save_path)}", level="info")
                    QtWidgets.QMessageBox.information(self, "Export Complete", f"Successfully exported {os.path.basename(save_path)}")
                except Exception as e:
                    self.update_progress(0, "Export Failed!")
                    self.log_message(f"Error exporting {os.path.basename(save_path)}: {e}", level="error")
                    QtWidgets.QMessageBox.critical(self, "Export Failed", f"Export failed: {e}")
                    cmds.error(f"Export failed: {e}") # Mayaのエラーログにも出力
            else:
                self.update_progress(0, "Export Cancelled!")
                self.log_message("No object selected for export. Please select an object.", level="warning")
                QtWidgets.QMessageBox.warning(self, "Export Cancelled", "No object selected for export. Please select an object.")
                cmds.warning("No object selected for export. Please select an object.")

    def update_progress(self, value, message=""):
        """
        進捗バーを更新し、進捗メッセージを表示する。
        PySide2のQProgressBarとQLabelを操作する。

        Args:
            value (int): 進捗の割合 (0から100)。
            message (str): 表示する進捗メッセージ。
        """
        if self.progress_bar:
            self.progress_bar.setValue(value)
        if self.progress_text:
            self.progress_text.setText(message)
        # PySideのUIは自動的に更新されるため、cmds.refresh()は不要だが、
        # 念のためQApplicationのイベント処理を強制することも可能 (通常は不要)
        # QtWidgets.QApplication.processEvents()

    def log_message(self, message, level="info"):
        """
        UI内のログ表示エリアにメッセージを出力する。
        PySide2のQTextEditを操作する。

        Args:
            message (str): 表示するログメッセージ。
            level (str): ログのレベル（例: "info", "warning", "error"）。
        """
        if self.log_field:
            timestamp = QtCore.QDateTime.currentDateTime().toString("HH:mm:ss")
            prefix = ""
            color = "black" # デフォルトの色
            if level == "warning":
                prefix = "[WARNING] "
                color = "#FFA500" # オレンジ色 (背景でも見やすいように)
            elif level == "error":
                prefix = "[ERROR] "
                color = "#FF4500" # 赤色 (背景でも見やすいように)
            elif level == "info":
                prefix = "[INFO] "
                color = "#1E90FF" # 青色 (背景でも見やすいように)

            # HTML形式でメッセージを追加し、色を適用
            formatted_message = f"<span style=\"color:{color};\">[{timestamp}] {prefix}{message}</span>"
            self.log_field.append(formatted_message) # appendは自動的に改行を追加し、スクロールする

        # Mayaのスクリプトエディタにも出力
        if level == "error":
            cmds.error(message)
        elif level == "warning":
            cmds.warning(message)

    def close_window(self):
        """
        UIウィンドウを閉じる。
        PySide2のQDialogを閉じる。
        """
        self.close() # QDialogのcloseメソッドを呼び出す
        self.log_message("MMD Tools UI closed.", level="info")

# MMD Tools UIのインスタンスをグローバルに保持
# これにより、メニューコマンドから同じUIインスタンスのメソッドを呼び出せる
_mmd_tools_ui_instance = None

def create_mmd_tools_menu():
    """
    MayaのメインメニューバーにMMD Toolsメニューを作成する。
    この関数はuserSetup.pyから呼び出されることを想定している。
    """
    global _mmd_tools_ui_instance

    # 既存のメニューがあれば削除
    if cmds.menu('MMDToolsMenu', exists=True):
        cmds.deleteUI('MMDToolsMenu')

    # メインウィンドウの取得
    gMainWindow = mel.eval('$temp1 = $gMainWindow')

    # MMD Toolsメニューの作成
    cmds.menu('MMDToolsMenu', parent=gMainWindow, tearOff=True, label='MMD Tools')

    # UIインスタンスがなければ作成
    if _mmd_tools_ui_instance is None:
        # Mayaのメインウィンドウを親としてPySideのUIを作成
        maya_main_window_ptr = mui.MQtUtil.mainWindow()
        maya_main_window = wrapInstance(int(maya_main_window_ptr), QtWidgets.QWidget)
        _mmd_tools_ui_instance = MMDToolsUI(parent=maya_main_window)

    # インポートメニューアイテム
    # コマンドはUIのcreate_main_windowメソッドを呼び出す
    cmds.menuItem('MMDTools_IO', parent='MMDToolsMenu', label='Import/Export', command=lambda *args: _mmd_tools_ui_instance.create_main_window())
