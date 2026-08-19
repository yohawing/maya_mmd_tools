"""VMD scene collector の Maya 非依存ロジックを検証するテスト。"""

import json
import math
import unittest
from types import SimpleNamespace
from unittest import mock

from tests.common.maya_stub import install_maya_stub

install_maya_stub()

from mmd_tools.converters import vmd_scene_collector as collector_module  # noqa: E402
from mmd_tools.converters.vmd_scene_collector import VmdSceneCollector  # noqa: E402
from mmd_tools.core.constants import (  # noqa: E402
    ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON,
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_CAMERA,
    ATTR_MMD_LIGHT,
    ATTR_MMD_MODEL_NAME,
    ATTR_MMD_VMD_IMPORT_PROVENANCE_JSON,
)


class FakeVector:
    """Tiny vector helper for collector camera export tests."""

    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def __sub__(self, other):
        return FakeVector(self.x - other.x, self.y - other.y, self.z - other.z)

    def __mul__(self, other):
        if isinstance(other, FakeVector):
            return self.x * other.x + self.y * other.y + self.z * other.z
        return FakeVector(self.x, self.y, self.z)

    def length(self):
        return (self.x * self.x + self.y * self.y + self.z * self.z) ** 0.5

    def normalize(self):
        length = self.length()
        if length > 1e-12:
            self.x /= length
            self.y /= length
            self.z /= length


class FakeOpenMaya:
    """OpenMaya subset used by VmdSceneCollector's aim-roll camera path."""

    MVector = FakeVector

    class MMatrix:
        def __init__(self, values):
            self.values = values


class FakeCmds:
    """Small maya.cmds fake for VmdSceneCollector tests."""

    def __init__(self):
        self.node_types = {}
        self.children = {}
        self.attrs = {}
        self.keys = {}
        self.connections = {}
        self.histories = {}
        self.translations = {}
        self.world_matrices = {}
        self.current_time = 0.0
        self.blendshape_weights = {}
        self.aliases = {}
        self.current_unit = "ntsc"
        self.relative_calls = []
        self.current_time_calls = []
        self.get_attr_calls = []
        self.playing = False
        self.fail_current_time_at = None
        self.fail_restore_time = None

    def ls(self, pattern=None, type=None, objectsOnly=False, long=False, uuid=False):  # noqa: A002,N803
        if pattern and not type and not objectsOnly:
            if uuid:
                return [str(pattern)] if pattern in self.node_types else []
            return [pattern] if pattern in self.node_types else []
        if objectsOnly and isinstance(pattern, str) and pattern.startswith("*."):
            attr = pattern[2:]
            return [node for node, node_attr in self.attrs if node_attr == attr]
        return [node for node, node_type in self.node_types.items() if node_type == type]

    def listRelatives(  # noqa: A002,N803
        self,
        node,
        allDescendents=False,
        type=None,
        fullPath=False,
        shapes=False,
    ):
        self.relative_calls.append(
            {
                "node": node,
                "type": type,
                "fullPath": fullPath,
                "shapes": shapes,
            }
        )
        result = []
        for child in self.children.get(node, []):
            if type is None or self.node_types.get(child) == type:
                result.append(child)
            if allDescendents:
                result.extend(self.listRelatives(child, allDescendents=True, type=type, fullPath=fullPath) or [])
        return result

    def nodeType(self, node):  # noqa: N802
        return self.node_types.get(node)

    def listHistory(self, node, pruneDagObjects=False):  # noqa: N802,N803
        return list(self.histories.get(node, []))

    def attributeQuery(self, attr, node, exists=False):  # noqa: N802
        return exists and (node, attr) in self.attrs

    def getAttr(self, plug, time=None):  # noqa: N802
        self.get_attr_calls.append((plug, time, self.current_time))
        node, attr = plug.split(".", 1)
        if attr == "worldMatrix[0]":
            return self.world_matrices.get(
                node,
                (
                    1.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    1.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    1.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    1.0,
                ),
            )
        if time is not None:
            return self.keys.get((node, attr), {}).get(float(time), self.attrs.get((node, attr), 0.0))
        if (node, attr) in self.keys:
            return self.keys[(node, attr)].get(
                self.current_time, self.attrs.get((node, attr), 0.0)
            )
        return self.attrs.get((node, attr), 0.0)

    def listConnections(self, plug, source=False, destination=False, **_kwargs):  # noqa: N802,N803
        node, attr = plug.split(".", 1)
        return list(self.connections.get((node, attr, bool(source), bool(destination)), []))

    def currentTime(self, time=None, edit=False, query=False):  # noqa: N802
        if query:
            return self.current_time
        if edit:
            if self.fail_current_time_at is not None and float(time) == float(
                self.fail_current_time_at
            ):
                raise RuntimeError("timeline evaluation failed")
            if self.fail_restore_time is not None and float(time) == float(
                self.fail_restore_time
            ):
                raise RuntimeError("timeline restoration failed")
            self.current_time = float(time)
            self.current_time_calls.append(float(time))
        return self.current_time

    def play(self, query=False, state=False):
        if query and state:
            return self.playing
        return None

    def currentUnit(self, time=None, query=False):  # noqa: N802
        if query:
            return self.current_unit
        self.current_unit = time
        return self.current_unit

    def xform(self, node, query=False, worldSpace=False, translation=False):  # noqa: N802,N803
        if query and worldSpace and translation:
            return self.translations.get((node, self.current_time), self.translations.get(node, (0.0, 0.0, 0.0)))
        return None

    def keyframe(self, plug, query=False, timeChange=False):  # noqa: N803
        node, attr = plug.split(".", 1)
        if query and timeChange:
            return list(self.keys.get((node, attr), {}))
        return []

    def blendShape(self, node, query=False, weightCount=False):  # noqa: N802,N803
        if query and weightCount:
            return self.blendshape_weights.get(node, 0)
        return None

    def aliasAttr(self, plug, query=False):  # noqa: N802
        return self.aliases.get(plug) if query else None


