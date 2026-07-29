"""Tests for feature-gated compiled bone-track introspection."""

import ctypes
import math
from unittest import TestCase

from mmd_tools.core.native.mmd_anim_runtime_handles import MmdRuntimeClip
from mmd_tools.core.native.mmd_anim_runtime_signatures import (
    setup_clip_bone_track_signatures,
)
from mmd_tools.core.native.mmd_anim_runtime_types import (
    MMD_RUNTIME_BONE_TRACK_CURVE_CUBIC_BEZIER,
    MMD_RUNTIME_BONE_TRACK_CURVE_NONE,
    MMD_RUNTIME_FEATURE_CLIP_BONE_TRACK_INTROSPECTION,
    MMD_RUNTIME_STATUS_BUFFER_TOO_SMALL,
    MMD_RUNTIME_STATUS_OK,
    MmdRuntimeFfiBoneTrackCurve,
    MmdRuntimeFfiBoneTrackDescriptor,
    MmdRuntimeFfiBoneTrackKey,
)


class _Function:
    """Callable fake that accepts ctypes signature attributes."""

    def __init__(self, callback):
        self.callback = callback
        self.restype = None
        self.argtypes = None

    def __call__(self, *args):
        return self.callback(*args)


def _curve(kind=MMD_RUNTIME_BONE_TRACK_CURVE_CUBIC_BEZIER):
    controls = (0.25, 0.5, 0.75, 1.0) if kind else (0.0, 0.0, 0.0, 0.0)
    return MmdRuntimeFfiBoneTrackCurve(kind, *controls)


def _key(frame, *, first=False, bone_index=7):
    curve = _curve(MMD_RUNTIME_BONE_TRACK_CURVE_NONE if first else MMD_RUNTIME_BONE_TRACK_CURVE_CUBIC_BEZIER)
    return MmdRuntimeFfiBoneTrackKey(
        bone_index=bone_index,
        frame=frame,
        position_xyz=(1.0, 2.0, 3.0),
        rotation_xyzw=(0.0, 0.0, 0.0, 1.0),
        translation_x=curve,
        translation_y=curve,
        translation_z=curve,
        rotation=curve,
    )


class _ClipIntrospectionLib:
    def __init__(
        self,
        *,
        flags=MMD_RUNTIME_FEATURE_CLIP_BONE_TRACK_INTROSPECTION,
        track_count=1,
        descriptor_status=MMD_RUNTIME_STATUS_OK,
        descriptor_count=2,
        key_count=2,
        written=2,
        copy_status=MMD_RUNTIME_STATUS_OK,
        invalid_curve=False,
        invalid_bone=False,
        nonfinite=False,
    ):
        self.flags = flags
        self.track_count = track_count
        self.descriptor_status = descriptor_status
        self.descriptor_count = descriptor_count
        self.key_count = key_count
        self.written = written
        self.copy_status = copy_status
        self.invalid_curve = invalid_curve
        self.invalid_bone = invalid_bone
        self.nonfinite = nonfinite
        self.free_calls = []

    def mmd_runtime_feature_flags(self):
        return self.flags

    def mmd_runtime_clip_bone_track_count(self, _clip):
        return self.track_count

    def mmd_runtime_clip_bone_track_descriptor(self, _clip, _index, out_descriptor):
        if self.descriptor_status != MMD_RUNTIME_STATUS_OK:
            return self.descriptor_status
        out_descriptor._obj.bone_index = 7
        out_descriptor._obj.key_count = self.descriptor_count
        return MMD_RUNTIME_STATUS_OK

    def mmd_runtime_clip_bone_track_key_count(self, _clip, _index):
        return self.key_count

    def mmd_runtime_clip_copy_bone_track_keys(
        self,
        _clip,
        _index,
        out_keys,
        capacity,
        out_written,
    ):
        out_written._obj.value = self.written
        if self.copy_status != MMD_RUNTIME_STATUS_OK:
            return self.copy_status
        if int(capacity) >= 2:
            out_keys[0] = _key(0, first=True)
            out_keys[1] = _key(10, bone_index=8 if self.invalid_bone else 7)
            if self.invalid_curve:
                out_keys[1].rotation.kind = 99
            if self.nonfinite:
                out_keys[1].position_xyz[0] = math.nan
        return MMD_RUNTIME_STATUS_OK

    def mmd_runtime_clip_free(self, handle):
        self.free_calls.append(handle)


