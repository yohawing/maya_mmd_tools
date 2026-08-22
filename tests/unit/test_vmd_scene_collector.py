"""VMD scene collector の Maya 非依存ロジックを検証するテスト。"""

import json
import math
import unittest
from array import array
from io import BytesIO
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
)
from mmd_tools.validation.snapshot import fingerprint_payload  # noqa: E402


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
        self.blend_connection_pairs = {}
        self.destination_connection_pairs = {}
        self.anim_layer_parents = {}

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
        parent=False,
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
        if parent:
            return [
                candidate
                for candidate, children in self.children.items()
                if node in children
            ]
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
        if "." not in plug:
            if destination and not source:
                return list(self.destination_connection_pairs.get(plug, []))
            return list(self.blend_connection_pairs.get(plug, []))
        node, attr = plug.split(".", 1)
        return list(self.connections.get((node, attr, bool(source), bool(destination)), []))

    def animLayer(self, layer, q=False, query=False, parent=False, **_kwargs):  # noqa: N802,N803
        if parent and (q or query):
            return self.anim_layer_parents.get(layer)
        return None

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
        if "." not in plug:
            if query and timeChange:
                return sorted({time for (node, _attr), values in self.keys.items() if node == plug for time in values})
            return []
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
        self.original_bake_timeline_writable_plug = (
            collector_module._bake_timeline_writable_plug
        )
        collector_module.cmds = self.cmds
        collector_module.read_mmd_control_rig_metadata = lambda _target_model: None
        # The non-Maya fake has no MSelectionList/MFnAttribute implementation.
        # Model ordinary authored input plugs as writable by default; tests for
        # nonwritable routes override this helper explicitly.
        collector_module._bake_timeline_writable_plug = lambda _node, _attr: True

    def tearDown(self):
        collector_module.cmds = self.original_cmds
        collector_module.read_mmd_control_rig_metadata = self.original_read_control_rig_metadata
        collector_module._bake_timeline_writable_plug = (
            self.original_bake_timeline_writable_plug
        )

    def _timeline_sampler(self):
        cmds_module = self.cmds
        bone_routes = {}

        class Samples:
            @property
            def diagnostics(self):
                return {"sample_count": len(scalar_frames)}

            def value(self, joint, attr, frame):
                node, source_attr = bone_routes.get(joint, {}).get(
                    attr, (joint, attr)
                )
                return float(
                    cmds_module.getAttr(f"{node}.{source_attr}", time=frame)
                )

            def scalar_track(self, logical_name):
                node, attr = scalar_channels[logical_name]
                return type(
                    "ScalarTrack",
                    (),
                    {
                        "frames": tuple(scalar_frames),
                        "values": [
                            float(cmds_module.getAttr(f"{node}.{attr}", time=frame))
                            for frame in scalar_frames
                        ],
                    },
                )()

            def close(self):
                return None

        scalar_frames = ()
        scalar_channels = {}

        class Sampler:
            available = True

            def __init__(self):
                self.bone_calls = []

            def sample_dense_bone_channels(self, frames, joints, routes):
                nonlocal bone_routes
                bone_routes = {
                    str(joint): {
                        str(attr): (str(node), str(source_attr))
                        for attr, (node, source_attr) in route.items()
                    }
                    for joint, route in routes.items()
                }
                self.bone_calls.append(
                    (tuple(frames), tuple(joints), bone_routes)
                )
                return Samples()

            def sample_dense_scalar_channels(self, frames, channels):
                nonlocal scalar_frames, scalar_channels
                scalar_frames = tuple(frames)
                scalar_channels = {
                    str(logical_name): (str(node), str(attr))
                    for logical_name, node, attr in channels
                }
                return Samples()

        return Sampler()

    def _collect_to_sink(self, options, sampler=None, diagnostics_sink=None):
        """Collect Mode C through the streaming contract used in production."""

        class Sink:
            def __init__(self):
                self.frames = []

            def begin_section(self, _section):
                return None

            def write_frame(self, section, frame):
                self.frames.append((section, frame))

        collector = VmdSceneCollector(
            diagnostics_sink=diagnostics_sink,
            bone_channel_sampler=sampler,
        )
        sink = Sink()
        result = collector.collect_to_sink(options, sink)
        return collector, result, sink

    @staticmethod
    def _direct_control_rig_candidate(
        joint,
        bone_name,
        control,
        value_node,
    ):
        channels = (
            "translateX",
            "translateY",
            "translateZ",
            "rotateX",
            "rotateY",
            "rotateZ",
        )
        return {
            "role": bone_name,
            "joint": joint,
            "boneName": bone_name,
            "control": control,
            "selectorPlugs": tuple(
                f"{control}.{channel}" for channel in channels
            ),
            "valueRoutes": {
                channel: (value_node, channel) for channel in channels
            },
            "ownedFamilies": ("translate", "rotate"),
        }

    def test_bake_timeline_collect_to_sink_keeps_canonical_sections_and_never_finishes(self):
        self.cmds.node_types.update({"model_root": "transform", "center_joint": "joint"})
        self.cmds.children["model_root"] = ["center_joint"]
        self.cmds.attrs[("center_joint", ATTR_MMD_BONE_NAME)] = "センター"
        for attr in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"):
            self.cmds.keys[("center_joint", attr)] = {0.0: 0.0, 2.0: 1.0 if attr == "translateX" else 0.0}

        class Sink:
            def __init__(self):
                self.sections = []
                self.frames = []
                self.finished = False

            def begin_section(self, section):
                self.sections.append(section)

            def write_frame(self, section, frame):
                self.frames.append((section, frame))

            def finish(self):
                self.finished = True

        sink = Sink()
        result = VmdSceneCollector(bone_channel_sampler=self._timeline_sampler()).collect_to_sink(
            {
                "target_model": "model_root",
                "export_strategy": "bake_timeline",
                "frame_range": (0, 2),
            },
            sink,
        )

        self.assertEqual(
            sink.sections,
            ["bones", "morphs", "cameras", "lights", "shadows", "ik"],
        )
        self.assertFalse(sink.finished)
        self.assertEqual(result["section_counts"]["bones"], 3)
        self.assertEqual(result["validation_frame_range"], (0, 2))
        self.assertEqual({section for section, _frame in sink.frames}, {"bones"})
        self.assertTrue(result["diagnostics"]["streaming"]["enabled"])

    def test_bake_timeline_streaming_derives_validation_range_from_timeline(self):
        self.cmds.node_types.update({"model_root": "transform", "center_joint": "joint"})
        self.cmds.children["model_root"] = ["center_joint"]
        self.cmds.attrs[("center_joint", ATTR_MMD_BONE_NAME)] = "センター"
        for attr in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"):
            self.cmds.keys[("center_joint", attr)] = {
                3.0: 0.0,
                7.0: 1.0 if attr == "translateX" else 0.0,
            }

        class Sink:
            def begin_section(self, _section):
                return None

            def write_frame(self, _section, _frame):
                return None

        result = VmdSceneCollector(
            bone_channel_sampler=self._timeline_sampler()
        ).collect_to_sink(
            {"target_model": "model_root", "export_strategy": "bake_timeline"},
            Sink(),
        )

        self.assertEqual(result["validation_frame_range"], (3, 7))

    def test_bake_timeline_stream_morph_exact_constant_semantics(self):
        self.cmds.node_types.update({"model_root": "transform", "face_bs": "blendShape"})
        self.cmds.blendshape_weights["face_bs"] = 1
        self.cmds.aliases["face_bs.weight[0]"] = "笑い"
        self.cmds.keys[("face_bs", "weight[0]")] = {0.0: 0.2, 2.0: 0.2}
        self.cmds.attrs[("face_bs", "weight[0]")] = 0.2

        class Sink:
            def __init__(self):
                self.frames = []

            def begin_section(self, _section):
                return None

            def write_frame(self, section, frame):
                self.frames.append((section, frame))

        options = {
            "target_model": "model_root",
            "blend_shapes": ["face_bs"],
            "export_strategy": "bake_timeline",
            "frame_range": (0, 2),
        }
        sink = Sink()
        result = VmdSceneCollector().collect_to_sink(options, sink)
        streamed = [frame for section, frame in sink.frames if section == "morphs"]
        self.assertEqual(
            [(frame["frame_number"], frame["weight"]) for frame in streamed],
            [(0, 0.2)],
        )
        self.assertEqual(
            result["diagnostics"]["track_selection"]["counts"]["constant_one_key"],
            1,
        )

    def test_bake_timeline_stream_uses_native_morphs_and_omits_camera_light(self):
        self.cmds.node_types.update(
            {
                "model_root": "transform",
                "face_bs": "blendShape",
                "camera_ctrl": "transform",
                "light_ctrl": "transform",
            }
        )
        self.cmds.blendshape_weights["face_bs"] = 1
        self.cmds.aliases["face_bs.weight[0]"] = "smile"
        self.cmds.keys[("face_bs", "weight[0]")] = {0.0: 0.0, 2.0: 1.0}

        class Sink:
            def begin_section(self, _section):
                return None

            def write_frame(self, _section, _frame):
                return None

        collector = VmdSceneCollector(
            bone_channel_sampler=self._timeline_sampler()
        )
        with mock.patch.object(
            collector_module,
            "_MayaTimelineReader",
            side_effect=AssertionError("Python Timeline path was used"),
        ):
            collector.collect_to_sink(
                {
                    "target_model": "model_root",
                    "blend_shapes": ["face_bs"],
                    "cameras": ["camera_ctrl"],
                    "lights": ["light_ctrl"],
                    "export_strategy": "bake_timeline",
                    "frame_range": (0, 2),
                },
                Sink(),
            )

        self.assertEqual(
            collector.diagnostics["unsupported_bake_timeline_sections"],
            {"cameras": 1, "lights": 1},
        )
        self.assertEqual(
            collector.diagnostics["native_morph_sampler"]["sample_count"],
            3,
        )

    def test_bake_timeline_stream_morph_post_conversion_first_win(self):
        self.cmds.node_types.update(
            {"model_root": "transform", "face_bs": "blendShape", "driver": "network"}
        )
        self.cmds.blendshape_weights["face_bs"] = 1
        self.cmds.aliases["face_bs.weight[0]"] = "笑い"
        self.cmds.keys[("face_bs", "weight[0]")] = {
            0.0: 0.1,
            1.0: 0.2,
            2.0: 0.3,
        }
        # The incoming non-animCurve provider keeps this from being classified
        # as a direct-multi candidate; fixed 60fps maps frames 0 and 1 to VMD
        # frame 0, so the first value must win after conversion.
        self.cmds.current_unit = "ntscf"
        self.cmds.connections[("face_bs", "weight[0]", True, False)] = [
            "driver.output"
        ]

        class Sink:
            def __init__(self):
                self.frames = []

            def begin_section(self, _section):
                return None

            def write_frame(self, section, frame):
                self.frames.append((section, frame))

        options = {
            "target_model": "model_root",
            "blend_shapes": ["face_bs"],
            "joints": [],
            "export_strategy": "bake_timeline",
            "frame_range": (0, 2),
        }
        sink = Sink()
        streamed_collector = VmdSceneCollector()
        streamed_collector.collect_to_sink(options, sink)
        streamed_frames = [frame for section, frame in sink.frames if section == "morphs"]

        self.assertEqual([frame["frame_number"] for frame in streamed_frames], [0, 1])
        self.assertEqual([frame["weight"] for frame in streamed_frames], [0.1, 0.3])
        self.assertEqual(
            streamed_collector.diagnostics["track_selection"]["counts"][
                "authored_sampled"
            ],
            1,
        )

    def test_bake_timeline_stream_morph_dedups_before_exact_constant_classification(self):
        self.cmds.node_types.update({"model_root": "transform", "face_bs": "blendShape"})
        self.cmds.blendshape_weights["face_bs"] = 1
        self.cmds.aliases["face_bs.weight[0]"] = "笑い"
        self.cmds.keys[("face_bs", "weight[0]")] = {
            0.0: 0.0,
            1.0: 1.0,
            2.0: 0.0,
        }
        self.cmds.current_unit = "ntscf"

        class Sink:
            def __init__(self):
                self.frames = []

            def begin_section(self, _section):
                return None

            def write_frame(self, section, frame):
                self.frames.append((section, frame))

        options = {
            "target_model": "model_root",
            "blend_shapes": ["face_bs"],
            "joints": [],
            "export_strategy": "bake_timeline",
            "frame_range": (0, 2),
        }
        sink = Sink()
        streamed_collector = VmdSceneCollector()
        streamed = streamed_collector.collect_to_sink(options, sink)

        self.assertEqual(
            [frame for section, frame in sink.frames if section == "morphs"],
            [],
        )
        self.assertEqual(streamed["diagnostics"]["section_counts"]["morphs"], 0)

    def test_bake_timeline_stream_native_samples_close_once_on_sink_failures(self):
        self.cmds.node_types.update({"model_root": "transform", "center_joint": "joint"})
        self.cmds.children["model_root"] = ["center_joint"]
        self.cmds.attrs[("center_joint", ATTR_MMD_BONE_NAME)] = "センター"
        for attr in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"):
            self.cmds.keys[("center_joint", attr)] = {
                0.0: 0.0,
                2.0: 1.0 if attr == "translateX" else 0.0,
            }

        class Samples:
            def __init__(self):
                self.close_count = 0

            def value(self, joint, attr, frame):
                return float(self.cmds.getAttr(f"{joint}.{attr}", time=frame))

            def close(self):
                self.close_count += 1

        # Bind the fake's cmds through the instance after construction so the
        # sample object remains small and has an observable close contract.
        class Sampler:
            available = True

            def __init__(self):
                self.samples = None

            def sample_dense_bone_channels(self, _frames, _joints, _routes):
                self.samples = Samples()
                self.samples.cmds = self_cmds
                return self.samples

        class Sink:
            def begin_section(self, _section):
                return None

            def write_frame(self, _section, _frame):
                raise failure("sink cancellation")

        self_cmds = self.cmds
        for failure in (RuntimeError, KeyboardInterrupt):
            sampler = Sampler()
            with self.assertRaises(failure):
                VmdSceneCollector(bone_channel_sampler=sampler).collect_to_sink(
                    {
                        "target_model": "model_root",
                        "export_strategy": "bake_timeline",
                        "frame_range": (0, 2),
                    },
                    Sink(),
                )
            self.assertEqual(sampler.samples.close_count, 1)

    def test_bake_timeline_stream_morph_spool_closes_on_sink_failure_and_restores_timeline(self):
        self.cmds.node_types.update({"model_root": "transform", "face_bs": "blendShape"})
        self.cmds.blendshape_weights["face_bs"] = 1
        self.cmds.aliases["face_bs.weight[0]"] = "笑い"
        self.cmds.keys[("face_bs", "weight[0]")] = {
            0.0: 0.0,
            1.0: 1.0,
            2.0: 0.0,
        }
        self.cmds.current_time = 17.5
        spool = BytesIO()

        class Sink:
            def begin_section(self, _section):
                return None

            def write_frame(self, section, _frame):
                if section == "morphs":
                    raise RuntimeError("sink failed")

        options = {
            "target_model": "model_root",
            "blend_shapes": ["face_bs"],
            "joints": [],
            "export_strategy": "bake_timeline",
            "frame_range": (0, 2),
        }
        with mock.patch.object(
            collector_module.tempfile, "TemporaryFile", return_value=spool
        ):
            with self.assertRaisesRegex(RuntimeError, "sink failed"):
                VmdSceneCollector().collect_to_sink(options, Sink())
        self.assertTrue(spool.closed)
        self.assertEqual(self.cmds.current_time, 17.5)

    def test_bake_timeline_stream_morph_spool_replays_multiple_candidates_in_one_pass(self):
        self.cmds.node_types.update({"model_root": "transform", "face_bs": "blendShape"})
        self.cmds.blendshape_weights["face_bs"] = 2
        self.cmds.aliases["face_bs.weight[0]"] = "笑い"
        self.cmds.aliases["face_bs.weight[1]"] = "怒り"
        self.cmds.keys[("face_bs", "weight[0]")] = {
            0.0: 0.0,
            1.0: 1.0,
            2.0: 0.0,
        }
        self.cmds.keys[("face_bs", "weight[1]")] = {
            0.0: 0.2,
            1.0: 0.4,
            2.0: 0.6,
        }
        record_count = 6

        class CountingSpool:
            def __init__(self):
                self.buffer = BytesIO()
                self.read_attempts = 0

            @property
            def closed(self):
                return self.buffer.closed

            def write(self, value):
                return self.buffer.write(value)

            def flush(self):
                return self.buffer.flush()

            def seek(self, *args):
                return self.buffer.seek(*args)

            def read(self, size=-1):
                if size == 20:
                    self.read_attempts += 1
                return self.buffer.read(size)

            def close(self):
                return self.buffer.close()

        class Sink:
            def __init__(self):
                self.frames = []

            def begin_section(self, _section):
                return None

            def write_frame(self, section, frame):
                self.frames.append((section, frame))

        options = {
            "target_model": "model_root",
            "blend_shapes": ["face_bs"],
            "joints": [],
            "export_strategy": "bake_timeline",
            "frame_range": (0, 2),
        }
        spool = CountingSpool()
        sink = Sink()
        streamed_collector = VmdSceneCollector()
        with mock.patch.object(
            collector_module.tempfile, "TemporaryFile", return_value=spool
        ):
            streamed = streamed_collector.collect_to_sink(options, sink)

        streamed_frames = [
            (
                frame["morph_name"],
                frame["frame_number"],
                frame["weight"],
            )
            for section, frame in sink.frames
            if section == "morphs"
        ]
        self.assertEqual(
            sorted(streamed_frames),
            [
                ("怒り", 0, 0.2),
                ("怒り", 1, 0.4),
                ("怒り", 2, 0.6),
                ("笑い", 0, 0.0),
                ("笑い", 1, 1.0),
                ("笑い", 2, 0.0),
            ],
        )
        streamed_selection = streamed["diagnostics"]["track_selection"]
        self.assertEqual(streamed_selection["counts"]["authored_sampled"], 2)
        self.assertTrue(spool.closed)
        self.assertLessEqual(spool.read_attempts, 2 * (record_count + 1))

    def _reduce_exact_run(self, values, protected=(), track="morph", report=None):
        frames = []
        report = report or collector_module._new_key_reduction_report(True)[
            "sections"
        ]["morphs"]
        reducer = collector_module._ExactRunReducer(
            frames.append,
            ("weight",),
            set(protected),
            report,
            track,
        )
        for frame_number, value in enumerate(values):
            reducer.add(
                {
                    "morph_name": "morph",
                    "frame_number": frame_number,
                    "weight": value,
                }
            )
        reducer.finish()
        return frames, report

    def test_exact_run_reducer_retains_plateau_endpoints_and_change_sides(self):
        aaa, _report = self._reduce_exact_run([1.0, 1.0, 1.0])
        aaabbb, report = self._reduce_exact_run(
            [1.0, 1.0, 1.0, 2.0, 2.0, 2.0]
        )

        self.assertEqual([frame["frame_number"] for frame in aaa], [0, 2])
        self.assertEqual(
            [frame["frame_number"] for frame in aaabbb], [0, 2, 3, 5]
        )
        self.assertEqual(
            {key: report[key] for key in ("input", "output", "removed")},
            {"input": 6, "output": 4, "removed": 2},
        )

    def test_exact_run_reducer_keeps_short_runs_and_protected_interior(self):
        one, _report = self._reduce_exact_run([1.0])
        two, _report = self._reduce_exact_run([1.0, 1.0])
        protected, _report = self._reduce_exact_run(
            [1.0, 1.0, 1.0], protected={1}
        )

        self.assertEqual([frame["frame_number"] for frame in one], [0])
        self.assertEqual([frame["frame_number"] for frame in two], [0, 1])
        self.assertEqual(
            [frame["frame_number"] for frame in protected], [0, 1, 2]
        )

    def test_exact_run_reducer_diagnostics_are_capped(self):
        report = collector_module._new_key_reduction_report(True)["sections"]["morphs"]
        for index in range(70):
            self._reduce_exact_run(
                [float(index)] * 3,
                track=f"morph-{index}",
                report=report,
            )

        self.assertEqual(len(report["witnesses"]), 64)
        self.assertEqual(len({row["track"] for row in report["witnesses"]}), 64)
        self.assertEqual(report["witness_omitted_count"], 6)

    def test_exact_run_reducer_witnesses_exclude_protected_interior(self):
        _frames, report = self._reduce_exact_run(
            [0.0, 0.0, 0.0, 0.0, 0.0], protected={2}
        )

        self.assertEqual(report["witnesses"], [{"track": "morph", "frame": 1}])
        self.assertEqual(report["witness_omitted_count"], 1)
        self.assertNotIn(2, [row["frame"] for row in report["witnesses"]])

    def test_exact_run_reducer_propagates_sink_failure(self):
        report = collector_module._new_key_reduction_report(True)["sections"]["bones"]

        def fail(_payload):
            raise RuntimeError("reducer sink failed")

        reducer = collector_module._ExactRunReducer(
            fail,
            ("position", "rotation"),
            set(),
            report,
            "center",
        )
        with self.assertRaisesRegex(RuntimeError, "reducer sink failed"):
            reducer.add(
                {
                    "bone_name": "center",
                    "frame_number": 0,
                    "position": (0.0, 0.0, 0.0),
                    "rotation": (0.0, 0.0, 0.0, 1.0),
                }
            )

    def test_bake_timeline_stream_exact_run_reduces_dependency_bone_and_morph(self):
        self.cmds.node_types.update(
            {
                "model_root": "transform",
                "center_joint": "joint",
                "direct_joint": "joint",
                "face_bs": "blendShape",
                "driver": "network",
            }
        )
        self.cmds.attrs[("center_joint", ATTR_MMD_BONE_NAME)] = "center"
        self.cmds.attrs[("direct_joint", ATTR_MMD_BONE_NAME)] = "direct"
        self.cmds.blendshape_weights["face_bs"] = 1
        self.cmds.aliases["face_bs.weight[0]"] = "smile"
        self.cmds.keys[("center_joint", "translateX")] = {
            0.0: 0.0,
            2.0: 0.0,
            5.0: 1.0,
        }
        self.cmds.keys[("face_bs", "weight[0]")] = {
            0.0: 0.0,
            2.0: 0.0,
            5.0: 1.0,
        }
        self.cmds.keys[("direct_joint", "translateX")] = {
            0.0: 0.0,
            2.0: 0.0,
            5.0: 1.0,
        }
        self.cmds.connections[("center_joint", "translateX", True, False)] = [
            "driver.output"
        ]
        self.cmds.connections[("face_bs", "weight[0]", True, False)] = [
            "driver.output"
        ]

        class Sink:
            def __init__(self):
                self.frames = []

            def begin_section(self, _section):
                return None

            def write_frame(self, section, frame):
                self.frames.append((section, frame))

        base_options = {
            "target_model": "model_root",
            "joints": ["center_joint", "direct_joint"],
            "blend_shapes": ["face_bs"],
            "export_strategy": "bake_timeline",
            "frame_range": (0, 5),
        }
        reduced_sink = Sink()
        reduced = VmdSceneCollector(
            bone_channel_sampler=self._timeline_sampler()
        ).collect_to_sink(base_options, reduced_sink)
        dense_sink = Sink()
        dense = VmdSceneCollector(
            bone_channel_sampler=self._timeline_sampler()
        ).collect_to_sink(
            {**base_options, "bake_timeline_exact_run_reduction": False}, dense_sink
        )

        for section in ("bones", "morphs"):
            reduced_frames = [
                frame["frame_number"]
                for emitted_section, frame in reduced_sink.frames
                if emitted_section == section
                and (section != "bones" or frame["bone_name"] == "center")
            ]
            dense_frames = [
                frame["frame_number"]
                for emitted_section, frame in dense_sink.frames
                if emitted_section == section
                and (section != "bones" or frame["bone_name"] == "center")
            ]
            self.assertEqual(reduced_frames, [0, 2, 4, 5])
            self.assertEqual(dense_frames, [0, 1, 2, 3, 4, 5])
            self.assertEqual(
                reduced["diagnostics"]["key_reduction"]["sections"][section]["removed"],
                4 if section == "bones" else 2,
            )
            self.assertEqual(dense["diagnostics"]["key_reduction"]["sections"][section]["removed"], 0)
        self.assertEqual(
            [
                frame["frame_number"]
                for section, frame in reduced_sink.frames
                if section == "bones" and frame["bone_name"] == "direct"
            ],
            [0, 2, 4, 5],
        )
        self.assertEqual(
            reduced["diagnostics"]["key_reduction"]["sections"]["bones"][
                "witnesses"
            ],
            [{"track": "center", "frame": 1}, {"track": "direct", "frame": 1}],
        )
        self.assertEqual(
            reduced["diagnostics"]["key_reduction"]["sections"]["morphs"][
                "witnesses"
            ],
            [{"track": "smile", "frame": 1}],
        )
        self.assertEqual(
            dense["diagnostics"]["key_reduction"]["sections"]["bones"]["witnesses"],
            [],
        )

    def test_bake_timeline_stream_exact_run_protects_global_ik_key_frame(self):
        self.cmds.node_types.update(
            {
                "model_root": "transform",
                "center_joint": "joint",
                "driver": "network",
                "ik_node": "mmdCcdIk",
            }
        )
        self.cmds.attrs[("center_joint", ATTR_MMD_BONE_NAME)] = "center"
        self.cmds.keys[("center_joint", "translateX")] = {0.0: 0.0, 5.0: 1.0}
        self.cmds.keys[("ik_node", "enabled")] = {2.0: 0.0}
        self.cmds.connections[("center_joint", "translateX", True, False)] = [
            "driver.output"
        ]

        class Sink:
            def __init__(self):
                self.frames = []

            def begin_section(self, _section):
                return None

            def write_frame(self, section, frame):
                self.frames.append((section, frame))

        sink = Sink()
        with mock.patch.object(
            collector_module,
            "collect_ik_nodes_by_bone_name",
            return_value={"leg": "ik_node"},
        ):
            result = VmdSceneCollector(
                bone_channel_sampler=self._timeline_sampler()
            ).collect_to_sink(
                {
                    "target_model": "model_root",
                    "joints": ["center_joint"],
                    "blend_shapes": [],
                    "export_strategy": "bake_timeline",
                    "frame_range": (0, 5),
                },
                sink,
            )

        self.assertEqual(
            [
                frame["frame_number"]
                for section, frame in sink.frames
                if section == "bones"
            ],
            [0, 2, 4, 5],
        )
        ik_witnesses = result["diagnostics"]["key_reduction"]["sections"]["bones"][
            "witnesses"
        ]
        self.assertEqual(ik_witnesses, [{"track": "center", "frame": 1}])
        self.assertNotIn(2, [row["frame"] for row in ik_witnesses])

    def test_bake_timeline_stream_bone_frame_collision_is_first_win_for_all_paths(self):
        self.cmds.current_unit = "ntscf"
        self.cmds.node_types.update(
            {
                "model_root": "transform",
                "direct_joint": "joint",
                "dependency_joint": "joint",
                "driver": "network",
            }
        )
        self.cmds.attrs[("direct_joint", ATTR_MMD_BONE_NAME)] = "direct"
        self.cmds.attrs[("dependency_joint", ATTR_MMD_BONE_NAME)] = "dependency"
        for joint in ("direct_joint", "dependency_joint"):
            self.cmds.keys[(joint, "translateX")] = {
                0.0: 0.2,
                1.0: 0.9,
                2.0: 0.4,
            }
        self.cmds.connections[("dependency_joint", "translateX", True, False)] = [
            "driver.output"
        ]

        class Sink:
            def __init__(self):
                self.frames = []

            def begin_section(self, _section):
                return None

            def write_frame(self, section, frame):
                if section == "bones":
                    self.frames.append(frame)

        options = {
            "target_model": "model_root",
            "joints": ["direct_joint", "dependency_joint"],
            "blend_shapes": [],
            "export_strategy": "bake_timeline",
            "frame_range": (0, 2),
        }
        expected = None
        for reduction_enabled in (True, False):
            with self.subTest(reduction_enabled=reduction_enabled):
                sink = Sink()
                VmdSceneCollector(
                    bone_channel_sampler=self._timeline_sampler()
                ).collect_to_sink(
                    {
                        **options,
                        "bake_timeline_exact_run_reduction": reduction_enabled,
                    },
                    sink,
                )
                actual = sorted(
                    (frame["bone_name"], frame["frame_number"], frame["position"])
                    for frame in sink.frames
                )

                if expected is None:
                    expected = actual
                self.assertEqual(actual, expected)
                identities = [
                    (frame["bone_name"], frame["frame_number"])
                    for frame in sink.frames
                ]
                self.assertEqual(len(identities), len(set(identities)))

    def test_bake_timeline_stream_direct_exact_constant_remains_one_key(self):
        self.cmds.node_types.update({"model_root": "transform", "face_bs": "blendShape"})
        self.cmds.blendshape_weights["face_bs"] = 1
        self.cmds.aliases["face_bs.weight[0]"] = "smile"
        self.cmds.keys[("face_bs", "weight[0]")] = {0.0: 0.5, 5.0: 0.5}
        self.cmds.attrs[("face_bs", "weight[0]")] = 0.5

        class Sink:
            def __init__(self):
                self.frames = []

            def begin_section(self, _section):
                return None

            def write_frame(self, section, frame):
                self.frames.append((section, frame))

        sink = Sink()
        result = VmdSceneCollector().collect_to_sink(
            {
                "target_model": "model_root",
                "joints": [],
                "blend_shapes": ["face_bs"],
                "export_strategy": "bake_timeline",
                "frame_range": (0, 5),
            },
            sink,
        )

        self.assertEqual(
            [frame["frame_number"] for section, frame in sink.frames if section == "morphs"],
            [0],
        )
        self.assertEqual(
            result["diagnostics"]["track_selection"]["counts"]["constant_one_key"],
            1,
        )

    def test_bake_timeline_stream_requires_timeline_native_sampler(self):
        self.cmds.node_types.update({"model_root": "transform", "center_joint": "joint"})
        self.cmds.children["model_root"] = ["center_joint"]
        self.cmds.attrs[("center_joint", ATTR_MMD_BONE_NAME)] = "センター"
        self.cmds.keys[("center_joint", "translateX")] = {0.0: 0.0, 2.0: 1.0}

        with self.assertRaisesRegex(RuntimeError, "native bone sampling"):
            self._collect_to_sink(
                {
                    "target_model": "model_root",
                    "export_strategy": "bake_timeline",
                    "frame_range": (0, 2),
                }
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

    def test_bake_timeline_dense_samples_requested_frame_range(self):
        self.cmds.node_types.update({"model_root": "transform", "center_joint": "joint"})
        self.cmds.children["model_root"] = ["center_joint"]
        self.cmds.attrs[("center_joint", ATTR_MMD_BONE_NAME)] = "センター"
        for attribute in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"):
            self.cmds.keys[("center_joint", attribute)] = {0.0: 0.0, 2.0: 1.0}

        _collector, _result, sink = self._collect_to_sink(
            {
                "target_model": "model_root",
                "export_strategy": "bake_timeline",
                "frame_range": (0, 2),
                "bake_timeline_exact_run_reduction": False,
            },
            self._timeline_sampler(),
        )

        self.assertEqual(
            [
                frame["frame_number"]
                for section, frame in sink.frames
                if section == "bones"
            ],
            [0, 1, 2],
        )
        self.assertNotIn(
            "interpolation",
            next(frame for section, frame in sink.frames if section == "bones"),
        )

    def test_control_rig_direct_export_uses_control_keys_only_as_selector(self):
        self.cmds.node_types.update(
            {
                "model_root": "transform",
                "parent_joint": "joint",
                "center_joint": "joint",
                "center_control": "transform",
                "center_authored": "transform",
            }
        )
        self.cmds.children["model_root"] = ["parent_joint"]
        self.cmds.children["parent_joint"] = ["center_joint"]
        self.cmds.attrs[("center_joint", ATTR_MMD_BONE_NAME)] = "センター"
        self.cmds.attrs[("parent_joint", "mmd_bone_index")] = 0
        self.cmds.attrs[("center_joint", "mmd_bone_index")] = 1
        self.cmds.keys[("center_control", "translateX")] = {
            0.0: 100.0,
            2.0: 300.0,
        }
        for channel in (
            "translateX",
            "translateY",
            "translateZ",
            "rotateX",
            "rotateY",
            "rotateZ",
        ):
            self.cmds.keys[("center_authored", channel)] = {
                0.0: 1.0 if channel == "translateX" else 0.0,
                1.0: 2.0 if channel == "translateX" else 0.0,
                2.0: 3.0 if channel == "translateX" else 0.0,
            }
        resolved = {
            "modelRoot": "model_root",
            "candidates": {
                "center_joint": self._direct_control_rig_candidate(
                    "center_joint",
                    "センター",
                    "center_control",
                    "center_authored",
                )
            },
            "omittedRoles": (),
            "ikStateRoutes": {"左足ＩＫ": ("ik_control", "ikEnabled")},
        }
        self.cmds.keys[("ik_control", "ikEnabled")] = {
            0.0: 1.0,
            1.0: 0.0,
            2.0: 1.0,
        }
        sampler = self._timeline_sampler()

        rotation_context = mock.Mock(return_value={})
        with mock.patch.object(
            collector_module,
            "resolve_control_rig_direct_vmd_export_routes",
            return_value=resolved,
        ), mock.patch.object(
            collector_module,
            "_build_rotation_export_context",
            rotation_context,
        ):
            collector_module.read_mmd_control_rig_metadata = lambda _model: {
                "state": "EDIT",
                "owner": "CONTROL_OWNED",
            }
            _collector, result, sink = self._collect_to_sink(
                {
                    "target_model": "model_root",
                    "joints": ["center_joint"],
                    "export_strategy": "bake_timeline",
                    "frame_range": (0, 2),
                    "bake_timeline_exact_run_reduction": False,
                },
                sampler,
            )

        bone_frames = [
            frame for section, frame in sink.frames if section == "bones"
        ]
        self.assertEqual(result["section_counts"]["bones"], 3)
        self.assertEqual(
            [frame["position"] for frame in bone_frames],
            [(1.0, 0.0, -0.0), (2.0, 0.0, -0.0), (3.0, 0.0, -0.0)],
        )
        self.assertNotIn((100.0, 0.0, -0.0), [
            frame["position"] for frame in bone_frames
        ])
        self.assertEqual(len(sampler.bone_calls), 1)
        self.assertEqual(
            sampler.bone_calls[0][2]["center_joint"]["translateX"],
            ("center_authored", "translateX"),
        )
        rotation_context.assert_called_once_with(["parent_joint", "center_joint"])
        ik_frames = [frame for section, frame in sink.frames if section == "ik"]
        self.assertEqual(result["section_counts"]["ik"], 3)
        self.assertEqual(
            [frame["ik_states"] for frame in ik_frames],
            [
                [("左足ＩＫ", True)],
                [("左足ＩＫ", False)],
                [("左足ＩＫ", True)],
            ],
        )

    def test_control_rig_direct_export_omits_keyless_defaults_and_exports_scene_keys(self):
        self.cmds.node_types.update(
            {
                "model_root": "transform",
                "keyless_joint": "joint",
                "unbound_joint": "joint",
                "keyless_control": "transform",
                "keyless_authored": "transform",
            }
        )
        self.cmds.children["model_root"] = ["keyless_joint", "unbound_joint"]
        self.cmds.attrs[("keyless_joint", ATTR_MMD_BONE_NAME)] = "上半身2"
        self.cmds.attrs[("unbound_joint", ATTR_MMD_BONE_NAME)] = "グルーブ"
        self.cmds.attrs[("keyless_joint", "mmd_bone_index")] = 0
        self.cmds.attrs[("unbound_joint", "mmd_bone_index")] = 1
        self.cmds.keys[("keyless_authored", "rotateZ")] = {0.0: 15.0, 2.0: 30.0}
        self.cmds.keys[("unbound_joint", "translateX")] = {0.0: 1.0, 2.0: 2.0}
        resolved = {
            "modelRoot": "model_root",
            "candidates": {
                "keyless_joint": self._direct_control_rig_candidate(
                    "keyless_joint",
                    "上半身2",
                    "keyless_control",
                    "keyless_authored",
                )
            },
            "omittedRoles": (
                {"role": "groove", "reason": "fallback"},
            ),
        }
        sampler = self._timeline_sampler()

        with mock.patch.object(
            collector_module,
            "resolve_control_rig_direct_vmd_export_routes",
            return_value=resolved,
        ), mock.patch.object(
            collector_module,
            "_build_rotation_export_context",
            return_value={},
        ):
            collector_module.read_mmd_control_rig_metadata = lambda _model: {
                "state": "EDIT",
                "owner": "CONTROL_OWNED",
            }
            collector, result, sink = self._collect_to_sink(
                {
                    "target_model": "model_root",
                    "export_strategy": "bake_timeline",
                    "frame_range": (0, 2),
                },
                sampler,
            )

        self.assertEqual(result["section_counts"]["bones"], 3)
        self.assertEqual(
            [
                (frame["bone_name"], frame["frame_number"])
                for section, frame in sink.frames
                if section == "bones"
            ],
            [("グルーブ", 0), ("グルーブ", 1), ("グルーブ", 2)],
        )
        self.assertEqual(sampler.bone_calls[0][1], ("unbound_joint",))
        self.assertEqual(
            collector.diagnostics["control_rig_direct_export"]["omitted"][
                "keyless_control"
            ],
            ["keyless_joint"],
        )

    def test_control_rig_direct_export_merges_control_and_scene_authored_tracks(self):
        self.cmds.node_types.update(
            {
                "model_root": "transform",
                "parent_joint": "joint",
                "center_joint": "joint",
                "scene_joint": "joint",
                "center_control": "transform",
                "center_authored": "transform",
            }
        )
        self.cmds.children["model_root"] = ["parent_joint", "scene_joint"]
        self.cmds.children["parent_joint"] = ["center_joint"]
        for index, (joint, bone_name) in enumerate(
            (
                ("parent_joint", "親"),
                ("center_joint", "センター"),
                ("scene_joint", "スカート"),
            )
        ):
            self.cmds.attrs[(joint, ATTR_MMD_BONE_NAME)] = bone_name
            self.cmds.attrs[(joint, "mmd_bone_index")] = index
        self.cmds.keys[("center_control", "translateX")] = {
            0.0: 100.0,
            2.0: 300.0,
        }
        self.cmds.keys[("center_authored", "translateX")] = {
            0.0: 1.0,
            1.0: 2.0,
            2.0: 3.0,
        }
        self.cmds.keys[("scene_joint", "translateX")] = {
            1.0: 10.0,
            3.0: 30.0,
        }
        self.cmds.node_types["scene_translate_x"] = "animCurveTL"
        self.cmds.connections[("scene_joint", "translateX", True, False)] = [
            "scene_translate_x.output"
        ]
        resolved = {
            "modelRoot": "model_root",
            "candidates": {
                "center_joint": self._direct_control_rig_candidate(
                    "center_joint",
                    "センター",
                    "center_control",
                    "center_authored",
                )
            },
            "ikStateRoutes": {},
        }
        sampler = self._timeline_sampler()
        with mock.patch.object(
            collector_module,
            "resolve_control_rig_direct_vmd_export_routes",
            return_value=resolved,
        ), mock.patch.object(
            VmdSceneCollector,
            "_scene_authored_input_routes",
            return_value={},
        ) as scene_routes, mock.patch.object(
            collector_module,
            "_build_rotation_export_context",
            return_value={},
        ):
            collector_module.read_mmd_control_rig_metadata = lambda _model: {
                "state": "EDIT",
                "owner": "CONTROL_OWNED",
            }
            _collector, result, sink = self._collect_to_sink(
                {
                    "target_model": "model_root",
                    "export_strategy": "bake_timeline",
                    "frame_range": (0, 3),
                },
                sampler,
            )

        scene_routes.assert_called_once_with(
            ["parent_joint", "center_joint", "scene_joint"],
            "model_root",
            strict_bake_timeline=True,
        )
        bone_frames = [
            frame for section, frame in sink.frames if section == "bones"
        ]
        self.assertEqual(
            {(frame["bone_name"], frame["frame_number"]) for frame in bone_frames},
            {
                ("センター", 0),
                ("センター", 1),
                ("センター", 2),
                ("センター", 3),
                ("スカート", 0),
                ("スカート", 1),
                ("スカート", 2),
                ("スカート", 3),
            },
        )
        self.assertEqual(result["section_counts"]["bones"], len(bone_frames))
        self.assertEqual(
            sampler.bone_calls[0][1], ("center_joint", "scene_joint")
        )
        # The keyless parent is omitted, while the keyed scene track survives
        # beside the Control-owned track without a duplicate VMD bone name.
        diagnostics = _collector.diagnostics["control_rig_direct_export"]
        self.assertEqual(diagnostics["selected"]["control"], ["center_joint"])
        self.assertEqual(diagnostics["selected"]["scene_authored"], ["scene_joint"])
        self.assertEqual(diagnostics["omitted"]["keyless_default"], ["parent_joint"])

    def test_control_rig_direct_export_blocks_external_joint(self):
        self.cmds.node_types.update(
            {
                "model_root": "transform",
                "inside_joint": "joint",
                "outside_joint": "joint",
            }
        )
        self.cmds.children["model_root"] = ["inside_joint"]
        self.cmds.attrs[("inside_joint", ATTR_MMD_BONE_NAME)] = "内"
        self.cmds.attrs[("outside_joint", ATTR_MMD_BONE_NAME)] = "外"
        collector_module.read_mmd_control_rig_metadata = lambda _model: {
            "state": "EDIT",
            "owner": "CONTROL_OWNED",
        }
        with mock.patch.object(
            collector_module,
            "resolve_control_rig_direct_vmd_export_routes",
            return_value={"candidates": {}, "ikStateRoutes": {}},
        ):
            collector = VmdSceneCollector()
            with self.assertRaisesRegex(ValueError, "outside the selected model"):
                collector._control_rig_direct_export_plan(
                    "model_root", ["inside_joint", "outside_joint"]
                )
        self.assertEqual(
            collector.diagnostics["control_rig_direct_export"]["blocked"][
                "model_external"
            ],
            ["outside_joint"],
        )

    def test_control_rig_direct_export_accepts_validated_animation_layer(self):
        self.cmds.node_types.update(
            {
                "model_root": "transform",
                "layered_joint": "joint",
                "blend_tx": "animBlendNodeAdditiveDL",
            }
        )
        self.cmds.children["model_root"] = ["layered_joint"]
        self.cmds.attrs[("layered_joint", ATTR_MMD_BONE_NAME)] = "スカート"
        self.cmds.keys[("layered_joint", "translateX")] = {0.0: 1.0, 2.0: 2.0}
        self.cmds.connections[("layered_joint", "translateX", True, False)] = [
            "blend_tx.output"
        ]
        collector_module.read_mmd_control_rig_metadata = lambda _model: {
            "state": "EDIT",
            "owner": "CONTROL_OWNED",
        }
        with mock.patch.object(
            collector_module,
            "resolve_control_rig_direct_vmd_export_routes",
            return_value={"candidates": {}, "ikStateRoutes": {}},
        ), mock.patch.object(
            VmdSceneCollector,
            "_scene_authored_input_routes",
            return_value={},
        ), mock.patch.object(
            collector_module,
            "_bake_timeline_single_key_bone_route",
            return_value="layered",
        ) as validate_layer:
            collector = VmdSceneCollector()
            plan = collector._control_rig_direct_export_plan(
                "model_root", ["layered_joint"]
            )

        self.assertEqual(plan["joints"], ["layered_joint"])
        self.assertEqual(
            plan["diagnostics"]["selected"]["scene_authored"],
            ["layered_joint"],
        )
        validate_layer.assert_called_once_with("layered_joint", {})

    def test_control_rig_direct_export_blocks_unknown_dependency_output(self):
        self.cmds.node_types.update(
            {
                "model_root": "transform",
                "dependency_joint": "joint",
                "driver": "transform",
            }
        )
        self.cmds.children["model_root"] = ["dependency_joint"]
        self.cmds.attrs[("dependency_joint", ATTR_MMD_BONE_NAME)] = "依存"
        self.cmds.connections[("dependency_joint", "translateX", True, False)] = [
            "driver.output"
        ]
        collector_module.read_mmd_control_rig_metadata = lambda _model: {
            "state": "EDIT",
            "owner": "CONTROL_OWNED",
        }
        with mock.patch.object(
            collector_module,
            "resolve_control_rig_direct_vmd_export_routes",
            return_value={"candidates": {}, "ikStateRoutes": {}},
        ), mock.patch.object(
            VmdSceneCollector,
            "_scene_authored_input_routes",
            return_value={},
        ):
            collector = VmdSceneCollector()
            with self.assertRaisesRegex(ValueError, "dependency output") as raised:
                collector._control_rig_direct_export_plan(
                    "model_root", ["dependency_joint"]
                )
        self.assertEqual(
            raised.exception.validation_issue_code,
            "VMD_CONTROL_RIG_ROUTE_UNRESOLVED",
        )
        self.assertEqual(
            raised.exception.validation_issue_path,
            "scene.control_rig.direct_vmd_export."
            "dependency_joint.channels",
        )
        self.assertTrue(
            collector.diagnostics["control_rig_direct_export"]["blocked"][
                "dependency_output"
            ]
        )

    def test_unsupported_dependency_classifier_keeps_fatal_graph_reasons(self):
        def classify(source_type, sources, destinations=()):
            self.cmds.node_types = {
                "model_root": "transform",
                "dependency_joint": "joint",
                "source": source_type,
                "other_joint": "joint",
            }
            self.cmds.children = {
                "model_root": ["dependency_joint", "other_joint"]
            }
            self.cmds.connections = {
                ("dependency_joint", "translateX", True, False): list(sources),
                ("source", "output", False, True): list(destinations),
            }
            return collector_module._classify_unsupported_bone_dependency(
                "dependency_joint",
                "model_root",
                ("translateX",),
            )

        external = classify("transform", ("source.output",))
        self.assertIn("external/foreign", external["reason"])

        ambiguous = classify(
            "plusMinusAverage",
            ("source.output", "source.output2"),
        )
        self.assertIn("ambiguous", ambiguous["reason"])

        shared = classify(
            "plusMinusAverage",
            ("source.output",),
            ("dependency_joint.translateX", "other_joint.translateX"),
        )
        self.assertIn("shared", shared["reason"])

        unknown = classify("foreignPluginNode", ("source.output",))
        self.assertIn("unknown dependency closure node type", unknown["reason"])

        self.cmds.node_types.update(
            {
                "source": "plusMinusAverage",
                "downstream": "multiplyDivide",
            }
        )
        self.cmds.connections.update(
            {
                ("dependency_joint", "translateX", True, False): [
                    "downstream.outputX"
                ],
                ("downstream", "outputX", False, True): [
                    "dependency_joint.translateX"
                ],
                ("source", "output", False, True): [
                    "downstream.input1X"
                ],
            }
        )
        self.cmds.destination_connection_pairs.update(
            {
                "downstream": ["dependency_joint.translateX"],
                "source": ["downstream.input1X"],
            }
        )
        self.cmds.blend_connection_pairs["downstream"] = ["source.output"]
        chain = collector_module._classify_unsupported_bone_dependency(
            "dependency_joint",
            "model_root",
            ("translateX",),
        )
        self.assertEqual(chain["status"], "accepted")

        self.cmds.node_types.update(
            {
                "source": "plusMinusAverage",
                "foreign_transform": "transform",
            }
        )
        self.cmds.connections.update(
            {
                ("dependency_joint", "translateX", True, False): [
                    "source.output"
                ],
                ("source", "output", False, True): [
                    "dependency_joint.translateX"
                ],
            }
        )
        self.cmds.destination_connection_pairs["source"] = [
            "dependency_joint.translateX"
        ]
        self.cmds.blend_connection_pairs["source"] = [
            "foreign_transform.translateX"
        ]
        mid_foreign = collector_module._classify_unsupported_bone_dependency(
            "dependency_joint",
            "model_root",
            ("translateX",),
        )
        self.assertIn("external/foreign", mid_foreign["reason"])

    def test_unsupported_dependency_classifier_checks_source_node_fanout(self):
        self.cmds.node_types = {
            "model_root": "transform",
            "dependency_joint": "joint",
            "other_joint": "joint",
            "source": "plusMinusAverage",
        }
        self.cmds.children = {
            "model_root": ["dependency_joint", "other_joint"]
        }
        self.cmds.connections = {
            ("dependency_joint", "translateX", True, False): [
                "source.outputX"
            ],
            ("source", "outputX", False, True): [
                "dependency_joint.translateX"
            ],
            ("source", "outputY", False, True): [
                "other_joint.translateX"
            ],
        }
        self.cmds.destination_connection_pairs["source"] = [
            "dependency_joint.translateX",
            "other_joint.translateX",
        ]
        local_fanout = collector_module._classify_unsupported_bone_dependency(
            "dependency_joint",
            "model_root",
            ("translateX",),
        )
        self.assertEqual(local_fanout["status"], "accepted")

        self.cmds.node_types["foreign_joint"] = "joint"
        self.cmds.connections[("source", "outputY", False, True)] = [
            "foreign_joint.translateX"
        ]
        self.cmds.destination_connection_pairs["source"] = [
            "dependency_joint.translateX",
            "foreign_joint.translateX",
        ]
        foreign_fanout = collector_module._classify_unsupported_bone_dependency(
            "dependency_joint",
            "model_root",
            ("translateX",),
        )
        self.assertIn("external/foreign", foreign_fanout["reason"])

    def test_control_rig_direct_export_accepts_owned_local_dependency_bake(self):
        self.cmds.node_types.update(
            {
                "model_root": "transform",
                "dependency_joint": "joint",
                "local_utility": "plusMinusAverage",
            }
        )
        self.cmds.children["model_root"] = ["dependency_joint"]
        self.cmds.attrs[("dependency_joint", ATTR_MMD_BONE_NAME)] = "依存"
        self.cmds.connections[("dependency_joint", "translateX", True, False)] = [
            "local_utility.output1D"
        ]
        collector_module.read_mmd_control_rig_metadata = lambda _model: {
            "state": "EDIT",
            "owner": "CONTROL_OWNED",
            "bindings": {},
        }
        with mock.patch.object(
            collector_module,
            "resolve_control_rig_direct_vmd_export_routes",
            return_value={"candidates": {}, "ikStateRoutes": {}},
        ), mock.patch.object(
            VmdSceneCollector,
            "_scene_authored_input_routes",
            return_value={},
        ):
            collector = VmdSceneCollector()
            plan = collector._control_rig_direct_export_plan(
                "model_root", ["dependency_joint"]
            )

        self.assertEqual(plan["joints"], ["dependency_joint"])
        self.assertEqual(
            plan["diagnostics"]["selected"]["dependency_baked"],
            ["dependency_joint"],
        )
        self.assertEqual(
            plan["diagnostics"]["dependency_baked"][0]["reason"],
            collector_module._UNSUPPORTED_BONE_BAKE_REASON,
        )

    def test_unsupported_dependency_classifier_keeps_runtime_plug_provenance(self):
        self.cmds.node_types.update(
            {
                "model_root": "transform",
                "dependency_joint": "joint",
                "append_runtime": "mmdAppend",
            }
        )
        self.cmds.children["model_root"] = ["dependency_joint"]
        self.cmds.connections[("dependency_joint", "translateX", True, False)] = [
            "append_runtime.outputTranslateX"
        ]
        self.cmds.destination_connection_pairs["append_runtime"] = [
            "dependency_joint.translateX"
        ]

        classification = collector_module._classify_unsupported_bone_dependency(
            "dependency_joint",
            "model_root",
            ("translateX",),
        )

        self.assertEqual(classification["status"], "accepted")
        self.assertEqual(classification["runtime_node_types"], ("mmdAppend",))
        self.assertEqual(classification["runtime_nodes"], ("append_runtime",))
        self.assertEqual(
            classification["plug_provenance"][0]["source"],
            "append_runtime.outputTranslateX",
        )
        self.assertEqual(
            classification["plug_provenance"][0]["destination"],
            "dependency_joint.translateX",
        )

    def test_control_rig_direct_export_recovers_complete_runtime_compound_route(self):
        self.cmds.node_types.update(
            {
                "model_root": "transform",
                "dependency_joint": "joint",
                "append_runtime": "mmdAppend",
            }
        )
        self.cmds.children["model_root"] = ["dependency_joint"]
        self.cmds.attrs[("dependency_joint", ATTR_MMD_BONE_NAME)] = "依存"
        for axis in "XYZ":
            self.cmds.connections[
                ("dependency_joint", f"translate{axis}", True, False)
            ] = [f"append_runtime.outputTranslate{axis}"]
        self.cmds.destination_connection_pairs["append_runtime"] = [
            "dependency_joint.translateX",
            "dependency_joint.translateY",
            "dependency_joint.translateZ",
        ]
        collector_module.read_mmd_control_rig_metadata = lambda _model: {
            "state": "EDIT",
            "owner": "CONTROL_OWNED",
            "bindings": {},
        }
        classification = {
            "status": "accepted",
            "reason": "model_local_dependency_closure",
            "node_types": ("mmdAppend",),
            "runtime_nodes": ("append_runtime",),
            "runtime_node_types": ("mmdAppend",),
            "plug_provenance": (),
        }
        recovered = {
            "translateX": ("append_runtime", "baseTranslateX"),
            "translateY": ("append_runtime", "baseTranslateY"),
            "translateZ": ("append_runtime", "baseTranslateZ"),
        }
        with mock.patch.object(
            collector_module,
            "resolve_control_rig_direct_vmd_export_routes",
            return_value={"candidates": {}, "ikStateRoutes": {}},
        ), mock.patch.object(
            VmdSceneCollector,
            "_scene_authored_input_routes",
            return_value={"dependency_joint": {"translateX": recovered["translateX"]}},
        ), mock.patch.object(
            collector_module,
            "_classify_unsupported_bone_dependency",
            return_value=classification,
        ), mock.patch.object(
            VmdSceneCollector,
            "_recover_runtime_authoring_routes",
            return_value=recovered,
        ), mock.patch.object(
            collector_module,
            "_bake_timeline_single_key_bone_route",
            return_value=None,
        ):
            collector = VmdSceneCollector()
            plan = collector._control_rig_direct_export_plan(
                "model_root", ["dependency_joint"]
            )

        self.assertEqual(plan["joints"], ["dependency_joint"])
        self.assertEqual(plan["value_routes"]["dependency_joint"], recovered)
        self.assertEqual(
            plan["diagnostics"]["dependency_baked"][0]["classification_node_types"],
            ["mmdAppend"],
        )

    def test_control_rig_direct_export_blocks_partial_runtime_compound_route(self):
        self.cmds.node_types.update(
            {
                "model_root": "transform",
                "dependency_joint": "joint",
                "append_runtime": "mmdAppend",
            }
        )
        self.cmds.children["model_root"] = ["dependency_joint"]
        self.cmds.attrs[("dependency_joint", ATTR_MMD_BONE_NAME)] = "依存"
        self.cmds.connections[("dependency_joint", "translateY", True, False)] = [
            "append_runtime.outputTranslateY"
        ]
        self.cmds.destination_connection_pairs["append_runtime"] = [
            "dependency_joint.translateY"
        ]
        collector_module.read_mmd_control_rig_metadata = lambda _model: {
            "state": "EDIT",
            "owner": "CONTROL_OWNED",
            "bindings": {},
        }
        classification = {
            "status": "accepted",
            "reason": "model_local_dependency_closure",
            "node_types": ("mmdAppend",),
            "runtime_nodes": ("append_runtime",),
            "runtime_node_types": ("mmdAppend",),
            "plug_provenance": (),
        }
        with mock.patch.object(
            collector_module,
            "resolve_control_rig_direct_vmd_export_routes",
            return_value={"candidates": {}, "ikStateRoutes": {}},
        ), mock.patch.object(
            VmdSceneCollector,
            "_scene_authored_input_routes",
            return_value={"dependency_joint": {"translateX": ("append_runtime", "baseTranslateX")}},
        ), mock.patch.object(
            collector_module,
            "_classify_unsupported_bone_dependency",
            return_value=classification,
        ), mock.patch.object(
            VmdSceneCollector,
            "_recover_runtime_authoring_routes",
            return_value={"translateX": ("append_runtime", "baseTranslateX")},
        ), mock.patch.object(
            collector_module,
            "_bake_timeline_single_key_bone_route",
            return_value=None,
        ):
            collector = VmdSceneCollector()
            with self.assertRaisesRegex(ValueError, "complete translate authoring route"):
                collector._control_rig_direct_export_plan(
                    "model_root", ["dependency_joint"]
                )
        self.assertTrue(
            collector.diagnostics["control_rig_direct_export"]["blocked"][
                "dependency_output"
            ]
        )

    def test_runtime_physics_route_recovery_passes_joint_route_map(self):
        self.cmds.node_types.update(
            {
                "model_root": "transform",
                "dependency_joint": "joint",
                "physics_driver": "mmdPhysicsBoneDriver",
            }
        )
        self.cmds.children["model_root"] = ["dependency_joint"]
        collector = VmdSceneCollector()
        initial_route = {
            "translateX": ("physics_driver", "inPreTranslateX"),
        }
        complete_route = {
            **initial_route,
            "translateY": ("physics_driver", "inPreTranslateY"),
            "translateZ": ("physics_driver", "inPreTranslateZ"),
            "rotateX": ("physics_driver", "inPreRotateX"),
            "rotateY": ("physics_driver", "inPreRotateY"),
            "rotateZ": ("physics_driver", "inPreRotateZ"),
        }
        classification = {
            "runtime_nodes": ("physics_driver",),
            "runtime_node_types": ("mmdPhysicsBoneDriver",),
        }

        def merge(*, joints, target_model, routes, strict_bake_timeline):
            self.assertEqual(joints, ("dependency_joint",))
            self.assertEqual(target_model, "model_root")
            self.assertTrue(strict_bake_timeline)
            self.assertEqual(routes, {"dependency_joint": initial_route})
            routes["dependency_joint"].update(complete_route)

        with mock.patch.object(
            collector,
            "_merge_physics_authored_input_routes",
            side_effect=merge,
        ):
            recovered = collector._recover_runtime_authoring_routes(
                "dependency_joint",
                "model_root",
                initial_route,
                classification,
            )

        self.assertEqual(recovered, complete_route)

    def test_control_rig_direct_export_streams_moving_dependency_through_native_sampler(self):
        self.cmds.node_types.update(
            {
                "model_root": "transform",
                "dependency_joint": "joint",
                "local_utility": "plusMinusAverage",
                "local_anim_curve": "animCurveTL",
            }
        )
        self.cmds.children["model_root"] = ["dependency_joint"]
        self.cmds.attrs.update(
            {
                ("dependency_joint", ATTR_MMD_BONE_NAME): "EyeCtrl",
                ("dependency_joint", "mmd_bone_index"): 0,
            }
        )
        self.cmds.connections[("dependency_joint", "translateX", True, False)] = [
            "local_utility.output1D"
        ]
        self.cmds.blend_connection_pairs["local_utility"] = [
            "local_anim_curve.output"
        ]
        self.cmds.connections[("local_anim_curve", "output", False, True)] = [
            "local_utility.input1D[0]"
        ]
        self.cmds.connections[("local_utility", "output1D", False, True)] = [
            "dependency_joint.translateX"
        ]
        self.cmds.destination_connection_pairs.update(
            {
                "local_anim_curve": ["local_utility.input1D[0]"],
                "local_utility": ["dependency_joint.translateX"],
            }
        )
        collector_module.read_mmd_control_rig_metadata = lambda _model: {
            "state": "EDIT",
            "owner": "CONTROL_OWNED",
            "bindings": {},
        }

        class Samples:
            diagnostics = {"available": True, "used": True}

            def value(self, _joint, attr, frame):
                return float(frame) if attr == "translateX" else 0.0

            def close(self):
                return None

        class Sampler:
            available = True

            def __init__(self):
                self.calls = []

            def sample_dense_bone_channels(self, frames, joints, routes):
                self.calls.append((tuple(frames), tuple(joints), routes))
                return Samples()

        sampler = Sampler()
        with mock.patch.object(
            collector_module,
            "resolve_control_rig_direct_vmd_export_routes",
            return_value={"candidates": {}, "ikStateRoutes": {}},
        ), mock.patch.object(
            VmdSceneCollector,
            "_scene_authored_input_routes",
            return_value={},
        ), mock.patch.object(
            collector_module,
            "_build_rotation_export_context",
            return_value={},
        ):
            collector, result, sink = self._collect_to_sink(
                {
                    "target_model": "model_root",
                    "export_strategy": "bake_timeline",
                    "frame_range": (0, 2),
                    "bake_timeline_exact_run_reduction": False,
                },
                sampler,
            )

        bone_frames = [
            frame for section, frame in sink.frames if section == "bones"
        ]
        self.assertEqual([frame["frame_number"] for frame in bone_frames], [0, 1, 2])
        self.assertEqual(sampler.calls[0][1], ("dependency_joint",))
        row = collector.diagnostics["control_rig_direct_export"]["dependency_baked"][0]
        self.assertEqual(row["bone"], "EyeCtrl")
        self.assertEqual(row["decision"], "dependency_baked")
        self.assertEqual(row["frame_range"], [0, 2])
        self.assertEqual(row["generated_key_count"], 3)
        self.assertEqual(result["section_counts"]["bones"], 3)

    def test_control_rig_direct_export_rejects_dependency_cycle(self):
        self.cmds.node_types.update(
            {
                "model_root": "transform",
                "dependency_joint": "joint",
                "local_utility": "plusMinusAverage",
            }
        )
        self.cmds.children["model_root"] = ["dependency_joint"]
        self.cmds.attrs[("dependency_joint", ATTR_MMD_BONE_NAME)] = "依存"
        self.cmds.connections[("dependency_joint", "translateX", True, False)] = [
            "local_utility.output1D"
        ]
        self.cmds.blend_connection_pairs["local_utility"] = [
            "dependency_joint.translateX"
        ]
        collector_module.read_mmd_control_rig_metadata = lambda _model: {
            "state": "EDIT",
            "owner": "CONTROL_OWNED",
            "bindings": {},
        }
        with mock.patch.object(
            collector_module,
            "resolve_control_rig_direct_vmd_export_routes",
            return_value={"candidates": {}, "ikStateRoutes": {}},
        ), mock.patch.object(
            VmdSceneCollector,
            "_scene_authored_input_routes",
            return_value={},
        ):
            collector = VmdSceneCollector()
            with self.assertRaisesRegex(ValueError, "cycle"):
                collector._control_rig_direct_export_plan(
                    "model_root", ["dependency_joint"]
                )
        self.assertIn(
            "cycle",
            collector.diagnostics["control_rig_direct_export"]["blocked"][
                "dependency_output"
            ][0],
        )

    def test_control_rig_direct_export_does_not_bake_dropped_control_candidate(self):
        self.cmds.node_types.update(
            {
                "model_root": "transform",
                "control_joint": "joint",
                "local_utility": "plusMinusAverage",
            }
        )
        self.cmds.children["model_root"] = ["control_joint"]
        self.cmds.attrs[("control_joint", ATTR_MMD_BONE_NAME)] = "Control"
        self.cmds.connections[("control_joint", "translateX", True, False)] = [
            "local_utility.output1D"
        ]
        collector_module.read_mmd_control_rig_metadata = lambda _model: {
            "state": "EDIT",
            "owner": "CONTROL_OWNED",
            "bindings": {"control": {"joint": "control_joint"}},
        }
        with mock.patch.object(
            collector_module,
            "resolve_control_rig_direct_vmd_export_routes",
            return_value={"candidates": {}, "ikStateRoutes": {}},
        ), mock.patch.object(
            VmdSceneCollector,
            "_scene_authored_input_routes",
            return_value={},
        ):
            collector = VmdSceneCollector()
            with self.assertRaisesRegex(ValueError, "cannot hide Control-owned"):
                collector._control_rig_direct_export_plan(
                    "model_root", ["control_joint"]
                )
        self.assertTrue(
            collector.diagnostics["control_rig_direct_export"]["blocked"][
                "ownership_unknown"
            ]
        )

    def test_control_rig_direct_export_reserves_keyless_control_bone_name(self):
        self.cmds.node_types.update(
            {
                "model_root": "transform",
                "keyless_control_joint": "joint",
                "unsupported_joint": "joint",
                "keyless_control": "transform",
                "local_utility": "plusMinusAverage",
            }
        )
        self.cmds.children["model_root"] = [
            "keyless_control_joint",
            "unsupported_joint",
        ]
        for joint in ("keyless_control_joint", "unsupported_joint"):
            self.cmds.attrs[(joint, ATTR_MMD_BONE_NAME)] = "same"
        self.cmds.connections[("unsupported_joint", "translateX", True, False)] = [
            "local_utility.output1D"
        ]
        resolved = {
            "candidates": {
                "keyless_control_joint": self._direct_control_rig_candidate(
                    "keyless_control_joint",
                    "same",
                    "keyless_control",
                    "keyless_control",
                )
            },
            "ikStateRoutes": {},
        }
        collector_module.read_mmd_control_rig_metadata = lambda _model: {
            "state": "EDIT",
            "owner": "CONTROL_OWNED",
            "bindings": {},
        }
        with mock.patch.object(
            collector_module,
            "resolve_control_rig_direct_vmd_export_routes",
            return_value=resolved,
        ), mock.patch.object(
            VmdSceneCollector,
            "_scene_authored_input_routes",
            return_value={},
        ):
            collector = VmdSceneCollector()
            with self.assertRaisesRegex(ValueError, "duplicate VMD bone name"):
                collector._control_rig_direct_export_plan(
                    "model_root",
                    ["keyless_control_joint", "unsupported_joint"],
                )

    def test_direct_rotation_context_rejects_unindexed_selected_joint(self):
        self.cmds.node_types.update({"model_root": "transform", "joint": "joint"})
        self.cmds.children["model_root"] = ["joint"]

        with self.assertRaisesRegex(ValueError, "unindexed selected joint"):
            collector_module._validate_direct_rotation_export_indices(
                ["joint"], ["joint"]
            )

    def test_direct_rotation_context_rejects_duplicate_bone_indices(self):
        self.cmds.node_types.update(
            {"model_root": "transform", "parent_joint": "joint", "joint": "joint"}
        )
        self.cmds.children["model_root"] = ["parent_joint"]
        self.cmds.children["parent_joint"] = ["joint"]
        self.cmds.attrs[("parent_joint", "mmd_bone_index")] = 7
        self.cmds.attrs[("joint", "mmd_bone_index")] = 7

        with self.assertRaisesRegex(ValueError, "duplicate bone index 7"):
            collector_module._validate_direct_rotation_export_indices(
                ["parent_joint", "joint"], ["joint"]
            )

    def test_bake_timeline_direct_single_key_bones_avoid_native_sampling(self):
        self.cmds.node_types.update(
            {
                "model_root": "transform",
                "default_joint": "joint",
                "offset_joint": "joint",
            }
        )
        self.cmds.children["model_root"] = ["default_joint", "offset_joint"]
        self.cmds.attrs[("default_joint", ATTR_MMD_BONE_NAME)] = "default"
        self.cmds.attrs[("offset_joint", ATTR_MMD_BONE_NAME)] = "offset"
        self.cmds.keys[("default_joint", "translateX")] = {0.0: 0.0}
        self.cmds.keys[("offset_joint", "translateX")] = {0.0: 1.0}

        collector, _result, sink = self._collect_to_sink(
            {
                "target_model": "model_root",
                "export_strategy": "bake_timeline",
                "frame_range": (0, 2),
            }
        )

        self.assertEqual(
            [
                (frame["bone_name"], frame["frame_number"])
                for section, frame in sink.frames
                if section == "bones"
            ],
            [("offset", 0)],
        )
        selection = collector.diagnostics["track_selection"]
        self.assertEqual(selection["counts"]["omitted_default"], 1)
        self.assertEqual(selection["counts"]["constant_one_key"], 1)

    def _configure_static_bone(self, translate_x=0.0):
        self.cmds.node_types.update({"center_joint": "joint"})
        self.cmds.attrs[("center_joint", ATTR_MMD_BONE_NAME)] = "center"
        self.cmds.attrs.update(
            {
                ("center_joint", "translateX"): translate_x,
                ("center_joint", "translateY"): 0.0,
                ("center_joint", "translateZ"): 0.0,
                ("center_joint", "rotateX"): 0.0,
                ("center_joint", "rotateY"): 0.0,
                ("center_joint", "rotateZ"): 0.0,
            }
        )

    def test_bake_timeline_keyless_bone_static_default_nondefault_and_bind_offset(self):
        cases = (
            (1.0, (1.0, 0.0, 0.0), [], "omitted_default"),
            (2.0, (1.0, 0.0, 0.0), [2], "constant_one_key"),
            (0.0, (1.0, 0.0, 0.0), [0], "constant_one_key"),
        )
        for translate_x, bind, expected_frames, decision in cases:
            with self.subTest(translate_x=translate_x):
                self._configure_static_bone(translate_x)
                collector = VmdSceneCollector()
                frames = collector.collect_bone_frames(
                    ["center_joint"],
                    start_frame=1.2 if translate_x != 0.0 else 0,
                    end_frame=3.8 if translate_x != 0.0 else 2,
                    bone_bind_poses={"center": bind},
                    dense_sample=True,
                    force_dense_sample=True,
                    time_converter=lambda value: value,
                )
                self.assertEqual(
                    [frame["frame_number"] for frame in frames], expected_frames
                )
                if translate_x == 2.0:
                    self.assertEqual(frames[0]["position"], (1.0, 0.0, 0.0))
                selection = collector.diagnostics["track_selection"]
                self.assertEqual(selection["counts"][decision], 1)
                self.assertEqual(
                    selection["evidence"][0]["source_key_count"], 0
                )

    def test_bake_timeline_keyless_bone_uses_earliest_requested_dense_integer(self):
        self._configure_static_bone(1.0)

        frames = VmdSceneCollector().collect_bone_frames(
            ["center_joint"],
            start_frame=0,
            end_frame=4,
            dense_sample=True,
            force_dense_sample=True,
            dense_frame_samples=[3.5, 3, 2],
            time_converter=lambda value: value,
        )

        self.assertEqual([frame["frame_number"] for frame in frames], [2])

    def test_bake_timeline_keyless_routed_bone_is_dense_native_dependency(self):
        self.cmds.node_types.update(
            {"routed_joint": "joint", "route_driver": "transform"}
        )
        self.cmds.attrs[("routed_joint", ATTR_MMD_BONE_NAME)] = "routed"
        self.cmds.attrs[("route_driver", "output")]=1.25
        captured = {}
        cmds_module = self.cmds

        class Samples:
            def value(self, joint, attr, frame):
                node, source_attr = captured["routes"].get(joint, {}).get(
                    attr, (joint, attr)
                )
                return cmds_module.getAttr(f"{node}.{source_attr}", time=frame)

        class Sampler:
            available = True

            def sample_dense_bone_channels(self, frames, joints, routes):
                captured.update(
                    {"frames": list(frames), "joints": list(joints), "routes": routes}
                )
                return Samples()

        collector = VmdSceneCollector(bone_channel_sampler=Sampler())
        frames = collector.collect_bone_frames(
            ["routed_joint"],
            0,
            2,
            input_routes={"routed_joint": {"translateX": ("route_driver", "output")}},
            dense_sample=True,
            force_dense_sample=True,
            dense_frame_samples=[0, 1, 2],
            time_converter=lambda value: value,
            bone_channel_sampler=Sampler(),
        )

        self.assertEqual([frame["frame_number"] for frame in frames], [0, 1, 2])
        self.assertEqual(captured["frames"], [0, 1, 2])
        self.assertEqual(captured["joints"], ["routed_joint"])
        evidence = collector.diagnostics["track_selection"]["evidence"]
        self.assertEqual(evidence[0]["decision"], "dependency_baked")
        self.assertEqual(evidence[0]["reason"], "keyless_routed_dependency")
        self.assertEqual(evidence[0]["source_key_count"], 0)

    def test_bake_timeline_native_bulk_track_matches_scalar_and_reuses_direct_multi_track(self):
        self.cmds.node_types["center_joint"] = "joint"
        self.cmds.attrs[("center_joint", ATTR_MMD_BONE_NAME)] = "center"
        for attr in (
            "translateX",
            "translateY",
            "translateZ",
            "rotateX",
            "rotateY",
            "rotateZ",
        ):
            self.cmds.keys[("center_joint", attr)] = {
                0.0: 0.0,
                2.0: 2.0 if attr == "translateX" else 0.0,
            }

        class ScalarSamples:
            diagnostics = {"available": True, "used": True}

            def __init__(self, cmds_module, calls):
                self._cmds = cmds_module
                self._calls = calls

            def value(self, joint, attr, frame):
                self._calls.append((joint, attr, float(frame)))
                return float(self._cmds.getAttr(f"{joint}.{attr}", time=frame))

            def close(self):
                return None

        class BulkTrack:
            def __init__(self, cmds_module, joint, frames):
                self.frames = tuple(float(frame) for frame in frames)
                self._components = {
                    attr: array(
                        "d",
                        [
                            float(cmds_module.getAttr(f"{joint}.{attr}", time=frame))
                            for frame in self.frames
                        ],
                    )
                    for attr in collector_module._BONE_EXPORT_ATTRS
                }

            def component(self, attr):
                raise AssertionError("collector must use fixed-order components")

            def _components_for_collector(self):
                return tuple(
                    self._components[attr] for attr in collector_module._BONE_EXPORT_ATTRS
                )

        class BulkSamples:
            diagnostics = {"available": True, "used": True}

            def __init__(self, cmds_module, calls):
                self._cmds = cmds_module
                self._calls = calls

            def bone_track(self, joint, frames=None):
                self._calls.append((joint, tuple(frames)))
                return BulkTrack(self._cmds, joint, frames)

            def value(self, _joint, _attr, _frame):
                raise AssertionError("bulk collector must not use scalar value")

            def close(self):
                return None

        class ScalarSampler:
            available = True

            def __init__(self, calls):
                self._calls = calls
                self.samples = None

            def sample_dense_bone_channels(self, _frames, _joints, _routes):
                self.samples = ScalarSamples(self_cmds, self._calls)
                return self.samples

        class BulkSampler:
            available = True

            def __init__(self, calls):
                self._calls = calls
                self.samples = None

            def sample_dense_bone_channels(self, _frames, _joints, _routes):
                self.samples = BulkSamples(self_cmds, self._calls)
                return self.samples

        self_cmds = self.cmds
        scalar_calls = []
        bulk_calls = []

        def collect(sampler, output):
            collector = VmdSceneCollector(bone_channel_sampler=sampler)
            collector._mmd_bone_name = lambda joint: str(joint)
            with mock.patch.object(
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
                collector.collect_bone_frames(
                    ["center_joint"],
                    dense_sample=True,
                    force_dense_sample=True,
                    dense_frame_samples=[0, 1, 2],
                    time_converter=lambda value: value,
                    bone_channel_sampler=sampler,
                    frame_sink=output.append,
                )
            return collector

        scalar_collector = collect(ScalarSampler(scalar_calls), [])
        bulk_rows = []
        bulk_collector = collect(BulkSampler(bulk_calls), bulk_rows)
        scalar_rows = []
        collect(ScalarSampler([]), scalar_rows)
        self.assertEqual(bulk_rows, scalar_rows)
        self.assertEqual([row["frame_number"] for row in bulk_rows], [0, 1, 2])
        self.assertEqual(bulk_calls, [("center_joint", (0, 1, 2))])
        self.assertEqual(len(scalar_calls), 36)
        bulk_report = bulk_collector.diagnostics["native_sampler"]
        self.assertTrue(bulk_report["bulk_track_api"])
        self.assertEqual(bulk_report["bulk_track_count"], 1)
        self.assertEqual(bulk_report["bulk_track_frame_count"], 3)
        self.assertEqual(bulk_report["scalar_native_value_read_count"], 0)
        scalar_report = scalar_collector.diagnostics["native_sampler"]
        self.assertFalse(scalar_report["bulk_track_api"])
        self.assertGreater(scalar_report["scalar_native_value_read_count"], 0)

    def test_bake_timeline_native_bulk_track_failure_closes_samples(self):
        self.cmds.node_types["center_joint"] = "joint"
        self.cmds.attrs[("center_joint", ATTR_MMD_BONE_NAME)] = "center"
        self.cmds.keys[("center_joint", "translateX")] = {0.0: 0.0, 2.0: 1.0}

        class Samples:
            diagnostics = {"available": True, "used": True}

            def __init__(self):
                self.closed = 0

            def value(self, _joint, _attr, _frame):
                return 0.0

            def bone_track(self, _joint, _frames=None):
                raise ValueError("bulk track failed")

            def close(self):
                self.closed += 1

        class Sampler:
            available = True

            def __init__(self):
                self.samples = Samples()

            def sample_dense_bone_channels(self, _frames, _joints, _routes):
                return self.samples

        sampler = Sampler()
        collector = VmdSceneCollector(bone_channel_sampler=sampler)
        collector._mmd_bone_name = lambda joint: str(joint)
        with mock.patch.object(
            collector_module,
            "_build_rotation_export_context",
            return_value={},
        ), mock.patch.object(
            collector_module,
            "_maya_joint_rotate_to_vmd_quaternion",
            return_value=(0.0, 0.0, 0.0, 1.0),
        ), mock.patch.object(
            collector_module,
            "_resolve_bind_pose",
            return_value=(0.0, 0.0, 0.0),
        ), mock.patch.object(
            collector_module,
            "_maya_translate_to_vmd_position",
            return_value=(0.0, 0.0, 0.0),
        ), self.assertRaisesRegex(RuntimeError, "native bone track failed"):
            collector.collect_bone_frames(
                ["center_joint"],
                dense_sample=True,
                force_dense_sample=True,
                dense_frame_samples=[0, 1, 2],
                time_converter=lambda value: value,
                bone_channel_sampler=sampler,
            )
        self.assertEqual(sampler.samples.closed, 1)
        self.assertTrue(collector.diagnostics["native_sampler"]["fatal"])

    def test_bake_timeline_keyless_incoming_bone_is_dense_dependency(self):
        self._configure_static_bone()
        self.cmds.node_types["constraint"] = "parentConstraint"
        self.cmds.connections[("center_joint", "translateX", True, False)] = [
            "constraint.output"
        ]
        collector = VmdSceneCollector(bone_channel_sampler=self._timeline_sampler())

        frames = collector.collect_bone_frames(
            ["center_joint"],
            0,
            2,
            dense_sample=True,
            force_dense_sample=True,
            dense_frame_samples=[0, 1, 2],
            time_converter=lambda value: value,
            bone_channel_sampler=self._timeline_sampler(),
        )

        self.assertEqual([frame["frame_number"] for frame in frames], [0, 1, 2])
        evidence = collector.diagnostics["track_selection"]["evidence"]
        self.assertEqual(evidence[0]["decision"], "dependency_baked")
        self.assertEqual(evidence[0]["reason"], "keyless_incoming_dependency")
        self.assertEqual(evidence[0]["source_key_count"], 0)

    def test_bake_timeline_static_dependency_keeps_constant_policy(self):
        self._configure_static_bone(translate_x=0.0)
        self.cmds.node_types["constraint"] = "parentConstraint"
        self.cmds.connections[("center_joint", "translateX", True, False)] = [
            "constraint.output"
        ]
        collector = VmdSceneCollector(bone_channel_sampler=self._timeline_sampler())
        emitted = []
        collector.collect_bone_frames(
            ["center_joint"],
            0,
            2,
            dense_sample=True,
            force_dense_sample=True,
            dense_frame_samples=[0, 1, 2],
            time_converter=lambda value: value,
            bone_channel_sampler=self._timeline_sampler(),
            frame_sink=emitted.append,
            exact_run_reduction=True,
            key_reduction_report={"input": 0, "output": 0, "witnesses": []},
        )

        self.assertEqual(emitted, [])
        evidence = collector.diagnostics["track_selection"]["evidence"]
        self.assertEqual(evidence[0]["decision"], "omitted_default")
        self.assertEqual(
            evidence[0]["reason"],
            "unsupported_dependency_static_default",
        )

    def test_bake_timeline_keyless_dependency_without_native_sampler_is_fatal(self):
        self._configure_static_bone()
        self.cmds.node_types["constraint"] = "parentConstraint"
        self.cmds.connections[("center_joint", "translateX", True, False)] = [
            "constraint.output"
        ]

        with self.assertRaisesRegex(RuntimeError, "native bone sampling"):
            VmdSceneCollector().collect_bone_frames(
                ["center_joint"],
                0,
                2,
                dense_sample=True,
                force_dense_sample=True,
                dense_frame_samples=[0, 1, 2],
                time_converter=lambda value: value,
            )

    def test_bake_timeline_native_nonfinite_dependency_value_is_fatal(self):
        self._configure_static_bone()
        self.cmds.node_types["constraint"] = "parentConstraint"
        self.cmds.connections[("center_joint", "translateX", True, False)] = [
            "constraint.output"
        ]

        class Samples:
            def value(self, _joint, _attr, _frame):
                return float("nan")

            def close(self):
                return None

        class Sampler:
            available = True

            def sample_dense_bone_channels(self, _frames, _joints, _routes):
                return Samples()

        with self.assertRaisesRegex(RuntimeError, "non-finite"):
            VmdSceneCollector(bone_channel_sampler=Sampler()).collect_bone_frames(
                ["center_joint"],
                0,
                2,
                dense_sample=True,
                force_dense_sample=True,
                dense_frame_samples=[0, 1, 2],
                time_converter=lambda value: value,
                bone_channel_sampler=Sampler(),
            )

    def test_bake_timeline_keyless_connection_query_failure_is_fatal(self):
        self._configure_static_bone()
        original = self.cmds.listConnections

        def fail(*_args, **_kwargs):
            raise RuntimeError("connection query failed")

        self.cmds.listConnections = fail
        try:
            with self.assertRaisesRegex(RuntimeError, "connection query failed"):
                VmdSceneCollector().collect_bone_frames(
                    ["center_joint"],
                    0,
                    2,
                    dense_sample=True,
                    force_dense_sample=True,
                    dense_frame_samples=[0, 1, 2],
                    time_converter=lambda value: value,
                )
        finally:
            self.cmds.listConnections = original

    def test_dense_frame_samples_explicit_range_does_not_need_observed_keys(self):
        self.assertEqual(
            collector_module._dense_frame_samples([], 1.2, 3.8), [2, 3]
        )
        self.assertEqual(
            collector_module._dense_frame_samples([], 3.8, 1.2), []
        )

    def test_bake_timeline_keyless_bone_invalid_dense_samples_fall_back_to_range_start(self):
        self._configure_static_bone(1.0)

        frames = VmdSceneCollector().collect_bone_frames(
            ["center_joint"],
            start_frame=1.2,
            end_frame=3.8,
            dense_sample=True,
            force_dense_sample=True,
            dense_frame_samples=[-2, 0.5, 4, float("nan")],
            time_converter=lambda value: value,
        )

        self.assertEqual([frame["frame_number"] for frame in frames], [2])

    def test_bake_timeline_keyless_bone_incoming_or_empty_range_stays_unclassified(self):
        for incoming, start, end in ((True, 0, 2), (False, 0.2, 0.8)):
            with self.subTest(incoming=incoming):
                self._configure_static_bone()
                if incoming:
                    self.cmds.connections[("center_joint", "translateX", True, False)] = [
                        "constraint.output"
                    ]
                sampler = self._timeline_sampler() if incoming else None
                collector = VmdSceneCollector(bone_channel_sampler=sampler)
                frames = collector.collect_bone_frames(
                    ["center_joint"],
                    start_frame=start,
                    end_frame=end,
                    dense_sample=True,
                    force_dense_sample=True,
                    time_converter=lambda value: value,
                    bone_channel_sampler=sampler,
                )
                if incoming:
                    self.assertEqual(
                        [frame["frame_number"] for frame in frames], [0, 1, 2]
                    )
                    self.assertEqual(
                        collector.diagnostics["track_selection"]["evidence"][0][
                            "reason"
                        ],
                        "keyless_incoming_dependency",
                    )
                else:
                    self.assertEqual(frames, [])
                    self.assertNotIn("track_selection", collector.diagnostics)

    def test_bake_timeline_arbitrary_routed_output_keeps_single_key_bone_dense(self):
        self.cmds.node_types.update(
            {"center_joint": "joint", "authoring_driver": "transform"}
        )
        self.cmds.attrs[("center_joint", ATTR_MMD_BONE_NAME)] = "center"
        self.cmds.keys[("authoring_driver", "output")] = {0.0: 1.0}

        collector = VmdSceneCollector(bone_channel_sampler=self._timeline_sampler())
        frames = collector.collect_bone_frames(
            ["center_joint"],
            0,
            2,
            input_routes={"center_joint": {"translateX": ("authoring_driver", "output")}},
            dense_sample=True,
            force_dense_sample=True,
            dense_frame_samples=[0, 1, 2],
            time_converter=lambda value: value,
            bone_channel_sampler=self._timeline_sampler(),
        )

        self.assertEqual([frame["frame_number"] for frame in frames], [0, 1, 2])
        selection = collector.diagnostics["track_selection"]
        self.assertEqual(selection["counts"]["constant_one_key"], 0)
        self.assertEqual(
            selection["evidence"][0]["reason"],
            "routed_dependency",
        )

    def test_bake_timeline_validated_routed_single_key_bone_uses_one_key(self):
        self.cmds.node_types.update(
            {"model_root": "transform", "center_joint": "joint", "proxy": "transform"}
        )
        self.cmds.children["model_root"] = ["center_joint"]
        self.cmds.attrs["center_joint", ATTR_MMD_BONE_NAME] = "center"
        self.cmds.keys["proxy", "translateX"] = {0.0: 1.0}
        route = {
            attribute: ("proxy", attribute)
            for attribute in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ")
        }

        collector = VmdSceneCollector()
        frames = collector.collect_bone_frames(
            ["center_joint"],
            0,
            2,
            input_routes={"center_joint": route},
            dense_sample=True,
            force_dense_sample=True,
            dense_frame_samples=[0, 1, 2],
            time_converter=lambda value: value,
        )

        self.assertEqual([frame["frame_number"] for frame in frames], [0])
        evidence = collector.diagnostics["track_selection"]["evidence"]
        self.assertEqual(evidence[0]["reason"], "routed_direct_single_key_non_default")
        self.assertEqual(evidence[0]["source_key_count"], 1)

    def test_bake_timeline_direct_tl_ta_single_key_bone_uses_one_key(self):
        self.cmds.node_types.update(
            {
                "model_root": "transform",
                "center_joint": "joint",
                "tx_curve": "animCurveTL",
                "rz_curve": "animCurveTA",
                "time1": "time",
            }
        )
        self.cmds.children["model_root"] = ["center_joint"]
        self.cmds.attrs["center_joint", ATTR_MMD_BONE_NAME] = "center"
        self.cmds.attrs["center_joint", "translateX"] = 1.0
        self.cmds.connections.update(
            {
                ("center_joint", "translateX", True, False): ["tx_curve.output"],
                ("center_joint", "rotateZ", True, False): ["rz_curve.output"],
                ("tx_curve", "input", True, False): ["time1.outTime"],
                ("rz_curve", "input", True, False): ["time1.outTime"],
            }
        )
        self.cmds.keys["tx_curve", "output"] = {0.0: 1.0}
        self.cmds.keys["rz_curve", "output"] = {0.0: 0.0}

        collector = VmdSceneCollector()
        frames = collector.collect_bone_frames(
            ["center_joint"],
            0,
            2,
            dense_sample=True,
            force_dense_sample=True,
            dense_frame_samples=[0, 1, 2],
            time_converter=lambda value: value,
        )

        self.assertEqual([frame["frame_number"] for frame in frames], [0])
        self.assertEqual(
            collector.diagnostics["track_selection"]["evidence"][0]["reason"],
            "direct_single_key_non_default",
        )

    def test_bake_timeline_validated_animation_layer_single_key_bone_uses_one_key(self):
        self.cmds.node_types.update(
            {
                "model_root": "transform",
                "center_joint": "joint",
                "proxy": "transform",
                "blend_tx": "animBlendNodeAdditiveDL",
                "blend_rz": "animBlendNodeAdditiveRotation",
                "curve_tx": "animCurveTL",
                "curve_rz": "animCurveTA",
                "base_curve_tx": "animCurveTL",
                "base_curve_rz": "animCurveTA",
                "layer": "animLayer",
                "BaseAnimation": "animLayer",
            }
        )
        self.cmds.children["model_root"] = ["center_joint"]
        self.cmds.attrs["center_joint", ATTR_MMD_BONE_NAME] = "center"
        self.cmds.attrs["proxy", "translateX"] = 1.25
        self.cmds.anim_layer_parents["layer"] = "BaseAnimation"
        self.cmds.connections.update(
            {
                ("proxy", "translateX", True, False): ["blend_tx.output"],
                ("proxy", "rotateZ", True, False): ["blend_rz.outputZ"],
            }
        )
        self.cmds.blend_connection_pairs.update(
            {
                "blend_tx": [
                    "blend_tx.inputA", "base_curve_tx.output",
                    "blend_tx.inputB", "curve_tx.output",
                    "blend_tx.weightA", "layer.backgroundWeight",
                    "blend_tx.weightB", "layer.foregroundWeight",
                ],
                "blend_rz": [
                    "blend_rz.inputAZ", "base_curve_rz.output",
                    "blend_rz.inputBX", "curve_rz.output",
                    "blend_rz.rotateOrder", "proxy.rotateOrder",
                    "blend_rz.weightA", "layer.backgroundWeight",
                    "blend_rz.weightB", "layer.foregroundWeight",
                    "blend_rz.accumulationMode", "layer.outRotationAccumulationMode",
                ],
            }
        )
        self.cmds.keys.update(
            {
                ("curve_tx", "output"): {0.0: 1.25},
                ("curve_rz", "output"): {0.0: 0.0},
                ("base_curve_tx", "output"): {0.0: 0.0},
                ("base_curve_rz", "output"): {0.0: 0.0},
            }
        )
        route = {
            attribute: ("proxy", attribute)
            for attribute in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ")
        }

        collector = VmdSceneCollector()
        frames = collector.collect_bone_frames(
            ["center_joint"],
            0,
            2,
            input_routes={"center_joint": route},
            dense_sample=True,
            force_dense_sample=True,
            dense_frame_samples=[0, 1, 2],
            time_converter=lambda value: value,
        )

        self.assertEqual([frame["frame_number"] for frame in frames], [0])
        self.assertEqual(
            collector.diagnostics["track_selection"]["evidence"][0]["reason"],
            "layered_direct_single_key_non_default",
        )

    def test_bake_timeline_animation_layer_base_curve_extra_key_keeps_bone_dense(self):
        self.test_bake_timeline_validated_animation_layer_single_key_bone_uses_one_key()
        self.cmds.keys["base_curve_tx", "output"][2.0] = 0.5
        route = {
            attribute: ("proxy", attribute)
            for attribute in collector_module._BONE_EXPORT_ATTRS
        }
        collector = VmdSceneCollector(bone_channel_sampler=self._timeline_sampler())
        frames = collector.collect_bone_frames(
            ["center_joint"],
            0,
            2,
            input_routes={"center_joint": route},
            dense_sample=True,
            force_dense_sample=True,
            dense_frame_samples=[0, 1, 2],
            time_converter=lambda value: value,
            bone_channel_sampler=self._timeline_sampler(),
        )
        self.assertEqual([frame["frame_number"] for frame in frames], [0, 1, 2])

    def test_bake_timeline_validated_animation_layer_default_single_key_is_omitted(self):
        self.test_bake_timeline_validated_animation_layer_single_key_bone_uses_one_key()
        self.cmds.attrs["proxy", "translateX"] = 0.0
        route = {
            attribute: ("proxy", attribute)
            for attribute in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ")
        }
        collector = VmdSceneCollector()
        frames = collector.collect_bone_frames(
            ["center_joint"],
            0,
            2,
            input_routes={"center_joint": route},
            dense_sample=True,
            force_dense_sample=True,
            dense_frame_samples=[0, 1, 2],
            time_converter=lambda value: value,
        )
        self.assertEqual(frames, [])
        self.assertEqual(
            collector.diagnostics["track_selection"]["evidence"][0]["reason"],
            "layered_direct_single_key_default",
        )

    def test_bake_timeline_animation_layer_weight_key_keeps_single_key_bone_dense(self):
        self.test_bake_timeline_validated_animation_layer_single_key_bone_uses_one_key()
        self.cmds.keys["layer", "weight"] = {0.0: 1.0}
        route = {
            attribute: ("proxy", attribute)
            for attribute in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ")
        }
        collector = VmdSceneCollector()
        frames = collector.collect_bone_frames(
            ["center_joint"],
            0,
            2,
            input_routes={"center_joint": route},
            dense_sample=True,
            force_dense_sample=True,
            dense_frame_samples=[0, 1, 2],
            time_converter=lambda value: value,
            bone_channel_sampler=self._timeline_sampler(),
        )
        self.assertEqual([frame["frame_number"] for frame in frames], [0, 1, 2])

    def test_bake_timeline_routed_single_key_query_failure_keeps_dense(self):
        self.test_bake_timeline_validated_routed_single_key_bone_uses_one_key()
        route = {
            attribute: ("proxy", attribute)
            for attribute in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ")
        }
        original = self.cmds.listConnections

        def fail(plug, *args, **kwargs):
            if plug == "proxy.translateX":
                raise RuntimeError("route query failed")
            return original(plug, *args, **kwargs)

        self.cmds.listConnections = fail
        try:
            collector = VmdSceneCollector(bone_channel_sampler=self._timeline_sampler())
            frames = collector.collect_bone_frames(
                ["center_joint"],
                0,
                2,
                input_routes={"center_joint": route},
                dense_sample=True,
                force_dense_sample=True,
                dense_frame_samples=[0, 1, 2],
                time_converter=lambda value: value,
                bone_channel_sampler=self._timeline_sampler(),
            )
        finally:
            self.cmds.listConnections = original
        self.assertEqual([frame["frame_number"] for frame in frames], [0, 1, 2])

    def test_bake_timeline_routed_nonwritable_single_key_keeps_dense(self):
        self.test_bake_timeline_validated_routed_single_key_bone_uses_one_key()
        route = {
            attribute: ("proxy", attribute)
            for attribute in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ")
        }
        with mock.patch.object(collector_module, "_bake_timeline_writable_plug", return_value=False):
            collector = VmdSceneCollector(bone_channel_sampler=self._timeline_sampler())
            frames = collector.collect_bone_frames(
                ["center_joint"],
                0,
                2,
                input_routes={"center_joint": route},
                dense_sample=True,
                force_dense_sample=True,
                dense_frame_samples=[0, 1, 2],
                time_converter=lambda value: value,
                bone_channel_sampler=self._timeline_sampler(),
            )
        self.assertEqual([frame["frame_number"] for frame in frames], [0, 1, 2])

    def test_bake_timeline_direct_nonconstant_multi_key_bone_stays_dense(self):
        self.cmds.node_types.update({"model_root": "transform", "center_joint": "joint"})
        self.cmds.children["model_root"] = ["center_joint"]
        self.cmds.attrs[("center_joint", ATTR_MMD_BONE_NAME)] = "center"
        self.cmds.keys[("center_joint", "translateX")] = {
            0.0: 0.0,
            1.0: 1.0,
            2.0: 1.0000000001,
        }

        collector, _result, sink = self._collect_to_sink(
            {
                "target_model": "model_root",
                "export_strategy": "bake_timeline",
                "frame_range": (0, 2),
                "bake_timeline_exact_run_reduction": False,
            },
            self._timeline_sampler(),
        )

        self.assertEqual(
            [
                frame["frame_number"]
                for section, frame in sink.frames
                if section == "bones"
            ],
            [0, 1, 2],
        )
        self.assertEqual(
            collector.diagnostics["track_selection"]["counts"]["authored_sampled"], 1
        )

    def test_bake_timeline_routed_constant_multi_key_bone_remains_dependency_dense(self):
        self.cmds.node_types.update(
            {
                "model_root": "transform",
                "center_joint": "joint",
                "driver": "transform",
            }
        )
        self.cmds.children["model_root"] = ["center_joint"]
        self.cmds.attrs[("center_joint", ATTR_MMD_BONE_NAME)] = "center"
        self.cmds.keys[("driver", "output")] = {0.0: 0.5, 2.0: 0.5}

        collector = VmdSceneCollector(bone_channel_sampler=self._timeline_sampler())
        frames = collector.collect_bone_frames(
            ["center_joint"],
            0,
            2,
            input_routes={"center_joint": {"translateX": ("driver", "output")}},
            dense_sample=True,
            force_dense_sample=True,
            dense_frame_samples=[0, 1, 2],
            time_converter=lambda value: value,
            bone_channel_sampler=self._timeline_sampler(),
        )

        self.assertEqual(
            [frame["frame_number"] for frame in frames], [0, 1, 2]
        )
        self.assertEqual(
            collector.diagnostics["track_selection"]["counts"]["dependency_baked"], 1
        )

    def test_bake_timeline_direct_single_key_bone_excludes_only_that_native_track(self):
        self.cmds.node_types.update(
            {"model_root": "transform", "single_joint": "joint", "dense_joint": "joint"}
        )
        self.cmds.children["model_root"] = ["single_joint", "dense_joint"]
        self.cmds.attrs[("single_joint", ATTR_MMD_BONE_NAME)] = "single"
        self.cmds.attrs[("dense_joint", ATTR_MMD_BONE_NAME)] = "dense"
        self.cmds.keys[("single_joint", "translateX")] = {0.0: 0.0}
        self.cmds.keys[("dense_joint", "translateX")] = {0.0: 0.0, 2.0: 1.0}
        sampler = self._timeline_sampler()
        sampled_joints = []
        sample = sampler.sample_dense_bone_channels

        def capture(frames, joints, routes):
            sampled_joints.extend(joints)
            return sample(frames, joints, routes)

        sampler.sample_dense_bone_channels = capture
        _collector, _result, sink = self._collect_to_sink(
            {
                "target_model": "model_root",
                "export_strategy": "bake_timeline",
                "frame_range": (0, 2),
            },
            sampler,
        )

        self.assertEqual(sampled_joints, ["dense_joint"])
        self.assertEqual(
            [
                (frame["bone_name"], frame["frame_number"])
                for section, frame in sink.frames
                if section == "bones"
            ],
            [("dense", 0), ("dense", 1), ("dense", 2)],
        )

    def test_bake_timeline_direct_single_key_morphs_do_not_scrub_dense_range(self):
        self.cmds.node_types["face_bs"] = "blendShape"
        self.cmds.blendshape_weights["face_bs"] = 2
        self.cmds.aliases.update(
            {"face_bs.weight[0]": "zero", "face_bs.weight[1]": "smile"}
        )
        self.cmds.keys[("face_bs", "weight[0]")] = {0.0: 0.0}
        self.cmds.keys[("face_bs", "weight[1]")] = {0.0: 0.5}

        collector = VmdSceneCollector()
        frames = collector.collect_morph_frames(
            ["face_bs"],
            time_converter=lambda value: value,
            dense_sample=True,
            dense_frame_samples=[0, 1, 2],
            timeline_evaluation=True,
        )

        self.assertEqual(frames, [{"morph_name": "smile", "frame_number": 0, "weight": 0.5}])
        self.assertFalse(any(frame in {1.0, 2.0} for frame in self.cmds.current_time_calls))
        selection = collector.diagnostics["track_selection"]
        self.assertEqual(selection["counts"]["omitted_default"], 1)
        self.assertEqual(selection["counts"]["constant_one_key"], 1)

    def test_bake_timeline_direct_constant_multi_key_morph_collapses_after_timeline_sampling(self):
        self.cmds.node_types["face_bs"] = "blendShape"
        self.cmds.blendshape_weights["face_bs"] = 1
        self.cmds.aliases["face_bs.weight[0]"] = "smile"
        self.cmds.keys[("face_bs", "weight[0]")] = {0.0: 0.5, 2.0: 0.5}
        self.cmds.attrs[("face_bs", "weight[0]")] = 0.5

        collector = VmdSceneCollector()
        frames = collector.collect_morph_frames(
            ["face_bs"],
            time_converter=lambda value: value,
            dense_sample=True,
            dense_frame_samples=[0, 1, 2],
            timeline_evaluation=True,
        )

        self.assertEqual(frames, [{"morph_name": "smile", "frame_number": 0, "weight": 0.5}])
        evidence = collector.diagnostics["track_selection"]["evidence"]
        self.assertEqual(evidence[0]["decision"], "constant_one_key")
        self.assertEqual(evidence[0]["reason"], "dense_exact_constant")
        self.assertEqual(evidence[0]["source_key_count"], 2)
        self.assertEqual(evidence[0]["planned_key_count"], 1)

    def test_bake_timeline_direct_nonconstant_multi_key_morph_stays_dense(self):
        self.cmds.node_types["face_bs"] = "blendShape"
        self.cmds.blendshape_weights["face_bs"] = 1
        self.cmds.aliases["face_bs.weight[0]"] = "smile"
        self.cmds.keys[("face_bs", "weight[0]")] = {
            0.0: 0.5,
            1.0: 0.6,
            2.0: 0.5000000001,
        }

        collector = VmdSceneCollector()
        frames = collector.collect_morph_frames(
            ["face_bs"],
            time_converter=lambda value: value,
            dense_sample=True,
            dense_frame_samples=[0, 1, 2],
            timeline_evaluation=True,
        )

        self.assertEqual([frame["frame_number"] for frame in frames], [0, 1, 2])
        self.assertEqual(
            collector.diagnostics["track_selection"]["counts"]["authored_sampled"], 1
        )

    def test_bake_timeline_controller_constant_multi_key_morph_collapses_after_timeline_sampling(self):
        self.cmds.node_types.update(
            {"model_root": "transform", "morph_controller": "mmdMorphController"}
        )
        self.cmds.attrs[("model_root", "mmd_morph_controller")] = True
        self.cmds.connections[("model_root", "mmd_morph_controller", True, False)] = [
            "morph_controller"
        ]
        self.cmds.keys[("morph_controller", "inputWeight[3]")] = {
            0.0: 0.5,
            2.0: 0.5,
        }
        self.cmds.attrs[("morph_controller", "inputWeight[3]")] = 0.5
        metadata = [SimpleNamespace(morph_type="bone", name="bone_morph", index=3)]

        with mock.patch.object(
            collector_module, "iter_morph_network_metadata", return_value=metadata
        ):
            collector = VmdSceneCollector()
            frames = collector.collect_morph_frames(
                [],
                target_model="model_root",
                time_converter=lambda value: value,
                dense_sample=True,
                dense_frame_samples=[0, 1, 2],
                timeline_evaluation=True,
            )

        self.assertEqual([frame["frame_number"] for frame in frames], [0])
        self.assertEqual(
            collector.diagnostics["track_selection"]["counts"]["constant_one_key"], 1
        )
        self.assertEqual(
            collector.diagnostics["track_selection"]["evidence"][0]["reason"],
            "dense_exact_constant",
        )

    def test_bake_timeline_controller_direct_single_default_omits_without_dense_scrub(self):
        self.cmds.node_types["morph_controller"] = "mmdMorphController"
        self.cmds.keys["morph_controller", "inputWeight[0]"] = {1.0: 0.0}
        self.cmds.attrs["morph_controller", "inputWeight[0]"] = 0.0
        metadata = [SimpleNamespace(morph_type="bone", name="bone_morph", index=0)]

        with mock.patch.object(
            collector_module, "_morph_controller_for_model", return_value="morph_controller"
        ), mock.patch.object(
            collector_module, "iter_morph_network_metadata", return_value=metadata
        ):
            collector = VmdSceneCollector()
            frames = collector.collect_morph_frames(
                [],
                target_model="model_root",
                start_frame=0,
                end_frame=2,
                time_converter=lambda value: value,
                dense_sample=True,
                dense_frame_samples=[0, 1, 2],
                timeline_evaluation=True,
            )

        self.assertEqual(frames, [])
        self.assertEqual(self.cmds.current_time_calls, [1.0, 0.0])
        evidence = collector.diagnostics["track_selection"]["evidence"]
        self.assertEqual(evidence[0]["decision"], "omitted_default")
        self.assertEqual(evidence[0]["reason"], "controller_direct_single_default")
        self.assertEqual(evidence[0]["source_key_count"], 1)
        self.assertEqual(evidence[0]["planned_key_count"], 0)

    def test_bake_timeline_controller_direct_single_non_default_emits_one_key(self):
        self.cmds.node_types["morph_controller"] = "mmdMorphController"
        self.cmds.keys["morph_controller", "inputWeight[0]"] = {1.0: 0.5}
        self.cmds.attrs["morph_controller", "inputWeight[0]"] = 0.5
        metadata = [SimpleNamespace(morph_type="bone", name="bone_morph", index=0)]

        with mock.patch.object(
            collector_module, "_morph_controller_for_model", return_value="morph_controller"
        ), mock.patch.object(
            collector_module, "iter_morph_network_metadata", return_value=metadata
        ):
            collector = VmdSceneCollector()
            frames = collector.collect_morph_frames(
                [],
                target_model="model_root",
                start_frame=0,
                end_frame=2,
                time_converter=lambda value: value,
                dense_sample=True,
                dense_frame_samples=[0, 1, 2],
                timeline_evaluation=True,
            )

        self.assertEqual(
            frames,
            [{"morph_name": "bone_morph", "frame_number": 1, "weight": 0.5}],
        )
        self.assertEqual(self.cmds.current_time_calls, [1.0, 0.0])
        evidence = collector.diagnostics["track_selection"]["evidence"]
        self.assertEqual(evidence[0]["decision"], "constant_one_key")
        self.assertEqual(evidence[0]["reason"], "controller_direct_single_non_default")

    def test_bake_timeline_controller_direct_multi_key_near_equal_or_overshoot_stays_dense(self):
        self.cmds.node_types["morph_controller"] = "mmdMorphController"
        metadata = [SimpleNamespace(morph_type="bone", name="bone_morph", index=0)]

        for values in (
            {0.0: 0.5, 1.0: 0.5000000001, 2.0: 0.5},
            {0.0: 0.5, 1.0: 0.6, 2.0: 0.5},
        ):
            with self.subTest(values=values):
                self.cmds.keys["morph_controller", "inputWeight[0]"] = values
                self.cmds.attrs["morph_controller", "inputWeight[0]"] = values[0.0]
                self.cmds.current_time_calls.clear()
                with mock.patch.object(
                    collector_module,
                    "_morph_controller_for_model",
                    return_value="morph_controller",
                ), mock.patch.object(
                    collector_module,
                    "iter_morph_network_metadata",
                    return_value=metadata,
                ):
                    collector = VmdSceneCollector()
                    frames = collector.collect_morph_frames(
                        [],
                        target_model="model_root",
                        start_frame=0,
                        end_frame=2,
                        time_converter=lambda value: value,
                        dense_sample=True,
                        dense_frame_samples=[0, 1, 2],
                        timeline_evaluation=True,
                    )

                self.assertEqual([frame["frame_number"] for frame in frames], [0, 1, 2])
                self.assertEqual(
                    collector.diagnostics["track_selection"]["counts"][
                        "authored_sampled"
                    ],
                    1,
                )
                self.assertEqual(
                    collector.diagnostics["track_selection"]["evidence"][0]["reason"],
                    "multiple_source_keys",
                )

    def test_bake_timeline_controller_nonanim_incoming_remains_dependency_dense(self):
        self.cmds.node_types["morph_controller"] = "mmdMorphController"
        self.cmds.keys["morph_controller", "inputWeight[0]"] = {0.0: 0.5, 2.0: 0.5}
        self.cmds.attrs["morph_controller", "inputWeight[0]"] = 0.5
        self.cmds.connections[
            "morph_controller", "inputWeight[0]", True, False
        ] = ["constraint.output"]
        metadata = [SimpleNamespace(morph_type="bone", name="bone_morph", index=0)]

        with mock.patch.object(
            collector_module, "_morph_controller_for_model", return_value="morph_controller"
        ), mock.patch.object(
            collector_module, "iter_morph_network_metadata", return_value=metadata
        ):
            collector = VmdSceneCollector()
            frames = collector.collect_morph_frames(
                [],
                target_model="model_root",
                start_frame=0,
                end_frame=2,
                time_converter=lambda value: value,
                dense_sample=True,
                dense_frame_samples=[0, 1, 2],
                timeline_evaluation=True,
            )

        self.assertEqual([frame["frame_number"] for frame in frames], [0, 1, 2])
        evidence = collector.diagnostics["track_selection"]["evidence"]
        self.assertEqual(evidence[0]["decision"], "dependency_baked")
        self.assertEqual(evidence[0]["reason"], "morph_controller_route")

    def test_bake_timeline_controller_out_of_range_second_key_is_not_direct_single(self):
        self.cmds.node_types["morph_controller"] = "mmdMorphController"
        self.cmds.keys["morph_controller", "inputWeight[0]"] = {0.0: 0.5, 20.0: 0.5}
        self.cmds.attrs["morph_controller", "inputWeight[0]"] = 0.5
        metadata = [SimpleNamespace(morph_type="bone", name="bone_morph", index=0)]

        with mock.patch.object(
            collector_module, "_morph_controller_for_model", return_value="morph_controller"
        ), mock.patch.object(
            collector_module, "iter_morph_network_metadata", return_value=metadata
        ):
            collector = VmdSceneCollector()
            frames = collector.collect_morph_frames(
                [],
                target_model="model_root",
                start_frame=0,
                end_frame=10,
                time_converter=lambda value: value,
                dense_sample=True,
                dense_frame_samples=list(range(11)),
                timeline_evaluation=True,
            )

        self.assertEqual([frame["frame_number"] for frame in frames], list(range(11)))
        self.assertEqual(
            collector.diagnostics["track_selection"]["counts"]["dependency_baked"],
            1,
        )
        self.assertEqual(
            collector.diagnostics["track_selection"]["evidence"][0]["source_key_count"],
            1,
        )

    def test_bake_timeline_exact_default_multi_key_bone_and_morph_are_omitted(self):
        self.cmds.node_types.update(
            {"model_root": "transform", "center_joint": "joint", "face_bs": "blendShape"}
        )
        self.cmds.children["model_root"] = ["center_joint"]
        self.cmds.attrs[("center_joint", ATTR_MMD_BONE_NAME)] = "center"
        self.cmds.keys[("center_joint", "translateX")] = {0.0: 0.0, 2.0: 0.0}
        self.cmds.blendshape_weights["face_bs"] = 1
        self.cmds.aliases["face_bs.weight[0]"] = "zero"
        self.cmds.keys[("face_bs", "weight[0]")] = {0.0: 0.0, 2.0: 0.0}
        self.cmds.attrs[("face_bs", "weight[0]")] = 0.0

        collector = VmdSceneCollector(bone_channel_sampler=self._timeline_sampler())
        self.assertEqual(
            collector.collect_bone_frames(
                ["center_joint"],
                dense_sample=True,
                force_dense_sample=True,
                dense_frame_samples=[0, 1, 2],
                time_converter=lambda value: value,
                bone_channel_sampler=self._timeline_sampler(),
            ),
            [],
        )
        self.assertEqual(
            collector.collect_morph_frames(
                ["face_bs"],
                dense_sample=True,
                dense_frame_samples=[0, 1, 2],
                time_converter=lambda value: value,
                timeline_evaluation=True,
            ),
            [],
        )
        selection = collector.diagnostics["track_selection"]
        self.assertEqual(selection["counts"]["omitted_default"], 2)
        self.assertEqual(
            [entry["reason"] for entry in selection["evidence"]],
            ["dense_exact_constant", "dense_exact_constant"],
        )

    def _configure_static_morph(self, weight=None):
        self.cmds.node_types["face_bs"] = "blendShape"
        self.cmds.blendshape_weights["face_bs"] = 1
        self.cmds.aliases["face_bs.weight[0]"] = "smile"
        if weight is not None:
            self.cmds.attrs[("face_bs", "weight[0]")] = weight

    def test_bake_timeline_keyless_direct_morph_default_and_non_default_sample_once(self):
        for weight in (0.0, 0.5):
            with self.subTest(weight=weight):
                self._configure_static_morph(weight)
                self.cmds.current_time_calls.clear()
                collector = VmdSceneCollector()
                frames = collector.collect_morph_frames(
                    ["face_bs"],
                    start_frame=1.2,
                    end_frame=3.8,
                    time_converter=lambda value: value,
                    dense_sample=True,
                    dense_frame_samples=[3, 4],
                    timeline_evaluation=True,
                )

                if weight == 0.0:
                    self.assertEqual(frames, [])
                    self.assertEqual(
                        collector.diagnostics["track_selection"]["counts"][
                            "omitted_default"
                        ],
                        1,
                    )
                else:
                    self.assertEqual(
                        [frame["frame_number"] for frame in frames],
                        [3],
                    )
                    self.assertEqual(
                        collector.diagnostics["track_selection"]["counts"][
                            "constant_one_key"
                        ],
                        1,
                    )
                self.assertEqual(self.cmds.current_time_calls, [3.0, 0.0])

    def test_bake_timeline_keyless_morph_invalid_dense_samples_fall_back_to_range_start(self):
        self._configure_static_morph(0.5)

        frames = VmdSceneCollector().collect_morph_frames(
            ["face_bs"],
            start_frame=1.2,
            end_frame=3.8,
            time_converter=lambda value: value,
            dense_sample=True,
            dense_frame_samples=[-2, 0.5, 4, float("inf")],
            timeline_evaluation=True,
        )

        self.assertEqual([frame["frame_number"] for frame in frames], [2])
        self.assertEqual(self.cmds.current_time_calls, [2.0, 0.0])

    def test_bake_timeline_keyless_morph_with_incoming_or_controller_is_dependency(self):
        self._configure_static_morph()
        self.cmds.connections[("face_bs", "weight[0]", True, False)] = [
            "animCurve.output"
        ]

        collector = VmdSceneCollector()
        frames = collector.collect_morph_frames(
            ["face_bs"],
            start_frame=0,
            end_frame=2,
            dense_sample=True,
            timeline_evaluation=True,
        )
        self.assertEqual([frame["frame_number"] for frame in frames], [0, 1, 2])
        evidence = collector.diagnostics["track_selection"]["evidence"]
        self.assertEqual(evidence[0]["decision"], "dependency_baked")
        self.assertEqual(evidence[0]["reason"], "keyless_incoming_dependency")
        self.assertEqual(evidence[0]["source_key_count"], 0)

        self.cmds.connections.clear()
        self.cmds.node_types["morph_controller"] = "mmdMorphController"
        with mock.patch.object(
            collector_module,
            "_morph_controller_for_model",
            return_value="morph_controller",
        ), mock.patch.object(
            collector_module,
            "iter_morph_network_metadata",
            return_value=[SimpleNamespace(name="bone_morph", index=0)],
        ):
            collector = VmdSceneCollector()
            frames = collector.collect_morph_frames(
                [],
                target_model="model_root",
                start_frame=0,
                end_frame=2,
                dense_sample=True,
                timeline_evaluation=True,
            )
        self.assertEqual([frame["frame_number"] for frame in frames], [0, 1, 2])
        evidence = collector.diagnostics["track_selection"]["evidence"]
        self.assertEqual(evidence[0]["decision"], "dependency_baked")
        self.assertEqual(evidence[0]["reason"], "keyless_controller_dependency")
        self.assertEqual(evidence[0]["source_key_count"], 0)

    def test_public_bake_timeline_keyless_incoming_morph_uses_explicit_range(self):
        self._configure_static_morph()
        self.cmds.node_types["model_root"] = "transform"
        self.cmds.connections[("face_bs", "weight[0]", True, False)] = [
            "constraint.output"
        ]

        _collector, _result, sink = self._collect_to_sink(
            {
                "target_model": "model_root",
                "blend_shapes": ["face_bs"],
                "export_strategy": "bake_timeline",
                "frame_range": (0, 2),
                "bake_timeline_exact_run_reduction": False,
            },
            self._timeline_sampler(),
        )
        frames = [
            frame for section, frame in sink.frames if section == "morphs"
        ]

        self.assertEqual([frame["frame_number"] for frame in frames], [0, 1, 2])

    def test_bake_timeline_keyless_morph_connection_query_failure_raises(self):
        self._configure_static_morph()
        original = self.cmds.listConnections

        def fail(*_args, **_kwargs):
            raise RuntimeError("morph connection query failed")

        self.cmds.listConnections = fail
        try:
            with self.assertRaisesRegex(RuntimeError, "morph connection query failed"):
                VmdSceneCollector().collect_morph_frames(
                    ["face_bs"],
                    start_frame=0,
                    end_frame=2,
                    dense_sample=True,
                    timeline_evaluation=True,
                )
        finally:
            self.cmds.listConnections = original

    def test_bake_timeline_duplicate_morph_controller_index_fails_closed(self):
        self.cmds.node_types["morph_controller"] = "mmdMorphController"
        metadata = [
            SimpleNamespace(name="first", index=0, node="provider_a"),
            SimpleNamespace(name="second", index=0, node="provider_b"),
        ]
        with mock.patch.object(
            collector_module, "_morph_controller_for_model", return_value="morph_controller"
        ), mock.patch.object(
            collector_module, "iter_morph_network_metadata", return_value=metadata
        ):
            with self.assertRaisesRegex(ValueError, "duplicate controller index 0"):
                VmdSceneCollector().collect_morph_frames(
                    [],
                    target_model="model_root",
                    start_frame=0,
                    end_frame=2,
                    dense_sample=True,
                    timeline_evaluation=True,
                )

    def test_bake_timeline_duplicate_morph_controller_name_fails_closed(self):
        self.cmds.node_types["morph_controller"] = "mmdMorphController"
        metadata = [
            SimpleNamespace(name="same", index=0, node="provider_a"),
            SimpleNamespace(name="same", index=1, node="provider_b"),
        ]
        with mock.patch.object(
            collector_module, "_morph_controller_for_model", return_value="morph_controller"
        ), mock.patch.object(
            collector_module, "iter_morph_network_metadata", return_value=metadata
        ):
            with self.assertRaisesRegex(ValueError, "duplicate controller name"):
                VmdSceneCollector().collect_morph_frames(
                    [],
                    target_model="model_root",
                    start_frame=0,
                    end_frame=2,
                    dense_sample=True,
                    timeline_evaluation=True,
                )

    def test_bake_timeline_vertex_controller_owns_duplicate_blendshape_output(self):
        self.cmds.node_types.update(
            {
                "model_root": "transform",
                "morph_controller": "mmdMorphController",
                "face_bs": "blendShape",
            }
        )
        self.cmds.attrs["face_bs", ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON] = json.dumps(
            {"0": "shared"}, ensure_ascii=False
        )
        self.cmds.blendshape_weights["face_bs"] = 1
        self.cmds.keys["face_bs", "weight[0]"] = {0.0: 0.25, 2.0: 0.25}
        self.cmds.keys["morph_controller", "inputWeight[0]"] = {
            0.0: 0.1,
            2.0: 0.9,
        }
        self.cmds.attrs["morph_controller", "inputWeight[0]"] = 0.7
        metadata = [
            SimpleNamespace(
                morph_type="vertex",
                name="shared",
                index=0,
                node="vertex_provider",
            )
        ]
        with mock.patch.object(
            collector_module,
            "_morph_controller_for_model",
            return_value="morph_controller",
        ), mock.patch.object(
            collector_module,
            "iter_morph_network_metadata",
            return_value=metadata,
        ):
            collector = VmdSceneCollector()
            frames = collector.collect_morph_frames(
                ["face_bs"],
                target_model="model_root",
                start_frame=0,
                end_frame=2,
                dense_sample=True,
                timeline_evaluation=True,
            )

        self.assertEqual(
            [frame["frame_number"] for frame in frames],
            [0, 1, 2],
        )
        self.assertEqual(
            [frame["weight"] for frame in frames],
            [0.1, 0.7, 0.9],
        )
        self.assertNotIn(0.25, [frame["weight"] for frame in frames])
        evidence = collector.diagnostics["track_selection"]["evidence"]
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["decision"], "authored_sampled")
        self.assertEqual(evidence[0]["reason"], "multiple_source_keys")

    def test_bake_timeline_nonvertex_controller_duplicate_blendshape_output_raises(self):
        self.cmds.node_types.update(
            {
                "model_root": "transform",
                "morph_controller": "mmdMorphController",
                "face_bs": "blendShape",
            }
        )
        self.cmds.attrs["face_bs", ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON] = json.dumps(
            {"0": "shared"}, ensure_ascii=False
        )
        self.cmds.blendshape_weights["face_bs"] = 1
        self.cmds.keys["face_bs", "weight[0]"] = {0.0: 0.25}
        metadata = [
            SimpleNamespace(
                morph_type="bone",
                name="shared",
                index=0,
                node="bone_provider",
            )
        ]
        with mock.patch.object(
            collector_module,
            "_morph_controller_for_model",
            return_value="morph_controller",
        ), mock.patch.object(
            collector_module,
            "iter_morph_network_metadata",
            return_value=metadata,
        ):
            with self.assertRaisesRegex(ValueError, "non-vertex controller"):
                VmdSceneCollector().collect_morph_frames(
                    ["face_bs"],
                    target_model="model_root",
                    start_frame=0,
                    end_frame=2,
                    dense_sample=True,
                    timeline_evaluation=True,
                )

    def test_bake_timeline_duplicate_blendshape_output_provider_fails_closed(self):
        self.cmds.node_types.update({"face_bs_a": "blendShape", "face_bs_b": "blendShape"})
        self.cmds.blendshape_weights.update({"face_bs_a": 1, "face_bs_b": 1})
        self.cmds.aliases.update(
            {"face_bs_a.weight[0]": "same", "face_bs_b.weight[0]": "same"}
        )
        self.cmds.connections.update(
            {
                ("face_bs_a", "weight[0]", True, False): ["constraint_a.output"],
                ("face_bs_b", "weight[0]", True, False): ["constraint_b.output"],
            }
        )

        with self.assertRaisesRegex(ValueError, "duplicate providers"):
            VmdSceneCollector().collect_morph_frames(
                ["face_bs_a", "face_bs_b"],
                start_frame=0,
                end_frame=2,
                dense_sample=True,
                timeline_evaluation=True,
            )

    def test_bake_timeline_keyless_morph_noninteger_range_does_not_invent_sample(self):
        self._configure_static_morph()

        collector = VmdSceneCollector()
        frames = collector.collect_morph_frames(
            ["face_bs"],
            start_frame=0.2,
            end_frame=0.8,
            dense_sample=True,
            timeline_evaluation=True,
        )

        self.assertEqual(frames, [])
        self.assertEqual(self.cmds.current_time_calls, [])
        self.assertNotIn("track_selection", collector.diagnostics)

    def test_bake_timeline_morph_with_out_of_range_second_key_remains_dense(self):
        self.cmds.node_types["face_bs"] = "blendShape"
        self.cmds.blendshape_weights["face_bs"] = 1
        self.cmds.aliases["face_bs.weight[0]"] = "smile"
        self.cmds.keys[("face_bs", "weight[0]")] = {0.0: 0.0, 20.0: 0.5}

        collector = VmdSceneCollector()
        frames = collector.collect_morph_frames(
            ["face_bs"],
            start_frame=0,
            end_frame=10,
            time_converter=lambda value: value,
            dense_sample=True,
            dense_frame_samples=list(range(11)),
            timeline_evaluation=True,
        )

        self.assertEqual([frame["frame_number"] for frame in frames], list(range(11)))
        selection = collector.diagnostics["track_selection"]
        self.assertEqual(selection["counts"]["authored_sampled"], 1)
        self.assertEqual(selection["counts"]["constant_one_key"], 0)
        self.assertEqual(selection["counts"]["omitted_default"], 0)

    def test_bake_timeline_controller_single_key_default_morph_is_omitted(self):
        self.cmds.node_types["morph_controller"] = "mmdMorphController"
        self.cmds.keys[("morph_controller", "inputWeight[0]")] = {0.0: 0.0}
        collector = VmdSceneCollector()

        with mock.patch.object(
            collector_module, "_morph_controller_for_model", return_value="morph_controller"
        ), mock.patch.object(
            collector_module,
            "iter_morph_network_metadata",
            return_value=[SimpleNamespace(name="bone_morph", index=0)],
        ):
            frames = collector.collect_morph_frames(
                [],
                target_model="model_root",
                time_converter=lambda value: value,
                dense_sample=True,
                dense_frame_samples=[0, 1, 2],
                timeline_evaluation=True,
            )

        self.assertEqual(frames, [])
        selection = collector.diagnostics["track_selection"]
        self.assertEqual(selection["counts"]["omitted_default"], 1)
        self.assertEqual(selection["counts"]["constant_one_key"], 0)
        self.assertEqual(
            selection["evidence"][0]["reason"], "controller_direct_single_default"
        )

    def test_track_selection_diagnostics_are_bounded_and_counted(self):
        collector = VmdSceneCollector()
        for index in range(collector_module._MAX_TRACK_SELECTION_EVIDENCE + 1):
            collector._record_track_selection(
                "bone", f"bone_{index}", "authored_sampled", "multiple_source_keys", 2, 3
            )
        collector._record_track_selection(
            "Bone", "センター", "omitted_default", "direct_single_key_default", 2, 0
        )
        collector._record_track_selection(
            "bone", "センター", "omitted_default", "direct_single_key_default", 2, 0
        )
        collector._record_track_selection(
            "BONE", "  CENTER  Bone ", "omitted_default", "direct_single_key_default", 1, 0
        )
        collector._record_track_selection(
            "bone", "center bone", "omitted_default", "direct_single_key_default", 1, 0
        )
        collector._record_track_selection(
            "MORPH", "smile", "omitted_default", "direct_single_key_default", 3, 0
        )
        collector._record_track_selection(
            "morph", "keyless", "omitted_default", "keyless_static_default", 0, 0
        )

        selection = collector.diagnostics["track_selection"]
        self.assertEqual(selection["counts"]["authored_sampled"], 129)
        self.assertEqual(selection["counts"]["omitted_default"], 6)
        self.assertEqual(selection["counts_by_section"]["bone"]["authored_sampled"], 129)
        self.assertEqual(selection["counts_by_section"]["bone"]["omitted_default"], 4)
        self.assertEqual(selection["counts_by_section"]["morph"]["omitted_default"], 2)
        self.assertEqual(
            selection["key_counts"],
            {"source": 267, "planned": 387, "reduced": 9, "added": 129},
        )
        self.assertEqual(len(selection["evidence"]), 128)
        self.assertEqual(selection["evidence_omitted_count"], 7)
        identities = [
            ["bone", "center bone"],
            ["bone", "センター"],
            ["morph", "smile"],
        ]
        self.assertEqual(
            selection["source_omission_identity"],
            {"count": 3, "fingerprint": fingerprint_payload(identities)},
        )
        self.assertEqual(
            selection["evidence"][0],
            {
                "section": "bone",
                "name": "bone_0",
                "decision": "authored_sampled",
                "reason": "multiple_source_keys",
                "source_key_count": 2,
                "planned_key_count": 3,
            },
        )

    def test_track_selection_source_omission_identity_resets_per_collect(self):
        collector = VmdSceneCollector()
        collector._record_track_selection(
            "bone", "center", "omitted_default", "direct_single_key_default", 1, 0
        )
        first = collector.diagnostics["track_selection"]["source_omission_identity"]
        self.assertEqual(first["count"], 1)

        collector.collect({})
        selection = collector.diagnostics["track_selection"]
        self.assertEqual(selection["source_omission_identity"]["count"], 0)
        self.assertEqual(
            selection["source_omission_identity"]["fingerprint"], fingerprint_payload([])
        )

    def test_bake_timeline_morph_sampling_is_frame_major_current_time_and_restores(self):
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

    def test_bake_timeline_camera_and_light_use_current_frame_without_double_scrub(self):
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

    def test_bake_timeline_ik_uses_ascending_current_time_and_restores(self):
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

    def test_bake_timeline_timeline_blocks_playback_and_restores_after_sample_error(self):
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

    def test_bake_timeline_timeline_restore_failure_blocks_export(self):
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

    def test_bake_timeline_timeline_reader_rejects_backward_sampling(self):
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
            "export_strategy": "bake_timeline",
            "frame_range": (0, 2),
        }
        captured = []
        _plain_collector, plain, plain_sink = self._collect_to_sink(
            options,
            self._timeline_sampler(),
        )
        instrumented, with_sink, instrumented_sink = self._collect_to_sink(
            options,
            self._timeline_sampler(),
            captured.append,
        )

        self.assertEqual(plain["section_counts"], with_sink["section_counts"])
        self.assertEqual(plain_sink.frames, instrumented_sink.frames)
        self.assertGreaterEqual(len(captured), 2)
        diagnostics = instrumented.diagnostics
        self.assertEqual(diagnostics["status"], "completed")
        self.assertEqual(diagnostics["route_provenance_dense_planning"]["dense_frame_count"], 3)
        self.assertEqual(diagnostics["section_counts"]["bones"], 3)
        self.assertEqual(diagnostics["section_counts"]["morphs"], 0)
        self.assertEqual(diagnostics["section_counts"]["cameras"], 0)
        self.assertEqual(diagnostics["section_counts"]["lights"], 0)
        self.assertEqual(diagnostics["section_counts"]["ik"], 0)
        self.assertGreaterEqual(diagnostics["total"]["wall_sec"], 0.0)

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
            _collector, _result, sink = self._collect_to_sink(
                {
                    "target_model": "model_root",
                    "start_frame": 10.0,
                    "end_frame": 20.0,
                    "export_strategy": "bake_timeline",
                }
            )
        finally:
            collector_module.collect_ik_nodes_by_bone_name = original_collect

        self.assertEqual(
            [frame for section, frame in sink.frames if section == "ik"],
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

    def test_bake_timeline_stream_collects_ik_baseline_before_later_enabled_key(self):
        self.cmds.attrs[("ik_solver", "enabled")] = False
        self.cmds.keys[("ik_solver", "enabled")] = {20.0: 1.0}
        original_collect = collector_module.collect_ik_nodes_by_bone_name
        collector_module.collect_ik_nodes_by_bone_name = lambda **_kwargs: {"左足ＩＫ": "ik_solver"}
        try:
            _collector, _result, sink = self._collect_to_sink(
                {
                    "target_model": "model_root",
                    "export_strategy": "bake_timeline",
                }
            )
        finally:
            collector_module.collect_ik_nodes_by_bone_name = original_collect

        self.assertEqual(
            [frame for section, frame in sink.frames if section == "ik"],
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

    def test_bake_timeline_stream_keeps_keyed_ik_sparse_when_other_tracks_are_dense(self):
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
            _collector, _result, sink = self._collect_to_sink(
                {
                    "target_model": "model_root",
                    "export_strategy": "bake_timeline",
                    "frame_range": (0, 3),
                }
            )
        finally:
            collector_module.collect_ik_nodes_by_bone_name = original_collect

        self.assertEqual(
            [frame for section, frame in sink.frames if section == "ik"],
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

    def test_collects_vertex_morph_frames_from_model_controller_keys(self):
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
        metadata = [SimpleNamespace(morph_type="vertex", name="vertex_morph", index=3)]
        with mock.patch.object(collector_module, "iter_morph_network_metadata", return_value=metadata):
            result = VmdSceneCollector().collect({"target_model": "model_root"})

        self.assertEqual(
            result["morph_frames"],
            [
                {"morph_name": "vertex_morph", "frame_number": 0, "weight": 0.0},
                {"morph_name": "vertex_morph", "frame_number": 10, "weight": 0.75},
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
