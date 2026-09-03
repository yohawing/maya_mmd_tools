"""GUI テスト用のベースクラスとユーティリティ。"""

import unittest
import functools
import time
import maya.cmds as cmds

from tests.common.test_timing import TestTimingRecorder


_ATTACHED_FILE_MUTATION_FLAGS = frozenset(
    {
        "new",
        "open",
        "o",
        "save",
        "s",
        "saveDisk",
        "rename",
        "import",
        "i",
        "reference",
        "r",
        "loadReference",
        "lr",
        "removeReference",
        "rr",
        "exportAll",
        "exportSelected",
        "ea",
        "es",
    }
)


def _script_job_ids():
    """Return the IDs of current Maya scriptJobs without touching existing jobs."""
    result = set()
    for description in cmds.scriptJob(listJobs=True) or []:
        prefix = str(description).split(":", 1)[0].strip()
        try:
            result.add(int(prefix))
        except ValueError:
            continue
    return result


def _batch_environment_snapshot():
    """Capture only identities/state needed to remove per-case additions."""
    from mmd_tools.ui.qt_compat import QApplication

    app_instance = getattr(QApplication, "instance", None)
    app = app_instance() if callable(app_instance) else None
    widgets = set(id(widget) for widget in (app.topLevelWidgets() if app is not None else []))
    return {
        "widgets": widgets,
        "maya_windows": set(cmds.lsUI(windows=True) or []),
        "script_jobs": _script_job_ids(),
    }


def _restore_batch_environment(snapshot, new_scene=True):
    """Remove only Qt/Maya state created after *snapshot*.

    QSettings is process-isolated before this snapshot is taken, so restoring
    settings is neither needed nor a safety boundary for the user's store.
    """
    from mmd_tools.ui.qt_compat import QApplication

    errors = []
    app_instance = getattr(QApplication, "instance", None)
    app = app_instance() if callable(app_instance) else None
    if app is not None:
        for widget in list(app.topLevelWidgets()):
            if id(widget) in snapshot["widgets"]:
                continue
            try:
                widget.close()
                widget.setParent(None)
                widget.deleteLater()
            except Exception as exc:
                errors.append(f"Qt window cleanup: {exc}")
        app.processEvents()

    for window in set(cmds.lsUI(windows=True) or []) - snapshot["maya_windows"]:
        try:
            cmds.deleteUI(window, window=True)
        except Exception as exc:
            errors.append(f"Maya window cleanup {window}: {exc}")

    for job_id in _script_job_ids() - snapshot["script_jobs"]:
        try:
            cmds.scriptJob(kill=job_id, force=True)
        except Exception as exc:
            errors.append(f"scriptJob cleanup {job_id}: {exc}")

    if new_scene:
        try:
            cmds.file(new=True, force=True)
        except Exception as exc:
            errors.append(f"scene reset: {exc}")
    if errors:
        raise RuntimeError("; ".join(errors))