class TestClipIntrospectionAbiLayout(TestCase):
    def test_windows_x64_layout(self):
        expected = {
            MmdRuntimeFfiBoneTrackCurve: (20, 4, {"kind": 0, "x1": 4, "y2": 16}),
            MmdRuntimeFfiBoneTrackDescriptor: (16, 8, {"bone_index": 0, "key_count": 8}),
            MmdRuntimeFfiBoneTrackKey: (
                116,
                4,
                {"bone_index": 0, "frame": 4, "position_xyz": 8, "rotation_xyzw": 20, "rotation": 96},
            ),
        }
        for record, (size, alignment, offsets) in expected.items():
            self.assertEqual(ctypes.sizeof(record), size)
            self.assertEqual(ctypes.alignment(record), alignment)
            for name, offset in offsets.items():
                self.assertEqual(getattr(record, name).offset, offset)

    def test_optional_signatures_are_feature_gated(self):
        class Lib:
            def __init__(self, flags):
                self.mmd_runtime_feature_flags = _Function(lambda: flags)
                self.mmd_runtime_clip_bone_track_count = _Function(lambda _clip: 0)
                self.mmd_runtime_clip_bone_track_descriptor = _Function(lambda *_args: 0)
                self.mmd_runtime_clip_bone_track_key_count = _Function(lambda *_args: 0)
                self.mmd_runtime_clip_copy_bone_track_keys = _Function(lambda *_args: 0)

        disabled = Lib(0)
        setup_clip_bone_track_signatures(disabled)
        self.assertIsNone(disabled.mmd_runtime_clip_bone_track_count.restype)

        enabled = Lib(MMD_RUNTIME_FEATURE_CLIP_BONE_TRACK_INTROSPECTION)
        setup_clip_bone_track_signatures(enabled)
        self.assertIs(enabled.mmd_runtime_clip_bone_track_count.restype, ctypes.c_size_t)
        self.assertEqual(len(enabled.mmd_runtime_clip_copy_bone_track_keys.argtypes), 5)


class TestClipIntrospectionWrapper(TestCase):
    def test_copies_owned_keys_that_survive_clip_free(self):
        lib = _ClipIntrospectionLib()
        clip = MmdRuntimeClip(lib, ctypes.c_void_p(0x1234))

        tracks = clip.bone_tracks()
        clip.free()

        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].descriptor, (7, 2))
        self.assertEqual([key.frame for key in tracks[0].keys], [0, 10])
        self.assertEqual(tracks[0].keys[0].rotation.kind, MMD_RUNTIME_BONE_TRACK_CURVE_NONE)
        self.assertEqual(
            tracks[0].keys[1].rotation.kind,
            MMD_RUNTIME_BONE_TRACK_CURVE_CUBIC_BEZIER,
        )
        self.assertEqual(tracks[0].keys[1].position_xyz, (1.0, 2.0, 3.0))
        self.assertEqual(len(lib.free_calls), 1)

    def test_missing_feature_or_symbol_fails_closed(self):
        self.assertIsNone(MmdRuntimeClip(_ClipIntrospectionLib(), None).bone_tracks())
        self.assertIsNone(MmdRuntimeClip(_ClipIntrospectionLib(flags=0), 1).bone_tracks())
        lib = _ClipIntrospectionLib()
        lib.mmd_runtime_clip_copy_bone_track_keys = None
        self.assertIsNone(MmdRuntimeClip(lib, 1).bone_tracks())

    def test_zero_tracks_is_a_valid_owned_snapshot(self):
        self.assertEqual(MmdRuntimeClip(_ClipIntrospectionLib(track_count=0), 1).bone_tracks(), ())

    def test_out_of_range_descriptor_failure_is_not_partial_success(self):
        lib = _ClipIntrospectionLib(descriptor_status=1)
        self.assertIsNone(MmdRuntimeClip(lib, 1).bone_tracks())

    def test_count_capacity_and_partial_results_fail_closed(self):
        self.assertIsNone(
            MmdRuntimeClip(_ClipIntrospectionLib(descriptor_count=3), 1).bone_tracks()
        )
        self.assertIsNone(MmdRuntimeClip(_ClipIntrospectionLib(written=1), 1).bone_tracks())
        self.assertIsNone(
            MmdRuntimeClip(
                _ClipIntrospectionLib(
                    copy_status=MMD_RUNTIME_STATUS_BUFFER_TOO_SMALL,
                    written=0,
                ),
                1,
            ).bone_tracks()
        )

    def test_invalid_semantics_fail_closed(self):
        for lib in (
            _ClipIntrospectionLib(invalid_curve=True),
            _ClipIntrospectionLib(invalid_bone=True),
            _ClipIntrospectionLib(nonfinite=True),
        ):
            self.assertIsNone(MmdRuntimeClip(lib, 1).bone_tracks())


if __name__ == "__main__":
    import unittest

    unittest.main()
