"""Tests for registered VMD runtime provenance payloads."""

import hashlib
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from mmd_tools.converters.vmd_runtime_provenance import (
    _scene_provenance_json,
    build_raw_bone_interpolation_provenance,
    build_raw_ik_provenance,
    build_raw_vmd_source_provenance,
    build_runtime_registration_provenance,
    materialize_raw_bone_source_provenance,
    store_runtime_registration_provenance,
)
from mmd_tools.core.vmd_data import VmdData
from mmd_tools.core.vmd_data.ik_show_hide_frame import VmdIKShowHideFrame


class TestVmdRuntimeProvenance(unittest.TestCase):
    """Raw sources and native artifact identity remain explicit."""

    def test_builds_model_paired_registered_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime_path = Path(directory) / "mmd_runtime_ffi.dll"
            runtime_path.write_bytes(b"runtime")

            result = build_runtime_registration_provenance(
                vmd_bytes=b"vmd",
                pmx_bytes=b"pmx",
                vmd_source_path="motion.vmd",
                pmx_source_path="model.pmx",
                runtime_library_path=runtime_path,
                runtime_abi_version=3,
                runtime_feature_flags=0x1F,
            )

        self.assertEqual(result["registration_mode"], "model_paired_registered")
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["fallback"], "none")
        self.assertEqual(result["raw_vmd_sha256"], hashlib.sha256(b"vmd").hexdigest())
        self.assertEqual(result["pmx_sha256"], hashlib.sha256(b"pmx").hexdigest())
        self.assertEqual(
            result["runtime_library_sha256"],
            hashlib.sha256(b"runtime").hexdigest(),
        )
        self.assertEqual(result["runtime_abi_version"], 3)
        self.assertEqual(result["runtime_feature_flags"], 0x1F)

    def test_missing_optional_paths_are_nonfatal(self):
        result = build_runtime_registration_provenance(
            vmd_bytes=b"vmd",
            pmx_bytes=b"pmx",
            vmd_source_path=None,
            pmx_source_path=None,
            runtime_library_path=None,
            runtime_abi_version=0,
            runtime_feature_flags=0,
        )

        self.assertEqual(result["raw_vmd_path"], "")
        self.assertEqual(result["pmx_path"], "")
        self.assertEqual(result["runtime_library_sha256"], "")

    def test_serializes_complete_raw_bone_interpolation_records(self):
        result = build_raw_bone_interpolation_provenance(
            [
                {
                    "bone_name": "腕",
                    "frame_number": 10,
                    "interpolation": [10] * 64,
                },
                {
                    "bone_name": "腕",
                    "frame_number": 0,
                    "interpolation": bytes([20]) * 64,
                },
            ]
        )

        self.assertTrue(result["available"])
        self.assertTrue(result["complete"])
        self.assertEqual(result["key_count"], 2)
        self.assertEqual([record["frame_number"] for record in result["records"]], [0, 10])
        self.assertEqual(result["records"][0]["interpolation"], [20] * 64)

    def test_malformed_raw_interpolation_is_incomplete_after_duplicate_collapse(self):
        result = build_raw_bone_interpolation_provenance(
            [
                {"bone_name": "腕", "frame_number": 0, "interpolation": [20] * 64},
                {"bone_name": "腕", "frame_number": 0, "interpolation": [21] * 64},
                {"bone_name": "足", "frame_number": 1, "interpolation": [20] * 63},
            ]
        )

        self.assertFalse(result["complete"])
        self.assertEqual(result["key_count"], 1)
        self.assertEqual(result["source_key_count"], 3)
        self.assertEqual(result["duplicate_key_count"], 1)
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(result["records"][0]["interpolation"], [21] * 64)

    def test_duplicate_keys_are_canonicalized_last_record_wins(self):
        result = build_raw_bone_interpolation_provenance(
            [
                {
                    "bone_name": "腕",
                    "frame_number": 0,
                    "position": (0.0, 0.0, 0.0),
                    "rotation": (0.0, 0.0, 0.0, 1.0),
                    "interpolation": [20] * 64,
                },
                {
                    "bone_name": "腕",
                    "frame_number": 0,
                    "position": (1.0, 2.0, 3.0),
                    "rotation": (0.0, 0.0, 1.0, 0.0),
                    "interpolation": [21] * 64,
                },
            ]
        )

        self.assertTrue(result["complete"])
        self.assertTrue(result["transform_complete"])
        self.assertEqual(result["key_count"], 1)
        self.assertEqual(result["source_key_count"], 2)
        self.assertEqual(result["duplicate_key_count"], 1)
        self.assertEqual(result["records"][0]["position"], [1.0, 2.0, 3.0])
        self.assertEqual(result["records"][0]["interpolation"], [21] * 64)

    def test_empty_bone_names_are_ignored_as_unbindable(self):
        result = build_raw_bone_interpolation_provenance(
            [
                {
                    "bone_name": "",
                    "frame_number": 0,
                    "position": (0.0, 0.0, 0.0),
                    "rotation": (0.0, 0.0, 0.0, 1.0),
                    "interpolation": [20] * 64,
                },
                {
                    "bone_name": "腕",
                    "frame_number": 0,
                    "position": (0.0, 0.0, 0.0),
                    "rotation": (0.0, 0.0, 0.0, 1.0),
                    "interpolation": [20] * 64,
                },
            ]
        )

        self.assertTrue(result["complete"])
        self.assertTrue(result["transform_complete"])
        self.assertEqual(result["key_count"], 1)
        self.assertEqual(result["source_key_count"], 2)
        self.assertEqual(result["ignored_key_count"], 1)

    def test_runtime_profile_contains_raw_interpolation_authority(self):
        result = build_runtime_registration_provenance(
            vmd_bytes=b"vmd",
            pmx_bytes=b"pmx",
            vmd_source_path="motion.vmd",
            pmx_source_path="model.pmx",
            runtime_library_path=None,
            runtime_abi_version=3,
            runtime_feature_flags=1,
            raw_bone_frames=[
                {
                    "bone_name": "センター",
                    "frame_number": 0,
                    "position": (1.0, 2.0, 3.0),
                    "rotation": (0.0, 0.0, 0.0, 1.0),
                    "interpolation": [20] * 64,
                }
            ],
        )

        self.assertTrue(result["raw_bone_interpolation_complete"])
        self.assertTrue(result["raw_bone_transform_complete"])
        self.assertEqual(result["raw_bone_key_count"], 1)
        self.assertEqual(result["raw_bone_interpolation"][0]["interpolation"], [20] * 64)
        self.assertEqual(result["raw_bone_interpolation"][0]["position"], [1.0, 2.0, 3.0])

    def test_serializes_exact_raw_ik_authority(self):
        result = build_raw_ik_provenance(
            [
                {
                    "frame_number": 12,
                    "visible": 0,
                    "ik_states": [("左足ＩＫ", 0), ("右足ＩＫ", 1)],
                },
                {
                    "frame_number": 3,
                    "visible": 1,
                    "ik_states": [],
                },
            ]
        )

        self.assertTrue(result["complete"])
        self.assertEqual(result["key_count"], 2)
        self.assertEqual(result["records"][0]["frame_number"], 12)
        self.assertEqual(
            result["records"][0]["ik_states"],
            [["左足ＩＫ", 0], ["右足ＩＫ", 1]],
        )

    def test_malformed_raw_ik_authority_is_incomplete(self):
        result = build_raw_ik_provenance(
            [{"frame_number": 0, "visible": 1, "ik_states": [("左足ＩＫ", 2)]}]
        )

        self.assertFalse(result["complete"])
        self.assertEqual(result["key_count"], 0)
        self.assertEqual(result["source_key_count"], 1)

    def test_legacy_profile_keeps_raw_source_authority_without_runtime_identity(self):
        result = build_raw_vmd_source_provenance(
            vmd_bytes=b"vmd",
            pmx_bytes=None,
            vmd_source_path="motion.vmd",
            pmx_source_path="model.pmx",
            raw_bone_frames=[
                {"bone_name": "センター", "frame_number": 0, "interpolation": [20] * 64}
            ],
        )

        self.assertEqual(result["registration_mode"], "raw_vmd_source")
        self.assertEqual(result["fallback"], "legacy")
        self.assertEqual(result["runtime_abi_version"], 0)
        self.assertTrue(result["raw_bone_interpolation_complete"])

    @patch("mmd_tools.converters.vmd_runtime_provenance.maya_attribute_utils.set_attribute")
    @patch("mmd_tools.converters.vmd_runtime_provenance.cmds")
    def test_stores_provenance_on_model_root(self, cmds_mock, set_attribute_mock):
        cmds_mock.objExists.return_value = True
        cmds_mock.attributeQuery.return_value = False
        payload = {"registration_mode": "model_paired_registered", "status": "success"}

        stored = store_runtime_registration_provenance("modelRoot", payload)

        self.assertTrue(stored)
        cmds_mock.addAttr.assert_called_once_with(
            "modelRoot",
            longName="mmd_vmd_import_provenance_json",
            dataType="string",
        )
        args = set_attribute_mock.call_args.args
        self.assertEqual(args[0:2], ("modelRoot", "mmd_vmd_import_provenance_json"))
        self.assertIn('"registration_mode":"model_paired_registered"', args[2])
        self.assertEqual(args[3], "string")

    @patch("mmd_tools.converters.vmd_runtime_provenance._MAX_EMBEDDED_PROVENANCE_BYTES", 1)
    def test_oversized_provenance_stores_verified_source_reference(self):
        source_path = Path("tests/data/mmt_test_model_test_motion.vmd").resolve()
        source_bytes = source_path.read_bytes()
        source = VmdData().parse_file(str(source_path))
        payload = build_runtime_registration_provenance(
            vmd_bytes=source_bytes,
            pmx_bytes=b"pmx",
            vmd_source_path=str(source_path),
            pmx_source_path="model.pmx",
            runtime_library_path=None,
            runtime_abi_version=0,
            runtime_feature_flags=0,
            raw_bone_frames=source.bone_frames,
        )

        compact = json.loads(_scene_provenance_json(payload))

        self.assertNotIn("raw_bone_interpolation", compact)
        self.assertEqual(
            compact["raw_bone_interpolation_storage"],
            "source_vmd_reference",
        )
        restored = materialize_raw_bone_source_provenance(compact)
        self.assertIsNotNone(restored)
        self.assertEqual(
            restored["raw_bone_key_count"],
            len(source.bone_frames),
        )
        self.assertEqual(
            len(restored["raw_bone_interpolation"]),
            len(source.bone_frames),
        )

    @patch("mmd_tools.converters.vmd_runtime_provenance._MAX_EMBEDDED_PROVENANCE_BYTES", 1)
    def test_external_provenance_rejects_changed_source(self):
        source_path = Path("tests/data/mmt_test_model_test_motion.vmd").resolve()
        source = VmdData().parse_file(str(source_path))
        payload = build_runtime_registration_provenance(
            vmd_bytes=b"not-the-source",
            pmx_bytes=b"pmx",
            vmd_source_path=str(source_path),
            pmx_source_path="model.pmx",
            runtime_library_path=None,
            runtime_abi_version=0,
            runtime_feature_flags=0,
            raw_bone_frames=source.bone_frames,
        )
        compact = json.loads(_scene_provenance_json(payload))

        self.assertIsNone(materialize_raw_bone_source_provenance(compact))

    @patch("mmd_tools.converters.vmd_runtime_provenance._MAX_EMBEDDED_PROVENANCE_BYTES", 1)
    def test_external_provenance_materializes_exact_ik_after_identity_check(self):
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "source.vmd"
            source = VmdData().parse_file("tests/data/mmt_test_model_test_motion.vmd")
            ik_frame = VmdIKShowHideFrame()
            ik_frame.frame_number = 9
            ik_frame.visible = 0
            ik_frame.ik_states = [("左足ＩＫ", 0), ("右足ＩＫ", 1)]
            ik_frame.ik_count = len(ik_frame.ik_states)
            source.ik_show_hide_frames = [ik_frame]
            source.write_file(str(source_path))
            source_bytes = source_path.read_bytes()
            parsed = VmdData().parse_file(str(source_path))
            payload = build_runtime_registration_provenance(
                vmd_bytes=source_bytes,
                pmx_bytes=b"pmx",
                vmd_source_path=str(source_path),
                pmx_source_path="model.pmx",
                runtime_library_path=None,
                runtime_abi_version=0,
                runtime_feature_flags=0,
                raw_bone_frames=parsed.bone_frames,
                raw_ik_frames=parsed.ik_show_hide_frames,
            )

            compact = json.loads(_scene_provenance_json(payload))
            restored = materialize_raw_bone_source_provenance(compact)

        self.assertEqual(compact["raw_ik_storage"], "source_vmd_reference")
        self.assertEqual(restored["raw_ik_key_count"], 1)
        self.assertEqual(restored["raw_ik_frames"][0]["frame_number"], 9)
        self.assertEqual(
            restored["raw_ik_frames"][0]["ik_states"],
            [["左足ＩＫ", 0], ["右足ＩＫ", 1]],
        )


if __name__ == "__main__":
    unittest.main()
