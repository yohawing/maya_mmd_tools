"""Pre-command diagnostics contracts for long native sampling phases."""

from __future__ import annotations

import unittest
from unittest import mock

from tests.common.maya_stub import install_maya_stub

install_maya_stub()

from mmd_tools.converters import vmd_scene_collector as collector_module  # noqa: E402
from mmd_tools.converters.vmd_scene_collector import VmdSceneCollector  # noqa: E402


class _Cmds:
    def ls(self, node, long=False):
        return [str(node)]

    def nodeType(self, node):
        return "mmdPhysicsBoneDriver" if str(node) == "|driver" else "joint"


class _Evaluator:
    def value(self, _joint, _attr, frame, _route):
        return float(frame)


class _FailingSampler:
    available = False

    def set_diagnostics_sink(self, sink):
        self._sink = sink

    def sample_dense_bone_channels(self, _frames, _joints, _routes):
        self._sink(
            {
                "status": "sampling_chunk",
                "chunk_index": 0,
                "channel_count": 6,
                "protocol_failure": True,
                "fallback_reason": "packed protocol mismatch",
            }
        )
        raise RuntimeError("packed protocol mismatch")


class VmdDiagnosticsTests(unittest.TestCase):
    def test_collector_flushes_route_inventory_before_native_call(self):
        events = []
        collector = VmdSceneCollector(
            diagnostics_sink=events.append,
            bone_channel_sampler=_FailingSampler(),
        )
        with mock.patch.object(collector_module, "cmds", _Cmds()), mock.patch.object(
            collector_module, "_RoutedPlugValueEvaluator", _Evaluator
        ), mock.patch.object(
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
            collector.collect_bone_frames(
                ["joint"],
                input_routes={"joint": {"rotateX": ("|driver", "inPreRotateX")}},
                dense_sample=True,
                force_dense_sample=True,
                dense_frame_samples=[0, 1],
                time_converter=lambda value: value,
                bone_channel_sampler=collector._bone_channel_sampler,
            )
        self.assertGreaterEqual(len(events), 2)
        preflight = events[0]["native_sampler"]
        self.assertEqual(preflight["status"], "preflight")
        self.assertEqual(preflight["route_target_node_count"], 2)
        self.assertEqual(preflight["route_target_node_types"]["mmdPhysicsBoneDriver"], 1)
        self.assertEqual(preflight["physics_driver_reached_count"], 1)
        chunk = events[1]["native_sampler"]
        self.assertEqual(chunk["chunk_index"], 0)
        self.assertTrue(chunk["protocol_failure"])
        self.assertIn("protocol mismatch", chunk["fallback_reason"])


if __name__ == "__main__":
    unittest.main()
