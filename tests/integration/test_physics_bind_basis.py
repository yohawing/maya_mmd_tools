"""Maya integration gates for saved physics bind-basis authority."""

from __future__ import annotations

import json
import unittest
import os
import tempfile
from types import SimpleNamespace

import maya.api.OpenMaya as om
from maya import cmds

from mmd_tools.core import physics_bind_basis as basis
from mmd_tools.nodes.mmd_physics_solver_node import MmdPhysicsSolverNode as MayaBindSolverNode
from tests.common.maya_test_base import MayaTestBase


def _matrix_values(matrix):
    try:
        values = [float(value) for value in matrix]
        if len(values) == 16:
            return values
    except Exception:
        pass
    return [
        float(matrix.getElement(row, column))
        for row in range(4)
        for column in range(4)
    ]


class TestPhysicsBindBasisMaya(MayaTestBase):
    """Verify bind resolution against real Maya dagPose/skinCluster plugs."""

    def test_saved_dag_pose_wins_over_animated_live_world(self):
        root = cmds.createNode("transform", name="bindModel")
        cmds.select(clear=True)
        joint = cmds.joint(name="bindJoint", position=(1.0, 2.0, 3.0))
        cmds.parent(joint, root)
        joint = (cmds.ls(joint, long=True) or [joint])[0]
        cmds.dagPose(joint, save=True, bindPose=True, name="bindModelPose")
        bind_world = _matrix_values(om.MMatrix(cmds.getAttr(f"{joint}.worldMatrix[0]")))

        # This is deliberately a different nonzero-frame live pose.  The
        # resolver must not observe it when constructing the correction basis.
        cmds.currentTime(24, edit=True)
        cmds.setKeyframe(joint, attribute="rotateY", value=37.0, time=24)
        live_world = _matrix_values(om.MMatrix(cmds.getAttr(f"{joint}.worldMatrix[0]")))
        self.assertGreater(max(abs(a - b) for a, b in zip(live_world, bind_world)), 1e-4)
        resolved = basis.resolve_saved_bind_world_matrix(joint)
        self.assertListEqual(_matrix_values(resolved), bind_world)

        # Exercise the actual solver initialization path with a minimal rest
        # runtime.  Rebuilding at the animated frame must retain the exact
        # correction captured at bind.
        node = MayaBindSolverNode()
        node._bone_joints = [joint]
        node._kinematic_corrections = {}
        node._instance = SimpleNamespace(
            evaluate_rest_pose=lambda: True,
            get_world_matrices=lambda: [[
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
            ]]
        )
        node._find_physics_bone_indices = lambda _root: ({0}, {0})
        node._build_kinematic_pose_data(root)
        animated_correction = _matrix_values(node._kinematic_corrections[0])
        cmds.currentTime(0, edit=True)
        node._kinematic_corrections = {}
        node._build_kinematic_pose_data(root)
        bind_correction = _matrix_values(node._kinematic_corrections[0])
        self.assertListEqual(animated_correction, bind_correction)

    def test_dag_pose_member_logical_index_is_used(self):
        root = cmds.createNode("transform", name="sparseModel")
        cmds.select(clear=True)
        first = cmds.joint(name="firstJoint", position=(1.0, 0.0, 0.0))
        cmds.select(clear=True)
        second = cmds.joint(name="secondJoint", position=(2.0, 0.0, 0.0))
        cmds.parent(first, root)
        cmds.parent(second, root)
        first = (cmds.ls(first, long=True) or [first])[0]
        second = (cmds.ls(second, long=True) or [second])[0]
        cmds.dagPose([first, second], save=True, bindPose=True, name="sparseModelPose")
        expected = _matrix_values(om.MMatrix(cmds.getAttr(f"{second}.worldMatrix[0]")))

        # Move only the second member so selecting the wrong logical index is
        # observable in the returned matrix.
        cmds.setAttr(f"{second}.translateX", 9.0)
        resolved = basis.resolve_saved_bind_world_matrix(second)
        self.assertListEqual(_matrix_values(resolved), expected)

    def test_singular_matrix_is_rejected_by_real_mmatrix_validation(self):
        singular = [0.0] * 16
        with self.assertRaises(basis.BindBasisResolutionError) as context:
            basis._validate_matrix(
                singular,
                joint="|singularJoint",
                label="dagPose.worldMatrix[0]",
            )
        self.assertEqual(context.exception.reason_code, "bind_basis_singular")

    def test_skin_cluster_bind_pre_matrix_is_validated_fallback(self):
        root = cmds.createNode("transform", name="skinModel")
        cmds.select(clear=True)
        joint = cmds.joint(name="skinJoint", position=(0.0, 1.0, 0.0))
        cmds.parent(joint, root)
        mesh, _ = cmds.polyPlane(name="skinMesh", width=1.0, height=1.0)
        skin, = cmds.skinCluster(joint, mesh, toSelectedBones=True, normalizeWeights=1)
        joint = (cmds.ls(joint, long=True) or [joint])[0]

        # Remove Maya's automatically-created bindPose so only the validated
        # inverse(bindPreMatrix) route remains available to the resolver.
        poses = cmds.dagPose(joint, query=True, bindPose=True) or []
        if poses:
            cmds.delete(poses)

        bind_pre = cmds.getAttr(f"{skin}.bindPreMatrix[0]")
        expected = om.MMatrix(bind_pre).inverse()
        resolved = basis.resolve_saved_bind_world_matrix(joint)
        self.assertListEqual(_matrix_values(resolved), _matrix_values(expected))

    def test_imported_metadata_covers_zero_weight_bone_without_maya_bind_record(self):
        root = cmds.createNode("transform", name="metadataBindModel")
        cmds.select(clear=True)
        parent = cmds.joint(name="metadataParent", position=(1.0, 2.0, 3.0))
        child = cmds.joint(name="metadataChild", position=(4.0, 2.0, 3.0))
        cmds.parent(parent, root)
        parent = (cmds.ls(parent, long=True) or [parent])[0]
        child = (cmds.ls(child, long=True) or [child])[0]
        cmds.setAttr(f"{parent}.jointOrientY", 17.0)
        for joint in (parent, child):
            translate = cmds.getAttr(f"{joint}.translate")[0]
            cmds.addAttr(joint, longName="mmd_vmd_bind_translate", dataType="string")
            cmds.setAttr(
                f"{joint}.mmd_vmd_bind_translate",
                json.dumps([float(value) for value in translate]),
                type="string",
            )
        expected = _matrix_values(om.MMatrix(cmds.getAttr(f"{child}.worldMatrix[0]")))

        cmds.setAttr(f"{parent}.rotateZ", 43.0)
        cmds.setAttr(f"{child}.translateY", 6.0)
        live = _matrix_values(om.MMatrix(cmds.getAttr(f"{child}.worldMatrix[0]")))
        self.assertGreater(max(abs(a - b) for a, b in zip(live, expected)), 1e-4)

        resolved = basis.resolve_imported_bind_world_matrix(child)
        self.assertListEqual(_matrix_values(resolved), expected)

    def test_namespace_multiple_models_survive_save_reopen(self):
        path = os.path.join(tempfile.gettempdir(), "mmd_physics_bind_basis_reopen.ma")
        try:
            cmds.namespace(add="bindNs")
            cmds.namespace(set="bindNs")
            first_root = cmds.createNode("transform", name="firstModel")
            cmds.select(clear=True)
            first_joint = cmds.joint(name="joint", position=(1.0, 0.0, 0.0))
            cmds.parent(first_joint, first_root)
            first_joint = (cmds.ls(first_joint, long=True) or [first_joint])[0]
            cmds.dagPose(first_joint, save=True, bindPose=True, name="firstPose")
            cmds.namespace(set=":")

            second_root = cmds.createNode("transform", name="secondModel")
            cmds.select(clear=True)
            second_joint = cmds.joint(name="joint", position=(4.0, 0.0, 0.0))
            cmds.parent(second_joint, second_root)
            second_joint = (cmds.ls(second_joint, long=True) or [second_joint])[0]
            cmds.dagPose(second_joint, save=True, bindPose=True, name="secondPose")

            cmds.file(rename=path)
            cmds.file(save=True, force=True, type="mayaAscii")
            cmds.file(path, open=True, force=True)

            reopened_first = (cmds.ls("bindNs:joint", long=True) or [])[0]
            reopened_second = (cmds.ls("joint", long=True) or [])[0]
            first_bind = basis.resolve_saved_bind_world_matrix(reopened_first)
            second_bind = basis.resolve_saved_bind_world_matrix(reopened_second)
            self.assertNotEqual(_matrix_values(first_bind), _matrix_values(second_bind))
        finally:
            if os.path.exists(path):
                os.remove(path)


if __name__ == "__main__":
    unittest.main()
