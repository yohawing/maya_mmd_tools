"""Fail-closed input and invisible-frame checks for current render captures."""

import json
from pathlib import Path
import tempfile
import unittest

from tools.render_override.release_capture import load_cases, visible_pixels
from tools.render_override.render_override_visual_gate import write_png_rgb


class ReleaseRenderCaptureTest(unittest.TestCase):
    def test_background_and_tiny_foreground_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hidden, actual = root / "hidden.png", root / "actual.png"
            background = [(255, 255, 255)] * 400
            write_png_rgb(hidden, 20, 20, background)
            write_png_rgb(actual, 20, 20, background)
            with self.assertRaisesRegex(ValueError, "invisible"):
                visible_pixels(actual, hidden)
            write_png_rgb(actual, 20, 20, [(0, 0, 0)] * 10 + background[10:])
            with self.assertRaisesRegex(ValueError, "invisible"):
                visible_pixels(actual, hidden)
            write_png_rgb(actual, 20, 20, [(0, 0, 0)] * 200 + background[200:])
            self.assertEqual(visible_pixels(actual, hidden), 200)

    def test_missing_requested_case_and_oracle_are_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "fixture.pmx").write_bytes(b"fixture")
            manifest = root / "manifest.json"
            case = {"name": "fixture", "assets": {"model": "fixture.pmx"},
                    "oracle": {"path": "frame-0.png"}}
            manifest.write_text(json.dumps({"cases": [case]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "every requested"):
                load_cases(manifest, ["fixture", "missing"])
            with self.assertRaisesRegex(FileNotFoundError, "oracle_png"):
                load_cases(manifest, ["fixture"])
            (root / "frame-0.png").write_bytes(b"oracle")
            self.assertEqual(len(load_cases(manifest, ["fixture"])), 1)
            manifest.write_text(json.dumps({"cases": [case, case]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "exactly once"):
                load_cases(manifest, ["fixture"])


if __name__ == "__main__":
    unittest.main()
