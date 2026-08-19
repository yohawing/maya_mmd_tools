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
from mmd_tools.adapters import native_vmd_batch_sampler as sampler_module  # noqa: E402

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
        return [2.0, frame_count, channel_count, 0.0, float(channel_count), 0.0, *values]


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
        packed = [2.0, 2.0, 6.0, 0.0, 6.0, 0.0]
        packed.extend(float(index) for index in range(12))
        rows, counts = parse_packed_result(packed, plan)
        self.assertEqual(rows[0], tuple(float(index) for index in range(6)))
        self.assertEqual(counts, {"direct_curve": 0, "static": 6, "timed_mplug": 0})
        with self.assertRaises(NativeVmdBatchSamplerError):
            parse_packed_result(packed[:-1], plan)
        with self.assertRaises(NativeVmdBatchSamplerError):
            parse_packed_result([2.0, 2.0, 6.0, 0.0, 6.0, 0.0, *([float("nan")] * 12)], plan)
        with self.assertRaisesRegex(NativeVmdBatchSamplerError, "unsupported native sampler protocol"):
            parse_packed_result([1.0, 2.0, 6.0, 0.0, 6.0, 0.0, *([0.0] * 12)], plan)

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
        self.assertEqual(cmds.calls[0]["version"], 2)
        self.assertEqual(
            cmds.calls[0]["evaluation_policy"],
            sampler_module.EVALUATION_POLICY,
        )
        self.assertEqual(len(cmds.calls[0]["channels"]), 11)
        self.assertEqual(samples.value("joint_a", "translateX", 1), 11.0)
        self.assertEqual(samples.value("joint_b", "translateX", 1), 11.0)
        self.assertEqual(samples.sample_count, 22)
        self.assertEqual(samples.diagnostics["chunk_count"], 1)
        self.assertEqual(samples.plan._frame_indices, {0.0: 0, 1.0: 1})
        self.assertEqual(samples.plan._logical_indices[("joint_b", "translateX")], 0)
        # The hot path must remain dictionary indexed even when repeatedly
        # reading a dense track; this guards against reintroducing list.index.
        for _ in range(1000):
            self.assertEqual(samples.value("joint_b", "translateX", 1), 11.0)
        samples.close()

    def test_samples_use_read_only_mmap_and_cleanup_is_idempotent(self):
        samples = NativeVmdBatchSampler(_FakeCmds()).sample_dense_bone_channels(
            [0, 1], ["joint"]
        )
        spool_path = Path(samples._spool_path)
        self.assertTrue(spool_path.exists())
        self.assertEqual(samples.diagnostics["storage_backend"], "read_only_mmap")
        self.assertEqual(samples.diagnostics["storage_bytes"], 2 * 6 * 8)
        self.assertEqual(samples.diagnostics["storage_value_count"], 12)
        self.assertEqual(samples.value("joint", "rotateZ", 1), 11.0)
        samples.close()
        samples.close()
        self.assertFalse(spool_path.exists())
        with self.assertRaisesRegex(RuntimeError, "samples are closed"):
            samples.value("joint", "translateX", 0)

    def test_second_chunk_failure_removes_partial_spool(self):
        class _BrokenSecondChunk(_FakeCmds):
            def mmdVmdBatchSample(self, payload=None):
                if self.calls:
                    raise RuntimeError("second chunk failed")
                return super().mmdVmdBatchSample(payload)

        paths = []
        real_mkstemp = sampler_module.tempfile.mkstemp

        def track_mkstemp(*args, **kwargs):
            fd, path = real_mkstemp(*args, **kwargs)
            paths.append(path)
            return fd, path

        sampler = NativeVmdBatchSampler(_BrokenSecondChunk())
        with mock.patch.object(sampler_module, "MAX_NATIVE_SAMPLES", 12), mock.patch.object(
            sampler_module.tempfile, "mkstemp", side_effect=track_mkstemp
        ), self.assertRaises(NativeVmdBatchSamplerError):
            sampler.sample_dense_bone_channels([0, 1, 2, 3], ["joint"])
        self.assertEqual(len(paths), 1)
        self.assertFalse(Path(paths[0]).exists())

    def test_base_exception_cleans_partial_spool_and_is_not_wrapped(self):
        class _CancelledSecondChunk(_FakeCmds):
            def mmdVmdBatchSample(self, payload=None):
                if self.calls:
                    raise KeyboardInterrupt()
                return super().mmdVmdBatchSample(payload)

        paths = []
        real_mkstemp = sampler_module.tempfile.mkstemp

        def track_mkstemp(*args, **kwargs):
            fd, path = real_mkstemp(*args, **kwargs)
            paths.append(path)
            return fd, path

        sampler = NativeVmdBatchSampler(_CancelledSecondChunk())
        with mock.patch.object(sampler_module, "MAX_NATIVE_SAMPLES", 12), mock.patch.object(
            sampler_module.tempfile, "mkstemp", side_effect=track_mkstemp
        ), self.assertRaises(KeyboardInterrupt):
            sampler.sample_dense_bone_channels([0, 1, 2, 3], ["joint"])
        self.assertEqual(len(paths), 1)
        self.assertFalse(Path(paths[0]).exists())

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

    def test_large_request_is_chunked_at_native_sample_limit(self):
        class _ChunkCmds(_FakeCmds):
            def mmdVmdBatchSample(self, payload=None):
                request = json.loads(payload)
                self.calls.append(request)
                channel_count = len(request["channels"])
                values = [
                    float(frame * 100.0 + channel)
                    for frame in request["frames"]
                    for channel in range(channel_count)
                ]
                return [
                    2.0,
                    len(request["frames"]),
                    channel_count,
                    0.0,
                    float(channel_count),
                    0.0,
                    *values,
                ]

        cmds = _ChunkCmds()
        sampler = NativeVmdBatchSampler(cmds)
        with mock.patch.object(sampler_module, "MAX_NATIVE_SAMPLES", 12):
            samples = sampler.sample_dense_bone_channels(
                [0, 1, 2, 3, 4], ["joint"]
            )
        self.assertEqual(len(cmds.calls), 3)
        self.assertEqual([request["frames"] for request in cmds.calls], [[0.0, 1.0], [2.0, 3.0], [4.0]])
        self.assertTrue(
            all(
                len(request["frames"]) * len(request["channels"]) <= 12
                for request in cmds.calls
            )
        )
        self.assertEqual(samples.value("joint", "translateX", 1), 100.0)
        self.assertEqual(samples.value("joint", "translateX", 2), 200.0)
        self.assertEqual(samples.value("joint", "rotateZ", 4), 405.0)
        self.assertEqual(samples.diagnostics["chunk_count"], 3)
        self.assertEqual(samples.diagnostics["max_samples_per_chunk"], 12)
        self.assertEqual(len(samples.diagnostics["chunk_wall_sec"]), 3)
        self.assertTrue(
            all(
                request["evaluation_policy"] == sampler_module.EVALUATION_POLICY
                for request in cmds.calls
            )
        )

    def test_timeline_requests_are_bounded_to_120_frames(self):
        class _FrameBoundCmds(_FakeCmds):
            def mmdVmdBatchSample(self, payload=None):
                request = json.loads(payload)
                self.calls.append(request)
                channel_count = len(request["channels"])
                return [
                    2.0,
                    len(request["frames"]),
                    channel_count,
                    0.0,
                    float(channel_count),
                    0.0,
                    *([0.0] * (len(request["frames"]) * channel_count)),
                ]

        cmds = _FrameBoundCmds()
        sampler = NativeVmdBatchSampler(cmds)
        with mock.patch.object(sampler_module, "MAX_NATIVE_SAMPLES", 100000):
            with mock.patch.object(sampler_module, "MAX_NATIVE_FRAMES", 2):
                sampler.sample_dense_bone_channels(range(5), ["joint"])
        self.assertEqual(
            [len(request["frames"]) for request in cmds.calls],
            [2, 2, 1],
        )
        self.assertTrue(
            all(
                request["evaluation_policy"] == sampler_module.EVALUATION_POLICY
                for request in cmds.calls
            )
        )

    def test_chunk_strategy_mismatch_is_a_protocol_failure(self):
        class _ChangingCmds(_FakeCmds):
            def mmdVmdBatchSample(self, payload=None):
                request = json.loads(payload)
                self.calls.append(request)
                channel_count = len(request["channels"])
                values = [0.0] * (len(request["frames"]) * channel_count)
                static_count = channel_count if len(self.calls) == 1 else 0
                timed_count = 0 if len(self.calls) == 1 else channel_count
                return [
                    2.0,
                    len(request["frames"]),
                    channel_count,
                    0.0,
                    float(static_count),
                    float(timed_count),
                    *values,
                ]

        cmds = _ChangingCmds()
        sampler = NativeVmdBatchSampler(cmds)
        with mock.patch.object(sampler_module, "MAX_NATIVE_SAMPLES", 12):
            with self.assertRaisesRegex(
                NativeVmdBatchSamplerError,
                "strategy counts differ between chunks",
            ):
                sampler.sample_dense_bone_channels([0, 1, 2, 3], ["joint"])
        self.assertEqual(len(cmds.calls), 2)
        self.assertFalse(sampler.last_diagnostics["used"])
        self.assertIn("strategy counts differ", sampler.last_diagnostics["fallback_reason"])

    def test_diagnostics_sink_flushes_before_each_native_chunk(self):
        events = []
        sampler = NativeVmdBatchSampler(_FakeCmds(), diagnostics_sink=events.append)
        with mock.patch.object(sampler_module, "MAX_NATIVE_SAMPLES", 12):
            sampler.sample_dense_bone_channels([0, 1, 2], ["joint"])
        statuses = [event.get("status") for event in events]
        self.assertEqual(statuses[0], "sampling")
        self.assertEqual(statuses[1:3], ["sampling_chunk", "sampling_chunk"])
        self.assertEqual(statuses[-1], "completed")
        self.assertEqual(events[1]["chunk_index"], 0)
        self.assertEqual(events[2]["chunk_index"], 1)
        self.assertEqual(events[-1]["chunk_count"], 2)

    def test_collector_only_batches_keyed_joints_and_ignores_raw_provenance(self):
        class _Cmds:
            def ls(self, node, long=False):
                return [str(node)]

            def listConnections(self, _plug, **_kwargs):
                return []

        class _Samples:
            diagnostics = {
                "available": True,
                "used": True,
                "sample_count": 12,
            }

            def __init__(self):
                self.closed = 0

            def close(self):
                self.closed += 1

            def value(self, _joint, attr, frame):
                return float(frame) + (10.0 if attr.startswith("rotate") else 1.0)

        class _Native:
            def __init__(self):
                self.joints = None
                self.samples = None
                self.last_diagnostics = {
                    "plugin_path": "F:/native/mmd_tools_cpp.mll",
                    "plugin_load_status": "loaded",
                }

            def sample_dense_bone_channels(self, frames, joints, routes):
                self.joints = tuple(joints)
                self.frames = tuple(frames)
                self.routes = routes
                self.samples = _Samples()
                return self.samples

        native = _Native()
        collector = VmdSceneCollector(bone_channel_sampler=native)
        collector._mode_c_physics_output_excluded_targets = {"sparse"}
        raw = {
            ("dense", 0): ((99.0, 99.0, 99.0), (0.0, 0.0, 0.0, 1.0)),
            ("dense", 1): ((99.0, 99.0, 99.0), (0.0, 0.0, 0.0, 1.0)),
        }
        with mock.patch.object(collector_module, "cmds", _Cmds()), mock.patch.object(
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
        self.assertEqual(native.samples.closed, 1)
        self.assertEqual(
            collector.diagnostics["native_sampler"]["plugin_load_status"],
            "loaded",
        )

    def test_collector_blocks_when_native_sampling_fails(self):
        class _Cmds:
            def ls(self, node, long=False):
                return [str(node)]

        class _BrokenNative:
            available = True

            def sample_dense_bone_channels(self, _frames, _joints, _routes):
                raise NativeVmdBatchSamplerError("protocol mismatch")

        collector = VmdSceneCollector(bone_channel_sampler=_BrokenNative())
        with mock.patch.object(collector_module, "cmds", _Cmds()), mock.patch.object(
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
            with self.assertRaisesRegex(RuntimeError, "Mode C native bone sampling failed"):
                collector.collect_bone_frames(
                    ["joint"],
                    input_routes={},
                    dense_sample=True,
                    force_dense_sample=True,
                    dense_frame_samples=[0, 1],
                    time_converter=lambda value: value,
                    bone_channel_sampler=collector._bone_channel_sampler,
                )
        self.assertFalse(collector.diagnostics["native_sampler"]["used"])
        self.assertTrue(collector.diagnostics["native_sampler"]["fatal"])
        self.assertIn("fallback_reason", collector.diagnostics["native_sampler"])

    def test_collector_closes_native_samples_when_value_read_fails(self):
        class _Cmds:
            def ls(self, node, long=False):
                return [str(node)]

            def listConnections(self, _plug, **_kwargs):
                return []

        class _Samples:
            diagnostics = {"available": True, "used": True}

            def __init__(self):
                self.closed = 0

            def value(self, _joint, _attr, _frame):
                raise ValueError("sample read failed")

            def close(self):
                self.closed += 1

        class _Native:
            available = True

            def __init__(self):
                self.samples = _Samples()

            def sample_dense_bone_channels(self, _frames, _joints, _routes):
                return self.samples

        native = _Native()
        collector = VmdSceneCollector(bone_channel_sampler=native)
        with mock.patch.object(collector_module, "cmds", _Cmds()), mock.patch.object(
            collector_module, "_routed_key_times", return_value=[0.0, 1.0]
        ), mock.patch.object(
            collector_module, "_build_rotation_export_context", return_value={}
        ), mock.patch.object(
            collector_module,
            "_maya_joint_rotate_to_vmd_quaternion",
            side_effect=lambda _joint, rx, ry, rz, _context: (rx, ry, rz, 1.0),
        ), mock.patch.object(
            collector_module, "_resolve_bind_pose", return_value=(0.0, 0.0, 0.0)
        ), mock.patch.object(
            collector_module,
            "_maya_translate_to_vmd_position",
            side_effect=lambda values, _bind, _scale: tuple(values),
        ):
            collector._mmd_bone_name = lambda joint: str(joint)
            with self.assertRaisesRegex(RuntimeError, "Mode C native bone value failed"):
                collector.collect_bone_frames(
                    ["joint"],
                    input_routes={},
                    dense_sample=True,
                    force_dense_sample=True,
                    dense_frame_samples=[0, 1],
                    time_converter=lambda value: value,
                    bone_channel_sampler=native,
                )
        self.assertEqual(native.samples.closed, 1)

    def test_collector_closes_native_samples_when_static_prepass_fails(self):
        class _Cmds:
            def ls(self, node, long=False):
                return [str(node)]

            def listConnections(self, _plug, **_kwargs):
                return []

        class _Samples:
            diagnostics = {"available": True, "used": True}

            def __init__(self):
                self.closed = 0

            def value(self, _joint, _attr, _frame):
                return 0.0

            def close(self):
                self.closed += 1

        class _Native:
            available = True

            def __init__(self):
                self.samples = _Samples()

            def sample_dense_bone_channels(self, _frames, _joints, _routes):
                return self.samples

        native = _Native()
        collector = VmdSceneCollector(bone_channel_sampler=native)
        with mock.patch.object(collector_module, "cmds", _Cmds()), mock.patch.object(
            collector_module, "_routed_key_times", return_value=[0.0, 1.0]
        ), mock.patch.object(
            collector_module, "_build_rotation_export_context", return_value={}
        ), mock.patch.object(
            collector_module,
            "_mode_c_earliest_integer_sample",
            side_effect=KeyboardInterrupt(),
        ):
            collector._mmd_bone_name = lambda joint: str(joint)
            with self.assertRaises(KeyboardInterrupt):
                collector.collect_bone_frames(
                    ["joint"],
                    input_routes={},
                    dense_sample=True,
                    force_dense_sample=True,
                    dense_frame_samples=[0, 1],
                    time_converter=lambda value: value,
                    bone_channel_sampler=native,
                )
        self.assertEqual(native.samples.closed, 1)


if __name__ == "__main__":
    unittest.main()
