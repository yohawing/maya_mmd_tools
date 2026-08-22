"""Headless tests for PMX temporary-output verification."""

from pathlib import Path
import tempfile
import unittest

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.io.pmx_exporter import PmxExporter
from mmd_tools.validation.output_verifier import verify_model_output


def _valid_model_data():
    """Return a small payload accepted by the PMX writer."""
    return {
        "model_name": "OutputVerifierFixture",
        "vertices": [
            {"position": [0.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [0.0, 0.0], "bone_indices": [0]},
            {"position": [1.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [1.0, 0.0], "bone_indices": [0]},
            {"position": [0.0, 1.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [0.0, 1.0], "bone_indices": [0]},
        ],
        "faces": [[0, 1, 2]],
        "materials": [{"name": "Default", "face_count": 3}],
        "bones": None,
    }


class OutputVerifierTests(unittest.TestCase):
    """Verify output headers, parser acceptance, and section counts."""

    def test_real_python_pmx_output_passes(self):
        model_data = _valid_model_data()
        with tempfile.TemporaryDirectory() as directory:
            pmx_path = Path(directory) / "fixture.pmx"
            PmxExporter(native_parts_exporter=None).export_pmx_model(str(pmx_path), model_data)

            report = verify_model_output(str(pmx_path), "pmx", model_data)
            self.assertTrue(report.valid)
            self.assertEqual(report.issues, ())

    def test_missing_empty_and_invalid_headers_are_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.pmx"
            empty = Path(directory) / "empty.pmx"
            invalid = Path(directory) / "invalid.pmx"
            empty.write_bytes(b"")
            invalid.write_bytes(b"not a pmx")

            self.assertEqual(verify_model_output(str(missing), "pmx").issues[0].code, "OUTPUT_VERIFY_FAILED")
            self.assertEqual(verify_model_output(str(empty), "pmx").issues[0].code, "OUTPUT_VERIFY_FAILED")
            self.assertEqual(verify_model_output(str(invalid), "pmx").issues[0].code, "OUTPUT_VERIFY_FAILED")

    def test_valid_header_with_truncated_payload_reports_parse_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "truncated.pmx"
            output_path.write_bytes(b"PMX \x00")

            report = verify_model_output(str(output_path), "pmx")

            self.assertTrue(report.is_blocking)
        self.assertEqual(report.issues[0].code, "OUTPUT_VERIFY_FAILED")

    def test_section_count_mismatch_is_blocking(self):
        model_data = _valid_model_data()
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "fixture.pmx"
            PmxExporter(native_parts_exporter=None).export_pmx_model(str(output_path), model_data)
            expected = dict(model_data)
            expected["vertices"] = list(model_data["vertices"]) + [model_data["vertices"][0]]

            report = verify_model_output(str(output_path), "pmx", expected)

            self.assertTrue(report.is_blocking)
            self.assertEqual(report.issues[0].code, "OUTPUT_VERIFY_FAILED")
            self.assertIn("expected count 4", report.issues[0].reason)
            self.assertEqual(report.issues[0].details["section"], "vertex")
            self.assertEqual(report.issues[0].details["expected_count"], 4)
            self.assertEqual(report.issues[0].details["actual_count"], 3)

    def test_explicit_empty_material_table_matches_writer_default(self):
        model_data = _valid_model_data()
        model_data["materials"] = []
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "default-material.pmx"
            PmxExporter(native_parts_exporter=None).export_pmx_model(str(output_path), model_data)

            report = verify_model_output(str(output_path), "pmx", model_data)

            self.assertTrue(report.valid)
            self.assertEqual(report.issues, ())

    def test_unknown_format_is_blocking(self):
        report = verify_model_output("unused.asset", "obj")

        self.assertTrue(report.is_blocking)
        self.assertEqual(report.issues[0].code, "EXPORT_OPTIONS_INVALID")


if __name__ == "__main__":
    unittest.main()
