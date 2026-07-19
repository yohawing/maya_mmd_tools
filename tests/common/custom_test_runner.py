#!/usr/bin/env python
"""
カラーサポート付きのテストランナーを提供するモジュール。

テスト結果を見やすく色分けして表示します。
"""

import platform
import sys
from unittest.runner import TextTestResult, TextTestRunner

# ANSIカラーコード
COLOR = {
    "RESET": "\033[0m",
    "BOLD": "\033[1m",
    "RED": "\033[31m",
    "GREEN": "\033[32m",
    "YELLOW": "\033[33m",
    "BLUE": "\033[34m",
    "MAGENTA": "\033[35m",
    "CYAN": "\033[36m",
    "WHITE": "\033[37m",
}


def enable_windows_ansi_support():
    """WindowsターミナルでANSIカラーコードを有効にする"""
    if platform.system() == "Windows":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            # Windows 10 v1607+ で ANSI エスケープシーケンスを有効化
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except (ImportError, AttributeError):
            # ctypes が利用できないか、Windows APIが変更された場合
            pass


class CustomTestResult(TextTestResult):
    """
    カラー表示に対応したTextTestResultのサブクラス。

    成功したテストは緑色、失敗したテストは赤色、エラーはマゼンタ、
    スキップされたテストは青色で表示されます。
    """

    def __init__(self, *args, **kwargs):
        super(CustomTestResult, self).__init__(*args, **kwargs)
        self.use_colors = sys.stdout.isatty()  # ターミナルの場合のみカラー表示
        self.show_error_details = True

    def getDescription(self, test):
        """テストの説明を取得します。docstringがあれば表示します。"""
        doc_first_line = test.shortDescription()
        if self.descriptions and doc_first_line:
            return f"{test.id()} \n {doc_first_line}"
        else:
            return test.id()

    def addSuccess(self, test):
        """テストが成功した場合の処理"""
        super(CustomTestResult, self).addSuccess(test)
        if self.showAll:
            if self.use_colors:
                self.stream.writeln(f"{COLOR['GREEN']}OK{COLOR['RESET']}")
            else:
                self.stream.writeln("OK")

    def addError(self, test, err):
        """テストがエラーになった場合の処理"""
        super(CustomTestResult, self).addError(test, err)
        if self.showAll:
            if self.use_colors:
                self.stream.writeln(f"{COLOR['MAGENTA']}ERROR{COLOR['RESET']}")
            else:
                self.stream.writeln("ERROR")

    def addFailure(self, test, err):
        """テストが失敗した場合の処理"""
        super(CustomTestResult, self).addFailure(test, err)
        if self.showAll:
            if self.use_colors:
                self.stream.writeln(f"{COLOR['RED']}FAIL{COLOR['RESET']}")
            else:
                self.stream.writeln("FAIL")

    def addSkip(self, test, reason):
        """テストがスキップされた場合の処理"""
        super(CustomTestResult, self).addSkip(test, reason)
        if self.showAll:
            if self.use_colors:
                self.stream.writeln(f"{COLOR['BLUE']}SKIP{COLOR['RESET']}: {reason}")
            else:
                self.stream.writeln(f"SKIP: {reason}")

    def addExpectedFailure(self, test, err):
        """予期された失敗の場合の処理"""
        super(CustomTestResult, self).addExpectedFailure(test, err)
        if self.showAll:
            if self.use_colors:
                self.stream.writeln(f"{COLOR['YELLOW']}expected failure{COLOR['RESET']}")
            else:
                self.stream.writeln("expected failure")

    def addUnexpectedSuccess(self, test):
        """予期せず成功した場合の処理"""
        super(CustomTestResult, self).addUnexpectedSuccess(test)
        if self.showAll:
            if self.use_colors:
                self.stream.writeln(f"{COLOR['YELLOW']}unexpected success{COLOR['RESET']}")
            else:
                self.stream.writeln("unexpected success")

    def printErrors(self):
        """エラーの詳細を出力"""
        if not self.show_error_details:
            self._print_compact_failures()
            return

        if self.dots or self.showAll:
            self.stream.writeln()

        # エラーがあれば表示
        if self.errors and self.use_colors:
            self.stream.writeln(f"{COLOR['BOLD']}{COLOR['MAGENTA']}エラー詳細:{COLOR['RESET']}")
            self.printErrorList("ERROR", self.errors)
        elif self.errors:
            self.stream.writeln("エラー詳細:")
            self.printErrorList("ERROR", self.errors)

        # 失敗があれば表示
        if self.failures and self.use_colors:
            self.stream.writeln(f"{COLOR['BOLD']}{COLOR['RED']}失敗詳細:{COLOR['RESET']}")
            self.printErrorList("FAIL", self.failures)
        elif self.failures:
            self.stream.writeln("失敗詳細:")
            self.printErrorList("FAIL", self.failures)

    def printErrorList(self, flavour, errors):
        """エラーリストの表示をカスタマイズ"""
        for test, err in errors:
            self.stream.writeln(self.separator1)
            if self.use_colors:
                color = COLOR["MAGENTA"] if flavour == "ERROR" else COLOR["RED"]
                self.stream.writeln(f"{color}{self.getDescription(test)}{COLOR['RESET']}")
                self.stream.writeln(f"{color}{self.separator2}{COLOR['RESET']}")
                self.stream.writeln(f"{color}{err}{COLOR['RESET']}")
            else:
                self.stream.writeln(self.getDescription(test))
                self.stream.writeln(self.separator2)
                self.stream.writeln(err)

    def _print_compact_failures(self):
        """端末向けにtracebackを省略した失敗サマリを出力"""
        failures = [
            ("ERROR", test)
            for test, _ in self.errors
        ] + [
            ("FAIL", test)
            for test, _ in self.failures
        ] + [
            ("UNEXPECTED SUCCESS", test)
            for test in self.unexpectedSuccesses
        ]
        if not failures:
            return

        self.stream.writeln()
        self.stream.writeln("エラー詳細は省略しました（--verboseで表示）")
        for kind, test in failures:
            self.stream.writeln(f"  {kind}: {test.id()}")


class CustomTestRunner(TextTestRunner):
    """
    カラー表示に対応したTextTestRunnerのサブクラス。

    テスト結果を色分けして表示し、視認性を向上させます。
    """

    resultclass = CustomTestResult

    def __init__(self, *, show_error_details=True, **kwargs):
        self.show_error_details = show_error_details
        super(CustomTestRunner, self).__init__(**kwargs)
        enable_windows_ansi_support()

    def _makeResult(self):
        result = super()._makeResult()
        result.show_error_details = self.show_error_details
        return result

    def run(self, test):
        """テストを実行し、結果を表示"""
        result = super(CustomTestRunner, self).run(test)

        # 結果のサマリーを表示
        use_colors = sys.stdout.isatty()

        if result.wasSuccessful():
            if use_colors:
                self.stream.write(f"{COLOR['GREEN']}")
            self.stream.writeln("======================================================================")
            self.stream.writeln(f"すべてのテストが成功しました！ ({result.testsRun} テスト実行)")
            self.stream.writeln("======================================================================")
            if use_colors:
                self.stream.write(f"{COLOR['RESET']}")
        else:
            if use_colors:
                self.stream.write(f"{COLOR['RED']}")
            self.stream.writeln("======================================================================")
            self.stream.writeln(f"テストに失敗しました ({result.testsRun} テスト実行)")
            self.stream.writeln("======================================================================")
            if use_colors:
                self.stream.write(f"{COLOR['RESET']}")

        return result
