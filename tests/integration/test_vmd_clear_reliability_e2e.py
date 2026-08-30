"""Maya E2E coverage for binary VMD A->B clear and rollback routes."""

from pathlib import Path
import unittest
from unittest.mock import patch

from maya import cmds

from mmd_tools.converters.vmd_camera_animation import get_or_create_camera
from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.converters.vmd_light_animation import get_or_create_light
from mmd_tools.core import settings
from mmd_tools.core.mmd_control_rig_motion import control_rig_edit_routes_for_joints
from mmd_tools.core.native.mmd_anim_runtime import is_mmd_runtime_available
from mmd_tools.core.vmd_data import VmdData
from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame
from mmd_tools.io.mmd_importer import import_mmd_file
from tests.common.maya_test_base import MayaTestBase


_TEST_DATA = Path(__file__).resolve().parents[1] / "data"
_PMX_PATH = _TEST_DATA / "mmt_test_model.pmx"
_PMX_BYTES = _PMX_PATH.read_bytes()
_EVALUATION_MODES = ("off", "serial", "parallel")
_TRACKS_A = ("センター", "グルーブ")
_TRACKS_B = ("グルーブ", "左腕")


def _motion(track_names, offset):
    """Build a small VMD with two distinct bone tracks."""
    data = VmdData()
    data.header.model_name = "mmt_test_model"
    for track_index, bone_name in enumerate(track_names):
        for frame_number, scale in ((6, 1.0), (12, 2.0)):
            frame = VmdBoneFrame()
            frame.bone_name = bone_name
            frame.frame_number = frame_number
            frame.position = (
                float(offset + track_index * 0.1) * scale,
                0.0,
                0.0,
            )
            frame.rotation = (0.0, 0.0, 0.0, 1.0)
            data.bone_frames.append(frame)
    return data


def _curve_state(plug):
    """Capture animCurve UUID and key payload for one Maya plug."""
    curves = list(cmds.keyframe(plug, query=True, name=True) or [])
    if not curves:
        curves = list(
            cmds.listConnections(
                plug,
                source=True,
                destination=False,
                type="animCurve",
            )
            or []
        )
    rows = []
    for curve in dict.fromkeys(str(value) for value in curves):
        curve_uuid = cmds.ls(curve, uuid=True) or []
        rows.append(
            {
                "uuid": str(curve_uuid[0]) if curve_uuid else "",
                "times": tuple(cmds.keyframe(curve, query=True, timeChange=True) or []),
                "values": tuple(cmds.keyframe(curve, query=True, valueChange=True) or []),
            }
        )
    return tuple(sorted(rows, key=lambda row: row["uuid"]))


def _layer_curves(layer, plug):
    curves = cmds.animLayer(layer, query=True, findCurveForPlug=plug) or []
    if isinstance(curves, str):
        curves = [curves]
    return curves


def _layer_state(layer):
    """Capture layer membership/settings and every member curve payload."""
    if not cmds.objExists(layer):
        return {"exists": False}
    layer_uuid = cmds.ls(layer, uuid=True) or []
    attributes = tuple(sorted(str(value) for value in cmds.animLayer(layer, query=True, attribute=True) or []))
    curves = {}
    for plug in attributes:
        for curve in _layer_curves(layer, plug):
            curve = str(curve)
            curve_uuid = cmds.ls(curve, uuid=True) or []
            if not curve_uuid:
                continue
            curves[str(curve_uuid[0])] = {
                "times": tuple(cmds.keyframe(curve, query=True, timeChange=True) or []),
                "values": tuple(cmds.keyframe(curve, query=True, valueChange=True) or []),
            }
    settings_state = {}
    for flag in ("selected", "weight", "mute", "solo"):
        settings_state[flag] = cmds.animLayer(layer, query=True, **{flag: True})
    return {
        "exists": True,
        "uuid": str(layer_uuid[0]) if layer_uuid else "",
        "attributes": attributes,
        "settings": settings_state,
        "curves": curves,
    }


def _key_times(plug):
    return tuple(cmds.keyframe(plug, query=True, timeChange=True) or [])


