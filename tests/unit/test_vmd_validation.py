"""VMD current-scene Bake Timeline validation and atomic export contracts."""

import hashlib
import math
from pathlib import Path
import struct
import tempfile
import unittest
from types import SimpleNamespace

from mmd_tools.core.vmd_data import VmdData
from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame
from mmd_tools.validation.vmd_validator import (
    VMD_EXPORT_BAKE_TIMELINE,
    validate_vmd_data,
    verify_vmd_output,
    verify_vmd_output_streaming,
)
from tests.common.vmd_parts_export_oracle import export_vmd_from_parts_oracle


def _valid_bone_frame() -> VmdBoneFrame:
    """Return one finite bone frame suitable for a structural fixture."""
    frame = VmdBoneFrame()
    frame.bone_name = "センター"
    frame.rotation = (0.0, 0.0, 0.0, 1.0)
    return frame


def _stream_counts(*, bones, morphs, cameras, lights, shadows, ik):
    """Return the six canonical section counts expected by the stream verifier."""
    return {
        "bones": len(bones),
        "morphs": len(morphs),
        "cameras": len(cameras),
        "lights": len(lights),
        "shadows": len(shadows),
        "ik": len(ik),
    }


def _stream_bounds(*, bones, morphs, cameras, lights, shadows, ik):
    """Return canonical per-section frame bounds for the stream verifier."""
    def bounds(frames):
        return SimpleNamespace(
            minimum=min(frames) if frames else None,
            maximum=max(frames) if frames else None,
        )

    return {
        "bones": bounds(bones),
        "morphs": bounds(morphs),
        "cameras": bounds(cameras),
        "lights": bounds(lights),
        "shadows": bounds(shadows),
        "ik": bounds(ik),
    }


def _write_stream_fixture(
    path: Path,
    *,
    bones=(4,),
    morphs=(8,),
    cameras=(12,),
    lights=(16,),
    shadows=(20,),
    ik=(24,),
    bone_name="センター",
    morph_name="笑い",
) -> bytes:
    """Write a canonical all-section fixture through the shared VMD oracle."""
    metadata = {
        "modelName": "モデル",
        "boneNames": [{"name": bone_name}],
        "morphNames": [{"name": morph_name}],
        "cameraFrames": [
            {"frame": frame, "interpolation": bytes(24)} for frame in cameras
        ],
        "lightFrames": [{"frame": frame} for frame in lights],
        "selfShadowFrames": [{"frame": frame} for frame in shadows],
        "propertyFrames": [
            {
                "frame": frame,
                "visible": True,
                "ikStates": [{"boneName": "足IK", "enabled": True}],
            }
            for frame in ik
        ],
    }
    data = export_vmd_from_parts_oracle(
        metadata,
        [0] * len(bones),
        list(bones),
        [0.0] * (3 * len(bones)),
        [0.0, 0.0, 0.0, 1.0] * len(bones),
        bytes(range(64)) * len(bones),
        [0] * len(morphs),
        list(morphs),
        [0.25] * len(morphs),
    )
    path.write_bytes(data)
    return data


