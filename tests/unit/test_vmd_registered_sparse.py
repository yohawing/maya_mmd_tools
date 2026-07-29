"""Tests for compiled registered sparse-key adaptation and fail-closed preflight."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from mmd_tools.converters import vmd_converter as converter_module
from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.converters.vmd_registered_sparse import registered_sparse_bone_frames
from mmd_tools.core.exceptions import MMDImportException
from mmd_tools.core.native.mmd_anim_runtime_types import (
    MMD_RUNTIME_BONE_TRACK_CURVE_CUBIC_BEZIER,
    MMD_RUNTIME_BONE_TRACK_CURVE_NONE,
    MmdRuntimeBoneTrack,
    MmdRuntimeBoneTrackCurve,
    MmdRuntimeBoneTrackDescriptor,
    MmdRuntimeBoneTrackKey,
)


def _curve(kind, controls=(0.25, 0.5, 0.75, 1.0)):
    if kind == MMD_RUNTIME_BONE_TRACK_CURVE_NONE:
        controls = (0.0, 0.0, 0.0, 0.0)
    return MmdRuntimeBoneTrackCurve(kind, *controls)


def _track(bone_index=3):
    none = _curve(MMD_RUNTIME_BONE_TRACK_CURVE_NONE)
    cubic = _curve(MMD_RUNTIME_BONE_TRACK_CURVE_CUBIC_BEZIER)
    keys = (
        MmdRuntimeBoneTrackKey(
            bone_index,
            0,
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0, 1.0),
            none,
            none,
            none,
            none,
        ),
        MmdRuntimeBoneTrackKey(
            bone_index,
            10,
            (1.0, 2.0, 3.0),
            (0.0, 0.0, 0.5, 0.8660254),
            cubic,
            cubic,
            cubic,
            cubic,
        ),
    )
    return MmdRuntimeBoneTrack(MmdRuntimeBoneTrackDescriptor(bone_index, 2), keys)


class TestRegisteredSparseAdapter(TestCase):
    def test_maps_compiled_index_and_semantic_curves_without_raw_bytes(self):
        frames = registered_sparse_bone_frames(
            (_track(),),
            bone_names_by_index={3: "左腕捩"},
            imported_bone_indices={3: "|model|leftArmTwist"},
        )

        self.assertEqual([(frame.bone_index, frame.bone_name, frame.frame_number) for frame in frames], [(3, "左腕捩", 0), (3, "左腕捩", 10)])
        self.assertEqual(frames[0].semantic_interpolation["rotation"], (0.0, 0.0, 1.0, 1.0))
        self.assertEqual(frames[1].semantic_interpolation["rotation"], (0.25, 0.5, 0.75, 1.0))
        self.assertFalse(hasattr(frames[1], "interpolation"))

    def test_rejects_compiled_index_not_in_imported_pmx_table(self):
        with self.assertRaisesRegex(ValueError, "absent from imported PMX table"):
            registered_sparse_bone_frames(
                (_track(),),
                bone_names_by_index={3: "左腕捩"},
                imported_bone_indices={4: "wrongJoint"},
            )


class TestRegisteredSparsePreflight(TestCase):
    def setUp(self):
        self.converter = VmdConverter()
        self.converter.bone_name_to_index = {"左腕捩": 3}
        self.converter.bone_index_to_joint = {3: "|model|leftArmTwist"}

    def test_builds_one_model_paired_clip_and_records_profile(self):
        model = SimpleNamespace(free=lambda: None)
        clip = SimpleNamespace(bone_tracks=lambda: (_track(),), free=lambda: None)
        profile = {}
        with patch.object(
            converter_module,
            "resolve_runtime_pmx_bytes_and_morph_names",
            return_value=(b"pmx", []),
        ), patch.object(
            converter_module.MmdRuntimeModel,
            "from_pmx_bytes",
            return_value=model,
        ) as model_create, patch.object(
            converter_module.MmdRuntimeClip,
            "from_vmd_bytes_for_model",
            return_value=clip,
        ) as clip_create:
            frames, provenance = self.converter._compiled_registered_sparse_frames(
                vmd_bytes=b"vmd",
                pmx_bytes=b"pmx",
                pmx_path="model.pmx",
                vmd_source_path="motion.vmd",
                profile=profile,
            )

        model_create.assert_called_once_with(b"pmx")
        clip_create.assert_called_once_with(model, b"vmd")
        self.assertEqual(len(frames), 2)
        self.assertEqual(provenance["evaluation_mode"], "authored_sparse_keys")
        self.assertEqual(profile["vmd_converter"]["registered_sparse"]["fallback"], "none")

    def test_missing_introspection_fails_without_raw_fallback(self):
        model = SimpleNamespace(free=lambda: None)
        clip = SimpleNamespace(bone_tracks=lambda: None, free=lambda: None)
        with patch.object(
            converter_module,
            "resolve_runtime_pmx_bytes_and_morph_names",
            return_value=(b"pmx", []),
        ), patch.object(
            converter_module.MmdRuntimeModel,
            "from_pmx_bytes",
            return_value=model,
        ), patch.object(
            converter_module.MmdRuntimeClip,
            "from_vmd_bytes_for_model",
            return_value=clip,
        ):
            with self.assertRaises(MMDImportException) as raised:
                self.converter._compiled_registered_sparse_frames(
                    vmd_bytes=b"vmd",
                    pmx_bytes=b"pmx",
                    pmx_path="model.pmx",
                    vmd_source_path="motion.vmd",
                    profile={},
                )

        self.assertEqual(raised.exception.reason_code, "registered_sparse_introspection_unavailable")


if __name__ == "__main__":
    import unittest

    unittest.main()
