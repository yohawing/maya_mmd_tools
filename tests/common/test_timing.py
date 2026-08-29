"""Shared per-test timing support for unittest runners.

The recorder is intentionally independent of Maya and of a particular output
format.  GUI and mayapy runners both attach it to their ``TestResult`` so the
test lifecycle is measured once, including teardown and cleanup hooks.
"""

import time


class TestTimingRecorder:
    """Collect per-test outcome and elapsed time without rerunning tests."""

    __test__ = False

    _OUTCOME_PRIORITY = {
        "not_run": 0,
        "unknown": 1,
        "success": 1,
        "skipped": 2,
        "expected_failure": 2,
        "unexpected_success": 3,
        "failure": 4,
        "error": 5,
    }

    def __init__(self, test_ids):
        self.tests = [
            {"id": test_id, "status": "not_run", "elapsed_seconds": None}
            for test_id in test_ids
        ]
        self._by_id = {entry["id"]: entry for entry in self.tests}
        self._started = {}

    def start_test(self, test):
        """Record the start after unittest has entered the test lifecycle."""
        self._started[id(test)] = time.perf_counter()

    def record_outcome(self, test, outcome):
        """Record an outcome while retaining the strongest observed result."""
        entry = self._by_id.get(test.id())
        if entry is None:
            entry = {"id": test.id(), "status": "not_run", "elapsed_seconds": None}
            self.tests.append(entry)
            self._by_id[test.id()] = entry
        current = entry["status"]
        if self._OUTCOME_PRIORITY.get(outcome, 1) >= self._OUTCOME_PRIORITY.get(current, 1):
            entry["status"] = outcome

    def outcome_for(self, test):
        """Return the recorded outcome, or ``None`` if it has not run yet."""
        entry = self._by_id.get(test.id())
        if entry is None or entry["status"] == "not_run":
            return None
        return entry["status"]

    def finish_test(self, test, outcome=None):
        """Finish timing after unittest teardown and cleanup have completed."""
        if outcome is not None:
            self.record_outcome(test, outcome)
        started = self._started.pop(id(test), None)
        entry = self._by_id.get(test.id())
        if entry is None:
            entry = {"id": test.id(), "status": "not_run", "elapsed_seconds": None}
            self.tests.append(entry)
            self._by_id[test.id()] = entry
        if started is not None:
            entry["elapsed_seconds"] = max(0.0, time.perf_counter() - started)
