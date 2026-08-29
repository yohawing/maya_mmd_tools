"""Tests for the shared unittest timing recorder and mayapy result adapter."""

import io
import unittest
from unittest.mock import patch

from tests.common.custom_test_runner import CustomTestRunner
from tests.common.test_timing import TestTimingRecorder


class TestTimingRecorderContract(unittest.TestCase):
    """Keep timing and outcome semantics independent from Maya."""

    def test_unstarted_tests_keep_the_not_run_representation(self):
        recorder = TestTimingRecorder(["suite.test_started", "suite.test_not_run"])

        class StartedTest:
            def id(self):
                return "suite.test_started"

        test = StartedTest()
        with patch("tests.common.test_timing.time.perf_counter", side_effect=[10.0, 10.25]):
            recorder.start_test(test)
            recorder.finish_test(test, "success")

        self.assertEqual(
            recorder.tests,
            [
                {"id": "suite.test_started", "status": "success", "elapsed_seconds": 0.25},
                {"id": "suite.test_not_run", "status": "not_run", "elapsed_seconds": None},
            ],
        )

    def test_custom_runner_records_outcomes_and_finishes_after_teardown(self):
        state = {"torn_down": False}

        class LifecycleCase(unittest.TestCase):
            def tearDown(self):
                state["torn_down"] = True

            def test_pass(self):
                pass

        suite = unittest.defaultTestLoader.loadTestsFromTestCase(LifecycleCase)
        test_id = next(iter(suite)).id()
        recorder = TestTimingRecorder([test_id])

        result = CustomTestRunner(
            stream=io.StringIO(),
            verbosity=0,
            timing_recorder=recorder,
        ).run(suite)

        self.assertTrue(result.wasSuccessful())
        self.assertTrue(state["torn_down"])
        self.assertIs(result.timing_recorder, recorder)
        self.assertEqual("success", recorder.tests[0]["status"])
        self.assertGreaterEqual(recorder.tests[0]["elapsed_seconds"], 0.0)

    def test_custom_runner_keeps_failure_and_skip_statuses(self):
        class OutcomeCase(unittest.TestCase):
            def test_failure(self):
                self.fail("expected failure")

            @unittest.skip("expected skip")
            def test_skip(self):
                pass

        suite = unittest.defaultTestLoader.loadTestsFromTestCase(OutcomeCase)
        recorder = TestTimingRecorder(test.id() for test in suite)
        result = CustomTestRunner(
            stream=io.StringIO(),
            verbosity=0,
            timing_recorder=recorder,
        ).run(suite)

        self.assertFalse(result.wasSuccessful())
        statuses = {entry["id"].rsplit(".", 1)[-1]: entry for entry in recorder.tests}
        self.assertEqual("failure", statuses["test_failure"]["status"])
        self.assertEqual("skipped", statuses["test_skip"]["status"])
        self.assertTrue(all(entry["elapsed_seconds"] is not None for entry in recorder.tests))

    def test_skipped_subtest_is_recorded_on_its_parent(self):
        class SubTestCase(unittest.TestCase):
            def test_subtests(self):
                with self.subTest(case="skipped"):
                    self.skipTest("expected subtest skip")

        suite = unittest.defaultTestLoader.loadTestsFromTestCase(SubTestCase)
        test_id = next(iter(suite)).id()
        recorder = TestTimingRecorder([test_id])

        result = CustomTestRunner(
            stream=io.StringIO(),
            verbosity=0,
            timing_recorder=recorder,
        ).run(unittest.defaultTestLoader.loadTestsFromTestCase(SubTestCase))

        self.assertTrue(result.wasSuccessful())
        self.assertEqual(1, len(recorder.tests))
        self.assertEqual(test_id, recorder.tests[0]["id"])
        self.assertEqual("skipped", recorder.tests[0]["status"])
        self.assertGreaterEqual(recorder.tests[0]["elapsed_seconds"], 0.0)
