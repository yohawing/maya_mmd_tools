"""Focused tests for GoldenOracle viewport image comparison gates."""

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tests.viewport.visual_regression_compare import compare_report


class VisualRegressionCompareTest(unittest.TestCase):
    def _fixture(self, reference_color, actual_color):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        reference = root / "reference.png"
        actual = root / "actual.png"
        Image.new("RGB", (8, 8), reference_color).save(reference)
        Image.new("RGB", (8, 8), actual_color).save(actual)
        capture = root / "capture.json"
        capture.write_text(
            json.dumps({"results": [{"name": "fixture-diffuse", "oracle_png": str(reference), "actual_png": str(actual)}]}),
            encoding="utf-8",
        )
        return temp, root, capture

    def test_identical_image_passes(self):
        temp, root, capture = self._fixture((200, 40, 20), (200, 40, 20))
        self.addCleanup(temp.cleanup)
        report = compare_report(capture, root / "result.json")
        self.assertEqual("pass", report["status"])

    def test_flat_gray_image_fails(self):
        temp, root, capture = self._fixture((200, 40, 20), (100, 100, 100))
        self.addCleanup(temp.cleanup)
        report = compare_report(capture, root / "result.json", {"diffuse": 1.0})
        self.assertEqual("fail", report["status"])
        self.assertIn("flat-gray suspected", report["results"][0]["failures"][0])

    def test_missing_oracle_is_not_skipped(self):
        temp, root, capture = self._fixture((200, 40, 20), (200, 40, 20))
        self.addCleanup(temp.cleanup)
        data = json.loads(capture.read_text(encoding="utf-8"))
        data["results"][0]["oracle_png"] = str(root / "missing.png")
        capture.write_text(json.dumps(data), encoding="utf-8")
        report = compare_report(capture, root / "result.json")
        self.assertEqual("fail", report["status"])
        self.assertIn("missing GoldenOracle PNG", report["results"][0]["failures"][0])

    def test_capture_errors_fail_comparison(self):
        temp, root, capture = self._fixture((200, 40, 20), (200, 40, 20))
        self.addCleanup(temp.cleanup)
        capture.write_text(json.dumps({"results": [], "errors": [{"name": "broken"}]}), encoding="utf-8")
        report = compare_report(capture, root / "result.json")
        self.assertEqual("fail", report["status"])
        self.assertEqual([{"name": "broken"}], report["capture_errors"])


if __name__ == "__main__":
    unittest.main()
