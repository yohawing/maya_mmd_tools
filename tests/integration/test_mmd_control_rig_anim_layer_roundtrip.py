"""Real PMX Control Rig animLayer ownership roundtrip coverage."""

from pathlib import Path
from unittest import mock

from maya import cmds

from mmd_tools.core.constants import ATTR_MMD_CONTROL_RIG_JSON
from mmd_tools.core.mmd_control_rig_anim_layers import (
    capture_mmd_control_rig_anim_layers,
)
from mmd_tools.core.mmd_control_rig_builder import (
    build_mmd_control_rig,
    read_mmd_control_rig_metadata,
    resolve_mmd_control_rig_binding_joint,
)
from mmd_tools.core.mmd_control_rig_motion import (
    bake_mmd_control_rig,
    enter_mmd_control_rig_edit,
    restore_mmd_control_rig_attached,
)
from mmd_tools.io.mmd_importer import import_mmd_file
from tests.common.maya_test_base import MayaTestBase


_ROOT = Path(__file__).resolve().parents[2]
_PMX_PATH = str(_ROOT / "tests" / "data" / "mmt_test_model.pmx")


class TestMmdControlRigAnimLayerRoundtrip(MayaTestBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plugin = _ROOT / "mmd_tools" / "plugin_main.py"
        if not cmds.pluginInfo(str(plugin), query=True, loaded=True):
            cmds.loadPlugin(str(plugin), quiet=True)
            cls.plugins_loaded.append(str(plugin))

    def _scene(self):
        root = import_mmd_file(
            _PMX_PATH,
            options={
                "setup_rig": True,
                "setup_bone_orientation": True,
                "import_physics": False,
            },
        )
        rig = build_mmd_control_rig(root)
        metadata = read_mmd_control_rig_metadata(root)
        binding = metadata["bindings"]["center"]
        joint = resolve_mmd_control_rig_binding_joint(cmds, binding)
        layer = cmds.animLayer("cr061_real_exclusive_layer", override=False, weight=1.0)
        cmds.animLayer(layer, edit=True, attribute=f"{joint}.translateX")
        cmds.setKeyframe(joint, attribute="translateX", time=2.0, value=1.25, animLayer=layer)
        cmds.animLayer(layer, edit=True, weight=0.7, selected=True, preferred=True)
        return root, rig, joint, layer

    def test_target_exclusive_layer_survives_edit_bake_restore_and_failure(self):
        root, _rig, joint, layer = self._scene()
        before = capture_mmd_control_rig_anim_layers(cmds, root, None)
        route_before = before["routes"][f"{joint}.translateX"]
        curve_uuid = route_before["curveRef"]["nodeUuid"]

        edited = enter_mmd_control_rig_edit(root)
        layer_row = next(
            row
            for row in edited["journal"]["channels"]
            if row.get("layerRoute")
        )
        self.assertEqual(layer_row["layerRoute"]["curveRef"]["nodeUuid"], curve_uuid)
        # Translate-authorable layer curves keep the original C source
        # untouched and drive the controller through an owned delta duplicate
        # D plus an additive baseline helper into inputB.
        self.assertNotEqual(
            layer_row["controlSource"].split(".", 1)[0],
            route_before["curve"].split(".", 1)[0],
        )
        self.assertTrue(cmds.isConnected(layer_row["controlSource"], layer_row["control"]))
        self.assertTrue(
            cmds.isConnected(
                layer_row["translateBaselineOutput"],
                route_before["blend"],
            )
        )
        self.assertFalse(cmds.isConnected(route_before["curve"], route_before["blend"]))

        restore_mmd_control_rig_attached(root)
        restored = capture_mmd_control_rig_anim_layers(cmds, root, None)
        self.assertEqual(restored, before)
        self.assertEqual(cmds.animLayer(layer, query=True, weight=True), 0.7)
        self.assertTrue(cmds.animLayer(layer, query=True, selected=True))
        self.assertTrue(cmds.animLayer(layer, query=True, preferred=True))

        edited = enter_mmd_control_rig_edit(root)
        bake_mmd_control_rig(root)
        baked = capture_mmd_control_rig_anim_layers(cmds, root, None)
        self.assertEqual(baked, before)
        self.assertEqual(read_mmd_control_rig_metadata(root)["state"], "BAKED")

        restore_mmd_control_rig_attached(root)
        self.assertEqual(capture_mmd_control_rig_anim_layers(cmds, root, None), before)

        metadata_before_failure = cmds.getAttr(f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}")
        original_connect = cmds.connectAttr
        failures = [RuntimeError("animLayer route rollback")]

        def fail_once(*args, **kwargs):
            if failures:
                raise failures.pop()
            return original_connect(*args, **kwargs)

        with mock.patch.object(cmds, "connectAttr", side_effect=fail_once):
            with self.assertRaisesRegex(RuntimeError, "animLayer route rollback"):
                enter_mmd_control_rig_edit(root)
        self.assertEqual(cmds.getAttr(f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}"), metadata_before_failure)
        self.assertEqual(capture_mmd_control_rig_anim_layers(cmds, root, None), before)


if __name__ == "__main__":
    import unittest

    unittest.main()
