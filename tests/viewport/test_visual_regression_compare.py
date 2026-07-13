"""Focused tests for GoldenOracle viewport image comparison gates."""

import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from tests.viewport.visual_regression_compare import backend_capture_report, compare_report


def _write_png(path: Path, color: tuple[int, int, int]) -> None:
    width = height = 8
    raw = b"".join(b"\x00" + bytes(color) * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


class VisualRegressionCompareTest(unittest.TestCase):
    def _fixture(self, reference_color, actual_color):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        reference = root / "reference.png"
        actual = root / "actual.png"
        _write_png(reference, reference_color)
        _write_png(actual, actual_color)
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

    def test_backend_reports_compare_matching_cases(self):
        temp, root, reference_capture = self._fixture((200, 40, 20), (200, 40, 20))
        self.addCleanup(temp.cleanup)
        reference = json.loads(reference_capture.read_text(encoding="utf-8"))
        reference["results"][0]["actual_png"] = reference["results"][0]["oracle_png"]
        reference_capture.write_text(json.dumps(reference), encoding="utf-8")
        actual_capture = root / "actual-capture.json"
        actual_capture.write_text(json.dumps({"results": [reference["results"][0]]}), encoding="utf-8")
        report = backend_capture_report(reference_capture, actual_capture, root / "backend.json", 0.01)
        self.assertEqual("pass", report["status"])
        self.assertEqual("maya-visual-regression-backend-comparison", report["kind"])


if __name__ == "__main__":
    unittest.main()
