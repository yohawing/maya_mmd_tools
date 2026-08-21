"""Unit contracts for the optional native Bake Timeline batch sampler."""

from __future__ import annotations

import json
import struct
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
    NativeDenseBoneTrack,
    build_dense_bone_sample_plan,
    build_dense_scalar_sample_plan,
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
        request = self.calls[-1]
        channel_count = len(request.get("channels", ()))
        frame_count = len(request["frames"])
        values = [float(index) for index in range(frame_count * channel_count)]
        if request.get("mode") == "direct_spool":
            output_count = int(request["output_channel_count"])
            rows = []
            for frame_index in range(frame_count):
                row = [float(value) for value in request["output_defaults"]]
                for channel_index, output_slot in enumerate(request["output_slots"]):
                    row[int(output_slot)] = values[frame_index * channel_count + channel_index]
                rows.extend(row)
            Path(request["spool_path"]).write_bytes(
                struct.pack("=" + "d" * len(rows), *rows)
            )
            checkpoint_count = (frame_count + sampler_module.MAX_NATIVE_FRAMES - 1) // sampler_module.MAX_NATIVE_FRAMES
            result = [
                2.0,
                frame_count,
                output_count,
                0.0,
                float(channel_count),
                0.0,
                0.1 * checkpoint_count,
                0.2 * checkpoint_count,
                0.3 * checkpoint_count,
                1.0,
                3.0,
                1.0,
                3.0,
                0.0,
                0.0,
                1.0,
                float(checkpoint_count),
            ]
            for _checkpoint_index in range(checkpoint_count):
                result.extend([0.1, 0.2, 0.3, 1.0, 3.0, 1.0, 3.0, 0.0, 0.0, 0.6])
            return result


class _LegacyDirectSpoolCmds(_FakeCmds):
    """Simulate a loaded pre-Bake-Timeline MLL that rejects only direct mode."""

    def mmdVmdBatchSample(self, payload=None):
        request = json.loads(payload)
        if request.get("mode") == "direct_spool":
            self.calls.append(request)
            raise RuntimeError(
                "[mmdVmdBatchSample] payload requires version=2, frames, "
                "channels, and evaluation_policy=maya_timeline_bake_v1"
            )
        return super().mmdVmdBatchSample(payload)


class _DirectErrorCmds(_FakeCmds):
    def mmdVmdBatchSample(self, payload=None):
        self.calls.append(json.loads(payload))
        raise RuntimeError("direct spool I/O failed")


