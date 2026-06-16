import os
import shutil
import tempfile
import uuid

from maya import cmds
from mmd_tools.core.settings import settings

from .test_base import TestBase

try:
    # import maya.standalone

    # Mayaのスタンドアロンモードを初期化
    # maya.standalone.initialize(name="python")
    # MayaのコマンドとMELをインポート
    from maya import cmds

    MAYA_AVAILABLE = True
except ImportError:
    MAYA_AVAILABLE = False


class Settings:
    """
    Settings for the Maya test environment.
    """

    temp_dir = os.path.join(tempfile.gettempdir(), "maya_mmd_tools", str(uuid.uuid4()))
    delete_files = True  # Set to False to keep temporary files after tests
    buffer_output = True
    file_new = True  # Whether to create a new file before each test


class MayaTestBase(TestBase):
    """
    Base class for Maya integration tests.
    Handles setting up and tearing down the Maya scene.
    """

    plugins_loaded = []  # List to keep track of loaded plugins
    files_created = []  # List to keep track of created files

    @classmethod
    def setUpClass(cls):
        """
        テストケースクラスの初期化処理
        """
        super(MayaTestBase, cls).setUpClass()
        if not MAYA_AVAILABLE:
            raise RuntimeError("Maya is not available. Tests cannot run without Maya.")

        settings.set("logging.level", "WARNING")

    @classmethod
    def tearDownClass(cls):
        """テストケースクラスの終了処理"""
        # プラグインのアンロード
        cls.unload_plugins()
        cls.delete_temp_files()

        super().tearDownClass()

    def setUp(self):
        """
        Set up a clean Maya scene before each test.
        """
        super(MayaTestBase, self).setUp()
        # 新しいシーンの作成
        cmds.file(new=True, force=True)

    def tearDown(self):
        """
        Clean up after each test.
        """
        super(MayaTestBase, self).tearDown()
        # The scene is cleared in setUp of the next test,
        # so no specific teardown is needed here for now.

        cmds.file(f=True, new=True)

    @classmethod
    def load_plugin(cls, plugin):
        """指定されたプラグインをロードし、TestCase終了時にアンロードするために保存します。

        @param plugin: プラグイン名。
        """
        cmds.loadPlugin(plugin, qt=True)
        cls.plugins_loaded.append(plugin)

    @classmethod
    def unload_plugins(cls):
        # このテストケースでロードされたプラグインをアンロード
        for plugin in cls.plugins_loaded:
            cmds.unloadPlugin(plugin)
        cls.plugins_loaded = []

    @classmethod
    def delete_temp_files(cls):
        """キャッシュ内の一時ファイルを削除し、キャッシュをクリアします。"""
        # デバッグ目的で一時ファイルを保持したくない場合、このTestCaseのすべてのテストが
        # 実行された後にファイルを削除します
        if Settings.delete_files:
            for f in cls.files_created:
                if os.path.exists(f):
                    os.remove(f)
            cls.files_created = []
            if os.path.exists(Settings.temp_dir):
                shutil.rmtree(Settings.temp_dir)

    @classmethod
    def get_temp_filename(cls, file_name):
        """テストディレクトリ内の一意のファイルパス名を取得します。

        ファイルは作成されません。作成は呼び出し元の責任です。このファイルはテスト終了時に削除されます。
        @param file_name: 部分的なパス例：'directory/somefile.txt'
        @return 一時ファイルへのフルパス。
        """
        temp_dir = Settings.temp_dir
        if not os.path.exists(temp_dir):
            os.makedirs(temp_dir)
        base_name, ext = os.path.splitext(file_name)
        path = f"{temp_dir}/{base_name}{ext}"
        count = 0
        while os.path.exists(path):
            # If the file already exists, add an incrememted number
            count += 1
            path = f"{temp_dir}/{base_name}{count}{ext}"
        cls.files_created.append(path)
        return path

    def assertListAlmostEqual(self, first, second, places=7, msg=None, delta=None):
        """浮動小数点値のリストがほぼ等しいことをアサートします。

        unittestにはassertAlmostEqualとassertListEqualはありますが、assertListAlmostEqualはありません。
        """
        self.assertEqual(len(first), len(second), msg)
        for a, b in zip(first, second):
            self.assertAlmostEqual(a, b, places, msg, delta)
