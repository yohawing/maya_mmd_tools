"""Unit contracts for the optional native Mode C batch sampler."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from tests.common.maya_stub import install_maya_stub

install_maya_stub()

from mmd_tools.converters import vmd_scene_collector as collector_module  # noqa: E402
from mmd_tools.converters.vmd_scene_collector import VmdSceneCollector  # noqa: E402

from mmd_tools.adapters.native_vmd_batch_sampler import (
    NativeVmdBatchSampler,
    NativeVmdBatchSamplerError,
    build_dense_bone_sample_plan,
    parse_packed_result,
)


class _FakeCmds:
    def __init__(self):
        self.calls = []

    def ls(self, node, long=False):
        return [str(node)]

    def listConnections(self, plug, **_kwargs):
        return []

    def getAttr(self, plug, type=False):
        return "double" if type else 0.0

    def attributeQuery(self, _attr, node=None, listParent=False):
        return [] if listParent else False

    def mmdVmdBatchSample(self, payload=None):
        self.calls.append(json.loads(payload))
        channel_count = len(self.calls[-1]["channels"])
        frame_count = len(self.calls[-1]["frames"])
        values = [float(index) for index in range(frame_count * channel_count)]
        return [1.0, frame_count, channel_count, 0.0, float(channel_count), 0.0, *values]


class NativeVmdBatchSamplerTests(unittest.TestCase):
    def test_plan_is_deterministic_and_deduplicates_physical_plugs(self):
        routes = {
            "joint_a": {"translateX": ("shared", "value")},
            "joint_b": {"translateX": ("shared", "value")},
        }
        plan = build_dense_bone_sample_plan(
            ["joint_a", "joint_b"], [0, 1], input_routes=routes
        )
        self.assertEqual(plan.frames, (0.0, 1.0))
        self.assertEqual(len(plan.logical_channels), 12)
        self.assertEqual(len(plan.physical_channels), 11)
        aliases = [
            channel
            for channel in plan.logical_channels
            if channel.attr == "translateX"
        ]
        self.assertEqual([channel.physical_index for channel in aliases], [0, 0])
        self.assertEqual(
            [channel.plug for channel in plan.physical_channels[:2]],
            ["shared.value", "joint_a.translateY"],
        )

    def test_hints_are_conservative_about_direct_and_intermediary_routes(self):
        class _HintCmds(_FakeCmds):
            def listConnections(self, plug, **_kwargs):
                return {
                    "joint.rotateX": ["curve.output"],
                    "joint.rotateZ": ["unit.output"],
                }.get(plug, [])

            def nodeType(self, node):
                return {"curve": "animCurveTA", "unit": "unitConversion"}.get(node)

        routes = {
            "joint": {
                "rotateX": ("joint", "rotateX"),
                "rotateZ": ("joint", "rotateZ"),
            }
        }
        plan = build_dense_bone_sample_plan(
            ["joint"], [0, 1], input_routes=routes, cmds_module=_HintCmds()
        )
        hints = {channel.attr: channel.hint for channel in plan.physical_channels}
        self.assertEqual(hints["rotateX"], "direct_curve")
        self.assertEqual(hints["translateX"], "static")
        self.assertEqual(hints["rotateZ"], "timed_mplug")

    def test_packed_result_validates_header_length_and_finite_values(self):
        plan = build_dense_bone_sample_plan(["joint"], [0, 1])
        packed = [1.0, 2.0, 6.0, 0.0, 6.0, 0.0]
        packed.extend(float(index) for index in range(12))
        rows, counts = parse_packed_result(packed, plan)
        self.assertEqual(rows[0], tuple(float(index) for index in range(6)))
        self.assertEqual(counts, {"direct_curve": 0, "static": 6, "timed_mplug": 0})
        with self.assertRaises(NativeVmdBatchSamplerError):
            parse_packed_result(packed[:-1], plan)
        with self.assertRaises(NativeVmdBatchSamplerError):
            parse_packed_result([1.0, 2.0, 6.0, 0.0, 6.0, 0.0, *([float("nan")] * 12)], plan)

    def test_command_payload_and_logical_aliases_are_frame_major(self):
        cmds = _FakeCmds()
        sampler = NativeVmdBatchSampler(cmds)
        routes = {
            "joint_a": {"translateX": ("shared", "value")},
            "joint_b": {"translateX": ("shared", "value")},
        }
        samples = sampler.sample_dense_bone_channels(
            [0, 1], ["joint_a", "joint_b"], routes
        )
        self.assertEqual(cmds.calls[0]["version"], 1)
        self.assertEqual(len(cmds.calls[0]["channels"]), 11)
        self.assertEqual(samples.value("joint_a", "translateX", 1), 11.0)
        self.assertEqual(samples.value("joint_b", "translateX", 1), 11.0)
        self.assertEqual(samples.sample_count, 22)
        self.assertEqual(samples.plan._frame_indices, {0.0: 0, 1.0: 1})
        self.assertEqual(samples.plan._logical_indices[("joint_b", "translateX")], 0)
        # The hot path must remain dictionary indexed even when repeatedly
        # reading a dense track; this guards against reintroducing list.index.
        for _ in range(1000):
            self.assertEqual(samples.value("joint_b", "translateX", 1), 11.0)

    def test_command_failure_is_diagnosed(self):
        class Broken(_FakeCmds):
            def mmdVmdBatchSample(self, payload=None):
                raise RuntimeError("command unavailable")

        sampler = NativeVmdBatchSampler(Broken())
        with self.assertRaises(NativeVmdBatchSamplerError):
            sampler.sample_dense_bone_channels([0, 1], ["joint"])
        self.assertFalse(sampler.last_diagnostics["used"])
        self.assertIn("fallback_reason", sampler.last_diagnostics)

    def test_plugin_loads_once_and_registers_the_command(self):
        class _LoadableCmds:
            pass

        plugin_path = Path("F:/native/plug-ins/2024/Release/mmd_tools_cpp.mll")
        cmds = _LoadableCmds()

        def register(_path, _cmds, **_kwargs):
            _cmds.mmdVmdBatchSample = lambda **_payload: []
            return True

        sampler = NativeVmdBatchSampler(cmds)
        with mock.patch(
            "mmd_tools.core.cpp_plugin_locator.running_maya_major_version",
            return_value="2024",
        ), mock.patch(
            "mmd_tools.core.cpp_plugin_locator.plugin_candidate_paths",
            return_value=[plugin_path],
        ), mock.patch(
            "mmd_tools.core.cpp_plugin_locator.find_plugin_path",
            return_value=plugin_path,
        ), mock.patch(
            "mmd_tools.core.cpp_plugin_locator.prepare_plugin_directory"
        ) as prepare, mock.patch(
            "mmd_tools.core.cpp_plugin_locator.load_plugin",
            side_effect=register,
        ) as load:
            self.assertTrue(sampler.available)
            self.assertTrue(sampler.available)
        prepare.assert_called_once_with(plugin_path)
        load.assert_called_once_with(plugin_path, cmds, prepare=False)
        self.assertEqual(sampler.last_diagnostics["plugin_path"], str(plugin_path))
        self.assertEqual(sampler.last_diagnostics["plugin_load_status"], "loaded")

        # A subsequent command failure must retain the loader evidence used
        # to diagnose which native binary actually owned the command.
        with self.assertRaises(NativeVmdBatchSamplerError):
            sampler.sample_dense_bone_channels([0.0], ["joint"])
        self.assertEqual(sampler.last_diagnostics["plugin_path"], str(plugin_path))
        self.assertEqual(sampler.last_diagnostics["plugin_load_status"], "loaded")

    def test_missing_plugin_is_safe_and_records_candidate(self):
        class _NoCommand:
            pass

        candidate = Path("F:/missing/mmd_tools_cpp.mll")
        sampler = NativeVmdBatchSampler(_NoCommand())
        with mock.patch(
            "mmd_tools.core.cpp_plugin_locator.running_maya_major_version",
            return_value="2024",
        ), mock.patch(
            "mmd_tools.core.cpp_plugin_locator.plugin_candidate_paths",
            return_value=[candidate],
        ), mock.patch(
            "mmd_tools.core.cpp_plugin_locator.find_plugin_path",
            return_value=None,
        ):
            self.assertFalse(sampler.available)
            self.assertFalse(sampler.available)
        self.assertEqual(sampler.last_diagnostics["plugin_path"], str(candidate))
        self.assertEqual(sampler.last_diagnostics["plugin_load_status"], "missing")

    def test_plugin_load_exception_is_safe_and_not_retried(self):
        class _NoCommand:
            pass

        candidate = Path("F:/broken/mmd_tools_cpp.mll")
        sampler = NativeVmdBatchSampler(_NoCommand())
        with mock.patch(
            "mmd_tools.core.cpp_plugin_locator.running_maya_major_version",
            return_value="2024",
        ), mock.patch(
            "mmd_tools.core.cpp_plugin_locator.plugin_candidate_paths",
            return_value=[candidate],
        ), mock.patch(
            "mmd_tools.core.cpp_plugin_locator.find_plugin_path",
            return_value=candidate,
        ), mock.patch(
            "mmd_tools.core.cpp_plugin_locator.prepare_plugin_directory"
        ), mock.patch(
            "mmd_tools.core.cpp_plugin_locator.load_plugin",
            side_effect=RuntimeError("load failed"),
        ) as load:
            self.assertFalse(sampler.available)
            self.assertFalse(sampler.available)
        load.assert_called_once()
        self.assertEqual(sampler.last_diagnostics["plugin_load_status"], "error")
        self.assertIn("load failed", sampler.last_diagnostics["plugin_load_error"])

    def test_collector_only_batches_keyed_joints_and_ignores_raw_provenance(self):
        class _Cmds:
            def ls(self, node, long=False):
                return [str(node)]

        class _FallbackEvaluator:
            def value(self, _joint, _attr, frame, _route):
                return float(frame)

        class _Samples:
            diagnostics = {
                "available": True,
                "used": True,
                "sample_count": 12,
            }

            def value(self, _joint, attr, frame):
                return float(frame) + (10.0 if attr.startswith("rotate") else 1.0)

        class _Native:
            def __init__(self):
                self.joints = None
                self.last_diagnostics = {
                    "plugin_path": "F:/native/mmd_tools_cpp.mll",
                    "plugin_load_status": "loaded",
                }

            def sample_dense_bone_channels(self, frames, joints, routes):
                self.joints = tuple(joints)
                self.frames = tuple(frames)
                self.routes = routes
                return _Samples()

        native = _Native()
        collector = VmdSceneCollector(bone_channel_sampler=native)
        raw = {
            ("dense", 0): ((99.0, 99.0, 99.0), (0.0, 0.0, 0.0, 1.0)),
            ("dense", 1): ((99.0, 99.0, 99.0), (0.0, 0.0, 0.0, 1.0)),
        }
        with mock.patch.object(collector_module, "cmds", _Cmds()), mock.patch.object(
            collector_module,
            "_RoutedPlugValueEvaluator",
            _FallbackEvaluator,
        ), mock.patch.object(
            collector_module,
            "_routed_key_times",
            side_effect=lambda joint, _route: [0.0, 1.0]
            if joint == "dense"
            else [],
        ), mock.patch.object(
            collector_module,
            "_build_rotation_export_context",
            return_value={},
        ), mock.patch.object(
            collector_module,
            "_maya_joint_rotate_to_vmd_quaternion",
            side_effect=lambda _joint, rx, ry, rz, _context: (rx, ry, rz, 1.0),
        ), mock.patch.object(
            collector_module,
            "_resolve_bind_pose",
            return_value=(0.0, 0.0, 0.0),
        ), mock.patch.object(
            collector_module,
            "_maya_translate_to_vmd_position",
            side_effect=lambda values, _bind, _scale: tuple(values),
        ):
            collector._mmd_bone_name = lambda joint: str(joint)
            result = collector.collect_bone_frames(
                ["dense", "sparse"],
                start_frame=0,
                end_frame=1,
                input_routes={},
                dense_sample=True,
                force_dense_sample=True,
                dense_frame_samples=[0, 1],
                time_converter=lambda value: value,
                raw_bone_transforms=raw,
                bone_channel_sampler=native,
            )
        self.assertEqual(native.joints, ("dense",))
        self.assertEqual(native.frames, (0, 1))
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["position"], (1.0, 1.0, 1.0))
        self.assertEqual(result[1]["position"], (2.0, 2.0, 2.0))
        self.assertNotEqual(result[0]["position"], raw[("dense", 0)][0])
        self.assertEqual(
            collector.diagnostics["native_sampler"]["plugin_load_status"],
            "loaded",
        )

    def test_collector_falls_back_when_native_sampling_fails(self):
        class _Cmds:
            def ls(self, node, long=False):
                return [str(node)]

        class _FallbackEvaluator:
            def value(self, _joint, attr, frame, _route):
                return float(frame) + (20.0 if attr.startswith("rotate") else 2.0)

        class _BrokenNative:
            available = True

            def sample_dense_bone_channels(self, _frames, _joints, _routes):
                raise NativeVmdBatchSamplerError("protocol mismatch")

        collector = VmdSceneCollector(bone_channel_sampler=_BrokenNative())
        with mock.patch.object(collector_module, "cmds", _Cmds()), mock.patch.object(
            collector_module,
            "_RoutedPlugValueEvaluator",
            _FallbackEvaluator,
        ), mock.patch.object(
            collector_module,
            "_routed_key_times",
            return_value=[0.0, 1.0],
        ), mock.patch.object(
            collector_module,
            "_build_rotation_export_context",
            return_value={},
        ), mock.patch.object(
            collector_module,
            "_maya_joint_rotate_to_vmd_quaternion",
            side_effect=lambda _joint, rx, ry, rz, _context: (rx, ry, rz, 1.0),
        ), mock.patch.object(
            collector_module,
            "_resolve_bind_pose",
            return_value=(0.0, 0.0, 0.0),
        ), mock.patch.object(
            collector_module,
            "_maya_translate_to_vmd_position",
            side_effect=lambda values, _bind, _scale: tuple(values),
        ):
            collector._mmd_bone_name = lambda joint: str(joint)
            result = collector.collect_bone_frames(
                ["joint"],
                input_routes={},
                dense_sample=True,
                force_dense_sample=True,
                dense_frame_samples=[0, 1],
                time_converter=lambda value: value,
                bone_channel_sampler=collector._bone_channel_sampler,
            )
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["position"], (2.0, 2.0, 2.0))
        self.assertFalse(collector.diagnostics["native_sampler"]["used"])
        self.assertIn("fallback_reason", collector.diagnostics["native_sampler"])


if __name__ == "__main__":
    unittest.main()
