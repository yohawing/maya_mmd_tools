"""Tests for compiled registered sparse-key adaptation and fail-closed preflight."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch, Mock

from tests.common.maya_test_base import MayaTestBase

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

    def test_does_not_retain_raw_source_payload(self):
        frames = registered_sparse_bone_frames(
            (_track(),),
            bone_names_by_index={3: "左腕捩"},
            imported_bone_indices={3: "|model|leftArmTwist"},
        )

        self.assertFalse(hasattr(frames[1], "source_interpolation"))
        self.assertEqual(
            frames[1].semantic_interpolation["rotation"],
            (0.25, 0.5, 0.75, 1.0),
        )

    def test_owned_key_views_preserve_groups_filtering_and_validation(self):
        from collections.abc import Mapping
        from mmd_tools.converters.vmd_registered_sparse import RegisteredSparseFrames
        track = _track()
        frames = registered_sparse_bone_frames(
            (track, _track(4)), bone_names_by_index={3: "same", 4: "same"},
            imported_bone_indices={3: "joint3", 4: "joint4"})
        self.assertIs(frames[1].position, track.keys[1].position_xyz)
        self.assertIs(frames[1].rotation, track.keys[1].rotation_xyzw)
        self.assertIsInstance(frames[1].semantic_interpolation, Mapping)
        self.assertEqual(set(frames.by_bone), {("index", 3), ("index", 4)})
        filtered = VmdConverter._control_rig_bone_frames_for_import(frames, {"same"})
        self.assertIsInstance(filtered, RegisteredSparseFrames)
        self.assertEqual(len(filtered), 4)
        self.assertEqual(len(VmdConverter._control_rig_bone_frames_for_import(frames, {"absent"})), 0)
        del track
        self.assertEqual(dict(frames[1].semantic_interpolation)["translate_x"], (.25, .5, .75, 1.))
        with self.assertRaisesRegex(ValueError, "duplicate compiled"):
            registered_sparse_bone_frames((_track(), _track()), bone_names_by_index={3: "same"}, imported_bone_indices={3: "j"})
        invalid = _track()._replace(keys=(_track(4).keys[0],))
        with self.assertRaisesRegex(ValueError, "key/track bone index mismatch"):
            registered_sparse_bone_frames((invalid,), bone_names_by_index={3: "same"}, imported_bone_indices={3: "j"})

    def test_grouped_authoring_matches_flat_input_with_duplicate_names(self):
        from mmd_tools.converters.vmd_bone_animation import convert_bone_animation
        frames = registered_sparse_bone_frames((_track(), _track(4)),
            bone_names_by_index={3: "same", 4: "same"}, imported_bone_indices={3: "j3", 4: "j4"})
        context = SimpleNamespace(bone_index_to_joint={3: "j3", 4: "j4"},
            bone_name_mapping={"same": "j4"}, set_bone_keyframes=Mock(),
            use_animation_layers=False, logger=Mock(), failed_bones=set())
        self.assertTrue(convert_bone_animation(context, list(frames), key_routes={}))
        expected = context.set_bone_keyframes.call_args_list
        context.set_bone_keyframes.reset_mock()
        self.assertTrue(convert_bone_animation(context, frames, key_routes={}))
        self.assertEqual(context.set_bone_keyframes.call_args_list, expected)
        self.assertEqual([call.args[0] for call in expected], ["j3", "j4"])

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
        self.converter.bone_name_mapping = {"左腕捩": "|model|leftArmTwist"}

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
            frames, registration = self.converter._compiled_registered_sparse_frames(
                vmd_bytes=b"vmd",
                pmx_bytes=b"pmx",
                pmx_path="model.pmx",
                profile=profile,
            )

        model_create.assert_called_once_with(b"pmx")
        clip_create.assert_called_once_with(model, b"vmd")
        self.assertEqual(len(frames), 2)
        self.assertFalse(hasattr(frames[1], "source_interpolation"))
        self.assertEqual(registration["evaluation_mode"], "authored_sparse_keys")
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
                    profile={},
                )

        self.assertEqual(raised.exception.reason_code, "registered_sparse_introspection_unavailable")



class TestRegisteredSparseCurveParity(MayaTestBase):
    def test_lazy_semantics_match_eager_translation_curves_between_keys(self):
        from maya import cmds
        from mmd_tools.converters.vmd_registered_sparse import RegisteredSparseBoneFrame
        from mmd_tools.converters.vmd_bezier_tangent import apply_vmd_bezier_tangents
        converter = VmdConverter()
        lazy = registered_sparse_bone_frames((_track(),), bone_names_by_index={3: "bone"}, imported_bone_indices={3: "joint"})
        eager = [RegisteredSparseBoneFrame(f.bone_name, f.bone_index, f.frame_number,
                 f.position, f.rotation, dict(f.semantic_interpolation)) for f in lazy]
        values = []
        for frames in (eager, lazy):
            node = cmds.createNode("transform")
            for frame in frames:
                for i, axis in enumerate("XYZ"):
                    cmds.setKeyframe(node, attribute="translate"+axis,
                        time=converter.vmd_frame_to_maya_time(frame.frame_number), value=frame.position[i])
            apply_vmd_bezier_tangents(converter, node, frames, ["translate"+axis for axis in "XYZ"],
                {"translate"+axis: "translate_"+axis.lower() for axis in "XYZ"})
            values.append([cmds.getAttr(node+".translate"+axis, time=converter.vmd_frame_to_maya_time(t))
                           for axis in "XYZ" for t in (.25, 2.5, 5., 7.75, 9.5)])
        self.assertEqual(values[0], values[1])


if __name__ == "__main__":
    import unittest

    unittest.main()
