"""Behavioral tests for shared render-override harness plumbing."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.render_override.common import (
    capture_view,
    png_size,
    write_report,
)


class TestRenderOverrideCommon(unittest.TestCase):
    """Behavioral contracts for shared render-override helpers."""

    def test_png_size_reads_and_rejects_minimal_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            path = directory / "image.png"
            path.write_bytes(
                b"\x89PNG\r\n\x1a\n"
                + b"\x00\x00\x00\x0dIHDR"
                + (2).to_bytes(4, "big")
                + (3).to_bytes(4, "big")
                + b"\x08\x02\x00\x00\x00"
            )
            self.assertEqual(png_size(path), (2, 3))

            path.write_bytes(b"not png")
            with self.assertRaisesRegex(ValueError, "not a PNG"):
                png_size(path)

    def test_write_report_creates_parent_and_preserves_json_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            report_path = directory / "nested" / "report.json"
            write_report(report_path, {"status": "pass", "checks": {"capture": True}})
            self.assertEqual(
                json.loads(report_path.read_text(encoding="utf-8")),
                {"status": "pass", "checks": {"capture": True}},
            )

    def test_capture_view_returns_only_fresh_playblast_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            destination = directory / "capture.png"
            destination.write_bytes(b"stale-before")

            class Commands:
                @staticmethod
                def playblast(**kwargs):
                    Path(kwargs["filename"] + ".png").write_bytes(b"fresh")
                    return "capture.png"

            self.assertEqual(
                capture_view(Commands(), destination, "modelPanel4", 32, 24),
                destination,
            )
            self.assertEqual(destination.read_bytes(), b"fresh")