class TestVmdSceneCollector(unittest.TestCase):
    """VmdSceneCollector の最小収集契約を検証する。"""

    def setUp(self):
        self.cmds = FakeCmds()
        self.original_cmds = collector_module.cmds
        self.original_read_control_rig_metadata = collector_module.read_mmd_control_rig_metadata
        collector_module.cmds = self.cmds
        collector_module.read_mmd_control_rig_metadata = lambda _target_model: None

    def tearDown(self):
        collector_module.cmds = self.original_cmds
        collector_module.read_mmd_control_rig_metadata = self.original_read_control_rig_metadata

    def _timeline_sampler(self):
        cmds_module = self.cmds

        class Samples:
            def value(self, joint, attr, frame):
                return float(cmds_module.getAttr(f"{joint}.{attr}", time=frame))

        class Sampler:
            available = True

            def sample_dense_bone_channels(self, _frames, _joints, _routes):
                return Samples()

        return Sampler()

    def test_mode_c_requires_timeline_native_sampler(self):
        self.cmds.node_types.update({"model_root": "transform", "center_joint": "joint"})
        self.cmds.children["model_root"] = ["center_joint"]
        self.cmds.attrs[("center_joint", ATTR_MMD_BONE_NAME)] = "センター"
        self.cmds.keys[("center_joint", "translateX")] = {0.0: 0.0, 2.0: 1.0}

        with self.assertRaisesRegex(RuntimeError, "native bone sampling"):
            VmdSceneCollector().collect(
                {
                    "target_model": "model_root",
                    "vmd_mode": "C",
                    "frame_range": (0, 2),
                }
            )

    def test_indexes_raw_transform_frames_once_by_bone(self):
        raw = {
            ("center", 0): ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)),
            ("center", 10): ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)),
            ("arm", 4): ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)),
        }

        self.assertEqual(
            collector_module._index_raw_bone_transform_frames(raw),
            {"center": {0, 10}, "arm": {4}},
        )

    def test_collects_bone_frames_from_mmd_named_joints(self):
        self.cmds.node_types.update({"model_root": "transform", "center_joint": "joint"})
        self.cmds.children["model_root"] = ["center_joint"]
        self.cmds.attrs[("model_root", ATTR_MMD_MODEL_NAME)] = "TestModel"
        self.cmds.attrs[("center_joint", ATTR_MMD_BONE_NAME)] = "センター"
        self.cmds.keys[("center_joint", "translateX")] = {0.0: 1.0, 10.0: 2.0}
        self.cmds.keys[("center_joint", "translateY")] = {0.0: 0.0, 10.0: 3.0}
        self.cmds.keys[("center_joint", "translateZ")] = {0.0: 0.0, 10.0: 4.0}
        self.cmds.keys[("center_joint", "rotateX")] = {0.0: 0.0, 10.0: 0.0}
        self.cmds.keys[("center_joint", "rotateY")] = {0.0: 0.0, 10.0: 0.0}
        self.cmds.keys[("center_joint", "rotateZ")] = {0.0: 0.0, 10.0: 90.0}

        result = VmdSceneCollector().collect({"target_model": "model_root"})

        self.assertEqual(result["model_name"], "TestModel")
        self.assertEqual(len(result["bone_frames"]), 2)
        self.assertEqual(result["bone_frames"][0]["bone_name"], "センター")
        self.assertEqual(result["bone_frames"][0]["frame_number"], 0)
        self.assertEqual(result["bone_frames"][0]["position"], (1.0, 0.0, 0.0))
        self.assertEqual(result["bone_frames"][0]["rotation"], (0.0, 0.0, 0.0, 1.0))
        self.assertEqual(result["bone_frames"][1]["bone_name"], "センター")
        self.assertEqual(result["bone_frames"][1]["frame_number"], 10)
        self.assertEqual(result["bone_frames"][1]["position"], (2.0, 3.0, -4.0))
        self.assertAlmostEqual(result["bone_frames"][1]["rotation"][2], 0.7071067811865476)
        self.assertAlmostEqual(result["bone_frames"][1]["rotation"][3], 0.7071067811865476)

    def test_mode_c_dense_samples_requested_frame_range(self):
        self.cmds.node_types.update({"model_root": "transform", "center_joint": "joint"})
        self.cmds.children["model_root"] = ["center_joint"]
        self.cmds.attrs[("center_joint", ATTR_MMD_BONE_NAME)] = "センター"
        for attribute in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"):
            self.cmds.keys[("center_joint", attribute)] = {0.0: 0.0, 2.0: 1.0}

        result = VmdSceneCollector(bone_channel_sampler=self._timeline_sampler()).collect(
            {
                "target_model": "model_root",
                "vmd_mode": "C",
                "frame_range": (0, 2),
            }
        )

        self.assertEqual(
            [frame["frame_number"] for frame in result["bone_frames"]],
            [0, 1, 2],
        )
        self.assertNotIn("interpolation", result["bone_frames"][0])

    def test_mode_c_morph_sampling_is_frame_major_current_time_and_restores(self):
        self.cmds.node_types["face_bs"] = "blendShape"
        self.cmds.blendshape_weights["face_bs"] = 2
        self.cmds.aliases.update(
            {
                "face_bs.weight[0]": "smile",
                "face_bs.weight[1]": "blink",
            }
        )
        self.cmds.keys[("face_bs", "weight[0]")] = {0.0: 0.0, 2.0: 0.8}
        self.cmds.keys[("face_bs", "weight[1]")] = {0.0: 0.1, 2.0: 0.9}
        self.cmds.current_time = 9.0

        frames = VmdSceneCollector().collect_morph_frames(
            ["face_bs"],
            time_converter=lambda value: value,
            dense_sample=True,
            dense_frame_samples=[2, 0, 1],
            timeline_evaluation=True,
        )

        self.assertEqual(len(frames), 6)
        self.assertEqual(self.cmds.current_time_calls, [0.0, 1.0, 2.0, 9.0])
        sampled_reads = [
            call for call in self.cmds.get_attr_calls if call[0].startswith("face_bs.weight")
        ]
        self.assertTrue(sampled_reads)
        self.assertTrue(all(time is None for _plug, time, _current in sampled_reads))
        self.assertEqual(self.cmds.current_time, 9.0)

    def test_sparse_morph_sampling_keeps_alternate_time_reads(self):
        self.cmds.node_types["face_bs"] = "blendShape"
        self.cmds.blendshape_weights["face_bs"] = 1
        self.cmds.keys[("face_bs", "weight[0]")] = {0.0: 0.0, 2.0: 0.8}

        VmdSceneCollector().collect_morph_frames(["face_bs"])

        sampled_reads = [
            call
            for call in self.cmds.get_attr_calls
            if call[0] == "face_bs.weight[0]"
        ]
        self.assertEqual([time for _plug, time, _current in sampled_reads], [0.0, 2.0])
        self.assertEqual(self.cmds.current_time_calls, [])

    def test_mode_c_camera_and_light_use_current_frame_without_double_scrub(self):
        self.cmds.node_types.update(
            {"mmd_camera": "transform", "mmd_light": "transform"}
        )
        for attr in (
            "translateX",
            "translateY",
            "translateZ",
            "rotateX",
            "rotateY",
            "rotateZ",
            "mmd_camera_distance",
            "mmd_camera_viewing_angle",
            "mmd_camera_perspective",
        ):
            self.cmds.keys[("mmd_camera", attr)] = {0.0: 0.0, 2.0: 2.0}
        for attr in (
            "mmd_light_colorR",
            "mmd_light_colorG",
            "mmd_light_colorB",
            "rotateX",
            "rotateY",
        ):
            self.cmds.keys[("mmd_light", attr)] = {0.0: 0.0, 2.0: 1.0}
        self.cmds.current_time = 7.0

        VmdSceneCollector().collect_camera_frames(
            ["mmd_camera"],
            time_converter=lambda value: value,
            dense_sample=True,
            dense_frame_samples=[2, 0, 1],
            timeline_evaluation=True,
        )
        self.assertEqual(self.cmds.current_time_calls, [0.0, 1.0, 2.0, 7.0])
        camera_reads = [
            call for call in self.cmds.get_attr_calls if call[0].startswith("mmd_camera.")
        ]
        self.assertTrue(all(time is None for _plug, time, _current in camera_reads))

        self.cmds.current_time_calls.clear()
        self.cmds.get_attr_calls.clear()
        VmdSceneCollector().collect_light_frames(
            ["mmd_light"],
            time_converter=lambda value: value,
            dense_sample=True,
            dense_frame_samples=[2, 0, 1],
            timeline_evaluation=True,
        )
        self.assertEqual(self.cmds.current_time_calls, [0.0, 1.0, 2.0, 7.0])
        light_reads = [
            call for call in self.cmds.get_attr_calls if call[0].startswith("mmd_light.")
        ]
        self.assertTrue(all(time is None for _plug, time, _current in light_reads))

    def test_mode_c_ik_uses_ascending_current_time_and_restores(self):
        self.cmds.attrs[("ik_solver", "enabled")] = False
        self.cmds.keys[("ik_solver", "enabled")] = {2.0: True}
        self.cmds.current_time = 8.0
        with mock.patch.object(
            collector_module,
            "collect_ik_nodes_by_bone_name",
            return_value={"左足ＩＫ": "ik_solver"},
        ):
            frames = VmdSceneCollector().collect_ik_show_hide_frames(
                "model_root",
                time_converter=lambda value: value,
                timeline_evaluation=True,
            )

        self.assertEqual([row["frame_number"] for row in frames], [0, 2])
        self.assertEqual(self.cmds.current_time_calls, [0.0, 2.0, 8.0])
        ik_reads = [
            call for call in self.cmds.get_attr_calls if call[0] == "ik_solver.enabled"
        ]
        self.assertTrue(all(time is None for _plug, time, _current in ik_reads))

    def test_mode_c_timeline_blocks_playback_and_restores_after_sample_error(self):
        self.cmds.node_types["face_bs"] = "blendShape"
        self.cmds.blendshape_weights["face_bs"] = 1
        self.cmds.keys[("face_bs", "weight[0]")] = {0.0: 0.0, 1.0: 1.0}
        self.cmds.playing = True
        with self.assertRaisesRegex(RuntimeError, "during playback"):
            VmdSceneCollector().collect_morph_frames(
                ["face_bs"],
                dense_sample=True,
                dense_frame_samples=[0, 1],
                timeline_evaluation=True,
            )

        self.cmds.playing = False
        self.cmds.current_time = 7.0
        self.cmds.fail_current_time_at = 1.0
        with self.assertRaisesRegex(RuntimeError, "at frame 1"):
            VmdSceneCollector().collect_morph_frames(
                ["face_bs"],
                dense_sample=True,
                dense_frame_samples=[0, 1],
                timeline_evaluation=True,
            )
        self.assertEqual(self.cmds.current_time, 7.0)

    def test_mode_c_timeline_restore_failure_blocks_export(self):
        self.cmds.node_types["face_bs"] = "blendShape"
        self.cmds.blendshape_weights["face_bs"] = 1
        self.cmds.keys[("face_bs", "weight[0]")] = {0.0: 0.5}
        self.cmds.current_time = 7.0
        self.cmds.fail_restore_time = 7.0

        with self.assertRaisesRegex(RuntimeError, "restoration failed"):
            VmdSceneCollector().collect_morph_frames(
                ["face_bs"],
                dense_sample=True,
                dense_frame_samples=[0],
                timeline_evaluation=True,
            )

    def test_mode_c_timeline_reader_rejects_backward_sampling(self):
        self.cmds.current_time = 9.0

        with self.assertRaisesRegex(RuntimeError, "ascending order"):
            with collector_module._MayaTimelineReader() as reader:
                reader.set_frame(2)
                reader.set_frame(1)

        self.assertEqual(self.cmds.current_time, 9.0)

    def test_diagnostics_sink_preserves_collection_values_and_reports_counts(self):
        self.cmds.node_types.update({"model_root": "transform", "center_joint": "joint"})
        self.cmds.children["model_root"] = ["center_joint"]
        self.cmds.attrs["center_joint", ATTR_MMD_BONE_NAME] = "center"
        for attribute in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"):
            self.cmds.keys["center_joint", attribute] = {0.0: 0.0, 2.0: 1.0}

        options = {
            "target_model": "model_root",
            "vmd_mode": "C",
            "frame_range": (0, 2),
        }
        plain = VmdSceneCollector(bone_channel_sampler=self._timeline_sampler()).collect(options)
        captured = []
        instrumented = VmdSceneCollector(
            diagnostics_sink=captured.append,
            bone_channel_sampler=self._timeline_sampler(),
        )
        with_sink = instrumented.collect(options)

        self.assertEqual(plain, with_sink)
        self.assertGreaterEqual(len(captured), 2)
        diagnostics = instrumented.diagnostics
        self.assertEqual(diagnostics["status"], "completed")
        self.assertEqual(diagnostics["route_provenance_dense_planning"]["dense_frame_count"], 3)
        self.assertEqual(diagnostics["bone_collection"]["joint_count"], 1)
        self.assertEqual(diagnostics["bone_collection"]["frame_count"], 3)
        self.assertEqual(diagnostics["bone_collection"]["estimated_scalar_bone_reads"], 18)
        self.assertEqual(diagnostics["morph_collection"]["frame_count"], 0)
        self.assertEqual(diagnostics["camera_collection"]["frame_count"], 0)
        self.assertEqual(diagnostics["light_collection"]["frame_count"], 0)
        self.assertEqual(diagnostics["ik_collection"]["frame_count"], 0)
        self.assertGreaterEqual(diagnostics["total"]["wall_sec"], 0.0)

    def test_uses_complete_raw_interpolation_provenance_from_model_root(self):
        self.cmds.node_types.update({"model_root": "transform", "center_joint": "joint"})
        self.cmds.children["model_root"] = ["center_joint"]
        self.cmds.attrs[("model_root", ATTR_MMD_MODEL_NAME)] = "TestModel"
        self.cmds.attrs[("center_joint", ATTR_MMD_BONE_NAME)] = "センター"
        self.cmds.attrs[("model_root", ATTR_MMD_VMD_IMPORT_PROVENANCE_JSON)] = json.dumps(
            {
                "raw_bone_interpolation_complete": True,
                "raw_bone_key_count": 1,
                "raw_bone_interpolation": [
                    {
                        "bone_name": "センター",
                        "frame_number": 0,
                        "interpolation": [7] * 64,
                    }
                ],
            }
        )
        for attribute in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"):
            self.cmds.keys[("center_joint", attribute)] = {0.0: 0.0}

        result = VmdSceneCollector().collect({"target_model": "model_root"})

        self.assertIsNotNone(result["raw_provenance"])
        self.assertEqual(result["bone_frames"][0]["interpolation"], bytes([7]) * 64)

    def test_mode_c_preserves_sparse_keys_with_complete_raw_transform_provenance(self):
        self.cmds.node_types.update({"model_root": "transform", "center_joint": "joint"})
        self.cmds.children["model_root"] = ["center_joint"]
        self.cmds.attrs[("center_joint", ATTR_MMD_BONE_NAME)] = "センター"
        raw_records = [
            {
                "bone_name": "センター",
                "frame_number": frame,
                "position": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0, 1.0],
                "interpolation": [7] * 64,
            }
            for frame in (0, 2)
        ]
        self.cmds.attrs[("model_root", ATTR_MMD_VMD_IMPORT_PROVENANCE_JSON)] = json.dumps(
            {
                "raw_bone_interpolation_complete": True,
                "raw_bone_transform_complete": True,
                "raw_bone_key_count": len(raw_records),
                "raw_bone_interpolation": raw_records,
            }
        )
        for attribute in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"):
            self.cmds.keys[("center_joint", attribute)] = {0.0: 0.0, 2.0: 0.0}

        result = VmdSceneCollector().collect(
            {
                "target_model": "model_root",
                "vmd_mode": "C",
                "frame_range": (0, 2),
                "preserve_raw_bone_transforms": True,
            }
        )

        self.assertEqual([frame["frame_number"] for frame in result["bone_frames"]], [0, 2])
        self.assertEqual(result["bone_frames"][0]["interpolation"], bytes([7]) * 64)

    def test_mode_c_dense_bakes_when_raw_provenance_is_not_opted_in(self):
        self.cmds.node_types.update({"model_root": "transform", "center_joint": "joint"})
        self.cmds.children["model_root"] = ["center_joint"]
        self.cmds.attrs[("center_joint", ATTR_MMD_BONE_NAME)] = "センター"
        raw_records = [
            {
                "bone_name": "センター",
                "frame_number": frame,
                "position": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0, 1.0],
                "interpolation": [7] * 64,
            }
            for frame in (0, 2)
        ]
        self.cmds.attrs[("model_root", ATTR_MMD_VMD_IMPORT_PROVENANCE_JSON)] = json.dumps(
            {
                "raw_bone_interpolation_complete": True,
                "raw_bone_transform_complete": True,
                "raw_bone_key_count": len(raw_records),
                "raw_bone_interpolation": raw_records,
            }
        )
        for attribute in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"):
            self.cmds.keys[("center_joint", attribute)] = {0.0: 0.0, 2.0: 0.0}

        result = VmdSceneCollector(bone_channel_sampler=self._timeline_sampler()).collect(
            {
                "target_model": "model_root",
                "vmd_mode": "C",
                "frame_range": (0, 2),
                "preserve_raw_bone_transforms": False,
            }
        )

        self.assertEqual(
            [frame["frame_number"] for frame in result["bone_frames"]],
            [0, 1, 2],
        )

    def test_explicit_raw_roundtrip_preserves_transform_values(self):
        self.cmds.node_types.update({"model_root": "transform", "center_joint": "joint"})
        self.cmds.children["model_root"] = ["center_joint"]
        self.cmds.attrs[("center_joint", ATTR_MMD_BONE_NAME)] = "センター"
        raw_record = {
            "bone_name": "センター",
            "frame_number": 0,
            "position": [1.0, 2.0, 3.0],
            "rotation": [0.0, 0.0, 0.3826834324, 0.9238795325],
            "interpolation": [7] * 64,
        }
        self.cmds.attrs[("model_root", ATTR_MMD_VMD_IMPORT_PROVENANCE_JSON)] = json.dumps(
            {
                "raw_bone_interpolation_complete": True,
                "raw_bone_transform_complete": True,
                "raw_bone_key_count": 1,
                "raw_bone_interpolation": [raw_record],
            }
        )
        for attribute in (
            "translateX",
            "translateY",
            "translateZ",
            "rotateX",
            "rotateY",
            "rotateZ",
        ):
            self.cmds.keys[("center_joint", attribute)] = {0.0: 0.0}

        result = VmdSceneCollector().collect(
            {
                "target_model": "model_root",
                "vmd_mode": "C",
                "preserve_raw_bone_transforms": True,
            }
        )

        frame = result["bone_frames"][0]
        self.assertEqual(frame["position"], (1.0, 2.0, 3.0))
        self.assertEqual(frame["rotation"], (0.0, 0.0, 0.3826834324, 0.9238795325))

    def test_rejects_raw_provenance_with_inconsistent_key_count(self):
        self.cmds.attrs[("model_root", ATTR_MMD_VMD_IMPORT_PROVENANCE_JSON)] = json.dumps(
            {
                "raw_bone_interpolation_complete": True,
                "raw_bone_key_count": 2,
                "raw_bone_interpolation": [
                    {
                        "bone_name": "センター",
                        "frame_number": 0,
                        "interpolation": [7] * 64,
                    }
                ],
            }
        )

        result = VmdSceneCollector().collect({"target_model": "model_root"})

        self.assertIsNone(result["raw_provenance"])

    def test_auto_discovery_is_scoped_to_namespaced_model_root(self):
        root = "|hero:model_ROOT"
        mesh_group = "|hero:model_ROOT|hero:Geometry"
        mesh_shape = "|hero:model_ROOT|hero:Geometry|hero:meshShape"
        owned_blend_shape = "|hero:faceBlendShape"
        foreign_blend_shape = "|rival:faceBlendShape"
        owned_camera = "|hero:model_ROOT|hero:mmd_camera"
        foreign_camera = "|rival:mmd_camera"
        owned_light = "|hero:model_ROOT|hero:mmd_light"
        foreign_light = "|rival:mmd_light"
        self.cmds.node_types.update(
            {
                root: "transform",
                mesh_group: "transform",
                mesh_shape: "mesh",
                owned_blend_shape: "blendShape",
                foreign_blend_shape: "blendShape",
                owned_camera: "transform",
                foreign_camera: "transform",
                owned_light: "transform",
                foreign_light: "transform",
            }
        )
        self.cmds.children[root] = [mesh_group, owned_camera, owned_light]
        self.cmds.children[mesh_group] = [mesh_shape]
        self.cmds.histories[mesh_shape] = [owned_blend_shape]
        self.cmds.attrs.update(
            {
                (owned_camera, ATTR_MMD_CAMERA): True,
                (foreign_camera, ATTR_MMD_CAMERA): True,
                (owned_light, ATTR_MMD_LIGHT): True,
                (foreign_light, ATTR_MMD_LIGHT): True,
            }
        )

        collector = VmdSceneCollector()

        self.assertEqual(collector._find_blend_shapes(root), [owned_blend_shape])
        self.assertEqual(
            collector._find_tagged_nodes(ATTR_MMD_CAMERA, root),
            [owned_camera],
        )
        self.assertEqual(
            collector._find_tagged_nodes(ATTR_MMD_LIGHT, root),
            [owned_light],
        )

    def test_targetless_auto_discovery_fails_closed_on_tagged_camera_decoy(self):
        self.cmds.node_types.update({"camera_a": "transform", "camera_b": "transform"})
        self.cmds.attrs.update(
            {
                ("camera_a", ATTR_MMD_CAMERA): True,
                ("camera_b", ATTR_MMD_CAMERA): True,
            }
        )

        with self.assertRaisesRegex(RuntimeError, "multiple tagged nodes"):
            VmdSceneCollector().collect()

    def test_target_model_does_not_hide_ambiguous_scene_camera_track(self):
        root = "|hero:model_ROOT"
        camera_a = "|hero:model_ROOT|hero:camera_a"
        camera_b = "|rival:camera_b"
        self.cmds.node_types.update(
            {root: "transform", camera_a: "transform", camera_b: "transform"}
        )
        self.cmds.attrs.update(
            {
                (camera_a, ATTR_MMD_CAMERA): True,
                (camera_b, ATTR_MMD_CAMERA): True,
            }
        )

        with self.assertRaisesRegex(RuntimeError, "multiple tagged nodes"):
            VmdSceneCollector().collect({"target_model": root})

    def test_target_model_does_not_hide_ambiguous_scene_light_track(self):
        root = "|hero:model_ROOT"
        light_a = "|hero:model_ROOT|hero:light_a"
        light_b = "|rival:light_b"
        self.cmds.node_types.update(
            {root: "transform", light_a: "transform", light_b: "transform"}
        )
        self.cmds.attrs.update(
            {
                (light_a, ATTR_MMD_LIGHT): True,
                (light_b, ATTR_MMD_LIGHT): True,
            }
        )

        with self.assertRaisesRegex(RuntimeError, "multiple tagged nodes"):
            VmdSceneCollector().collect({"target_model": root})

    def test_explicit_multiple_camera_nodes_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError, "multiple tagged nodes"):
            VmdSceneCollector().collect({"cameras": ["camera_a", "camera_b"]})

    def test_explicit_empty_camera_and_light_lists_skip_auto_discovery(self):
        self.cmds.node_types.update({"camera_auto": "transform", "light_auto": "transform"})
        self.cmds.attrs.update(
            {
                ("camera_auto", ATTR_MMD_CAMERA): True,
                ("light_auto", ATTR_MMD_LIGHT): True,
            }
        )

        result = VmdSceneCollector().collect({"cameras": [], "lights": []})

        self.assertEqual(result["camera_frames"], [])
        self.assertEqual(result["light_frames"], [])

    def test_collects_bone_morph_base_channels_from_control_rig_metadata(self):
        self.cmds.node_types.update(
            {
                "model_root": "transform",
                "center_joint": "joint",
                "center_accum": "mmdBoneMorphAccum",
            }
        )
        self.cmds.children["model_root"] = ["center_joint"]
        self.cmds.attrs[("center_joint", ATTR_MMD_BONE_NAME)] = "センター"
        for attr, values in {
            "baseTranslateX": {0.0: 0.0, 10.0: 3.0},
            "baseTranslateY": {0.0: 0.0, 10.0: 0.0},
            "baseTranslateZ": {0.0: 0.0, 10.0: 0.0},
            "baseRotateX": {0.0: 0.0, 10.0: 0.0},
            "baseRotateY": {0.0: 0.0, 10.0: 0.0},
            "baseRotateZ": {0.0: 0.0, 10.0: 90.0},
        }.items():
            self.cmds.keys[("center_accum", attr)] = values
        metadata = {
            "state": "BAKED",
            "bindings": {
                "center": {
                    "inputKind": "bone_morph_base",
                    "joint": "center_joint",
                    "authoredPlugs": [
                        "center_accum.baseTranslate",
                        "center_accum.baseRotate",
                    ],
                }
            },
        }
        with mock.patch.object(collector_module, "read_mmd_control_rig_metadata", return_value=metadata), mock.patch.object(
            collector_module, "collect_append_info", return_value={}
        ), mock.patch.object(collector_module, "collect_mmd_ik_passthrough_info", return_value={}):
            result = VmdSceneCollector().collect({"target_model": "model_root"})

        center_frames = [row for row in result["bone_frames"] if row["bone_name"] == "センター"]
        self.assertEqual(len(center_frames), 11)
        self.assertEqual(center_frames[-1]["frame_number"], 10)
        self.assertEqual(center_frames[-1]["position"], (3.0, 0.0, 0.0))
        self.assertAlmostEqual(center_frames[-1]["rotation"][2], 0.7071067811865476)
        self.assertAlmostEqual(center_frames[-1]["rotation"][3], 0.7071067811865476)

    def test_collects_ik_controller_base_channels_from_control_rig_metadata(self):
        self.cmds.node_types.update(
            {
                "model_root": "transform",
                "left_ik_joint": "joint",
                "left_ik_accum": "mmdBoneMorphAccum",
            }
        )
        self.cmds.children["model_root"] = ["left_ik_joint"]
        self.cmds.attrs[("left_ik_joint", ATTR_MMD_BONE_NAME)] = "左足ＩＫ"
        self.cmds.keys[("left_ik_accum", "baseTranslateX")] = {0.0: 0.0, 3.0: 0.35}
        self.cmds.keys[("left_ik_accum", "baseTranslateY")] = {0.0: 0.0, 3.0: 0.0}
        self.cmds.keys[("left_ik_accum", "baseTranslateZ")] = {0.0: 0.0, 3.0: 0.0}
        for axis in "XYZ":
            self.cmds.keys[("left_ik_accum", f"baseRotate{axis}")] = {0.0: 0.0, 3.0: 0.0}
        metadata = {
            "state": "BAKED",
            "bindings": {
                "left_foot_ik": {
                    "inputKind": "ik_controller",
                    "joint": "left_ik_joint",
                    "authoredPlugs": [
                        "left_ik_accum.baseTranslate",
                        "left_ik_accum.baseRotate",
                    ],
                }
            },
        }
        with mock.patch.object(collector_module, "read_mmd_control_rig_metadata", return_value=metadata), mock.patch.object(
            collector_module, "collect_append_info", return_value={}
        ), mock.patch.object(collector_module, "collect_mmd_ik_passthrough_info", return_value={}):
            result = VmdSceneCollector().collect({"target_model": "model_root"})

        left_ik_frames = [
            row for row in result["bone_frames"] if row["bone_name"] == "左足ＩＫ"
        ]
        self.assertEqual([row["frame_number"] for row in left_ik_frames], [0, 1, 2, 3])
        self.assertEqual(left_ik_frames[-1]["position"], (0.35, 0.0, 0.0))

    def test_experimental_rotation_tracks_stay_sparse_while_other_bones_are_dense(self):
        self.cmds.node_types.update(
            {
                "sparse_joint": "joint",
                "dense_joint": "joint",
            }
        )
        self.cmds.attrs[("sparse_joint", ATTR_MMD_BONE_NAME)] = "下半身"
        self.cmds.attrs[("dense_joint", ATTR_MMD_BONE_NAME)] = "左足ＩＫ"
        for joint in ("sparse_joint", "dense_joint"):
            for attr in (
                "translateX",
                "translateY",
                "translateZ",
                "rotateX",
                "rotateY",
                "rotateZ",
            ):
                self.cmds.keys[(joint, attr)] = {0.0: 0.0, 2.0: 1.0}

        frames = VmdSceneCollector().collect_bone_frames(
            ["sparse_joint", "dense_joint"],
            dense_sample=True,
            rotation_interpolation={"下半身": {2: bytes([20] * 64)}},
            time_converter=lambda value: value,
        )

        sparse_frames = [
            row["frame_number"] for row in frames if row["bone_name"] == "下半身"
        ]
        dense_frames = [
            row["frame_number"] for row in frames if row["bone_name"] == "左足ＩＫ"
        ]
        self.assertEqual(sparse_frames, [0, 2])
        self.assertEqual(dense_frames, [0, 1, 2])

    def test_rejects_control_rig_export_while_editing(self):
        collector_module.read_mmd_control_rig_metadata = lambda _target_model: {
            "state": "EDIT"
        }

        with self.assertRaisesRegex(ValueError, "Bake the MMD control rig"):
            VmdSceneCollector().collect({"target_model": "model_root"})

    def test_collects_bone_translate_as_bind_relative_scaled_vmd_offset(self):
        self.cmds.node_types["center_joint"] = "joint"
        self.cmds.attrs[("center_joint", ATTR_MMD_BONE_NAME)] = "センター"
        self.cmds.keys[("center_joint", "translateX")] = {12.0: 5.0}
        self.cmds.keys[("center_joint", "translateY")] = {12.0: 8.0}
        self.cmds.keys[("center_joint", "translateZ")] = {12.0: -1.0}

        result = VmdSceneCollector().collect(
            {
                "joints": ["center_joint"],
                "motion_scale": 2.0,
                "bone_bind_poses": {"センター": (3.0, 4.0, 5.0)},
            }
        )

        self.assertEqual(result["bone_frames"][0]["position"], (1.0, 2.0, 3.0))

    def test_converts_maya_60fps_time_to_fixed_30fps_vmd_frames_for_all_tracks(self):
        self.cmds.current_unit = "ntscf"
        self.cmds.node_types.update(
            {
                "center_joint": "joint",
                "face_bs": "blendShape",
                "mmd_camera": "transform",
                "mmd_light": "transform",
            }
        )
        self.cmds.attrs.update(
            {
                ("center_joint", ATTR_MMD_BONE_NAME): "センター",
                ("face_bs", ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON): json.dumps({"0": "笑い"}),
                ("mmd_camera", ATTR_MMD_CAMERA): True,
                ("mmd_light", ATTR_MMD_LIGHT): True,
            }
        )
        self.cmds.blendshape_weights["face_bs"] = 1
        self.cmds.keys[("center_joint", "translateX")] = {20.0: 1.0}
        self.cmds.keys[("face_bs", "weight[0]")] = {20.0: 0.5}
        self.cmds.keys[("mmd_camera", "translateX")] = {20.0: 1.0}
        self.cmds.keys[("mmd_light", "mmd_light_colorR")] = {20.0: 0.1}

        result = VmdSceneCollector().collect(
            {
                "joints": ["center_joint"],
                "blend_shapes": ["face_bs"],
                "cameras": ["mmd_camera"],
                "lights": ["mmd_light"],
            }
        )

        self.assertEqual(result["bone_frames"][0]["frame_number"], 10)
        self.assertEqual(result["morph_frames"][0]["frame_number"], 10)
        self.assertEqual(result["camera_frames"][0]["frame_number"], 10)
        self.assertEqual(result["light_frames"][0]["frame_number"], 10)

    def test_keeps_maya_30fps_time_as_same_vmd_frame(self):
        self.cmds.current_unit = "ntsc"
        self.cmds.node_types["center_joint"] = "joint"
        self.cmds.attrs[("center_joint", ATTR_MMD_BONE_NAME)] = "センター"
        self.cmds.keys[("center_joint", "translateX")] = {20.0: 1.0}

        result = VmdSceneCollector().collect({"joints": ["center_joint"]})

        self.assertEqual(result["bone_frames"][0]["frame_number"], 20)

    def test_collects_constant_off_ik_baseline_in_requested_range(self):
        self.cmds.attrs[("ik_solver", "enabled")] = False
        original_collect = collector_module.collect_ik_nodes_by_bone_name
        collector_module.collect_ik_nodes_by_bone_name = lambda **_kwargs: {"左足ＩＫ": "ik_solver"}
        try:
            result = VmdSceneCollector().collect(
                {
                    "target_model": "model_root",
                    "start_frame": 10.0,
                    "end_frame": 20.0,
                    "vmd_mode": "C",
                }
            )
        finally:
            collector_module.collect_ik_nodes_by_bone_name = original_collect

        self.assertEqual(
            result["ik_show_hide_frames"],
            [
                {
                    "frame_number": 10,
                    "visible": True,
                    "ik_states": [("左足ＩＫ", False)],
                }
            ],
        )

    def test_omits_keyless_all_on_ik_baseline(self):
        self.cmds.attrs.update(
            {
                ("left_ik_solver", "enabled"): True,
                ("right_ik_solver", "enabled"): True,
            }
        )
        original_collect = collector_module.collect_ik_nodes_by_bone_name
        collector_module.collect_ik_nodes_by_bone_name = lambda **_kwargs: {
            "左足ＩＫ": "left_ik_solver",
            "右足ＩＫ": "right_ik_solver",
        }
        try:
            result = VmdSceneCollector().collect_ik_show_hide_frames(
                "model_root",
                time_converter=lambda value: value,
            )
        finally:
            collector_module.collect_ik_nodes_by_bone_name = original_collect

        self.assertEqual(result, [])

    def test_dense_ik_samples_keep_source_state_keys(self):
        self.cmds.attrs[("ik_solver", "enabled")] = True
        self.cmds.keys[("ik_solver", "enabled")] = {2.0: 0.0}
        original_collect = collector_module.collect_ik_nodes_by_bone_name
        collector_module.collect_ik_nodes_by_bone_name = lambda **_kwargs: {
            "左足ＩＫ": "ik_solver",
        }
        try:
            result = VmdSceneCollector().collect_ik_show_hide_frames(
                "model_root",
                time_converter=lambda value: value,
                dense_sample=True,
                dense_frame_samples=[0, 1, 2],
            )
        finally:
            collector_module.collect_ik_nodes_by_bone_name = original_collect

        self.assertEqual(
            [row["frame_number"] for row in result],
            [0, 1, 2],
        )
        self.assertEqual(
            [row["ik_states"] for row in result],
            [[("左足ＩＫ", True)], [("左足ＩＫ", True)], [("左足ＩＫ", False)]],
        )

    def test_dense_ik_omits_keyless_all_on_property_section(self):
        self.cmds.attrs[("ik_solver", "enabled")] = True
        original_collect = collector_module.collect_ik_nodes_by_bone_name
        collector_module.collect_ik_nodes_by_bone_name = lambda **_kwargs: {
            "左足ＩＫ": "ik_solver",
        }
        try:
            result = VmdSceneCollector().collect_ik_show_hide_frames(
                "model_root",
                time_converter=lambda value: value,
                dense_sample=True,
                dense_frame_samples=[0, 1, 2],
            )
        finally:
            collector_module.collect_ik_nodes_by_bone_name = original_collect

        self.assertEqual(result, [])

    def test_dense_ik_keeps_keyless_constant_off_state(self):
        self.cmds.attrs[("ik_solver", "enabled")] = False
        original_collect = collector_module.collect_ik_nodes_by_bone_name
        collector_module.collect_ik_nodes_by_bone_name = lambda **_kwargs: {
            "左足ＩＫ": "ik_solver",
        }
        try:
            result = VmdSceneCollector().collect_ik_show_hide_frames(
                "model_root",
                time_converter=lambda value: value,
                dense_sample=True,
                dense_frame_samples=[0, 1, 2],
            )
        finally:
            collector_module.collect_ik_nodes_by_bone_name = original_collect

        self.assertEqual([row["frame_number"] for row in result], [0, 1, 2])
        self.assertEqual(
            [row["ik_states"] for row in result],
            [[("左足ＩＫ", False)]] * 3,
        )

    def test_collects_ik_baseline_before_later_enabled_key(self):
        self.cmds.attrs[("ik_solver", "enabled")] = False
        self.cmds.keys[("ik_solver", "enabled")] = {20.0: 1.0}
        original_collect = collector_module.collect_ik_nodes_by_bone_name
        collector_module.collect_ik_nodes_by_bone_name = lambda **_kwargs: {"左足ＩＫ": "ik_solver"}
        try:
            result = VmdSceneCollector().collect(
                {"target_model": "model_root", "vmd_mode": "C"}
            )
        finally:
            collector_module.collect_ik_nodes_by_bone_name = original_collect

        self.assertEqual(
            result["ik_show_hide_frames"],
            [
                {
                    "frame_number": 0,
                    "visible": True,
                    "ik_states": [("左足ＩＫ", False)],
                },
                {
                    "frame_number": 20,
                    "visible": True,
                    "ik_states": [("左足ＩＫ", True)],
                },
            ],
        )

    def test_mode_c_keeps_keyed_ik_sparse_when_other_tracks_are_dense(self):
        self.cmds.attrs[("ik_solver", "enabled")] = False
        self.cmds.keys[("ik_solver", "enabled")] = {0.0: 0.0}
        self.cmds.node_types["center_joint"] = "joint"
        self.cmds.attrs[("center_joint", ATTR_MMD_BONE_NAME)] = "センター"
        for attribute in (
            "translateX",
            "translateY",
            "translateZ",
            "rotateX",
            "rotateY",
            "rotateZ",
        ):
            self.cmds.keys[("center_joint", attribute)] = {0.0: 0.0, 3.0: 1.0}
        original_collect = collector_module.collect_ik_nodes_by_bone_name
        collector_module.collect_ik_nodes_by_bone_name = lambda **_kwargs: {
            "左足ＩＫ": "ik_solver",
        }
        try:
            result = VmdSceneCollector().collect(
                {
                    "target_model": "model_root",
                    "vmd_mode": "C",
                    "frame_range": (0, 3),
                }
            )
        finally:
            collector_module.collect_ik_nodes_by_bone_name = original_collect

        self.assertEqual(
            result["ik_show_hide_frames"],
            [
                {
                    "frame_number": 0,
                    "visible": True,
                    "ik_states": [("左足ＩＫ", False)],
                }
            ],
        )

    def test_does_not_emit_negative_ik_baseline_for_end_only_range(self):
        self.cmds.attrs[("ik_solver", "enabled")] = False
        original_collect = collector_module.collect_ik_nodes_by_bone_name
        collector_module.collect_ik_nodes_by_bone_name = lambda **_kwargs: {"左足ＩＫ": "ik_solver"}
        try:
            result = VmdSceneCollector().collect(
                {
                    "target_model": "model_root",
                    "end_frame": -1.0,
                }
            )
        finally:
            collector_module.collect_ik_nodes_by_bone_name = original_collect

        self.assertEqual(result["ik_show_hide_frames"], [])

    def test_collects_bone_rotation_with_vmd_quaternion_signs(self):
        self.cmds.node_types["arm_joint"] = "joint"
        self.cmds.attrs[("arm_joint", ATTR_MMD_BONE_NAME)] = "腕"
        self.cmds.keys[("arm_joint", "rotateX")] = {5.0: 90.0}

        result = VmdSceneCollector().collect({"joints": ["arm_joint"]})

        rotation = result["bone_frames"][0]["rotation"]
        self.assertAlmostEqual(rotation[0], -0.7071067811865476)
        self.assertAlmostEqual(rotation[1], -0.0)
        self.assertAlmostEqual(rotation[2], 0.0)
        self.assertAlmostEqual(rotation[3], 0.7071067811865476)

    def test_collects_morph_frames_from_stored_blendshape_names(self):
        self.cmds.node_types["face_bs"] = "blendShape"
        self.cmds.blendshape_weights["face_bs"] = 2
        self.cmds.attrs[("face_bs", ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON)] = json.dumps(
            {"0": "笑い"},
            ensure_ascii=False,
        )
        self.cmds.aliases["face_bs.weight[1]"] = "blink_alias"
        self.cmds.keys[("face_bs", "weight[0]")] = {0.0: 0.0, 15.0: 1.0}
        self.cmds.keys[("face_bs", "weight[1]")] = {15.0: 0.25}

        result = VmdSceneCollector().collect({"blend_shapes": ["face_bs"], "start_frame": 1})

        self.assertEqual(
            result["morph_frames"],
            [
                {"morph_name": "blink_alias", "frame_number": 15, "weight": 0.25},
                {"morph_name": "笑い", "frame_number": 15, "weight": 1.0},
            ],
        )

    def test_collects_model_scoped_network_morph_controller_keys(self):
        self.cmds.node_types.update(
            {
                "model_root": "transform",
                "morph_controller": "mmdMorphController",
            }
        )
        self.cmds.attrs[("model_root", "mmd_morph_controller")] = True
        self.cmds.connections[("model_root", "mmd_morph_controller", True, False)] = [
            "morph_controller"
        ]
        self.cmds.keys[("morph_controller", "inputWeight[3]")] = {
            0.0: 0.0,
            10.0: 0.75,
        }
        metadata = [
            SimpleNamespace(
                morph_type="bone",
                name="bone_morph",
                index=3,
            )
        ]
        with mock.patch.object(collector_module, "iter_morph_network_metadata", return_value=metadata):
            result = VmdSceneCollector().collect({"target_model": "model_root"})

        self.assertEqual(
            result["morph_frames"],
            [
                {"morph_name": "bone_morph", "frame_number": 0, "weight": 0.0},
                {"morph_name": "bone_morph", "frame_number": 10, "weight": 0.75},
            ],
        )

    def test_network_morph_rows_are_scoped_and_deduplicated(self):
        self.cmds.node_types.update(
            {
                "model_root": "transform",
                "morph_controller": "mmdMorphController",
                "face_bs": "blendShape",
            }
        )
        self.cmds.attrs.update(
            {
                ("model_root", "mmd_morph_controller"): True,
                ("face_bs", ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON): json.dumps(
                    {"0": "shared_morph"}, ensure_ascii=False
                ),
            }
        )
        self.cmds.connections[("model_root", "mmd_morph_controller", True, False)] = [
            "morph_controller"
        ]
        self.cmds.blendshape_weights["face_bs"] = 1
        self.cmds.keys[("face_bs", "weight[0]")] = {5.0: 0.25}
        self.cmds.keys[("morph_controller", "inputWeight[4]")] = {5.0: 0.9}
        metadata = [
            SimpleNamespace(morph_type="bone", name="shared_morph", index=4),
            # A duplicate index is ambiguous and must be skipped rather than
            # choosing one network provider.
            SimpleNamespace(morph_type="material", name="other_morph", index=4),
        ]
        with mock.patch.object(collector_module, "iter_morph_network_metadata", return_value=metadata):
            result = VmdSceneCollector().collect(
                {
                    "target_model": "model_root",
                    "blend_shapes": ["face_bs"],
                }
            )

        self.assertEqual(
            result["morph_frames"],
            [{"morph_name": "shared_morph", "frame_number": 5, "weight": 0.25}],
        )

    def test_collects_camera_frames_from_tagged_camera_controller(self):
        self.cmds.node_types["mmd_camera"] = "transform"
        self.cmds.attrs[("mmd_camera", ATTR_MMD_CAMERA)] = True
        self.cmds.keys[("mmd_camera", "translateX")] = {12.0: 1.0}
        self.cmds.keys[("mmd_camera", "translateY")] = {12.0: 2.0}
        self.cmds.keys[("mmd_camera", "translateZ")] = {12.0: -3.0}
        self.cmds.keys[("mmd_camera", "rotateX")] = {12.0: 10.0}
        self.cmds.keys[("mmd_camera", "rotateY")] = {12.0: 20.0}
        self.cmds.keys[("mmd_camera", "rotateZ")] = {12.0: -30.0}
        self.cmds.keys[("mmd_camera", "mmd_camera_distance")] = {12.0: -45.0}
        self.cmds.keys[("mmd_camera", "mmd_camera_viewing_angle")] = {12.0: 42.0}
        self.cmds.keys[("mmd_camera", "mmd_camera_perspective")] = {12.0: 1.0}

        result = VmdSceneCollector().collect()

        self.assertEqual(len(result["camera_frames"]), 1)
        frame = result["camera_frames"][0]
        self.assertEqual(frame["frame_number"], 12)
        self.assertEqual(frame["distance"], -45.0)
        self.assertEqual(frame["position"], (1.0, 2.0, 3.0))
        self.assertAlmostEqual(frame["rotation"][0], 0.17453292519943295)
        self.assertAlmostEqual(frame["rotation"][1], 0.3490658503988659)
        self.assertAlmostEqual(frame["rotation"][2], 0.5235987755982988)
        self.assertEqual(frame["viewing_angle"], 42)
        self.assertEqual(frame["perspective"], 1)

    def test_collects_imported_light_color_from_directional_shape(self):
        """Legacy VMD light imports keep color on the child shape."""
        self.cmds.node_types.update(
            {
                "mmd_light": "transform",
                "mmd_lightShape": "directionalLight",
            }
        )
        self.cmds.children["mmd_light"] = ["mmd_lightShape"]
        self.cmds.attrs[("mmd_light", ATTR_MMD_LIGHT)] = True
        self.cmds.attrs.update(
            {
                ("mmd_lightShape", "colorR"): 0.2,
                ("mmd_lightShape", "colorG"): 0.3,
                ("mmd_lightShape", "colorB"): 0.4,
            }
        )
        self.cmds.keys.update(
            {
                ("mmd_light", "rotateX"): {12.0: -30.0},
                ("mmd_light", "rotateY"): {12.0: 20.0},
                ("mmd_lightShape", "colorR"): {12.0: 0.2},
                ("mmd_lightShape", "colorG"): {12.0: 0.3},
                ("mmd_lightShape", "colorB"): {12.0: 0.4},
            }
        )

        result = VmdSceneCollector().collect({"lights": ["mmd_light"]})

        self.assertEqual(result["light_frames"], [
            {
                "frame_number": 12,
                "color": (0.2, 0.3, 0.4),
                "position": collector_module._maya_light_rotation_to_vmd_direction(-30.0, 20.0),
            }
        ])
        self.assertTrue(any(call["fullPath"] for call in self.cmds.relative_calls))

    def test_imported_light_shape_resolution_uses_full_path_for_same_name_shapes(self):
        light = "|hero:mmd_light"
        shape = "|hero:mmd_light|hero:mmd_lightShape"
        self.cmds.node_types.update({light: "transform", shape: "directionalLight"})
        self.cmds.children[light] = [shape]
        self.cmds.attrs[(light, ATTR_MMD_LIGHT)] = True
        for attr, value in {"colorR": 0.2, "colorG": 0.3, "colorB": 0.4}.items():
            self.cmds.attrs[(shape, attr)] = value
            self.cmds.keys[(shape, attr)] = {12.0: value}
        self.cmds.keys[(light, "rotateX")] = {12.0: -30.0}

        result = VmdSceneCollector().collect({"lights": [light]})

        self.assertEqual(result["light_frames"][0]["color"], (0.2, 0.3, 0.4))
        self.assertEqual(self.cmds.relative_calls[-1]["node"], light)
        self.assertTrue(self.cmds.relative_calls[-1]["fullPath"])

    def test_collects_camera_position_from_target_attrs_when_present(self):
        self.cmds.node_types["mmd_camera"] = "transform"
        self.cmds.attrs[("mmd_camera", ATTR_MMD_CAMERA)] = True
        self.cmds.attrs[("mmd_camera", "mmd_camera_rig_type")] = "mmd"
        self.cmds.attrs[("mmd_camera", "mmd_camera_target_x")] = 1.0
        self.cmds.attrs[("mmd_camera", "mmd_camera_target_y")] = 2.0
        self.cmds.attrs[("mmd_camera", "mmd_camera_target_z")] = 3.0
        self.cmds.keys[("mmd_camera", "translateX")] = {12.0: 99.0}
        self.cmds.keys[("mmd_camera", "translateY")] = {12.0: 99.0}
        self.cmds.keys[("mmd_camera", "translateZ")] = {12.0: 99.0}
        self.cmds.keys[("mmd_camera", "mmd_camera_target_x")] = {12.0: 1.0}
        self.cmds.keys[("mmd_camera", "mmd_camera_target_y")] = {12.0: 2.0}
        self.cmds.keys[("mmd_camera", "mmd_camera_target_z")] = {12.0: 3.0}
        self.cmds.keys[("mmd_camera", "rotateX")] = {12.0: 0.0}
        self.cmds.keys[("mmd_camera", "rotateY")] = {12.0: 0.0}
        self.cmds.keys[("mmd_camera", "rotateZ")] = {12.0: 0.0}
        self.cmds.keys[("mmd_camera", "mmd_camera_distance")] = {12.0: -45.0}
        self.cmds.keys[("mmd_camera", "mmd_camera_viewing_angle")] = {12.0: 42.0}
        self.cmds.keys[("mmd_camera", "mmd_camera_perspective")] = {12.0: 0.0}

        result = VmdSceneCollector().collect()

        self.assertEqual(result["camera_frames"][0]["position"], (1.0, 2.0, 3.0))

    def test_collects_aim_roll_camera_from_target_and_world_orientation(self):
        self.cmds.node_types.update(
            {
                "mmd_camera": "transform",
                "mmd_cameraShape": "camera",
                "mmd_camera_target": "transform",
            }
        )
        self.cmds.children["mmd_camera"] = ["mmd_cameraShape"]
        self.cmds.attrs[("mmd_camera", ATTR_MMD_CAMERA)] = True
        self.cmds.attrs[("mmd_camera", "mmd_camera_rig_type")] = "mmd_aim_roll"
        self.cmds.attrs[("mmd_camera", "mmd_camera_target_node")] = None
        self.cmds.connections[("mmd_camera", "mmd_camera_target_node", True, False)] = ["mmd_camera_target"]
        self.cmds.translations[("mmd_camera", 12.0)] = (1.0, 2.0, -1.0)
        self.cmds.translations[("mmd_camera_target", 12.0)] = (1.0, 2.0, -3.0)
        self.cmds.keys[("mmd_camera", "translateX")] = {12.0: 1.0}
        self.cmds.keys[("mmd_camera", "rotateZ")] = {12.0: -30.0}
        self.cmds.keys[("mmd_camera_target", "translateX")] = {12.0: 1.0}
        self.cmds.keys[("mmd_camera_target", "translateY")] = {12.0: 2.0}
        self.cmds.keys[("mmd_camera_target", "translateZ")] = {12.0: -3.0}
        self.cmds.attrs[("mmd_cameraShape", "focalLength")] = 25.4 / (2.0 * math.tan(math.radians(42.0) / 2.0))
        self.cmds.attrs[("mmd_cameraShape", "verticalFilmAperture")] = 1.0
        self.cmds.attrs[("mmd_cameraShape", "orthographic")] = 1.0
        self.cmds.keys[("mmd_cameraShape", "focalLength")] = {12.0: self.cmds.attrs[("mmd_cameraShape", "focalLength")]}
        self.cmds.keys[("mmd_cameraShape", "orthographic")] = {12.0: 1.0}
        self.cmds.current_time = 7.0

        calls = []

        def fake_rotation_from_forward_up(forward, up):
            calls.append((forward, up))
            return (0.1, 0.2, 0.3)

        original_rotation_from_forward_up = collector_module.mmd_camera_rotation_from_maya_forward_up
        original_om = collector_module.om
        collector_module.mmd_camera_rotation_from_maya_forward_up = fake_rotation_from_forward_up
        collector_module.om = FakeOpenMaya
        try:
            result = VmdSceneCollector().collect()
        finally:
            collector_module.mmd_camera_rotation_from_maya_forward_up = original_rotation_from_forward_up
            collector_module.om = original_om

        frame = result["camera_frames"][0]
        self.assertEqual(frame["frame_number"], 12)
        self.assertEqual(frame["position"], (1.0, 2.0, 3.0))
        self.assertEqual(frame["distance"], -2.0)
        self.assertEqual(frame["rotation"], (0.1, 0.2, 0.3))
        self.assertEqual(frame["viewing_angle"], 42)
        self.assertEqual(frame["perspective"], 1)
        self.assertEqual(calls, [((0.0, 0.0, -1.0), (0.0, 1.0, 0.0))])
        self.assertEqual(self.cmds.current_time, 7.0)

    def test_collects_aim_roll_camera_positive_distance_sign(self):
        self.cmds.node_types.update(
            {
                "mmd_camera": "transform",
                "mmd_camera_target": "transform",
            }
        )
        self.cmds.attrs[("mmd_camera", ATTR_MMD_CAMERA)] = True
        self.cmds.attrs[("mmd_camera", "mmd_camera_rig_type")] = "mmd_aim_roll"
        self.cmds.attrs[("mmd_camera", "mmd_camera_target_node")] = None
        self.cmds.connections[("mmd_camera", "mmd_camera_target_node", True, False)] = ["mmd_camera_target"]
        self.cmds.translations[("mmd_camera", 12.0)] = (1.0, 2.0, -5.0)
        self.cmds.translations[("mmd_camera_target", 12.0)] = (1.0, 2.0, -3.0)
        self.cmds.keys[("mmd_camera", "translateZ")] = {12.0: 2.0}

        original_rotation_from_forward_up = collector_module.mmd_camera_rotation_from_maya_forward_up
        original_om = collector_module.om
        collector_module.mmd_camera_rotation_from_maya_forward_up = lambda _forward, _up: (0.0, 0.0, 0.0)
        collector_module.om = FakeOpenMaya
        try:
            result = VmdSceneCollector().collect()
        finally:
            collector_module.mmd_camera_rotation_from_maya_forward_up = original_rotation_from_forward_up
            collector_module.om = original_om

        self.assertEqual(result["camera_frames"][0]["distance"], 2.0)

    def test_collects_aim_roll_camera_with_motion_scale(self):
        self.cmds.node_types.update(
            {
                "mmd_camera": "transform",
                "mmd_camera_target": "transform",
            }
        )
        self.cmds.attrs[("mmd_camera", ATTR_MMD_CAMERA)] = True
        self.cmds.attrs[("mmd_camera", "mmd_camera_rig_type")] = "mmd_aim_roll"
        self.cmds.attrs[("mmd_camera", "mmd_camera_target_node")] = None
        self.cmds.attrs[("mmd_camera", "mmd_camera_motion_scale")] = 2.0
        self.cmds.connections[("mmd_camera", "mmd_camera_target_node", True, False)] = ["mmd_camera_target"]
        self.cmds.translations[("mmd_camera", 12.0)] = (2.0, 4.0, -2.0)
        self.cmds.translations[("mmd_camera_target", 12.0)] = (2.0, 4.0, -6.0)
        self.cmds.keys[("mmd_camera", "translateZ")] = {12.0: 4.0}

        original_rotation_from_forward_up = collector_module.mmd_camera_rotation_from_maya_forward_up
        original_om = collector_module.om
        collector_module.mmd_camera_rotation_from_maya_forward_up = lambda _forward, _up: (0.0, 0.0, 0.0)
        collector_module.om = FakeOpenMaya
        try:
            result = VmdSceneCollector().collect()
        finally:
            collector_module.mmd_camera_rotation_from_maya_forward_up = original_rotation_from_forward_up
            collector_module.om = original_om

        frame = result["camera_frames"][0]
        self.assertEqual(frame["position"], (1.0, 2.0, 3.0))
        self.assertEqual(frame["distance"], -2.0)

    def test_collects_aim_roll_camera_frames_from_root_keys(self):
        self.cmds.node_types.update(
            {
                "mmd_camera": "transform",
                "mmd_camera_target": "transform",
                "mmd_camera_rig": "transform",
            }
        )
        self.cmds.attrs[("mmd_camera", ATTR_MMD_CAMERA)] = True
        self.cmds.attrs[("mmd_camera", "mmd_camera_rig_type")] = "mmd_aim_roll"
        self.cmds.attrs[("mmd_camera", "mmd_camera_target_node")] = None
        self.cmds.attrs[("mmd_camera", "mmd_camera_root_node")] = None
        self.cmds.connections[("mmd_camera", "mmd_camera_target_node", True, False)] = ["mmd_camera_target"]
        self.cmds.connections[("mmd_camera", "mmd_camera_root_node", True, False)] = ["mmd_camera_rig"]
        self.cmds.translations[("mmd_camera", 24.0)] = (1.0, 2.0, -1.0)
        self.cmds.translations[("mmd_camera_target", 24.0)] = (1.0, 2.0, -3.0)
        self.cmds.keys[("mmd_camera_rig", "translateX")] = {24.0: 1.0}

        original_rotation_from_forward_up = collector_module.mmd_camera_rotation_from_maya_forward_up
        original_om = collector_module.om
        collector_module.mmd_camera_rotation_from_maya_forward_up = lambda _forward, _up: (0.0, 0.0, 0.0)
        collector_module.om = FakeOpenMaya
        try:
            result = VmdSceneCollector().collect()
        finally:
            collector_module.mmd_camera_rotation_from_maya_forward_up = original_rotation_from_forward_up
            collector_module.om = original_om

        self.assertEqual([frame["frame_number"] for frame in result["camera_frames"]], [24])

    def test_legacy_camera_target_attrs_do_not_override_transform_without_rig_type(self):
        self.cmds.node_types["mmd_camera"] = "transform"
        self.cmds.attrs[("mmd_camera", ATTR_MMD_CAMERA)] = True
        self.cmds.attrs[("mmd_camera", "mmd_camera_target_x")] = 99.0
        self.cmds.attrs[("mmd_camera", "mmd_camera_target_y")] = 99.0
        self.cmds.attrs[("mmd_camera", "mmd_camera_target_z")] = -99.0
        self.cmds.attrs[("mmd_camera", "mmd_camera_rotation_x")] = 0.0
        self.cmds.attrs[("mmd_camera", "mmd_camera_rotation_y")] = 0.0
        self.cmds.attrs[("mmd_camera", "mmd_camera_rotation_z")] = 0.0
        self.cmds.keys[("mmd_camera", "translateX")] = {12.0: 1.0}
        self.cmds.keys[("mmd_camera", "translateY")] = {12.0: 2.0}
        self.cmds.keys[("mmd_camera", "translateZ")] = {12.0: -3.0}
        self.cmds.keys[("mmd_camera", "rotateX")] = {12.0: 10.0}
        self.cmds.keys[("mmd_camera", "rotateY")] = {12.0: 20.0}
        self.cmds.keys[("mmd_camera", "rotateZ")] = {12.0: -30.0}
        self.cmds.keys[("mmd_camera", "mmd_camera_target_x")] = {12.0: 99.0}
        self.cmds.keys[("mmd_camera", "mmd_camera_target_y")] = {12.0: 99.0}
        self.cmds.keys[("mmd_camera", "mmd_camera_target_z")] = {12.0: -99.0}
        self.cmds.keys[("mmd_camera", "mmd_camera_rotation_x")] = {12.0: 0.0}
        self.cmds.keys[("mmd_camera", "mmd_camera_rotation_y")] = {12.0: 0.0}
        self.cmds.keys[("mmd_camera", "mmd_camera_rotation_z")] = {12.0: 0.0}
        self.cmds.keys[("mmd_camera", "mmd_camera_distance")] = {12.0: -45.0}
        self.cmds.keys[("mmd_camera", "mmd_camera_viewing_angle")] = {12.0: 42.0}
        self.cmds.keys[("mmd_camera", "mmd_camera_perspective")] = {12.0: 0.0}

        result = VmdSceneCollector().collect()
        frame = result["camera_frames"][0]

        self.assertEqual(frame["position"], (1.0, 2.0, 3.0))
        self.assertAlmostEqual(frame["rotation"][0], 0.17453292519943295)
        self.assertAlmostEqual(frame["rotation"][1], 0.3490658503988659)
        self.assertAlmostEqual(frame["rotation"][2], 0.5235987755982988)

    def test_collects_light_frames_from_tagged_light_controller(self):
        self.cmds.node_types["mmd_light"] = "transform"
        self.cmds.attrs[("mmd_light", ATTR_MMD_LIGHT)] = True
        self.cmds.keys[("mmd_light", "mmd_light_colorR")] = {8.0: 0.1}
        self.cmds.keys[("mmd_light", "mmd_light_colorG")] = {8.0: 0.2}
        self.cmds.keys[("mmd_light", "mmd_light_colorB")] = {8.0: 0.3}
        self.cmds.keys[("mmd_light", "rotateX")] = {8.0: 0.0}
        self.cmds.keys[("mmd_light", "rotateY")] = {8.0: 90.0}
        self.cmds.keys[("mmd_light", "rotateZ")] = {8.0: 0.0}

        result = VmdSceneCollector().collect()

        self.assertEqual(
            result["light_frames"],
            [
                {
                    "frame_number": 8,
                    "color": (0.1, 0.2, 0.3),
                    "position": (-1.0, 0.0, 6.123233995736766e-17),
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
