"""Native from-parts sibling-stage contracts for one-shot VMD export."""

from pathlib import Path
import tempfile
from unittest import mock
import unittest

from mmd_tools.actions import vmd_sibling_stage
from mmd_tools.actions.vmd_sibling_stage import (
    VmdSiblingStageError,
    VmdSiblingStageSession,
)
from mmd_tools.core.vmd_data import VmdData


class VmdSiblingStageTests(unittest.TestCase):
    """Keep the compact native writer boundary covered without a legacy action."""

    def test_native_stage_preserves_all_sections_cp932_names_and_interpolation(self):
        bone_name = "センター"
        morph_name = "笑い"
        bone_raw = bone_name.encode("cp932")
        morph_raw = morph_name.encode("cp932")
        bone_interpolation = bytes(range(64))
        camera_interpolation = bytes(range(24))

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "motion.vmd"
            stage = VmdSiblingStageSession("モデル", target_path=str(target))
            try:
                stage.write_frame(
                    "bones",
                    {
                        "bone_name": bone_raw,
                        "frame": 2,
                        "position": (1.0, 2.0, 3.0),
                        "rotation": (0.0, 0.0, 0.0, 1.0),
                        "interpolation": bone_interpolation,
                    },
                )
                stage.write_frame(
                    "morphs", {"morph_name": morph_raw, "frame": 3, "weight": 0.5}
                )
                stage.write_frame(
                    "cameras",
                    {
                        "frame": 4,
                        "distance": 42.0,
                        "position": (4.0, 5.0, 6.0),
                        "rotation": (0.1, 0.2, 0.3),
                        "interpolation": camera_interpolation,
                        "fov": 30,
                        "perspective": True,
                    },
                )
                stage.write_frame(
                    "lights", {"frame": 5, "color": (0.1, 0.2, 0.3), "position": (7.0, 8.0, 9.0)}
                )
                stage.write_frame("shadows", {"frame": 6, "mode": 2, "distance": 11.0})
                stage.write_frame(
                    "ik",
                    {"frame": 7, "visible": True, "ik_states": [("足IK".encode("cp932"), True)]},
                )
                summary = stage.finish_collection()
                output = Path(stage.file_path).read_bytes()
                parsed = VmdData().parse_file(stage.file_path)
            finally:
                stage.cleanup()

        self.assertEqual(
            summary.counts,
            {"bones": 1, "morphs": 1, "cameras": 1, "lights": 1, "shadows": 1, "ik": 1},
        )
        self.assertEqual(output[54:69], bone_raw.ljust(15, b"\x00"))
        self.assertEqual(output[169:184], morph_raw.ljust(15, b"\x00"))
        self.assertEqual([len(getattr(parsed, name)) for name in (
            "bone_frames", "morph_frames", "camera_frames", "light_frames",
            "shadow_frames", "ik_show_hide_frames",
        )], [1, 1, 1, 1, 1, 1])
        self.assertEqual(parsed.bone_frames[0].bone_name, bone_name)
        self.assertEqual(parsed.morph_frames[0].morph_name, morph_name)
        self.assertEqual(parsed.bone_frames[0].interpolation, bone_interpolation)
        self.assertEqual(parsed.camera_frames[0].interpolation, camera_interpolation)
        self.assertEqual(parsed.ik_show_hide_frames[0].ik_states, [("足IK", 1)])

    def test_native_stage_rejects_invalid_payload_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            stage = VmdSiblingStageSession(
                "モデル", target_path=str(Path(directory) / "motion.vmd")
            )
            try:
                with self.assertRaisesRegex(
                    VmdSiblingStageError, "interpolation must contain exactly 64 bytes"
                ):
                    stage.write_frame(
                        "bones",
                        {
                            "bone_name": "センター",
                            "frame": 0,
                            "interpolation": b"too short",
                        },
                    )
                self.assertEqual(stage.file_path and Path(stage.file_path).read_bytes(), b"")
            finally:
                stage.cleanup()

    def test_native_stage_fails_fast_when_sibling_open_is_denied(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "motion.vmd"
            with mock.patch.object(
                vmd_sibling_stage.os,
                "open",
                side_effect=PermissionError("protected output directory"),
            ) as open_mock:
                with self.assertRaisesRegex(
                    VmdSiblingStageError, "could not create temporary VMD sibling"
                ):
                    VmdSiblingStageSession("モデル", target_path=str(target))

            # CPython's tempfile implementation retries this error when its
            # writable-directory heuristic says the path is usable.  The
            # export boundary must stop after the first denied open.
            self.assertEqual(open_mock.call_count, 1)

    def test_native_stage_bounds_sibling_name_collisions(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "motion.vmd"
            with mock.patch.object(
                vmd_sibling_stage.os,
                "open",
                side_effect=FileExistsError("candidate already exists"),
            ) as open_mock:
                with self.assertRaisesRegex(
                    VmdSiblingStageError, "could not create a unique temporary VMD sibling"
                ):
                    VmdSiblingStageSession("モデル", target_path=str(target))

            self.assertEqual(open_mock.call_count, vmd_sibling_stage._MAX_TEMPFILE_ATTEMPTS)

    def test_native_stage_truncates_cp932_names_at_character_boundaries(self):
        model_name = "M" * 19 + "モ"
        bone_name = "B" * 14 + "骨"
        morph_name = "P" * 14 + "笑"
        with tempfile.TemporaryDirectory() as directory:
            stage = VmdSiblingStageSession(
                model_name, target_path=str(Path(directory) / "motion.vmd")
            )
            try:
                stage.write_frame("bones", {"bone_name": bone_name, "frame": 0})
                stage.write_frame("morphs", {"morph_name": morph_name, "frame": 0})
                stage.finish_collection()
                output = Path(stage.file_path).read_bytes()
                parsed = VmdData().parse_file(stage.file_path)
            finally:
                stage.cleanup()

        self.assertEqual(output[30:50], ("M" * 19).encode("cp932") + b"\x00")
        self.assertEqual(output[54:69], ("B" * 14).encode("cp932") + b"\x00")
        self.assertEqual(output[169:184], ("P" * 14).encode("cp932") + b"\x00")
        self.assertEqual(parsed.header.model_name, "M" * 19)
        self.assertEqual(parsed.bone_frames[0].bone_name, "B" * 14)
        self.assertEqual(parsed.morph_frames[0].morph_name, "P" * 14)


if __name__ == "__main__":
    unittest.main()
