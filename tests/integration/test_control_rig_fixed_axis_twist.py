"""PMX fixed-axis twist authoring must survive Bake and registered VMD import."""
from pathlib import Path

from maya import cmds

from mmd_tools.converters.vmd_scene_collector import VmdSceneCollector
from mmd_tools.core.mmd_control_rig_builder import read_mmd_control_rig_metadata
from mmd_tools.core.mmd_control_rig_motion import bake_mmd_control_rig
from mmd_tools.io.mmd_importer import import_mmd_file
from mmd_tools.io.vmd_exporter import VmdExporter
from tests.common.maya_test_base import MayaTestBase
from tests.integration.test_control_rig_arm_ik import arm_pose, create_arm_case, pose_delta

ROOT = Path(__file__).resolve().parents[2]


class TestControlRigFixedAxisTwist(MayaTestBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.load_plugin(str(ROOT / "mmd_tools/plugin_main.py"))

    def test_nonzero_fixed_axis_twists_bake_and_vmd_roundtrip(self):
        """A child direction differing from the PMX axis must not author swing."""
        cmds.currentUnit(time="ntsc")
        root, _controls, arm_joints = create_arm_case("right")
        metadata = read_mmd_control_rig_metadata(root)
        twist_joints = []
        for role, values in (("right_arm_twist", (20, 50)), ("right_wrist_twist", (-15, -35))):
            control = cmds.ls(metadata["controls"][role], long=True)[0]
            twist_joints.append(cmds.ls(metadata["bindings"][role]["jointUuid"], long=True)[0])
            for frame, value in zip((1, 10), values):
                cmds.setKeyframe(control + ".rotateZ", time=frame, value=value)
            cmds.keyTangent(control + ".rotateZ", inTangentType="linear", outTangentType="linear")
        joints = arm_joints + twist_joints
        expected = {}
        for frame in range(1, 11):
            cmds.currentTime(frame, update=True)
            expected[frame] = arm_pose(joints)
        # Protect the authored pose, not merely a round-trip of a wrong bake.
        bake_mmd_control_rig(root)
        for frame, pose in expected.items():
            cmds.currentTime(frame, update=True)
            self.assertLess(pose_delta(pose, arm_pose(joints)), 1e-4, "Bake frame " + str(frame))
        names = [cmds.getAttr(joint + ".mmd_bone_name") for joint in joints]
        output = self.get_temp_filename("fixed_axis_twist.vmd")
        VmdExporter().export_vmd_animation(output, VmdSceneCollector().collect({"target_model": root}))
        model = str(ROOT / "tests/data/mmt_test_model.pmx")
        fresh = import_mmd_file(model, options={
            "setup_rig": True, "setup_bone_orientation": True,
            "import_physics": False, "use_namespace": True,
        })
        self.assertTrue(import_mmd_file(output, options={"target_model": fresh, "pmx_path": model}))
        by_name = {
            cmds.getAttr(joint + ".mmd_bone_name"): joint
            for joint in cmds.listRelatives(fresh, allDescendents=True, type="joint", fullPath=True)
        }
        for frame, pose in expected.items():
            cmds.currentTime(frame, update=True)
            self.assertLess(pose_delta(pose, arm_pose([by_name[name] for name in names])), 1e-4,
                            "VMD reimport frame " + str(frame))


if __name__ == "__main__":
    import unittest
    unittest.main()
