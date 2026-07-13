"""Validate GoldenOracle motion manifest coverage independently of the CLI.

Usage:
    python -m unittest tests.unit.test_golden_oracle_manifest
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


MANIFEST_PATH = Path(__file__).resolve().parents[1] / "golden-oracle" / "manifest.json"


class GoldenOracleManifestTest(unittest.TestCase):
    def test_motion_numeric_cases_have_oracle_focus_and_requested_frames(self):
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        default_focus = manifest["defaults"]["focus"]["bones"]
        motion_cases = [
            case for case in manifest["cases"] if case.get("kind") == "motion-numeric"
        ]

        self.assertTrue(motion_cases, "manifest must contain motion-numeric cases")
        for case in motion_cases:
            case_name = case["name"]
            for asset_name, asset_value in case["assets"].items():
                asset_path = (MANIFEST_PATH.parent / asset_value).resolve()
                self.assertTrue(
                    asset_path.is_file(),
                    f"{case_name}: missing {asset_name} asset {asset_path}",
                )

            oracle_path = (MANIFEST_PATH.parent / case["oracle"]["path"]).resolve()
            self.assertTrue(
                oracle_path.is_file(),
                f"{case_name}: missing oracle {oracle_path}",
            )

            metadata = case.get("metadata", {})
            focus_metadata = metadata.get("focus", {})
            focus = (
                focus_metadata["bones"]
                if "bones" in focus_metadata
                else default_focus
            )
            self.assertIsInstance(focus, list, f"{case_name}: focus must be a list")
            self.assertTrue(focus, f"{case_name}: focus must not be empty")
            self.assertTrue(
                all(isinstance(name, str) and name for name in focus),
                f"{case_name}: focus names must be non-empty strings",
            )

            records = [
                json.loads(line)
                for line in oracle_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertTrue(records, f"{case_name}: oracle must contain records")
            self.assertTrue(case["frames"], f"{case_name}: frames must not be empty")

            for record in records:
                source = record["source"]
                self.assertEqual(source["mmdAnimVersion"], "0.2.0")
                self.assertEqual(source["runtimeAbi"], 2)
                self.assertIsInstance(source["runtimeFeatureFlags"], int)
                self.assertRegex(source["runtimeSha256"], r"^[0-9a-f]{64}$")
                self.assertEqual(
                    Path(source["runtimeRequestedPath"]),
                    Path(source["runtimeLoadedPath"]),
                    f"{case_name}: requested and loaded runtime paths must match",
                )
                self.assertTrue(
                    re.fullmatch(r"[0-9a-f]{40}", source["mmdAnimCommit"]),
                    f"{case_name}: mmd-anim commit must be provenance-stamped",
                )

            for requested_frame in case["frames"]:
                frame_records = [
                    record
                    for record in records
                    if float(record["frame"]) == float(requested_frame)
                ]
                self.assertTrue(
                    frame_records,
                    f"{case_name}: oracle is missing requested frame {requested_frame}",
                )
                for record in frame_records:
                    bone_names = {
                        bone["name"]
                        for model in record["models"]
                        for bone in model["bones"]
                    }
                    self.assertTrue(
                        set(focus) & bone_names,
                        f"{case_name}: focus has no oracle bone at frame "
                        f"{requested_frame}",
                    )
