"""Atomic publication tests for prepared Bake Timeline VMD artifacts."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from typing import Optional
from unittest import mock

from mmd_tools.actions import publish_prepared_vmd_action as publisher
from mmd_tools.actions.prepared_vmd_artifact import PreparedVmdArtifactReceipt
from mmd_tools.validation.export_validator import ExportValidationReport


def _receipt(
    directory: Path,
    content: bytes,
    target: Optional[Path] = None,
) -> PreparedVmdArtifactReceipt:
    target = target or (directory / "output.vmd")
    temporary_fd, temporary_name = tempfile.mkstemp(
        prefix=f".{target.stem}.",
        suffix=target.suffix or ".vmd",
        dir=str(target.parent),
    )
    os.close(temporary_fd)
    stage = target.parent
    path = Path(temporary_name)
    path.write_bytes(content)
    return PreparedVmdArtifactReceipt(
        schema_version=1,
        stage_directory=str(stage),
        file_path=str(path),
        target_path=str(target),
        sha256=hashlib.sha256(content).hexdigest(),
        size=len(content),
        section_counts={"bone_frames": 0},
        frame_bounds=None,
        output_validation_report=ExportValidationReport("vmd", (), mode="bake_timeline"),
    )


class TestPublishPreparedVmdArtifact(unittest.TestCase):
    def test_publishes_canonical_receipt_after_cwd_change(self):
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "nested" / "output.vmd"
            target.parent.mkdir()
            receipt = _receipt(root, b"stage", target)
            try:
                os.chdir(root.parent)
                result = publisher.publish_prepared_vmd_artifact(
                    receipt,
                    receipt.target_path,
                    validation_report=receipt.output_validation_report,
                )
            finally:
                os.chdir(original_cwd)
                receipt.cleanup()

            self.assertTrue(result.succeeded)
            self.assertEqual(target.read_bytes(), b"stage")
            self.assertTrue(target.parent.is_dir())

    def test_publishes_exact_bytes_by_one_replace_after_safe_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content = b"prepared-vmd-bytes"
            target = root / "new-name.vmd"
            receipt = _receipt(root, content, target)

            with mock.patch.object(publisher.os, "replace", wraps=publisher.os.replace) as replace:
                result = publisher.publish_prepared_vmd_artifact(
                    receipt,
                    str(target),
                    validation_report=receipt.output_validation_report,
                )

            self.assertTrue(result.succeeded)
            self.assertEqual(result.exported_path, str(target))
            self.assertEqual(target.read_bytes(), content)
            self.assertEqual(result.payload_fingerprint, receipt.sha256)
            self.assertEqual(replace.call_count, 1)
            self.assertTrue(Path(receipt.file_path).exists())
            self.assertTrue(target.parent.is_dir())
            self.assertTrue(hasattr(publisher, "_copy_and_digest"))

    def test_source_tamper_preserves_existing_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "output.vmd"
            receipt = _receipt(root, b"original-stage", target)
            Path(receipt.file_path).write_bytes(b"tampered-stage")
            target.write_bytes(b"existing-output")
            before = target.read_bytes()

            result = publisher.publish_prepared_vmd_artifact(
                receipt,
                str(target),
                validation_report=receipt.output_validation_report,
            )

            self.assertFalse(result.succeeded)
            self.assertEqual(target.read_bytes(), before)
            self.assertTrue(Path(receipt.file_path).exists())

    def test_copy_tamper_preserves_existing_target_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "output.vmd"
            receipt = _receipt(root, b"stage", target)
            target.write_bytes(b"existing-output")
            before = target.read_bytes()

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
            self.assertEqual(target.read_bytes(), before)
            self.assertTrue(Path(receipt.file_path).exists())

    def test_copy_failure_preserves_existing_target_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "output.vmd"
            receipt = _receipt(root, b"stage", target)
            target.write_bytes(b"existing-output")
            before = target.read_bytes()

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
            self.assertEqual(target.read_bytes(), before)
            self.assertTrue(Path(receipt.file_path).exists())

    def test_replace_failure_preserves_existing_target_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "output.vmd"
            receipt = _receipt(root, b"stage", target)
            target.write_bytes(b"existing-output")
            before = target.read_bytes()

            with mock.patch.object(publisher.os, "replace", side_effect=OSError("replace failed")) as replace:
                result = publisher.publish_prepared_vmd_artifact(
                    receipt,
                    str(target),
                    validation_report=receipt.output_validation_report,
                )

            self.assertFalse(result.succeeded)
            self.assertEqual(replace.call_count, 1)
            self.assertEqual(target.read_bytes(), before)
            self.assertTrue(Path(receipt.file_path).exists())

    def test_keyboard_interrupt_cleans_copy_and_preserves_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "output.vmd"
            receipt = _receipt(root, b"stage", target)
            target.write_bytes(b"existing-output")

            with mock.patch.object(publisher, "_copy_and_digest", side_effect=KeyboardInterrupt("cancel")):
                with self.assertRaisesRegex(KeyboardInterrupt, "cancel"):
                    publisher.publish_prepared_vmd_artifact(
                        receipt,
                        str(target),
                        validation_report=receipt.output_validation_report,
                    )

            self.assertEqual(target.read_bytes(), b"existing-output")
            self.assertTrue(Path(receipt.file_path).exists())

    def test_receipt_cleanup_never_removes_target_parent_or_neighbor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "output.vmd"
            neighbor = root / "neighbor.txt"
            neighbor.write_bytes(b"neighbor")
            receipt = _receipt(root, b"stage", target)
            stage_path = Path(receipt.file_path)

            self.assertEqual(stage_path.parent, target.parent)
            self.assertTrue(receipt.cleanup())
            self.assertTrue(root.is_dir())
            self.assertEqual(neighbor.read_bytes(), b"neighbor")
            self.assertFalse(stage_path.exists())


if __name__ == "__main__":
    unittest.main()