class TestVmdClearReliabilityE2E(MayaTestBase):
    """Exercise target-scoped binary replacement under Maya evaluation modes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not _PMX_PATH.is_file():
            raise unittest.SkipTest(f"PMX fixture not found: {_PMX_PATH}")
        plugin_path = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
        if not cmds.pluginInfo(str(plugin_path), query=True, loaded=True):
            cls.plugins_loaded.extend(cmds.loadPlugin(str(plugin_path), quiet=True) or [])

    def setUp(self):
        super().setUp()
        settings.set("import.model.create_mmd_shaders", False)
        settings.set("import.rig.add_semi_standard_bones", False)

    def _import_fixture(self, namespace, *, setup_rig):
        root = import_mmd_file(
            str(_PMX_PATH),
            options={
                "custom_namespace": namespace,
                "setup_rig": setup_rig,
                "setup_bone_orientation": True,
                "import_physics": False,
                "create_mmd_shaders": False,
            },
        )
        self.assertTrue(root, f"PMX import failed for {namespace}")
        return cmds.ls(root, long=True)[0]

    @staticmethod
    def _find_joint(root, bone_name):
        for node in cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []:
            if cmds.attributeQuery("mmd_bone_name", node=node, exists=True):
                if cmds.getAttr(f"{node}.mmd_bone_name") == bone_name:
                    return node
        raise AssertionError(f"MMD bone not found: {bone_name}")

    def _binary_motion(self, name, motion):
        path = Path(self.get_temp_filename(f"{name}.vmd"))
        motion.write_file(str(path))
        parsed = VmdData().parse_file(str(path))
        return parsed, path.read_bytes()

    def _convert_binary(self, root, name, motion, *, config, layer_name, clear_existing_motion=False):
        parsed, vmd_bytes = self._binary_motion(name, motion)
        profile = {}
        converter = VmdConverter()
        converter.use_animation_layers = bool(config["use_animation_layers"])
        use_runtime_source = bool(config["bake_mode"] or config["control_rig"])
        self.assertTrue(
            converter.convert(
                parsed,
                layer_name=layer_name,
                target_model=root,
                clear_existing_motion=clear_existing_motion,
                create_mmd_control_rig=bool(config["control_rig"]),
                bake_mode=bool(config["bake_mode"]),
                vmd_bytes=vmd_bytes,
                pmx_bytes=_PMX_BYTES if use_runtime_source else None,
                pmx_path=str(_PMX_PATH) if use_runtime_source else None,
                profile=profile,
            ),
            f"VMD conversion failed for {name}",
        )
        return profile

    def _track_probe_plug(self, root, bone_name, *, control_rig):
        joint = self._find_joint(root, bone_name)
        if control_rig:
            routes = control_rig_edit_routes_for_joints({joint})
            route = routes.get(joint) or {}
            if "translateX" in route:
                node, attribute = route["translateX"]
                return f"{node}.{attribute}"
        return f"{joint}.translateX"

    def _assert_clear_profile(self, profile, root):
        clear_profile = profile.get("motion_clear")
        self.assertIsInstance(clear_profile, dict)
        self.assertTrue(clear_profile["requested"]["clear_existing_motion"])
        self.assertEqual(clear_profile["requested"]["target_model"], root)
        self.assertEqual(clear_profile["status"], "success")
        before = clear_profile["before"]["key_count"]
        after = clear_profile["after"]["key_count"]
        self.assertGreater(before, after)
        self.assertEqual(clear_profile["effective"]["cleared_key_count"], before - after)
        self.assertIn("routes", clear_profile["before"])
        self.assertIn("routes", clear_profile["after"])

    @staticmethod
    def _set_evaluation_mode(mode):
        previous = cmds.evaluationManager(query=True, mode=True) or []
        cmds.evaluationManager(mode=mode)
        return previous

    def _run_replacement_case(self, config, evaluation_mode):
        previous_modes = self._set_evaluation_mode(evaluation_mode)
        try:
            setup_rig = bool(config["control_rig"] or not config["bake_mode"])
            target_root = self._import_fixture(
                f"vmd_clear_target_{config['name']}_{evaluation_mode}",
                setup_rig=setup_rig,
            )
            foreign_root = self._import_fixture(
                f"vmd_clear_foreign_{config['name']}_{evaluation_mode}",
                setup_rig=setup_rig,
            )
            foreign_joint = self._find_joint(foreign_root, _TRACKS_A[0])
            cmds.setKeyframe(foreign_joint, attribute="translateX", time=31, value=9.0)

            camera = get_or_create_camera()
            light = get_or_create_light()
            cmds.setKeyframe(camera, attribute="translateX", time=32, value=4.0)
            cmds.setKeyframe(light, attribute="rotateX", time=32, value=15.0)
            expected_time = 37.0
            cmds.currentTime(expected_time, edit=True)

            layer_name = f"VMD_Clear_E2E_{config['name']}_{evaluation_mode}"
            self._convert_binary(
                target_root,
                f"{config['name']}_{evaluation_mode}_a",
                _motion(_TRACKS_A, 0.25),
                config=config,
                layer_name=layer_name,
            )
            self.assertEqual(float(cmds.currentTime(query=True)), expected_time)
            # PMX/VMD setup may establish the scene FPS and Maya retimes
            # existing keys when that unit changes.  Capture the unchanged
            # foreign/scene baseline after A so B is compared in one unit.
            foreign_before = _curve_state(f"{foreign_joint}.translateX")
            camera_before = _curve_state(f"{camera}.translateX")
            light_before = _curve_state(f"{light}.rotateX")
            a_only_plug = self._track_probe_plug(target_root, _TRACKS_A[0], control_rig=config["control_rig"])
            b_only_plug = self._track_probe_plug(target_root, _TRACKS_B[1], control_rig=config["control_rig"])
            self.assertTrue(_key_times(a_only_plug), "A did not author the A-only track")

            cmds.currentTime(expected_time, edit=True)
            profile_b = self._convert_binary(
                target_root,
                f"{config['name']}_{evaluation_mode}_b",
                _motion(_TRACKS_B, 0.5),
                config=config,
                layer_name=layer_name,
                clear_existing_motion=True,
            )

            self.assertFalse(_key_times(a_only_plug), "A-only track survived clear_existing_motion")
            self.assertTrue(_key_times(b_only_plug), "B-only track was not authored")
            self._assert_clear_profile(profile_b, target_root)
            self.assertEqual(_curve_state(f"{foreign_joint}.translateX"), foreign_before)
            self.assertEqual(_curve_state(f"{camera}.translateX"), camera_before)
            self.assertEqual(_curve_state(f"{light}.rotateX"), light_before)
            self.assertEqual(float(cmds.currentTime(query=True)), expected_time)
            if config["use_animation_layers"]:
                self.assertTrue(cmds.objExists(layer_name))
                self.assertTrue(cmds.animLayer(layer_name, query=True, attribute=True))
        finally:
            if previous_modes:
                cmds.evaluationManager(mode=previous_modes[0])
            cmds.file(new=True, force=True)

    def test_binary_replacement_covers_supported_routes_and_evaluation_modes(self):
        cases = (
            {
                "name": "legacy_direct",
                "use_animation_layers": False,
                "control_rig": False,
                "bake_mode": False,
            },
            {
                "name": "animation_layer",
                "use_animation_layers": True,
                "control_rig": False,
                "bake_mode": False,
            },
            {
                "name": "control_rig_direct",
                "use_animation_layers": False,
                "control_rig": True,
                "bake_mode": False,
            },
        )
        for config in cases:
            for evaluation_mode in _EVALUATION_MODES:
                with self.subTest(route=config["name"], evaluation_mode=evaluation_mode):
                    self._run_replacement_case(config, evaluation_mode)

    def test_bake_runtime_binary_replacement_when_runtime_is_available(self):
        if not is_mmd_runtime_available():
            self.skipTest("mmd-anim runtime unavailable for the bake/runtime route")
        config = {
            "name": "bake_runtime",
            "use_animation_layers": False,
            "control_rig": False,
            "bake_mode": True,
        }
        for evaluation_mode in _EVALUATION_MODES:
            with self.subTest(route=config["name"], evaluation_mode=evaluation_mode):
                self._run_replacement_case(config, evaluation_mode)

    def test_late_failure_restores_legacy_and_layer_curve_payloads(self):
        for config in (
            {
                "name": "rollback_legacy",
                "use_animation_layers": False,
                "control_rig": False,
                "bake_mode": False,
            },
            {
                "name": "rollback_layer",
                "use_animation_layers": True,
                "control_rig": False,
                "bake_mode": False,
            },
        ):
            with self.subTest(route=config["name"]):
                target_root = self._import_fixture(config["name"], setup_rig=True)
                layer_name = f"VMD_Clear_Rollback_{config['name']}"
                self._convert_binary(
                    target_root,
                    f"{config['name']}_a",
                    _motion(_TRACKS_A, 0.25),
                    config=config,
                    layer_name=layer_name,
                )
                a_only_plug = self._track_probe_plug(target_root, _TRACKS_A[0], control_rig=False)
                before_layer = _layer_state(layer_name) if config["use_animation_layers"] else None
                before_curve = None if before_layer is not None else _curve_state(a_only_plug)
                expected_time = 123.0
                cmds.currentTime(expected_time, edit=True)

                converter = VmdConverter()
                converter.use_animation_layers = config["use_animation_layers"]
                parsed_b, vmd_bytes_b = self._binary_motion(
                    f"{config['name']}_b",
                    _motion(_TRACKS_B, 0.5),
                )
                parsed_b.morph_frames = [object()]
                profile = {}
                with patch.object(converter, "_convert_morph_animation", side_effect=RuntimeError("forced late VMD failure")):
                    result = converter.convert(
                        parsed_b,
                        layer_name=layer_name,
                        target_model=target_root,
                        clear_existing_motion=True,
                        vmd_bytes=vmd_bytes_b,
                        profile=profile,
                    )
                self.assertFalse(result)
                if before_layer is not None:
                    self.assertEqual(_layer_state(layer_name), before_layer)
                else:
                    self.assertEqual(_curve_state(a_only_plug), before_curve)
                self.assertEqual(float(cmds.currentTime(query=True)), expected_time)
                cmds.file(new=True, force=True)


if __name__ == "__main__":
    unittest.main()
