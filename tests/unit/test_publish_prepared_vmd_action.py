"""Atomic publication tests for prepared Bake Timeline VMD artifacts."""

from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from mmd_tools.actions import publish_prepared_vmd_action as publisher
from mmd_tools.actions.prepared_vmd_artifact import PreparedVmdArtifactReceipt
from mmd_tools.validation.export_validator import ExportValidationReport


def _receipt(directory: Path, content: bytes) -> PreparedVmdArtifactReceipt:
    stage = directory / "stage"
    stage.mkdir()
    path = stage / "prepared.vmd"
    path.write_bytes(content)
    return PreparedVmdArtifactReceipt(
        schema_version=1,
        stage_directory=str(stage),
        file_path=str(path),
        sha256=hashlib.sha256(content).hexdigest(),
        size=len(content),
        section_counts={"bone_frames": 0},
        frame_bounds=None,
        output_validation_report=ExportValidationReport("vmd", (), mode="bake_timeline"),
    )


class TestPublishPreparedVmdArtifact(unittest.TestCase):
    def test_publishes_exact_bytes_to_a_changed_output_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content = b"prepared-vmd-bytes"
            receipt = _receipt(root, content)
            target = root / "nested" / "new-name.vmd"

            result = publisher.publish_prepared_vmd_artifact(
                receipt,
                str(target),
                validation_report=receipt.output_validation_report,
            )

            self.assertTrue(result.succeeded)
            self.assertEqual(result.exported_path, str(target))
            self.assertEqual(target.read_bytes(), content)
            self.assertEqual(result.payload_fingerprint, receipt.sha256)

    def test_source_tamper_preserves_existing_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = _receipt(root, b"original-stage")
            receipt_path = Path(receipt.file_path)
            receipt_path.write_bytes(b"tampered-stage")
            target = root / "output.vmd"
            target.write_bytes(b"existing-output")

            result = publisher.publish_prepared_vmd_artifact(
                receipt,
                str(target),
                validation_report=receipt.output_validation_report,
            )

            self.assertFalse(result.succeeded)
            self.assertEqual(target.read_bytes(), b"existing-output")

    def test_copy_tamper_preserves_existing_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = _receipt(root, b"original-stage")
            target = root / "output.vmd"
            target.write_bytes(b"existing-output")
            original_copy = publisher._copy_and_digest

            def copy_then_tamper(source, destination):
                result = original_copy(source, destination)
                source.write_bytes(b"changed-during-copy")
                return result

            with mock.patch.object(publisher, "_copy_and_digest", copy_then_tamper):
                result = publisher.publish_prepared_vmd_artifact(
                    receipt,
                    str(target),
                    validation_report=receipt.output_validation_report,
                )

            self.assertFalse(result.succeeded)
            self.assertEqual(target.read_bytes(), b"existing-output")

    def test_copy_failure_preserves_existing_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = _receipt(root, b"stage")
            target = root / "output.vmd"
            target.write_bytes(b"existing-output")

            with mock.patch.object(
                publisher,
                "_copy_and_digest",
                side_effect=OSError("copy failed"),
            ):
                result = publisher.publish_prepared_vmd_artifact(
                    receipt,
                    str(target),
                    validation_report=receipt.output_validation_report,
                )

            self.assertFalse(result.succeeded)
            self.assertEqual(target.read_bytes(), b"existing-output")

    def test_replace_failure_preserves_existing_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = _receipt(root, b"stage")
            target = root / "output.vmd"
            target.write_bytes(b"existing-output")

            with mock.patch.object(publisher.os, "replace", side_effect=OSError("replace failed")):
                result = publisher.publish_prepared_vmd_artifact(
                    receipt,
                    str(target),
                    validation_report=receipt.output_validation_report,
                )

            self.assertFalse(result.succeeded)
            self.assertEqual(target.read_bytes(), b"existing-output")


if __name__ == "__main__":
    unittest.main()
