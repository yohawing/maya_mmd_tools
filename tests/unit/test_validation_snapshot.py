"""Tests for deterministic validation snapshots and payload fingerprints."""

import math
import unittest

from mmd_tools.validation.snapshot import ExportValidationSnapshot, fingerprint_payload


class ValidationSnapshotTests(unittest.TestCase):
    """Verify deep-copy, fingerprint, and scene-revision boundaries."""

    def test_fingerprint_is_stable_for_mapping_order(self):
        first = fingerprint_payload({"b": 2, "a": [1, 2]})
        second = fingerprint_payload({"a": [1, 2], "b": 2})

        self.assertEqual(first, second)
        self.assertTrue(first.startswith("sha256:"))

    def test_capture_keeps_an_immutable_validation_payload(self):
        source = {"nested": {"value": 1}, "items": ["a"]}
        snapshot = ExportValidationSnapshot.capture(
            source,
            "pmx",
            scene_revision=7,
            target_identity="modelRoot",
        )
        source["nested"]["value"] = 2
        source["items"].append("b")

        self.assertEqual(snapshot.model_data["nested"]["value"], 1)
        self.assertEqual(snapshot.model_data["items"], ["a"])
        self.assertFalse(snapshot.matches(source, "pmx", scene_revision=7))
        self.assertTrue(
            snapshot.matches(
                snapshot.model_data,
                "pmx",
                scene_revision=7,
                target_identity="modelRoot",
            )
        )
        self.assertFalse(snapshot.matches(snapshot.model_data, "pmx", scene_revision=8))
        self.assertFalse(
            snapshot.matches(
                snapshot.model_data,
                "pmx",
                scene_revision=7,
                target_identity="otherRoot",
            )
        )
        self.assertEqual(snapshot.target_identity, "modelRoot")

    def test_writer_copy_cannot_mutate_snapshot(self):
        snapshot = ExportValidationSnapshot.capture({"items": [1]}, "pmx")
        writer_copy = snapshot.copy_for_export()
        writer_copy["items"].append(2)

        self.assertEqual(snapshot.model_data["items"], [1])

    def test_non_finite_payload_is_not_fingerprintable(self):
        with self.assertRaises(ValueError):
            fingerprint_payload({"value": math.nan})


if __name__ == "__main__":
    unittest.main()