class NativeVmdBatchSamplerTests(unittest.TestCase):
    def test_direct_spool_is_one_call_and_cleans_after_close(self):
        cmds = _FakeCmds()
        sampler = NativeVmdBatchSampler(cmds)
        samples = sampler.sample_dense_bone_channels(range(3), ["joint"])
        self.assertEqual(len(cmds.calls), 1)
        self.assertEqual(cmds.calls[0]["mode"], "direct_spool")
        self.assertEqual(samples.diagnostics["storage_backend"], "read_only_mmap")
        self.assertEqual(sampler.last_diagnostics["mode"], "direct_spool")
        self.assertEqual(samples.chunk_count, 1)
        self.assertEqual(len(samples.diagnostics["chunk_wall_sec"]), 1)
        self.assertEqual(samples.diagnostics["python_scalar_unpack_count"], 0)
        self.assertEqual(
            [samples.value("joint", "translateX", frame) for frame in range(3)],
            [0.0, 6.0, 12.0],
        )
        spool_path = Path(samples._spool_path)
        self.assertTrue(spool_path.exists())
        samples.close()
        self.assertFalse(spool_path.exists())

    def test_direct_spool_reports_each_120_frame_checkpoint(self):
        cmds = _FakeCmds()
        sampler = NativeVmdBatchSampler(cmds)
        samples = sampler.sample_dense_bone_channels(range(121), ["joint"])
        self.assertEqual(samples.chunk_count, 2)
        for key in (
            "chunk_wall_sec",
            "chunk_set_current_time_wall_sec",
            "chunk_first_timed_mplug_read_wall_sec",
            "chunk_channel_loop_wall_sec",
            "chunk_classified_compound_group_count",
            "chunk_compound_success_group_count",
            "chunk_compound_fallback_group_count",
        ):
            self.assertEqual(len(samples.diagnostics[key]), 2, key)
        samples.close()

    def test_direct_spool_rejects_checkpoint_runtime_shape_changes(self):
        ack = [
            2.0,
            121.0,
            6.0,
            0.0,
            6.0,
            0.0,
            0.1,
            0.2,
            0.3,
            1.0,
            3.0,
            1.0,
            3.0,
            0.0,
            0.0,
            1.0,
            2.0,
            0.1,
            0.2,
            0.3,
            1.0,
            3.0,
            1.0,
            3.0,
            0.0,
            0.0,
            0.6,
            0.1,
            0.2,
            0.3,
            1.0,
            3.0,
            0.0,
            0.0,
            1.0,
            3.0,
            0.6,
        ]
        with self.assertRaisesRegex(
            NativeVmdBatchSamplerError,
            "differ between checkpoints",
        ):
            sampler_module._parse_direct_spool_result(
                ack,
                frame_count=121,
                output_channel_count=6,
                native_channel_count=6,
            )

    def test_direct_spool_preserves_python_static_physics_layout(self):
        class _PhysicsFakeCmds(_FakeCmds):
            def nodeType(self, node):
                return "mmdPhysicsBoneDriver" if str(node) == "physics" else "transform"

            def getAttr(self, plug, type=False):
                if type:
                    return "double"
                return 2.5 if str(plug) == "physics.inPreTranslateX" else 0.0

        routes = {
            "joint": {
                "translateX": ("physics", "inPreTranslateX"),
            }
        }
        sampler = NativeVmdBatchSampler(_PhysicsFakeCmds())
        samples = sampler.sample_dense_bone_channels(
            [0, 1], ["joint"], input_routes=routes
        )
        self.assertEqual(
            [samples.value("joint", "translateX", frame) for frame in (0, 1)],
            [2.5, 2.5],
        )
        self.assertEqual(samples.diagnostics["physical_channel_count"], 6)
        self.assertGreaterEqual(samples.diagnostics["strategy_counts"]["static"], 1)
        samples.close()

    def test_direct_spool_supports_all_static_physics_routes(self):
        values = {
            "inPreTranslateX": 1.0,
            "inPreTranslateY": 2.0,
            "inPreTranslateZ": 3.0,
            "inPreRotateX": 4.0,
            "inPreRotateY": 5.0,
            "inPreRotateZ": 6.0,
        }

        class _AllStaticPhysicsCmds(_FakeCmds):
            def nodeType(self, node):
                return "mmdPhysicsBoneDriver" if str(node) == "physics" else "transform"

            def getAttr(self, plug, type=False):
                if type:
                    return "double"
                return values.get(str(plug).rsplit(".", 1)[-1], 0.0)

        routes = {
            "joint": {
                attr: ("physics", f"inPre{attr[0].upper() + attr[1:]}")
                for attr in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ")
            }
        }
        cmds = _AllStaticPhysicsCmds()
        samples = NativeVmdBatchSampler(cmds).sample_dense_bone_channels(
            [0, 1], ["joint"], input_routes=routes
        )
        self.assertEqual(cmds.calls[0]["channels"], [])
        self.assertEqual(cmds.calls[0]["output_slots"], [])
        ordered_defaults = [
            values[f"inPre{attr[0].upper() + attr[1:]}"]
            for attr in (
                "translateX",
                "translateY",
                "translateZ",
                "rotateX",
                "rotateY",
                "rotateZ",
            )
        ]
        self.assertEqual(
            cmds.calls[0]["output_defaults"],
            ordered_defaults,
        )
        for attr in values:
            logical_attr = attr[5].lower() + attr[6:]
            self.assertEqual(
                [samples.value("joint", logical_attr, frame) for frame in (0, 1)],
                [values[attr], values[attr]],
            )
        self.assertEqual(samples.diagnostics["strategy_counts"]["static"], 6)
        samples.close()

    def test_old_loaded_mll_fails_closed_with_rebuild_error(self):
        cmds = _LegacyDirectSpoolCmds()
        sampler = NativeVmdBatchSampler(cmds)
        with self.assertRaisesRegex(
            NativeVmdBatchSamplerError,
            "direct-spool capable plug-in; rebuild it and restart Maya",
        ):
            sampler.sample_dense_bone_channels([0, 1], ["joint"])
        self.assertEqual(len(cmds.calls), 1)


    def test_direct_spool_does_not_retry_arbitrary_command_error(self):
        cmds = _DirectErrorCmds()
        sampler = NativeVmdBatchSampler(cmds)
        with self.assertRaisesRegex(RuntimeError, "direct spool I/O failed"):
            sampler.sample_dense_bone_channels([0, 1], ["joint"])
        self.assertEqual(len(cmds.calls), 1)

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

    def test_scalar_plan_and_track_support_direct_array_element_curve(self):
        class _MorphCmds(_FakeCmds):
            def listConnections(self, plug, **_kwargs):
                if plug == "morph.inputWeight[0]":
                    return ["curve.output"]
                return []

            def nodeType(self, node):
                return "animCurveTU" if node == "curve" else "network"

        cmds = _MorphCmds()
        plan = build_dense_scalar_sample_plan(
            [("smile", "morph", "inputWeight[0]")],
            [0, 1, 2],
            cmds,
        )
        self.assertEqual(plan.physical_channels[0].hint, "direct_curve")
        samples = NativeVmdBatchSampler(cmds).sample_dense_scalar_channels(
            [0, 1, 2],
            [("smile", "morph", "inputWeight[0]")],
        )
        self.assertEqual(cmds.calls[0]["channels"][0]["unit"], "scalar")
        self.assertEqual(cmds.calls[0]["channels"][0]["hint"], "direct_curve")
        track = samples.scalar_track("smile")
        self.assertEqual(track.frames, (0.0, 1.0, 2.0))
        self.assertEqual(list(track.values), [0.0, 1.0, 2.0])
        self.assertEqual(samples.diagnostics["python_scalar_unpack_count"], 0)
        samples.close()


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
        self.assertEqual(cmds.calls[0]["timing"], sampler_module._TIMING_PROTOCOL)
        self.assertEqual(
            cmds.calls[0]["evaluation_policy"],
            sampler_module.EVALUATION_POLICY,
        )
        self.assertEqual(len(cmds.calls[0]["channels"]), 11)
        self.assertEqual(samples.value("joint_a", "translateX", 1), 11.0)
        self.assertEqual(samples.value("joint_b", "translateX", 1), 11.0)
        self.assertEqual(samples.sample_count, 22)
        self.assertEqual(samples.diagnostics["chunk_count"], 1)
        self.assertEqual(samples.diagnostics["set_current_time_wall_sec"], 0.1)
        self.assertEqual(samples.diagnostics["first_timed_mplug_read_wall_sec"], 0.2)
        self.assertEqual(samples.diagnostics["channel_loop_wall_sec"], 0.3)
        self.assertEqual(samples.diagnostics["classified_compound_group_count"], 1)
        self.assertEqual(
            samples.diagnostics["classified_compound_covered_channel_count"], 3
        )
        self.assertEqual(samples.diagnostics["compound_success_group_count"], 1)
        self.assertEqual(
            samples.diagnostics["compound_success_covered_channel_count"], 3
        )
        self.assertEqual(samples.diagnostics["compound_fallback_group_count"], 0)
        self.assertEqual(samples.plan._frame_indices, {0.0: 0, 1.0: 1})
        self.assertEqual(samples.plan._logical_indices[("joint_b", "translateX")], 0)
        # The hot path must remain dictionary indexed even when repeatedly
        # reading a dense track; this guards against reintroducing list.index.
        for _ in range(1000):
            self.assertEqual(samples.value("joint_b", "translateX", 1), 11.0)
        samples.close()

    def test_static_pre_physics_input_is_merged_without_native_request(self):
        class _LoadedLegacySampler(_FakeCmds):
            def nodeType(self, node):
                return "mmdPhysicsBoneDriver" if node == "driver" else "joint"

            def getAttr(self, plug, type=False):
                if type:
                    return "double"
                if plug == "driver.inPreTranslateX":
                    return 12.5
                return 0.0

        cmds = _LoadedLegacySampler()
        sampler = NativeVmdBatchSampler(cmds)
        samples = sampler.sample_dense_bone_channels(
            [0, 1],
            ["joint"],
            {"joint": {"translateX": ("driver", "inPreTranslateX")}},
        )

        requested_plugs = [channel["plug"] for channel in cmds.calls[0]["channels"]]
        self.assertNotIn("driver.inPreTranslateX", requested_plugs)
        self.assertEqual(len(requested_plugs), 5)
        self.assertEqual(samples.value("joint", "translateX", 0), 12.5)
        self.assertEqual(samples.value("joint", "translateX", 1), 12.5)
        self.assertEqual(samples.value("joint", "translateY", 1), 5.0)
        self.assertEqual(samples.diagnostics["strategy_counts"]["static"], 6)
        self.assertEqual(
            sampler.last_diagnostics["python_static_physics_compat_count"],
            1,
        )
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

    def test_bone_track_is_detached_soa_with_alias_and_subset_parity(self):
        routes = {
            "joint_a": {"translateX": ("shared", "value")},
            "joint_b": {"translateX": ("shared", "value")},
        }
        samples = NativeVmdBatchSampler(_FakeCmds()).sample_dense_bone_channels(
            [0, 1, 2, 3], ["joint_a", "joint_b"], routes
        )
        track = samples.bone_track("joint_b", [0, 2, 3])
        self.assertIsInstance(track, NativeDenseBoneTrack)
        self.assertEqual(samples.diagnostics["python_scalar_unpack_count"], 0)
        self.assertEqual(track.frames, (0.0, 2.0, 3.0))
        self.assertEqual(list(track.translate_x), [0.0, 22.0, 33.0])
        self.assertEqual(
            list(track.rotate_z),
            [
                samples.value("joint_b", "rotateZ", frame)
                for frame in track.frames
            ],
        )
        # Named accessors return detached arrays, not mutable mmap-backed views.
        detached = track.translate_x
        detached[0] = 999.0
        self.assertEqual(track.translate_x[0], 0.0)
        samples.close()
        self.assertEqual(list(track.translate_x), [0.0, 22.0, 33.0])

    def test_bone_track_validates_joint_frames_and_order(self):
        samples = NativeVmdBatchSampler(_FakeCmds()).sample_dense_bone_channels(
            [0, 1, 2], ["joint"]
        )
        with self.assertRaises(KeyError):
            samples.bone_track("missing", [0, 1])
        with self.assertRaises(KeyError):
            samples.bone_track("joint", [0, 4])
        with self.assertRaises(NativeVmdBatchSamplerError):
            samples.bone_track("joint", [1, 0])
        with self.assertRaises(NativeVmdBatchSamplerError):
            samples.bone_track("joint", [])
        samples.close()
        with self.assertRaisesRegex(RuntimeError, "samples are closed"):
            samples.bone_track("joint")

    def test_bone_track_reads_subset_across_native_chunks(self):
        samples = NativeVmdBatchSampler(_FakeCmds())
        with mock.patch.object(sampler_module, "MAX_NATIVE_SAMPLES", 12):
            native_samples = samples.sample_dense_bone_channels(
                [0, 1, 2, 3, 4], ["joint"]
            )
        track = native_samples.bone_track("joint", [0, 2, 4])
        self.assertEqual(track.frames, (0.0, 2.0, 4.0))
        self.assertEqual(
            list(track.translate_x),
            [native_samples.value("joint", "translateX", frame) for frame in track.frames],
        )
        native_samples.close()




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
        collector._bake_timeline_physics_output_excluded_targets = {"sparse"}
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
            with self.assertRaisesRegex(RuntimeError, "Bake Timeline native bone sampling failed"):
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
            with self.assertRaisesRegex(RuntimeError, "Bake Timeline native bone value failed"):
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
            "_bake_timeline_earliest_integer_sample",
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
