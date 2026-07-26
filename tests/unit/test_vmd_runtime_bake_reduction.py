"""Converter-level routing tests for the opt-in reduced runtime bake."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import mmd_tools.converters.vmd_converter as vmd_converter_module
import mmd_tools.core.native.mmd_anim_runtime as mmd_runtime_module
from mmd_tools.core.native.mmd_anim_runtime_types import MmdRuntimeReductionTolerances
from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.converters.vmd_reduced_pose_integration import ReducedPoseIntegrationOutcome


class _FakeModel:
    last = None
    reduced_result = object()

    def __init__(self):
        self.reducer_calls = []
        self.reduced_pose = self.__class__.reduced_result
        self.free_calls = 0
        self.__class__.last = self

    @classmethod
    def from_pmx_bytes(cls, _pmx_bytes):
        return cls()

    def reduce_dense_pose(self, batch, **kwargs):
        self.reducer_calls.append((batch, kwargs))
        return self.reduced_pose

    def free(self):
        self.free_calls += 1


class _FakeClip:
    @classmethod
    def from_vmd_bytes_for_model(cls, _model, _vmd_bytes):
        return cls()

    def free(self):
        pass


class _FakeInstance:
    @classmethod
    def for_model(cls, _model):
        return cls()

    def free(self):
        pass


_UNSET = object()


def _cache():
    return SimpleNamespace(
        baked_frames=[0.0, 1.0],
        bake_times=[],
        joint_channel_values={},
        joint_channel_static={},
        morph_cache=[],
        batch_mode=True,
        eval_elapsed=0.0,
        eval_copy_elapsed=0.0,
        batch_unpack_elapsed=0.0,
        local_elapsed=0.0,
        append_elapsed=0.0,
        physics_bake={"used": False},
        dense_batch_result=object(),
    )


class RuntimeBakeReductionRoutingTest(unittest.TestCase):
    def setUp(self):
        self.converter = VmdConverter()
        self.converter.bone_index_to_joint = {}
        self.converter.bone_name_to_index = {}
        self.converter._disable_mmd_rig_constraints_for_runtime_bake = lambda: None
        self.converter._restore_joints_to_bind_pose_for_runtime_bake = lambda: None
        self.converter._build_runtime_bind_world_maps = lambda: None
        self.converter._get_animation_frame_range = lambda _vmd_data: (0, 1)

    def test_native_reduction_tolerance_defaults_use_product_translation_preset(self):
        tolerances = MmdRuntimeReductionTolerances()

        self.assertEqual(tolerances.local_position, 5.0e-4)
        self.assertEqual(tolerances.local_rotation_radians, 1.0e-3)
        self.assertEqual(tolerances.world_position, 5.0e-4)
        self.assertEqual(tolerances.world_rotation_radians, 1.0e-3)
        self.assertEqual(tolerances.morph_weight, 1.0e-3)

    def test_native_reducer_preflight_requires_generic_symbols_and_feature_flag(self):
        class FakeLibrary:
            def mmd_runtime_feature_flags(self):
                return mmd_runtime_module.MMD_RUNTIME_FEATURE_REDUCED_POSE_GENERIC_CURVES

        for symbol in mmd_runtime_module._MMD_RUNTIME_REDUCED_POSE_REQUIRED_SYMBOLS:
            setattr(FakeLibrary, symbol, lambda *args, **kwargs: None)

        with patch.object(mmd_runtime_module, "get_mmd_runtime_library", return_value=FakeLibrary()):
            self.assertTrue(mmd_runtime_module.is_native_reduced_pose_available())

        missing = FakeLibrary()
        missing.mmd_runtime_reduced_pose_generic_curve_keys = None
        with patch.object(mmd_runtime_module, "get_mmd_runtime_library", return_value=missing):
            self.assertFalse(mmd_runtime_module.is_native_reduced_pose_available())

        no_feature = FakeLibrary()
        no_feature.mmd_runtime_feature_flags = lambda: 0
        with patch.object(mmd_runtime_module, "get_mmd_runtime_library", return_value=no_feature):
            self.assertFalse(mmd_runtime_module.is_native_reduced_pose_available())

    def _run(
        self,
        *,
        reduce_bake_keys,
        integration=None,
        reducer_result=_UNSET,
        reduce_translate_tolerance=5.0e-4,
        reduce_rotate_tolerance=1.0e-4,
        reduce_morph_tolerance=1.0e-3,
    ):
        _FakeModel.reduced_result = object() if reducer_result is _UNSET else reducer_result
        dense_apply = MagicMock()
        profile = {}
        with patch.object(vmd_converter_module, "MmdRuntimeModel", _FakeModel), patch.object(
            vmd_converter_module, "MmdRuntimeClip", _FakeClip
        ), patch.object(vmd_converter_module, "MmdRuntimeInstance", _FakeInstance), patch.object(
            vmd_converter_module,
            "resolve_runtime_pmx_bytes_and_morph_names",
            return_value=(b"pmx", []),
        ), patch.object(
            vmd_converter_module,
            "collect_runtime_bake_cache",
            return_value=_cache(),
        ), patch.object(
            vmd_converter_module,
            "apply_runtime_channel_arrays_to_scene_with_undo_disabled",
            dense_apply,
        ), patch.object(
            vmd_converter_module,
            "author_reduced_pose_from_runtime_cache",
            return_value=integration,
        ) as authorer:
            result = self.converter._convert_using_mmd_runtime(
                SimpleNamespace(),
                vmd_bytes=b"vmd",
                pmx_bytes=b"pmx",
                pmx_path="",
                reduce_bake_keys=reduce_bake_keys,
                reduce_translate_tolerance=reduce_translate_tolerance,
                reduce_rotate_tolerance=reduce_rotate_tolerance,
                reduce_morph_tolerance=reduce_morph_tolerance,
                profile=profile,
            )
        return result, dense_apply, authorer, profile, _FakeModel.last

    def test_option_off_applies_dense_once_without_reducer_or_authorer(self):
        result, dense_apply, authorer, profile, model = self._run(reduce_bake_keys=False)

        self.assertTrue(result)
        dense_apply.assert_called_once()
        authorer.assert_not_called()
        self.assertEqual(model.reducer_calls, [])
        self.assertNotIn("reduced_bake_keys", profile.get("vmd_converter", {}))

    def test_sparse_success_skips_dense_apply(self):
        sparse = ReducedPoseIntegrationOutcome(True, plan=None, authoring=SimpleNamespace(created_curves=()))
        result, dense_apply, authorer, _profile, model = self._run(
            reduce_bake_keys=True,
            integration=sparse,
        )

        self.assertTrue(result)
        dense_apply.assert_not_called()
        authorer.assert_called_once()
        self.assertEqual(len(model.reducer_calls), 1)

    def test_tolerances_reach_native_reducer_and_adapter(self):
        sparse = ReducedPoseIntegrationOutcome(True, plan=None, authoring=SimpleNamespace(created_curves=()))
        result, _dense_apply, authorer, _profile, model = self._run(
            reduce_bake_keys=True,
            integration=sparse,
            reduce_translate_tolerance=0.01,
            reduce_rotate_tolerance=0.02,
            reduce_morph_tolerance=0.03,
        )

        self.assertTrue(result)
        native_tolerances = model.reducer_calls[0][1]["tolerances"]
        self.assertEqual(native_tolerances.local_position, 0.01)
        self.assertEqual(native_tolerances.world_position, 0.01)
        self.assertEqual(native_tolerances.local_rotation_radians, 0.02)
        self.assertEqual(native_tolerances.world_rotation_radians, 0.02)
        self.assertEqual(native_tolerances.morph_weight, 0.03)
        adapter_kwargs = authorer.call_args.kwargs
        self.assertEqual(adapter_kwargs["translate_tolerance"], 0.01)
        self.assertEqual(adapter_kwargs["rotate_tolerance_radians"], 0.02)
        self.assertEqual(adapter_kwargs["morph_tolerance"], 0.03)

    def test_reducer_failure_returns_error_without_dense_fallback(self):
        result, dense_apply, authorer, profile, model = self._run(
            reduce_bake_keys=True,
            integration=None,
            reducer_result=None,
        )

        self.assertFalse(result)
        dense_apply.assert_not_called()
        authorer.assert_not_called()
        self.assertEqual(len(model.reducer_calls), 1)
        reduced_profile = profile["vmd_converter"]["reduced_bake_keys"]
        self.assertFalse(reduced_profile["used"])
        self.assertIn("reducer", reduced_profile["reason"])
        self.assertEqual(profile["vmd_converter"]["warnings"][0]["fallback"], "none")

    def test_adapter_failure_returns_error_without_dense_fallback(self):
        failure = ReducedPoseIntegrationOutcome(False, "forced adapter failure")
        result, dense_apply, authorer, profile, _model = self._run(
            reduce_bake_keys=True,
            integration=failure,
        )

        self.assertFalse(result)
        dense_apply.assert_not_called()
        authorer.assert_called_once()
        self.assertIn("forced adapter failure", profile["vmd_converter"]["reduced_bake_keys"]["reason"])

    def test_scene_authoring_failure_aborts_without_dense_fallback(self):
        authoring = SimpleNamespace(success=False, created_curves=(), rolled_back=True)
        failure = ReducedPoseIntegrationOutcome(False, "forced connection failure", authoring=authoring)
        result, dense_apply, authorer, profile, _model = self._run(
            reduce_bake_keys=True,
            integration=failure,
        )

        self.assertFalse(result)
        dense_apply.assert_not_called()
        authorer.assert_called_once()
        reduced_profile = profile["vmd_converter"]["reduced_bake_keys"]
        self.assertEqual(reduced_profile["fallback"], "none")
        self.assertIn("forced connection failure", reduced_profile["reason"])


if __name__ == "__main__":
    unittest.main()
