import ctypes
import json
import unittest

from mmd_tools.core.native.mmd_anim_runtime_export import (
    MmdAnimRuntimeExportError,
    export_vmd_from_parts,
)
from mmd_tools.core.native.mmd_anim_runtime_types import MmdRuntimeFfiByteBuffer


class _FakeNativeLibrary:
    def __init__(self, payload=b"VMD"):
        self.payload = payload
        self.storage = None
        self.free_count = 0
        self.calls = []
        self.error = b"invalid input"

    def mmd_runtime_export_vmd_from_parts(self, *args):
        self.calls.append(args)
        if self.payload is None:
            return MmdRuntimeFfiByteBuffer()
        self.storage = (ctypes.c_uint8 * len(self.payload)).from_buffer_copy(self.payload)
        return MmdRuntimeFfiByteBuffer(self.storage, len(self.payload))

    def mmd_runtime_byte_buffer_free(self, buffer):
        self.free_count += 1

    def mmd_runtime_last_error_message(self):
        return self.error


class _MissingVmdPartsLibrary:
    def mmd_runtime_byte_buffer_free(self, buffer):
        pass


def _metadata():
    return {
        "schema": "mmd-anim-vmd-parts",
        "version": 1,
        "modelName": "モデル",
        "modelNameBytes": [131, 130, 131, 102, 131, 139],
        "boneNames": [],
        "morphNames": [],
        "cameraFrames": [],
        "lightFrames": [],
        "selfShadowFrames": [],
        "propertyFrames": [],
    }


class MmdAnimRuntimeExportTests(unittest.TestCase):
    def test_typed_parts_passes_explicit_lengths_and_frees_once(self):
        lib = _FakeNativeLibrary(b"native-vmd")
        result = export_vmd_from_parts(
            _metadata(),
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            get_library=lambda: lib,
        )

        self.assertEqual(result, b"native-vmd")
        self.assertEqual(lib.free_count, 1)
        self.assertEqual(len(lib.calls), 1)
        self.assertEqual(len(lib.calls[0]), 18)
        self.assertEqual([lib.calls[0][1], lib.calls[0][3], lib.calls[0][5]], [len(json.dumps(_metadata(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")), 0, 0])

    def test_native_empty_buffer_is_fail_closed_and_freed_once(self):
        lib = _FakeNativeLibrary(None)
        with self.assertRaisesRegex(MmdAnimRuntimeExportError, "invalid input"):
            export_vmd_from_parts(
                _metadata(), [], [], [], [], [], [], [], [], get_library=lambda: lib
            )
        self.assertEqual(lib.free_count, 1)

    def test_missing_vmd_parts_symbol_is_fail_closed(self):
        with self.assertRaisesRegex(MmdAnimRuntimeExportError, "required ABI symbol is missing"):
            export_vmd_from_parts(
                _metadata(), [], [], [], [], [], [], [], [],
                get_library=lambda: _MissingVmdPartsLibrary(),
            )

    def test_bool_and_non_finite_values_are_rejected_before_ffi(self):
        lib = _FakeNativeLibrary(b"unused")
        with self.assertRaises(MmdAnimRuntimeExportError):
            export_vmd_from_parts(
                _metadata(), [True], [0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0], [0] * 64,
                [], [], [], get_library=lambda: lib
            )
        with self.assertRaises(MmdAnimRuntimeExportError):
            export_vmd_from_parts(
                _metadata(), [], [], [float("nan")], [], [], [], [], [], get_library=lambda: lib
            )
        self.assertEqual(lib.calls, [])


if __name__ == "__main__":
    unittest.main()