class TestVmdValidator(unittest.TestCase):
    """VMD payload issue codes remain deterministic and fail closed."""

    def test_empty_bake_timeline_payload_is_ready(self):
        report = validate_vmd_data(VmdData(), VMD_EXPORT_BAKE_TIMELINE)

        self.assertTrue(report.valid)
        self.assertEqual(report.mode, VMD_EXPORT_BAKE_TIMELINE)
        self.assertEqual(report.to_dict()["status"], "ready")

    def test_payload_type_and_frame_range_emit_required_v2_details(self):
        payload_report = validate_vmd_data(object(), VMD_EXPORT_BAKE_TIMELINE)
        self.assertEqual(
            payload_report.issues[0].details,
            {
                "field": "animation_data",
                "expected_type": "VmdData",
                "actual_type": "object",
            },
        )

        range_report = validate_vmd_data(
            VmdData(), VMD_EXPORT_BAKE_TIMELINE, frame_range=(2, 1)
        )
        self.assertEqual(range_report.issues[0].details["frame_range"], [2, 1])

    def test_invalid_bone_payload_reports_all_relevant_contracts(self):
        data = VmdData()
        frame = _valid_bone_frame()
        frame.frame_number = -1
        frame.rotation = (math.nan, 0.0, 0.0, 0.0)
        frame.interpolation = b"short"
        data.bone_frames.append(frame)

        report = validate_vmd_data(data, VMD_EXPORT_BAKE_TIMELINE, frame_range=(0, 10))
        codes = [issue.code for issue in report.issues]

        self.assertEqual(
            codes,
            [
                "INPUT_INVALID",
                "INPUT_INVALID",
                "INPUT_INVALID",
                "INPUT_INVALID",
                "INPUT_INVALID",
            ],
        )
        self.assertEqual(report.issues[0].path, "bone_frames[0].frame_number")

    def test_verify_output_parses_vmd_written_by_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "motion.vmd"
            data = VmdData()
            data.bone_frames.append(_valid_bone_frame())
            data.write_file(path)

            report = verify_vmd_output(str(path), VMD_EXPORT_BAKE_TIMELINE)

        self.assertTrue(report.valid)
        self.assertEqual(report.export_format, "vmd")

    def test_verify_output_rejects_section_count_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "motion.vmd"
            VmdData().write_file(path)

            report = verify_vmd_output(
                str(path),
                VMD_EXPORT_BAKE_TIMELINE,
                expected_counts={"bone_frames": 1},
            )

        self.assertEqual(report.issues[0].code, "OUTPUT_VERIFY_FAILED")
        self.assertEqual(
            report.issues[0].details,
            {
                "section": "bone_frames",
                "expected_count": 1,
                "actual_count": 0,
                "aggregation_discriminator": "output_count",
                "field": "output.bone_frames",
                "phase": "verify",
            },
        )

    def test_streaming_verifier_matches_canonical_export_receipt(self):
        bones, morphs = (4,), (8,)
        cameras, lights, shadows, ik = (), (), (), ()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stream.vmd"
            source = _write_stream_fixture(
                path,
                bones=bones,
                morphs=morphs,
                cameras=cameras,
                lights=lights,
                shadows=shadows,
                ik=ik,
            )

            streaming = verify_vmd_output_streaming(
                str(path),
                VMD_EXPORT_BAKE_TIMELINE,
                expected_counts=_stream_counts(
                    bones=bones,
                    morphs=morphs,
                    cameras=cameras,
                    lights=lights,
                    shadows=shadows,
                    ik=ik,
                ),
                expected_bounds=_stream_bounds(
                    bones=bones,
                    morphs=morphs,
                    cameras=cameras,
                    lights=lights,
                    shadows=shadows,
                    ik=ik,
                ),
                expected_sha256=hashlib.sha256(source).hexdigest(),
                expected_size=len(source),
            )
            legacy = verify_vmd_output(str(path), VMD_EXPORT_BAKE_TIMELINE)

        self.assertTrue(streaming.valid, streaming.issues)
        self.assertEqual(streaming.issues, legacy.issues)

    def test_streaming_verifier_rejects_noncanonical_global_frame_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stream.vmd"
            _write_stream_fixture(
                path,
                bones=(),
                morphs=(8,),
                cameras=(),
                lights=(),
                shadows=(),
                ik=(),
            )

            report = verify_vmd_output_streaming(
                str(path),
                VMD_EXPORT_BAKE_TIMELINE,
                expected_bounds=(8, 8),
            )

        self.assertFalse(report.valid)
        self.assertIn("INPUT_INVALID", [issue.code for issue in report.issues])

    def test_streaming_verifier_rejects_malformed_canonical_metadata(self):
        bones, morphs, cameras, lights, shadows, ik = (), (), (), (), (), ()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stream.vmd"
            _write_stream_fixture(
                path,
                bones=bones,
                morphs=morphs,
                cameras=cameras,
                lights=lights,
                shadows=shadows,
                ik=ik,
            )
            counts = _stream_counts(
                bones=bones,
                morphs=morphs,
                cameras=cameras,
                lights=lights,
                shadows=shadows,
                ik=ik,
            )
            incomplete_counts = dict(counts)
            incomplete_counts.pop("ik")
            boolean_counts = dict(counts)
            boolean_counts["bones"] = True
            mapping_bounds = _stream_bounds(
                bones=bones,
                morphs=morphs,
                cameras=cameras,
                lights=lights,
                shadows=shadows,
                ik=ik,
            )
            mapping_bounds["bones"] = {"minimum": None, "maximum": None}

            malformed = (
                {"expected_counts": incomplete_counts},
                {"expected_counts": boolean_counts},
                {"expected_counts": {"bone_frames": 0}},
                {"expected_bounds": mapping_bounds},
            )
            for metadata in malformed:
                with self.subTest(metadata=metadata):
                    report = verify_vmd_output_streaming(
                        str(path),
                        VMD_EXPORT_BAKE_TIMELINE,
                        **metadata,
                    )
                    self.assertFalse(report.valid)
                    issue = report.issues[0]
                    required_key = (
                        "expected_bounds_contract"
                        if "expected_bounds" in metadata
                        else "expected_count_contract"
                    )
                    self.assertIn(required_key, issue.details)

    def test_streaming_verifier_enforces_inclusive_frame_range_for_all_sections(self):
        section_names = ("bones", "morphs", "cameras", "lights", "shadows", "ik")
        for section in section_names:
            for frame in (9, 11):
                with self.subTest(section=section, frame=frame), tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "stream.vmd"
                    frames = {name: (frame if name == section else 10,) for name in section_names}
                    _write_stream_fixture(path, **frames)

                    report = verify_vmd_output_streaming(
                        str(path),
                        VMD_EXPORT_BAKE_TIMELINE,
                        expected_frame_range=(10, 10),
                    )

                self.assertFalse(report.valid)
                range_issues = [issue for issue in report.issues if issue.code == "OUTPUT_VERIFY_FAILED"]
                self.assertEqual(len(range_issues), 1)
                self.assertIn("frame_number", range_issues[0].path)
                wire_sections = {
                    "bones": "bone_frames",
                    "morphs": "morph_frames",
                    "cameras": "camera_frames",
                    "lights": "light_frames",
                    "shadows": "shadow_frames",
                    "ik": "ik_show_hide_frames",
                }
                self.assertEqual(
                    range_issues[0].details["section"], wire_sections[section]
                )
                self.assertEqual(range_issues[0].details["expected_bounds"], [10, 10])

    def test_streaming_verifier_accepts_inclusive_frame_range_endpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stream.vmd"
            _write_stream_fixture(
                path,
                bones=(),
                morphs=(10,),
                cameras=(),
                lights=(),
                shadows=(),
                ik=(),
            )

            report = verify_vmd_output_streaming(
                str(path),
                VMD_EXPORT_BAKE_TIMELINE,
                expected_frame_range=(10, 10),
            )

        self.assertTrue(report.valid, report.issues)

    def test_streaming_verifier_rejects_malformed_expected_frame_range(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stream.vmd"
            _write_stream_fixture(
                path,
                bones=(),
                morphs=(),
                cameras=(),
                lights=(),
                shadows=(),
                ik=(),
            )
            for frame_range in (
                (),
                (1,),
                (1, 2, 3),
                (2, 1),
                (-1, 1),
                (True, 1),
                (1.5, 2),
                ("1", 2),
                ("bad", 1),
                {0: 1, 1: 2},
                (0, 0x1_0000_0000),
            ):
                with self.subTest(frame_range=frame_range):
                    report = verify_vmd_output_streaming(
                        str(path),
                        VMD_EXPORT_BAKE_TIMELINE,
                        expected_frame_range=frame_range,
                    )
                    self.assertFalse(report.valid)
                    self.assertIn("INPUT_INVALID", [issue.code for issue in report.issues])

    def test_streaming_verifier_rejects_truncation_in_each_section(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stream.vmd"
            source = _write_stream_fixture(path)
            # Header/model (50), then count + record for each fixed section;
            # IK has a 9-byte fixed prefix and one 21-byte state.
            record_ends = (164, 191, 256, 288, 301, 335)
            for index, end in enumerate(record_ends):
                candidate = Path(directory) / "truncated-{}.vmd".format(index)
                candidate.write_bytes(source[:end])
                report = verify_vmd_output_streaming(str(candidate), VMD_EXPORT_BAKE_TIMELINE)
                self.assertFalse(report.valid)
                self.assertIn("OUTPUT_VERIFY_FAILED", [issue.code for issue in report.issues])

    def test_streaming_verifier_requires_declared_empty_tail_sections(self):
        empty = ()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stream.vmd"
            source = _write_stream_fixture(
                path,
                bones=empty,
                morphs=empty,
                cameras=empty,
                lights=empty,
                shadows=empty,
                ik=empty,
            )
            # Preserve the required bone and morph counts, but remove the four
            # empty tail counts emitted by the canonical export fixture.
            path.write_bytes(source[:58])

            report = verify_vmd_output_streaming(
                str(path),
                VMD_EXPORT_BAKE_TIMELINE,
                expected_counts=_stream_counts(
                    bones=empty,
                    morphs=empty,
                    cameras=empty,
                    lights=empty,
                    shadows=empty,
                    ik=empty,
                ),
            )

        self.assertFalse(report.valid)
        self.assertIn("OUTPUT_VERIFY_FAILED", [issue.code for issue in report.issues])

    def test_streaming_verifier_rejects_nonfinite_values_and_wire_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stream.vmd"
            source = _write_stream_fixture(path)
            # Offsets are stable for the one-record fixture above.
            corruptions = (
                (54 + 31, struct.pack("<f", float("nan")), "OUTPUT_VERIFY_FAILED"),
                (169 + 19, struct.pack("<f", float("inf")), "OUTPUT_VERIFY_FAILED"),
                (196 + 60, b"\x02", "OUTPUT_VERIFY_FAILED"),
                (293 + 4, b"\x09", "OUTPUT_VERIFY_FAILED"),
                (306 + 4, b"\x02", "OUTPUT_VERIFY_FAILED"),
                (306 + 9 + 20, b"\x02", "OUTPUT_VERIFY_FAILED"),
                (54, b"\x81 ", "OUTPUT_VERIFY_FAILED"),
            )
            for index, (offset, payload, code) in enumerate(corruptions):
                candidate = Path(directory) / "corrupt-{}.vmd".format(index)
                data = bytearray(source)
                data[offset : offset + len(payload)] = payload
                candidate.write_bytes(data)
                report = verify_vmd_output_streaming(str(candidate), VMD_EXPORT_BAKE_TIMELINE)
                self.assertFalse(report.valid, "corruption {} produced {}".format(index, report.issues))
                self.assertIn(code, [issue.code for issue in report.issues])

    def test_streaming_verifier_reports_trailing_bytes_and_metadata_mismatch(self):
        empty = ()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stream.vmd"
            source = _write_stream_fixture(
                path,
                bones=empty,
                morphs=empty,
                cameras=empty,
                lights=empty,
                shadows=empty,
                ik=empty,
            )
            path.write_bytes(source + b"trailing")
            expected_counts = _stream_counts(
                bones=empty,
                morphs=empty,
                cameras=empty,
                lights=empty,
                shadows=empty,
                ik=empty,
            )
            expected_counts["bones"] = 1

            report = verify_vmd_output_streaming(
                str(path),
                VMD_EXPORT_BAKE_TIMELINE,
                expected_counts=expected_counts,
                expected_sha256="0" * 64,
                expected_size=0,
            )

        codes = [issue.code for issue in report.issues]
        self.assertIn("OUTPUT_VERIFY_FAILED", codes)
        self.assertFalse(report.valid)

    def test_streaming_verifier_bounds_issue_memory_for_many_invalid_records(self):
        empty = ()
        morphs = range(130)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "many-invalid.vmd"
            _write_stream_fixture(
                path,
                bones=empty,
                morphs=morphs,
                cameras=empty,
                lights=empty,
                shadows=empty,
                ik=empty,
                morph_name="",
            )

            report = verify_vmd_output_streaming(str(path), VMD_EXPORT_BAKE_TIMELINE)

        self.assertFalse(report.valid)
        self.assertLessEqual(len(report.issues), 100)


if __name__ == "__main__":
    unittest.main()
