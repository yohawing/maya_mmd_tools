"""Real Maya coverage for secondary controls added after layered VMD authoring."""

from pathlib import Path

from maya import cmds

from mmd_tools.converters.vmd_bone_animation import set_bone_keyframes
from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.converters.vmd_redirected_authoring_proxy import (
    ensure_redirected_authoring_proxy,
    resolve_redirected_authoring_proxy_authority,
)
from mmd_tools.core import secondary_bone_controls as controls
from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame
from mmd_tools.io.mmd_importer import import_mmd_file
from tests.common.maya_test_base import MayaTestBase


class SecondaryControlsVmdTests(MayaTestBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plugin = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
        cls.load_plugin(str(plugin))

    def test_build_import_remove_preserves_owned_proxy_and_layered_pose(self):
        fixture = Path(__file__).resolve().parents[1] / "data" / "physics" / "test_hair_physics.pmx"
        root = import_mmd_file(str(fixture), options={
            "import_physics": True, "create_mmd_shaders": False, "setup_rig": False,
        })
        rows, world = controls._plan(cmds, root)
        cmds.setAttr(f"{world}.enable", False)
        row = rows[0]
        joint, driver = row["joint"], row["driver"]
        destinations = {channel: (driver, controls._pre(driver, channel).split(".", 1)[1])
                        for channel in controls._CHANNELS}
        proxy_routes = ensure_redirected_authoring_proxy(joint, destinations)
        proxy = proxy_routes["rotateX"][0]
        layer = cmds.animLayer("VMD_Motion_secondary_existing", override=False)
        for channel in ("translateX", "rotateX"):
            cmds.animLayer(layer, edit=True, attribute=f"{proxy}.{channel}")
            for frame, value in ((1, 2.0), (5, 12.0)):
                cmds.setKeyframe(proxy, attribute=channel, time=frame, value=value, animLayer=layer)

        def sample():
            values = []
            for frame in (1, 3, 5, 7, 9):
                cmds.currentTime(frame, edit=True)
                values.append(tuple(cmds.getAttr(controls._pre(driver, channel)) for channel in controls._CHANNELS))
            return values

        expected = sample()
        state = controls.build(root)
        built = next(item for item in state["controls"] if item["joint"] == joint)
        control = built["control"]
        self.assertEqual(sample(), expected)
        memberships = cmds.animLayer(layer, query=True, attribute=True) or []
        self.assertIn(f"{control.rsplit('|', 1)[-1]}.rotateX", memberships)
        self.assertTrue(resolve_redirected_authoring_proxy_authority(joint)[2])
        for channel in controls._CHANNELS:
            source, attribute = controls.secondary_control_input_route(driver, joint, channel)
            self.assertEqual(cmds.ls(source, long=True), [control])
            self.assertEqual(attribute, channel)

        cmds.setKeyframe(control, attribute="rotateX", time=7, value=20.0, animLayer=layer)
        cmds.currentTime(7, edit=True)
        self.assertAlmostEqual(cmds.getAttr(controls._pre(driver, "rotateX")), 20.0)
        converter = VmdConverter()
        converter.anim_layer = cmds.animLayer("VMD_Motion_secondary_new", override=False)
        converter._bone_bind_poses["bone"] = (0.0, 0.0, 0.0)
        frame = VmdBoneFrame()
        frame.bone_name, frame.frame_number = "bone", 9
        frame.position, frame.rotation = (4.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)
        set_bone_keyframes(converter._bone_animation_context(), joint, [frame], "bone", {
            "attr_targets": {channel: (control, channel) for channel in controls._CHANNELS},
            "secondary_control_channels": controls._CHANNELS,
            "quaternion_interpolation_safe": True,
        })
        cmds.currentTime(9, edit=True)
        self.assertAlmostEqual(cmds.getAttr(controls._pre(driver, "translateX")), 4.0)
        before_remove = sample()
        controls.remove(root)
        self.assertEqual(sample(), before_remove)
        self.assertTrue(cmds.objExists(proxy))
        self.assertTrue(cmds.objExists(layer))
        self.assertIn(f"{proxy}.rotateX", cmds.animLayer(layer, query=True, attribute=True) or [])
        resolved, authority, claimed = resolve_redirected_authoring_proxy_authority(joint)
        self.assertTrue(claimed)
        self.assertEqual(authority, destinations)
        self.assertEqual(set(resolved), set(controls._CHANNELS))
