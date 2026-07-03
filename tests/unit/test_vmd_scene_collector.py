"""VMD scene collector の Maya 非依存ロジックを検証するテスト。"""

import json
import math
import unittest

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
        self.translations = {}
        self.world_matrices = {}
        self.current_time = 0.0
        self.blendshape_weights = {}
        self.aliases = {}

    def ls(self, pattern=None, type=None, objectsOnly=False):  # noqa: A002,N803
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
        result = []
        for child in self.children.get(node, []):
            if type is None or self.node_types.get(child) == type:
                result.append(child)
            if allDescendents:
                result.extend(self.listRelatives(child, allDescendents=True, type=type, fullPath=fullPath) or [])
        return result

    def nodeType(self, node):  # noqa: N802
        return self.node_types.get(node)

    def attributeQuery(self, attr, node, exists=False):  # noqa: N802
        return exists and (node, attr) in self.attrs

    def getAttr(self, plug, time=None):  # noqa: N802
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
        return self.attrs.get((node, attr), 0.0)

    def listConnections(self, plug, source=False, destination=False, **_kwargs):  # noqa: N802,N803
        node, attr = plug.split(".", 1)
        return list(self.connections.get((node, attr, bool(source), bool(destination)), []))

    def currentTime(self, time=None, edit=False, query=False):  # noqa: N802
        if query:
            return self.current_time
        if edit:
            self.current_time = float(time)
        return self.current_time

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
        collector_module.cmds = self.cmds

    def tearDown(self):
        collector_module.cmds = self.original_cmds

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
