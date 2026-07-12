"""FFI byte-buffer ownership tests for parsed PMX model accessors."""

import ctypes
from ctypes import POINTER, c_uint8, c_void_p
from types import SimpleNamespace
import unittest
from unittest import mock

from mmd_tools.core.native.mmd_anim_runtime_parsed_model import MmdParsedModel
from mmd_tools.core.native.mmd_anim_runtime_types import MmdRuntimeFfiByteBuffer


def _buffer(payload: bytes):
    storage = (c_uint8 * len(payload))(*payload)
    buf = MmdRuntimeFfiByteBuffer(
        data=ctypes.cast(storage, POINTER(c_uint8)),
        len=len(payload),
    )
    buf._test_storage = storage
    return buf


def _null_buffer(length=0):
    return MmdRuntimeFfiByteBuffer(data=None, len=length)


class TestParsedModelBufferOwnership(unittest.TestCase):
    def _model(self, *, metadata=None, names=None, count=0, free=None):
        lib = SimpleNamespace(
            mmd_runtime_parsed_model_metadata_json=mock.Mock(return_value=metadata),
            mmd_runtime_parsed_model_vertex_morph_name=mock.Mock(side_effect=names),
            mmd_runtime_parsed_model_vertex_morph_count=mock.Mock(return_value=count),
            mmd_runtime_byte_buffer_free=free or mock.Mock(),
        )
        return MmdParsedModel(lib, c_void_p(1)), lib

    def test_metadata_success_frees_actual_buffer_once(self):
        buf = _buffer(b'{"ok": true}')
        model, lib = self._model(metadata=buf)

        self.assertEqual(model.metadata_json, '{"ok": true}')
        lib.mmd_runtime_byte_buffer_free.assert_called_once_with(buf)

    def test_metadata_empty_and_null_address_each_free_once(self):
        for buf in (_null_buffer(), _null_buffer(length=4)):
            with self.subTest(length=buf.len):
                model, lib = self._model(metadata=buf)
                self.assertIsNone(model.metadata_json)
                lib.mmd_runtime_byte_buffer_free.assert_called_once_with(buf)

    def test_metadata_ctypes_exception_frees_actual_buffer_once(self):
        buf = _buffer(b"metadata")
        model, lib = self._model(metadata=buf)

        with mock.patch(
            "mmd_tools.core.native.mmd_anim_runtime_parsed_model.ctypes.cast",
            side_effect=RuntimeError("ctypes failed"),
        ):
            self.assertIsNone(model.metadata_json)

        lib.mmd_runtime_byte_buffer_free.assert_called_once_with(buf)

    def test_vertex_morph_mid_loop_exception_frees_each_actual_buffer_once(self):
        first = _buffer("笑顔".encode())
        second = _buffer(b"broken")
        model, lib = self._model(names=[first, second], count=2)
        real_cast = ctypes.cast
        casts = 0

        def cast_then_fail(value, target):
            nonlocal casts
            casts += 1
            if casts == 2:
                raise RuntimeError("ctypes failed")
            return real_cast(value, target)

        with mock.patch(
            "mmd_tools.core.native.mmd_anim_runtime_parsed_model.ctypes.cast",
            side_effect=cast_then_fail,
        ):
            self.assertIsNone(model.vertex_morph_names)

        self.assertEqual(
            lib.mmd_runtime_byte_buffer_free.call_args_list,
            [mock.call(first), mock.call(second)],
        )

    def test_vertex_morph_success_and_empty_free_each_buffer_once(self):
        first = _buffer(b"smile")
        second = _null_buffer()
        model, lib = self._model(names=[first, second], count=2)

        self.assertEqual(model.vertex_morph_names, ["smile", ""])
        self.assertEqual(
            lib.mmd_runtime_byte_buffer_free.call_args_list,
            [mock.call(first), mock.call(second)],
        )

    def test_missing_free_function_preserves_none_contract(self):
        model, lib = self._model(metadata=_buffer(b"metadata"))
        lib.mmd_runtime_byte_buffer_free = None

        self.assertIsNone(model.metadata_json)
        self.assertIsNone(model.vertex_morph_names)

    def test_free_failure_does_not_mask_result_or_primary_error_contract(self):
        success = _buffer(b"metadata")
        failing_free = mock.Mock(side_effect=RuntimeError("free failed"))
        model, _lib = self._model(metadata=success, free=failing_free)
        self.assertEqual(model.metadata_json, "metadata")

        primary = _buffer(b"broken")
        model, _lib = self._model(metadata=primary, free=failing_free)
        with mock.patch(
            "mmd_tools.core.native.mmd_anim_runtime_parsed_model.ctypes.cast",
            side_effect=RuntimeError("primary failure"),
        ):
            self.assertIsNone(model.metadata_json)


if __name__ == "__main__":
    unittest.main()
