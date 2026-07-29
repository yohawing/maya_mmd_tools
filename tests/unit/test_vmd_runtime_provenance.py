"""Tests for registered VMD runtime provenance payloads."""

import hashlib
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from mmd_tools.converters.vmd_runtime_provenance import (
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
