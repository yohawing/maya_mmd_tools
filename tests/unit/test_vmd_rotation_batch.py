"""Native sparse rotation conversion must retain the scalar Maya result."""

from functools import partial
import logging
from types import SimpleNamespace
from unittest.mock import patch

from maya import cmds
import maya.api.OpenMaya as om

from tests.common.maya_test_base import MayaTestBase
from mmd_tools.converters import vmd_joint_rotation as rotation
from mmd_tools.converters.vmd_bone_animation import _sparse_rotation_samples


class TestVmdRotationBatch(MayaTestBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import os
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        version = str(cmds.about(version=True)).split()[0]
        config = os.environ.get("MMD_TOOLS_CPP_CONFIG", "Release")
        suffix = ".mll" if os.name == "nt" else ".bundle"
        cls.load_plugin(str(root / "plug-ins" / version / config / ("mmd_tools_cpp" + suffix)))

    def test_batch_matches_scalar_orders_bind_and_control_basis(self):
        joint = cmds.createNode("joint")
        control = cmds.createNode("transform")
        cmds.setAttr(joint + ".jointOrient", 17., -29., 43.)
        quaternions = [tuple(om.MEulerRotation(i*.19, i*.13, -i*.23).asQuaternion()) for i in range(40)]
        quaternions += [(1., 0., 0., 0.), (-1., 0., 0., 1e-12)]
        frames = [SimpleNamespace(frame_number=i//2, rotation=q) for i,q in enumerate(quaternions)]
        matrix = om.MEulerRotation(.2, -.4, .1).asMatrix()
        terms = (om.MVector(1., 2., -3.), matrix, matrix.inverse(), om.MMatrix())
        for order in range(6):
            cmds.setAttr(joint + ".rotateOrder", order)
            cmds.setAttr(control + ".rotateOrder", 5-order)
            converter = SimpleNamespace(logger=logging.getLogger(__name__), bone_index_to_joint={})
            for bind in (None, terms):
                for basis in (None, tuple(om.MEulerRotation(.3, .1, -.7).asQuaternion())):
                    route = {"authoring_basis": basis, "attr_targets": {"rotateX": (control, "rotateX")}}
                    context = SimpleNamespace(vmd_frame_to_maya_time=lambda v: v*.8-4,
                        convert_vmd_quat_to_joint_rotate=partial(rotation.convert_vmd_quat_to_joint_rotate, converter))
                    with self.subTest(order=order, bind=bind is not None, basis=basis is not None), patch.object(rotation, "_bind_space_terms", return_value=bind):
                        expected = _sparse_rotation_samples(context, joint, frames, route)
                        context.convert_vmd_quats_to_joint_rotates = partial(rotation.convert_vmd_quats_to_joint_rotates, converter)
                        actual = _sparse_rotation_samples(context, joint, frames, route)
                        self.assertEqual([v[0] for v in expected], [v[0] for v in actual])
                        for (_,a),(_,b) in zip(expected,actual):
                            for x,y in zip(a,b):
                                self.assertAlmostEqual(x,y,places=8)
                        del context.convert_vmd_quats_to_joint_rotates

    def test_missing_command_uses_scalar(self):
        with patch.object(cmds, "mmdVmdRotationSamples", None):
            self.assertIsNone(rotation.convert_vmd_quats_to_joint_rotates(None, "missing", [(0,0,0,1)]))
