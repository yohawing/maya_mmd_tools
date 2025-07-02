"""
YWTA Tools Maya テスト実行スクリプト

このスクリプトは、Maya環境でテストを実行するためのエントリーポイントを提供します。
Maya内から実行するか、mayapy.exeを使用して実行します。
"""
import os
import sys
import unittest
import maya.cmds as cmds
from tests.common.maya_test_base import Settings

# The environment variable that signifies tests are being run with the custom TestResult class.
TESTING_VAR = "MMD_TOOLS_TEST"


def run_tests(test=None, test_suite=None):
    """指定されたパスにあるすべてのテストを実行します。

    @param test: 実行する特定のテストの名前（オプション）。
    @param test_suite: 実行するTestSuite（オプション）。省略された場合、TestSuiteが生成されます。
    """
    if test_suite is None:
        test_suite = get_tests(test)

    runner = unittest.TextTestRunner(verbosity=2, )
    runner.failfast = False
    runner.buffer = True
    runner.run(test_suite)


def get_tests(test=None, test_suite=None):
    """必要なすべてのテストを含むunittest.TestSuiteを取得します。
    testsディレクトリを使用します。

    @param test: 'test_mytest.SomeTestCase.test_function'のような特定のテストを見つけるためのテストパス（オプション）。
    @param test_suite: 発見されたテストを追加するunittest.TestSuite（オプション）。省略された場合、新しいTestSuiteが
    作成されます。
    @return: テストが追加されたTestSuite。
    """

    directories = [os.path.join(os.path.dirname(__file__), "./integration")]

    # Populate a TestSuite with all the tests
    if test_suite is None:
        test_suite = unittest.TestSuite()

    if test:
        # Find the specified test to run
        directories_added_to_path = [p for p in directories if add_to_path(p)]
        discovered_suite = unittest.TestLoader().loadTestsFromName(test)
        if discovered_suite.countTestCases():
            test_suite.addTests(discovered_suite)
    else:
        # Find all tests to run
        directories_added_to_path = []
        for p in directories:
            discovered_suite = unittest.TestLoader().discover(p)
            if discovered_suite.countTestCases():
                test_suite.addTests(discovered_suite)

    # Remove the added paths.
    for path in directories_added_to_path:
        sys.path.remove(path)

    return test_suite


def run_tests_from_commandline():
    """Mayaスタンドアロンモードでテストを実行します。
    """
    import maya.standalone

    maya.standalone.initialize()

    # Make sure all paths in PYTHONPATH are also in sys.path
    # When a maya module is loaded, the scripts folder is added to PYTHONPATH, but it doesn't seem
    # to be added to sys.path. So we are unable to import any of the python files that are in the
    # module/scripts folder. To workaround this, we simply add the paths to sys ourselves.
    realsyspath = [os.path.realpath(p) for p in sys.path]
    pythonpath = os.environ.get("PYTHONPATH", "")
    for p in pythonpath.split(os.pathsep):
        p = os.path.realpath(p)  # Make sure symbolic links are resolved
        if p not in realsyspath:
            sys.path.insert(0, p)

    # run_tests()

    # Starting Maya 2016, we have to call uninitialize
    if float(cmds.about(v=True)) >= 2016.0:
        maya.standalone.uninitialize()

def add_to_path(path):
    """指定されたパスをシステムパスに追加します。

    @param path: 追加するパス。
    @return パスが追加された場合はTrue。パスが存在しないか、すでにsys.pathにある場合はFalseを返します。
    """
    if os.path.exists(path) and path not in sys.path:
        sys.path.insert(0, path)
        return True
#     return False

# class TestResult(unittest.TextTestResult):
#     """各テスト間での新規ファイル作成やスクリプトエディタの出力抑制などを行うためにテスト結果をカスタマイズします。"""

#     def __init__(self, stream, descriptions, verbosity):
#         super(TestResult, self).__init__(stream, descriptions, verbosity)
#         self.successes = []

#     def startTestRun(self):
#         """テスト実行前に呼び出されます。"""
#         super(TestResult, self).startTestRun()
#         # カスタムランナーを通じてテストが実行されていることを指定する環境変数を作成します。
#         os.environ[TESTING_VAR] = "1"

#         ScriptEditorState.suppress_output()
#         if Settings.buffer_output:
#             # テスト実行中のログを無効にします。criticalを無効にすることで、
#             # critical以下のすべてのレベルのログも無効になります
#             logging.disable(logging.CRITICAL)

#     def stopTestRun(self):
#         """すべてのテスト実行後に呼び出されます。"""
#         if Settings.buffer_output:
#             # ログ状態を復元
#             logging.disable(logging.NOTSET)
#         ScriptEditorState.restore_output()
#         if Settings.delete_files and os.path.exists(Settings.temp_dir):
#             shutil.rmtree(Settings.temp_dir)

#         del os.environ[TESTING_VAR]

#         super(TestResult, self).stopTestRun()

#     def stopTest(self, test):
#         """個々のテスト実行後に呼び出されます。

#         @param test: 実行されたばかりのTestCase。"""
#         super(TestResult, self).stopTest(test)
#         if Settings.file_new:
#             cmds.file(f=True, new=True)

#     def addSuccess(self, test):
#         """成功したテストのリストを保存できるように、基本のaddSuccessメソッドをオーバーライドします。

#         @param test: 正常に実行されたTestCase。"""
#         super(TestResult, self).addSuccess(test)
#         self.successes.append(test)


if __name__ == "__main__":
    print("Running tests from command line...")
    run_tests_from_commandline()