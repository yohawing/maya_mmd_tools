"""Tests for registered VMD runtime provenance payloads."""

import hashlib
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from mmd_tools.converters.vmd_runtime_provenance import (
    build_raw_bone_interpolation_provenance,
    build_raw_vmd_source_provenance,
    build_runtime_registration_provenance,
    store_runtime_registration_provenance,
)


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

    def test_malformed_or_duplicate_raw_interpolation_is_incomplete(self):
        result = build_raw_bone_interpolation_provenance(
            [
                {"bone_name": "腕", "frame_number": 0, "interpolation": [20] * 64},
                {"bone_name": "腕", "frame_number": 0, "interpolation": [21] * 64},
                {"bone_name": "足", "frame_number": 1, "interpolation": [20] * 63},
            ]
        )

        self.assertFalse(result["complete"])
        self.assertEqual(result["key_count"], 3)
        self.assertEqual(len(result["records"]), 1)

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


if __name__ == "__main__":
    unittest.main()