class _AttachedSceneGuard:
    """Protect an externally owned Maya scene during one GUI test command.

    Attached sessions are explicitly clean and dedicated before dispatch.  The
    guard still prevents test fixtures from replacing or saving that scene and
    rolls back ordinary node edits through one owned undo chunk.  It never
    resets the scene with ``cmds.file(new=True, force=True)``.
    """

    def __init__(self):
        self._original_file = None
        self._scene_path = None
        self._modified = None
        self._selection = ()
        self._scene_fingerprint = None
        self._undo_enabled = None
        self._chunk_open = False
        self._undo_performed = False

    @staticmethod
    def _scene_fingerprint_value():
        """Return independent DAG and DG identities for ownership checking."""
        dag_nodes = tuple(sorted(str(node) for node in (cmds.ls(long=True, dag=True) or ())))
        dependency_nodes = tuple(
            sorted(str(node) for node in (cmds.ls(long=True, dependencyNodes=True) or ()))
        )
        return dag_nodes, dependency_nodes

    def start(self):
        """Capture the attached scene and install the fail-closed file guard."""
        self._scene_path = str(cmds.file(query=True, sceneName=True) or "")
        self._modified = bool(cmds.file(query=True, modified=True))
        self._selection = tuple(cmds.ls(selection=True, long=True) or ())
        self._scene_fingerprint = self._scene_fingerprint_value()
        self._undo_enabled = bool(cmds.undoInfo(query=True, state=True))
        if not self._undo_enabled:
            raise RuntimeError("attached GUI tests require Maya Undo to be enabled")

        self._original_file = cmds.file
        original_file = self._original_file

        def guarded_file(*args, **kwargs):
            if any(bool(kwargs.get(flag)) for flag in _ATTACHED_FILE_MUTATION_FLAGS):
                raise RuntimeError(
                    "attached GUI tests cannot replace, save, import, or export the external scene"
                )
            return original_file(*args, **kwargs)

        cmds.undoInfo(openChunk=True, chunkName="MMD GUI Attached Test")
        self._chunk_open = True
        try:
            cmds.file = guarded_file
        except Exception:
            cmds.undoInfo(closeChunk=True)
            self._chunk_open = False
            raise

    def finish(self):
        """Restore scene state and remove the temporary command wrapper."""
        errors = []
        try:
            current_path = str(cmds.file(query=True, sceneName=True) or "")
            current_modified = bool(cmds.file(query=True, modified=True))
            current_fingerprint = self._scene_fingerprint_value()
            current_undo_enabled = bool(cmds.undoInfo(query=True, state=True))
            changed = (
                current_path != self._scene_path
                or current_modified != self._modified
                or current_fingerprint != self._scene_fingerprint
                or current_undo_enabled != self._undo_enabled
            )
        except Exception as exc:
            errors.append(f"attached scene state probe: {exc}")
            changed = True

        if self._original_file is not None:
            try:
                cmds.file = self._original_file
            except Exception as exc:
                errors.append(f"attached file guard restore: {exc}")

        try:
            if self._chunk_open:
                cmds.undoInfo(closeChunk=True)
                self._chunk_open = False
            if changed:
                cmds.undo()
                self._undo_performed = True
        except Exception as exc:
            errors.append(f"attached scene rollback: {exc}")

        try:
            if self._selection:
                cmds.select(list(self._selection), replace=True)
            else:
                cmds.select(clear=True)
        except Exception as exc:
            errors.append(f"attached selection restore: {exc}")

        try:
            actual_path = str(cmds.file(query=True, sceneName=True) or "")
            actual_modified = bool(cmds.file(query=True, modified=True))
            actual_selection = tuple(cmds.ls(selection=True, long=True) or ())
            actual_fingerprint = self._scene_fingerprint_value()
            actual_undo_enabled = bool(cmds.undoInfo(query=True, state=True))
            if actual_path != self._scene_path:
                errors.append(
                    f"attached scene path changed: expected {self._scene_path!r}, got {actual_path!r}"
                )
            if actual_modified != self._modified:
                errors.append(
                    f"attached modified state changed: expected {self._modified!r}, got {actual_modified!r}"
                )
            if actual_selection != self._selection:
                errors.append(
                    f"attached selection changed: expected {self._selection!r}, got {actual_selection!r}"
                )
            if actual_fingerprint != self._scene_fingerprint:
                errors.append("attached DAG/DG fingerprint changed")
            if actual_undo_enabled != self._undo_enabled:
                errors.append(
                    f"attached Undo availability changed: expected {self._undo_enabled!r}, "
                    f"got {actual_undo_enabled!r}"
                )
        except Exception as exc:
            errors.append(f"attached scene restore probe: {exc}")
        if errors:
            raise RuntimeError("; ".join(errors))


def skip_if_no_gui(func):
    """GUIが利用できない場合はテストをスキップするデコレーター"""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if cmds.about(batch=True):
            raise unittest.SkipTest("GUI environment required")
        return func(*args, **kwargs)

    return wrapper


def requires_gui(cls):
    """クラス全体にGUI要求を適用するデコレーター"""
    for attr_name in dir(cls):
        attr = getattr(cls, attr_name)
        if callable(attr) and attr_name.startswith("test_"):
            setattr(cls, attr_name, skip_if_no_gui(attr))
    return cls


class GuiTestBase(unittest.TestCase):
    """GUI テスト用のベースクラス"""

    @classmethod
    def setUpClass(cls):
        """クラスレベルのセットアップ"""
        if cmds.about(batch=True):
            raise unittest.SkipTest("GUI environment required for this test class")
        super().setUpClass()

    def setUp(self):
        """各テストの前処理"""
        # 既存のウィンドウをクリーンアップ
        self._cleanup_windows()

    def tearDown(self):
        """各テストの後処理"""
        # ウィンドウをクリーンアップ
        self._cleanup_windows()

    def _cleanup_windows(self):
        """開いているウィンドウをクリーンアップ"""
        # Maya MMD Toolsのウィンドウを探して削除
        all_windows = cmds.lsUI(windows=True)
        for window in all_windows:
            if window.startswith("MayaMMDTools") or window.startswith("mmdTools"):
                try:
                    cmds.deleteUI(window, window=True)
                except Exception:
                    pass


class _LifecycleTextTestResult(unittest.TextTestResult):
    """Text result that flushes per-test lifecycle messages to its stream."""

    def __init__(self, *args, timing_recorder=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._unfinished_test_ids = set()
        self._timing_recorder = timing_recorder

    def _write_lifecycle(self, test, phase, outcome=None):
        message = f"[GUI TEST] {phase} {test.id()}"
        if outcome is not None:
            message += f" outcome={outcome}"
        self.stream.write(message + "\n")
        self.stream.flush()

    def startTest(self, test):
        super().startTest(test)
        self._unfinished_test_ids.add(id(test))
        if self._timing_recorder is not None:
            self._timing_recorder.start_test(test)
        self._write_lifecycle(test, "START")

    def _write_test_end(self, test, outcome):
        self._unfinished_test_ids.discard(id(test))
        if self._timing_recorder is not None:
            self._timing_recorder.record_outcome(test, outcome)
        self._write_lifecycle(test, "END", outcome)

    def addSuccess(self, test):
        super().addSuccess(test)
        self._write_test_end(test, "success")

    def addError(self, test, err):
        super().addError(test, err)
        self._write_test_end(test, "error")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._write_test_end(test, "failure")

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        parent_test = getattr(test, "test_case", None)
        if parent_test is not None:
            if self._timing_recorder is not None:
                self._timing_recorder.record_outcome(parent_test, "skipped")
            return
        self._write_test_end(test, "skipped")

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._write_test_end(test, "expected_failure")

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._write_test_end(test, "unexpected_success")

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None and self._timing_recorder is not None:
            outcome = (
                "failure"
                if issubclass(err[0], test.failureException)
                else "error"
            )
            self._timing_recorder.record_outcome(test, outcome)

    def stopTest(self, test):
        if id(test) in self._unfinished_test_ids:
            known_outcome = (
                self._timing_recorder.outcome_for(test)
                if self._timing_recorder is not None
                else None
            )
            self._write_test_end(test, known_outcome or "unknown")
        if self._timing_recorder is not None:
            # unittest calls stopTest only after tearDown and cleanup hooks.
            self._timing_recorder.finish_test(test)
        super().stopTest(test)



# Keep the historical private import path used by existing GUI tests.
_TestTimingRecorder = TestTimingRecorder


def _iter_tests(suite):
    """Yield leaf tests from a nested unittest suite."""
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from _iter_tests(test)
        else:
            yield test


class GuiTestRunner:
    """
    A static class to run GUI tests from an external command.
    It redirects all output to a specified log file.
    """

    @staticmethod
    def run_tests_from_command(
        log_file_path,
        test_dir_str,
        test_filter=None,
        timing_report_path=None,
        emit_completion_marker=True,
        preserve_attached_scene=False,
    ):
        """
        Discovers and runs tests, redirecting output to a log file.

        Args:
            log_file_path (str): The absolute path to the log file.
            test_dir_str (str): The relative path to the test directory.
            test_filter (str | None): Optional substring matched against test IDs.
            timing_report_path (str | None): Optional JSON timing report path.
            preserve_attached_scene (bool): Protect the externally owned scene.
        """
        import logging
        import sys
        from pathlib import Path
        from tests.common.qsettings_isolation import activate_qsettings_isolation

        # Get project root from this file's location
        project_root = Path(__file__).resolve().parent.parent.parent
        test_dir = project_root / test_dir_str

        # Configure logging to file
        # This will capture logs from the test runner and the application itself
        original_handlers = logging.root.handlers[:]
        original_log_level = logging.root.level
        for handler in original_handlers:
            logging.root.removeHandler(handler)
        logging.basicConfig(
            filename=log_file_path,
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
            encoding="utf-8",
            errors="backslashreplace",
        )

        # Redirect stdout and stderr to the log file.  Preserve the live Maya
        # streams so they can always be restored after a test-side exception.
        log_file = open(log_file_path, "a", encoding="utf-8")
        original_stdout, original_stderr = sys.stdout, sys.stderr
        sys.stdout = log_file
        sys.stderr = log_file
        status = "ERROR"
        timing_report = None
        timing_recorder = None
        timing_helpers = None
        discovery_started = None
        tests_started = None
        attached_scene_guard = None
        attached_scene_guard_started = False
        attached_cleanup_acknowledged = False

        try:
            # This must happen before discovery imports production widgets.
            # The redirected backend is process-scoped and survives failures,
            # timeouts, and forced Maya termination without touching UserScope.
            settings_isolation = activate_qsettings_isolation()
            print(f"QSettings test backend: {settings_isolation['root']}")
            if preserve_attached_scene:
                attached_scene_guard = _AttachedSceneGuard()
                attached_scene_guard.start()
                attached_scene_guard_started = True
            print(f"Starting GUI tests. Project root: {project_root}")
            print(f"Test directory: {test_dir}")
            if test_filter:
                print(f"Test filter: {test_filter}")
            print(f"Log file: {log_file_path}")

            # Discover tests
            suite = unittest.TestSuite()
            loader = unittest.TestLoader()

            # Use discover to find all test modules in the specified directory
            discovery_started = time.perf_counter()
            discovered_suite = loader.discover(str(test_dir), pattern="guitest_*.py", top_level_dir=str(project_root))
            if test_filter:
                discovered_suite = GuiTestRunner._filter_suite(
                    discovered_suite,
                    test_filter,
                    exact=preserve_attached_scene,
                )
            if preserve_attached_scene and discovered_suite.countTestCases() != 1:
                raise RuntimeError(
                    "attached GUI test filter must resolve to exactly one complete test ID"
                )
            suite.addTest(discovered_suite)
            discovery_elapsed = max(0.0, time.perf_counter() - discovery_started)
            timing_recorder = _TestTimingRecorder(test.id() for test in _iter_tests(suite))

            if timing_report_path:
                from tests import run_gui_tests as timing_helpers

                fallback = timing_helpers.new_timing_report("unknown", test_dir_str, test_filter)
                timing_report = timing_helpers.read_timing_report(timing_report_path, fallback)
                timing_report["phases"]["discovery"] = {
                    "status": "passed",
                    "elapsed_seconds": discovery_elapsed,
                }
                timing_report["phases"]["tests"] = {
                    "status": "running",
                    "elapsed_seconds": None,
                }
                timing_report["tests"] = timing_recorder.tests
                timing_helpers.write_timing_report(timing_report_path, timing_report)

            if suite.countTestCases() == 0:
                print("No tests found.")
                status = "NO_TESTS"
                if timing_report is not None:
                    timing_report["phases"]["tests"] = {
                        "status": "no_tests",
                        "elapsed_seconds": 0.0,
                    }
                # Final status is returned only after attached cleanup and
                # report publication in ``finally``.

            else:
                # Run tests
                print(f"Found {suite.countTestCases()} tests to run.")

                def result_factory(*args, **kwargs):
                    return _LifecycleTextTestResult(
                        *args,
                        timing_recorder=timing_recorder,
                        **kwargs,
                    )

                tests_started = time.perf_counter()
                runner = unittest.TextTestRunner(
                    stream=log_file,
                    verbosity=2,
                    resultclass=result_factory,
                )
                result = runner.run(suite)
                tests_elapsed = max(0.0, time.perf_counter() - tests_started)
                status = "PASS" if result.wasSuccessful() else "FAIL"
                if timing_report is not None:
                    timing_report["phases"]["tests"] = {
                        "status": "passed" if status == "PASS" else "failed",
                        "elapsed_seconds": tests_elapsed,
                    }

        except Exception:
            logging.error("An unexpected error occurred during test execution.", exc_info=True)
            if timing_report_path:
                if timing_report is None:
                    from tests import run_gui_tests as timing_helpers

                    fallback = timing_helpers.new_timing_report("unknown", test_dir_str, test_filter)
                    timing_report = timing_helpers.read_timing_report(timing_report_path, fallback)
                if timing_report["phases"]["discovery"]["status"] == "blocked":
                    timing_report["phases"]["discovery"] = {
                        "status": "failed",
                        "elapsed_seconds": (
                            max(0.0, time.perf_counter() - discovery_started)
                            if discovery_started is not None
                            else None
                        ),
                    }
                elif timing_report["phases"]["tests"]["status"] in {"blocked", "running"}:
                    timing_report["phases"]["tests"] = {
                        "status": "failed",
                        "elapsed_seconds": (
                            max(0.0, time.perf_counter() - tests_started)
                            if tests_started is not None
                            else None
                        ),
                    }
        finally:
            if attached_scene_guard_started:
                try:
                    attached_scene_guard.finish()
                    attached_cleanup_acknowledged = True
                except Exception as exc:
                    logging.error("Attached Maya scene cleanup failed", exc_info=True)
                    print(f"Attached Maya scene cleanup failed: {exc}")
                    status = "ERROR"
            if timing_report_path:
                if timing_report is None:
                    from tests import run_gui_tests as timing_helpers

                    fallback = timing_helpers.new_timing_report("unknown", test_dir_str, test_filter)
                    timing_report = timing_helpers.read_timing_report(timing_report_path, fallback)
                if timing_recorder is not None:
                    timing_report["tests"] = timing_recorder.tests
                if preserve_attached_scene:
                    timing_report["cleanup_acknowledged"] = (
                        attached_cleanup_acknowledged and status in {"PASS", "NO_TESTS"}
                    )
                timing_report["status"] = status
                timing_helpers.write_timing_report(timing_report_path, timing_report)
            if emit_completion_marker:
                print(f"\n//-- GUI TEST FINISHED --// status={status}")
            log_file.flush()
            log_file.close()
            # Restore original stdout/stderr
            sys.stdout = original_stdout
            sys.stderr = original_stderr
            # Do not leave a FileHandler holding the log open on Windows.  Maya
            # logging is restored to the state it had before this test command.
            for handler in logging.root.handlers[:]:
                logging.root.removeHandler(handler)
                handler.close()
            for handler in original_handlers:
                logging.root.addHandler(handler)
            logging.root.setLevel(original_log_level)

        return status

    @staticmethod
    def run_batch_from_command(log_file_path, cases, timing_report_path, new_scene=True):
        """Run focused manifest cases, optionally preserving the attached scene."""
        import os
        from pathlib import Path
        from tests import run_gui_tests as report_helpers
        from tests.common.qsettings_isolation import (
            activate_qsettings_isolation,
            reset_isolated_qsettings,
        )

        # Batch manifests bypass run_tests_from_command, so install the same
        # process-level backend before the first snapshot or test import.
        activate_qsettings_isolation()

        report_path = Path(timing_report_path)
        fallback = report_helpers.new_batch_report("unknown", cases)
        report = report_helpers.read_batch_report(report_path, fallback)
        if [entry.get("id") for entry in report.get("cases", [])] != [case["id"] for case in cases]:
            report = fallback
        try:
            snapshot = _batch_environment_snapshot()
        except Exception as exc:
            for entry in report["cases"]:
                entry["status"] = "BLOCKED"
                entry["blocked_reason"] = f"batch environment snapshot failed: {exc}"
            report["status"] = "ERROR"
            report_helpers.finalize_batch_report(report)
            report_helpers.write_timing_report(report_path, report)
            with open(log_file_path, "a", encoding="utf-8") as log_file:
                log_file.write("\n//-- GUI TEST FINISHED --// status=ERROR\n")
            return "ERROR"

        for index, case in enumerate(cases):
            entry = report["cases"][index]
            entry["status"] = "RUNNING"
            report_helpers.write_timing_report(report_path, report)
            started = time.perf_counter()
            case_report_path = report_path.with_name(
                f"{report_path.name}.case-{case['id']}-{os.getpid()}"
            )
            case_report = None
            try:
                try:
                    _restore_batch_environment(snapshot, new_scene=new_scene)
                    # Native safety is process-wide; fixture state is reset per
                    # case so a previous case cannot leak history into this one.
                    reset_isolated_qsettings()
                except Exception as exc:
                    entry["status"] = "BLOCKED"
                    entry["blocked_reason"] = str(exc)
                    continue

                case_report = report_helpers.new_timing_report(
                    report.get("maya_version", "unknown"),
                    case["test_path"],
                    case["test_filter"],
                )
                case_report["phases"]["startup"] = {
                    "status": "passed",
                    "elapsed_seconds": 0.0,
                }
                case_report["phases"]["shutdown"] = {
                    "status": "skipped",
                    "elapsed_seconds": 0.0,
                }
                report_helpers.write_timing_report(case_report_path, case_report)
                status = GuiTestRunner.run_tests_from_command(
                    log_file_path,
                    case["test_path"],
                    case["test_filter"],
                    str(case_report_path),
                    emit_completion_marker=False,
                    preserve_attached_scene=not new_scene,
                )
                case_report = report_helpers.read_timing_report(case_report_path, case_report)
                report_helpers.finalize_timing_report(case_report)
                entry.update(
                    status=status,
                    phases=case_report.get("phases", {}),
                    tests=case_report.get("tests", []),
                    test_counts=case_report.get("test_counts", {}),
                    slowest_tests=case_report.get("slowest_tests", []),
                )
            except Exception as exc:
                entry["status"] = "ERROR"
                entry["error"] = str(exc)
            finally:
                entry["elapsed_seconds"] = max(0.0, time.perf_counter() - started)
                cleanup_errors = []
                try:
                    _restore_batch_environment(snapshot, new_scene=new_scene)
                except Exception as exc:
                    entry["status"] = "ERROR"
                    entry["cleanup_error"] = str(exc)
                    cleanup_errors.append(str(exc))
                try:
                    case_report_path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    entry["status"] = "ERROR"
                    prior_error = entry.get("cleanup_error")
                    message = f"case timing cleanup: {exc}"
                    entry["cleanup_error"] = f"{prior_error}; {message}" if prior_error else message
                    cleanup_errors.append(message)
                try:
                    reset_isolated_qsettings()
                except Exception as exc:
                    entry["status"] = "ERROR"
                    prior_error = entry.get("cleanup_error")
                    message = f"QSettings case cleanup: {exc}"
                    entry["cleanup_error"] = f"{prior_error}; {message}" if prior_error else message
                    cleanup_errors.append(message)
                if cleanup_errors and case_report is not None:
                    # The per-case report is the first durable status emitted
                    # for a case.  If cleanup fails after the test body passed,
                    # rewrite it to ERROR before the batch report or marker can
                    # claim success.
                    case_report["status"] = "ERROR"
                    case_report["cleanup_error"] = "; ".join(cleanup_errors)
                    report_helpers.finalize_timing_report(case_report)
                    report_helpers.write_timing_report(case_report_path, case_report)
                report_helpers.write_timing_report(report_path, report)

        statuses = [entry["status"] for entry in report["cases"]]
        if statuses and all(status == "PASS" for status in statuses):
            status = "PASS"
        elif any(status in {"ERROR", "BLOCKED", "NOT_RUN", "RUNNING"} for status in statuses):
            status = "ERROR"
        else:
            status = "FAIL"
        report["status"] = status
        report_helpers.finalize_batch_report(report)
        report_helpers.write_timing_report(report_path, report)
        with open(log_file_path, "a", encoding="utf-8") as log_file:
            log_file.write(f"\n//-- GUI TEST FINISHED --// status={status}\n")
            log_file.flush()
        return status

    @staticmethod
    def _filter_suite(suite, test_filter, exact=False):
        """Return tests matching one complete ID or the legacy substring filter."""
        filtered_suite = unittest.TestSuite()
        for test in suite:
            if isinstance(test, unittest.TestSuite):
                nested_suite = GuiTestRunner._filter_suite(test, test_filter, exact=exact)
                if nested_suite.countTestCases():
                    filtered_suite.addTest(nested_suite)
            else:
                matches = test.id() == test_filter if exact else test_filter in test.id()
                if matches:
                    filtered_suite.addTest(test)
        return filtered_suite
